"""
Integración con la API de Claude (Anthropic) para Relacionai.

Dos operaciones:
  - resumir_relato(texto): resumen breve de UN relato individual, apenas
    llega a la carpeta del caso.
  - sintetizar_caso(apellido, relatos): al acumularse relatos en la
    carpeta, combina los resúmenes (o el texto completo si son pocos) y
    devuelve una síntesis general del caso, los problemas identificados,
    una interpretación y posibles soluciones.

Requiere la variable de entorno ANTHROPIC_API_KEY. Si no está configurada,
las funciones NO fallan: devuelven un resultado marcado como "pendiente de
configuración" para que el resto de la plataforma (carga de relatos,
historial, carpetas) se pueda seguir usando y probando igual.
"""

import json
import os
import re

import requests

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
REQUEST_TIMEOUT = 90


class ClaudeError(Exception):
    """Error al llamar a Claude; el mensaje ya está pensado para mostrarse al encargado."""


def _api_key_configurada() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _llamar_claude(prompt: str, max_tokens: int = 1200) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ClaudeError("falta_api_key")

    try:
        resp = requests.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": DEFAULT_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ClaudeError(f"No se pudo conectar con Claude: {exc}") from exc

    if resp.status_code != 200:
        detalle = resp.text[:300]
        raise ClaudeError(f"Claude respondió con error {resp.status_code}: {detalle}")

    data = resp.json()
    partes = data.get("content", [])
    texto = "".join(p.get("text", "") for p in partes if p.get("type") == "text")
    if not texto.strip():
        raise ClaudeError("Claude no devolvió texto.")
    return texto


def _parsear_json(texto: str) -> dict:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)```", texto, re.S)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    start = texto.find("{")
    end = texto.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(texto[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ClaudeError("La respuesta de Claude no vino en el formato JSON esperado.")


def resumir_relato(texto: str) -> str:
    if not _api_key_configurada():
        return (
            "[Resumen pendiente: configura ANTHROPIC_API_KEY en el servidor para que "
            "Claude genere automáticamente el resumen de este relato.]"
        )
    prompt = (
        "Eres analista de convivencia y resolución de conflictos. Lee el siguiente relato "
        "personal (transcrito de audio a texto, puede tener errores de transcripción) y "
        "escribe un resumen objetivo de 2 a 4 frases, en tercera persona, sin juicios de valor, "
        "que capture los hechos y lo que la persona plantea como problema.\n\n"
        "No agregues introducciones ni texto fuera del resumen mismo.\n\n"
        f"RELATO:\n\"\"\"\n{texto[:16000]}\n\"\"\"\n"
    )
    try:
        return _llamar_claude(prompt, max_tokens=400).strip()
    except ClaudeError as exc:
        return f"[No se pudo generar el resumen automático: {exc}]"


def sintetizar_caso(apellido: str, relatos: list) -> dict:
    """
    relatos: lista de dicts {nombre, formato, contenido, resumen}
    Devuelve: {sintesis, interpretacion, problemas: [...], soluciones: [...], nivel_urgencia}
    """
    if not _api_key_configurada():
        return {
            "sintesis": "Síntesis pendiente: configura ANTHROPIC_API_KEY en el servidor.",
            "interpretacion": "",
            "problemas": [],
            "soluciones": [],
            "nivel_urgencia": "medio",
        }

    bloques = []
    for i, r in enumerate(relatos, start=1):
        cuerpo = r.get("resumen") or r.get("contenido", "")
        bloques.append(f"--- Relato {i} (de: {r.get('nombre') or 'persona no identificada'}) ---\n{cuerpo[:4000]}")
    relatos_texto = "\n\n".join(bloques)[:40000]

    prompt = (
        "Eres una persona analista experta en relaciones interpersonales y convivencia, apoyando a "
        "quien coordina un caso. A continuación hay varios relatos personales recogidos sobre un mismo "
        "caso (identificado por el apellido «" + apellido + "»), posiblemente contados por distintas "
        "personas involucradas o testigos. Cada uno puede tener una versión distinta de los hechos.\n\n"
        "RELATOS DEL CASO:\n\"\"\"\n" + relatos_texto + "\n\"\"\"\n\n"
        "Responde ÚNICAMENTE con un objeto JSON con esta forma exacta (sin texto fuera del JSON):\n"
        "{\n"
        '  "sintesis": "síntesis general del caso integrando todos los relatos, en 4 a 8 frases, '
        'en tercera persona, sin tomar partido por una sola versión cuando hay versiones distintas",\n'
        '  "problemas": ["problema concreto 1", "problema concreto 2", "..."],\n'
        '  "interpretacion": "análisis de las dinámicas relacionales en juego: patrones, roles, puntos de '
        'acuerdo y desacuerdo entre los relatos, posibles causas de fondo, en 4 a 8 frases",\n'
        '  "soluciones": ["recomendación accionable 1", "recomendación accionable 2", "..."],\n'
        '  "nivelUrgencia": "bajo | medio | alto"\n'
        "}\n\n"
        "Entrega entre 2 y 6 problemas y entre 3 y 6 soluciones, concretas y realistas para quien coordina "
        "el caso. Si algún relato incluye señales de violencia, abuso, autolesión o riesgo grave para "
        "alguien, marca nivelUrgencia como \"alto\", indícalo explícitamente en la interpretación y "
        "recomienda derivar a un profesional o autoridad competente en vez de dar soluciones simplistas "
        "para esa parte."
    )
    try:
        texto = _llamar_claude(prompt, max_tokens=1800)
        data = _parsear_json(texto)
    except ClaudeError as exc:
        return {
            "sintesis": f"No se pudo generar la síntesis automática: {exc}",
            "interpretacion": "",
            "problemas": [],
            "soluciones": [],
            "nivel_urgencia": "medio",
        }

    nivel = data.get("nivelUrgencia") or data.get("nivel_urgencia") or "medio"
    if nivel not in ("bajo", "medio", "alto"):
        nivel = "medio"
    return {
        "sintesis": str(data.get("sintesis", "")),
        "interpretacion": str(data.get("interpretacion", "")),
        "problemas": [str(p) for p in data.get("problemas", [])][:8],
        "soluciones": [str(s) for s in data.get("soluciones", [])][:8],
        "nivel_urgencia": nivel,
    }
