"""The STRIDE and LINDDUN category names a threat mapping may use."""

STRIDE = (
    "Spoofing",
    "Tampering",
    "Repudiation",
    "Information disclosure",
    "Denial of service",
    "Elevation of privilege",
)
LINDDUN = (
    "Linking",
    "Identifying",
    "Non-repudiation",
    "Detecting",
    "Data disclosure",
    "Unawareness",
    "Non-compliance",
)
_TEXT_FIELDS = ("condition_class", "element", "rule", "qualifier")
_FRAMEWORKS = {"stride": STRIDE, "linddun": LINDDUN}


def validate_mappings(value):
    """The mappings with every key present, or a ValueError naming the bad row."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Threat mappings must be a list.")
    cleaned = []
    for number, row in enumerate(value, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Mapping {number} must be an object.")
        if extra := set(row) - {*_TEXT_FIELDS, *_FRAMEWORKS}:
            raise ValueError(f"Mapping {number} has unknown fields: {sorted(extra)}.")
        mapping = {}
        for field in _TEXT_FIELDS:
            text = row.get(field, "")
            if not isinstance(text, str):
                raise ValueError(f"Mapping {number}: {field} must be text.")
            mapping[field] = text.strip()
        if not mapping["condition_class"]:
            raise ValueError(f"Mapping {number} needs a condition class.")
        for field, allowed in _FRAMEWORKS.items():
            names = row.get(field, [])
            if not isinstance(names, list):
                raise ValueError(f"Mapping {number}: {field} must be a list.")
            if unknown := [name for name in names if name not in allowed]:
                raise ValueError(f"Mapping {number}: unknown {field} categories {unknown}.")
            mapping[field] = names
        cleaned.append(mapping)
    return cleaned
