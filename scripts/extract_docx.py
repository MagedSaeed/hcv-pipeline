#!/usr/bin/env python3
"""Extract a .docx to Markdown (default) or semantic HTML.

Markdown is the default because nothing in phases 1-3 reads term kinds, and the
seed scenarios are used as Phase 2 few-shots where HTML roughly doubles the
token cost. The .docx files stay in the repository, so re-extracting in another
format is always available.

Word tables become tables in both formats. A table's first row is treated as a
header only when every cell is at most HEADER_CELL_MAX characters — true of the
vocabulary tables ("Class | What it means"), false of the scenario cards, whose
first row is the Actor field rather than a header. A headerless two-column table
is a field/value list, so its first column is rendered as a row label.

    --format html

additionally recovers the colour coding the source documents use for
vocabulary term kind, emitting it as semantics rather than presentation:

    <span class="term-class">OperatorWorkstation</span>      blue bold
    <span class="term-property">interfaceOf</span>           green italic
    <span class="term-value">Config_ShippedDefault</span>    orange bold
    <span class="term-undefined">process data</span>         purple italic
    <span class="term-individual">W1</span>                  bold, no colour

That mapping is needed for ABox encoding, which is out of scope for now. Note
that individual detection is approximate: Word styles headings, column headers
and card labels the same way, so those are suppressed structurally, and bold
list markers can still be absorbed into a span.

Output is a fragment in both formats — meant to be embedded in prompts.

Usage: python3 scripts/extract_docx.py [--format md|html] FILE.docx [...]
Writes FILE.md (or FILE.html) next to each input.
"""

import argparse
import re
import sys
import zipfile
from html import escape
from pathlib import Path
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A first row whose cells are all at most this long is taken to be a header.
HEADER_CELL_MAX = 40

# Colour + emphasis -> semantic term kind, per the legend in the system
# description. Keys are (colour hex uppercased, bold, italic).
TERM_KINDS = {
    ("1F4E79", True, False): "term-class",
    ("1F6B3A", False, True): "term-property",
    ("9A4B00", True, False): "term-value",
    ("6B2D8E", False, True): "term-undefined",
}

# An individual is bold with no colour of its own. Word uses that same styling
# for headings, column headers and card field labels, so those are suppressed
# structurally by the caller. Near-black bold is a heading colour, not a term.
INDIVIDUAL_COLOURS = {"", "AUTO"}


def _run_style(run):
    """Return (colour, bold, italic) for a run."""
    props = run.find(f"{W}rPr")
    if props is None:
        return "", False, False
    colour = props.find(f"{W}color")
    return (
        (colour.get(f"{W}val", "") if colour is not None else "").upper(),
        props.find(f"{W}b") is not None,
        props.find(f"{W}i") is not None,
    )


def _run_text(run):
    parts = []
    for el in run.iter():
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        elif el.tag == f"{W}tab":
            parts.append(" ")
        elif el.tag in (f"{W}br", f"{W}cr"):
            parts.append(" ")
    return "".join(parts)


def _plain(paragraph):
    text = "".join(_run_text(r) for r in paragraph.iter(f"{W}r"))
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _annotated(paragraph, label=False):
    """Render runs as HTML, merging adjacent runs of the same term kind.

    Word splits a styled term across several runs (spell-check state, revision
    marks), so 'OperatorWorkstation' can arrive as three runs. Merging first
    means one span per term rather than one per fragment.

    `label` marks headings, column headers and row labels, which are bold with
    no colour and so indistinguishable from an individual by styling alone.
    """
    merged = []
    for run in paragraph.iter(f"{W}r"):
        text = _run_text(run)
        if not text:
            continue
        colour, bold, italic = _run_style(run)
        kind = TERM_KINDS.get((colour, bold, italic))
        if kind is None and bold and not label and colour in INDIVIDUAL_COLOURS:
            kind = "term-individual"
        if merged and merged[-1][0] == kind:
            merged[-1][1] += text
        else:
            merged.append([kind, text])

    out = []
    for kind, text in merged:
        body = text.strip()
        if kind is None or not body:
            out.append(escape(text))
            continue
        # Keep surrounding whitespace outside the span.
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        out.append(f'{lead}<span class="{kind}">{escape(body)}</span>{trail}')
    return re.sub(r"[ \t]{2,}", " ", "".join(out)).strip()


def _all_bold(paragraph):
    """True if every run carrying text is bold — a title with no heading style."""
    runs = [r for r in paragraph.iter(f"{W}r") if _run_text(r).strip()]
    return bool(runs) and all(_run_style(r)[1] for r in runs)


def _heading_level(paragraph):
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    if style is None:
        return 0
    match = re.fullmatch(r"[Hh]eading ?([1-6])", style.get(f"{W}val", ""))
    return int(match.group(1)) if match else 0


def _table_shape(table):
    """Return (rows, has_header, row_labels) for a Word table."""
    rows = [r for r in table.findall(f"{W}tr") if r.findall(f"{W}tc")]
    if not rows:
        return [], False, False
    first = rows[0].findall(f"{W}tc")

    def length(cell):
        return len("".join(_run_text(r) for r in cell.iter(f"{W}r")).strip())

    has_header = all(length(c) <= HEADER_CELL_MAX for c in first)
    row_labels = not has_header and all(len(r.findall(f"{W}tc")) == 2 for r in rows)
    return rows, has_header, row_labels


class MarkdownFormatter:
    suffix = ".md"

    def heading(self, level, paragraph):
        return [f"{'#' * level} {_plain(paragraph)}", ""]

    def paragraph(self, paragraph):
        return [_plain(paragraph), ""]

    def _cell(self, cell):
        parts = [_plain(p) for p in cell.findall(f"{W}p")]
        text = "<br>".join(p for p in parts if p)
        return text.replace("|", "\\|")

    def table(self, table):
        rows, has_header, _ = _table_shape(table)
        if not rows:
            return []
        width = max(len(r.findall(f"{W}tc")) for r in rows)

        def render(row):
            cells = [self._cell(c) for c in row.findall(f"{W}tc")]
            cells += [""] * (width - len(cells))
            return "| " + " | ".join(cells) + " |"

        # GFM requires a header row; an empty one renders as a headerless table.
        header = render(rows[0]) if has_header else "| " + " | ".join([""] * width) + " |"
        body = rows[1:] if has_header else rows
        return [header, "|" + "|".join([" --- "] * width) + "|"] + [
            render(r) for r in body
        ] + [""]

    def finish(self, lines):
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


class HtmlFormatter:
    suffix = ".html"

    def heading(self, level, paragraph):
        return [f"<h{level}>{_annotated(paragraph, label=True)}</h{level}>"]

    def paragraph(self, paragraph):
        text = _annotated(paragraph, label=_all_bold(paragraph))
        return [f"<p>{text}</p>"] if text else []

    def _cell(self, cell, label):
        parts = [_annotated(p, label) for p in cell.findall(f"{W}p")]
        return "<br>".join(p for p in parts if p)

    def table(self, table):
        rows, has_header, row_labels = _table_shape(table)
        if not rows:
            return []
        lines = ["<table>"]
        if has_header:
            lines.append("<thead><tr>")
            lines += [
                f"<th>{self._cell(c, True)}</th>"
                for c in rows[0].findall(f"{W}tc")
            ]
            lines.append("</tr></thead>")
            rows = rows[1:]
        lines.append("<tbody>")
        for row in rows:
            lines.append("<tr>")
            for index, cell in enumerate(row.findall(f"{W}tc")):
                if row_labels and index == 0:
                    lines.append(f'<th scope="row">{self._cell(cell, True)}</th>')
                else:
                    lines.append(f"<td>{self._cell(cell, False)}</td>")
            lines.append("</tr>")
        lines += ["</tbody>", "</table>"]
        return lines

    def finish(self, lines):
        return "\n".join(lines) + "\n"


FORMATTERS = {"md": MarkdownFormatter, "html": HtmlFormatter}


def extract(path: Path, formatter) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))

    lines = []
    for child in root.find(f"{W}body"):
        if child.tag == f"{W}p":
            if not _plain(child):
                lines.append("")
                continue
            level = _heading_level(child)
            lines += (
                formatter.heading(level, child)
                if level
                else formatter.paragraph(child)
            )
        elif child.tag == f"{W}tbl":
            lines += formatter.table(child)
    return formatter.finish(lines)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--format", choices=sorted(FORMATTERS), default="md")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    formatter = FORMATTERS[args.format]()
    for src in args.files:
        dst = src.with_suffix(formatter.suffix)
        dst.write_text(extract(src, formatter), encoding="utf-8")
        print(f"{src} -> {dst} ({dst.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
