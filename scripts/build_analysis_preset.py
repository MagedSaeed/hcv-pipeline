#!/usr/bin/env python3
"""Build the analyses preset and its parity fixture from the research exports.

python3 scripts/build_analysis_preset.py <exports-dir>
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESET = ROOT / "resources/presets/recommended_v1.json"
FIXTURE = ROOT / "backend/analyses/tests/fixtures/parity.json"

VOCABULARY = "hcv-vocabulary-basics"
VOCABULARY_START = "fixed value.\n\n"
VOCABULARY_END = "\n\n## How to write a scenario"
SCENARIO_FIELDS = (
    "scenario_id",
    "actor",
    "trigger",
    "standing_state",
    "asset",
    "threat",
    "outcome",
    "narrative",
)


def only(values, what):
    """The single distinct value, or exit: the exports must agree."""
    distinct = {json.dumps(value, sort_keys=True) for value in values}
    if len(distinct) != 1:
        sys.exit(f"The exports disagree on the {what} ({len(distinct)} versions).")
    return json.loads(distinct.pop())


def prompt(export, name, version):
    found = [p for p in export["prompts"] if (p["name"], p["version"]) == (name, version)]
    if len(found) != 1:
        sys.exit(f"Expected one prompt {name} v{version}, found {len(found)}.")
    return {
        "prompt_name": name,
        "prompt_version": version,
        "system_prompt": found[0]["system_prompt"],
        "user_prompt": found[0]["user_prompt"],
        "output_schema": found[0]["output_schema"],
    }


def calls(export, phase):
    runs = {run["phase_run"]: run for run in export["runs"]}
    return [
        (runs[call["phase_run"]], call)
        for call in export["calls"]
        if runs[call["phase_run"]]["phase"] == phase
    ]


def vocabulary(rendered):
    start = rendered.index(VOCABULARY_START, rendered.index("## Vocabulary"))
    start += len(VOCABULARY_START)
    return rendered[start : rendered.index(VOCABULARY_END, start)]


def digests(call):
    return {
        side: hashlib.sha256(call[f"rendered_{side}_prompt"].encode()).hexdigest()
        for side in ("system", "user")
    }


def examples(export):
    return [
        {"name": shot["name"], "input": shot["input"], "output": shot["output"]}
        for shot in export["inputs"]["fewshots"]
        if shot["phase"] == 3 and shot["is_active"]
    ]


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


def main(directory):
    paths = sorted(Path(directory).glob("*-export.json"))
    exports = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in paths}
    zero_shot = [export for name, export in exports.items() if name.startswith("Zero-shot")]
    if not zero_shot:
        sys.exit(f"No Zero-shot exports in {directory}.")

    texts = [vocabulary(c["rendered_user_prompt"]) for e in zero_shot for _, c in calls(e, 2)]
    generation = only([prompt(e, "Zero-shot Variant B", 1) for e in zero_shot], "prompt")
    generation |= {
        "model": "anthropic/claude-sonnet-5",
        "temperature": 0.8,
        "count": 8,
        "examples": [],
        "resources": {VOCABULARY: only(texts, "vocabulary")},
    }
    everything = exports.values()
    labelling = only([prompt(e, "default-labelling", 1) for e in everything], "labelling prompt")
    labelling |= {
        "model": "openai/gpt-5.6-luna-pro",
        "temperature": 0.0,
        "examples": only([examples(e) for e in everything], "examples"),
        "resources": {},
    }
    write(PRESET, {"version": "1", "generation": generation, "labelling": labelling})

    generated = next(call for run, call in calls(zero_shot[0], 2) if run["variant"] == "B")
    cases = []
    for name, export in exports.items():
        _, call = calls(export, 3)[0]
        scenario = next(s for s in export["scenarios"] if s["id"] == call["scenario"])
        system = next(s for s in export["systems"] if s["id"] == scenario["system_description"])
        cases.append(
            {
                "export": name,
                "system": {"title": system["title"], "body": system["body"]},
                "scenario": {field: scenario[field] for field in SCENARIO_FIELDS},
                "expected": digests(call),
            }
        )
    write(
        FIXTURE,
        {
            "generation": {
                "n_systems": zero_shot[0]["inputs"]["n_systems"],
                "expected": digests(generated),
            },
            "labelling": cases,
        },
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
