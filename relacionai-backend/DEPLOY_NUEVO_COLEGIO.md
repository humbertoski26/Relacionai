# Cómo levantar RelacionAI para un colegio nuevo

RelacionAI es de **un colegio por despliegue**: cada colegio tiene su propio
servicio web, su propia base de datos y sus propias cuentas — no comparten
nada entre sí. Vender a un colegio nuevo significa repetir este
procedimiento, no tocar el código.

Tiempo estimado: 20-30 minutos la primera vez, menos de 10 con práctica.

## 1. Repite el servicio web en Render

1. En el dashboard de Render, crea un **Web Service** nuevo apuntando al
   mismo repositorio de GitHub (rama `main`, o la que estés usando).
2. Comando de arranque: `gunicorn app:app` (ya está pensado para esto, no
   hace falta cambiar nada del `requirements.txt`).
3. Nombre del servicio: usa algo que identifique al colegio, por ejemplo
   `relacionai-<nombre-colegio>` — así se distingue de un vistazo en el
   dashboard de Render, que va a tener uno por cada colegio.

## 2. Variables de entorno — cada colegio necesita las suyas, no copiarlas de otro

Estas **tienen que ser distintas para cada colegio** (nunca reutilices las de
otro despliegue):

- `SECRET_KEY`: genera una nueva y al azar (por ejemplo
  `python3 -c "import secrets; print(secrets.token_hex(32))"`).
- `ENCARGADO_PASSWORD`: una contraseña temporal cualquiera — solo se usa una
  vez, para crear la primera cuenta del colegio (ver paso 4). Después de esa
  primera vez ya no importa cuál sea.
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

## 4. Primer ingreso y datos del colegio

1. Entra a `https://<dominio-del-colegio-nuevo>/encargado/login` con el
   correo `encargado@relacionai.local` (o el que ya estuviera en "Datos del
   encargado" — normalmente ninguno todavía, en un despliegue nuevo) y la
   contraseña que pusiste en `ENCARGADO_PASSWORD`.
2. Completa el asistente en `/encargado/configuracion`: datos del encargado
   (nombre, cargo, correo — el correo es donde llega el respaldo del informe
   de cada caso), **nombre del colegio** (aparece en el informe, en cada
   relato descargado y en el asunto de los correos), reglamento interno
   (opcional) e insignia del colegio (opcional).
3. Ve a `/encargado/usuarios` y crea una cuenta con el correo y nombre reales
   de cada persona del equipo de convivencia de ese colegio — no dejes a
   todos usando la cuenta que se creó automáticamente con
   `ENCARGADO_PASSWORD`, es solo el punto de partida.

## 5. Verificación rápida antes de entregárselo al colegio

- Crea un caso de prueba y sube un relato desde el link público — confirma
  que llega el correo de copia (si SMTP está configurado) y que Claude genera
  el resumen (revisa que `ANTHROPIC_API_KEY` esté bien puesta).
- Descarga el informe de ese caso de prueba y confirma que el nombre del
  colegio y la insignia aparecen correctamente.
- Purga el caso de prueba a mano si no quieres esperar al período de
  retención (o simplemente no te preocupes: es indistinguible de un caso
  real y se purga solo).
