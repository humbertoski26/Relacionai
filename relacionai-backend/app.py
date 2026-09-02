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
import threading
import urllib.parse
from datetime import datetime

from flask import (
    Flask, abort, flash, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

import models
from claude_client import resumir_relato, resumir_reglamento, sintetizar_caso
from email_client import enviar_copia_relato, enviar_invitacion, enviar_recordatorio
from extract import ExtractError, extraer_texto
from report_pdf import construir_informe_pdf

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB por archivo subido — a propósito
# bajo: el servidor gratuito tiene poca memoria, y un archivo muy pesado (sobre todo un PDF
# escaneado como fotos de cada página) puede hacer que el proceso se caiga por completo
# ("Internal Server Error") en vez de simplemente demorar más.

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


def _recalcular_sintesis(rotulo: str) -> dict:
    """Recalcula la síntesis general del caso a partir de los relatos actuales
    (usa la config del reglamento y los casos pasados vigentes al momento de llamarla)."""
    relatos = models.listar_relatos(rotulo)
    entrada = [
        {"nombre": r["nombre_persona"], "formato": r["formato_entrada"], "contenido": r["contenido"], "resumen": r["resumen"]}
        for r in relatos
    ]
    caso = models.obtener_caso(rotulo)
    config = models.obtener_configuracion()
    reglamento_texto = config["reglamento_texto"] if config else ""
    casos_pasados = models.casos_pasados_resumen(excluir_rotulo=rotulo)
    resultado = sintetizar_caso(caso["apellido"], entrada, reglamento_texto=reglamento_texto, casos_pasados=casos_pasados)
    models.guardar_sintesis_general(
        rotulo, resultado["sintesis"],
        resultado["problemas"], resultado["pasos_reglamento"], resultado["sugerencias"], resultado["nivel_urgencia"],
    )
    return resultado


def _procesar_pipeline(rotulo: str, relato_id: int, contenido: str):
    """Genera el resumen individual y recalcula la síntesis general del caso."""
    resumen = resumir_relato(contenido)
    models.guardar_resumen_relato(relato_id, resumen)
    models.registrar_historial(rotulo, actor="Claude", accion="Generó el resumen individual del relato recién subido.")

    resultado = _recalcular_sintesis(rotulo)
    models.registrar_historial(
        rotulo, actor="Claude",
        accion=f"Actualizó la síntesis general del caso (nivel de urgencia: {resultado['nivel_urgencia']}).",
    )


def _procesar_relato_en_segundo_plano(rotulo: str, relato_id: int, contenido: str, nombre: str, correo: str):
    """Se ejecuta en un hilo aparte para que la persona no tenga que esperar a Claude
    (ni al envío de su copia por correo) antes de ver la confirmación."""
    try:
        _procesar_pipeline(rotulo, relato_id, contenido)
    except Exception:
        app.logger.exception("Error procesando en segundo plano el relato %s del caso %s", relato_id, rotulo)
    if correo:
        try:
            enviar_copia_relato(correo, nombre, rotulo, contenido)
        except Exception:
            app.logger.exception("Error enviando la copia del relato a %s", correo)


def _sintetizar_en_segundo_plano(rotulo: str):
    """Se ejecuta en un hilo aparte al pedir manualmente 'Generar síntesis', para no
    dejar esperando al encargado (ni arriesgar un timeout del servidor) mientras Claude
    procesa — la misma razón por la que el envío de relatos ya corre en segundo plano."""
    try:
        resultado = _recalcular_sintesis(rotulo)
        models.registrar_historial(
            rotulo, actor="Encargado de convivencia",
            accion=f"Actualizó manualmente la síntesis general del caso (nivel de urgencia: {resultado['nivel_urgencia']}).",
        )
    except Exception:
        app.logger.exception("Error generando en segundo plano la síntesis manual del caso %s", rotulo)


def _procesar_reglamento_en_segundo_plano(texto: str):
    """Genera en un hilo aparte el resumen interno que confirma que Claude estudió el
    reglamento recién subido — se hace en segundo plano para que subir el archivo no
    deje esperando a el encargado mientras Claude lo procesa (una llamada a Claude puede
    tardar bastante y, si se hace de inmediato, corre el riesgo de que el servidor la
    corte a mitad de camino y se vea como un error)."""
    try:
        resumen = resumir_reglamento(texto)
        models.guardar_resumen_reglamento(resumen)
    except Exception:
        app.logger.exception("Error generando en segundo plano el resumen del reglamento interno.")


@app.errorhandler(413)
def error_archivo_grande(_exc):
    """Se activa cuando el archivo subido supera MAX_CONTENT_LENGTH — evita que la persona
    vea un error genérico del servidor y en vez de eso le explica qué hacer."""
    flash(
        "El archivo es demasiado pesado (el límite son 8 MB). Si es un PDF escaneado (fotos "
        "de cada página), prueba comprimirlo o guardarlo como Word/PDF de texto en vez de "
        "imágenes — así además el contenido se puede leer correctamente.",
        "error",
    )
    if request.path.startswith("/encargado/configuracion/reglamento"):
        return redirect(url_for("encargado_configuracion")), 302
    partes = request.path.strip("/").split("/")
    if request.path.startswith("/caso/") and len(partes) >= 2 and partes[1]:
        return redirect(url_for("caso_publico", rotulo=partes[1])), 302
    return redirect(url_for("home")), 302


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


@app.route("/encargado/configuracion", methods=["GET"])
@login_requerido
def encargado_configuracion():
    config = models.obtener_configuracion()
    return render_template("encargado_configuracion.html", config=config)


@app.route("/encargado/configuracion/datos", methods=["POST"])
@login_requerido
def encargado_guardar_datos():
    nombre = request.form.get("nombre_encargado") or ""
    cargo = request.form.get("cargo_encargado") or ""
    models.guardar_datos_encargado(nombre, cargo)
    flash("Datos del encargado actualizados.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/reglamento", methods=["POST"])
@login_requerido
def encargado_subir_reglamento():
    archivo = request.files.get("reglamento")
    if not archivo or not archivo.filename:
        flash("Elige un archivo Word o PDF con el reglamento interno.", "error")
        return redirect(url_for("encargado_configuracion"))
    try:
        texto = extraer_texto(archivo.filename, archivo.read())
    except ExtractError as exc:
        flash(str(exc), "error")
        return redirect(url_for("encargado_configuracion"))

    # Se guarda el archivo de inmediato (sin resumen todavía) y el estudio con Claude
    # se hace en segundo plano, para no dejar esperando al encargado — ver
    # _procesar_reglamento_en_segundo_plano.
    models.guardar_reglamento(archivo.filename, texto)
    threading.Thread(
        target=_procesar_reglamento_en_segundo_plano,
        args=(texto,),
        daemon=True,
    ).start()
    flash("Reglamento interno subido y en estudio — recarga esta página en unos segundos para ver la confirmación de que Claude ya lo estudió.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/reglamento/quitar", methods=["POST"])
@login_requerido
def encargado_quitar_reglamento():
    models.quitar_reglamento()
    flash("Se quitó el reglamento interno.", "ok")
    return redirect(url_for("encargado_configuracion"))


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
    destinatarios = models.listar_destinatarios(rotulo)
    return render_template(
        "encargado_caso.html",
        caso=caso, relatos=relatos, historial=historial, destinatarios=destinatarios,
        problemas=models.problemas_de(caso), soluciones=models.soluciones_de(caso),
        pasos_reglamento=models.pasos_reglamento_de(caso),
        link_publico=link_publico(rotulo),
        link_whatsapp=link_whatsapp(rotulo, caso["apellido"]),
        link_correo=link_correo(rotulo, caso["apellido"]),
        hoy=datetime.now().strftime("%Y-%m-%d"),
    )


@app.route("/encargado/casos/<rotulo>/plazo", methods=["POST"])
@login_requerido
def encargado_set_plazo(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    fecha_limite = (request.form.get("fecha_limite") or "").strip()
    models.set_fecha_limite(rotulo, fecha_limite)
    flash("Fecha límite actualizada." if fecha_limite else "Se quitó la fecha límite.", "ok")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


@app.route("/encargado/casos/<rotulo>/destinatarios", methods=["POST"])
@login_requerido
def encargado_agregar_destinatarios(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    crudo = request.form.get("emails") or ""
    emails = [e.strip() for e in crudo.replace(",", "\n").splitlines()]
    nuevos = models.agregar_destinatarios(rotulo, emails)
    if not nuevos:
        flash("No se agregó ningún correo nuevo (revisa que estén bien escritos, o si ya estaban invitados).", "error")
        return redirect(url_for("encargado_caso", rotulo=rotulo))

    link = link_publico(rotulo)
    enviados = 0
    for d in nuevos:
        if enviar_invitacion(d["email"], rotulo, link, caso["fecha_limite"] or ""):
            models.registrar_recordatorio_enviado(d["id"])
            enviados += 1
    flash(f"Se agregaron {len(nuevos)} destinatario(s)." + (f" Se envió la invitación por correo a {enviados}." if enviados else " No se pudo enviar el correo automático — revisa la configuración de SMTP; puedes compartir el link manualmente."), "ok")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


@app.route("/encargado/casos/<rotulo>/destinatarios/<int:destinatario_id>/recordar", methods=["POST"])
@login_requerido
def encargado_recordar_destinatario(rotulo, destinatario_id):
    caso = models.obtener_caso(rotulo)
    d = models.obtener_destinatario(destinatario_id)
    if not caso or not d or d["caso_id"] != caso["id"]:
        abort(404)
    ok = enviar_recordatorio(d["email"], rotulo, link_publico(rotulo), caso["fecha_limite"] or "")
    if ok:
        models.registrar_recordatorio_enviado(destinatario_id)
        models.registrar_historial(rotulo, actor="Encargado de convivencia", accion=f"Envió un recordatorio manual a {d['email']}.")
        flash(f"Recordatorio enviado a {d['email']}.", "ok")
    else:
        flash("No se pudo enviar el recordatorio — revisa la configuración de SMTP.", "error")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


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

    # Se hace en segundo plano (mismo motivo que el envío de relatos): la llamada a
    # Claude puede tardar bastante y no conviene dejar esperando al encargado ni
    # arriesgar que el servidor la corte a mitad de camino.
    threading.Thread(target=_sintetizar_en_segundo_plano, args=(rotulo,), daemon=True).start()
    models.registrar_historial(rotulo, actor="Encargado de convivencia", accion="Solicitó actualizar manualmente la síntesis general del caso.")
    flash("Actualizando la síntesis general — recarga esta página en unos segundos para ver el resultado.", "ok")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


@app.route("/encargado/casos/<rotulo>/informe.pdf")
@login_requerido
def encargado_informe(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    relatos = models.listar_relatos(rotulo)
    config = models.obtener_configuracion()
    pdf_bytes = construir_informe_pdf(
        caso, relatos, models.problemas_de(caso), models.soluciones_de(caso),
        pasos_reglamento=models.pasos_reglamento_de(caso), configuracion=config,
    )
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
    correo = (request.form.get("correo") or "").strip()
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

    relato_id = models.agregar_relato(rotulo, nombre, formato, archivo_original, contenido, correo_persona=correo)

    # El análisis con Claude (y el envío de la copia por correo) puede tardar
    # bastante — se hace en segundo plano para que la persona vea la confirmación
    # de inmediato en vez de tener que esperar.
    threading.Thread(
        target=_procesar_relato_en_segundo_plano,
        args=(rotulo, relato_id, contenido, nombre, correo),
        daemon=True,
    ).start()

    return render_template("publico_gracias.html", caso=caso, nombre=nombre, correo=correo)


# --- job diario de recordatorios (llamado por un Render Cron Job) -----

@app.route("/tasks/recordatorios", methods=["POST"])
def tarea_recordatorios():
    secreto_esperado = os.environ.get("TASKS_SECRET")
    if not secreto_esperado or request.headers.get("X-Tasks-Secret") != secreto_esperado:
        abort(403)

    pendientes = models.destinatarios_para_recordar()
    enviados = 0
    for d in pendientes:
        ok = enviar_recordatorio(d["email"], d["caso_rotulo"], link_publico(d["caso_rotulo"]), d["caso_fecha_limite"] or "")
        if ok:
            models.registrar_recordatorio_enviado(d["id"])
            models.registrar_historial(d["caso_rotulo"], actor="Sistema", accion=f"Envió un recordatorio automático a {d['email']}.")
            enviados += 1
    return {"revisados": len(pendientes), "enviados": enviados}, 200


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=puerto, debug=os.environ.get("FLASK_DEBUG") == "1")
