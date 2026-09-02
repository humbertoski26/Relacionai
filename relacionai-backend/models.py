"""
Capa de datos de Relacionai.

Un SQLite por despliegue (archivo en data/relacionai.db). Cuatro tablas:

- casos: una "carpeta" por caso, rotulada con el apellido y la fecha de
  creación. Guarda la síntesis general una vez calculada, y opcionalmente
  una fecha límite de entrega.
- relatos: cada relato individual que llega a un caso (de la persona que
  lo vive, o de terceros a quienes el encargado les compartió el link).
- destinatarios: personas a las que el encargado invitó por correo a subir
  su relato a un caso — para poder recordarles automáticamente si no lo
  han hecho.
- historial: bitácora de todas las acciones sobre un caso (quién subió
  qué, cuándo se generó cada resumen/síntesis, cuándo se descargó el
  informe, cuándo se envió cada recordatorio), para trazabilidad.
"""

import json
import random
import re
import sqlite3
import string
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "relacionai.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS casos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rotulo TEXT UNIQUE NOT NULL,
    apellido TEXT NOT NULL,
    titulo TEXT,
    creado_en TEXT NOT NULL,
    creado_por TEXT,
    estado TEXT NOT NULL DEFAULT 'abierto',
    sintesis_general TEXT,
    interpretacion TEXT,
    problemas_json TEXT,
    soluciones_json TEXT,
    pasos_reglamento_json TEXT,
    nivel_urgencia TEXT,
    sintesis_generada_en TEXT,
    fecha_limite TEXT
);

CREATE TABLE IF NOT EXISTS relatos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caso_id INTEGER NOT NULL REFERENCES casos(id),
    nombre_persona TEXT NOT NULL,
    formato_entrada TEXT NOT NULL,
    archivo_original TEXT,
    contenido TEXT NOT NULL,
    resumen TEXT,
    subido_en TEXT NOT NULL,
    correo_persona TEXT
);

CREATE TABLE IF NOT EXISTS destinatarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caso_id INTEGER NOT NULL REFERENCES casos(id),
    email TEXT NOT NULL,
    invitado_en TEXT NOT NULL,
    ultimo_recordatorio_en TEXT,
    relato_id INTEGER REFERENCES relatos(id),
    cumplido_en TEXT
);

CREATE TABLE IF NOT EXISTS historial (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caso_id INTEGER NOT NULL REFERENCES casos(id),
    ocurrido_en TEXT NOT NULL,
    actor TEXT NOT NULL,
    accion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS configuracion (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    nombre_encargado TEXT,
    cargo_encargado TEXT,
    reglamento_nombre_archivo TEXT,
    reglamento_texto TEXT,
    reglamento_subido_en TEXT,
    reglamento_resumen TEXT
);

CREATE INDEX IF NOT EXISTS idx_relatos_caso ON relatos(caso_id);
CREATE INDEX IF NOT EXISTS idx_historial_caso ON historial(caso_id);
CREATE INDEX IF NOT EXISTS idx_destinatarios_caso ON destinatarios(caso_id);
"""

# Migración ligera: columnas agregadas después del primer despliegue.
# ALTER TABLE ... ADD COLUMN es seguro de repetir (se salta si ya existe),
# así una base de datos ya desplegada se pone al día sola al reiniciar.
_MIGRATIONS = [
    ("casos", "fecha_limite", "TEXT"),
    ("relatos", "correo_persona", "TEXT"),
    ("casos", "pasos_reglamento_json", "TEXT"),
    ("configuracion", "reglamento_resumen", "TEXT"),
    ("configuracion", "reglamento_error", "TEXT"),
    ("configuracion", "correo_encargado", "TEXT"),
    ("casos", "informe_emitido_en", "TEXT"),
    ("casos", "purgado_en", "TEXT"),
    ("casos", "n_relatos_purgado", "INTEGER"),
    ("casos", "mensaje_invitacion", "TEXT"),
    ("configuracion", "insignia_bytes", "BLOB"),
    ("configuracion", "insignia_mime", "TEXT"),
    ("configuracion", "insignia_nombre_archivo", "TEXT"),
    ("configuracion", "nombre_colegio", "TEXT"),
    ("configuracion", "dias_retencion", "INTEGER"),
]

DIAS_RETENCION_DEFECTO = 15


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    # busy_timeout: ahora que el resumen/síntesis se procesa en un hilo en
    # segundo plano, puede haber escrituras concurrentes (una petición nueva
    # + un hilo terminando el caso anterior) — sin esto, sqlite podría lanzar
    # "database is locked" en vez de simplemente esperar un poco.
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for tabla, columna, tipo in _MIGRATIONS:
            columnas = {row["name"] for row in conn.execute(f"PRAGMA table_info({tabla})")}
            if columna not in columnas:
                conn.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
        conn.execute("INSERT OR IGNORE INTO configuracion (id) VALUES (1)")


def _slug_apellido(apellido: str) -> str:
    norm = unicodedata.normalize("NFD", apellido or "")
    norm = "".join(c for c in norm if unicodedata.category(c) != "Mn")
    norm = re.sub(r"[^A-Za-z]", "", norm).upper()
    return norm[:20] or "CASO"


def _random_suffix(n=4):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))


def crear_caso(apellido: str, titulo: str = "", creado_por: str = "encargado") -> sqlite3.Row:
    fecha = datetime.now().strftime("%Y%m%d")
    with get_conn() as conn:
        while True:
            rotulo = f"{_slug_apellido(apellido)}-{fecha}-{_random_suffix()}"
            existing = conn.execute("SELECT 1 FROM casos WHERE rotulo = ?", (rotulo,)).fetchone()
            if not existing:
                break
        conn.execute(
            """INSERT INTO casos (rotulo, apellido, titulo, creado_en, creado_por, estado)
               VALUES (?, ?, ?, ?, ?, 'abierto')""",
            (rotulo, apellido.strip(), titulo.strip(), now_iso(), creado_por),
        )
        caso = conn.execute("SELECT * FROM casos WHERE rotulo = ?", (rotulo,)).fetchone()
    registrar_historial(rotulo, actor=creado_por or "Encargado", accion=f"Carpeta del caso creada para el apellido «{apellido.strip()}».")
    return caso


def obtener_caso(rotulo: str):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM casos WHERE rotulo = ?", (rotulo,)).fetchone()


def obtener_caso_por_id(caso_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM casos WHERE id = ?", (caso_id,)).fetchone()


def listar_casos():
    with get_conn() as conn:
        return conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM relatos r WHERE r.caso_id = c.id) AS n_relatos
               FROM casos c ORDER BY c.creado_en DESC"""
        ).fetchall()


def casos_pasados_resumen(excluir_rotulo: str = "", limite: int = 6):
    """
    Últimos casos ya sintetizados (para que la síntesis de un caso nuevo pueda
    considerar patrones y criterios ya usados antes en el mismo establecimiento —
    sin mezclar los hechos concretos de un caso con otro). No incluye el
    contenido de los relatos, solo problemas / pasos de reglamento / sugerencias
    ya generados.
    """
    with get_conn() as conn:
        filas = conn.execute(
            """SELECT rotulo, apellido, problemas_json, pasos_reglamento_json, soluciones_json, nivel_urgencia
               FROM casos
               WHERE sintesis_general IS NOT NULL AND rotulo != ?
               ORDER BY sintesis_generada_en DESC
               LIMIT ?""",
            (excluir_rotulo or "", limite),
        ).fetchall()
    resultado = []
    for f in filas:
        resultado.append({
            "rotulo": f["rotulo"],
            "problemas": json.loads(f["problemas_json"]) if f["problemas_json"] else [],
            "pasos_reglamento": json.loads(f["pasos_reglamento_json"]) if f["pasos_reglamento_json"] else [],
            "sugerencias": json.loads(f["soluciones_json"]) if f["soluciones_json"] else [],
            "nivel_urgencia": f["nivel_urgencia"] or "medio",
        })
    return resultado


def set_fecha_limite(rotulo: str, fecha_limite: str, actor: str = "Encargado de convivencia"):
    """fecha_limite: fecha en formato YYYY-MM-DD, o cadena vacía/None para quitarla."""
    with get_conn() as conn:
        conn.execute("UPDATE casos SET fecha_limite = ? WHERE rotulo = ?", (fecha_limite or None, rotulo))
    registrar_historial(
        rotulo, actor=actor,
        accion=(f"Definió el {fecha_limite} como fecha máxima de entrega." if fecha_limite else "Quitó la fecha máxima de entrega."),
    )


def contar_relatos_de_correo(rotulo: str, correo: str) -> int:
    """Cuántos relatos ya envió esta persona (identificada por su correo) a este caso —
    se usa para limitar a un máximo de relatos por persona y deshabilitar el link para
    ella una vez alcanzado."""
    caso = obtener_caso(rotulo)
    if not caso or not (correo or "").strip():
        return 0
    with get_conn() as conn:
        fila = conn.execute(
            "SELECT COUNT(*) AS n FROM relatos WHERE caso_id = ? AND LOWER(correo_persona) = LOWER(?)",
            (caso["id"], correo.strip()),
        ).fetchone()
        return fila["n"] if fila else 0


def agregar_relato(rotulo: str, nombre_persona: str, formato_entrada: str, archivo_original, contenido: str, correo_persona: str = ""):
    caso = obtener_caso(rotulo)
    if not caso:
        raise ValueError("caso_no_encontrado")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO relatos (caso_id, nombre_persona, formato_entrada, archivo_original, contenido, subido_en, correo_persona)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (caso["id"], nombre_persona.strip(), formato_entrada, archivo_original, contenido, now_iso(), (correo_persona or "").strip() or None),
        )
        relato_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    registrar_historial(
        rotulo,
        actor=nombre_persona.strip() or "Persona anónima",
        accion=f"Subió un relato ({formato_entrada}).",
    )
    if correo_persona:
        _marcar_destinatario_cumplido(caso["id"], correo_persona, relato_id)
    return relato_id


def guardar_resumen_relato(relato_id: int, resumen: str):
    with get_conn() as conn:
        conn.execute("UPDATE relatos SET resumen = ? WHERE id = ?", (resumen, relato_id))


def obtener_relato(relato_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM relatos WHERE id = ?", (relato_id,)).fetchone()


def listar_relatos(rotulo: str):
    caso = obtener_caso(rotulo)
    if not caso:
        return []
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM relatos WHERE caso_id = ? ORDER BY subido_en ASC", (caso["id"],)
        ).fetchall()


def guardar_sintesis_general(rotulo: str, sintesis: str, problemas: list, pasos_reglamento: list, soluciones: list, nivel_urgencia: str):
    with get_conn() as conn:
        conn.execute(
            """UPDATE casos SET sintesis_general = ?, problemas_json = ?, pasos_reglamento_json = ?,
                                 soluciones_json = ?, nivel_urgencia = ?, sintesis_generada_en = ?
               WHERE rotulo = ?""",
            (sintesis, json.dumps(problemas, ensure_ascii=False), json.dumps(pasos_reglamento, ensure_ascii=False),
             json.dumps(soluciones, ensure_ascii=False), nivel_urgencia, now_iso(), rotulo),
        )


def registrar_historial(rotulo: str, actor: str, accion: str):
    caso = obtener_caso(rotulo)
    if not caso:
        return
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO historial (caso_id, ocurrido_en, actor, accion) VALUES (?, ?, ?, ?)",
            (caso["id"], now_iso(), actor, accion),
        )


def listar_historial(rotulo: str):
    caso = obtener_caso(rotulo)
    if not caso:
        return []
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM historial WHERE caso_id = ? ORDER BY ocurrido_en DESC", (caso["id"],)
        ).fetchall()


def problemas_de(caso) -> list:
    try:
        return json.loads(caso["problemas_json"]) if caso["problemas_json"] else []
    except (json.JSONDecodeError, TypeError):
        return []


def soluciones_de(caso) -> list:
    try:
        return json.loads(caso["soluciones_json"]) if caso["soluciones_json"] else []
    except (json.JSONDecodeError, TypeError):
        return []


def pasos_reglamento_de(caso) -> list:
    try:
        return json.loads(caso["pasos_reglamento_json"]) if caso["pasos_reglamento_json"] else []
    except (json.JSONDecodeError, TypeError):
        return []


# --------------------------------------------------------------- destinatarios

def agregar_destinatarios(rotulo: str, emails: list, actor: str = "Encargado de convivencia") -> list:
    """Agrega los correos nuevos (ignora los ya invitados en este caso). Devuelve las filas creadas."""
    caso = obtener_caso(rotulo)
    if not caso:
        raise ValueError("caso_no_encontrado")
    limpios = []
    vistos = set()
    for email in emails:
        email = (email or "").strip().lower()
        if email and email not in vistos and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            vistos.add(email)
            limpios.append(email)

    creados = []
    with get_conn() as conn:
        existentes = {
            row["email"] for row in conn.execute(
                "SELECT email FROM destinatarios WHERE caso_id = ?", (caso["id"],)
            )
        }
        for email in limpios:
            if email in existentes:
                continue
            conn.execute(
                "INSERT INTO destinatarios (caso_id, email, invitado_en) VALUES (?, ?, ?)",
                (caso["id"], email, now_iso()),
            )
            row_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            creados.append(conn.execute("SELECT * FROM destinatarios WHERE id = ?", (row_id,)).fetchone())

    for d in creados:
        registrar_historial(rotulo, actor=actor, accion=f"Invitó a {d['email']} a subir su relato a este caso.")
    return creados


def listar_destinatarios(rotulo: str):
    caso = obtener_caso(rotulo)
    if not caso:
        return []
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM destinatarios WHERE caso_id = ? ORDER BY invitado_en ASC", (caso["id"],)
        ).fetchall()


def obtener_destinatario(destinatario_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM destinatarios WHERE id = ?", (destinatario_id,)).fetchone()


def _marcar_destinatario_cumplido(caso_id: int, email: str, relato_id: int):
    email = (email or "").strip().lower()
    if not email:
        return
    with get_conn() as conn:
        conn.execute(
            """UPDATE destinatarios SET relato_id = ?, cumplido_en = ?
               WHERE caso_id = ? AND email = ? AND cumplido_en IS NULL""",
            (relato_id, now_iso(), caso_id, email),
        )


def registrar_recordatorio_enviado(destinatario_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE destinatarios SET ultimo_recordatorio_en = ? WHERE id = ?",
            (now_iso(), destinatario_id),
        )


def pendientes_por_urgencia():
    """Destinatarios que aún no completan su relato, en casos abiertos, agrupados por
    urgencia según los días que faltan para el plazo de su caso — para el cuadro de
    alertas del escritorio del encargado. Se recalcula en cada carga de la página (no
    se guarda aparte), así que siempre refleja el estado actual: al enviar un link o
    completar un relato, el destinatario deja de aparecer o cambia de grupo solo.

    rojo: falta 1 día o menos (incluye vencidos). amarillo: faltan 2 días.
    verde: faltan más de 2 días. sin_plazo: el caso aún no tiene fecha límite definida
    — sin esto no hay cómo calcular la urgencia, pero el destinatario sigue pendiente,
    así que se muestra igual en su propio cuadro en vez de desaparecer silenciosamente.
    """
    hoy = datetime.now().date()
    resultado = {"rojo": [], "amarillo": [], "verde": [], "sin_plazo": []}
    with get_conn() as conn:
        filas = conn.execute(
            """SELECT d.id AS destinatario_id, d.email AS email,
                      c.rotulo AS rotulo, c.apellido AS apellido, c.fecha_limite AS fecha_limite
               FROM destinatarios d
               JOIN casos c ON c.id = d.caso_id
               WHERE d.cumplido_en IS NULL AND c.estado = 'abierto'
               ORDER BY c.fecha_limite ASC"""
        ).fetchall()
    for f in filas:
        fecha_limite = None
        if f["fecha_limite"]:
            try:
                fecha_limite = datetime.strptime(f["fecha_limite"], "%Y-%m-%d").date()
            except ValueError:
                fecha_limite = None
        dias_restantes = (fecha_limite - hoy).days if fecha_limite else None
        item = {
            "destinatario_id": f["destinatario_id"], "email": f["email"], "rotulo": f["rotulo"],
            "apellido": f["apellido"], "fecha_limite": f["fecha_limite"], "dias_restantes": dias_restantes,
        }
        if dias_restantes is None:
            resultado["sin_plazo"].append(item)
        elif dias_restantes <= 1:
            resultado["rojo"].append(item)
        elif dias_restantes == 2:
            resultado["amarillo"].append(item)
        else:
            resultado["verde"].append(item)
    return resultado


def destinatarios_para_recordar(min_horas_desde_ultimo: int = 20):
    """
    Para el job diario de recordatorios: destinatarios que aún no completan su
    relato, en casos abiertos con fecha límite futura, a quienes no se les ha
    recordado en las últimas `min_horas_desde_ultimo` horas (o nunca).
    Devuelve tuplas (destinatario_row, caso_row).
    """
    ahora = datetime.now(timezone.utc)
    hoy = ahora.strftime("%Y-%m-%d")
    resultado = []
    with get_conn() as conn:
        filas = conn.execute(
            """SELECT d.*, c.rotulo AS caso_rotulo, c.fecha_limite AS caso_fecha_limite,
                      c.apellido AS caso_apellido, c.estado AS caso_estado
               FROM destinatarios d
               JOIN casos c ON c.id = d.caso_id
               WHERE d.cumplido_en IS NULL
                 AND c.estado = 'abierto'
                 AND c.fecha_limite IS NOT NULL
                 AND c.fecha_limite >= ?"""
            , (hoy,),
        ).fetchall()
    for row in filas:
        if row["ultimo_recordatorio_en"]:
            try:
                ultimo = datetime.fromisoformat(row["ultimo_recordatorio_en"])
                horas = (ahora - ultimo).total_seconds() / 3600
                if horas < min_horas_desde_ultimo:
                    continue
            except ValueError:
                pass
        resultado.append(row)
    return resultado


# --------------------------------------------------------------- configuración

def obtener_configuracion():
    """Fila única (id=1) con los datos del encargado y el reglamento interno subido."""
    with get_conn() as conn:
        return conn.execute("SELECT * FROM configuracion WHERE id = 1").fetchone()


def guardar_datos_encargado(nombre: str, cargo: str, correo: str = ""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE configuracion SET nombre_encargado = ?, cargo_encargado = ?, correo_encargado = ? WHERE id = 1",
            ((nombre or "").strip() or None, (cargo or "").strip() or None, (correo or "").strip().lower() or None),
        )


def dias_retencion() -> int:
    """Días que se mantiene el detalle de un caso cerrado antes de purgarlo — configurable
    por colegio; 15 si nunca se ha personalizado."""
    config = obtener_configuracion()
    valor = config["dias_retencion"] if config else None
    return int(valor) if valor else DIAS_RETENCION_DEFECTO


def guardar_dias_retencion(dias: int):
    with get_conn() as conn:
        conn.execute("UPDATE configuracion SET dias_retencion = ? WHERE id = 1", (int(dias),))


def guardar_nombre_colegio(nombre_colegio: str):
    """Nombre del colegio — se muestra antes de subir el reglamento interno y la
    insignia, y sirve de referencia para saber a qué colegio pertenece esta memoria
    (esta versión ya tiene soporte de un colegio por despliegue)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE configuracion SET nombre_colegio = ? WHERE id = 1",
            ((nombre_colegio or "").strip() or None,),
        )


def guardar_reglamento_pendiente(nombre_archivo: str):
    """Se llama apenas llega el archivo, antes de intentar leerlo — así la persona ve de
    inmediato que se subió, mientras la lectura (que puede tardar, sobre todo con PDF
    pesados o con estructuras raras) se termina en segundo plano."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE configuracion
               SET reglamento_nombre_archivo = ?, reglamento_texto = NULL, reglamento_subido_en = ?,
                   reglamento_resumen = NULL, reglamento_error = NULL
               WHERE id = 1""",
            (nombre_archivo, now_iso()),
        )


def guardar_reglamento(nombre_archivo: str, texto: str, resumen: str = ""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE configuracion
               SET reglamento_nombre_archivo = ?, reglamento_texto = ?, reglamento_subido_en = ?,
                   reglamento_resumen = ?, reglamento_error = NULL
               WHERE id = 1""",
            (nombre_archivo, texto, now_iso(), resumen or None),
        )


def guardar_resumen_reglamento(resumen: str):
    with get_conn() as conn:
        conn.execute("UPDATE configuracion SET reglamento_resumen = ? WHERE id = 1", (resumen,))


def guardar_error_reglamento(mensaje: str):
    """Se usa cuando la lectura del archivo falla en segundo plano (formato no compatible,
    PDF protegido, etc.) — para que el encargado vea por qué no quedó estudiado en vez de
    ver la página cargando para siempre."""
    with get_conn() as conn:
        conn.execute("UPDATE configuracion SET reglamento_error = ? WHERE id = 1", (mensaje,))


def quitar_reglamento():
    with get_conn() as conn:
        conn.execute(
            """UPDATE configuracion
               SET reglamento_nombre_archivo = NULL, reglamento_texto = NULL, reglamento_subido_en = NULL,
                   reglamento_resumen = NULL, reglamento_error = NULL
               WHERE id = 1""",
        )


def guardar_insignia(contenido_bytes: bytes, mime: str, nombre_archivo: str):
    """Insignia/logo del colegio (opcional) — se muestra en todas las páginas de la
    aplicación y en el informe final descargable, junto al reglamento interno."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE configuracion SET insignia_bytes = ?, insignia_mime = ?, insignia_nombre_archivo = ? WHERE id = 1",
            (contenido_bytes, mime, nombre_archivo),
        )


def quitar_insignia():
    with get_conn() as conn:
        conn.execute(
            "UPDATE configuracion SET insignia_bytes = NULL, insignia_mime = NULL, insignia_nombre_archivo = NULL WHERE id = 1",
        )


# --------------------------------------------------------------- cierre y retención

def guardar_mensaje_invitacion(rotulo: str, mensaje: str):
    """Mensaje/instrucción breve del caso que se incluye al compartir el link (por
    WhatsApp, correo, o con los destinatarios agregados) — puede venir escrito a mano o
    extraído de un archivo que subió el encargado."""
    with get_conn() as conn:
        conn.execute("UPDATE casos SET mensaje_invitacion = ? WHERE rotulo = ?", ((mensaje or "").strip() or None, rotulo))


def marcar_informe_emitido(rotulo: str):
    """Se llama al descargar el informe final: cierra el caso (ya no se pueden agregar
    relatos nuevos) y arranca la cuenta regresiva de 15 días para la purga de datos."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE casos SET estado = 'cerrado', informe_emitido_en = ? WHERE rotulo = ? AND estado = 'abierto'",
            (now_iso(), rotulo),
        )


def casos_para_purgar(dias: int = None):
    """Casos cerrados cuyo informe se emitió hace `dias` días o más, y que todavía no
    fueron purgados. Si no se indica `dias`, usa el período de retención configurado
    (15 por defecto)."""
    if dias is None:
        dias = dias_retencion()
    limite = now_iso()
    with get_conn() as conn:
        filas = conn.execute(
            """SELECT rotulo, informe_emitido_en FROM casos
               WHERE estado = 'cerrado' AND informe_emitido_en IS NOT NULL"""
        ).fetchall()
    resultado = []
    ahora = datetime.now(timezone.utc)
    for f in filas:
        try:
            emitido = datetime.fromisoformat(f["informe_emitido_en"])
        except (ValueError, TypeError):
            continue
        if (ahora - emitido).days >= dias:
            resultado.append(f["rotulo"])
    return resultado


def purgar_caso(rotulo: str, dias: int = None):
    """Borra el contenido sensible del caso (relatos y síntesis) una vez cumplido el
    período de retención configurado (15 días por defecto) desde que se emitió el
    informe, dejando solo el rótulo, las fechas, el nivel de urgencia y la cantidad de
    relatos como estadística — más el historial de acciones, que no contiene el texto de
    los relatos. El caso queda inaccesible desde ese momento."""
    if dias is None:
        dias = dias_retencion()
    with get_conn() as conn:
        n_relatos = conn.execute(
            "SELECT COUNT(*) AS n FROM relatos r JOIN casos c ON c.id = r.caso_id WHERE c.rotulo = ?",
            (rotulo,),
        ).fetchone()["n"]
        caso = conn.execute("SELECT id FROM casos WHERE rotulo = ?", (rotulo,)).fetchone()
        if not caso:
            return
        conn.execute("DELETE FROM relatos WHERE caso_id = ?", (caso["id"],))
        conn.execute(
            """UPDATE casos
               SET sintesis_general = NULL, problemas_json = NULL, soluciones_json = NULL,
                   pasos_reglamento_json = NULL, mensaje_invitacion = NULL,
                   estado = 'purgado', purgado_en = ?, n_relatos_purgado = ?
               WHERE id = ?""",
            (now_iso(), n_relatos, caso["id"]),
        )
    registrar_historial(
        rotulo, actor="Sistema",
        accion=f"Se cumplieron los {dias} días desde el informe: se eliminó el contenido de los relatos y la síntesis ({n_relatos} relato(s)); queda solo este historial como respaldo estadístico.",
    )
