"""The report as a designed PDF: a cover, an overview, then each vulnerability and scenario."""

import io
import logging
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

import arabic_reshaper
from bidi import get_display
from django.core.cache import cache
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from analyses.report import DISCLAIMER
from llm.models_api import MODEL_CACHE_KEY

logger = logging.getLogger(__name__)

# DejaVu Sans has Arabic letters; reportlab's built-in fonts don't.
FONT_DIRS = ("/usr/share/fonts/truetype/dejavu",)
_ARABIC = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

# One muted accent; everything else is ink and greys.
ACCENT = colors.HexColor("#1e3a5f")
INK = colors.HexColor("#111827")
BODY = colors.HexColor("#1f2937")
MUTED = colors.HexColor("#6b7280")
HAIRLINE = colors.HexColor("#d1d5db")

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 1.8 * cm
WIDTH = PAGE_WIDTH - 2 * MARGIN - 12


@lru_cache(maxsize=1)
def _fonts():
    for folder in FONT_DIRS:
        regular, bold = Path(folder, "DejaVuSans.ttf"), Path(folder, "DejaVuSans-Bold.ttf")
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("Report", str(regular)))
            pdfmetrics.registerFont(TTFont("Report-Bold", str(bold)))
            # Lets <b> inside a paragraph switch to the bold face.
            pdfmetrics.registerFontFamily(
                "Report",
                normal="Report",
                bold="Report-Bold",
                italic="Report",
                boldItalic="Report-Bold",
            )
            return "Report", "Report-Bold"
    logger.warning("DejaVu Sans not found: Arabic in PDF reports will not show.")
    return "Helvetica", "Helvetica-Bold"


@lru_cache(maxsize=1)
def _styles():
    regular, bold = _fonts()

    def style(name, **fields):
        base = {"fontName": regular, "fontSize": 9.5, "leading": 14, "textColor": BODY}
        return ParagraphStyle(name, **{**base, **fields})

    return {
        "kicker": style("kicker", fontSize=8, leading=11, textColor=MUTED),
        "title": style(
            "title", fontName=bold, fontSize=20, leading=25, textColor=INK, spaceAfter=4
        ),
        "meta": style("meta", fontSize=9, textColor=MUTED),
        "section": style("section", fontName=bold, fontSize=13, leading=17, textColor=INK),
        "subsection": style("subsection", fontName=bold, fontSize=10.5, leading=14, textColor=INK),
        "label": style("label", fontSize=8, leading=12, textColor=MUTED),
        "body": style("body"),
        "small": style("small", fontSize=8.5, leading=12, textColor=MUTED),
        "note": style("note", fontSize=8.5, leading=12.5, textColor=MUTED, leftIndent=8),
        "quote": style("quote", fontSize=9, leading=13, textColor=BODY, leftIndent=8),
        "cell": style("cell", fontSize=8.5, leading=12),
        "cell_head": style("cell_head", fontName=bold, fontSize=8, leading=11, textColor=INK),
        "figure": style("figure", fontName=bold, fontSize=15, leading=19, textColor=ACCENT),
    }


def _text(value):
    """Escaped for a Paragraph, with Arabic shaped and put in reading order."""
    return escape(_shape(value)).replace("\n", "<br/>")


def _shape(value):
    value = str(value or "")
    if _ARABIC.search(value):
        value = "\n".join(get_display(arabic_reshaper.reshape(line)) for line in value.splitlines())
    return value


# Rows of a card can split between pages, but one row cannot, so long text becomes several rows.
_CHUNK = 900


def _pieces(text):
    pieces = []
    for line in str(text or "").splitlines() or [""]:
        while len(line) > _CHUNK:
            cut = line.rfind(" ", 0, _CHUNK)
            cut = cut if cut > 0 else _CHUNK
            pieces.append(line[:cut])
            line = line[cut:].lstrip()
        pieces.append(line)
    return pieces


def _paragraphs(text, style, prefix="", suffix=""):
    pieces = _pieces(text)
    last = len(pieces) - 1
    return [
        Paragraph((prefix if i == 0 else "") + _text(piece) + (suffix if i == last else ""), style)
        for i, piece in enumerate(pieces)
    ]


def _plural(count, word):
    return f"{count} {word}" + ("" if count == 1 else "s")


class Tags(Flowable):
    """Small outlined tags in one quiet style.

    Each group is (label, [texts]) and starts its own line, the label in small grey
    capitals; a tag wider than the line wraps its text inside the outline.
    """

    size, pad, line_height, gap = 7.5, 4.5, 9.5, 4

    def __init__(self, groups):
        super().__init__()
        self.groups = [
            (label, [_shape(text) for text in texts]) for label, texts in groups if texts
        ]

    def _measure(self, text):
        return pdfmetrics.stringWidth(text, _fonts()[0], self.size)

    def _split(self, text, width):
        """The text's lines when the tag may be at most `width` wide."""
        inner = width - 2 * self.pad
        if self._measure(text) <= inner:
            return [text]
        lines, line = [], ""
        for word in text.split():
            candidate = f"{line} {word}".strip()
            if line and self._measure(candidate) > inner:
                lines.append(line)
                line = word
            else:
                line = candidate
        return [*lines, line]

    def wrap(self, available_width, available_height):
        label_width = 42 if any(label for label, _ in self.groups) else 0
        self.rows = []  # (y_height, [(x, width, lines)]), label per row start
        for label, texts in self.groups:
            row, x = [], label_width
            for text in texts:
                lines = self._split(text, available_width - label_width)
                width = max(self._measure(line) for line in lines) + 2 * self.pad
                if row and x + width > available_width:
                    self.rows.append((label, row))
                    label, row, x = "", [], label_width
                row.append((x, width, lines))
                x += width + self.gap
            self.rows.append((label, row))
        self.heights = [
            max(len(lines) for _, _, lines in row) * self.line_height + 4 for _, row in self.rows
        ]
        self.width = available_width
        self.height = sum(self.heights) + self.gap * len(self.rows)
        return self.width, self.height

    def draw(self):
        regular = _fonts()[0]
        top = self.height
        for (label, row), height in zip(self.rows, self.heights, strict=True):
            bottom = top - height
            if label:
                self.canv.setFillColor(MUTED)
                self.canv.setFont(regular, 6.5)
                self.canv.drawString(0, top - 9.5, label)
            for x, width, lines in row:
                tag_height = len(lines) * self.line_height + 4
                self.canv.setStrokeColor(HAIRLINE)
                self.canv.setFillColor(colors.white)
                self.canv.setLineWidth(0.6)
                self.canv.roundRect(x, top - tag_height, width, tag_height, 3, stroke=1, fill=1)
                self.canv.setFillColor(BODY)
                self.canv.setFont(regular, self.size)
                for index, line in enumerate(lines):
                    self.canv.drawString(x + self.pad, top - 9.5 - index * self.line_height, line)
            top = bottom - self.gap


def _threat_groups(threats):
    return [
        (
            framework,
            [
                f"{t['name']} ({t['qualifier']})" if t["qualifier"] else t["name"]
                for t in threats
                if t["framework"] == framework
            ],
        )
        for framework in ("STRIDE", "LINDDUN")
    ]


def _threats(threats, s):
    if not threats:
        return Paragraph("No threat mapping defined for this vulnerability", s["small"])
    return Tags(_threat_groups(threats))


def _rule(width=0.4, colour=HAIRLINE, space=6):
    line = Table([[""]], colWidths=[WIDTH], rowHeights=[0])
    line.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), width, colour)]))
    return [Spacer(1, space), line, Spacer(1, space)]


def _section(number, title, s):
    return [
        CondPageBreak(8 * cm),
        Spacer(1, 14),
        Paragraph(f"<font color='#1e3a5f'>{number}</font>&nbsp;&nbsp;{title}", s["section"]),
        Spacer(1, 8),
    ]


def _key_values(rows, s, key_width=0.2):
    """Label / text pairs in two columns, with no boxes."""
    cells = []
    for key, value in rows:
        values = value if isinstance(value, list) else [value]
        for index, item in enumerate(values or [Spacer(1, 1)]):
            cells.append([Paragraph(key, s["label"]) if index == 0 else "", item])
    table = Table(cells, colWidths=[WIDTH * key_width, WIDTH * (1 - key_width)])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _booktabs(rows, widths, s):
    """A table ruled the academic way: thick top and bottom rules, a thin one under the header."""
    head, *body = rows
    table = Table(
        [[Paragraph(cell, s["cell_head"]) for cell in head], *body],
        colWidths=[WIDTH * w for w in widths],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 1, INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, INK),
                ("LINEBELOW", (0, -1), (-1, -1), 1, INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _front(report, scenario_count, s):
    name = report["name"] or "Untitled system"
    findings = report["findings"]
    count = len(findings)
    threats = {"STRIDE": set(), "LINDDUN": set()}
    for finding in findings:
        for threat in finding["threats"]:
            threats[threat["framework"]].add(threat["name"])
    figures = [
        (count, f"potential {'vulnerability' if count == 1 else 'vulnerabilities'}"),
        (scenario_count, "Scenarios analysed"),
        (len(threats["STRIDE"]), "Security threats (STRIDE)"),
        (len(threats["LINDDUN"]), "Privacy threats (LINDDUN)"),
    ]
    figure_row = Table(
        [
            [
                [Paragraph(str(value), s["figure"]), Paragraph(label, s["small"])]
                for value, label in figures
            ]
        ],
        colWidths=[WIDTH / 4] * 4,
    )
    figure_row.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 1, INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, HAIRLINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    story = [
        Paragraph("HUMAN-CENTRIC VULNERABILITY REPORT", s["kicker"]),
        Spacer(1, 4),
        Paragraph(_text(name), s["title"]),
        Paragraph(f"{date.today():%d %B %Y}", s["meta"]),
        Spacer(1, 16),
        figure_row,
        Spacer(1, 10),
        Paragraph(f"<i>Note.</i> {DISCLAIMER}", s["note"]),
    ]
    if note := incomplete_text(report["incomplete"]):
        story += [Spacer(1, 4), Paragraph(note, s["note"])]
    return story


def _summary(findings, s):
    rows = [["#", "Vulnerability", "Scenarios", "Enables"]]
    for number, finding in enumerate(findings, 1):
        rows.append(
            [
                Paragraph(f"2.{number}", s["cell"]),
                Paragraph(
                    f"{_text(finding['name'])} <font color='#6b7280'>({finding['hcv_id']})</font>",
                    s["cell"],
                ),
                Paragraph(str(finding["scenario_count"]), s["cell"]),
                _threats(finding["threats"], s),
            ]
        )
    return [*_section(1, "Summary", s), _booktabs(rows, [0.06, 0.36, 0.13, 0.45], s)]


def _finding(number, finding, s):
    manual = f"; {finding['manual_count']} added manually" if finding["manual_count"] else ""
    heading = Paragraph(
        f"<font color='#1e3a5f'>2.{number}</font>&nbsp;&nbsp;{_text(finding['name'])}",
        s["subsection"],
    )
    meta = (
        f"{finding['hcv_id']} · found in {_plural(finding['scenario_count'], 'scenario')}{manual}"
    )
    if finding["only_manual"]:
        meta += " · Only in manually added scenarios"
    rows = [("Enables", _threats(finding["threats"], s))]
    if finding["elements"]:
        rows.append(("At risk", Paragraph(_text("; ".join(finding["elements"])), s["body"])))
    why = []
    for occurrence in finding["occurrences"]:
        why.append(Paragraph(f"<i>{_text(occurrence['title'])}</i>", s["body"]))
        if occurrence["is_manual"]:
            why.append(Tags([("", ["Added manually"])]))
        if occurrence.get("outcome"):
            why.append(Paragraph(f"Outcome: {_text(occurrence['outcome'])}", s["small"]))
        why += _paragraphs(occurrence["rationale"], s["body"])
        why += _paragraphs(occurrence["evidence"], s["quote"], "From the scenario: “", "”")
        why.append(Spacer(1, 4))
    rows.append(("Why", why))
    return [
        CondPageBreak(4 * cm),
        KeepTogether([heading, Paragraph(meta, s["small"]), Spacer(1, 4)]),
        _key_values(rows, s),
        *_rule(),
    ]


_FIELDS = (
    ("actor", "Who is involved"),
    ("trigger", "Occasion"),
    ("standing_state", "What was already true"),
    ("asset", "What it reaches"),
    ("outcome", "Outcome"),
    ("narrative", "What happens"),
)


def _scenario(number, scenario, s):
    story = [
        CondPageBreak(4 * cm),
        Paragraph(
            f"<font color='#1e3a5f'>S{number}</font>&nbsp;&nbsp;{_text(scenario['title'])}",
            s["subsection"],
        ),
        Spacer(1, 4),
    ]
    if scenario["is_manual"]:
        story += [Tags([("", ["Added manually"])]), Spacer(1, 2)]
    rows = []
    if scenario["labels"]:
        found = []
        for label in scenario["labels"]:
            found.append(Paragraph(f"<b>{label['hcv_id']}</b> {_text(label['name'])}", s["body"]))
            found += _paragraphs(label["rationale"], s["body"])
            found += _paragraphs(label["evidence"], s["quote"], "From the scenario: “", "”")
            found.append(Spacer(1, 3))
        rows.append((f"Vulnerabilities found ({len(scenario['labels'])})", found))
    else:
        rows.append(("Vulnerabilities found", Paragraph("None in this scenario", s["small"])))
    for key, title in _FIELDS:
        if scenario.get(key):
            rows.append((title, _paragraphs(scenario[key], s["body"])))
    if scenario.get("threat"):
        title = (
            "Threat, as entered" if scenario["is_manual"] else "Threat suggested by the generator"
        )
        rows.append((title, Paragraph(_text(scenario["threat"]), s["body"])))
    story += [_key_values(rows, s, key_width=0.24), *_rule()]
    return story


def _ignores_temperature(model):
    models = {row.get("id"): row for row in cache.get(MODEL_CACHE_KEY) or []}
    supported = models.get(model, {}).get("supported_parameters")
    return isinstance(supported, list) and "temperature" not in supported


def _setup_text(setup):
    examples = setup["examples"]
    prompting = f"few-shot, {_plural(len(examples), 'worked example')}" if examples else "zero-shot"
    lines = [
        f"<b>{'Custom' if setup['is_custom'] else 'Recommended'}</b>",
        f"{_text(setup['model'])} · temperature {setup['temperature']}",
        f"{_text(setup['prompt'])} · {prompting}"
        + (f" · {setup['count']} scenarios asked for" if setup.get("count") else ""),
    ]
    if _ignores_temperature(setup["model"]):
        lines.append("This model may ignore temperature")
    if examples:
        lines.append(f"Examples: {_text(', '.join(examples))}")
    return "<br/>".join(lines)


def _details(report, s):
    details = report["details"]
    taxonomy = details["taxonomy"]
    rows = [
        ["Item", "Value"],
        [
            Paragraph("Taxonomy", s["cell"]),
            Paragraph(f"{taxonomy['vocabulary_id']} v{taxonomy['version']}", s["cell"]),
        ],
    ]
    if extracted := details.get("extraction"):
        rows.append(
            [
                Paragraph("Extraction", s["cell"]),
                Paragraph(
                    f"<b>{'Custom' if extracted['is_custom'] else 'Recommended'}</b><br/>"
                    f"{_text(extracted['model'])} · temperature {extracted['temperature']}<br/>"
                    f"{_text(extracted['prompt'])}<br/>"
                    f"{extracted['approved']} values approved ({extracted['edited']} edited, "
                    f"{extracted['added']} added), {extracted['rejected']} rejected",
                    s["cell"],
                ),
            ]
        )
    if details["generation"]:
        rows.append(
            [
                Paragraph("Generation", s["cell"]),
                Paragraph(_setup_text(details["generation"]), s["cell"]),
            ]
        )
    for setup in details["labelling"]:
        rows.append(
            [Paragraph("Identification", s["cell"]), Paragraph(_setup_text(setup), s["cell"])]
        )
    conditions = [
        f"{finding['hcv_id']}: {mapping['condition_class']}"
        + (f" (rule {mapping['rule']})" if mapping["rule"] else "")
        for finding in report["findings"]
        for mapping in finding["mappings"]
    ]
    if conditions:
        rows.append(
            [
                Paragraph("Condition classes", s["cell"]),
                Paragraph("<br/>".join(conditions), s["cell"]),
            ]
        )
    return [*_section(4, "Research details", s), _booktabs(rows, [0.22, 0.78], s)]


def incomplete_text(incomplete):
    total = sum(incomplete.values())
    if not total:
        return ""
    parts = [
        f"{incomplete[key]} {words}"
        for key, words in (
            ("unlabelled", "not checked yet"),
            ("failed", "failed"),
            ("stale", "out of date"),
        )
        if incomplete[key]
    ]
    subject = "1 scenario is" if total == 1 else f"{total} scenarios are"
    return f"{subject} not in this report: {', '.join(parts)}."


class _NumberedCanvas(pdf_canvas.Canvas):
    """Holds every page until the end, so each footer can say "Page X of Y"."""

    title = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for page in self._pages:
            self.__dict__.update(page)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total):
        regular = _fonts()[0]
        self.saveState()
        self.setStrokeColor(HAIRLINE)
        self.setLineWidth(0.5)
        self.line(MARGIN, 1.35 * cm, PAGE_WIDTH - MARGIN, 1.35 * cm)
        self.setFont(regular, 7.5)
        self.setFillColor(MUTED)
        self.drawString(MARGIN, 0.95 * cm, self.title)
        self.drawRightString(PAGE_WIDTH - MARGIN, 0.95 * cm, f"Page {self._pageNumber} of {total}")
        self.restoreState()


def render(report, order):
    """`order` maps scenario ids to the numbers they have on the other steps."""
    s = _styles()
    name = report["name"] or "Untitled system"
    story = _front(report, len(report["scenarios"]), s)

    if report["findings"]:
        story += _summary(report["findings"], s)
        story += _section(2, "Vulnerabilities", s)
        for number, finding in enumerate(report["findings"], 1):
            story += _finding(number, finding, s)
    else:
        story += [
            *_section(1, "Summary", s),
            Paragraph("No vulnerabilities in the scenarios checked so far.", s["body"]),
            *_section(2, "Vulnerabilities", s),
            Paragraph("None.", s["small"]),
        ]

    story += _section(3, "Scenarios", s)
    for scenario in report["scenarios"]:
        story += _scenario(order.get(scenario["id"], "–"), scenario, s)
    if report["set_aside"]:
        aside = [Paragraph("Set aside, not in this report", s["subsection"])]
        aside += [Paragraph(f"• {_text(item['title'])}", s["body"]) for item in report["set_aside"]]
        story.append(KeepTogether(aside))

    story += _details(report, s)

    canvas_class = type(
        "ReportCanvas", (_NumberedCanvas,), {"title": _shape(f"{name} · Vulnerability report")}
    )
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=1.6 * cm,
        bottomMargin=2 * cm,
        title=f"{name} report",
    )
    document.build(story, canvasmaker=canvas_class)
    return buffer.getvalue()


def content_disposition(name):
    """Inline, so the browser shows it; a plain ASCII name plus the full one for modern browsers."""
    readable = (
        " ".join(re.sub(r'["\\/:*?<>|\x00-\x1f]+', " ", name or "Analysis").split()) or "Analysis"
    )
    filename = f"{readable} report.pdf"
    ascii_name = filename.encode("ascii", "ignore").decode().strip()
    if ascii_name == filename:
        return f'inline; filename="{filename}"'
    fallback = ascii_name if ascii_name != "report.pdf" else "analysis report.pdf"
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
