"""What an analysis's rows mean: locked, which step, and which labels are current."""

import hashlib
import json

from analyses import extraction, preset
from analyses.models import AnalysisRun

EXTRACTION = "extraction"
GENERATION = "generation"
LABELLING = "labelling"


def default_settings(kind):
    if kind == EXTRACTION:
        extractor = preset.load_extractor()
        return {"model": extractor.model, "temperature": extractor.temperature}
    step = getattr(preset.load(), kind)
    defaults = {
        "model": step.model,
        "temperature": step.temperature,
        "prompt": "preset",
        "examples": [f"preset:{index}" for index in range(len(step.examples))],
    }
    if kind == GENERATION:
        defaults["count"] = step.count
    return defaults


def active_run(analysis):
    return next((run for run in analysis.runs.all() if run.is_active), None)


def _is_generating(analysis):
    run = active_run(analysis)
    return run is not None and run.kind == AnalysisRun.Kind.GENERATE


def is_locked(analysis):
    """Locked while generating and for as long as generated scenarios exist."""
    return _is_generating(analysis) or any(not s.is_manual for s in analysis.scenarios.all())


def description_digest(analysis):
    return hashlib.sha256(analysis.description.encode()).hexdigest()


def extraction_status(analysis):
    """none, current, or stale when the description changed after extraction."""
    if not analysis.extraction_digest:
        return "none"
    return "current" if analysis.extraction_digest == description_digest(analysis) else "stale"


def pending_items(analysis):
    return sum(1 for item in analysis.items.all() if item.decision == "pending")


def item_dicts(analysis):
    fields = ("kind", "section", "field", "ref", "value", "state", "problem", "decision")
    return [{name: getattr(item, name) for name in fields} for item in analysis.items.all()]


def system_body(analysis):
    """What the generator and labeller read: the approved values, then the analyst's text."""
    return extraction.structured_body(
        item_dicts(analysis), analysis.extraction_gaps or {}, analysis.description
    )


def label_key(analysis, scenario):
    """A digest of everything that decides a scenario's labels."""
    sent = {
        "body": hashlib.sha256(system_body(analysis).encode()).hexdigest(),
        "settings": analysis.labelling_settings,
        "preset": analysis.preset_version,
        "taxonomy": analysis.taxonomy_id,
        "scenario_id": scenario.scenario_id,
        "card": scenario.card,
        "narrative": scenario.narrative,
    }
    return hashlib.sha256(json.dumps(sent, sort_keys=True).encode()).hexdigest()


def is_current(analysis, scenario):
    return bool(scenario.label_key) and scenario.label_key == label_key(analysis, scenario)


def label_status(analysis, scenario):
    if is_current(analysis, scenario):
        return "current"
    latest = max(scenario.calls.all(), key=lambda call: call.pk, default=None)
    if latest is not None and latest.error:
        return "failed"
    return "stale" if scenario.label_key else "unlabelled"


def step_states(analysis):
    scenarios = list(analysis.scenarios.all())
    included = [scenario for scenario in scenarios if not scenario.set_aside]
    generating = _is_generating(analysis)
    labelled = bool(included) and not generating and all(is_current(analysis, s) for s in included)
    locked = is_locked(analysis)
    extracted = extraction_status(analysis) == "current" and not pending_items(analysis)
    return {
        "system": locked or extraction_status(analysis) != "none",
        "extract": locked or extracted,
        "generate": bool(scenarios) and not generating,
        "label": labelled,
        "report": labelled,
    }


def resume_step(analysis):
    """The step where work stopped."""
    if run := active_run(analysis):
        return run.kind
    steps = step_states(analysis)
    if not steps["system"]:
        return "system"
    if not steps["extract"]:
        return "extract"
    if steps["report"]:
        return "report"
    if any(scenario.label_key for scenario in analysis.scenarios.all()):
        return "label"
    return "generate"
