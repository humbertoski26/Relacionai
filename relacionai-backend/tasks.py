"""
Ejecución de trabajos en segundo plano: análisis con Claude, envío de correos,
lectura del reglamento subido.

Hoy (sin configurar nada) cada uno de esos trabajos corre en un hilo de Python
(threading.Thread) dentro del mismo proceso del servidor web — funciona bien para
uno o pocos colegios, pero tiene dos límites cuando el volumen crece: si el
servidor se reinicia a mitad de un trabajo, ese trabajo se pierde sin dejar
rastro, y todos los hilos compiten por la misma memoria y CPU del proceso que
también está respondiendo páginas.

Si se define la variable de entorno REDIS_URL (al agregar un Key Value/Redis en
Render, por ejemplo), estos mismos trabajos se encolan en una cola de tareas real
(RQ) que corre en un proceso aparte (ver worker.py) — con reintentos y sin
competir por los recursos del servidor web. Sin esa variable, todo sigue
funcionando exactamente igual que antes, con hilos.
"""

import logging
import os
import threading

logger = logging.getLogger("relacionai.tasks")

REDIS_URL = os.environ.get("REDIS_URL", "").strip()

_cola = None
_intentado = False


def _obtener_cola():
    """Devuelve la cola de RQ (creándola la primera vez) o None si no hay
    REDIS_URL configurado, o si algo falla al conectar (en ese caso se cae de
    vuelta a hilos, no se pierde el trabajo)."""
    global _cola, _intentado
    if not REDIS_URL:
        return None
    if _cola is not None or _intentado:
        return _cola
    _intentado = True
    try:
        import redis
        from rq import Queue

        conexion = redis.from_url(REDIS_URL)
        conexion.ping()
        _cola = Queue("relacionai", connection=conexion)
    except Exception:
        logger.exception("No se pudo conectar a REDIS_URL — se usan hilos en segundo plano como respaldo.")
        _cola = None
    return _cola


def encolar(func, *args, **kwargs):
    """Ejecuta func(*args, **kwargs) en segundo plano — por una cola de tareas
    real (RQ) si REDIS_URL está configurado y disponible, o si no, en un hilo
    (comportamiento de siempre). `func` debe ser una función de nivel de módulo
    (no un closure/lambda) para poder encolarse de verdad: RQ necesita poder
    importarla por su nombre desde el proceso worker."""
    cola = _obtener_cola()
    if cola is not None:
        try:
            cola.enqueue(func, *args, **kwargs)
            return
        except Exception:
            logger.exception("No se pudo encolar el trabajo en RQ — se ejecuta en un hilo como respaldo.")
    threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True).start()
