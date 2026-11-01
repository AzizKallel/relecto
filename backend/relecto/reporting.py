"""Printable report (PDF). Unsigned reports carry a large "BROUILLON — NON SIGNÉ" mark."""

import io
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INK = colors.HexColor("#0F1B2D")
MUTED = colors.HexColor("#5B6675")
ACCENT = colors.HexColor("#0B6E6E")
SECTIONS = ("INDICATION", "TECHNIQUE", "RÉSULTATS", "RESULTATS", "CONCLUSION", "COMPARAISON")


def local_tz():
    """Centre's time zone for printed dates (RELECTO_TZ, default Africa/Tunis)."""
    return ZoneInfo(os.environ.get("RELECTO_TZ", "Africa/Tunis"))


def _local(d: datetime) -> datetime:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(local_tz())


def _date(d: str | None) -> str:
    if d and len(d) == 8 and d.isdigit():
        return f"{d[6:]}/{d[4:6]}/{d[:4]}"
    return d or "—"


def render_pdf(settings: dict, study, report, signer) -> bytes:
    buf = io.BytesIO()
    signed = report.status == "signed"
    st = {
        "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=14, textColor=INK, leading=18),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9, textColor=MUTED, leading=12),
        "sec": ParagraphStyle("sec", fontName="Helvetica-Bold", fontSize=10.5, textColor=ACCENT, spaceBefore=8, leading=14),
        "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10.5, textColor=INK, leading=15),
        "small": ParagraphStyle("small", fontName="Helvetica", fontSize=7.5, textColor=MUTED, leading=10),
        "lab": ParagraphStyle("lab", fontName="Helvetica", fontSize=8.5, textColor=MUTED, leading=11),
        "val": ParagraphStyle("val", fontName="Helvetica-Bold", fontSize=10, textColor=INK, leading=13),
    }

    def on_page(canvas, doc):
        canvas.saveState()
        if not signed:
            canvas.setFont("Helvetica-Bold", 46)
            canvas.setFillColor(colors.Color(0.8, 0.1, 0.1, alpha=0.12))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "BROUILLON — NON SIGNÉ")
        canvas.restoreState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=18 * mm, title="Compte rendu", author=settings.get("centre_name", ""))
    story = [Paragraph(escape(settings.get("centre_name") or ""), st["h"])]
    contact = " · ".join(x for x in (settings.get("centre_address"), settings.get("centre_phone")) if x)
    if contact:
        story.append(Paragraph(escape(contact), st["sub"]))
    story.append(Spacer(1, 6 * mm))

    cells = [
        ("Patient", study.patient_name or "—"), ("Identifiant", study.patient_id or "—"),
        ("Né(e) le", _date(study.patient_birth_date)), ("Examen", study.description or study.modality or "—"),
        ("Date de l'examen", _date(study.study_date)), ("N° d'examen", study.accession or "—"),
    ]
    rows = []
    for i in range(0, len(cells), 3):
        rows.append([Paragraph(escape(l), st["lab"]) for l, _ in cells[i:i + 3]])
        rows.append([Paragraph(escape(str(v)), st["val"]) for _, v in cells[i:i + 3]])
    t = Table(rows, colWidths=[58 * mm] * 3)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F6F8")),
                           ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E3E6EB")),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                           ("LEFTPADDING", (0, 0), (-1, -1), 8)]))
    story += [t, Spacer(1, 6 * mm)]

    for line in (report.text_deid or "").splitlines():
        s = line.strip()
        if not s:
            story.append(Spacer(1, 2 * mm))
            continue
        head, sep, rest = s.partition(":")
        if sep and head.strip().upper() in SECTIONS:
            story.append(Paragraph(escape(head.strip().upper()), st["sec"]))
            if rest.strip():
                story.append(Paragraph(escape(rest.strip()), st["body"]))
        else:
            story.append(Paragraph(escape(s), st["body"]))

    story.append(Spacer(1, 10 * mm))
    if signed and signer:
        who = f"{signer.title} {signer.name}".strip() if signer.title else signer.name
        when = _local(report.signed_at).strftime("%d/%m/%Y à %H:%M") if report.signed_at else ""
        story.append(Paragraph(f"Compte rendu validé et signé électroniquement par <b>{escape(who)}</b> le {when}.",
                               st["body"]))
    else:
        story.append(Paragraph("<b>Document non signé — ne pas remettre au patient.</b>", st["body"]))

    v = report.signed_versions or {}
    trace = (f"Brouillon préparé avec assistance logicielle (Relecto {v.get('engine', '')}"
             f"{', analyse : ' + str(v.get('findings_source')) if v.get('findings_source') else ''}), "
             f"relu, modifié et validé par le radiologue signataire, seul responsable du contenu. "
             f"Règles {v.get('rule_pack', '')}, lexique {v.get('lexicon', '')}. "
             f"Imprimé le {datetime.now(local_tz()).strftime('%d/%m/%Y %H:%M')}.")
    story += [Spacer(1, 6 * mm), Paragraph(escape(trace), st["small"])]
    if settings.get("report_footer"):
        story.append(Paragraph(escape(settings["report_footer"]), st["small"]))
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()
