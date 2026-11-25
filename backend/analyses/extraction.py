"""The extractor prompt's fields, read from the prompt itself, and the checks on its reply."""

import re
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings

from llm.schemas import loads_response

PROMPT_PATH = "presets/extractor_v1.txt"
PLACEHOLDER = "{{SYSTEM_DESCRIPTION}}"
SECTIONS = (
    "1. People and organizations",
    "2. System components",
    "3. Use and responsibility",
    "4. Data flows",
    "5. Sign-in and access",
    "6. Automated decisions",
    "7. Settings",
    "8. Controls",
)
CONTROL_SECTION = "8. Controls"
FACT_TYPES = ("Asserted", "Possible")
STATES = ("present", "absent")
_FACT_TYPE_RULES = {
    "Asserted": "asserted",
    "Asserted or Possible": "either",
    "Always Possible": "possible",
    "Asserted if named, otherwise Possible": "asserted_if_named",
}


@dataclass(frozen=True)
class FieldSpec:
    kind: str  # element or fact
    section: str
    allowed: tuple | None  # None when the value is an element, a number or a boolean
    value_kind: str  # choice, element, number or boolean
    fact_type: str  # asserted, either, possible or asserted_if_named


class ExtractError(ValueError):
    """The reply is not the shape the prompt asks for, so nothing can be kept."""


@lru_cache(maxsize=1)
def prompt_text():
    return (settings.RESOURCES_DIR / PROMPT_PATH).read_text(encoding="utf-8")


def _allowed(text):
    """The allowed values of a table cell, or how the value is given when it is not a list."""
    text = text.strip()
    if text == "a number":
        return None, "number"
    if text == "true, false":
        return None, "boolean"
    if not text[:1].isupper():
        return None, "element"
    plain = re.sub(r"\([^)]*\)", "", text)
    return tuple(value.strip() for value in plain.split(",") if value.strip()), "choice"


@lru_cache(maxsize=1)
def _vocabulary():
    text = prompt_text()
    fields = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) not in (5, 6) or cells[0] not in SECTIONS:
            continue
        kind = "element" if len(cells) == 5 else "fact"
        allowed, value_kind = _allowed(cells[3] if kind == "element" else cells[4])
        fields[cells[1]] = FieldSpec(
            kind, cells[0], allowed, value_kind, _FACT_TYPE_RULES[cells[-1]]
        )
    families = {
        name: tuple(member.strip() for member in members.split(","))
        for name, members in re.findall(r"^- (\w+Control): (.+)$", text, re.M)
    }
    listed = text.split("Allowed controls:", 1)[1].split("## D.", 1)[0]
    controls = tuple(re.findall(r"^(\w+): ", listed, re.M))
    return fields, families, controls


FIELDS, CONTROL_FAMILIES, CONTROLS = _vocabulary()


def _match(value, allowed):
    """The allowed spelling of `value`, ignoring case and surrounding space, or None."""
    wanted = str(value).strip().casefold()
    return next((option for option in allowed if option.casefold() == wanted), None)


def _text(entry, key):
    value = entry.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _base(entry, kind, section, field, ref):
    fact_type = _match(entry.get("fact_type", ""), FACT_TYPES) or "Asserted"
    spec = FIELDS.get(field)
    if spec and spec.fact_type == "possible":
        fact_type = "Possible"
    return {
        "kind": kind,
        "section": section,
        "field": field,
        "ref": ref,
        "value": "",
        "state": "",
        "fact_type": fact_type,
        "reason": _text(entry, "reason"),
        "other_reading": _text(entry, "other_reading"),
        "problem": "",
    }


def check_value(field, value, element_ids):
    """(normalised value, problem) for a fact or element value."""
    spec = FIELDS[field]
    if spec.value_kind == "number":
        if isinstance(value, bool) or not re.fullmatch(r"\d+", str(value).strip()):
            return str(value), "Must be a whole number of days."
        return str(int(str(value).strip())), ""
    if spec.value_kind == "boolean":
        word = str(value).strip().casefold()
        if word not in ("true", "false"):
            return str(value), "Must be true or false."
        return word, ""
    if spec.value_kind == "element":
        ref = str(value).strip()
        return ref, "" if ref in element_ids else f'"{ref}" names no element.'
    matched = _match(value, spec.allowed)
    return (
        (matched, "") if matched else (str(value).strip(), "Not an allowed value for this field.")
    )


def check_id(ref):
    """A problem with an element id's spelling, or ""."""
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", ref):
        return ""
    return "Use letters, digits and underscores, starting with a letter."


def check_control(name):
    matched = _match(name, CONTROLS + tuple(CONTROL_FAMILIES))
    return (matched, "") if matched else (str(name).strip(), "Not an allowed control.")


def parse_reply(text):
    """Items and gaps from the extractor's reply; raises ExtractError if its shape is wrong."""
    try:
        payload = loads_response(text)
    except ValueError as error:
        raise ExtractError("The reply is not JSON.") from error
    if not isinstance(payload, dict):
        raise ExtractError("The reply is not a JSON object.")
    for key in ("elements", "facts", "controls"):
        if not isinstance(payload.get(key), list):
            raise ExtractError(f'The reply has no "{key}" list.')
    gaps = payload.get("gaps")
    if not isinstance(gaps, dict):
        raise ExtractError('The reply has no "gaps" object.')

    items, element_ids = [], set()
    for entry in filter(lambda e: isinstance(e, dict), payload["elements"]):
        ref, field = _text(entry, "id"), _text(entry, "field")
        spec = FIELDS.get(field)
        item = _base(
            entry, "element", spec.section if spec else _text(entry, "section"), field, ref
        )
        item["value"] = str(entry.get("value", "")).strip()
        if not spec or spec.kind != "element":
            item["problem"] = "An unknown field for an element."
        elif not ref:
            item["problem"] = "This element has no id."
        elif ref in element_ids:
            item["problem"] = f'A duplicate id: "{ref}" is already used.'
        else:
            item["value"], item["problem"] = check_value(field, entry.get("value", ""), set())
        if ref and not item["problem"]:
            element_ids.add(ref)
        items.append(item)

    for entry in filter(lambda e: isinstance(e, dict), payload["facts"]):
        ref, field = _text(entry, "subject_id"), _text(entry, "field")
        spec = FIELDS.get(field)
        item = _base(entry, "fact", spec.section if spec else _text(entry, "section"), field, ref)
        item["value"] = str(entry.get("value", "")).strip()
        if not spec or spec.kind != "fact":
            item["problem"] = "An unknown field for a fact."
        elif ref not in element_ids:
            item["problem"] = f'"{ref}" names no element.'
        else:
            item["value"], item["problem"] = check_value(field, entry.get("value", ""), element_ids)
        items.append(item)

    for entry in filter(lambda e: isinstance(e, dict), payload["controls"]):
        ref = _text(entry, "element_id")
        item = _base(entry, "control", CONTROL_SECTION, "control", ref)
        item["value"], item["problem"] = check_control(entry.get("control", ""))
        state = _match(entry.get("state", ""), STATES)
        item["state"] = state or str(entry.get("state", "")).strip()
        if not item["problem"] and ref not in element_ids:
            item["problem"] = f'"{ref}" names no element.'
        if not item["problem"] and not state:
            item["problem"] = "The state must be present or absent."
        items.append(item)

    return items, {
        "not_assessable": [
            {"area": _text(g, "area"), "reason": _text(g, "reason")}
            for g in gaps.get("not_assessable") or []
            if isinstance(g, dict)
        ],
        "no_term": [
            {"statement": _text(g, "statement"), "reason": _text(g, "reason")}
            for g in gaps.get("no_term") or []
            if isinstance(g, dict)
        ],
    }


def kept(items):
    """Approved items without a problem, and whose elements are kept too."""
    elements = {
        item["ref"]
        for item in items
        if item["kind"] == "element" and item["decision"] == "approved" and not item["problem"]
    }

    def refs(item):
        names = [item["ref"]]
        if item["kind"] == "fact" and FIELDS[item["field"]].value_kind == "element":
            names.append(item["value"])
        return names

    return [
        item
        for item in items
        if item["decision"] == "approved"
        and not item["problem"]
        and all(name in elements for name in refs(item))
    ]


# Facts about a data flow, written on the flow's own line.
FLOW_PARTS = ("source", "destination", "data_category", "trust_boundary")


def _flow_line(flow, facts):
    """'- Id (DataFlow): Source → Destination; data: X; boundary: Y', from the flow's kept facts."""
    part = {item["field"]: item["value"] for item in facts if item["ref"] == flow["ref"]}
    pieces = []
    if "source" in part or "destination" in part:
        pieces.append(f"{part.get('source', '?')} → {part.get('destination', '?')}")
    if "data_category" in part:
        pieces.append(f"data: {part['data_category']}")
    if "trust_boundary" in part:
        pieces.append(f"boundary: {part['trust_boundary']}")
    line = f"- {flow['ref']} ({flow['value']})"
    return f"{line}: {'; '.join(pieces)}" if pieces else line


def structured_body(items, gaps, description):
    """What the generator and labeller read: the checked values, then the analyst's text."""
    chosen = kept(items)
    elements = [item for item in chosen if item["kind"] == "element"]
    flows = {item["ref"] for item in elements if item["field"] == "data_flow"}
    facts = [item for item in chosen if item["kind"] == "fact"]
    flow_facts = [item for item in facts if item["ref"] in flows and item["field"] in FLOW_PARTS]
    other_facts = [item for item in facts if item not in flow_facts]

    parts = ["## Structured description"]
    for section in SECTIONS:
        lines = [
            _flow_line(item, flow_facts)
            if item["ref"] in flows
            else f"- {item['ref']} ({item['value']})"
            for item in elements
            if item["section"] == section
        ]
        if lines:
            parts.append(f"### {section}\n" + "\n".join(lines))
    if other_facts:
        lines = [f"- {item['ref']} {item['field']} {item['value']}" for item in other_facts]
        parts.append("### Facts\n" + "\n".join(lines))
    controls = [
        f"- {item['ref']}: {item['value']} {item['state']}"
        for item in chosen
        if item["kind"] == "control"
    ]
    if controls:
        parts.append(f"### {CONTROL_SECTION}\n" + "\n".join(controls))
    missing = [f"- {g['area']}" for g in gaps.get("not_assessable", []) if g["area"]]
    missing += [
        f"- {g['statement']} (no matching term)" for g in gaps.get("no_term", []) if g["statement"]
    ]
    if missing:
        parts.append("### Not covered by the description\n" + "\n".join(missing))
    parts.append(f"## Original description\n\n{description}")
    return "\n\n".join(parts) + "\n"


def request_text(description):
    return prompt_text().replace(PLACEHOLDER, description)


def reply_schema():
    """The reply's structure only, without allowed values, so odd values reach review."""
    text = {"type": "string"}
    entry = lambda *keys: {  # noqa: E731
        "type": "object",
        "properties": dict.fromkeys(keys, text),
        "required": list(keys),
    }
    common = ("section", "field", "fact_type", "reason", "other_reading")
    return {
        "type": "object",
        "properties": {
            "elements": {"type": "array", "items": entry("id", "value", *common)},
            "facts": {"type": "array", "items": entry("subject_id", "value", *common)},
            "controls": {
                "type": "array",
                "items": entry(
                    "element_id",
                    "control",
                    "state",
                    "section",
                    "fact_type",
                    "reason",
                    "other_reading",
                ),
            },
            "gaps": {
                "type": "object",
                "properties": {
                    "not_assessable": {"type": "array", "items": entry("area", "reason")},
                    "no_term": {"type": "array", "items": entry("statement", "reason")},
                },
                "required": ["not_assessable", "no_term"],
            },
        },
        "required": ["elements", "facts", "controls", "gaps"],
    }


def known_elements(items):
    """Element ids that are usable: a known element field, an id, and its first use."""
    seen = set()
    for item in items:
        spec = FIELDS.get(item["field"])
        if item["kind"] == "element" and spec and spec.kind == "element" and item["ref"]:
            seen.add(item["ref"])
    return seen


def recheck(items):
    """The problem of each item, worked out again from the current values."""
    element_ids, first = known_elements(items), {}
    problems = []
    for index, item in enumerate(items):
        spec = FIELDS.get(item["field"])
        if item["kind"] == "element":
            if not spec or spec.kind != "element":
                problems.append("An unknown field for an element.")
            elif not item["ref"]:
                problems.append("This element has no id.")
            elif first.setdefault(item["ref"], index) != index:
                problems.append(f'A duplicate id: "{item["ref"]}" is already used.')
            else:
                problems.append(check_value(item["field"], item["value"], set())[1])
        elif item["kind"] == "fact":
            if not spec or spec.kind != "fact":
                problems.append("An unknown field for a fact.")
            elif item["ref"] not in element_ids:
                problems.append(f'"{item["ref"]}" names no element.')
            else:
                problems.append(check_value(item["field"], item["value"], element_ids)[1])
        else:
            problem = check_control(item["value"])[1]
            if not problem and item["ref"] not in element_ids:
                problem = f'"{item["ref"]}" names no element.'
            if not problem and item["state"] not in STATES:
                problem = "The state must be present or absent."
            problems.append(problem)
    return problems


def field_options():
    """What the review screen may offer for each field."""
    return {
        name: {
            "kind": spec.kind,
            "section": spec.section,
            "value_kind": spec.value_kind,
            "allowed": list(spec.allowed or []),
        }
        for name, spec in FIELDS.items()
    }
