# Cómo levantar RelacionAI para un colegio nuevo

RelacionAI es de **un colegio por despliegue**: cada colegio tiene su propio
servicio web, su propia base de datos y sus propias cuentas — no comparten
nada entre sí. Vender a un colegio nuevo significa repetir este
procedimiento, no tocar el código.

**Relacionai es parte de GADUAI en cada colegio que se abre:** este
procedimiento se hace siempre junto con el alta del colegio en GADUAI (en el
GADUAI compartido `triage-gaduai`, o en uno dedicado). Antes de empezar,
anota el **id del colegio en GADUAI** (lo que va después de `?colegio=` en su
link, por ejemplo `colegio-evolucion`; en un GADUAI dedicado es su
`DEFAULT_COLEGIO_ID`).

Tiempo estimado: 20-30 minutos la primera vez, menos de 10 con práctica.

## 1. Repite el servicio web en Render

1. En el dashboard de Render, crea un **Web Service** nuevo apuntando al
   mismo repositorio de GitHub (rama `main`, o la que estés usando).
2. Comando de arranque: `gunicorn app:app` (ya está pensado para esto, no
   hace falta cambiar nada del `requirements.txt`).
3. Nombre del servicio: usa algo que identifique al colegio, por ejemplo
   `relacionai-<nombre-colegio>` — así se distingue de un vistazo en el
   dashboard de Render, que va a tener uno por cada colegio.

## 2. Base de datos y variables de entorno — cada colegio necesita las suyas

Primero crea en Render una **base Postgres nueva** para este colegio
(`relacionai-<nombre-colegio>-db`) y copia su **Internal Database URL** en la
variable `DATABASE_URL` del servicio. Es obligatoria: en Render, Relacionai
no arranca sin ella (el archivo local se borra en cada despliegue).

Estas conectan este Relacionai con su colegio en GADUAI:

- `GADUAI_COLEGIO_ID`: el id del colegio en GADUAI (ver arriba). Obligatoria:
  sin ella nadie puede entrar sin clave desde GADUAI, y con ella Relacionai
  rechaza accesos de cualquier otro colegio y le dice a GADUAI a qué colegio
  van sus relatos y avisos.
- `GADUAI_ADMIN_KEY`: la misma llave de administración de GADUAI
  (`ADMIN_SETUP_KEY`) que usan todos los Relacionai.

Estas **tienen que ser distintas para cada colegio** (nunca reutilices las de
otro despliegue):

- `SECRET_KEY`: genera una nueva y al azar (por ejemplo
  `python3 -c "import secrets; print(secrets.token_hex(32))"`).
- `SSO_SHARED_SECRET`: el mismo valor que tiene GADUAI para este colegio —
  es lo que permite que las cuentas se creen solas al cruzar desde ahí (ver
  paso 4). Sin esto configurado en ambos lados, nadie puede entrar a
  Relacionai — no hay contraseña temporal de respaldo.
- `ANTHROPIC_API_KEY`: si cada colegio se factura o se mide por separado,
  usa una API key distinta por colegio; si no importa, puedes reutilizar la
  misma en todos.
- `TASKS_SECRET`: genera uno distinto por colegio (mismo comando que
  `SECRET_KEY`).

Estas son las mismas variables de siempre — revisa la tabla completa en
`README.md`. Si vas a usar Postgres o la cola de tareas real (ver checklist
de "Antes de venderlo" en `README.md`), agrega también `DATABASE_URL` y/o
`REDIS_URL` — cada colegio necesita su propia base de datos y su propio
Redis, nunca compartidos entre colegios (mezclarías los relatos de un
colegio con los de otro).

Estas pueden ser las mismas para todos los colegios si usas el mismo
proveedor de correo para todos:

- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_USE_TLS`.
- `SMTP_FROM`: considera que diga algo genérico de GADUAI, ya que el nombre
  del colegio se ve igual en el asunto de cada correo (`[Nombre del colegio] ...`).

## 3. (Opcional pero recomendado) Cron Job de recordatorios y purga

Cada colegio necesita su propio Cron Job de Render apuntando a
`https://<dominio-de-ese-colegio>/tasks/recordatorios` con el header
`X-Tasks-Secret` puesto al valor del `TASKS_SECRET` **de ese colegio** — no
sirve un solo Cron Job para todos, porque cada uno le pega a un dominio
distinto.

## 3b. Respaldos

En la tarea de respaldos `gaduai-respaldos` agrega la variable
`RESPALDO_DB_RELACIONAI_<NOMBRE_COLEGIO>` con la Internal Database URL de la
base nueva, y corre la tarea una vez (Trigger Run) para confirmar "N de N
bases OK".

## 4. Primer ingreso y datos del colegio

Las cuentas de Relacionai ya no se crean acá — se crean en el máster de
GADUAI de ese colegio y llegan solas la primera vez que cada persona cruza
por el botón "Relacionai" del encabezado (SSO). No hay contraseña temporal
ni formulario de "crear cuenta" en Relacionai.

1. En GADUAI, pega la URL de este despliegue en `relacionai_url` del colegio
   (panel de administrador de GADUAI) y confirma que `SSO_SHARED_SECRET` es
   el mismo valor en ambos servicios.
2. Entra a GADUAI como Director ejecutivo/máster de ese colegio (la cuenta
   ya existe: se crea sola al crear el colegio en GADUAI) y cruza a
   Relacionai con el botón del encabezado — esa primera cuenta queda creada
   en Relacionai automáticamente, ya como administrador.
3. Completa el asistente en `/encargado/configuracion`: datos del encargado
   (nombre, cargo, correo — el correo es donde llega el respaldo del informe
   de cada caso), **nombre del colegio** (aparece en el informe, en cada
   relato descargado y en el asunto de los correos), reglamento interno
   (opcional), insignia del colegio (opcional) y **Link a GADUAI** (el link
   con que ese colegio entra a GADUAI). Cada sección tiene su propio botón
   Guardar.
4. El resto del equipo de convivencia (Encargado de Convivencia, Director/a
   de colegio) obtiene su propia cuenta en Relacionai la primera vez que
   entra a GADUAI con su perfil y cruza por el mismo botón — no hace falta
   crear nada a mano.

## 5. Verificación rápida antes de entregárselo al colegio

- Crea un caso de prueba y sube un relato desde el link público — confirma
  que llega el correo de copia (si SMTP está configurado) y que Claude genera
  el resumen (revisa que `ANTHROPIC_API_KEY` esté bien puesta).
- Descarga el informe de ese caso de prueba y confirma que el nombre del
  colegio y la insignia aparecen correctamente.
- Purga el caso de prueba a mano si no quieres esperar al período de
  retención (o simplemente no te preocupes: es indistinguible de un caso
  real y se purga solo).
