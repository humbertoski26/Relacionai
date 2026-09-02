# Relacionai — backend propio

Plataforma para recibir relatos personales sobre un conflicto/caso, agruparlos
en una carpeta rotulada, y generar con Claude un resumen por relato y una
síntesis general del caso con problemas identificados y soluciones posibles,
en un informe descargable para el encargado de convivencia.

## Cómo funciona

1. El **encargado** entra al panel (`/encargado`, con contraseña) y crea un
   caso indicando el apellido. La plataforma genera una carpeta con un
   **rótulo único** (`APELLIDO-FECHA-CÓDIGO`, ej. `GARRIDO-20260901-YT0A`) y un
   link público para ese caso.
2. El encargado comparte ese link con las personas que estime conveniente —
   con un botón que abre WhatsApp con el mensaje y el link ya escritos, otro
   que abre el correo con el mismo mensaje, y un botón **"Copiar link"** para
   pegarlo donde quiera. El envío manual lo hace el encargado desde su propio
   WhatsApp/correo; la plataforma no manda mensajes por sí sola en ese caso.
   Además, el encargado puede fijar una **fecha máxima de entrega** para el
   caso (se muestra a las personas al entrar a subir su relato).
3. También puede agregar una lista de **destinatarios** (correos) directamente
   en el panel: a cada uno se le envía automáticamente el link de invitación
   por correo (si el envío de correo está configurado — ver más abajo), y
   quedan en una lista de seguimiento con su estado (pendiente / completado).
   El encargado puede recordarles manualmente con un botón, y además hay una
   tarea diaria automática que les reenvía un recordatorio si aún no
   completan su relato (ver "Recordatorios automáticos" más abajo).
4. Cada persona entra al link, escribe su nombre, opcionalmente su correo (para
   recibir una copia de su propio relato), y sube su relato — como texto
   pegado, dictado por voz (botón de micrófono, revisable y editable antes de
   enviar — funciona en navegadores compatibles, como Chrome), o como archivo
   `.docx`, `.pdf` o `.txt`. No ve los relatos de las demás personas.
5. Apenas llega un relato, la plataforma confirma de inmediato (no hace
   esperar a la persona) y en segundo plano Claude genera un **resumen
   individual**, y luego recalcula automáticamente la **síntesis general del
   caso** (combinando todos los relatos recibidos hasta ese momento):
   problemas identificados, pasos según el reglamento interno si el encargado
   subió uno (ver Configuración — si el reglamento no cubre algo puntual, lo
   dice explícitamente en vez de inventar), y sugerencias de acción propias.
   Todo queda registrado con hora y quién lo hizo en el **historial** del
   caso — puede tardar uno o dos minutos en aparecer; conviene recargar la
   página del caso.
6. El encargado descarga en cualquier momento el **informe final en PDF** con
   la síntesis, los problemas identificados, los pasos del reglamento interno
   (si aplica), las sugerencias de acción, el listado de relatos incluidos, y
   su nombre y cargo al final (configurables en "Configuración").

## Stack

Flask + SQLite (un archivo, sin servidor de base de datos aparte) +
`python-docx` / `pypdf` para leer los archivos subidos + `reportlab` para
generar el informe en PDF + llamadas HTTP directas a la API de Anthropic
(sin el SDK, para no depender de paquetes adicionales).

No se usó FastAPI porque este entorno no tenía acceso a PyPI para instalarlo;
Flask ya estaba disponible y cubre perfectamente las necesidades del proyecto.

## Correr en local

```bash
pip install -r requirements.txt
cp .env.example .env   # y completa las variables (ver más abajo)
export $(cat .env | xargs)   # o usa python-dotenv / la config de tu shell
python3 app.py
```

Abre `http://localhost:5050/encargado` con la contraseña que hayas definido.

## Variables de entorno

| Variable | Para qué sirve |
|---|---|
| `SECRET_KEY` | Firma la sesión del encargado. Genera una propia y no la compartas. |
| `ENCARGADO_PASSWORD` | Contraseña de acceso al panel (un solo usuario, por ahora). |
| `ANTHROPIC_API_KEY` | **Necesaria** para que Claude genere los resúmenes y la síntesis. Se obtiene en [console.anthropic.com](https://console.anthropic.com) — es distinta de tu cuenta de claude.ai. Sin ella, la plataforma funciona igual (recibe relatos, arma el historial, genera el PDF) pero la síntesis queda marcada como "pendiente de configuración". |
| `CLAUDE_MODEL` | Modelo de Claude a usar (por defecto `claude-sonnet-4-5`). Revisa el listado vigente en la [documentación de modelos](https://docs.claude.com/en/docs/about-claude/models). |
| `PORT` | Puerto del servidor. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` | Opcionales. Si se configuran, la plataforma envía automáticamente: copia del relato a quien lo sube (si dejó su correo), invitación a cada destinatario agregado, y recordatorios. Sin ellas, todo lo demás sigue funcionando igual — solo no se manda ningún correo (se puede seguir compartiendo el link manualmente). Sirve con Gmail (contraseña de aplicación) o cualquier proveedor SMTP transaccional (Resend, SendGrid, Mailgun, etc.). |
| `TASKS_SECRET` | Opcional. Clave para proteger la ruta `POST /tasks/recordatorios` (recordatorios automáticos diarios) — ver la sección de abajo. |

### Recordatorios automáticos

La ruta `POST /tasks/recordatorios` revisa todos los casos con fecha límite
vigente y le reenvía un correo a cada destinatario que aún no ha completado
su relato (como máximo cada 20 horas por persona, para no saturar). Requiere
el header `X-Tasks-Secret` con el valor de `TASKS_SECRET` — sin ese header
(o con uno incorrecto) responde `403`.

Para que se dispare sola todos los días, hay que programar algo externo que
llame a esa ruta — por ejemplo un **Cron Job de Render** apuntando a
`https://tu-dominio/tasks/recordatorios` con ese header. No se puede correr
como un proceso Python aparte porque necesita la misma base de datos SQLite
que usa el servidor web.

## Desplegarlo para que el link funcione de verdad

Este proyecto está listo para desplegarse, pero **necesita que tú elijas dónde
correrá** — no tengo forma de dejarlo publicado en un dominio desde esta
conversación. Opciones razonables para partir (todas tienen plan gratuito o
muy barato):

- **Render.com** o **Railway.app**: conectas el repo, defines las variables
  de entorno de la tabla de arriba, y usan `gunicorn app:app` como comando de
  arranque (ya está en `requirements.txt`).
- Un **VPS propio** (DigitalOcean, etc.): `gunicorn --bind 0.0.0.0:8000 app:app`
  detrás de Nginx, con un dominio apuntando a esa IP.

Una vez desplegado, los links que genera la plataforma (`/caso/<rótulo>`) van
a usar automáticamente el dominio real donde esté corriendo — no hay que
configurar nada aparte.

### Antes de usarlo con casos reales

- Cambia `SECRET_KEY` y `ENCARGADO_PASSWORD` por valores propios — los que
  vienen en `.env.example` son solo para desarrollo.
- Sirve el sitio con HTTPS (Render/Railway lo dan gratis; en un VPS, usa
  Let's Encrypt) — se están manejando relatos personales sensibles.
- Este prototipo no tiene límite de intentos de login ni protección
  CSRF explícita en los formularios; para un uso con más de un encargado o
  con exposición pública amplia, conviene agregar `flask-wtf` (CSRF) y un
  límite de intentos de login antes de salir a producción.
- La base de datos es un archivo SQLite en `data/relacionai.db`. Para
  Render/Railway con disco efímero, monta un volumen persistente en `data/`
  (o cambia a Postgres si prefieres — el código de `models.py` es la única
  pieza que tocaría).
- Haz respaldos periódicos de `data/relacionai.db` (contiene los relatos).

## Estructura del proyecto

```
app.py            # rutas Flask (encargado + público)
models.py         # esquema SQLite y acceso a datos (casos, relatos, historial)
extract.py        # extracción de texto desde .txt / .docx / .pdf
claude_client.py  # llamadas a la API de Anthropic (resumen + síntesis)
report_pdf.py     # informe final en PDF (reportlab)
templates/        # HTML (Jinja2)
static/style.css  # estilos (misma identidad visual del prototipo Relacionai)
data/             # base de datos SQLite (se crea sola al primer arranque)
```

## Configuración (panel del encargado)

En `/encargado/configuracion` el encargado puede:

- Guardar su **nombre y cargo**, que aparecen al final de todo informe PDF descargado.
- Subir el **reglamento interno** de la institución (Word, PDF o texto). Apenas se sube,
  Claude lo lee y muestra ahí mismo un resumen de lo que entendió (para confirmar que lo leyó
  bien). De ahí en adelante, cada síntesis de caso incluye una sección propia "Pasos del
  Reglamento Interno" con lo que aplica al caso — si no hay nada aplicable, lo dice
  explícitamente en vez de inventar un procedimiento. Se aplica a todas las síntesis (nuevas
  y al volver a generar una existente) mientras el reglamento esté cargado; se puede
  reemplazar o quitar en cualquier momento.

## Próximos pasos sugeridos

- Integrarlo como módulo de GADUAI cuando corresponda.
- Autenticación real por encargado (si hay más de una persona revisando
  casos) en vez de una sola contraseña compartida.
- El procesamiento con Claude ya corre en un hilo en segundo plano (no bloquea
  la confirmación a la persona); si el volumen de relatos crece mucho, migrar
  a una cola de tareas real (Celery/RQ) para mayor robustez.
- Notificar al encargado (correo/WhatsApp) cuando llega un relato nuevo, en
  vez de que tenga que revisar el panel manualmente.
