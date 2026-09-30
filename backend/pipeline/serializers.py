import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from corpus.models import Scenario
from pipeline.execution_config import ChosenInputUnavailable, chosen_generation_config
from pipeline.models import (
    VARIANTS,
    Experiment,
    Label,
    LLMCall,
    PhaseRun,
    PipelineRun,
    _progress_dict,
    generated_seed_systems_error,
    generation_config_errors,
    taxonomy_missing_rows_error,
    template_phase_mismatch_error,
    unbound_guard_taxonomy_error,
)
from taxonomy.models import Taxonomy

# Upper bound for `PhaseRunCreateSerializer`'s `n_systems`/
# `n_scenarios_per_system` overrides. NOT a PostgreSQL-overflow guard — a
# coordinator correction, second review round: `integer_field_ranges
# ["PositiveIntegerField"]` is `(0, 2147483647)` on EVERY Django database
# backend, and DRF's `IntegerField` maps that to `max_value` automatically,
# so `n_systems=2**31`/`10**12` were already 400s (`min_value`/`max_value`
# validation), never a live `NumericValueOutOfRange` 500 — that framing, in
# an earlier version of this comment and the report, was simply wrong. The
# real reason for this cap is a deliberate PRODUCT choice, not a database
# safety net: `Experiment.n_systems`/`n_scenarios_per_system` (uncapped —
# a real experiment can legitimately ask for 60 systems) describe a batch
# run someone is choosing to pay for; an AD-HOC OVERRIDE describes a cheap
# TUNING call, and 50 (ten times the real-experiment default of 3x5) is
# generous room to explore more variety before committing to a real batch
# without inviting a single phase-2 call to ask for hundreds of systems in
# one JSON body. The asymmetry with `Experiment.n_systems` is intentional,
# not an oversight: nothing here caps what a real experiment can request.
_ADHOC_OVERRIDE_MAX = 50
# Most scenarios one ad-hoc labelling test may choose (`PhaseRun.scenario_ids`).
_ADHOC_SCENARIOS_MAX = 200


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
            "phases",
            "phase2_model",
            "phase2_temperature",
            "phase2_prompt_a",
            "phase2_prompt_b",
            "phase3_model",
            "phase3_temperature",
            "phase3_prompt",
            "seed_systems",
            "fewshots",
            "label_scenarios",
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
        m2m_fields = frozenset({"seed_systems", "fewshots", "label_scenarios"})

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
        if "label_scenarios" in attrs:
            # clean() validates the submitted selection, not the saved one.
            instance._pending_label_scenarios = attrs["label_scenarios"]

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

        `generated_seed_systems_error` — see its own docstring — is the
        SAME check `Experiment.clean()` and `PhaseRunCreateSerializer` (an
        ad-hoc run borrowing an experiment's live seed_systems) both use.
        """
        seeds = attrs.get("seed_systems")
        if not seeds:
            return
        seed_error = generated_seed_systems_error(seeds)
        if seed_error:
            raise serializers.ValidationError({"seed_systems": seed_error})


class RunRequestSerializer(serializers.Serializer):
    n_repetitions = serializers.IntegerField(
        required=False,
        default=1,
        min_value=1,
        max_value=100,
        help_text=(
            "Model output varies between runs, so one repetition's result may be noise; "
            "more repetitions give a result you can trust. "
            "Cost grows with this number (each run page shows what it cost), and up to 2 "
            "repetitions run at once. Start with 1."
        ),
    )


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
    scenarios = serializers.JSONField(source="scenario_ids", read_only=True)
    # A prompt test's own chosen inputs (null when not chosen / borrowed / orchestrated).
    seed_systems = serializers.JSONField(source="seed_system_ids", read_only=True)
    fewshots = serializers.JSONField(source="fewshot_ids", read_only=True)
    target_system = serializers.IntegerField(source="target_system_pk", read_only=True)
    # Summed from the prefetched calls; None until a call reports a cost.
    total_cost = serializers.SerializerMethodField()

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
            # The Scenario ids an ad-hoc labelling run was asked to label, or
            # null when it labels `source_phase_run`'s output.
            "scenarios",
            # The ad-hoc "borrow an experiment's live inputs" trio — see
            # `PhaseRun.experiment`'s own comment. Always present (null on
            # every orchestrated row, and on a bare ad-hoc one), so a client
            # can tell what an iterate-mode run actually rendered against.
            "experiment",
            "n_systems",
            "n_scenarios_per_system",
            "seed_systems",
            "fewshots",
            "target_system",
            "generate_systems",
            "generate_scenarios",
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
            "total_cost",
        ]

    def get_total_cost(self, obj):
        costs = [call.cost for call in obj.calls.all() if call.cost is not None]
        return round(sum(costs), 6) if costs else None

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


class PhaseRunCreateSerializer(serializers.ModelSerializer):
    """Iterate mode: `POST /api/phase-runs/` — one phase, ad-hoc, no
    `pipeline_run`. A deliberately separate serializer from `PhaseRunSerializer`
    above, not that one reused for writes too: `PhaseRunSerializer` exposes
    computed, READ-only fields (`progress`, `leak_scan_ran`, `guarded_by`, ...)
    no create request should ever be able to set, and this serializer's own
    `validate()` (below) has no read-side meaning at all. `pipeline_run` is
    deliberately absent from `fields` — not merely read-only — so there is no
    way for a request to set it; `PhaseRunViewSet.create` is the only writer,
    and it never does either (see spec §5.4: "null for ad-hoc iterate-mode
    runs").

    Validated the same way `Experiment.clean()` validates an orchestrated
    run's configuration, reusing its exact reasoning rather than restating it
    loosely — `template_phase_mismatch_error`/`taxonomy_missing_rows_error`/
    `generated_seed_systems_error`/`generated_target_system_error`
    (`pipeline.models`) are the SAME checks `Experiment.clean()` itself
    calls, so an ad-hoc run cannot slip past a rule an orchestrated one would
    have been refused for. A phase-2 run
    additionally needs a real variant (`A` or `B`) — `run_phase2` fails
    closed on a blank one anyway (see its own docstring), but refusing it
    here means that failure is a 400 with a clear reason, not a wasted,
    immediately-FAILED `PhaseRun` row. A phase-3 run needs `source_phase_run`
    — it is where the scenarios to label come from
    (`pipeline.labelling.scenarios_for`) — and that source must itself be a
    phase-2 run, or there is nothing it could have generated.

    `experiment` (optional): borrow that experiment's CURRENT, LIVE inputs
    (`seed_systems`, `fewshots`, `target_system`, `generate_systems`,
    `generate_scenarios`, `n_systems`, `n_scenarios_per_system`) instead of
    the bare built-in defaults — see `pipeline.execution_config
    ._adhoc_generation_config`/`_adhoc_labelling_fewshots`, the readers.
    Read live at EXECUTION time, not pinned here at creation time: the whole
    point (researcher's own words, "rerun manually until I am satisfied") is
    iterating against the experiment as it stands right NOW, including an
    edit made after this row was created but before a worker picks it up.
    `n_systems`/`n_scenarios_per_system` (optional, `min_value=1`) override
    just those two counts from the borrowed experiment — the actual
    iterate-mode use case: real seeds and few-shots, but one system and one
    scenario, cheap to tune against. Meaningless without `experiment` set
    (nothing reads them), and for a phase-3 run (labelling has no system/
    scenario count of its own) — accepted regardless rather than refused,
    the same permissive stance `source_phase_run` already gets on a phase-2
    request.

    `contamination` — a GENERATED-origin system in the borrowed experiment's
    LIVE `seed_systems` OR its `target_system` (BOTH routes, not just
    seeding — see `generated_target_system_error`'s own docstring for why
    missing the second one is a critical, not cosmetic, gap) — is checked
    here too, at create time, for a fast, friendly 400.
    `Experiment.clean()`'s equivalent guards only ever run when the
    EXPERIMENT ITSELF is validated, which an ad-hoc run reading its live
    `seed_systems`/`target_system` never triggers. This is a best-effort,
    CREATE-time check only: the experiment can still be edited between
    creation and execution, so `_adhoc_generation_config` re-checks the same
    thing again, live, at the point it actually matters (see its own
    docstring) — this check exists to fail fast and readably for the common
    case, not as the only guard.
    """

    # Declared explicitly rather than left to ModelSerializer's own
    # inference: a bare `PositiveIntegerField(null=True)` maps to
    # `min_value=0, max_value=2147483647` — 0 systems/scenarios is not a
    # real override, just a confusing way to spell "none" (`min_value=1`
    # matches `RunRequestSerializer.n_repetitions`'s own stance on the same
    # class of "must be a real, positive count" field). `max_value` is
    # tightened FAR below that default (see `_ADHOC_OVERRIDE_MAX`'s own
    # comment for what this bound is actually for — a deliberate product
    # choice for a TUNING run, not a database safety net; the field's
    # built-in max was already enough to keep this out of the database).
    n_systems = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=_ADHOC_OVERRIDE_MAX
    )
    n_scenarios_per_system = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=_ADHOC_OVERRIDE_MAX
    )
    # NOT required, unlike the model field it backs (`PhaseRun.taxonomy` has
    # no `null=True`) — see `validate()`'s own handling, coordinator review
    # round finding 1: when `experiment` is borrowed, `taxonomy` is DERIVED
    # outright from `experiment.taxonomy` regardless of what (if anything)
    # was submitted, so requiring it here would demand a value this
    # serializer is about to discard anyway.
    taxonomy = serializers.PrimaryKeyRelatedField(queryset=Taxonomy.objects.all(), required=False)
    # Phase 3 only: label exactly these Scenario ids (any origin) instead of a
    # source run's output. Stored as `PhaseRun.scenario_ids`.
    scenarios = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_null=True
    )
    # Phase 2 without a borrowed experiment only: the test's own inputs, checked
    # by the same builder that resolves them at run time (`chosen_generation_config`).
    # An empty list is valid and means "no examples". `default=None` keeps an
    # omitted boolean None (not False) for form-encoded requests too.
    seed_systems = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_null=True
    )
    fewshots = serializers.ListField(
        child=serializers.IntegerField(min_value=1), required=False, allow_null=True
    )
    target_system = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    generate_systems = serializers.BooleanField(default=None, allow_null=True)
    generate_scenarios = serializers.BooleanField(default=None, allow_null=True)

    class Meta:
        model = PhaseRun
        fields = [
            "id",
            "phase",
            "variant",
            "model",
            "temperature",
            "prompt_template",
            "taxonomy",
            "source_phase_run",
            "scenarios",
            "experiment",
            "n_systems",
            "n_scenarios_per_system",
            "seed_systems",
            "fewshots",
            "target_system",
            "generate_systems",
            "generate_scenarios",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        errors = {}
        phase = attrs["phase"]
        variant = attrs.get("variant", "")
        source = attrs.get("source_phase_run")
        experiment = attrs.get("experiment")
        chosen = attrs.pop("scenarios", None)
        if chosen is not None:
            scenario_error = self._chosen_scenarios_error(phase, chosen, source)
            if scenario_error:
                errors["scenarios"] = scenario_error
            attrs["scenario_ids"] = list(dict.fromkeys(chosen))
        errors.update(self._chosen_inputs_errors(attrs, phase, variant, experiment))

        if phase == 2:
            if variant not in VARIANTS:
                errors["variant"] = "Choose variant A or variant B for a generation run."
        elif phase == 3:
            if source is None and chosen is None:
                errors["source_phase_run"] = (
                    "A phase 3 (labelling) run needs source_phase_run — it is "
                    "where the scenarios to label come from."
                )
            elif source is not None and source.phase != 2:
                errors["source_phase_run"] = (
                    f"source_phase_run must be a phase 2 (generation) run; "
                    f"{source} is phase {source.phase}."
                )
            # Coordinator review round, finding 5: phase 3 skipped this
            # check entirely, so `variant="C"` (or any other free string)
            # returned 202 — `PhaseRun.variant` is a plain CharField with no
            # DB-level choices constraint. Harmless to isolation (phase 3
            # never branches on it the way phase 2's `is_unbiased` does),
            # but it is archived and surfaces in every export, so it should
            # mean something recognisable there too. Blank stays allowed
            # (an ad-hoc labelling run need not record which variant it
            # labels), matching today's permissiveness for everything except
            # actual garbage.
            if variant and variant not in VARIANTS:
                errors["variant"] = (
                    f"variant must be blank or one of {list(VARIANTS)}, got {variant!r}."
                )

        template_error = template_phase_mismatch_error(
            attrs["prompt_template"], phase, "prompt_template"
        )
        if template_error:
            errors["prompt_template"] = template_error

        # STRUCTURAL: the guard/labelling taxonomy must not be a free
        # choice — coordinator review round, finding 1 (CRITICAL). See
        # `unbound_guard_taxonomy_error`'s own docstring for the full
        # reasoning (the decoy-taxonomy probe this closes, and why
        # `leak_scan_ran=True` on the WRONG list is worse than no scan at
        # all). Deriving beats validating: when `experiment` is borrowed,
        # `taxonomy` is FORCED to `experiment.taxonomy` outright, silently
        # overriding whatever (if anything) was submitted — a field that
        # cannot be wrong beats one that is checked. With no experiment,
        # there is nothing to derive from, so the submitted value is
        # validated instead, against `Taxonomy.active()`.
        if experiment is not None:
            attrs["taxonomy"] = experiment.taxonomy
        else:
            taxonomy_error = unbound_guard_taxonomy_error(attrs.get("taxonomy"), None)
            if taxonomy_error:
                errors["taxonomy"] = taxonomy_error

        resolved_taxonomy = attrs.get("taxonomy")
        if resolved_taxonomy is not None:
            rows_error = taxonomy_missing_rows_error(resolved_taxonomy)
            if rows_error:
                errors.setdefault("taxonomy", rows_error)

        if experiment is not None and phase == 2:
            # Phase-2 ONLY: `seed_systems`/`target_system`/the generation
            # toggles are read exclusively by `_adhoc_generation_config`
            # (phase 2's resolver) — `_adhoc_labelling_fewshots` (phase 3)
            # reads neither `seed_systems` nor `target_system` at all (see
            # its own docstring). Checking them for a phase-3 create was
            # OVER-enforcement, coordinator review round finding 5: refusing
            # a request for a reason that phase's own render path could
            # never actually reach.
            #
            # Round 4: every one of those config-shaped rules now comes from
            # the ONE shared checker `Experiment.clean()` itself uses —
            # `generation_config_errors` over `experiment.generation_config`
            # (`pipeline.models`). Three rounds running, a rule added to
            # `clean()` was never hand-ported to this method; there is now
            # nothing to hand-port, and `pipeline/tests/
            # test_adhoc_rule_parity.py` fails the build if a future rule
            # tries to skip this path anyway. Flattened to a list under one
            # `experiment` key because these are faults of the BORROWED
            # experiment, not of any field on this request.
            config_errors = generation_config_errors(experiment.generation_config)
            generation_errors = list(config_errors.values())
            if generation_errors:
                errors["experiment"] = generation_errors

        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @staticmethod
    def _chosen_inputs_errors(attrs, phase, variant, experiment):
        """Pop the chosen-input request fields into their stored names and check
        them with the SAME rules an experiment's inputs obey. Mutates `attrs`."""
        seed_ids = attrs.pop("seed_systems", None)
        fewshot_ids = attrs.pop("fewshots", None)
        target_pk = attrs.pop("target_system", None)
        chosen = {
            "seed_systems": seed_ids,
            "fewshots": fewshot_ids,
            "target_system": target_pk,
            "generate_systems": attrs.get("generate_systems"),
            "generate_scenarios": attrs.get("generate_scenarios"),
        }
        given = [field for field, value in chosen.items() if value is not None]
        if given and phase != 2:
            return dict.fromkeys(given, "Choosing these inputs applies only to a generation test.")
        if given and experiment is not None:
            message = (
                "This test borrows an experiment's inputs, so it can't also choose its "
                "own. Choose one or the other."
            )
            return dict.fromkeys(given, message)
        if seed_ids is not None:
            attrs["seed_system_ids"] = list(dict.fromkeys(seed_ids))
        if fewshot_ids is not None:
            attrs["fewshot_ids"] = list(dict.fromkeys(fewshot_ids))
        attrs["target_system_pk"] = target_pk
        if phase != 2 or experiment is not None:
            return {}

        try:
            config = chosen_generation_config(
                variant=variant,
                seed_system_ids=seed_ids,
                fewshot_ids=fewshot_ids,
                target_system_pk=target_pk,
                generate_systems=chosen["generate_systems"],
                generate_scenarios=chosen["generate_scenarios"],
                n_systems=attrs.get("n_systems"),
                n_scenarios_per_system=attrs.get("n_scenarios_per_system"),
            )
        except ChosenInputUnavailable as error:
            return {error.field: str(error)}
        # Generated seeds/target and the toggles: the checker Experiment.clean() uses.
        return generation_config_errors(config)

    @staticmethod
    def _chosen_scenarios_error(phase, chosen, source):
        if phase != 3:
            return "Choosing scenarios applies only to a labelling run."
        if source is not None:
            return "Choose either scenarios to label or a generation run, not both."
        ids = set(chosen)
        if not ids:
            return "Choose at least one scenario to label."
        if len(ids) > _ADHOC_SCENARIOS_MAX:
            return f"Choose at most {_ADHOC_SCENARIOS_MAX} scenarios for one test run."
        if Scenario.objects.filter(id__in=ids).count() != len(ids):
            return "Some chosen scenarios no longer exist. Refresh the list and choose again."
        return None


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
