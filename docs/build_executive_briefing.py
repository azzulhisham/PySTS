"""MANTIS leadership deck. Widescreen. Screenshots come from the live dashboard."""

import math

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.chart.data import XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

NAVY = RGBColor(0x0A, 0x16, 0x28)
INK = RGBColor(0x16, 0x23, 0x33)
TEAL = RGBColor(0x1F, 0xA7, 0xB8)
TEAL_DARK = RGBColor(0x0E, 0x6E, 0x7C)
GOLD = RGBColor(0xC4, 0xA3, 0x5A)
RED = RGBColor(0xC2, 0x4B, 0x4B)
PAPER = RGBColor(0xF6, 0xF4, 0xEF)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
MUTED = RGBColor(0x5C, 0x68, 0x74)
BODY = RGBColor(0x24, 0x31, 0x40)
LINE = RGBColor(0xE3, 0xDD, 0xD2)
DEEP = RGBColor(0x12, 0x24, 0x38)
PALE = RGBColor(0xD5, 0xE0, 0xE8)
SOFT = RGBColor(0xE8, 0xF4, 0xF6)

W = Inches(13.333)
H = Inches(7.5)
SLIDES = "/Users/zultan/sources/python/PySTS/docs/slides"
OUT = "/Users/zultan/sources/python/PySTS/docs/MANTIS-Executive-Briefing.pptx"


def _run(paragraph, value, size, color, bold=False, font="Calibri"):
    for i, part in enumerate(value.split("\n")):
        if i:
            paragraph.add_line_break()
        run = paragraph.add_run()
        run.text = part
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = False
        run.font.color.rgb = color


def _frame(slide, x, y, w, h):
    tf = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h)).text_frame
    tf.word_wrap = True
    tf.auto_size = None
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = Emu(0)
    return tf


def text(slide, value, x, y, w, h, size=14, color=BODY, bold=False, align=PP_ALIGN.LEFT, anchor="t"):
    tf = _frame(slide, x, y, w, h)
    tf._txBody.bodyPr.set("anchor", anchor)
    p = tf.paragraphs[0]
    p.alignment = align
    _run(p, value, size, color, bold)
    return tf


def items(slide, rows, x, y, w, h, size=12, color=BODY, spacing=6, lead_color=INK, numbered=False):
    """rows: str, or (lead, body). Lead is bold; numbered adds 1., 2., ..."""
    tf = _frame(slide, x, y, w, h)
    for i, row in enumerate(rows):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(spacing)
        if numbered:
            _run(p, f"{i + 1}.  ", size, TEAL_DARK, True)
        if isinstance(row, tuple):
            lead, body = row
            _run(p, lead, size, lead_color, True)
            _run(p, "  " + body, size, color)
        else:
            _run(p, row, size, color)
    return tf


def stacked(slide, rows, x, y, w, h, size=11, spacing=7, title_color=INK, color=BODY):
    """Title on its own line, explanation underneath."""
    tf = _frame(slide, x, y, w, h)
    for i, (title, body) in enumerate(rows):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(1)
        _run(p, title, size + 0.5, title_color, True)
        q = tf.add_paragraph()
        q.space_after = Pt(spacing)
        _run(q, body, size, color)
    return tf


def rect(slide, x, y, w, h, fill, line=None, radius=False):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h),
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    shape.shadow.inherit = False
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    if radius:
        shape.adjustments[0] = 0.08
    return shape


def arrow(slide, x1, y1, x2, y2, color=TEAL, width=1.75):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    conn.line.color.rgb = color
    conn.line.width = Pt(width)
    ln = conn.line._get_or_add_ln()
    tail = etree.SubElement(ln, qn("a:tailEnd"))
    tail.set("type", "triangle")
    tail.set("w", "med")
    tail.set("len", "med")
    return conn


def node(slide, x, y, w, h, title, sub=None, fill=WHITE, line=LINE, title_color=NAVY, sub_color=MUTED, size=12):
    rect(slide, x, y, w, h, fill, line, radius=True)
    if sub:
        text(slide, title, x + 0.12, y + 0.08, w - 0.24, 0.3, size, title_color, True)
        text(slide, sub, x + 0.12, y + 0.36, w - 0.24, h - 0.42, size - 1.5, sub_color)
    else:
        text(slide, title, x + 0.12, y, w - 0.24, h, size, title_color, True, anchor="ctr")


def footer(slide, dark=False):
    page = slide.part.package.presentation_part.presentation.slides.index(slide) + 1
    color = RGBColor(0x8A, 0x97, 0xA3) if dark else MUTED
    text(slide, "MANTIS  |  Maritime Intelligence & Intercept System", 0.48, 7.1, 6, 0.26, 10, color, True)
    text(slide, "October 2026", 5.2, 7.1, 3, 0.26, 10, color, align=PP_ALIGN.CENTER)
    text(slide, f"{page}", 11.3, 7.1, 1.55, 0.26, 10, color, align=PP_ALIGN.RIGHT)


def new(prs, paper=True):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    rect(slide, 0, 0, 13.333, 7.5, PAPER if paper else NAVY)
    rect(slide, 0, 0, 0.08, 7.5, TEAL)
    return slide


def heading(slide, kicker, title, lead=None):
    text(slide, kicker.upper(), 0.48, 0.28, 9, 0.24, 11, TEAL_DARK, True)
    text(slide, title, 0.46, 0.5, 12.4, 0.5, 26, NAVY, True)
    if lead:
        text(slide, lead, 0.48, 1.02, 12.35, 0.42, 13.5, MUTED)


def label(slide, value, x, y, w, color=TEAL_DARK):
    text(slide, value.upper(), x, y, w, 0.22, 10.5, color, True)
    rect(slide, x, y + 0.27, w, 0.02, LINE)


def shot(slide, name, x, y, w, h=None):
    path = f"{SLIDES}/{name}"
    with Image.open(path) as im:
        aspect = im.width / im.height
    if h is None:
        h = w / aspect
    if aspect > w / h:
        dw, dh = w, w / aspect
    else:
        dh, dw = h, h * aspect
    ox, oy = x + (w - dw) / 2, y + (h - dh) / 2
    rect(slide, ox - 0.04, oy - 0.04, dw + 0.08, dh + 0.08, NAVY)
    slide.shapes.add_picture(path, Inches(ox), Inches(oy), Inches(dw), Inches(dh))
    return oy + dh


def module_slide(prs, name, title, purpose, pipeline, figures, image, location, params):
    s = new(prs)
    heading(s, f"Analytics module  |  {name}", title, purpose)
    label(s, "Data pipeline and processing logic", 0.48, 1.58, 4.5)
    items(s, pipeline, 0.48, 1.98, 4.5, 4.95, 12, numbered=True, spacing=7)
    label(s, "How the dashboard figures are derived", 5.25, 1.58, 3.45)
    stacked(s, figures, 5.25, 1.98, 3.45, 4.95, 11, spacing=7)
    bottom = shot(s, image, 8.95, 1.6, 3.9)
    text(s, f"Dashboard location:  {location}", 8.95, bottom + 0.1, 3.9, 0.26, 10.5, TEAL_DARK, True)
    top = bottom + 0.45
    rect(s, 8.95, top, 3.9, 6.95 - top, DEEP, radius=True)
    text(s, "KEY PARAMETERS", 9.12, top + 0.1, 3.6, 0.22, 9.5, TEAL, True)
    items(s, params, 9.12, top + 0.36, 3.6, 6.95 - top - 0.4, 9.5, PALE, spacing=2, lead_color=WHITE)
    footer(s)


def build():
    prs = Presentation()
    prs.slide_width = W
    prs.slide_height = H
    prs.core_properties.title = "MANTIS - Maritime Intelligence & Intercept System"
    prs.core_properties.subject = "Platform architecture, analytics modules and data roadmap"

    # 1. Title
    s = new(prs, paper=False)
    text(s, "MARITIME INTELLIGENCE & INTERCEPT SYSTEM", 0.62, 1.3, 7, 0.28, 13, TEAL, True)
    text(s, "MANTIS", 0.58, 1.62, 7, 0.95, 60, WHITE, True)
    text(s, "Platform architecture, analytics modules\nand the path to machine learning", 0.62, 2.7, 6.5, 0.9, 20, PALE)
    rect(s, 0.62, 3.85, 1.4, 0.045, GOLD)
    text(s, "Malaysian waters\nStraits of Malacca and South China Sea", 0.62, 4.15, 6.2, 0.7, 15, RGBColor(0xC9, 0xD4, 0xDC))
    text(s, "Leadership review  |  October 2026", 0.62, 5.6, 6.2, 0.3, 14, RGBColor(0x9A, 0xAB, 0xB8))
    shot(s, "01-overview.png", 7.35, 0.85, 5.45, 5.7)
    footer(s, dark=True)

    # 2. Executive summary
    s = new(prs)
    heading(
        s, "Executive summary",
        "MANTIS converts AIS traffic into reviewable maritime intelligence",
        "The platform is in its data collection and validation phase. All modules and pipelines described here are in production; planned items appear on the roadmap.",
    )
    summary = [
        ("Coverage.", "Six analytics modules run on live AIS data for Malaysian waters: ship-to-ship transfer (STS), dark vessels, illegal anchoring, AIS spoofing, loitering and identity conflict. OFAC sanctions screening is applied across all of them."),
        ("Processing.", "Each module is backed by a scheduled data pipeline on the processing server. Pipelines write their results to the operational database; the dashboard and REST API read those results through a read-only connection."),
        ("Review workflow.", "Every STS observation becomes a review case, enriched with the outputs of the other modules. Cases that remain unexplained after that enrichment are raised to analysts, who record a final classification."),
        ("Infrastructure.", "The platform runs on the existing processing server, API server and databases. No additional infrastructure was introduced for this phase."),
        ("Next phase.", "Analyst classifications form the labelled dataset for machine learning. Model development starts once that dataset is large and consistent enough to be trained and evaluated against."),
    ]
    items(s, summary, 0.48, 1.7, 7.6, 5.2, 13, spacing=10)
    rect(s, 8.45, 1.7, 4.4, 5.15, DEEP, radius=True)
    text(s, "PLATFORM AT A GLANCE", 8.7, 1.9, 3.9, 0.25, 11, TEAL, True)
    facts = [
        ("6", "analytics modules on the dashboard"),
        ("6", "scheduled data pipelines in production"),
        ("30 s", "refresh cycle for STS proximity"),
        ("5", "review classes recorded by analysts"),
    ]
    for i, (num, desc) in enumerate(facts):
        y = 2.35 + i * 1.1
        text(s, num, 8.7, y, 1.5, 0.6, 30, WHITE, True)
        text(s, desc, 10.15, y + 0.12, 2.55, 0.6, 12.5, PALE)
    footer(s)

    # 3. Architecture
    s = new(prs)
    heading(
        s, "Platform architecture",
        "Four tiers: data sources, processing, storage and service",
        "Pipelines are the only components that write analytical results. The API and dashboard are read-only consumers.",
    )
    cols = [
        (0.4, 2.55, "Data sources"),
        (3.3, 3.05, "Processing server  |  data pipelines"),
        (6.75, 2.75, "Operational database"),
        (9.95, 2.95, "API server  |  consumers"),
    ]
    for x, w, name in cols:
        label(s, name, x, 1.6, w)
    sources = [
        ("AIS position history", "ClickHouse, full track per vessel"),
        ("Latest AIS position and static data", "Postgres, one row per vessel"),
        ("JKPTG licence register", "Dredging project licences"),
        ("OFAC sanctions lists", "SDN and Consolidated vessel entries"),
        ("Zone definitions", "Anchorage, watch and restricted polygons"),
    ]
    for i, (t, sub) in enumerate(sources):
        node(s, 0.4, 2.0 + i * 0.95, 2.55, 0.82, t, sub, size=11.5)
    pipes = [
        ("Movement activity", "Every 20 s"),
        ("Slow movement", "Every 20 s"),
        ("Proximity (STS)", "Every 30 s"),
        ("Loitering", "Every 15 min"),
        ("Position anomaly", "Every 15 min"),
        ("STS review case", "Every 15 min"),
    ]
    for i, (t, sub) in enumerate(pipes):
        node(s, 3.3, 2.0 + i * 0.79, 3.05, 0.68, t, sub, fill=SOFT, line=TEAL, size=11.5)
    stores = [
        ("Activity records", "Movement, slow movement, loitering"),
        ("STS observations", "Clusters, members, suspicion score"),
        ("Position anomalies", "One row per vessel per UTC day"),
        ("STS review cases", "Enrichment flags and analyst decision"),
    ]
    for i, (t, sub) in enumerate(stores):
        node(s, 6.75, 2.0 + i * 1.18, 2.75, 1.02, t, sub, size=11.5)
    node(s, 9.95, 2.0, 2.95, 1.45, "MANTIS REST API",
         "Gunicorn, two workers. Read-only connection pool. Review verification uses one separate write connection.",
         fill=DEEP, line=None, title_color=WHITE, sub_color=PALE, size=12)
    consumers = [
        ("Dashboard", "Live operating picture"),
        ("Notification and review", "Unexplained STS cases, analyst decisions"),
        ("Reports and dataset", "Unreviewed backlog, labelled cases"),
    ]
    for i, (t, sub) in enumerate(consumers):
        node(s, 9.95, 3.7 + i * 1.08, 2.95, 0.92, t, sub, size=11.5)
    arrow(s, 2.98, 4.35, 3.27, 4.35)
    arrow(s, 6.38, 4.35, 6.72, 4.35)
    arrow(s, 9.53, 4.35, 9.92, 4.35)
    arrow(s, 11.42, 3.47, 11.42, 3.68, width=1.5)
    footer(s)

    # 4. Pipeline catalogue
    s = new(prs)
    heading(
        s, "Data pipelines",
        "Each module is fed by a scheduled pipeline with a defined input and output",
        "Pipelines run as managed services on the processing server and restart automatically on failure.",
    )
    header = ["Data pipeline", "Input", "Cycle", "Output", "Consumed by"]
    rows = [
        ["Movement activity", "Latest AIS position per vessel", "20 s", "Movement records (stop at 0.5 kn)", "Illegal anchoring, STS candidates"],
        ["Slow movement", "Latest AIS position per vessel", "20 s", "Slow-movement records (3 kn)", "Dark vessels, STS enrichment"],
        ["Proximity (STS)", "Movement records, static data, licence cache", "30 s", "STS observations and members", "STS module, review cases"],
        ["Loitering", "AIS track, previous 2 hours", "15 min", "Loitering events with context flags", "Loitering module, STS enrichment"],
        ["Position anomaly", "AIS position history (cargo, tanker)", "15 min", "Daily position anomaly per vessel", "Spoofing module, STS enrichment"],
        ["STS review case", "STS observations and all outputs above", "15 min*", "Enriched review cases", "Notification, review, dataset"],
        ["Licence cache", "JKPTG project licence register", "12 h", "In-memory licence list", "Proximity admission, review cases"],
        ["Identity and sanctions", "Static data, OFAC lists", "On request", "API response", "All modules"],
    ]
    widths = [2.15, 3.15, 1.05, 3.0, 2.95]
    table = s.shapes.add_table(len(rows) + 1, 5, Inches(0.48), Inches(1.65), Inches(sum(widths)), Inches(4.9)).table
    for c, wv in enumerate(widths):
        table.columns[c].width = Inches(wv)
    for r in range(len(rows) + 1):
        table.rows[r].height = Inches(0.5)
        for c in range(5):
            cell = table.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.1)
            cell.margin_top = cell.margin_bottom = Inches(0.05)
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = DEEP
                value, color, bold = header[c], WHITE, True
            else:
                cell.fill.fore_color.rgb = WHITE if r % 2 else SOFT
                value, color, bold = rows[r - 1][c], NAVY if c == 0 else BODY, c == 0
            tf = cell.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = ""
            _run(p, value, 11.5, color, bold)
    text(s, "* The review-case pipeline processes 100 observations per batch. While a backlog exists it continues every 5 seconds, then returns to the 15-minute cycle.",
         0.48, 6.65, 12.3, 0.3, 11, MUTED)
    footer(s)

    # 5. Overview
    s = new(prs)
    heading(s, "Dashboard", "Operations overview", "A single summary of the four primary modules, refreshed on demand.")
    bottom = shot(s, "01-overview.png", 0.48, 1.6, 5.3)
    text(s, "Dashboard location:  Operations  >  Overview", 0.48, bottom + 0.1, 5.3, 0.26, 10.5, TEAL_DARK, True)
    label(s, "How the overview is assembled", 6.3, 1.6, 6.55)
    overview = [
        ("Single request.", "The dashboard calls one overview endpoint. The API runs the dark-vessel, STS and illegal-anchoring queries on the operational database in one thread and reads the stored position anomalies in a second thread, then returns summary counts only."),
        ("Same rules as the module pages.", "Each tile applies exactly the rule used on its module page. Selecting a tile opens that module with the full vessel list."),
        ("Short cache.", "The summary is cached in the API for up to two minutes so repeated page loads do not re-run the queries. A module page opened afterwards can reflect a slightly later refresh."),
        ("Sanctioned flags.", "The total of OFAC matches across the four modules. A vessel that appears in two modules is counted in both."),
        ("Flagged now.", "Each module's count, split into sanctioned and non-sanctioned vessels."),
    ]
    items(s, overview, 6.3, 2.0, 6.55, 4.9, 13, spacing=10)
    footer(s)

    # 6. STS
    module_slide(
        prs, "Ship-to-ship transfer", "Ship-to-ship transfer (STS)",
        "Identifies large vessels holding position alongside each other long enough to allow a cargo transfer.",
        [
            ("Candidate selection.", "Every 30 seconds the pipeline reads the latest movement record per vessel and keeps cargo and tanker vessels (AIS type 70 to 89) that have been stopped for at least one hour or have not reported for 30 minutes."),
            ("Dredger admission.", "A dredger is admitted only when its MMSI holds an in-force JKPTG licence and its type is not excluded. Fishing, tug, towing, pilot, passenger, pleasure, sailing, high-speed craft, port tender and search-and-rescue vessels are always excluded."),
            ("Pair search.", "Great-circle distance is computed between candidates. Pairs within 30 m are kept; a licensed dredger must be paired with a cargo or tanker vessel. Two MMSIs belonging to the same hull are never paired."),
            ("Observation lifecycle.", "Connected pairs form one cluster with a stable signature. The cluster is updated each cycle, its suspicion score is recalculated, and it is closed after 60 seconds without the vessels in range."),
            ("Dashboard filter.", "The API keeps open clusters scoring 4.5 or more whose centre lies inside an anchorage polygon extended by 1 km, removes designated exclusion zones, and re-measures pairs at up to 35 m to allow for GPS drift."),
        ],
        [
            ("Active pairs", "Vessel pairs remaining after the dashboard filter."),
            ("Paired vessels", "Distinct vessels across those pairs."),
            ("In anchorage", "Open clusters scoring 4.5 or more whose centre is inside the extended anchorage area."),
            ("Open high-score", "All open clusters scoring 4.5 or more, regardless of location. Clusters outside the anchorage area are still converted into review cases."),
            ("Sanctioned", "Pairs in which at least one vessel matches the OFAC list."),
        ],
        "03-sts.png", "Intelligence  >  STS Activities",
        [
            ("Cycle", "30 s, closed after 60 s out of range"),
            ("Distance", "30 m stored, 35 m on the dashboard"),
            ("Score threshold", "4.5 (configurable per request)"),
            ("Anchorage buffer", "1 km on parent polygons"),
        ],
    )

    # 7. Score
    s = new(prs)
    heading(
        s, "Analytics module  |  Ship-to-ship transfer",
        "STS suspicion score: formula and rationale",
        "The score determines the order in which STS observations are reviewed. It is recalculated every time an observation is updated.",
    )
    chart_data = XyChartData()
    hours = [h / 4 for h in range(0, 49)]
    curves = [
        ("Tanker and tanker", 2.5),
        ("Cargo and cargo", 1.75),
        ("Cargo and tanker", 1.0),
    ]
    for name, offset in curves:
        series = chart_data.add_series(name)
        for h in hours:
            series.add_data_point(h, round(2 * math.log1p(h) + offset, 3))
    threshold = chart_data.add_series("Review threshold (4.5)")
    threshold.add_data_point(0, 4.5)
    threshold.add_data_point(12, 4.5)
    gframe = s.shapes.add_chart(
        XL_CHART_TYPE.XY_SCATTER_SMOOTH_NO_MARKERS,
        Inches(0.4), Inches(1.55), Inches(7.2), Inches(4.25), chart_data,
    )
    chart = gframe.chart
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(10.5)
    chart.legend.font.color.rgb = BODY
    chart.font.name = "Calibri"
    colors = [TEAL, GOLD, NAVY, RED]
    for series, color in zip(chart.series, colors):
        series.format.line.color.rgb = color
        series.format.line.width = Pt(2.25)
        series.smooth = True
    chart.series[3].format.line.dash_style = MSO_LINE.DASH
    chart.series[3].format.line.width = Pt(1.5)
    x_axis, y_axis = chart.category_axis, chart.value_axis
    x_axis.minimum_scale, x_axis.maximum_scale, x_axis.major_unit = 0, 12, 2
    y_axis.minimum_scale, y_axis.maximum_scale, y_axis.major_unit = 0, 8, 1
    for axis, title in ((x_axis, "Hours alongside"), (y_axis, "Suspicion score")):
        axis.has_title = True
        axis.axis_title.text_frame.text = title
        run = axis.axis_title.text_frame.paragraphs[0].runs[0]
        run.font.size = Pt(11)
        run.font.bold = False
        run.font.color.rgb = MUTED
        axis.tick_labels.font.size = Pt(10)
        axis.tick_labels.font.color.rgb = MUTED
        axis.format.line.color.rgb = LINE
    y_axis.has_major_gridlines = True
    y_axis.major_gridlines.format.line.color.rgb = LINE
    x_axis.has_major_gridlines = False

    text(s, "DURATION TERM  2 x ln(1 + hours)", 0.48, 5.95, 7.1, 0.22, 10, TEAL_DARK, True)
    for i, (hrs, val) in enumerate([("1 hour", "1.4"), ("6 hours", "3.9"), ("24 hours", "6.4"), ("48 hours", "7.8")]):
        x = 0.48 + i * 1.8
        rect(s, x, 6.22, 1.68, 0.72, WHITE, LINE, radius=True)
        text(s, val, x + 0.14, 6.27, 0.8, 0.4, 18, NAVY, True)
        text(s, hrs, x + 0.82, 6.36, 0.82, 0.3, 11, MUTED)

    rect(s, 7.85, 1.55, 5.0, 1.38, DEEP, radius=True)
    text(s, "Score = 2 x ln(1 + hours) + (vessels - 1) + 1.5 x composition", 8.05, 1.7, 4.65, 0.32, 13, WHITE, True)
    text(s, "Hours: time the cluster has been open.  Vessels: number in the cluster.  Composition: 1.0 with two or more tankers, 0.5 with two or more cargo ships, otherwise 0.",
         8.05, 2.1, 4.65, 0.75, 11, PALE)
    label(s, "Why duration uses a logarithm", 7.85, 3.1, 5.0)
    rationale = [
        ("Starts at zero.", "ln(1 + 0) = 0, so a pair that has only just formed carries no duration weight."),
        ("Diminishing returns.", "The first hours carry the most weight. The first six hours add 3.9 points; the whole second day adds 1.3. A long routine wait still raises the score, but not by a fixed amount every hour."),
        ("Balance between terms.", "Because duration flattens, the vessel count (+1.0 per additional vessel) and the composition term (up to +1.5) keep their influence. With a linear term, a long berth wait would outrank a shorter meeting with a more significant composition."),
        ("Scale.", "The coefficient 2.0 sets how quickly duration accumulates. The weights follow operational rules today and will be calibrated against analyst-reviewed cases in the modelling phase."),
    ]
    items(s, rationale, 7.85, 3.48, 5.0, 2.45, 10.5, spacing=4)
    text(s, "TIME TO REACH 4.5", 7.85, 5.95, 5.0, 0.22, 10, TEAL_DARK, True)
    for i, (val, desc) in enumerate([("1.7 h", "Tanker and tanker"), ("3.0 h", "Cargo and cargo"), ("4.8 h", "Cargo and tanker")]):
        x = 7.85 + i * 1.69
        rect(s, x, 6.22, 1.6, 0.72, WHITE, LINE, radius=True)
        text(s, val, x + 0.12, 6.26, 1.4, 0.32, 15, NAVY, True)
        text(s, desc, x + 0.12, 6.58, 1.45, 0.3, 9.5, MUTED)
    footer(s)

    # Composition weighting
    s = new(prs)
    heading(
        s, "Analytics module  |  Ship-to-ship transfer",
        "Vessel composition: why tanker-to-tanker carries the highest weight",
        "The composition term reflects how transfers are carried out in Malaysian waters. Cargo is AIS type 70 to 79; tanker is 80 to 89.",
    )
    header = ["Cluster composition", "Weight", "Typical activity", "Interpretation"]
    rows = [
        ["Two or more tankers\n(a cargo ship may also be present)", "+1.5",
         "Crude oil or fuel moved from one tanker to another.",
         "The pattern used to change the vessel carrying a cargo and obscure where it came from. Highest weight."],
        ["Two or more cargo ships", "+0.75",
         "Bulk transhipment, for example dredged sand or minerals.",
         "Often lawful, but also used to move undeclared bulk cargo. Moderate weight."],
        ["Cargo ship beside one tanker", "0",
         "Bunkering: a bunker tanker refuelling a cargo ship.",
         "Routine across the Straits, one of the largest bunkering markets in the world. No weight, so bunkering does not crowd the review queue."],
    ]
    widths = [3.0, 1.0, 3.6, 4.75]
    table = s.shapes.add_table(4, 4, Inches(0.48), Inches(1.65), Inches(sum(widths)), Inches(2.9)).table
    for c, wv in enumerate(widths):
        table.columns[c].width = Inches(wv)
    for r in range(4):
        table.rows[r].height = Inches(0.45 if r == 0 else 0.8)
        for c in range(4):
            cell = table.cell(r, c)
            cell.margin_left = cell.margin_right = Inches(0.12)
            cell.margin_top = cell.margin_bottom = Inches(0.07)
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = DEEP
                value, color, bold, size = header[c], WHITE, True, 12
            else:
                cell.fill.fore_color.rgb = SOFT if r == 1 else WHITE
                value = rows[r - 1][c]
                color = NAVY if c in (0, 1) else BODY
                bold = c in (0, 1)
                size = 15 if c == 1 else 12
            p = cell.text_frame.paragraphs[0]
            cell.text_frame.word_wrap = True
            p.text = ""
            _run(p, value, size, color, bold)
    label(s, "Why tanker-to-tanker transfers rank first in Malaysian waters", 0.48, 4.85, 12.35)
    reasons = [
        ("Regional transfer area.", "Waters off eastern Johor and the southern South China Sea are widely reported as a transfer area where crude from sanctioned sources is moved between tankers and re-documented before onward shipment."),
        ("Change of carrying vessel.", "A tanker-to-tanker transfer moves the cargo onto a different hull, which breaks the documented link between origin and destination. A bunker delivery to a cargo ship does not change who carries the cargo."),
        ("Lawful transfers have a known setting.", "Authorised lightering normally takes place in designated areas with approval. A tanker pair outside the anchorage area, or alongside a dark or sanctioned vessel, is the case analysts need to see first."),
    ]
    items(s, reasons, 0.48, 5.25, 12.35, 1.75, 12, spacing=5)
    footer(s)

    # 8. Dark vessels
    module_slide(
        prs, "Dark vessels", "Dark vessels",
        "Flags large vessels that slowed down and then stopped transmitting AIS while still in the area.",
        [
            ("Slow-movement tracking.", "Every 20 seconds the pipeline reads the latest AIS report per vessel. A report at or below 3 knots opens or extends a slow-movement record, which counts position updates and keeps the latest position, speed and time."),
            ("Stop marking.", "A record is marked stopped when it reaches 30 updates within 30 m (a stop that is still being transmitted), or when no new report has arrived for 30 minutes (a transmission gap)."),
            ("Record closure.", "A record is closed once the vessel sustains a speed above 3 knots and has moved at least 100 m from where the record started."),
            ("Candidate rule.", "The API selects open, stopped records for AIS type 70 to 89 with fewer than 30 updates and a last report at least 30 minutes old. Records with 30 or more updates are excluded because the vessel was still transmitting when it stopped."),
            ("Location labelling.", "The last position is labelled with the anchorage or exclusion zone it falls in. Zones label a record; they never remove it."),
        ],
        [
            ("Candidates", "Records passing the candidate rule. The Slowdown Only view removes possible coverage exits."),
            ("Suspected dark", "5 to 29 updates during the slowdown and a last report within the past 3 days."),
            ("Coverage exit", "Last report older than 3 days, or last speed above 3 knots. The vessel has more likely left receiver coverage."),
            ("High confidence", "Same condition as suspected dark. Coverage exits are low confidence; the remainder is medium."),
            ("Sanctioned", "Candidates matching the OFAC list."),
        ],
        "02-dark.png", "Intelligence  >  Dark Vessels",
        [
            ("Slow speed", "3 knots or below"),
            ("Silence", "30 minutes since the last report"),
            ("Confirmed stop", "30 updates within 30 m"),
            ("Coverage window", "3 days"),
        ],
    )

    # 9. Illegal anchoring
    module_slide(
        prs, "Illegal anchoring", "Illegal anchoring",
        "Flags large vessels that have stopped inside restricted or monitored waters.",
        [
            ("Movement tracking.", "Every 20 seconds the pipeline reads the latest AIS report per vessel. A report at or below 0.5 knots opens or extends a movement record, which is marked stopped after 20 updates within 30 m."),
            ("Stopped population.", "The API takes the latest open movement record per vessel for AIS type 70 to 89 that has been stopped for at least one hour, or has not reported for 30 minutes."),
            ("Zone test.", "Each last position is tested against the restricted-limit polygon and the anchorage and watch polygons using a spatial point-in-polygon query."),
            ("Exclusions.", "Positions inside designated exclusion zones, which are carve-outs within a parent polygon, are removed."),
            ("Labelling.", "Each vessel is labelled restricted zone, watch polygon, or both. OFAC matching is applied afterwards and sanctioned vessels are listed first."),
        ],
        [
            ("Illegal anchorages", "Stopped vessels that pass the zone test after exclusions."),
            ("Restricted + watch", "Vessels inside both the restricted limit and a watch polygon."),
            ("Restricted zone", "Vessels inside the restricted limit only."),
            ("Watch polygon", "Vessels inside a watch polygon only. The three zone counts do not overlap and add up to the total."),
            ("Sanctioned", "Flagged vessels matching the OFAC list."),
        ],
        "04-anchoring.png", "Intelligence  >  Illegal Anchoring",
        [
            ("Stop speed", "0.5 knots or below"),
            ("Stopped", "20 updates within 30 m, held 1 hour"),
            ("Stale report", "30 minutes"),
            ("Vessel types", "AIS 70 to 89, cargo and tanker"),
        ],
    )

    # 10. Spoofing
    module_slide(
        prs, "AIS spoofing", "AIS spoofing (position anomaly)",
        "Identifies AIS position reports that imply a movement no merchant vessel can make.",
        [
            ("Input.", "Every 15 minutes the pipeline reads the AIS position history for cargo and tanker vessels. The list of those vessels is refreshed every 12 hours."),
            ("Consecutive comparison.", "A window function pairs each report with the previous one for the same vessel and calculates great-circle distance, time gap and implied speed in knots."),
            ("Threshold.", "The gap must be 10 to 900 seconds and the jump at least 1 km. The pair is kept when the jump is 20 km or more at over 50 knots (teleport), or when the implied speed exceeds 80 knots (implausible speed)."),
            ("Single-point filter.", "When the reports before and after a suspect point lie within 2 km of each other, and that gap is less than 10 percent of the jump, the point is treated as a bad fix and discarded."),
            ("Storage.", "One row is kept per vessel per UTC day, holding the highest implied speed. It is replaced only by a faster jump on the same day. The dashboard reads these stored rows."),
        ],
        [
            ("Events", "Stored daily anomalies within the last three days."),
            ("Vessels", "Distinct vessels across those events."),
            ("Teleport", "Jump of 20 km or more at an implied speed above 50 knots."),
            ("Implausible speed", "Implied speed above 80 knots that is not a teleport."),
            ("Sanctioned", "Events where the vessel matches the OFAC list."),
        ],
        "05-spoofing.png", "Intelligence  >  Spoofing",
        [
            ("Time gap", "10 to 900 seconds"),
            ("Teleport", "20 km or more and above 50 knots"),
            ("Implausible speed", "Above 80 knots"),
            ("Bad-fix filter", "2 km, under 10 percent of the jump"),
        ],
    )

    # 11. Loitering
    module_slide(
        prs, "Loitering", "Loitering",
        "Identifies vessels circling within a confined area for hours without making a passage.",
        [
            ("Input.", "Every 15 minutes the pipeline reads the previous two hours of AIS track for cargo and tanker vessels."),
            ("Track geometry.", "For each vessel it calculates the centre of the track, the radius (furthest point from the centre), the path length and the net displacement between the first and last position."),
            ("Storage rule.", "An event is stored when the track spans at least 2 hours with 8 or more reports, the radius is 100 m to 6 nautical miles, net displacement is under 0.5 nautical miles and net displacement is under 20 percent of the path."),
            ("Context flags.", "Outside anchorage: the centre is not in an anchorage polygon. Near restricted: inside or within 5 nautical miles of the restricted limit. Before STS: an STS observation for the vessel starts during the event or within 6 hours after it, within 5 nautical miles."),
            ("Pattern separation.", "Loitering requires a path of 2 nautical miles or more and a radius at least the hull length. A radius smaller than the hull is classified as anchor swing, which is normal behaviour."),
        ],
        [
            ("Events", "Open events carrying at least one context flag."),
            ("Outside anchorage", "Events whose centre is outside every anchorage polygon."),
            ("Near restricted", "Events inside or within 5 nautical miles of the restricted limit."),
            ("Before STS", "Events followed by an STS observation for the same vessel within 6 hours."),
            ("Sanctioned", "Events where the vessel matches the OFAC list."),
        ],
        "06-loitering.png", "Intelligence  >  Loitering",
        [
            ("Window", "2 hours, at least 8 reports"),
            ("Radius", "100 m to 6 nautical miles"),
            ("Net movement", "Under 0.5 nautical miles"),
            ("Hull floor", "Lengths under 300 m treated as 300 m"),
        ],
    )

    # 12. Identity conflict
    module_slide(
        prs, "Identity conflict", "Identity conflict",
        "Finds a single physical vessel that is broadcasting under more than one MMSI.",
        [
            ("Input.", "On each request the API reads the current static record per MMSI for AIS type 70 to 89, joined to the vessel's last reported position."),
            ("Normalisation.", "Names and call signs are upper-cased and AIS padding is removed. Values under 3 characters are ignored. An IMO must be 7 digits; placeholder and repeated-digit IMOs are discarded."),
            ("Matching.", "Two MMSIs describe the same hull when the IMO matches together with the name, call sign or dimensions, or when the name matches together with the call sign or dimensions. One shared field is never sufficient."),
            ("Grouping.", "Matched pairs are merged into groups of two or more MMSIs using a union-find structure. Each group carries its latest AIS time and the distance between its members' last positions."),
            ("Screening.", "OFAC matching is applied to every member and sanctioned groups are listed first."),
        ],
        [
            ("Conflict groups", "Groups of two or more MMSIs resolved to one hull."),
            ("Identities involved", "MMSIs across all groups."),
            ("Confirmed", "Groups whose match is anchored on a shared, valid IMO number."),
            ("Sanctioned groups", "Groups with at least one member matching the OFAC list."),
            ("Candidates scanned", "Static records evaluated on this request."),
        ],
        "07-identity.png", "Intelligence  >  Identity Conflict",
        [
            ("Match rule", "IMO + one field, or name + one field"),
            ("Fields", "IMO, name, call sign, dimensions"),
            ("Ignored", "Placeholder IMOs, short names"),
            ("Vessel types", "AIS 70 to 89"),
        ],
    )

    # 13. Sanctions
    module_slide(
        prs, "Sanctions screening", "Sanctions screening",
        "Screens every vessel surfaced by the analytics modules against the OFAC sanctions lists.",
        [
            ("Reference data.", "OFAC SDN and Consolidated vessel entries are held in the operational database. IMO and MMSI numbers are extracted from each entry and indexed in the API, refreshed every 5 minutes."),
            ("Match order.", "An IMO match is recorded as confirmed. An MMSI match is used only when the vessel has no IMO and is recorded as possible. If the vessel has an IMO that does not match, the MMSI is not used, which avoids false matches on reassigned MMSIs. Names are not matched."),
            ("Application.", "The label is attached to dark vessels, both sides of an STS pair, illegal anchoring, spoofing, loitering and identity groups. Sanctioned vessels are sorted to the top of every list."),
            ("Scoring.", "The sanctions label affects priority only. It does not change the STS suspicion score or dark-vessel confidence."),
        ],
        [
            ("Listed vessels", "Vessel entries in the sanctions registry."),
            ("Seen in region now", "Listed vessels present in at least one of the live module feeds."),
            ("Feeds checked", "Live module feeds re-queried for matches: dark vessels, STS, illegal anchoring and spoofing."),
            ("Programmes", "Distinct sanctions programmes across the listed entries."),
        ],
        "08-sanctions.png", "Intelligence  >  Sanctions",
        [
            ("Confirmed", "IMO match"),
            ("Possible", "MMSI match, vessel has no IMO"),
            ("Index refresh", "5 minutes"),
            ("Effect", "Ordering only, no score change"),
        ],
    )

    # 14. Case enrichment
    s = new(prs)
    heading(
        s, "Module integration",
        "Every STS observation is enriched with the outputs of the other modules",
        "The review-case pipeline checks each STS observation against the other data pipelines for the same vessels and the same time window.",
    )
    node(s, 4.9, 3.15, 3.5, 1.35, "STS review case",
         "One case per STS observation, refreshed until an analyst closes it.",
         fill=DEEP, line=None, title_color=WHITE, sub_color=PALE, size=14)
    groups = [
        (0.48, "Unexplained signals", "At least one is required for notification", TEAL, [
            ("Sanctions match", "A member matches the OFAC list. Also the first sort key."),
            ("Outside anchorage", "Centre outside the original anchorage polygon."),
            ("Dark overlap", "A cargo or tanker member has an open transmission gap."),
            ("Illegal anchoring overlap", "A member passes the zone rule."),
        ]),
        (8.9, "Lawful explanations", "Either one removes the case from notification", GOLD, [
            ("Dredge licence", "In-force JKPTG licence covering the observation period."),
            ("Bunker vessel register", "Field in place; populated once the register is supplied."),
        ]),
    ]
    for x, title, sub, color, entries in groups:
        text(s, title.upper(), x, 1.65, 3.95, 0.24, 11, color, True)
        text(s, sub, x, 1.92, 3.95, 0.26, 11, MUTED)
        for i, (t, d) in enumerate(entries):
            node(s, x, 2.3 + i * 0.98, 3.95, 0.86, t, d, size=12)
    text(s, "CONTEXT RECORDED ON THE CASE", 8.9, 4.4, 3.95, 0.24, 11, TEAL_DARK, True)
    text(s, "Stored for the analyst; neither triggers notification", 8.9, 4.67, 3.95, 0.26, 11, MUTED)
    node(s, 8.9, 5.05, 3.95, 0.86, "Loitering or anchor swing", "Overlapping the observation window.", size=12)
    node(s, 8.9, 6.0, 3.95, 0.86, "Position anomaly", "A stored daily anomaly inside the observation window.", size=12)
    arrow(s, 4.45, 3.82, 4.87, 3.82)
    arrow(s, 8.87, 3.0, 8.43, 3.5, GOLD)
    arrow(s, 8.87, 5.48, 8.43, 4.2, TEAL_DARK)
    footer(s)

    # 15. Notification and review workflow
    s = new(prs)
    heading(
        s, "Notification and review",
        "From STS observation to verified classification",
        "Notification is delivered through the REST API today. The dashboard view of the review queue is the next integration step.",
    )
    flow = [
        ("STS observation", "Proximity pipeline, every 30 s"),
        ("Review case", "Enriched every 15 min"),
        ("Notification", "Unexplained cases only"),
        ("Analyst review", "Class, reviewer, time, notes"),
        ("Labelled dataset", "Input to the modelling phase"),
    ]
    for i, (t, sub) in enumerate(flow):
        x = 0.48 + i * 2.5
        fill, tc, sc = (DEEP, WHITE, PALE) if i in (2, 3) else (WHITE, NAVY, MUTED)
        node(s, x, 1.65, 2.2, 0.95, t, sub, fill=fill, line=None if fill == DEEP else LINE,
             title_color=tc, sub_color=sc, size=12.5)
        if i < 4:
            arrow(s, x + 2.22, 2.12, x + 2.48, 2.12)
    label(s, "Notification rule (all conditions apply)", 0.48, 2.95, 6.0)
    rule = [
        "Case status is unreviewed.",
        "Suspicion score is 4.5 or higher.",
        "No in-force dredge licence on the pair.",
        "No bunker register match.",
        "At least one unexplained signal: sanctions match, outside anchorage, dark overlap or illegal anchoring overlap.",
    ]
    items(s, rule, 0.48, 3.35, 6.0, 2.2, 12, numbered=True, spacing=4)
    text(s, "Sanctioned cases are listed first, then by suspicion score.", 0.48, 5.3, 6.0, 0.3, 12, MUTED)
    label(s, "Analyst decision", 6.95, 2.95, 5.9)
    decision = [
        ("Classes.", "Illicit transfer, bunkering, dredging, other lawful activity, or uncertain."),
        ("Recorded with the class.", "Reviewer, timestamp and notes. Once a case is reviewed, the pipeline no longer updates it."),
        ("Unreviewed report.", "Lists every open case with an optional date range, so investigations that take days or weeks can be found again and closed."),
    ]
    items(s, decision, 6.95, 3.35, 5.9, 2.5, 12, spacing=7)
    rect(s, 0.48, 6.0, 12.37, 0.92, SOFT, TEAL, radius=True)
    text(s, "API ENDPOINTS", 0.68, 6.1, 2.0, 0.24, 10, TEAL_DARK, True)
    text(s, "GET /mantis/sts-review      notification queue\nGET /mantis/sts-review/unreviewed?from=&to=      unreviewed report",
         0.68, 6.36, 6.0, 0.55, 11, INK)
    text(s, "POST /mantis/sts-review/{caseId}/verify      analyst decision",
         6.95, 6.36, 5.7, 0.3, 11, INK)
    footer(s)

    # 16. Roadmap
    s = new(prs)
    heading(
        s, "Roadmap",
        "From validated data to a machine-learning model",
        "A reliable model needs verified outcomes to learn from. The current phase is building that record.",
    )
    phases = [
        ("Phase 1", "Data collection and validation", "Current", [
            "All six modules and their data pipelines in production.",
            "Every STS observation converted into an enriched review case.",
            "Analysts classify notified cases and close older ones through the unreviewed report.",
        ]),
        ("Phase 2", "Labelled dataset", "Next", [
            "Dashboard view of the review queue.",
            "Bunker vessel register integrated as a lawful explanation, expected within three months.",
            "Export of reviewed cases with their features and the analyst class.",
        ]),
        ("Phase 3", "Model development", "After phase 2", [
            "Supervised model trained on analyst classes, never on the notification itself.",
            "Evaluated against reviewed cases held out from training.",
            "Model output added as a ranking signal; the analyst remains the decision-maker.",
        ]),
    ]
    for i, (tag, title, status, lines) in enumerate(phases):
        x = 0.48 + i * 4.18
        current = i == 0
        rect(s, x, 1.65, 3.98, 3.55, DEEP if current else WHITE, None if current else LINE, radius=True)
        text(s, tag.upper(), x + 0.25, 1.82, 1.8, 0.24, 11, TEAL if current else TEAL_DARK, True)
        text(s, status, x + 1.9, 1.82, 1.85, 0.24, 11, GOLD, True, PP_ALIGN.RIGHT)
        text(s, title, x + 0.25, 2.12, 3.5, 0.4, 17, WHITE if current else NAVY, True)
        items(s, lines, x + 0.25, 2.65, 3.5, 2.45, 12, PALE if current else BODY, spacing=7)
    label(s, "Features available to the model", 0.48, 5.42, 7.4)
    text(s, "Duration, distance, vessel count and vessel composition, kept as separate inputs. Vessel types, sanctions match, anchorage position, dark and illegal-anchoring overlap, loitering and position anomalies, dredge licence and bunker register match.",
         0.48, 5.8, 7.4, 1.1, 12, BODY)
    label(s, "Conditions to start phase 3", 8.2, 5.42, 4.65)
    items(s, [
        "All five classes represented in volume.",
        "A written classification guide applied consistently across reviewers.",
        "Dataset built from reviewed cases only.",
    ], 8.2, 5.8, 4.65, 1.1, 12, spacing=3)
    footer(s)

    # 17. Closing
    s = new(prs, paper=False)
    text(s, "SUMMARY", 0.7, 1.2, 11, 0.3, 13, TEAL, True)
    text(s, "Current phase:\ndata collection and validation", 0.68, 1.6, 11.5, 1.5, 36, WHITE, True)
    closing = [
        "MANTIS is in production across six analytics modules, with sanctions screening applied to every result.",
        "Each STS observation is enriched with the outputs of the other modules. Cases that remain unexplained are raised for analyst review, and the analyst's classification is stored with the case.",
        "Those classifications are the ground truth for the machine-learning phase. A model trained only on rule-based alerts would reproduce the existing rules, so model development begins once the reviewed dataset meets the phase 3 conditions.",
    ]
    items(s, closing, 0.7, 3.55, 11.4, 2.4, 16, PALE, spacing=10)
    rect(s, 0.7, 6.2, 1.4, 0.045, GOLD)
    footer(s, dark=True)

    prs.save(OUT)
    print(OUT, "slides", len(prs.slides))


if __name__ == "__main__":
    build()
