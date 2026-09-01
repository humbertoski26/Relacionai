"""
Capa de datos de Relacionai.

Un SQLite por despliegue (archivo en data/relacionai.db). Tres tablas:

- casos: una "carpeta" por caso, rotulada con el apellido y la fecha de
  creación. Guarda la síntesis general una vez calculada.
- relatos: cada relato individual que llega a un caso (de la persona que
  lo vive, o de terceros a quienes el encargado les compartió el link).
- historial: bitácora de todas las acciones sobre un caso (quién subió
  qué, cuándo se generó cada resumen/síntesis, cuándo se descargó el
  informe), para trazabilidad.
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
    nivel_urgencia TEXT,
    sintesis_generada_en TEXT
);

CREATE TABLE IF NOT EXISTS relatos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caso_id INTEGER NOT NULL REFERENCES casos(id),
    nombre_persona TEXT NOT NULL,
    formato_entrada TEXT NOT NULL,
    archivo_original TEXT,
    contenido TEXT NOT NULL,
    resumen TEXT,
    subido_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS historial (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caso_id INTEGER NOT NULL REFERENCES casos(id),
    ocurrido_en TEXT NOT NULL,
    actor TEXT NOT NULL,
    accion TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relatos_caso ON relatos(caso_id);
CREATE INDEX IF NOT EXISTS idx_historial_caso ON historial(caso_id);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


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


def listar_casos():
    with get_conn() as conn:
        return conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM relatos r WHERE r.caso_id = c.id) AS n_relatos
               FROM casos c ORDER BY c.creado_en DESC"""
        ).fetchall()


def agregar_relato(rotulo: str, nombre_persona: str, formato_entrada: str, archivo_original, contenido: str):
    caso = obtener_caso(rotulo)
    if not caso:
        raise ValueError("caso_no_encontrado")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO relatos (caso_id, nombre_persona, formato_entrada, archivo_original, contenido, subido_en)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (caso["id"], nombre_persona.strip(), formato_entrada, archivo_original, contenido, now_iso()),
        )
        relato_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    registrar_historial(
        rotulo,
        actor=nombre_persona.strip() or "Persona anónima",
        accion=f"Subió un relato ({formato_entrada}).",
    )
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


def guardar_sintesis_general(rotulo: str, sintesis: str, interpretacion: str, problemas: list, soluciones: list, nivel_urgencia: str):
    with get_conn() as conn:
        conn.execute(
            """UPDATE casos SET sintesis_general = ?, interpretacion = ?, problemas_json = ?,
                                 soluciones_json = ?, nivel_urgencia = ?, sintesis_generada_en = ?
               WHERE rotulo = ?""",
            (sintesis, interpretacion, json.dumps(problemas, ensure_ascii=False),
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
