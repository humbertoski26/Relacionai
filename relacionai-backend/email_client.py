"""
Envío de correos de Relacionai: copia del relato a quien lo sube,
invitación cuando el encargado agrega un destinatario, y recordatorio
automático a quienes no han completado su relato.

Usa SMTP puro (smtplib, de la librería estándar) para no depender de
ningún proveedor en particular — funciona con Gmail (usando una
"contraseña de aplicación"), o con cualquier otro proveedor de correo o
servicio transaccional (Resend, SendGrid, Mailgun, etc.) que entregue
credenciales SMTP.

Variables de entorno esperadas:
  SMTP_HOST, SMTP_PORT (por defecto 587), SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM (por defecto SMTP_USER), SMTP_USE_TLS (por defecto "1").

Si no están configuradas, las funciones no fallan: simplemente no
envían nada (y quien las llama puede seguir con el resto del flujo).
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("relacionai.email")


def _configurado() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def _enviar(destinatario: str, asunto: str, cuerpo: str) -> bool:
    if not _configurado():
        logger.info("SMTP no configurado; no se envía correo a %s (%s)", destinatario, asunto)
        return False

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    usuario = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    remitente = os.environ.get("SMTP_FROM") or usuario
    usar_tls = os.environ.get("SMTP_USE_TLS", "1") != "0"

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = remitente
    msg["To"] = destinatario
    msg.set_content(cuerpo)

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if usar_tls:
                server.starttls()
            server.login(usuario, password)
            server.send_message(msg)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el correo a %s", destinatario)
        return False


def enviar_copia_relato(email: str, nombre: str, rotulo: str, contenido: str) -> bool:
    asunto = f"Copia de tu relato — caso {rotulo}"
    cuerpo = (
        f"Hola {nombre},\n\n"
        f"Esta es la copia del relato que registraste en el caso {rotulo}.\n\n"
        "----------------------------------------\n"
        f"{contenido}\n"
        "----------------------------------------\n\n"
        "Este correo es solo para que tengas tu propio respaldo. Si no fuiste tú quien "
        "subió este relato, puedes ignorar este mensaje."
    )
    return _enviar(email, asunto, cuerpo)


def enviar_invitacion(email: str, rotulo: str, link: str, fecha_limite: str = "", mensaje: str = "") -> bool:
    asunto = f"Te invitaron a registrar tu relato — caso {rotulo}"
    plazo = f"\nPor favor complétalo antes del {fecha_limite}.\n" if fecha_limite else ""
    contexto = f"\n{mensaje.strip()}\n" if (mensaje or "").strip() else ""
    cuerpo = (
        "Hola,\n\n"
        f"Te compartimos este link para que registres tu relato en el caso {rotulo}:\n{link}\n"
        f"{contexto}{plazo}\n"
        "Solo tú verás tu propio relato — no se comparte con otras personas que también hayan sido invitadas.\n\n"
        "Gracias."
    )
    return _enviar(email, asunto, cuerpo)


def enviar_informe_encargado(email: str, rotulo: str, apellido: str, docx_bytes: bytes, nombre_archivo: str, dias_retencion: int = 15, nombre_colegio: str = "") -> bool:
    """Envía el informe final (Word) al correo del encargado apenas se emite — sirve de
    respaldo, porque el caso se purga automáticamente pasados `dias_retencion` días.

    nombre_colegio (opcional) se antepone al asunto — cada colegio corre en su propio
    despliegue con su propia base de datos, pero una misma persona puede supervisar más
    de un colegio con la misma casilla de correo, y esto ayuda a distinguir de un vistazo
    a cuál colegio corresponde cada aviso."""
    if not _configurado():
        logger.info("SMTP no configurado; no se envía el informe del caso %s a %s", rotulo, email)
        return False

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    usuario = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    remitente = os.environ.get("SMTP_FROM") or usuario
    usar_tls = os.environ.get("SMTP_USE_TLS", "1") != "0"

    prefijo = f"[{nombre_colegio}] " if nombre_colegio else ""
    msg = EmailMessage()
    msg["Subject"] = f"{prefijo}Informe del caso {rotulo} — respaldo"
    msg["From"] = remitente
    msg["To"] = email
    msg.set_content(
        f"Hola,\n\nAdjunto el informe final del caso {rotulo} (apellido {apellido}), generado al "
        "cerrar el caso.\n\nEste correo es tu respaldo: por política de retención, el contenido "
        f"detallado del caso (los relatos y la síntesis) se elimina automáticamente {dias_retencion} días "
        "después de emitido este informe, y solo queda un registro estadístico. Guarda este archivo si "
        "necesitas conservar el detalle.\n\nGracias."
    )
    msg.add_attachment(
        docx_bytes,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=nombre_archivo,
    )

    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if usar_tls:
                server.starttls()
            server.login(usuario, password)
            server.send_message(msg)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el informe del caso %s a %s", rotulo, email)
        return False


def enviar_notificacion_relato_nuevo(email: str, rotulo: str, apellido: str, nombre_persona: str, link_caso: str, nombre_colegio: str = "") -> bool:
    """Avisa al encargado apenas llega un relato nuevo a uno de sus casos, para que no
    tenga que revisar el panel manualmente para enterarse."""
    prefijo = f"[{nombre_colegio}] " if nombre_colegio else ""
    asunto = f"{prefijo}Nuevo relato recibido — caso {rotulo}"
    cuerpo = (
        "Hola,\n\n"
        f"{nombre_persona} acaba de registrar un relato en el caso {rotulo} (apellido {apellido}).\n\n"
        f"Puedes revisarlo aquí:\n{link_caso}\n\n"
        "Gracias."
    )
    return _enviar(email, asunto, cuerpo)


def enviar_recordatorio(email: str, rotulo: str, link: str, fecha_limite: str = "") -> bool:
    asunto = f"Recordatorio: falta tu relato — caso {rotulo}"
    plazo = f"\nEl plazo para completarlo es el {fecha_limite}.\n" if fecha_limite else ""
    cuerpo = (
        "Hola,\n\n"
        f"Todavía no hemos recibido tu relato para el caso {rotulo}. Puedes registrarlo aquí:\n{link}\n"
        f"{plazo}\n"
        "Si ya lo enviaste y este correo llegó por error, puedes ignorarlo.\n\n"
        "Gracias."
    )
    return _enviar(email, asunto, cuerpo)
