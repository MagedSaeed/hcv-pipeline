#!/usr/bin/env python3
"""Extract a .docx to Markdown, rendering Word tables as GFM tables.

The pipeline feeds this text straight into LLM prompts, so structure has to
survive the conversion: Word XML is not a usable source of truth, and flat text
loses the table shape that the vocabulary and scenario cards depend on.

Heading styles become ATX headings. Tables become GFM tables. A table's first
row is treated as a header only when every one of its cells is short (see
HEADER_CELL_MAX) — which is true of the vocabulary tables ("Class | What it
means") and false of the scenario cards, whose first row is the Actor field
rather than a header. Headerless tables get an empty header row so they still
render.

Usage: python3 scripts/extract_docx.py FILE.docx [FILE.docx ...]
Writes FILE.md next to each input.
"""

import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A first row whose cells are all at most this long is taken to be a header.
HEADER_CELL_MAX = 40


def _text_of(node):
    """Concatenate the runs under a node, honouring explicit breaks and tabs."""
    parts = []
    for el in node.iter():
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        elif el.tag == f"{W}tab":
            parts.append(" ")
        elif el.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _heading_level(paragraph):
    """Return the ATX level for a Word heading style, or 0 for body text."""
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    if style is None:
        return 0
    name = style.get(f"{W}val", "")
    match = re.fullmatch(r"[Hh]eading ?([1-6])", name)
    return int(match.group(1)) if match else 0


def _cell(text):
    """Escape a cell so newlines and pipes cannot break the table."""
    return text.replace("|", "\\|").replace("\n", "<br>")


def _render_table(table):
    rows = []
    for row in table.findall(f"{W}tr"):
        cells = [_text_of(c) for c in row.findall(f"{W}tc")]
        if cells:
            rows.append(cells)
    if not rows:
        return []

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    has_header = all(len(c) <= HEADER_CELL_MAX for c in rows[0])
    header = rows[0] if has_header else [""] * width
    body = rows[1:] if has_header else rows

    lines = [
        "| " + " | ".join(_cell(c) for c in header) + " |",
        "|" + "|".join([" --- "] * width) + "|",
    ]
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in body]
    return lines


def extract(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))

    lines = []
    for child in root.find(f"{W}body"):
        if child.tag == f"{W}p":
            text = _text_of(child)
            if not text:
                lines.append("")
                continue
            level = _heading_level(child)
            lines.append(f"{'#' * level} {text}" if level else text)
            lines.append("")
        elif child.tag == f"{W}tbl":
            lines.extend(_render_table(child))
            lines.append("")

    # Collapse runs of blank lines left behind by Word's empty paragraphs.
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return out.strip() + "\n"


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    for name in argv:
        src = Path(name)
        dst = src.with_suffix(".md")
        dst.write_text(extract(src), encoding="utf-8")
        print(f"{src} -> {dst} ({dst.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
