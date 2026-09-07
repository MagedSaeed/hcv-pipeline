#!/usr/bin/env python3
"""Extract a .docx to plain text, preserving table rows as tab-separated cells.

The pipeline ingests text, not Word XML, so every .docx under resources/ is kept
alongside a .txt extraction that is the actual source of truth for seeding.

Usage: python3 scripts/extract_docx.py FILE.docx [FILE.docx ...]
Writes FILE.txt next to each input.
"""

import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _text_of(node):
    """Concatenate the runs under a node, honouring explicit breaks and tabs."""
    parts = []
    for el in node.iter():
        if el.tag == f"{W}t":
            parts.append(el.text or "")
        elif el.tag == f"{W}tab":
            parts.append("\t")
        elif el.tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def extract(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))

    body = root.find(f"{W}body")
    lines = []
    for child in body:
        if child.tag == f"{W}p":
            lines.append(_text_of(child))
        elif child.tag == f"{W}tbl":
            for row in child.findall(f"{W}tr"):
                cells = [_text_of(c) for c in row.findall(f"{W}tc")]
                lines.append("\t".join(cells))
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    for name in argv:
        src = Path(name)
        dst = src.with_suffix(".txt")
        dst.write_text(extract(src), encoding="utf-8")
        print(f"{src} -> {dst} ({dst.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
