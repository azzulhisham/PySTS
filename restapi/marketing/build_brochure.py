#!/usr/bin/env python3
"""Build the MANTIS marketing brochure — operations app, not API docs."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parent
OUT_PDF = ROOT / "MANTIS_Marketing_Brochure.pdf"
APP_SHOT = ROOT / "operations_overview.png"

# Command-centre palette, aligned with the live operations app
INK = HexColor("#070b10")
NAVY = HexColor("#0b1220")
PANEL = HexColor("#121a28")
PANEL_EDGE = HexColor("#1e2d44")
GOLD = HexColor("#e8c872")
TEAL = HexColor("#2dd4bf")
CYAN = HexColor("#38bdf8")
STS = HexColor("#fb923c")
DARK = HexColor("#fb7185")
ANCHOR = HexColor("#a78bfa")
LIGHT = HexColor("#e8eef7")
MUTED = HexColor("#94a3b8")
WHITE = white

PAGE = landscape(A4)
W, H = PAGE


def _styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle(
            "brand",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=46,
            leading=50,
            textColor=WHITE,
            alignment=TA_LEFT,
            spaceAfter=2,
        ),
        "eyebrow": ParagraphStyle(
            "eyebrow",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=11,
            textColor=GOLD,
            alignment=TA_LEFT,
            spaceAfter=6,
            tracking=1.4,
        ),
        "tagline": ParagraphStyle(
            "tagline",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=14,
            leading=20,
            textColor=TEAL,
            spaceAfter=12,
        ),
        "hero": ParagraphStyle(
            "hero",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=11,
            leading=16,
            textColor=LIGHT,
            alignment=TA_JUSTIFY,
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=WHITE,
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=GOLD,
            spaceBefore=2,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14.5,
            textColor=LIGHT,
            alignment=TA_JUSTIFY,
        ),
        "body_sm": ParagraphStyle(
            "body_sm",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=LIGHT,
            alignment=TA_JUSTIFY,
        ),
        "muted": ParagraphStyle(
            "muted",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=12,
            textColor=MUTED,
            alignment=TA_LEFT,
        ),
        "caption": ParagraphStyle(
            "caption",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8,
            leading=11,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "card_kicker": ParagraphStyle(
            "card_kicker",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=10,
            textColor=GOLD,
            spaceAfter=2,
        ),
        "card_title": ParagraphStyle(
            "card_title",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            textColor=WHITE,
            spaceAfter=5,
        ),
        "card_body": ParagraphStyle(
            "card_body",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8.7,
            leading=12.2,
            textColor=LIGHT,
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
        "cta": ParagraphStyle(
            "cta",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=17,
            textColor=INK,
            alignment=TA_CENTER,
        ),
        "cta_sub": ParagraphStyle(
            "cta_sub",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=INK,
            alignment=TA_CENTER,
        ),
        "who": ParagraphStyle(
            "who",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9.5,
            leading=14,
            textColor=LIGHT,
        ),
    }


def _paint_cover(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)

    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, W * 0.40, H, fill=1, stroke=0)

    canvas.setFillColor(GOLD)
    canvas.rect(W * 0.40 - 2.5, 0, 2.5, H, fill=1, stroke=0)

    canvas.setStrokeColor(Color(0.91, 0.78, 0.45, alpha=0.18))
    canvas.setLineWidth(1.2)
    cx, cy = W * 0.17, H * 0.58
    for r in (55, 85, 118, 152):
        canvas.circle(cx, cy, r, stroke=1, fill=0)
    canvas.setFillColor(GOLD)
    canvas.circle(cx, cy, 6, stroke=0, fill=1)

    canvas.setFillColor(TEAL)
    canvas.setFillColor(Color(0.18, 0.83, 0.75, alpha=0.35))
    canvas.circle(cx + 42, cy - 28, 4, stroke=0, fill=1)
    canvas.setFillColor(Color(0.98, 0.45, 0.24, alpha=0.45))
    canvas.circle(cx - 30, cy + 36, 3.5, stroke=0, fill=1)
    canvas.setFillColor(Color(0.98, 0.44, 0.52, alpha=0.45))
    canvas.circle(cx + 22, cy + 48, 3, stroke=0, fill=1)

    canvas.setFillColor(PANEL)
    canvas.rect(0, 0, W, 11 * mm, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(16 * mm, 4.5 * mm, "MANTIS  ·  Maritime Domain Awareness")
    canvas.drawRightString(W - 16 * mm, 4.5 * mm, "South-East Asia  ·  Live Operations")
    canvas.restoreState()


def _paint_inner(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(INK)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)

    canvas.setFillColor(GOLD)
    canvas.rect(0, H - 5 * mm, W, 5 * mm, fill=1, stroke=0)
    canvas.setFillColor(TEAL)
    canvas.rect(0, H - 5 * mm, 48 * mm, 5 * mm, fill=1, stroke=0)

    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, W, 12 * mm, fill=1, stroke=0)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(16 * mm, 4.8 * mm, "MANTIS  ·  Maritime Anomaly Intelligence")
    canvas.drawRightString(W - 16 * mm, 4.8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _accent_card(kicker: str, title: str, body: str, accent, styles, width):
    wrap = Table(
        [
            [Paragraph(kicker, styles["card_kicker"])],
            [Paragraph(title, styles["card_title"])],
            [Paragraph(body, styles["card_body"])],
        ],
        colWidths=[width],
    )
    wrap.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.6, PANEL_EDGE),
                ("LINEBEFORE", (0, 0), (0, -1), 3.2, accent),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (0, 0), 9),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 11),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return wrap


def _shot(width, height):
    if not APP_SHOT.exists():
        return Spacer(1, height)
    img = Image(str(APP_SHOT), width=width, height=height, kind="proportional")
    frame = Table([[img]], colWidths=[width])
    frame.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 1.2, GOLD),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return frame


def build():
    styles = _styles()
    margin = 14 * mm

    doc = BaseDocTemplate(
        str(OUT_PDF),
        pagesize=PAGE,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=12 * mm,
        bottomMargin=16 * mm,
        title="MANTIS — Maritime Anomaly Intelligence",
        author="MANTIS",
    )

    cover_left = Frame(14 * mm, 14 * mm, W * 0.36, H - 28 * mm, id="cover_left")
    cover_right = Frame(W * 0.40 + 10 * mm, 14 * mm, W * 0.56 - 12 * mm, H - 28 * mm, id="cover_right")

    content = Frame(margin, 16 * mm, W - 2 * margin, H - 32 * mm, id="content")

    col_w = (W - 2 * margin - 8 * mm) / 2
    left = Frame(margin, 16 * mm, col_w, H - 32 * mm, id="left")
    right = Frame(margin + col_w + 8 * mm, 16 * mm, col_w, H - 32 * mm, id="right")

    doc.addPageTemplates(
        [
            PageTemplate(id="cover", frames=[cover_left, cover_right], onPage=_paint_cover),
            PageTemplate(id="full", frames=[content], onPage=_paint_inner),
            PageTemplate(id="two", frames=[left, right], onPage=_paint_inner),
        ]
    )

    story = []

    # ===== COVER =====
    story.append(NextPageTemplate("cover"))
    story.append(Spacer(1, 22 * mm))
    story.append(Paragraph("MARITIME DOMAIN AWARENESS", styles["eyebrow"]))
    story.append(Paragraph("MANTIS", styles["brand"]))
    story.append(
        Paragraph(
            "See the anomaly before it becomes the incident.",
            styles["tagline"],
        )
    )
    story.append(
        Paragraph(
            "MANTIS is a live operations picture for South-East Asian waters. "
            "It turns crowded AIS traffic into a command view of the behaviours "
            "that matter: vessels that go dark, ship-to-ship transfers at sea, "
            "and unauthorised anchoring in sensitive waters.",
            styles["hero"],
        )
    )
    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(
            "Built for the watch floor — maritime security, coastal surveillance, "
            "port control and investigative command.",
            styles["muted"],
        )
    )
    story.append(FrameBreak())

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Inside the operations centre", styles["h2"]))
    story.append(_shot(W * 0.54, 108 * mm))
    story.append(Spacer(1, 2 * mm))
    story.append(
        Paragraph(
            "Operations Overview — Malaysian Waters, Straits of Malacca and the South China Sea. "
            "Live maritime picture with threat indicators and AI Copilot.",
            styles["caption"],
        )
    )

    # ===== PAGE 2: Live picture =====
    story.append(NextPageTemplate("full"))
    story.append(PageBreak())

    story.append(Paragraph("The Live Maritime Picture", styles["h1"]))
    story.append(
        Paragraph(
            "One screen. The whole theatre. Operators move from a regional picture "
            "to a zone of interest — Johor Strait, the Malacca approaches, the South China Sea — "
            "without losing the threat picture.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 3 * mm))
    story.append(_shot(W - 2 * margin, 112 * mm))
    story.append(Spacer(1, 2.5 * mm))

    legend = Table(
        [[
            Paragraph("<font color='#fb923c'><b>●</b></font>  STS activity", styles["card_body"]),
            Paragraph("<font color='#fb7185'><b>●</b></font>  Dark vessels", styles["card_body"]),
            Paragraph("<font color='#a78bfa'><b>●</b></font>  Illegal anchoring", styles["card_body"]),
            Paragraph("<font color='#38bdf8'><b>●</b></font>  AI Copilot on the watch floor", styles["card_body"]),
        ]],
        colWidths=[(W - 2 * margin) / 4] * 4,
    )
    legend.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.5, PANEL_EDGE),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(legend)

    # ===== PAGE 3: What it detects =====
    story.append(NextPageTemplate("full"))
    story.append(PageBreak())

    story.append(Paragraph("What MANTIS Detects", styles["h1"]))
    story.append(
        Paragraph(
            "Anomaly intelligence for the marine industry — not another map of every ship. "
            "MANTIS isolates the behaviours that change risk, cargo and command attention.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 5 * mm))

    card_w = (W - 2 * margin - 8 * mm) / 3
    pillars = Table(
        [[
            _accent_card(
                "SHIP-TO-SHIP",
                "STS transfers at sea",
                "Mid-sea pairing is where cargo, bunkers and risk change hands. "
                "MANTIS surfaces active ship-to-ship activity on the live picture "
                "so the watch floor can see who is alongside whom, how long they "
                "have been together, and where the transfer is unfolding — "
                "including in designated anchorage waters.",
                STS,
                styles,
                card_w,
            ),
            _accent_card(
                "AIS SILENCE",
                "Dark vessels",
                "When a vessel goes dark, the feed simply stops. MANTIS keeps "
                "that disappearance in the operational picture: suspected dark "
                "activity after unusual behaviour, likely coverage exit at the "
                "edge of the theatre, and high-confidence leads ready for "
                "tasking — with the last known location still on the map.",
                DARK,
                styles,
                card_w,
            ),
            _accent_card(
                "RESTRICTED WATERS",
                "Unauthorised anchoring",
                "Not every stop is innocent. MANTIS watches restricted waters "
                "and designated watch zones for vessels that linger where they "
                "should not — a clear cue for coastal enforcement, port control "
                "and security patrols across the Straits and coastal approaches.",
                ANCHOR,
                styles,
                card_w,
            ),
        ]],
        colWidths=[card_w] * 3,
    )
    pillars.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 4),
                ("LEFTPADDING", (1, 0), (1, 0), 4),
                ("LEFTPADDING", (2, 0), (2, 0), 4),
            ]
        )
    )
    story.append(pillars)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("From picture to decision", styles["h2"]))
    outcomes = Table(
        [[
            Paragraph(
                "<b>Threat indicators, live</b><br/>STS, dark activity and anchoring "
                "summarised beside the map — so command sees volume, urgency and "
                "confidence without leaving the picture.",
                styles["card_body"],
            ),
            Paragraph(
                "<b>Zone focus &amp; intercept view</b><br/>Move from the regional "
                "theatre to a strait, an anchorage or an intercept in a single action. "
                "The live picture stays coherent as the operator drills in.",
                styles["card_body"],
            ),
            Paragraph(
                "<b>AI Copilot on watch</b><br/>Brief the room from the same screen. "
                "Ask the operational picture — not a spreadsheet — what is unfolding "
                "in Malaysian waters, the Malacca Strait and the South China Sea.",
                styles["card_body"],
            ),
        ]],
        colWidths=[(W - 2 * margin) / 3] * 3,
    )
    outcomes.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.6, PANEL_EDGE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, PANEL_EDGE),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(outcomes)

    # ===== PAGE 4: Who / why / CTA =====
    story.append(NextPageTemplate("two"))
    story.append(PageBreak())

    story.append(Paragraph("Why it matters", styles["h1"]))
    story.append(
        Paragraph(
            "South-East Asian sea lanes carry the world’s energy, containers and "
            "risk. In that density, the dangerous act is rarely the ship that "
            "looks busy — it is the pairing, the silence, the stop that does not belong.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "MANTIS gives maritime command a <b>single operational picture</b> of "
            "those anomalies: situational awareness for the Straits of Malacca, "
            "coastal approaches and the South China Sea, with threat indicators "
            "that are already in the language of the watch floor.",
            styles["body"],
        )
    )
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("On the watch floor", styles["h2"]))
    for line in [
        "Maritime security and coast watch centres",
        "Vessel traffic and port control",
        "Coastal compliance and enforcement",
        "Sanctions, bunkering and investigative desks",
        "Naval and coast-guard operations",
    ]:
        story.append(Paragraph(f"<font color='#e8c872'>▸</font>  {line}", styles["who"]))
        story.append(Spacer(1, 1.6 * mm))

    story.append(FrameBreak())
    story.append(Paragraph("Command value", styles["h1"]))
    for title, body in [
        (
            "Situational awareness",
            "A live maritime picture instead of a pile of tracks. Operators see "
            "anomalies in geography, not in a log.",
        ),
        (
            "Faster triage",
            "Threat indicators separate STS, dark activity and illegal anchoring "
            "so the next action is obvious.",
        ),
        (
            "Credible briefing",
            "Walk a principal from the regional theatre to a single vessel — "
            "map, indicators and Copilot on one screen.",
        ),
    ]:
        story.append(
            _accent_card("OUTCOME", title, body, TEAL, styles, col_w - 2 * mm)
        )
        story.append(Spacer(1, 3.5 * mm))

    story.append(Spacer(1, 4 * mm))
    cta = Table(
        [[
            Paragraph("Request a live operations walkthrough", styles["cta"]),
        ], [
            Paragraph("One dark vessel. One STS pair. One illegal stop. On the map.", styles["cta_sub"]),
        ]],
        colWidths=[col_w - 2 * mm],
    )
    cta.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), GOLD),
                ("TOPPADDING", (0, 0), (0, 0), 12),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    story.append(cta)
    story.append(Spacer(1, 5 * mm))
    story.append(
        Paragraph(
            "MANTIS is an operational intelligence aid for the marine industry — "
            "not a legal determination. Always combine with local authority, permits and human review.",
            styles["caption"],
        )
    )

    doc.build(story)
    print(f"Wrote {OUT_PDF}")


if __name__ == "__main__":
    build()
