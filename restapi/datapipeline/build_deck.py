#!/usr/bin/env python3
"""
Seadragon Maritime Data Platform — management overview PDF (v2).

- No Blacksmith branding
- No attached screenshot images (custom diagrams only)
- Consistent gaps between cards / boxes on every page
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
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
ASSETS = ROOT / "assets"
OUT_PDF = ROOT / "Seadragon_Maritime_Data_Platform_Overview.pdf"

NAVY = HexColor("#0a1929")
DEEP = HexColor("#0e2236")
CARD = HexColor("#12263a")
CARD_EDGE = HexColor("#2a455c")
TEAL = HexColor("#14b8a6")
TEAL_DARK = HexColor("#0f766e")
ACCENT = HexColor("#5eead4")
SLATE = HexColor("#94a3b8")
LIGHT = HexColor("#e2e8f0")
MUTED = HexColor("#cbd5e1")

PAGE = landscape(A4)
W, H = PAGE
GAP = 5 * mm  # standard gap between boxes


def S():
    base = getSampleStyleSheet()
    return {
        "hero": ParagraphStyle(
            "hero", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=38, leading=42, textColor=white, spaceAfter=4,
        ),
        "eyebrow": ParagraphStyle(
            "eyebrow", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9, leading=12, textColor=ACCENT, spaceAfter=8,
        ),
        "subhero": ParagraphStyle(
            "subhero", parent=base["Normal"], fontName="Helvetica",
            fontSize=14, leading=19, textColor=ACCENT, spaceAfter=12,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"], fontName="Helvetica-Bold",
            fontSize=21, leading=25, textColor=white, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=12, leading=15, textColor=ACCENT, spaceBefore=2, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Helvetica",
            fontSize=10.5, leading=15, textColor=LIGHT, alignment=TA_JUSTIFY,
        ),
        "body_sm": ParagraphStyle(
            "body_sm", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.2, leading=13, textColor=MUTED, alignment=TA_JUSTIFY,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["Normal"], fontName="Helvetica",
            fontSize=10, leading=14, textColor=LIGHT, leftIndent=2, spaceAfter=4,
        ),
        "card_t": ParagraphStyle(
            "card_t", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10, leading=13, textColor=white, spaceAfter=2,
        ),
        "card_b": ParagraphStyle(
            "card_b", parent=base["Normal"], fontName="Helvetica",
            fontSize=8.2, leading=11, textColor=MUTED,
        ),
        "stat": ParagraphStyle(
            "stat", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=24, leading=28, textColor=ACCENT, alignment=TA_CENTER,
        ),
        "stat_l": ParagraphStyle(
            "stat_l", parent=base["Normal"], fontName="Helvetica",
            fontSize=8, leading=11, textColor=SLATE, alignment=TA_CENTER,
        ),
        "caption": ParagraphStyle(
            "caption", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8, leading=11, textColor=SLATE, alignment=TA_CENTER,
        ),
        "cta": ParagraphStyle(
            "cta", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=13, leading=17, textColor=NAVY, alignment=TA_CENTER,
        ),
        "center": ParagraphStyle(
            "center", parent=base["Normal"], fontName="Helvetica",
            fontSize=12, leading=17, textColor=LIGHT, alignment=TA_CENTER,
        ),
    }


class IconCard(Flowable):
    """Rounded card with icon, title, body — fixed outer size."""

    def __init__(self, icon_key: str, title: str, body: str, width, height=40 * mm):
        super().__init__()
        self.icon = ASSETS / f"icon_{icon_key}.png"
        self.title = title
        self.body = body
        self.width = width
        self.height = height
        self.s = S()

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        # soft shadow
        c.setFillColor(Color(0, 0, 0, alpha=0.25))
        c.roundRect(1.5, -1.5, self.width, self.height, 9, fill=1, stroke=0)
        # card face
        c.setFillColor(CARD)
        c.setStrokeColor(CARD_EDGE)
        c.setLineWidth(0.9)
        c.roundRect(0, 0, self.width, self.height, 9, fill=1, stroke=1)
        # teal accent strip
        c.setFillColor(TEAL)
        c.rect(0, self.height - 2.2, self.width, 2.2, fill=1, stroke=0)
        c.setFillColor(CARD)
        c.roundRect(0, 0, self.width, self.height - 1.5, 9, fill=1, stroke=0)
        c.setStrokeColor(CARD_EDGE)
        c.roundRect(0, 0, self.width, self.height, 9, fill=0, stroke=1)

        top = self.height - 7
        icon_size = 18
        icon_y = top - icon_size
        if self.icon.exists():
            c.drawImage(
                str(self.icon), 7, icon_y,
                width=icon_size, height=icon_size, mask="auto", preserveAspectRatio=True,
            )

        # Title may wrap — measure full height, then place body below it
        title = Paragraph(self.title, self.s["card_t"])
        _, title_h = title.wrap(self.width - 32, 48)
        title_y = top - title_h
        title.drawOn(c, 28, title_y)

        header_bottom = min(icon_y, title_y) - 5
        body_avail = max(10, header_bottom - 6)
        body = Paragraph(self.body, self.s["card_b"])
        _, body_h = body.wrap(self.width - 14, body_avail)
        body_y = header_bottom - body_h
        if body_y < 5:
            body_y = 5
            _, body_h = body.wrap(self.width - 14, header_bottom - 5)
        body.drawOn(c, 7, body_y)


class TextCard(Flowable):
    """Text-only rounded card."""

    def __init__(self, title: str, body: str, width, height=None):
        super().__init__()
        self.title = title
        self.body = body
        self.width = width
        self._height = height
        self.s = S()
        # measure
        pt = Paragraph(f"<b>{title}</b>", self.s["card_t"])
        pb = Paragraph(body, self.s["card_b"])
        _, th = pt.wrap(width - 16, 40)
        _, bh = pb.wrap(width - 16, 200)
        self._content_h = th + bh + 16
        self.height = height or max(28 * mm, self._content_h)

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(Color(0, 0, 0, alpha=0.2))
        c.roundRect(1.2, -1.2, self.width, self.height, 8, fill=1, stroke=0)
        c.setFillColor(CARD)
        c.setStrokeColor(CARD_EDGE)
        c.setLineWidth(0.8)
        c.roundRect(0, 0, self.width, self.height, 8, fill=1, stroke=1)
        c.setFillColor(TEAL)
        c.rect(0, self.height - 2, 18, 2, fill=1, stroke=0)

        pt = Paragraph(f"<b>{self.title}</b>", self.s["card_t"])
        pb = Paragraph(self.body, self.s["card_b"])
        _, th = pt.wrap(self.width - 16, 40)
        _, bh = pb.wrap(self.width - 16, 200)
        pt.drawOn(c, 8, self.height - 8 - th)
        pb.drawOn(c, 8, self.height - 12 - th - bh)


def icon_row(items, total_width, height=40 * mm, gap=GAP):
    """Horizontal cards with real visual gaps between boxes."""
    n = len(items)
    card_w = (total_width - gap * (n - 1)) / n
    row = []
    widths = []
    for i, (key, title, body) in enumerate(items):
        row.append(IconCard(key, title, body, card_w, height=height))
        widths.append(card_w)
        if i < n - 1:
            row.append(Spacer(gap, height))
            widths.append(gap)
    tbl = Table([row], colWidths=widths)
    tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tbl


def text_card_stack(pairs, width, gap=GAP):
    """Vertical stack of text cards with gaps."""
    flow = []
    for i, (title, body) in enumerate(pairs):
        flow.append(TextCard(title, body, width))
        if i < len(pairs) - 1:
            flow.append(Spacer(1, gap))
    return flow


def stats_row(cells, total_width, gap=GAP):
    """Stat boxes with gaps."""
    n = len(cells)
    box_w = (total_width - gap * (n - 1)) / n
    s = S()
    row = []
    widths = []
    for i, (value, label) in enumerate(cells):
        inner = Table(
            [[Paragraph(value, s["stat"])], [Paragraph(label, s["stat_l"])]],
            colWidths=[box_w],
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), CARD),
            ("BOX", (0, 0), (-1, -1), 0.9, CARD_EDGE),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        row.append(inner)
        widths.append(box_w)
        if i < n - 1:
            row.append(Spacer(gap, 10))
            widths.append(gap)
    tbl = Table([row], colWidths=widths)
    tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tbl


def roadmap_row(cells, total_width, gap=GAP):
    n = len(cells)
    box_w = (total_width - gap * (n - 1)) / n
    s = S()
    row, widths = [], []
    colors = [TEAL_DARK, CARD, CARD]
    for i, text in enumerate(cells):
        inner = Table([[Paragraph(text, s["card_b"])]], colWidths=[box_w])
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors[i % 3]),
            ("BOX", (0, 0), (-1, -1), 0.9, CARD_EDGE),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        row.append(inner)
        widths.append(box_w)
        if i < n - 1:
            row.append(Spacer(gap, 10))
            widths.append(gap)
    tbl = Table([row], colWidths=widths)
    tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return tbl


def paint_bg(canv, doc):
    canv.saveState()
    canv.setFillColor(NAVY)
    canv.rect(0, 0, W, H, fill=1, stroke=0)
    # subtle vignette bars
    canv.setFillColor(DEEP)
    canv.rect(0, H - 7 * mm, W, 7 * mm, fill=1, stroke=0)
    canv.setFillColor(TEAL)
    canv.rect(0, H - 7 * mm, 42 * mm, 3.2 * mm, fill=1, stroke=0)
    canv.setFillColor(TEAL_DARK)
    canv.rect(42 * mm, H - 7 * mm, 18 * mm, 3.2 * mm, fill=1, stroke=0)
    canv.setFillColor(DEEP)
    canv.rect(0, 0, W, 12 * mm, fill=1, stroke=0)
    canv.setStrokeColor(CARD_EDGE)
    canv.setLineWidth(0.4)
    canv.line(14 * mm, 12 * mm, W - 14 * mm, 12 * mm)
    canv.setFillColor(SLATE)
    canv.setFont("Helvetica", 8)
    canv.drawString(16 * mm, 4.8 * mm, "Seadragon  ·  Maritime Data Platform")
    canv.drawRightString(W - 16 * mm, 4.8 * mm, f"{doc.page}")
    canv.restoreState()


def paint_cover(canv, doc):
    canv.saveState()
    canv.setFillColor(NAVY)
    canv.rect(0, 0, W, H, fill=1, stroke=0)
    # left panel
    canv.setFillColor(DEEP)
    canv.rect(0, 0, W * 0.40, H, fill=1, stroke=0)
    canv.setFillColor(TEAL)
    canv.rect(W * 0.40 - 3, 0, 3, H, fill=1, stroke=0)
    # rings
    canv.setStrokeColor(Color(0.08, 0.72, 0.65, alpha=0.28))
    canv.setLineWidth(1.4)
    cx, cy = W * 0.175, H * 0.52
    for r in (55, 85, 115, 145, 175):
        canv.circle(cx, cy, r, stroke=1, fill=0)
    canv.setFillColor(TEAL)
    canv.circle(cx, cy, 8, stroke=0, fill=1)
    # corner accents
    canv.setFillColor(TEAL_DARK)
    canv.rect(0, H - 8 * mm, W * 0.40, 8 * mm, fill=1, stroke=0)
    canv.setFillColor(TEAL)
    canv.rect(0, H - 3 * mm, 50 * mm, 3 * mm, fill=1, stroke=0)
    canv.restoreState()


def build():
    s = S()
    margin = 15 * mm
    content_w = W - 2 * margin
    col_w = (content_w - GAP) / 2

    doc = BaseDocTemplate(
        str(OUT_PDF),
        pagesize=PAGE,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=14 * mm,
        bottomMargin=15 * mm,
        title="Seadragon Maritime Data Platform — Management Overview",
        author="Seadragon",
    )

    cover_l = Frame(14 * mm, 16 * mm, W * 0.34, H - 32 * mm, id="cl")
    cover_r = Frame(W * 0.40 + 12 * mm, 16 * mm, W * 0.52, H - 32 * mm, id="cr")
    full = Frame(margin, 15 * mm, content_w, H - 30 * mm, id="full")
    left = Frame(margin, 15 * mm, col_w, H - 30 * mm, id="left")
    right = Frame(margin + col_w + GAP, 15 * mm, col_w, H - 30 * mm, id="right")

    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_l, cover_r], onPage=paint_cover),
        PageTemplate(id="full", frames=[full], onPage=paint_bg),
        PageTemplate(id="two", frames=[left, right], onPage=paint_bg),
    ])

    story = []

    # ===== COVER =====
    story.append(Spacer(1, 20 * mm))
    story.append(Paragraph("MARITIME INTELLIGENCE", s["eyebrow"]))
    story.append(Paragraph("SEADRAGON", s["hero"]))
    story.append(Paragraph("Maritime Data Platform", s["subhero"]))
    story.append(Paragraph(
        "We turn live ship signals into clear operational insight — "
        "so leaders can see what is happening at sea, without needing to speak database.",
        s["body"],
    ))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "One shared data backbone.<br/>"
        "Many applications on top.<br/>"
        "Always monitored. Always backed up.",
        s["body_sm"],
    ))
    story.append(FrameBreak())

    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph("In this briefing", s["h1"]))
    story.append(Spacer(1, 2 * mm))
    for flowable in text_card_stack([
        ("The shared AIS backbone", "How ship signals become trusted working data."),
        ("Applications that reuse it", "JKPTG, TSS Reporting, and maritime intelligence apps."),
        ("What the system can spot", "Going dark, close ship pairs, sensitive anchoring — in plain language."),
        ("Operations you can trust", "Grafana monitoring and AWS S3 retention."),
    ], W * 0.50):
        story.append(flowable)

    # ===== WHY =====
    story.append(NextPageTemplate("two"))
    story.append(PageBreak())
    story.append(Paragraph("Why This Platform Exists", s["h1"]))
    story.append(Paragraph(
        "Shipping generates continuous AIS messages — identity, position, speed and status. "
        "Turning that flood into <b>decisions</b> is hard: the data never stops, volumes are large, "
        "and meaningful events are rare.",
        s["body"],
    ))
    story.append(Spacer(1, GAP))
    story.append(Paragraph("What managers usually feel", s["h2"]))
    for flowable in text_card_stack([
        ("Too many hand-offs", "Collection, storage and reporting often live in separate tools."),
        ("Insight arrives late", "Or not in a form the operations room can use."),
        ("Stacks are costly to keep", "Open-source big-data platforms need constant care."),
        ("Apps rebuild plumbing", "Every new product risks reinventing AIS collection."),
    ], col_w):
        story.append(flowable)

    story.append(FrameBreak())
    story.append(Paragraph("Our answer in one line", s["h1"]))
    story.append(Paragraph(
        "Make live maritime analytics as straightforward as <b>collect → understand → act</b>.",
        s["center"],
    ))
    story.append(Spacer(1, GAP))
    story.append(icon_row([
        ("ais", "Collect", "Receive AIS continuously from the JLM source."),
        ("decode", "Understand", "Python pipeline decodes and prepares every message."),
        ("api", "Act", "Apps and APIs deliver maps, reports and alerts."),
    ], col_w, height=48 * mm))
    story.append(Spacer(1, GAP))
    story.append(Paragraph(
        "Seadragon standardises the hard middle — so product teams focus on "
        "business outcomes, not reinventing data plumbing.",
        s["body_sm"],
    ))

    # ===== BACKBONE =====
    story.append(NextPageTemplate("full"))
    story.append(PageBreak())
    story.append(Paragraph("The Shared Data Backbone", s["h1"]))
    story.append(Paragraph(
        "Every major maritime app in this estate starts from the same reliable path. "
        "Think of it as the harbour road that all terminals share.",
        s["body"],
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(Image(str(ASSETS / "diagram_core_backbone.png"),
                       width=content_w, height=48 * mm, kind="proportional"))
    story.append(Spacer(1, GAP))
    story.append(icon_row([
        ("ais", "AIS source", "Live vessel messages from the JLM server."),
        ("decode", "Python pipeline", "Decode AIS and push clean records forward."),
        ("clickhouse", "ClickHouse", "High-speed store for heavy AIS volume."),
        ("track", "Tracking processor", "Build movement and behaviour insights."),
        ("postgres", "PostgreSQL", "Working database used by applications."),
        ("grafana", "Grafana + S3", "Watch health; keep durable backups."),
    ], content_w, height=44 * mm))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "This core structure is what JKPTG and TSS share — ingest, decode, fast store, "
        "tracking, then PostgreSQL for day-to-day use.",
        s["caption"],
    ))

    # ===== INTELLIGENCE =====
    story.append(PageBreak())
    story.append(Paragraph("From Tracking to Intelligence", s["h1"]))
    story.append(Paragraph(
        "Beyond “where is the ship?”, the same data can answer “is something unusual happening?”. "
        "The PySTS / MANTIS layer turns movement history into practical alerts.",
        s["body"],
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(Image(str(ASSETS / "diagram_intelligence.png"),
                       width=content_w - 8 * mm, height=46 * mm, kind="proportional"))
    story.append(Spacer(1, GAP))
    story.append(icon_row([
        ("dark", "Going dark",
         "A ship slows, then AIS goes quiet before a normal stop is confirmed. "
         "Useful for security attention — labelled as suspected, not proven intent."),
        ("sts", "Ship-to-ship closeness",
         "Cargo and tankers that remain unusually close are scored by duration "
         "and whether the mix looks like a transfer pattern."),
        ("anchor", "Anchoring watch",
         "Large commercial ships stopped inside restricted or watch waters — "
         "busy port traffic filtered so the signal stays clear."),
    ], content_w, height=46 * mm))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "Delivered through secure MANTIS APIs and map views — ready for briefings, not just engineers.",
        s["caption"],
    ))

    # ===== OPS =====
    story.append(NextPageTemplate("two"))
    story.append(PageBreak())
    story.append(Paragraph("Always On: Monitor & Protect", s["h1"]))
    story.append(Paragraph(
        "A data platform is only as good as its day-to-day reliability.",
        s["body"],
    ))
    story.append(Spacer(1, GAP))
    story.append(IconCard(
        "grafana", "Grafana monitoring",
        "Live dashboards watch pipeline health — so delays, failures or overload "
        "are seen early, not after a report goes missing.",
        col_w, height=52 * mm,
    ))
    story.append(Spacer(1, GAP))
    story.append(IconCard(
        "s3", "AWS S3 backup & retention",
        "Python jobs copy and retain critical data in AWS S3 — "
        "durable storage for recovery, audit and long-term history.",
        col_w, height=52 * mm,
    ))
    story.append(FrameBreak())
    story.append(Paragraph("What this means for management", s["h1"]))
    for flowable in text_card_stack([
        ("Continuity", "Applications stay fed even when one consumer changes."),
        ("Accountability", "Monitoring shows whether the pipeline is healthy today."),
        ("Durability", "S3 retention protects against loss and supports investigations."),
        ("Reuse", "New apps plug into PostgreSQL / APIs instead of re-ingesting AIS."),
    ], col_w):
        story.append(flowable)
    story.append(Spacer(1, GAP))
    story.append(Paragraph(
        "In short: the estate is designed to be <b>operated</b>, not only built once.",
        s["body_sm"],
    ))

    # ===== SCALE =====
    story.append(NextPageTemplate("full"))
    story.append(PageBreak())
    story.append(Paragraph("Proven Under Load", s["h1"]))
    story.append(Paragraph(
        "The platform approach has already been exercised at serious maritime volumes.",
        s["body"],
    ))
    story.append(Spacer(1, GAP))
    story.append(stats_row([
        ("9M+", "AIS messages processed\nin a single stress-test day"),
        ("20M+", "Derived insights generated\nfor deeper analysis"),
        ("Live", "Pipeline monitored\nwith Grafana"),
        ("Shared", "One backbone serving\nmultiple applications"),
    ], content_w))
    story.append(Spacer(1, GAP))
    story.append(Paragraph(
        "Example outcome: the same testing stream supported real-time GHG-style reporting "
        "aligned with IMO guidance — showing the backbone can feed both "
        "<b>operations</b> and <b>compliance</b> narratives.",
        s["body"],
    ))
    story.append(Spacer(1, GAP))
    story.append(icon_row([
        ("postgres", "Working data", "PostgreSQL holds tracking and app-ready tables."),
        ("api", "Programmatic access", "REST APIs for dashboards and partner systems."),
        ("sts", "Behaviour insight", "Proximity, slow-down and dark-candidate logic."),
        ("anchor", "Zone awareness", "Restricted and anchorage watch overlays."),
    ], content_w, height=40 * mm))

    # ===== WHO / WHAT =====
    story.append(NextPageTemplate("two"))
    story.append(PageBreak())
    story.append(Paragraph("Who Benefits", s["h1"]))
    for flowable in text_card_stack([
        ("Operations & coast watch",
         "Faster situational picture — where ships are, and which clusters need attention."),
        ("Compliance & reporting teams",
         "Consistent source data for GHG, traffic and programme reporting."),
        ("Product & system teams",
         "Reuse the backbone; spend effort on user experience and rules, not AIS plumbing."),
        ("Leadership",
         "One architecture story: collect once, serve many, monitor and protect always."),
    ], col_w):
        story.append(flowable)

    story.append(FrameBreak())
    story.append(Paragraph("What You Get", s["h1"]))
    story.append(Paragraph("Capability package in plain terms", s["h2"]))
    for flowable in text_card_stack([
        ("Near real-time ingestion", "AIS from the operational JLM source."),
        ("Decoded, query-ready stores", "ClickHouse for volume, PostgreSQL for apps."),
        ("Tracking processors", "Maintain vessel movement context continuously."),
        ("Application pipelines", "JKPTG, TSS Reporting and intelligence APIs."),
        ("Dashboards & maps", "Human briefings without raw database work."),
        ("Monitoring & backup", "Grafana visibility, AWS S3 retention."),
        ("Flexible delivery", "API-first so partners and internal apps can connect."),
    ], col_w):
        story.append(flowable)

    # ===== ROADMAP =====
    story.append(NextPageTemplate("full"))
    story.append(PageBreak())
    story.append(Paragraph("Roadmap Direction", s["h1"]))
    story.append(Paragraph(
        "Priorities stay practical: strengthen the backbone, widen insight, keep operations calm.",
        s["body"],
    ))
    story.append(Spacer(1, GAP))
    story.append(roadmap_row([
        "<b>Now</b><br/><font color='#cbd5e1'>Stable AIS backbone, tracking into PostgreSQL, "
        "Grafana + S3, live apps (JKPTG / TSS / MANTIS APIs).</font>",
        "<b>Next</b><br/><font color='#cbd5e1'>Clearer dark-vessel confidence, coverage-aware filters, "
        "richer briefing dashboards, tighter API packaging.</font>",
        "<b>Later</b><br/><font color='#cbd5e1'>Deeper analytics for programme owners, more automated alerts, "
        "broader partner access with strong tenancy controls.</font>",
    ], content_w))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("How to talk about it externally", s["h2"]))
    story.append(TextCard(
        "One-sentence story",
        "“We run a shared maritime data platform: AIS is collected and decoded once, "
        "stored for speed and for applications, enriched by tracking processors, "
        "monitored in Grafana, retained on AWS S3, and consumed by multiple programmes "
        "through databases and APIs — including intelligence for dark activity, "
        "close-quarters pairs and sensitive anchoring.”",
        content_w,
    ))

    # ===== CLOSE =====
    story.append(PageBreak())
    story.append(Spacer(1, 16 * mm))
    story.append(Paragraph("Ready for the next conversation?", s["h1"]))
    story.append(Paragraph(
        "Show the backbone once. Then show how JKPTG, TSS Reporting and MANTIS each ride it. "
        "Finish with monitoring and backup — so the story feels complete to management.",
        s["body"],
    ))
    story.append(Spacer(1, 10 * mm))
    story.append(icon_row([
        ("decode", "Pipeline", "Shared AIS → ClickHouse → PostgreSQL path."),
        ("api", "Applications", "JKPTG, TSS Reporting, MANTIS APIs."),
        ("dark", "Intelligence", "Dark / STS / anchoring in plain language."),
        ("grafana", "Operations", "Grafana watch + AWS S3 retention."),
    ], content_w, height=42 * mm))
    story.append(Spacer(1, 10 * mm))

    cta_w = content_w - 40 * mm
    cta = Table([[Paragraph(
        "Request a live walkthrough<br/>"
        "<font size='10'>Pipeline · Applications · Intelligence · Operations</font>",
        s["cta"],
    )]], colWidths=[cta_w])
    cta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ACCENT),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    # center CTA with side spacers
    story.append(Table([[Spacer(20 * mm, 1), cta, Spacer(20 * mm, 1)]],
                       colWidths=[20 * mm, cta_w, 20 * mm]))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "Seadragon Maritime Data Platform  ·  Support local, serve the region",
        s["caption"],
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        "Intelligence outputs are operational aids. Combine with local rules, permits and human review.",
        s["caption"],
    ))

    doc.build(story)
    print(f"Wrote {OUT_PDF}")


if __name__ == "__main__":
    build()
