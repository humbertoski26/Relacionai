"""
Proceso worker de la cola de tareas (RQ) — solo se necesita si se configuró
REDIS_URL (ver tasks.py). En Render, esto va como un segundo servicio (tipo
"Background Worker") aparte del servicio web, con el mismo repo y el mismo
comando de arranque: `python worker.py`.

Importa app.py para que las funciones que se encolan (el análisis con Claude,
el envío de correos, la lectura del reglamento) existan en este proceso — RQ
necesita poder importar cada función por su nombre para ejecutarla.
"""

import os
import sys

import redis
from rq import Connection, Queue, Worker

import app  # noqa: F401  (registra las funciones que se encolan desde app.py)

if __name__ == "__main__":
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        print("REDIS_URL no está configurado — no hay nada que hacer (los trabajos se ejecutan en hilos).")
        sys.exit(0)

    conexion = redis.from_url(redis_url)
    with Connection(conexion):
        worker = Worker([Queue("relacionai")])
        worker.work()
