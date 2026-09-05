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
import re
import urllib.parse
from datetime import datetime

from flask import (
    Flask, abort, flash, redirect, render_template, request,
    send_file, session, url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix

import models
import tasks
from claude_client import resumir_relato, resumir_reglamento, sintetizar_caso
from email_client import (
    enviar_copia_relato,
    enviar_informe_encargado,
    enviar_invitacion,
    enviar_notificacion_relato_nuevo,
    enviar_recordatorio,
)
from extract import ExtractError, extraer_texto
from report_docx import construir_informe_docx, construir_relato_docx

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAX_RELATOS_POR_PERSONA = 2  # por caso — al llegar al tope, el link queda deshabilitado para esa persona


def correo_valido(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "cambia-esta-clave-en-produccion")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8 MB por archivo subido — a propósito
# bajo: el servidor gratuito tiene poca memoria, y un archivo muy pesado (sobre todo un PDF
# escaneado como fotos de cada página) puede hacer que el proceso se caiga por completo
# ("Internal Server Error") en vez de simplemente demorar más.

models.init_db()


# ---------------------------------------------------------------- helpers

def login_requerido(vista):
    @functools.wraps(vista)
    def envoltura(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("encargado_login", next=request.path))
        return vista(*args, **kwargs)
    return envoltura


def admin_requerido(vista):
    """Como login_requerido, pero además exige que el usuario logueado sea administrador
    — se usa en las rutas de gestión de usuarios (crear cuentas nuevas, desactivar)."""
    @functools.wraps(vista)
    def envoltura(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("encargado_login", next=request.path))
        if not session.get("usuario_admin"):
            abort(403)
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


@app.context_processor
def inject_config_global():
    """Disponible en todas las plantillas (incluidas las públicas) — usado por base.html
    para mostrar la insignia del colegio en el encabezado de cada página, sin que cada
    vista tenga que pasarla a mano."""
    try:
        return {"config_global": models.obtener_configuracion()}
    except Exception:
        return {"config_global": None}


@app.context_processor
def inject_usuario_actual():
    """Nombre y rol de quien está logueado — para mostrarlo en la barra superior del panel
    y para decidir si se muestra el enlace a "Usuarios" (solo administradores)."""
    return {
        "usuario_actual_nombre": session.get("usuario_nombre"),
        "usuario_actual_admin": bool(session.get("usuario_admin")),
    }


def link_publico(rotulo: str) -> str:
    return url_for("caso_publico", rotulo=rotulo, _external=True)


def link_caso_privado(rotulo: str) -> str:
    return url_for("encargado_caso", rotulo=rotulo, _external=True)


def _texto_plazo(fecha_limite: str) -> str:
    return f" El plazo máximo para completarlo es el {fecha_limite}." if fecha_limite else ""


def link_whatsapp(rotulo: str, apellido: str, descripcion: str = "", fecha_limite: str = "") -> str:
    contexto = f" {descripcion.strip()[:200]}" if descripcion else ""
    mensaje = (
        f"Te comparto el link para registrar tu relato en el caso {rotulo}.{contexto} "
        f"Cuando puedas, entra y sube tu versión de los hechos: {link_publico(rotulo)}"
        f"{_texto_plazo(fecha_limite)}"
    )
    return "https://wa.me/?text=" + urllib.parse.quote(mensaje)


def _texto_dias_restantes(dias_restantes) -> str:
    if dias_restantes is None:
        return "Todavía no has completado tu relato"
    if dias_restantes < 0:
        return "El plazo para tu relato ya venció"
    if dias_restantes == 0:
        return "Hoy es el último día para completar tu relato"
    if dias_restantes == 1:
        return "Mañana vence el plazo para tu relato"
    return f"Quedan {dias_restantes} días para completar tu relato"


def link_whatsapp_recordatorio(rotulo: str, dias_restantes) -> str:
    mensaje = (
        f"Recordatorio — caso {rotulo}: {_texto_dias_restantes(dias_restantes)}. "
        f"Puedes completarlo aquí: {link_publico(rotulo)}"
    )
    return "https://wa.me/?text=" + urllib.parse.quote(mensaje)


def link_correo_recordatorio(rotulo: str, dias_restantes) -> str:
    asunto = f"Recordatorio — falta tu relato, caso {rotulo}"
    cuerpo = (
        f"Hola,\n\n{_texto_dias_restantes(dias_restantes)} en el caso {rotulo}.\n"
        f"Puedes completarlo aquí:\n{link_publico(rotulo)}\n\nGracias."
    )
    return "mailto:?subject=" + urllib.parse.quote(asunto) + "&body=" + urllib.parse.quote(cuerpo)


def actor_encargado() -> str:
    """Nombre a usar en el historial para acciones que hace la persona logueada como
    encargado — ahora que cada encargado tiene su propia cuenta (ver /encargado/usuarios),
    usa el nombre de la sesión activa, que identifica exactamente quién hizo la acción."""
    nombre = session.get("usuario_nombre")
    if nombre:
        return nombre
    config = models.obtener_configuracion()
    nombre = config["nombre_encargado"] if config else None
    return nombre or "Encargado de convivencia"


def link_correo(rotulo: str, apellido: str, descripcion: str = "", fecha_limite: str = "") -> str:
    asunto = f"Registro de relato — caso {rotulo}"
    contexto = f"\n{descripcion.strip()[:400]}\n" if descripcion else ""
    cuerpo = (
        f"Hola,\n\nTe comparto el link para registrar tu relato en el caso {rotulo}.\n{contexto}"
        f"Cuando puedas, entra y sube tu versión de los hechos:\n{link_publico(rotulo)}\n"
        f"{_texto_plazo(fecha_limite)}\n\nGracias."
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


def _procesar_relato_en_segundo_plano(rotulo: str, relato_id: int, contenido: str, nombre: str, correo: str, link_caso_privado: str = ""):
    """Se ejecuta en un hilo aparte para que la persona no tenga que esperar a Claude
    (ni al envío de su copia por correo, ni al aviso al encargado) antes de ver la
    confirmación."""
    try:
        _procesar_pipeline(rotulo, relato_id, contenido)
    except Exception:
        app.logger.exception("Error procesando en segundo plano el relato %s del caso %s", relato_id, rotulo)
    if correo:
        try:
            enviar_copia_relato(correo, nombre, rotulo, contenido)
        except Exception:
            app.logger.exception("Error enviando la copia del relato a %s", correo)
    try:
        caso = models.obtener_caso(rotulo)
        config = models.obtener_configuracion()
        correo_encargado = config["correo_encargado"] if config else None
        if caso and correo_encargado:
            nombre_colegio = (config["nombre_colegio"] if config else "") or ""
            enviar_notificacion_relato_nuevo(correo_encargado, rotulo, caso["apellido"], nombre, link_caso_privado, nombre_colegio=nombre_colegio)
    except Exception:
        app.logger.exception("Error avisando al encargado del nuevo relato en el caso %s", rotulo)


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


def _procesar_reglamento_en_segundo_plano(nombre_archivo: str, contenido_bytes: bytes):
    """Se ejecuta en un hilo aparte: lee el archivo (Word/PDF/texto) y genera el resumen
    interno que confirma que Claude estudió el reglamento recién subido.

    Se hace todo en segundo plano — no solo la llamada a Claude — porque algunos PDF
    (sobre todo reglamentos largos, con estructuras raras o casi escaneados) pueden hacer
    que la lectura misma del archivo (no solo la consulta a Claude) tarde muchísimo o se
    quede pegada. Si eso pasara en la respuesta directa al encargado, el servidor la corta
    a mitad de camino y se ve como un error; en un hilo aparte simplemente tarda más, sin
    afectar el resto de la plataforma."""
    try:
        texto = extraer_texto(nombre_archivo, contenido_bytes)
    except ExtractError as exc:
        models.guardar_error_reglamento(str(exc))
        return
    except Exception:
        app.logger.exception("Error inesperado leyendo el reglamento interno recién subido.")
        models.guardar_error_reglamento(
            "No se pudo leer este archivo. Prueba con otro formato (Word, PDF de texto o .txt)."
        )
        return

    models.guardar_reglamento(nombre_archivo, texto)
    try:
        resumen = resumir_reglamento(texto)
        models.guardar_resumen_reglamento(resumen)
    except Exception:
        app.logger.exception("Error generando en segundo plano el resumen del reglamento interno.")

    # El reglamento aplica a TODOS los casos del colegio, no solo a los nuevos: se
    # re-sintetizan los casos abiertos que ya tenían una síntesis generada, para que
    # incorporen los pasos del reglamento recién cargado (o reemplazado) sin que el
    # encargado tenga que entrar caso por caso a pedirlo manualmente.
    for c in models.listar_casos():
        if c["estado"] == "abierto" and c["sintesis_general"]:
            try:
                _recalcular_sintesis(c["rotulo"])
                models.registrar_historial(
                    c["rotulo"], actor="Claude",
                    accion="Actualizó la síntesis para incorporar el reglamento interno recién cargado.",
                )
            except Exception:
                app.logger.exception("Error re-sintetizando el caso %s tras cargar el reglamento", c["rotulo"])


def _enviar_respaldo_informe_en_segundo_plano(correo_encargado: str, rotulo: str, apellido: str, docx_bytes: bytes, nombre_archivo: str, dias_retencion: int):
    """Se ejecuta en segundo plano al descargar el informe final: le manda una copia por
    correo al encargado como respaldo. Es una función de nivel de módulo (no un closure)
    para poder encolarse en una cola de tareas real (ver tasks.py) además de en un hilo."""
    try:
        config = models.obtener_configuracion()
        nombre_colegio = config["nombre_colegio"] if config else ""
        enviar_informe_encargado(correo_encargado, rotulo, apellido, docx_bytes, nombre_archivo, dias_retencion, nombre_colegio=nombre_colegio or "")
    except Exception:
        app.logger.exception("Error enviando el respaldo del informe del caso %s a %s", rotulo, correo_encargado)


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
    if session.get("usuario_id"):
        return redirect(url_for("encargado_dashboard"))
    return redirect(url_for("encargado_login"))


# --- login del encargado ---------------------------------------------

@app.route("/encargado/login", methods=["GET", "POST"])
def encargado_login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        usuario = models.verificar_login(email, password)
        if usuario:
            session["usuario_id"] = usuario["id"]
            session["usuario_nombre"] = usuario["nombre"]
            session["usuario_admin"] = usuario["es_admin"]
            destino = request.args.get("next") or url_for("encargado_dashboard")
            return redirect(destino)
        flash("Correo o contraseña incorrectos, o la cuenta está desactivada.", "error")
    return render_template("encargado_login.html")


@app.route("/encargado/logout")
def encargado_logout():
    session.pop("usuario_id", None)
    session.pop("usuario_nombre", None)
    session.pop("usuario_admin", None)
    return redirect(url_for("encargado_login"))


# --- usuarios (cuentas del equipo de convivencia) -----------------------

@app.route("/encargado/usuarios")
@admin_requerido
def encargado_usuarios():
    return render_template("encargado_usuarios.html", usuarios=models.listar_usuarios())


@app.route("/encargado/usuarios/nuevo", methods=["POST"])
@admin_requerido
def encargado_crear_usuario():
    nombre = (request.form.get("nombre") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""
    es_admin = request.form.get("es_admin") == "on"
    if not nombre or not email:
        flash("Completa el nombre y el correo de la nueva cuenta.", "error")
        return redirect(url_for("encargado_usuarios"))
    if not correo_valido(email):
        flash("Ese correo no parece válido.", "error")
        return redirect(url_for("encargado_usuarios"))
    try:
        models.crear_usuario(nombre, email, password, es_admin=es_admin)
    except ValueError as exc:
        mensajes = {
            "password_muy_corta": "La contraseña debe tener al menos 6 caracteres.",
            "email_ya_registrado": "Ya existe una cuenta con ese correo.",
            "email_requerido": "El correo es obligatorio.",
        }
        flash(mensajes.get(str(exc), "No se pudo crear la cuenta."), "error")
        return redirect(url_for("encargado_usuarios"))
    flash(f"Cuenta creada para {nombre} ({email}).", "ok")
    return redirect(url_for("encargado_usuarios"))


@app.route("/encargado/usuarios/<int:usuario_id>/activar", methods=["POST"])
@admin_requerido
def encargado_activar_usuario(usuario_id):
    models.set_usuario_activo(usuario_id, True)
    flash("Cuenta reactivada.", "ok")
    return redirect(url_for("encargado_usuarios"))


@app.route("/encargado/usuarios/<int:usuario_id>/desactivar", methods=["POST"])
@admin_requerido
def encargado_desactivar_usuario(usuario_id):
    if usuario_id == session.get("usuario_id"):
        flash("No puedes desactivar tu propia cuenta mientras tienes la sesión abierta.", "error")
        return redirect(url_for("encargado_usuarios"))
    models.set_usuario_activo(usuario_id, False)
    flash("Cuenta desactivada — esa persona ya no podrá entrar al panel.", "ok")
    return redirect(url_for("encargado_usuarios"))


@app.route("/encargado/mi-cuenta/password", methods=["POST"])
@login_requerido
def encargado_cambiar_password():
    """Cualquier usuario logueado puede cambiar su propia contraseña (no hace falta ser
    administrador) — un administrador puede además crear/desactivar cuentas de otras
    personas en /encargado/usuarios."""
    actual = request.form.get("password_actual") or ""
    nueva = request.form.get("password_nueva") or ""
    # verificar_login necesita el email; lo tomamos del usuario logueado en vez de pedirlo
    # de nuevo en el formulario.
    usuario_actual = models.obtener_usuario(session.get("usuario_id"))
    if not usuario_actual or not models.verificar_login(usuario_actual["email"], actual):
        flash("Tu contraseña actual no es correcta.", "error")
        return redirect(url_for("encargado_configuracion"))
    try:
        models.cambiar_password(session["usuario_id"], nueva)
    except ValueError:
        flash("La contraseña nueva debe tener al menos 6 caracteres.", "error")
        return redirect(url_for("encargado_configuracion"))
    flash("Contraseña actualizada.", "ok")
    return redirect(url_for("encargado_configuracion"))


# --- panel del encargado ----------------------------------------------

@app.route("/encargado")
@login_requerido
def encargado_dashboard():
    casos = models.listar_casos()
    pendientes_raw = models.pendientes_por_urgencia()
    pendientes = {}
    for color, items in pendientes_raw.items():
        enriquecidos = []
        for it in items:
            it = dict(it)
            it["link"] = link_publico(it["rotulo"])
            it["link_whatsapp"] = link_whatsapp_recordatorio(it["rotulo"], it["dias_restantes"])
            it["link_correo"] = link_correo_recordatorio(it["rotulo"], it["dias_restantes"])
            enriquecidos.append(it)
        pendientes[color] = enriquecidos
    return render_template("encargado_dashboard.html", casos=casos, pendientes=pendientes)


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
    correo = (request.form.get("correo_encargado") or "").strip()
    if not nombre.strip() or not cargo.strip():
        flash("Completa tu nombre y cargo.", "error")
        return redirect(url_for("encargado_configuracion"))
    if not correo:
        flash("Tu correo es obligatorio — es donde se envía automáticamente el informe final de cada caso al cerrarse.", "error")
        return redirect(url_for("encargado_configuracion"))
    if not correo_valido(correo):
        flash("El correo del encargado no parece válido — revisa que tenga @ y una extensión (ej. .cl, .com).", "error")
        return redirect(url_for("encargado_configuracion"))
    models.guardar_datos_encargado(nombre, cargo, correo)
    flash("Datos del encargado actualizados.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/colegio", methods=["POST"])
@login_requerido
def encargado_guardar_colegio():
    nombre_colegio = (request.form.get("nombre_colegio") or "").strip()
    if not nombre_colegio:
        flash("Escribe el nombre del colegio.", "error")
        return redirect(url_for("encargado_configuracion"))
    models.guardar_nombre_colegio(nombre_colegio)
    flash("Nombre del colegio guardado.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/retencion", methods=["POST"])
@login_requerido
def encargado_guardar_retencion():
    crudo = (request.form.get("dias_retencion") or "").strip()
    try:
        dias = int(crudo)
    except ValueError:
        dias = 0
    if dias < 1 or dias > 365:
        flash("El período de retención debe ser un número de días entre 1 y 365.", "error")
        return redirect(url_for("encargado_configuracion"))
    models.guardar_dias_retencion(dias)
    flash(f"Período de retención actualizado a {dias} días.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/gaduai", methods=["POST"])
@login_requerido
def encargado_guardar_gaduai_url():
    url = (request.form.get("gaduai_url") or "").strip()
    models.guardar_gaduai_url(url)
    flash("Link a GADUAI guardado." if url else "Link a GADUAI eliminado.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/reglamento", methods=["POST"])
@login_requerido
def encargado_subir_reglamento():
    archivo = request.files.get("reglamento")
    if not archivo or not archivo.filename:
        flash("Elige un archivo Word o PDF con el reglamento interno.", "error")
        return redirect(url_for("encargado_configuracion"))

    nombre_archivo = archivo.filename
    contenido_bytes = archivo.read()

    # Tanto la lectura del archivo (que en algunos PDF puede tardar muchísimo o quedarse
    # pegada) como el resumen con Claude se hacen en segundo plano — ver
    # _procesar_reglamento_en_segundo_plano — para no dejar esperando al encargado ni
    # arriesgar que el servidor corte la respuesta a mitad de camino.
    models.guardar_reglamento_pendiente(nombre_archivo)
    tasks.encolar(_procesar_reglamento_en_segundo_plano, nombre_archivo, contenido_bytes)
    flash("Reglamento interno subido y en estudio — recarga esta página en unos segundos para ver la confirmación de que Claude ya lo estudió.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/reglamento/quitar", methods=["POST"])
@login_requerido
def encargado_quitar_reglamento():
    models.quitar_reglamento()
    flash("Se quitó el reglamento interno.", "ok")
    return redirect(url_for("encargado_configuracion"))


TIPOS_INSIGNIA = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


@app.route("/encargado/configuracion/insignia", methods=["POST"])
@login_requerido
def encargado_subir_insignia():
    archivo = request.files.get("insignia")
    if not archivo or not archivo.filename:
        flash("Elige una imagen (PNG, JPG o WEBP) con la insignia del colegio.", "error")
        return redirect(url_for("encargado_configuracion"))
    mime = archivo.mimetype
    if mime not in TIPOS_INSIGNIA:
        flash("Formato de imagen no compatible — usa PNG, JPG o WEBP.", "error")
        return redirect(url_for("encargado_configuracion"))
    contenido = archivo.read()
    models.guardar_insignia(contenido, mime, archivo.filename)
    flash("Insignia del colegio guardada — ya aparece en todas las páginas y en el informe final.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/encargado/configuracion/insignia/quitar", methods=["POST"])
@login_requerido
def encargado_quitar_insignia():
    models.quitar_insignia()
    flash("Se quitó la insignia del colegio.", "ok")
    return redirect(url_for("encargado_configuracion"))


@app.route("/insignia")
def ver_insignia():
    """Sirve la insignia del colegio — sin login, porque aparece en todas las páginas,
    incluida la pública donde alguien sube su relato."""
    config = models.obtener_configuracion()
    if not config or not config["insignia_bytes"]:
        abort(404)
    from io import BytesIO
    return send_file(BytesIO(config["insignia_bytes"]), mimetype=config["insignia_mime"] or "image/png")


@app.route("/encargado/casos", methods=["POST"])
@login_requerido
def encargado_crear_caso():
    apellido = (request.form.get("apellido") or "").strip()
    titulo = (request.form.get("titulo") or "").strip()
    if not apellido:
        flash("Indica el apellido para rotular el caso.", "error")
        return redirect(url_for("encargado_dashboard"))
    caso = models.crear_caso(apellido, titulo=titulo, creado_por=actor_encargado())
    return redirect(url_for("encargado_caso", rotulo=caso["rotulo"]))


def _numerar_relatos(relatos) -> dict:
    """Numera los relatos cuando la misma persona sube más de uno (Relato 1, Relato 2…) —
    se usa tanto para mostrarlos en pantalla como para nombrar el archivo al descargar uno
    por separado."""
    conteo_nombre = {}
    total_por_nombre = {}
    for r in relatos:
        clave = (r["nombre_persona"] or "").strip().lower()
        total_por_nombre[clave] = total_por_nombre.get(clave, 0) + 1
    numero_relato = {}
    for r in relatos:
        clave = (r["nombre_persona"] or "").strip().lower()
        if total_por_nombre.get(clave, 0) > 1:
            conteo_nombre[clave] = conteo_nombre.get(clave, 0) + 1
            numero_relato[r["id"]] = conteo_nombre[clave]
    return numero_relato


@app.route("/encargado/casos/<rotulo>")
@login_requerido
def encargado_caso(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    relatos = models.listar_relatos(rotulo)
    historial = models.listar_historial(rotulo)
    destinatarios = models.listar_destinatarios(rotulo)

    numero_relato = _numerar_relatos(relatos)

    # El botón "Actualizar síntesis" se destaca si llegaron relatos nuevos después de la
    # última síntesis generada.
    sintesis_desactualizada = False
    if relatos:
        ultimo_relato = max(r["subido_en"] for r in relatos)
        if not caso["sintesis_generada_en"] or ultimo_relato > caso["sintesis_generada_en"]:
            sintesis_desactualizada = bool(caso["sintesis_general"])

    descripcion = caso["mensaje_invitacion"] or ""
    return render_template(
        "encargado_caso.html",
        caso=caso, relatos=relatos, historial=historial, destinatarios=destinatarios,
        problemas=models.problemas_de(caso), soluciones=models.soluciones_de(caso),
        pasos_reglamento=models.pasos_reglamento_de(caso),
        numero_relato=numero_relato,
        sintesis_desactualizada=sintesis_desactualizada,
        link_publico=link_publico(rotulo),
        link_whatsapp=link_whatsapp(rotulo, caso["apellido"], descripcion, caso["fecha_limite"] or ""),
        link_correo=link_correo(rotulo, caso["apellido"], descripcion, caso["fecha_limite"] or ""),
        hoy=datetime.now().strftime("%Y-%m-%d"),
    )


@app.route("/encargado/casos/<rotulo>/plazo", methods=["POST"])
@login_requerido
def encargado_set_plazo(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    fecha_limite = (request.form.get("fecha_limite") or "").strip()
    models.set_fecha_limite(rotulo, fecha_limite, actor=actor_encargado())
    flash("Fecha límite actualizada." if fecha_limite else "Se quitó la fecha límite.", "ok")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


@app.route("/encargado/casos/<rotulo>/destinatarios", methods=["POST"])
@login_requerido
def encargado_agregar_destinatarios(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    # getlist(), no get(): el formulario manda varias casillas con el mismo name="emails"
    # (una por cada correo agregado con el botón "+") — con .get() solo se leía la primera.
    crudos = request.form.getlist("emails")
    emails = [e.strip() for crudo in crudos for e in re.split(r"[,\n]", crudo) if e.strip()]
    invalidos = [e for e in emails if not correo_valido(e)]
    if invalidos:
        flash("Estos correos no parecen válidos (revisa la arroba y la extensión, ej. .cl, .com): " + ", ".join(invalidos), "error")
        return redirect(url_for("encargado_caso", rotulo=rotulo))

    # Instrucción breve del caso, para incluir en la invitación — texto escrito a mano, o
    # extraído de un archivo si el encargado prefiere subir algo más largo.
    mensaje = (request.form.get("mensaje") or "").strip()
    archivo_instrucciones = request.files.get("instrucciones")
    if not mensaje and archivo_instrucciones and archivo_instrucciones.filename:
        try:
            mensaje = extraer_texto(archivo_instrucciones.filename, archivo_instrucciones.read())
        except ExtractError as exc:
            flash(str(exc), "error")
            return redirect(url_for("encargado_caso", rotulo=rotulo))
    if mensaje:
        models.guardar_mensaje_invitacion(rotulo, mensaje)

    nuevos = models.agregar_destinatarios(rotulo, emails, actor=actor_encargado())
    if not nuevos:
        flash("No se agregó ningún correo nuevo (revisa que estén bien escritos, o si ya estaban invitados).", "error")
        return redirect(url_for("encargado_caso", rotulo=rotulo))

    link = link_publico(rotulo)
    enviados = 0
    for d in nuevos:
        if enviar_invitacion(d["email"], rotulo, link, caso["fecha_limite"] or "", mensaje=mensaje):
            models.registrar_recordatorio_enviado(d["id"])
            enviados += 1
    models.registrar_historial(
        rotulo, actor=actor_encargado(),
        accion=f"Envió el link del caso a {len(nuevos)} destinatario(s) nuevo(s) ({', '.join(d['email'] for d in nuevos)}).",
    )
    flash(f"Se agregaron {len(nuevos)} destinatario(s)." + (f" Se envió la invitación por correo a {enviados}." if enviados else " No se pudo enviar el correo automático — revisa la configuración de SMTP; puedes compartir el link manualmente."), "ok")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


@app.route("/encargado/casos/<rotulo>/link/registrar", methods=["POST"])
@login_requerido
def encargado_registrar_envio_link(rotulo):
    """Beacon liviano: se llama por fetch() desde los botones de copiar link / WhatsApp /
    correo en la página del caso, solo para dejar registro en el historial de qué persona
    compartió el link y por qué medio."""
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    metodo = (request.get_json(silent=True) or {}).get("metodo") or "link"
    etiquetas = {"copiar": "Copió el link.", "whatsapp": "Compartió el link por WhatsApp.", "correo": "Compartió el link por correo."}
    models.registrar_historial(rotulo, actor=actor_encargado(), accion=etiquetas.get(metodo, "Compartió el link."))
    return {"ok": True}, 200


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
        models.registrar_historial(rotulo, actor=actor_encargado(), accion=f"Envió un recordatorio manual a {d['email']}.")
        flash(f"Recordatorio enviado a {d['email']}.", "ok")
    else:
        flash("No se pudo enviar el recordatorio — revisa la configuración de SMTP.", "error")
    if request.form.get("volver") == "dashboard":
        return redirect(url_for("encargado_dashboard"))
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
    tasks.encolar(_sintetizar_en_segundo_plano, rotulo)
    models.registrar_historial(rotulo, actor=actor_encargado(), accion="Solicitó actualizar manualmente la síntesis general del caso.")
    flash("Actualizando la síntesis general — recarga esta página en unos segundos para ver el resultado.", "ok")
    return redirect(url_for("encargado_caso", rotulo=rotulo))


@app.route("/encargado/casos/<rotulo>/informe.docx")
@login_requerido
def encargado_informe(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    dias_retencion = models.dias_retencion()
    if caso["estado"] == "purgado":
        flash(f"Este caso ya fue purgado (pasaron más de {dias_retencion} días desde que se emitió el informe) — ya no está disponible.", "error")
        return redirect(url_for("encargado_caso", rotulo=rotulo))
    relatos = models.listar_relatos(rotulo)
    config = models.obtener_configuracion()
    nombre_archivo = f"informe_{rotulo}.docx"
    docx_bytes = construir_informe_docx(
        caso, relatos, models.problemas_de(caso), models.soluciones_de(caso),
        pasos_reglamento=models.pasos_reglamento_de(caso), configuracion=config,
    )

    era_abierto = caso["estado"] == "abierto"
    models.marcar_informe_emitido(rotulo)
    models.registrar_historial(
        rotulo, actor=actor_encargado(),
        accion="Descargó el informe final del caso." + (f" El caso queda cerrado; en {dias_retencion} días se eliminará el detalle de los relatos y la síntesis, quedando solo este historial." if era_abierto else ""),
    )

    correo_encargado = config["correo_encargado"] if config else None
    if era_abierto and correo_encargado:
        tasks.encolar(_enviar_respaldo_informe_en_segundo_plano, correo_encargado, rotulo, caso["apellido"], docx_bytes, nombre_archivo, dias_retencion)

    from io import BytesIO
    return send_file(
        BytesIO(docx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=nombre_archivo,
    )


def _slug_archivo(texto: str) -> str:
    texto = re.sub(r"[^\w\s-]", "", (texto or "").strip().lower())
    return re.sub(r"[\s-]+", "-", texto) or "relato"


@app.route("/encargado/casos/<rotulo>/relatos/<int:relato_id>/descargar")
@login_requerido
def encargado_descargar_relato(rotulo, relato_id):
    """Descarga un relato individual como Word — para que el encargado pueda guardarlo en
    su computador sin tener que descargar (y con eso, cerrar) el informe completo del caso."""
    caso = models.obtener_caso(rotulo)
    if not caso:
        abort(404)
    relato = models.obtener_relato(relato_id)
    if not relato or relato["caso_id"] != caso["id"]:
        abort(404)

    numero = _numerar_relatos(models.listar_relatos(rotulo)).get(relato_id)
    docx_bytes = construir_relato_docx(caso, relato, numero=numero, configuracion=models.obtener_configuracion())
    sufijo_numero = f"-relato{numero}" if numero else ""
    nombre_archivo = f"relato_{_slug_archivo(relato['nombre_persona'])}{sufijo_numero}_{rotulo}.docx"

    from io import BytesIO
    return send_file(
        BytesIO(docx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        as_attachment=True,
        download_name=nombre_archivo,
    )


# --- página pública para subir un relato -------------------------------

@app.route("/caso/<rotulo>", methods=["GET"])
def caso_publico(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        return render_template("publico_no_encontrado.html"), 404
    if caso["estado"] != "abierto":
        return render_template("publico_cerrado.html", caso=caso), 410
    return render_template("publico_caso.html", caso=caso)


@app.route("/caso/<rotulo>", methods=["POST"])
def caso_publico_enviar(rotulo):
    caso = models.obtener_caso(rotulo)
    if not caso:
        return render_template("publico_no_encontrado.html"), 404
    if caso["estado"] != "abierto":
        return render_template("publico_cerrado.html", caso=caso), 410

    nombre = (request.form.get("nombre") or "").strip()
    correo = (request.form.get("correo") or "").strip()
    metodo = request.form.get("metodo") or "texto"
    if not nombre:
        flash("Por favor indica tu nombre.", "error")
        return redirect(url_for("caso_publico", rotulo=rotulo))
    if not correo or not correo_valido(correo):
        flash("Indica tu correo (lo necesitamos para poder enviarte una copia de tu relato) — revisa que tenga @ y una extensión válida, ej. .cl o .com.", "error")
        return redirect(url_for("caso_publico", rotulo=rotulo))

    if models.contar_relatos_de_correo(rotulo, correo) >= MAX_RELATOS_POR_PERSONA:
        flash(
            f"Ya registraste {MAX_RELATOS_POR_PERSONA} relatos con este correo en este caso — "
            "por ahora el link queda deshabilitado para ti. Si necesitas agregar algo más, "
            "contacta directamente a la persona encargada de convivencia escolar.",
            "error",
        )
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

    # El análisis con Claude (y el envío de la copia por correo, y el aviso al encargado)
    # puede tardar bastante — se hace en segundo plano para que la persona vea la
    # confirmación de inmediato en vez de tener que esperar. El link privado se arma acá
    # (con contexto de request todavía disponible) y se pasa ya armado al hilo.
    tasks.encolar(_procesar_relato_en_segundo_plano, rotulo, relato_id, contenido, nombre, correo, link_caso_privado(rotulo))

    return render_template("publico_gracias.html", caso=caso, nombre=nombre, correo=correo)


# --- job diario de mantenimiento (llamado por un Render Cron Job) -----
# Hace dos cosas: reenvía recordatorios pendientes, y purga los casos cerrados hace más
# de 15 días (borra el detalle sensible y deja solo el historial con la estadística).

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

    dias_retencion = models.dias_retencion()
    rotulos_a_purgar = models.casos_para_purgar(dias=dias_retencion)
    for rotulo in rotulos_a_purgar:
        try:
            models.purgar_caso(rotulo, dias=dias_retencion)
        except Exception:
            app.logger.exception("Error purgando el caso %s", rotulo)

    return {"revisados": len(pendientes), "enviados": enviados, "purgados": len(rotulos_a_purgar)}, 200


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=puerto, debug=os.environ.get("FLASK_DEBUG") == "1")
