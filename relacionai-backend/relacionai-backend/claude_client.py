"""
Integración con la API de Claude (Anthropic) para Relacionai.

Operaciones:
  - resumir_relato(texto): resumen breve de UN relato individual, apenas
    llega a la carpeta del caso.
  - sintetizar_caso(apellido, relatos, reglamento_texto): al acumularse
    relatos en la carpeta, combina los resúmenes (o el texto completo si son
    pocos) y devuelve una síntesis general del caso, los problemas
    identificados, los pasos según el reglamento interno (si se subió uno) y
    sugerencias de acción propias.
  - resumir_reglamento(texto): resumen de confirmación que se muestra al
    encargado apenas sube un reglamento interno, para que vea que Claude
    efectivamente lo leyó.

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


def sintetizar_caso(apellido: str, relatos: list, reglamento_texto: str = "", casos_pasados: list = None) -> dict:
    """
    relatos: lista de dicts {nombre, formato, contenido, resumen}
    reglamento_texto: texto del reglamento interno de la institución (opcional). Si se
      entrega, se pide además una lista separada de pasos según ese reglamento.
    casos_pasados: lista opcional de dicts {rotulo, problemas, pasos_reglamento, sugerencias,
      nivel_urgencia} de casos anteriores ya sintetizados (sin el contenido de sus relatos),
      para que la plataforma "aprenda" de la experiencia acumulada del establecimiento sin
      mezclar los hechos concretos de un caso con otro.
    Devuelve: {sintesis, problemas: [...], pasos_reglamento: [...], sugerencias: [...], nivel_urgencia}
    """
    if not _api_key_configurada():
        return {
            "sintesis": "Síntesis pendiente: configura ANTHROPIC_API_KEY en el servidor.",
            "problemas": [],
            "pasos_reglamento": [],
            "sugerencias": [],
            "nivel_urgencia": "medio",
        }

    bloques = []
    for i, r in enumerate(relatos, start=1):
        cuerpo = r.get("resumen") or r.get("contenido", "")
        bloques.append(f"--- Relato {i} (de: {r.get('nombre') or 'persona no identificada'}) ---\n{cuerpo[:4000]}")
    relatos_texto = "\n\n".join(bloques)[:40000]

    reglamento_texto = (reglamento_texto or "").strip()
    if reglamento_texto:
        bloque_reglamento = (
            "\n\nREGLAMENTO INTERNO DE LA INSTITUCIÓN (referencia obligatoria):\n\"\"\"\n"
            + reglamento_texto[:20000] + "\n\"\"\"\n\n"
            "Revisa este reglamento y arma la lista \"pasosReglamento\" con los pasos o "
            "procedimientos concretos que indica para una situación como la de este caso "
            "(citando artículos o secciones si el reglamento los tiene). Si no encuentras nada "
            "aplicable en el reglamento para este caso, la lista \"pasosReglamento\" debe tener "
            "un único ítem que diga textualmente: \"Este caso no aparece contemplado específicamente "
            "en el reglamento interno.\" — no inventes procedimientos que el reglamento no contiene."
        )
        pide_pasos_reglamento = '  "pasosReglamento": ["paso o procedimiento 1 según el reglamento", "..."],\n'
    else:
        bloque_reglamento = ""
        pide_pasos_reglamento = ""

    if casos_pasados:
        lineas = []
        for c in casos_pasados:
            partes = [f"urgencia {c.get('nivel_urgencia', 'medio')}"]
            if c.get("problemas"):
                partes.append("problemas: " + "; ".join(c["problemas"][:4]))
            if c.get("pasos_reglamento"):
                partes.append("pasos de reglamento aplicados: " + "; ".join(c["pasos_reglamento"][:3]))
            if c.get("sugerencias"):
                partes.append("sugerencias dadas: " + "; ".join(c["sugerencias"][:4]))
            lineas.append(f"- Caso {c.get('rotulo', '')} ({', '.join(partes)})")
        bloque_casos_pasados = (
            "\n\nCASOS ANTERIORES DEL MISMO ESTABLECIMIENTO (solo como referencia de patrones y "
            "criterios ya usados — son casos DISTINTOS e independientes; no mezcles sus hechos con "
            "los del caso actual ni asumas que están relacionados):\n"
            + "\n".join(lineas) + "\n\n"
            "Si el caso actual repite un patrón visible en estos casos anteriores (mismo tipo de "
            "conflicto, mismas personas, o una sugerencia que ya se dio antes y no funcionó), "
            "puedes mencionarlo brevemente en la síntesis o proponer una sugerencia más específica "
            "en vez de repetir literalmente la misma recomendación genérica."
        )
    else:
        bloque_casos_pasados = ""

    prompt = (
        "Eres una persona analista experta en relaciones interpersonales y convivencia, apoyando a "
        "quien coordina un caso. A continuación hay varios relatos personales recogidos sobre un mismo "
        "caso (identificado por el apellido «" + apellido + "»), posiblemente contados por distintas "
        "personas involucradas o testigos. Cada uno puede tener una versión distinta de los hechos.\n\n"
        "RELATOS DEL CASO:\n\"\"\"\n" + relatos_texto + "\n\"\"\"\n"
        + bloque_reglamento + bloque_casos_pasados + "\n"
        "Responde ÚNICAMENTE con un objeto JSON con esta forma exacta (sin texto fuera del JSON):\n"
        "{\n"
        '  "sintesis": "síntesis general del caso integrando todos los relatos, en 4 a 8 frases, '
        'en tercera persona, sin tomar partido por una sola versión cuando hay versiones distintas",\n'
        '  "problemas": ["problema concreto 1", "problema concreto 2", "..."],\n'
        + pide_pasos_reglamento
        + '  "sugerencias": ["sugerencia de acción 1", "sugerencia de acción 2", "..."],\n'
        '  "nivelUrgencia": "bajo | medio | alto"\n'
        "}\n\n"
        "Entrega entre 2 y 6 problemas y entre 3 y 6 sugerencias de acción, concretas y realistas para "
        "quien coordina el caso (independientes de los pasos del reglamento, si los hay). Si algún "
        "relato incluye señales de violencia, abuso, autolesión o riesgo grave para alguien, marca "
        "nivelUrgencia como \"alto\" y recomienda explícitamente, como una de las sugerencias de acción, "
        "derivar a un profesional o autoridad competente en vez de dar sugerencias simplistas para esa parte."
    )
    try:
        texto = _llamar_claude(prompt, max_tokens=1800)
        data = _parsear_json(texto)
    except ClaudeError as exc:
        return {
            "sintesis": f"No se pudo generar la síntesis automática: {exc}",
            "problemas": [],
            "pasos_reglamento": [],
            "sugerencias": [],
            "nivel_urgencia": "medio",
        }

    nivel = data.get("nivelUrgencia") or data.get("nivel_urgencia") or "medio"
    if nivel not in ("bajo", "medio", "alto"):
        nivel = "medio"
    return {
        "sintesis": str(data.get("sintesis", "")),
        "problemas": [str(p) for p in data.get("problemas", [])][:8],
        "pasos_reglamento": [str(p) for p in data.get("pasosReglamento", [])][:8],
        "sugerencias": [str(s) for s in data.get("sugerencias", [])][:8],
        "nivel_urgencia": nivel,
    }


def resumir_reglamento(texto: str) -> str:
    """Resumen breve para confirmarle al encargado que Claude efectivamente leyó y
    entendió el reglamento recién subido (se muestra apenas termina de subirlo)."""
    if not _api_key_configurada():
        return (
            "[Resumen pendiente: configura ANTHROPIC_API_KEY en el servidor para que Claude "
            "confirme qué entendió del reglamento subido.]"
        )
    prompt = (
        "Lee el siguiente reglamento interno de una institución (puede venir de un Word o PDF, "
        "con errores de formato). Resume en 3 a 6 frases, en español, qué tipo de situaciones "
        "cubre y qué procedimientos generales establece (plazos, instancias, sanciones, "
        "derivaciones), para confirmarle a quien lo subió que fue leído correctamente. Si el "
        "texto no parece ser un reglamento o protocolo institucional, dilo explícitamente en vez "
        "de inventar contenido.\n\n"
        f"REGLAMENTO:\n\"\"\"\n{texto[:20000]}\n\"\"\"\n"
    )
    try:
        return _llamar_claude(prompt, max_tokens=500).strip()
    except ClaudeError as exc:
        return f"[No se pudo generar el resumen de confirmación: {exc}]"
