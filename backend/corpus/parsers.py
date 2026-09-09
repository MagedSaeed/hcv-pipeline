"""Parse the expert seed pack out of the Markdown produced by extract_docx.py.

Each scenario is an `# sc_NN` heading, a headerless two-column table of the six
card fields, and a `## The scenario` section holding the narrative. Anything
before the first `# sc_` heading is the template preamble and is skipped.
"""

import re

CARD_FIELDS = {
    "actor": "actor",
    "trigger": "trigger",
    "standing state": "standing_state",
    "asset": "asset",
    "threat": "threat",
    "outcome": "outcome",
}

SCENARIO_HEADING = re.compile(r"^#\s+(sc_\w+)\s*$", re.MULTILINE)
NARRATIVE_HEADING = re.compile(r"^##\s+The scenario\s*$", re.MULTILINE | re.IGNORECASE)
TABLE_ROW = re.compile(r"^\|\s*(?P<label>[^|]*?)\s*\|\s*(?P<value>.*?)\s*\|\s*$")
SEPARATOR_ROW = re.compile(r"^\|[\s:|-]+\|$")


def _parse_card(block):
    card = dict.fromkeys(CARD_FIELDS.values(), "")
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or SEPARATOR_ROW.match(line):
            continue
        match = TABLE_ROW.match(line)
        if not match:
            continue
        field = CARD_FIELDS.get(match.group("label").strip().lower())
        if field:
            card[field] = match.group("value").replace("\\|", "|").strip()
    return card


def _split_body(block):
    """Return (card_block, narrative) for one scenario's body."""
    match = NARRATIVE_HEADING.search(block)
    if not match:
        return block, ""
    return block[: match.start()], block[match.end() :].strip()


def parse_scenarios(markdown):
    """Return one dict per scenario, in document order."""
    if not markdown:
        return []

    matches = list(SCENARIO_HEADING.finditer(markdown))
    scenarios = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        card_block, narrative = _split_body(markdown[match.end() : end])
        scenarios.append(
            {"scenario_id": match.group(1), **_parse_card(card_block), "narrative": narrative}
        )
    return scenarios
