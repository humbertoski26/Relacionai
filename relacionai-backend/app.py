"""
Relacionai — backend propio (Flask).

Dos zonas:
  - /encargado/...  Panel privado (login simple con contraseña) donde se
    crean casos, se revisan relatos, se genera/actualiza la síntesis y se
    descarga el informe final.
  - /caso/<rotulo>   Página pública (sin login) a la que llega cualquier
    persona a la que el encargado le compartió el link, para subir su
    relato (texto, .docx o .pdf).

Ver README.md para variables de entorno y despliegue.
"""

import functools
import os
import urllib.parse
from datetime import datetime

from flask import (
    Flask, abort, flash, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

import models
from claude_client import resumir_relato, sintetizar_caso
from extract import ExtractError, extraer_texto
from report_pdf import construir_informe_pdf

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB por archivo subido

ENCARGADO_PASSWORD = os.environ.get("ENCARGADO_PASSWORD", "relacionai")

models.init_db()


# ---------------------------------------------------------------- helpers

def login_requerido(vista):
    @functools.wraps(vista)
    def envoltura(*args, **kwargs):
        if not session.get("encargado"):
            return redirect(url_for("encargado_login", next=request.path))
        return vista(*args, **kwargs)
    return envoltura


def fmt_fecha(iso: str) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return iso


app.jinja_env.filters["fecha"] = fmt_fecha


def link_publico(rotulo: str) -> str:
    return url_for("caso_publico", rotulo=rotulo, _external=True)


def link_whatsapp(rotulo: str, apellido: str) -> str:
    mensaje = (
        f"Te comparto el link para registrar tu relato en el caso {rotulo}. "
        f"Cuando puedas, entra y sube tu versión de los hechos: {link_publico(rotulo)}"
    )
    return "https://wa.me/?text=" + urllib.parse.quote(mensaje)


def link_correo(rotulo: str, apellido: str) -> str:
    asunto = f"Registro de relato — caso {rotulo}"
    cuerpo = (
        f"Hola,\n\nTe comparto el link para registrar tu relato en el caso {rotulo}.\n"
        f"Cuando puedas, entra y sube tu versión de los hechos:\n{link_publico(rotulo)}\n\nGracias."
    )
    return "mailto:?subject=" + urllib.parse.quote(asunto) + "&body=" + urllib.parse.quote(cuerpo)


def _procesar_pipeline(rotulo: str, relato_id: int, contenido: str):
    """Genera el resumen individual y recalcula la síntesis general del caso."""
    resumen = resumir_relato(contenido)
    models.guardar_resumen_relato(relato_id, resumen)
    models.registrar_historial(rotulo, actor="Claude", accion="Generó el resumen individual del relato recién subido.")

    relatos = models.listar_relatos(rotulo)
    entrada = [
        {"nombre": r["nombre_persona"], "formato": r["formato_entrada"], "contenido": r["contenido"], "resumen": r["resumen"]}
        for r in relatos
    ]
    caso = models.obtener_caso(rotulo)
    resultado = sintetizar_caso(caso["apellido"], entrada)
    models.guardar_sintesis_general(
        rotulo, resultado["sintesis"], resultado["interpretacion"],
        resultado["problemas"], resultado["soluciones"], resultado["nivel_urgencia"],
    )
    models.registrar_historial(
        rotulo, actor="Claude",
        accion=f"Actualizó la síntesis general del caso (nivel de urgencia: {resultado['nivel_urgencia']}).",
    )


# ------------------------------------------------------------------ rutas

@app.route("/")
def home():
    if session.get("encargado"):
        return redirect(url_for("encargado_dashboard"))
    return redirect(url_for("encargado_login"))


# --- login del encargado ---------------------------------------------

@app.route("/encargado/login", methods=["GET", "POST"])
def encargado_login():
    if request.method == "POST":
        if request.form.get("password") == ENCARGADO_PASSWORD:
            session["encargado"] = True
            destino = request.args.get("next") or url_for("encargado_dashboard")
            return redirect(destino)
        flash("Contraseña incorrecta.", "error")
    return render_template("encargado_login.html")


@app.route("/encargado/logout")
def encargado_logout():
    session.pop("encargado", None)
    return redirect(url_for("encargado_login"))


# --- panel del encargado ----------------------------------------------

@app.route("/encargado")
@login_requerido
def encargado_dashboard():
    casos = models.listar_casos()
    return render_template("encargado_dashboard.html", casos=casos)


@app.route("/encargado/casos", methods=["POST"])
@login_requerido
def encargado_crear_caso():
    apellido = (request.form.get("apellido") or "").strip()
    titulo = (request.form.get("titulo") or "").strip()
    if not apellido:
        flash("Indica el apellido para rotular el caso.", "error")
        return redirect(url_for("encargado_dashboard"))
    caso = models.crear_caso(apellido, titulo=titulo, creado_por="Encargado de convivencia")
    return redirect(url_for("encargado_caso", rotulo=caso["rotulo"]))


@app.route("/encargado/casos/<rotulo>")
@login_requerido
def encargado_caso(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    relatos = models.listar_relatos(rotulo)
    historial = models.listar_historial(rotulo)
    return render_template(
        "encargado_caso.html",
        caso=caso, relatos=relatos, historial=historial,
        problemas=models.problemas_de(caso), soluciones=models.soluciones_de(caso),
        link_publico=link_publico(rotulo),
        link_whatsapp=link_whatsapp(rotulo, caso["apellido"]),
        link_correo=link_correo(rotulo, caso["apellido"]),
    )


@app.route("/encargado/casos/<rotulo>/sintetizar", methods=["POST"])
@login_requerido
def encargado_sintetizar(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    relatos = models.listar_relatos(rotulo)
    if not relatos:
        flash("Este caso todavía no tiene relatos para sintetizar.", "error")
        return redirect(url_for("encargado_caso", rotulo=rotulo))
    entrada = [
        {"nombre": r["nombre_persona"], "formato": r["formato_entrada"], "contenido": r["contenido"], "resumen": r["resumen"]}
        for r in relatos
    ]
    resultado = sintetizar_caso(caso["apellido"], entrada)
    models.guardar_sintesis_general(
        rotulo, resultado["sintesis"], resultado["interpretacion"],
        resultado["problemas"], resultado["soluciones"], resultado["nivel_urgencia"],
    )
    models.registrar_historial(rotulo, actor="Encargado de convivencia", accion="Solicitó actualizar manualmente la síntesis general del caso.")
    flash("Síntesis general actualizada.", "ok")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


@app.route("/encargado/casos/<rotulo>/informe.pdf")
@login_requerido
def encargado_informe(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    relatos = models.listar_relatos(rotulo)
    pdf_bytes = construir_informe_pdf(caso, relatos, models.problemas_de(caso), models.soluciones_de(caso))
    models.registrar_historial(rotulo, actor="Encargado de convivencia", accion="Descargó el informe final del caso.")
    from io import BytesIO
    return send_file(
        BytesIO(pdf_bytes), mimetype="application/pdf", as_attachment=True,
        download_name=f"informe_{rotulo}.pdf",
    )


# --- página pública para subir un relato -------------------------------

@app.route("/caso/<rotulo>", methods=["GET"])
def caso_publico(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        return render_template("publico_no_encontrado.html"), 404
    return render_template("publico_caso.html", caso=caso)


@app.route("/caso/<rotulo>", methods=["POST"])
def caso_publico_enviar(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        return render_template("publico_no_encontrado.html"), 404

    nombre = (request.form.get("nombre") or "").strip()
    metodo = request.form.get("metodo") or "texto"
    if not nombre:
        flash("Por favor indica tu nombre.", "error")
        return redirect(url_for("caso_publico", rotulo=rotulo))

    archivo = request.files.get("archivo")
    if metodo == "archivo" and archivo and archivo.filename:
        try:
            contenido = extraer_texto(archivo.filename, archivo.read())
        except ExtractError as exc:
            flash(str(exc), "error")
            return redirect(url_for("caso_publico", rotulo=rotulo))
        formato = "word" if archivo.filename.lower().endswith(".docx") else (
            "pdf" if archivo.filename.lower().endswith(".pdf") else "texto")
        archivo_original = archivo.filename
    else:
        contenido = (request.form.get("relato") or "").strip()
        if not contenido:
            flash("Escribe tu relato o sube un archivo antes de enviarlo.", "error")
            return redirect(url_for("caso_publico", rotulo=rotulo))
        formato = "texto"
        archivo_original = None

    relato_id = models.agregar_relato(rotulo, nombre, formato, archivo_original, contenido)
    _procesar_pipeline(rotulo, relato_id, contenido)

    return render_template("publico_gracias.html", caso=caso, nombre=nombre)


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=puerto, debug=os.environ.get("FLASK_DEBUG") == "1")
