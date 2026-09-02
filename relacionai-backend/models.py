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
]


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


def set_fecha_limite(rotulo: str, fecha_limite: str):
    """fecha_limite: fecha en formato YYYY-MM-DD, o cadena vacía/None para quitarla."""
    with get_conn() as conn:
        conn.execute("UPDATE casos SET fecha_limite = ? WHERE rotulo = ?", (fecha_limite or None, rotulo))
    registrar_historial(
        rotulo, actor="Encargado de convivencia",
        accion=(f"Definió el {fecha_limite} como fecha máxima de entrega." if fecha_limite else "Quitó la fecha máxima de entrega."),
    )


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

def agregar_destinatarios(rotulo: str, emails: list) -> list:
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
        registrar_historial(rotulo, actor="Encargado de convivencia", accion=f"Invitó a {d['email']} a subir su relato a este caso.")
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


def guardar_datos_encargado(nombre: str, cargo: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE configuracion SET nombre_encargado = ?, cargo_encargado = ? WHERE id = 1",
            ((nombre or "").strip() or None, (cargo or "").strip() or None),
        )


def guardar_reglamento(nombre_archivo: str, texto: str, resumen: str = ""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE configuracion
               SET reglamento_nombre_archivo = ?, reglamento_texto = ?, reglamento_subido_en = ?,
                   reglamento_resumen = ?
               WHERE id = 1""",
            (nombre_archivo, texto, now_iso(), resumen or None),
        )


def guardar_resumen_reglamento(resumen: str):
    with get_conn() as conn:
        conn.execute("UPDATE configuracion SET reglamento_resumen = ? WHERE id = 1", (resumen,))


def quitar_reglamento():
    with get_conn() as conn:
        conn.execute(
            """UPDATE configuracion
               SET reglamento_nombre_archivo = NULL, reglamento_texto = NULL, reglamento_subido_en = NULL,
                   reglamento_resumen = NULL
               WHERE id = 1""",
        )
