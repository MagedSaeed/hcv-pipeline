"""The report: current labels, joined to the taxonomy's fixed threat table."""

import json
from collections import Counter

from django.db.models import Prefetch, Sum

from analyses import state
from analyses.models import AnalysisCall
from analyses.serializers import scenario_json, scenario_title, taxonomy_json

_FRAMEWORKS = (("stride", "STRIDE"), ("linddun", "LINDDUN"))
DISCLAIMER = (
    "These are potential vulnerabilities found in generated or analyst-written scenarios. "
    "Check them against the real system. A vulnerability that is not listed may still be present."
)


def threats(mappings):
    """Threats in every row are plain; one in only some carries that row's qualifier."""
    plain, qualified, seen = [], [], set()
    for key, framework in _FRAMEWORKS:
        for mapping in mappings:
            for name in mapping[key]:
                if (framework, name) in seen:
                    continue
                seen.add((framework, name))
                rows = [row for row in mappings if name in row[key]]
                threat = {"name": name, "framework": framework, "qualifier": ""}
                if len(rows) == len(mappings):
                    plain.append(threat)
                else:
                    conditions = (row.get("qualifier") or row["condition_class"] for row in rows)
                    threat["qualifier"] = "; ".join(dict.fromkeys(conditions))
                    qualified.append(threat)
    return plain + qualified


def _findings(current):
    grouped = {}
    for scenario in current:
        for label in scenario.labels.all():
            grouped.setdefault(label.hcv, []).append((scenario, label))
    findings = []
    for hcv, found in grouped.items():
        mappings = hcv.threat_mappings or []
        manual = sum(1 for scenario, _ in found if scenario.is_manual)
        findings.append(
            {
                "hcv_id": hcv.hcv_id,
                "name": hcv.name,
                "scenario_count": len(found),
                "manual_count": manual,
                "only_manual": manual == len(found),
                "threats": threats(mappings),
                "elements": list(
                    dict.fromkeys(row["element"] for row in mappings if row.get("element"))
                ),
                "mappings": mappings,
                "occurrences": [
                    {
                        "scenario": scenario.pk,
                        "scenario_id": scenario.scenario_id,
                        "title": scenario_title(scenario),
                        "outcome": scenario.outcome,
                        "is_manual": scenario.is_manual,
                        "evidence": label.evidence,
                        "rationale": label.rationale,
                    }
                    for scenario, label in found
                ],
            }
        )
    return sorted(findings, key=lambda row: (-row["scenario_count"], row["hcv_id"]))


def _setup(pinned):
    return {
        "model": pinned.get("model", ""),
        "temperature": pinned.get("temperature"),
        "prompt": pinned.get("prompt_label", ""),
        "examples": [example["name"] for example in pinned.get("examples", [])],
        "count": pinned.get("count"),
        "is_custom": pinned.get("is_custom", False),
        "preset_version": pinned.get("preset_version", ""),
    }


def _extraction(analysis):
    run = analysis.runs.filter(kind="extract", status="completed").order_by("-pk").first()
    if run is None:
        return None
    items = list(analysis.items.all())
    approved = [item for item in items if item.decision == "approved"]
    return {
        "model": run.pinned.get("model", ""),
        "temperature": run.pinned.get("temperature"),
        "prompt": run.pinned.get("prompt_label", ""),
        "is_custom": run.pinned.get("is_custom", False),
        "approved": len(approved),
        "edited": sum(1 for item in approved if item.edited),
        "added": sum(1 for item in approved if item.added),
        "rejected": sum(1 for item in items if item.decision == "rejected"),
    }


def build(analysis):
    scenarios = list(
        analysis.scenarios.select_related("generated_by").prefetch_related(
            "labels__hcv",
            Prefetch("calls", AnalysisCall.objects.only("id", "scenario_id", "error")),
            Prefetch(
                "labelled_call",
                AnalysisCall.objects.only("id", "run").prefetch_related("run"),
            ),
        )
    )
    included = [scenario for scenario in scenarios if not scenario.set_aside]
    current = [scenario for scenario in included if state.is_current(analysis, scenario)]
    statuses = Counter(state.label_status(analysis, scenario) for scenario in included)

    labelling = {}
    for scenario in current:
        if scenario.labelled_call:
            setup = _setup(scenario.labelled_call.run.pinned)
            labelling.setdefault(json.dumps(setup, sort_keys=True), setup)
    generated_by = next((s.generated_by for s in scenarios if s.generated_by), None)
    cost = AnalysisCall.objects.filter(run__analysis=analysis).aggregate(total=Sum("cost"))

    return {
        "name": analysis.name,
        "findings": _findings(current),
        "scenarios": [scenario_json(analysis, s, current_labels_only=True) for s in included],
        "set_aside": [
            scenario_json(analysis, s, current_labels_only=True) for s in scenarios if s.set_aside
        ],
        "incomplete": {name: statuses[name] for name in ("unlabelled", "failed", "stale")},
        "details": {
            "taxonomy": taxonomy_json(analysis.taxonomy),
            "extraction": _extraction(analysis),
            "generation": _setup(generated_by.pinned) if generated_by else None,
            "labelling": list(labelling.values()),
            "calls_cost": cost["total"],
        },
    }
