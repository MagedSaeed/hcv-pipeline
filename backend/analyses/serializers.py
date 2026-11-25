"""The API's input checks and the JSON it returns."""

from rest_framework import serializers

from analyses import state
from analyses.models import AnalysisRun, AnalysisScenario
from taxonomy.models import Taxonomy

DESCRIPTION_MAX_LENGTH = 50_000
TITLE_MAX_LENGTH = 160


class AnalysisWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=300, allow_blank=True, required=False)
    description = serializers.CharField(
        max_length=DESCRIPTION_MAX_LENGTH,
        allow_blank=True,
        trim_whitespace=False,
        required=False,
    )
    taxonomy = serializers.PrimaryKeyRelatedField(queryset=Taxonomy.objects.all(), required=False)
    extraction_settings = serializers.JSONField(required=False)
    generation_settings = serializers.JSONField(required=False)
    labelling_settings = serializers.JSONField(required=False)


class ItemWriteSerializer(serializers.Serializer):
    ref = serializers.CharField(max_length=120, required=False)
    value = serializers.CharField(max_length=200, required=False, allow_blank=True)
    state = serializers.ChoiceField(choices=["present", "absent"], required=False)
    decision = serializers.ChoiceField(choices=["pending", "approved", "rejected"], required=False)


class ItemCreateSerializer(serializers.Serializer):
    field = serializers.CharField(max_length=60)
    ref = serializers.CharField(max_length=120)
    value = serializers.CharField(max_length=200)
    state = serializers.ChoiceField(choices=["present", "absent"], required=False)


class ScenarioWriteSerializer(serializers.Serializer):
    narrative = serializers.CharField(max_length=20_000, required=False)
    set_aside = serializers.BooleanField(required=False)

    def get_fields(self):
        fields = super().get_fields()
        for name in AnalysisScenario.CARD_FIELDS:
            fields[name] = serializers.CharField(
                max_length=10_000, allow_blank=True, required=False
            )
        return fields


class LabelRequestSerializer(serializers.Serializer):
    only_failed = serializers.BooleanField(default=False)


def scenario_title(scenario):
    for text in (scenario.trigger, scenario.narrative):
        for line in text.splitlines():
            if line.strip():
                return line.strip()[:TITLE_MAX_LENGTH]
    return scenario.scenario_id


def label_json(label):
    return {
        "hcv_id": label.hcv.hcv_id,
        "name": label.hcv.name,
        "evidence": label.evidence,
        "rationale": label.rationale,
    }


def scenario_json(analysis, scenario, *, current_labels_only=False):
    status = state.label_status(analysis, scenario)
    latest = max(scenario.calls.all(), key=lambda call: call.pk, default=None)
    show_labels = status == "current" or not current_labels_only
    return {
        "id": scenario.pk,
        "scenario_id": scenario.scenario_id,
        **scenario.card,
        "narrative": scenario.narrative,
        "title": scenario_title(scenario),
        "is_manual": scenario.is_manual,
        "set_aside": scenario.set_aside,
        "label_status": status,
        "error": latest.error if status == "failed" and latest else "",
        "labels": [label_json(row) for row in scenario.labels.all()] if show_labels else [],
    }


def run_json(run):
    if run is None:
        return None
    return {
        "id": run.pk,
        "kind": run.kind,
        "status": run.status,
        "total": run.total,
        "done": run.done,
        "failed": run.failed,
        "notice": run.notice,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "working_on": _working_on(run),
    }


def _working_on(run):
    """The scenario a label run is on now: tasks go one at a time, in target order."""
    active = run.status in (AnalysisRun.Status.QUEUED, AnalysisRun.Status.RUNNING)
    if run.kind != AnalysisRun.Kind.LABEL or not active or run.position >= len(run.targets):
        return None
    return run.targets[run.position]


def taxonomy_json(taxonomy):
    return {
        "id": taxonomy.pk,
        "vocabulary_id": taxonomy.vocabulary_id,
        "version": taxonomy.version,
    }


def summary_json(analysis):
    return {
        "id": analysis.pk,
        "name": analysis.name,
        "owner_email": analysis.owner.email if analysis.owner else None,
        "steps": state.step_states(analysis),
        "resume_step": state.resume_step(analysis),
        "updated_at": analysis.updated_at.isoformat(),
    }


def item_json(item, rejected):
    refers = item.ref in rejected or (item.kind == "fact" and item.value in rejected)
    return {
        "id": item.pk,
        "kind": item.kind,
        "section": item.section,
        "field": item.field,
        "ref": item.ref,
        "value": item.value,
        "original_value": item.original_value,
        "state": item.state,
        "original_state": item.original_state,
        "fact_type": item.fact_type,
        "reason": item.reason,
        "other_reading": item.other_reading,
        "problem": item.problem,
        "decision": item.decision,
        "edited": item.edited,
        "added": item.added,
        "refers_to_rejected": item.kind != "element" and refers,
    }


def extraction_json(analysis, latest_run):
    items = list(analysis.items.all())
    rejected = {i.ref for i in items if i.kind == "element" and i.decision == "rejected"}
    return {
        "status": state.extraction_status(analysis),
        "settings": analysis.extraction_settings,
        "items": [item_json(item, rejected) for item in items],
        "gaps": analysis.extraction_gaps or {"not_assessable": [], "no_term": []},
        "pending": sum(1 for item in items if item.decision == "pending"),
        "run": run_json(latest_run),
        "body": state.system_body(analysis),
    }


def detail_json(analysis):
    latest = {run.kind: run for run in sorted(analysis.runs.all(), key=lambda run: run.pk)}
    return {
        **summary_json(analysis),
        "description": analysis.description,
        "locked": state.is_locked(analysis),
        "taxonomy": taxonomy_json(analysis.taxonomy),
        "extraction_settings": analysis.extraction_settings,
        "generation_settings": analysis.generation_settings,
        "labelling_settings": analysis.labelling_settings,
        "extraction": extraction_json(analysis, latest.get("extract")),
        "latest_runs": {
            kind: run_json(latest.get(kind)) for kind in ("extract", "generate", "label")
        },
        "scenarios": [scenario_json(analysis, row) for row in analysis.scenarios.all()],
    }
