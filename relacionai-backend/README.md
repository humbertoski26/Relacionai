# RelacionAI — backend propio

Un producto de **GADUAI**. Plataforma para recibir relatos personales sobre un
conflicto/caso de convivencia escolar, agruparlos en una carpeta rotulada, y
generar con Claude un resumen por relato y una síntesis general del caso con
problemas identificados y soluciones posibles, en un informe descargable
(Word) para el encargado de convivencia.

## Cómo funciona

1. El **encargado** entra al panel (`/encargado`, con contraseña) y, la
   primera vez, pasa por una **configuración guiada**: primero su nombre,
   cargo y **correo**, los tres obligatorios (se usan como firma del informe
   y como destino del respaldo automático — ver más abajo), luego el nombre
   del colegio, y luego —opcional— el reglamento interno de la institución y
   la insignia del colegio. Un botón "Ir a los casos" aparece apenas el
   primer paso está listo, y se resalta con un pulso una vez que se subió el
   reglamento interno.
2. Crea un caso indicando el apellido. La plataforma genera una carpeta con
   un **rótulo único** (`APELLIDO-FECHA-CÓDIGO`, ej. `GARRIDO-20260901-YT0A`)
   y un link público para ese caso.
3. El encargado comparte ese link con las personas que estime conveniente —
   con un botón que abre WhatsApp con el mensaje y el link ya escritos, otro
   que abre el correo con el mismo mensaje, y un botón **"Copiar link"** para
   pegarlo donde quiera; los tres mensajes incluyen automáticamente una breve
   descripción del caso, si el encargado la definió (ver punto 4). Cada vez
   que se usa uno de estos tres botones queda una entrada en el **historial**
   del caso indicando qué persona (según el nombre configurado) compartió el
   link y por qué medio. El envío manual lo hace el encargado desde su propio
   WhatsApp/correo; la plataforma no manda mensajes por sí sola en ese caso.
   Además, el encargado puede fijar una **fecha máxima de entrega** para el
   caso (se muestra a las personas al entrar a subir su relato, arriba del
   link para compartir).
4. También puede agregar una lista de **destinatarios**: dos casillas de
   correo visibles con un botón **"+ Agregar otro correo"** para sumar más
   (en vez de un cuadro de texto libre) — cada correo se valida en el momento
   (falta la arroba, extensión rara como `.cl`/`.com`, etc.) y se marca en
   rojo si no es válido, tanto al escribir como al enviar. Junto con los
   correos, el encargado puede escribir una **descripción breve del caso** o,
   si prefiere algo más largo, **subir un archivo** (Word/PDF/texto) con las
   instrucciones — ese texto se usa como cuerpo de la invitación por correo y
   como contexto en los mensajes de WhatsApp/correo del punto 3. A cada
   destinatario válido se le envía automáticamente el link de invitación por
   correo (si el envío de correo está configurado — ver más abajo), y quedan
   en una lista de seguimiento con su estado (pendiente / completado). El
   encargado puede recordarles manualmente con un botón, y además hay una
   tarea diaria automática que les reenvía un recordatorio si aún no
   completan su relato (ver "Recordatorios automáticos y purga" más abajo).
   En el escritorio (`/encargado`), cuatro cuadros de alerta — rojo (falta 1
   día o menos, incluye vencidos), amarillo (2 días), verde (más de 2 días)
   y un cuarto gris **"sin plazo definido"** — muestran cuántos destinatarios
   están pendientes según cuánto falta para el plazo de su caso; el cuarto
   cuadro es para cuando el caso todavía no tiene una fecha límite definida
   (sin eso no hay cómo calcular la urgencia, pero la persona sigue
   pendiente, así que no desaparece del escritorio: se agrupa aparte en vez
   de no aparecer en ningún lado). Cada cuadro se abre con un clic y lista a
   las personas con acciones directas para copiar el link o reenviarlo por
   WhatsApp/correo como recordatorio. Se recalculan solos en cada carga de
   la página, así que un destinatario desaparece de un cuadro (o cambia de
   color) apenas completa su relato, se define/cambia el plazo del caso.
5. Cada persona entra al link, escribe su nombre, **su correo (obligatorio,
   validado)** —para recibir una copia de su propio relato—, y sube su
   relato — como texto pegado, dictado por voz (botón de micrófono, revisable
   y editable antes de enviar — funciona en navegadores compatibles, como
   Chrome), o como archivo `.docx`, `.pdf` o `.txt`. No ve los relatos de las
   demás personas. Si la misma persona sube más de un relato al mismo caso,
   el panel del encargado los numera («Relato 1», «Relato 2»…) para
   distinguirlos — hasta un **máximo de 2 relatos por correo y por caso**;
   al tercer intento el link queda deshabilitado para esa persona.
6. Apenas llega un relato, la plataforma confirma de inmediato (no hace
   esperar a la persona) y en segundo plano Claude genera un **resumen
   individual**, y luego recalcula automáticamente la **síntesis general del
   caso** (combinando todos los relatos recibidos hasta ese momento):
   problemas identificados, pasos según el reglamento interno si el encargado
   subió uno (ver Configuración — si el reglamento no cubre algo puntual, lo
   dice explícitamente en vez de inventar), y sugerencias de acción propias.
   Todo queda registrado con hora y quién lo hizo en el **historial** del
   caso — puede tardar uno o dos minutos en aparecer; conviene recargar la
   página del caso. El botón **"Actualizar síntesis"** se destaca visualmente
   cuando llegaron relatos nuevos después de la última síntesis generada.
7. El encargado descarga cuando quiera el **informe final en Word (.docx)**
   con la síntesis, los problemas identificados, los pasos del reglamento
   interno (si aplica), las sugerencias de acción, el listado de relatos
   incluidos, y su nombre y cargo al final (configurables en "Configuración").
   **Descargar el informe cierra el caso**: ya no se pueden agregar relatos
   nuevos (el link público muestra "caso cerrado" a quien lo visite después),
   y arranca la cuenta regresiva de retención de 15 días (ver el punto
   siguiente). Si el encargado configuró su correo, el mismo informe se le
   envía automáticamente por correo en ese momento, como respaldo.
8. **Retención de datos: purga automática a los 15 días.** Quince días
   después de haberse emitido el informe (es decir, de la primera descarga),
   la tarea diaria automática (la misma que reenvía recordatorios — ver más
   abajo) borra el contenido sensible del caso: los relatos completos y la
   síntesis generada. Queda solo el rótulo, el apellido, las fechas, el nivel
   de urgencia, la cantidad de relatos que tuvo, y el **historial de
   acciones** (que nunca contiene el texto de los relatos) como respaldo
   estadístico — el caso pasa a estar inaccesible para cualquier persona que
   tenga el link. Por eso el envío automático del informe al correo del
   encargado (punto 7) es importante: es la única copia completa que queda
   después de esos 15 días.
9. Cada síntesis nueva considera además, como referencia, los problemas y
   sugerencias de los últimos casos ya sintetizados en el mismo establecimiento
   (sin mezclar los hechos concretos de un caso con otro) — así la plataforma
   va acumulando criterio de un caso a otro en vez de partir de cero siempre.

## Stack

Flask + SQLite (un archivo, sin servidor de base de datos aparte) +
`python-docx` para leer los archivos Word subidos **y** generar el informe
final + `pypdf` para leer PDF + llamadas HTTP directas a la API de Anthropic
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

### Pruebas automatizadas

`test_funcional.py` cubre el flujo completo (configuración guiada, casos,
destinatarios con validación de correo, relatos por texto/voz/archivo,
síntesis, descarga del informe Word con cierre de caso, purga a los 15 días,
seguridad de la ruta de tareas, y una migración simulada de una base de datos
"vieja" sin las columnas nuevas) usando una base de datos temporal y sin
depender de red ni de credenciales reales (Claude/SMTP quedan reemplazados
por versiones falsas). Se corre a mano, no forma parte del despliegue:

```bash
python3 test_funcional.py
```

## Variables de entorno

| Variable | Para qué sirve |
|---|---|
| `SECRET_KEY` | Firma la sesión del encargado. Genera una propia y no la compartas. |
| `ENCARGADO_PASSWORD` | Contraseña de acceso al panel (un solo usuario, por ahora). |
| `ANTHROPIC_API_KEY` | **Necesaria** para que Claude genere los resúmenes y la síntesis. Se obtiene en [console.anthropic.com](https://console.anthropic.com) — es distinta de tu cuenta de claude.ai. Sin ella, la plataforma funciona igual (recibe relatos, arma el historial, genera el informe) pero la síntesis queda marcada como "pendiente de configuración". |
| `CLAUDE_MODEL` | Modelo de Claude a usar (por defecto `claude-sonnet-4-5`). Revisa el listado vigente en la [documentación de modelos](https://docs.claude.com/en/docs/about-claude/models). |
| `PORT` | Puerto del servidor. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS` | Opcionales, pero **muy recomendadas para un uso real**: si se configuran, la plataforma envía automáticamente: copia del relato a quien lo sube, invitación a cada destinatario agregado, recordatorios, y el informe final al correo del encargado al cerrar cada caso (su único respaldo antes de la purga a los 15 días — ver arriba). Sin ellas, todo lo demás sigue funcionando igual — solo no se manda ningún correo (se puede seguir compartiendo el link manualmente, pero el respaldo del informe no llega a ninguna parte). Sirve con Gmail (contraseña de aplicación) o cualquier proveedor SMTP transaccional (Resend, SendGrid, Mailgun, etc.). |
| `TASKS_SECRET` | Recomendada. Clave para proteger la ruta `POST /tasks/recordatorios` (recordatorios automáticos diarios **y purga de casos cerrados hace 15+ días**) — ver la sección de abajo. |

### Recordatorios automáticos y purga

La ruta `POST /tasks/recordatorios` hace dos cosas cada vez que se llama:

1. Revisa todos los casos con fecha límite vigente y le reenvía un correo a
   cada destinatario que aún no ha completado su relato (como máximo cada 20
   horas por persona, para no saturar).
2. Revisa todos los casos **cerrados** (informe ya descargado) cuyo informe
   se emitió hace 15 días o más, y **purga** su contenido sensible (relatos y
   síntesis), dejando solo el historial con la estadística — ver el punto 8
   de "Cómo funciona" arriba.

Requiere el header `X-Tasks-Secret` con el valor de `TASKS_SECRET` — sin ese
header (o con uno incorrecto) responde `403`.

Para que se dispare sola todos los días, hay que programar algo externo que
llame a esa ruta — por ejemplo un **Cron Job de Render** apuntando a
`https://tu-dominio/tasks/recordatorios` con ese header. Una sola tarea
programada cubre ambas cosas (recordatorios y purga); no hace falta configurar
una segunda. No se puede correr como un proceso Python aparte porque necesita
la misma base de datos SQLite que usa el servidor web.

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

### Antes de venderlo/usarlo con colegios reales — checklist importante

- **Disco persistente (crítico).** El plan gratuito de Render usa disco
  **efímero**: cada vez que el servicio se reinicia o se vuelve a desplegar,
  todo lo que esté en `data/relacionai.db` (es decir, **todos los casos,
  relatos e historial de todos los colegios**) puede perderse por completo.
  Esto es aceptable para probar, pero **no para vender el producto**: antes
  de tener clientes reales hay que pasar a un plan de Render con **disco
  persistente montado en `data/`** (Render lo ofrece como "Persistent Disk"
  en los planes pagados), o migrar `models.py` a Postgres (es la única pieza
  que tocaría). Sin esto, un simple redeploy podría borrar la información de
  un colegio sin aviso.
- Cambia `SECRET_KEY` y `ENCARGADO_PASSWORD` por valores propios — los que
  vienen en `.env.example` son solo para desarrollo. Si vendes esto a varios
  colegios, cada uno necesita su **propio despliegue** (su propia base de
  datos y su propia contraseña) — este prototipo es de un solo colegio con
  un solo encargado compartiendo la misma contraseña, no multi-tenant.
- Configura SMTP (ver tabla de variables arriba) — sin esto, el respaldo
  automático del informe antes de la purga de 15 días no se envía a ninguna
  parte, y el punto 8 de "Cómo funciona" se vuelve una pérdida de datos real.
- Sirve el sitio con HTTPS (Render/Railway lo dan gratis; en un VPS, usa
  Let's Encrypt) — se están manejando relatos personales sensibles.
- Este prototipo no tiene límite de intentos de login ni protección
  CSRF explícita en los formularios; para un uso con más de un encargado o
  con exposición pública amplia, conviene agregar `flask-wtf` (CSRF) y un
  límite de intentos de login antes de salir a producción.
- Haz respaldos periódicos de `data/relacionai.db` además del disco
  persistente (por si acaso) — contiene los relatos de los casos aún no
  purgados.
- Los archivos subidos (relatos, reglamento interno, instrucciones del caso)
  tienen un **límite de 8 MB** — a propósito bajo, porque el plan gratuito de
  Render tiene poca memoria y un archivo muy pesado (sobre todo un PDF
  escaneado como fotos de cada página) puede hacer que el proceso se caiga
  por completo en vez de solo demorar más. Si alguien sube algo más pesado,
  ve un mensaje explicándole que lo comprima o lo guarde como Word/PDF de
  texto (no como imágenes). Si se necesita aceptar archivos más grandes, hay
  que subir el plan de Render a uno con más memoria (el gratuito ronda los
  512 MB).
- Cada persona (identificada por su correo) puede enviar como máximo **2
  relatos por caso** — al tercer intento con el mismo correo, el link queda
  deshabilitado para ella y se le pide contactar directamente al encargado.

## Estructura del proyecto

```
app.py            # rutas Flask (encargado + público)
models.py         # esquema SQLite y acceso a datos (casos, relatos, historial, purga)
extract.py        # extracción de texto desde .txt / .docx / .pdf
claude_client.py  # llamadas a la API de Anthropic (resumen + síntesis)
report_docx.py    # informe final en Word (python-docx) — el que se usa
report_pdf.py     # versión anterior en PDF (reportlab) — ya no se usa, se deja de referencia
email_client.py   # envío de correos (copia de relato, invitación, recordatorio, informe final)
test_funcional.py # pruebas automatizadas de extremo a extremo (ver más arriba)
templates/        # HTML (Jinja2)
static/style.css  # estilos (identidad visual RelacionAI / GADUAI)
static/img/gaduai-logo.png  # logo oficial de GADUAI (pie de página de la app)
data/             # base de datos SQLite (se crea sola al primer arranque)
```

## Configuración (panel del encargado)

En `/encargado/configuracion`, presentado como un asistente de pasos que se
van resaltando (y atenuando una vez completos) en el orden en que conviene
llenarlos:

1. **Datos del encargado**: nombre, cargo y **correo** (los tres son
   obligatorios). El nombre y el cargo aparecen al final de todo informe Word
   descargado; el correo es donde se envía automáticamente ese mismo informe
   cada vez que se cierra un caso (respaldo antes de la purga de 15 días).
2. **Nombre del colegio** — se pide antes de subir el reglamento interno y la
   insignia, como referencia de a qué colegio pertenece este despliegue.
3. **Reglamento interno** de la institución (Word, PDF o texto) — opcional.
   Al subirlo, la página confirma de inmediato que se guardó, y tanto la
   lectura del archivo como el estudio con Claude se hacen en segundo plano
   (no hacen esperar al encargado, y no bloquean al servidor aunque un PDF en
   particular tarde mucho en leerse); recargando la página después de unos
   segundos se ve un indicador de "Reglamento estudiado ✓" (sin mostrar un
   resumen del contenido), o un aviso explicando qué pasó si el archivo no se
   pudo leer. De ahí en adelante, cada síntesis de caso incluye una sección
   propia "Pasos del Reglamento Interno" con lo que aplica al caso — si no
   hay nada aplicable, lo dice explícitamente en vez de inventar un
   procedimiento. Se aplica a todas las síntesis (nuevas y al volver a
   generar una existente) mientras el reglamento esté cargado; se puede
   reemplazar o quitar en cualquier momento. **Al subir o reemplazar el
   reglamento, los casos abiertos que ya tenían una síntesis generada se
   vuelven a sintetizar solos en segundo plano** para que incorporen los
   pasos del reglamento recién cargado — no hace falta entrar caso por
   caso a pedirlo manualmente (queda una entrada en el historial de cada
   caso afectado).

   Justo debajo, se puede subir opcionalmente la **insignia del colegio**
   (PNG, JPG o WEBP) — aparece en el encabezado de todas las páginas de la
   aplicación (junto al nombre RelacionAI) y en el informe final Word. El
   botón "Ir a los casos" se resalta (con un pulso) una vez que el reglamento
   quedó subido, para guiar al encargado al siguiente paso natural.

## Próximos pasos sugeridos

- Disco persistente / Postgres antes de tener clientes reales (ver checklist).
- Si se vende a varios colegios: separar cada uno en su propio despliegue
  (base de datos y contraseña propias) — esta versión es de un solo colegio.
- Autenticación real por encargado (si hay más de una persona revisando
  casos) en vez de una sola contraseña compartida — hoy el historial usa el
  nombre configurado en "Datos del encargado" como aproximación de "quién
  hizo la acción", que funciona bien con un solo encargado pero no distingue
  entre varias personas que compartan el mismo login.
- El procesamiento con Claude (relatos nuevos, subir el reglamento interno, y
  generar la síntesis manualmente) ya corre en un hilo en segundo plano — no
  bloquea la respuesta ni arriesga un error del servidor por demora; si el
  volumen de casos crece mucho, migrar a una cola de tareas real (Celery/RQ)
  para mayor robustez.
- Notificar al encargado (correo/WhatsApp) cuando llega un relato nuevo, en
  vez de que tenga que revisar el panel manualmente.
- Hacer configurable el período de retención (hoy 15 días fijos en
  `models.casos_para_purgar`) desde el panel, si distintos colegios quieren
  plazos distintos.
