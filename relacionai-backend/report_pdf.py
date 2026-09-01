"""
Genera el informe final descargable de un caso: síntesis general,
problemas identificados, interpretación y soluciones posibles, más el
listado de relatos que se incluyeron en el análisis.

Usa reportlab (platypus) para no depender de binarios externos.
"""

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ACCENT = colors.HexColor("#1E4E48")
INK = colors.HexColor("#232019")
MUTED = colors.HexColor("#6E675C")
URGENCIA_HEX = {"bajo": "#3F7D4F", "medio": "#A3610C", "alto": "#B0362A"}


def _fmt_fecha(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        return iso or "—"


def _styles():
    base = getSampleStyleSheet()
    styles = {
        "titulo": ParagraphStyle("titulo", parent=base["Title"], fontName="Helvetica-Bold", fontSize=18, textColor=INK, spaceAfter=4),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontName="Helvetica", fontSize=9.5, textColor=MUTED, spaceAfter=2),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11, textColor=ACCENT, spaceBefore=16, spaceAfter=6, letterSpacing=0.4),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName="Helvetica", fontSize=10.3, leading=15, textColor=INK, alignment=TA_JUSTIFY),
        "li": ParagraphStyle("li", parent=base["Normal"], fontName="Helvetica", fontSize=10.3, leading=15, textColor=INK),
        "small": ParagraphStyle("small", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8, textColor=MUTED),
    }
    return styles


def _bullets(items, style):
    items = items or ["—"]
    return ListFlowable(
        [ListItem(Paragraph(str(it), style), leftIndent=6) for it in items],
        bulletType="bullet", start="•", leftIndent=14, spaceBefore=2, bulletFontSize=8,
    )


def construir_informe_pdf(caso, relatos, problemas, soluciones) -> bytes:
    """caso: sqlite3.Row de la tabla casos. relatos: lista de sqlite3.Row de la tabla relatos."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Informe {caso['rotulo']} — Relacionai",
    )
    s = _styles()
    story = []

    story.append(Paragraph("Informe de análisis — Relacionai", s["titulo"]))
    nivel = (caso["nivel_urgencia"] or "medio")
    nivel_color = URGENCIA_HEX.get(nivel, "#6E675C")
    story.append(Paragraph(f"Caso <b>{caso['rotulo']}</b> · Apellido: {caso['apellido']}", s["meta"]))
    story.append(Paragraph(
        f"Carpeta creada: {_fmt_fecha(caso['creado_en'])} · Relatos incluidos: {len(relatos)} · "
        f'Nivel de urgencia: <font color="{nivel_color}"><b>{nivel.upper()}</b></font>', s["meta"],
    ))
    if caso["sintesis_generada_en"]:
        story.append(Paragraph(f"Síntesis generada: {_fmt_fecha(caso['sintesis_generada_en'])}", s["meta"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("SÍNTESIS GENERAL DEL CASO", s["h2"]))
    story.append(Paragraph(caso["sintesis_general"] or "Aún no se ha generado una síntesis para este caso.", s["body"]))

    story.append(Paragraph("PROBLEMAS IDENTIFICADOS", s["h2"]))
    story.append(_bullets(problemas, s["li"]))

    story.append(Paragraph("INTERPRETACIÓN", s["h2"]))
    story.append(Paragraph(caso["interpretacion"] or "—", s["body"]))

    story.append(Paragraph("SOLUCIONES POSIBLES", s["h2"]))
    story.append(_bullets(soluciones, s["li"]))

    story.append(Paragraph("RELATOS INCLUIDOS EN ESTE CASO", s["h2"]))
    filas = [["Persona", "Formato", "Recibido"]]
    for r in relatos:
        filas.append([r["nombre_persona"], r["formato_entrada"], _fmt_fecha(r["subido_en"])])
    tabla = Table(filas, colWidths=[70 * mm, 30 * mm, 45 * mm])
    tabla.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1EEE8")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#DCD6CA")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tabla)

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Generado con asistencia de Claude a partir de los relatos registrados en Relacionai. "
        "Documento de uso interno y confidencial.", s["small"],
    ))

    doc.build(story)
    return buf.getvalue()
