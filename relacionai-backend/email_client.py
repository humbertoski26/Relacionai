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
import re
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("relacionai.email")


def _limpiar(valor):
    """Saca cualquier espacio (incluido el espacio de no separación \\xa0 que Gmail deja al
    copiar la contraseña de aplicación agrupada en bloques de 4) — sin esto, smtplib falla
    con UnicodeEncodeError al armar el mensaje de autenticación."""
    return re.sub(r"\s+", "", valor) if valor else valor


def _configurado() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"))


def _credenciales():
    host = _limpiar(os.environ.get("SMTP_HOST"))
    port = int(_limpiar(os.environ.get("SMTP_PORT")) or "587")
    usuario = _limpiar(os.environ.get("SMTP_USER"))
    password = _limpiar(os.environ.get("SMTP_PASSWORD"))
    remitente = _limpiar(os.environ.get("SMTP_FROM")) or usuario
    usar_tls = os.environ.get("SMTP_USE_TLS", "1") != "0"
    return host, port, usuario, password, remitente, usar_tls


def _enviar(destinatario: str, asunto: str, cuerpo: str) -> bool:
    if not _configurado():
        logger.info("SMTP no configurado; no se envía correo a %s (%s)", destinatario, asunto)
        return False

    host, port, usuario, password, remitente, usar_tls = _credenciales()

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


def enviar_codigo_verificacion(email: str, codigo: str, rotulo: str, nombre_colegio: str = "", minutos: int = 10) -> bool:
    colegio = f" · {nombre_colegio}" if nombre_colegio else ""
    asunto = f"Tu código de verificación: {codigo} — caso {rotulo}{colegio}"
    cuerpo = (
        "Hola,\n\n"
        f"Tu código para continuar con tu relato en el caso {rotulo} es:\n\n"
        f"    {codigo}\n\n"
        f"Escríbelo en la página del relato. Vence en {minutos} minutos y sirve una sola vez.\n\n"
        "Si no pediste este código, puedes ignorar este correo: nadie podrá registrar un relato "
        "con tu correo sin él."
    )
    return _enviar(email, asunto, cuerpo)


def enviar_copia_relato(email: str, nombre: str, rotulo: str, contenido: str,
                        numero: str = "", fecha_hora: str = "", archivo: str = "", nombre_colegio: str = "") -> bool:
    """Comprobante de entrega: confirma que el relato quedó registrado y deja una copia."""
    colegio = f" · {nombre_colegio}" if nombre_colegio else ""
    asunto = f"Comprobante de entrega de tu relato — caso {rotulo}{colegio}"
    detalle = [f"Caso: {rotulo}", f"Entregado por: {nombre} ({email})"]
    if fecha_hora:
        detalle.append(f"Fecha y hora: {fecha_hora}")
    if numero:
        detalle.append(f"N° de comprobante: {numero}")
    if archivo:
        detalle.append(f"Archivo adjunto: {archivo}")
    cuerpo = (
        f"Hola {nombre},\n\n"
        "Tu relato quedó registrado correctamente. Este correo es tu comprobante de entrega.\n\n"
        + "\n".join(detalle) + "\n\n"
        "Copia de tu relato:\n"
        "----------------------------------------\n"
        f"{contenido}\n"
        "----------------------------------------\n\n"
        "Solo la persona encargada de convivencia escolar puede ver tu relato. "
        "Guarda este correo como respaldo."
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

    host, port, usuario, password, remitente, usar_tls = _credenciales()

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


def enviar_notificacion_relato_nuevo(email: str, rotulo: str, apellido: str, nombre_persona: str, link_caso: str,
                                     nombre_colegio: str = "", correo_persona: str = "", numero: str = "",
                                     fecha_hora: str = "", archivo: str = "", titulo: str = "") -> bool:
    """Comprobante para el encargado que solicitó el relato: queda como evidencia de la
    entrega, con los datos generales (sin el contenido del relato, que se revisa en el caso)."""
    prefijo = f"[{nombre_colegio}] " if nombre_colegio else ""
    asunto = f"{prefijo}Comprobante: relato recibido — caso {rotulo}"
    detalle = [f"Caso: {rotulo} (apellido {apellido})"]
    if titulo:
        detalle.append(f"Título del caso: {titulo}")
    detalle.append(f"Entregado por: {nombre_persona}" + (f" ({correo_persona}, correo verificado con código)" if correo_persona else ""))
    if fecha_hora:
        detalle.append(f"Fecha y hora: {fecha_hora}")
    if numero:
        detalle.append(f"N° de comprobante: {numero}")
    detalle.append(f"Forma de entrega: {'archivo ' + archivo if archivo else 'texto escrito o dictado'}")
    if nombre_colegio:
        detalle.append(f"Colegio: {nombre_colegio}")
    cuerpo = (
        "Hola,\n\n"
        f"Se recibió un relato en el caso {rotulo}. Este correo es el comprobante de la entrega.\n\n"
        + "\n".join(detalle) + "\n\n"
        f"Puedes revisar el relato aquí:\n{link_caso}\n\n"
        "Quien entregó el relato también recibió su comprobante con una copia."
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
