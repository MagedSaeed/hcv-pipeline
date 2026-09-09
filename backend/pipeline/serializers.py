import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun, _progress_dict


class ProgressField(serializers.Field):
    """Read-only passthrough of a model's `progress` dict.

    Prefers `ExperimentViewSet.get_queryset()`'s per-row annotations
    (`progress_total`/`progress_completed`/`progress_failed`) over the
    `.progress` property, which costs its own aggregate query — cheap for a
    single detail lookup, but one extra query PER ROW on a list of N
    experiments. An instance that never went through that annotated
    queryset — freshly created by `perform_create`, say — has none of those
    three attributes at all, which is the signal to fall back to the
    property instead of silently reading three missing values as zero.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, value):
        if not hasattr(value, "progress_total"):
            return value.progress
        completed = value.progress_completed or 0
        failed = value.progress_failed or 0
        return _progress_dict(completed + failed, value.progress_total or 0, failed)


class ExperimentSerializer(serializers.ModelSerializer):
    progress = ProgressField()
    run_count = serializers.SerializerMethodField()

    class Meta:
        model = Experiment
        fields = [
            "id",
            "name",
            "description",
            "taxonomy",
            "phase2_model",
            "phase2_temperature",
            "phase2_prompt",
            "phase3_model",
            "phase3_temperature",
            "phase3_prompt",
            "seed_systems",
            "fewshots",
            "generate_systems",
            "generate_scenarios",
            "target_system",
            "n_systems",
            "n_scenarios_per_system",
            "variants",
            "progress",
            "run_count",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["created_by", "created_at"]
        # Not settable on an unsaved instance, so validate() skips them.
        m2m_fields = frozenset({"seed_systems", "fewshots"})

    def get_run_count(self, obj):
        """Prefer `get_queryset()`'s annotated Count over a live `.count()`.

        Same reasoning as `ProgressField` above: an instance annotated by
        `ExperimentViewSet.get_queryset()` carries `run_count_annotated`
        already; one that was not — a fresh `perform_create` instance —
        falls back to the query it would have cost per row anyway.
        """
        if hasattr(obj, "run_count_annotated"):
            return obj.run_count_annotated
        return obj.runs.count()

    def validate(self, attrs):
        """Run the model's own validation. The API must not bypass it.

        Experiment.clean() carries the rules that keep the comparison honest —
        that a generated system may not target a run, that both toggles cannot
        be off, that each prompt matches its phase, that the taxonomy has rows
        for variant A's leak backstop to scan. Skipping it here would let the
        API store configurations `launch` then refuses, so the operator learns
        at run time what they could have learned at save time.

        Validation runs against a COPY carrying the pending changes, so a
        rejected PATCH leaves `self.instance` untouched for the error response.
        """
        instance = copy.copy(self.instance) if self.instance else Experiment()
        for field, value in attrs.items():
            if field not in self.Meta.m2m_fields:
                setattr(instance, field, value)

        try:
            instance.clean()
        except DjangoValidationError as error:
            # `.message_dict` raises AttributeError on a ValidationError that
            # was not built from a dict (a plain string, say) — falling back
            # to `{"detail": str(error)}` keeps that a 400 instead of a 500.
            raise serializers.ValidationError(
                getattr(error, "message_dict", {"detail": str(error)})
            ) from error

        self._refuse_generated_seeds(attrs)
        return attrs

    def _refuse_generated_seeds(self, attrs):
        """Close the create-time hole in Experiment.clean().

        clean() can only check seed_systems when the row already has a pk —
        an unsaved instance has no M2M table to query. So on create, a
        generated system could be seeded through the API and would only be
        caught later, by `launch`'s full_clean. Checking the supplied list
        directly catches it at the point the operator can still fix it.
        """
        seeds = attrs.get("seed_systems")
        if not seeds:
            return
        generated = [s.title for s in seeds if s.origin == "generated"]
        if generated:
            raise serializers.ValidationError(
                {
                    "seed_systems": (
                        f"{generated} were generated by a previous run. Seeding an "
                        "experiment with its own output contaminates the comparison."
                    )
                }
            )


class RunRequestSerializer(serializers.Serializer):
    n_repetitions = serializers.IntegerField(required=False, default=1, min_value=1, max_value=100)


class PipelineRunSerializer(serializers.ModelSerializer):
    progress = ProgressField()

    class Meta:
        model = PipelineRun
        fields = [
            "id",
            "experiment",
            "repetition_index",
            "status",
            # The configuration THIS repetition actually used, pinned once at
            # launch — see `PipelineRun.config`. Exposed here, not just via
            # `/export/`, per the user's own framing of the decision: "even
            # if these config data change, browsing an older run should show
            # its previous config" (2026-09-09).
            "config",
            "progress",
            "started_at",
            "finished_at",
        ]


class PhaseRunSerializer(serializers.ModelSerializer):
    progress = ProgressField()
    leak_scan_ran = serializers.SerializerMethodField()
    guarded_by = serializers.SerializerMethodField()

    class Meta:
        model = PhaseRun
        fields = [
            "id",
            "pipeline_run",
            "phase",
            "variant",
            "model",
            "temperature",
            "prompt_template",
            "taxonomy",
            "source_phase_run",
            "status",
            "error",
            "progress",
            "total_children",
            "completed_children",
            "failed_children",
            "leak_scan_ran",
            "guarded_by",
            "started_at",
            "finished_at",
        ]

    def get_leak_scan_ran(self, obj):
        """Whether the Run A isolation backstop ran for EVERY archived call.

        For a phase-2 call this now stands for TWO checks, not one:
        `LLMCall.leak_scan_ran` (see its own field comment) is only ever
        True when both the render-time backstop AND the final-request
        choke point (`pipeline.generation._preflight`, wired into
        `llm.client`'s `preflight`) ran clean — a call the second one
        caught never gets archived at all, so it cannot pull this aggregate
        down by contributing a False row; it simply contributes no row.

        `any(...)` used to report True the moment ONE call had run the scan,
        even with other calls in the same run that had not — read by an
        operator as "this run's isolation is proven" when it was only
        PARTIALLY proven, which is worse than reporting nothing. This fails
        toward "not proven" instead: no calls at all, or any call that did
        not run the scan, both read False. `run_phase2` archives exactly one
        call today, so the distinction from `any(...)` is not reachable yet —
        this is about what the field means, not a live bug.
        """
        calls = list(obj.calls.all())
        return bool(calls) and all(call.leak_scan_ran for call in calls)

    def get_guarded_by(self, obj):
        """Every distinct taxonomy id that guarded one of this run's calls.

        A run's calls are all pinned to the same taxonomy at launch, so in
        practice they should never disagree — but silently returning
        whichever call happened to be first would paper over that exact
        disagreement if it ever occurred, which is precisely the case an
        operator most needs surfaced. Always a list rather than a single id:
        empty when no archived call ran the leak backstop at all, one element
        when every call that did agrees on which taxonomy, more than one if
        they do not.
        """
        ids = {call.guarded_by_id for call in obj.calls.all() if call.guarded_by_id}
        return sorted(ids)


class LLMCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLMCall
        fields = [
            "id",
            "sequence",
            "scenario",
            "rendered_system_prompt",
            "rendered_user_prompt",
            "request_json",
            "response_json",
            "raw_response",
            "parsed_ok",
            "parse_error",
            "unknown_hcv_ids",
            "stray_scenarios",
            "http_status",
            "transport_error",
            "model",
            "finish_reason",
            "prompt_tokens",
            "completion_tokens",
            "cost",
            "latency_ms",
            "leak_scan_ran",
            "guarded_by",
            "created_at",
        ]


class LabelSerializer(serializers.ModelSerializer):
    hcv_id = serializers.CharField(source="hcv.hcv_id", read_only=True)
    hcv_name = serializers.CharField(source="hcv.name", read_only=True)
    scenario_id = serializers.CharField(source="scenario.scenario_id", read_only=True)

    class Meta:
        model = Label
        fields = [
            "id",
            "phase_run",
            "scenario",
            "scenario_id",
            "hcv",
            "hcv_id",
            "hcv_name",
            "evidence",
            "rationale",
            "source",
            "llm_call",
            "created_by",
            "created_at",
        ]
