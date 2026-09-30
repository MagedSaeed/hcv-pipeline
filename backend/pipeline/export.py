"""Assemble the archival bundle for one experiment.

This computes nothing. Spec §9 makes the bundle the record from which the
deferred metrics work and the offline ontology step both read, so it carries
the prompts and every call verbatim rather than only the labels. Truncating to
save size would defeat the point.

Known limit, not yet addressed: `build_bundle` materialises every section
(every call included) into one in-memory dict, and `json.dumps`/
`write_csv_zip` each build a further full copy of it on top. At
`RunRequestSerializer`'s maximum of 100 repetitions, a two-variant experiment
with a modest scenario count is roughly 160 MB resident for the bundle alone,
and 3-4x that at peak while a second copy (the JSON string, or the CSV zip
buffer) is being built alongside the first. There is no streaming here — this
is a known boundary to design around later (a background export job, or a
streaming writer), not a bug to silently hit.

An experiment that has never been launched has no `PhaseRun` to pin
anything to, so `prompts` and `taxonomy` come back empty (`[]`) — this is
correct, not data loss: nothing has been pinned yet, so there is nothing
verbatim to archive. `runs`/`systems`/`scenarios`/`labels`/`calls` are
empty for the same reason. The export still succeeds (200, a valid JSON body
or a valid zip) rather than refusing outright — an operator exporting a
freshly-created experiment should see "nothing has run yet", not an error
that looks like the export itself is broken.

Two bundle shapes share almost all of their assembly: `build_bundle`
(everything an experiment has ever run) and `build_run_bundle` (one
repetition). Both read `taxonomy`/`prompts`/`systems`/`scenarios`/`labels`/
`calls` off the same `phase_run_ids`-scoped helpers below — the only
difference is which `PhaseRun` ids that scope resolves to. `build_bundle`'s
`experiment_current` section is deliberately named to make clear it is the
experiment's CURRENT, live, editable configuration — never mistake it for
what any particular run actually used, which is `runs[N]["config"]` (or,
for `build_run_bundle`, the top-level `inputs`) instead. See
`PipelineRun.config`'s own docstring for why: `Experiment` may be (and is
meant to stay) freely editable even after it has runs, so only a value
PINNED at launch, never re-read afterward, can be trusted to describe what a
past run actually saw.
"""

import csv
import io
import json
import re
import zipfile

from django.db.models import Q, Sum

from corpus.models import Scenario, SystemDescription
from pipeline.execution_config import (
    _FEWSHOT_KEY_ORDER,
    _SCENARIO_KEY_ORDER,
    _SYSTEM_KEY_ORDER,
    _TOP_LEVEL_KEY_ORDER,
)
from pipeline.models import Label, LLMCall, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import Taxonomy

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename_stem(name):
    """A `Content-Disposition` filename stem built from user-controlled text.

    `Experiment.name` is a free, staff-writable 200-char field with no
    charset restriction. A bare `\\r`/`\\n` in it reaches
    `HttpResponse.__setitem__` verbatim and raises `BadHeaderError` (a 500);
    a bare `"` silently breaks the filename's own quoting. Collapsing
    anything outside a safe filename charset to `-` keeps something
    recognisable while never letting the header itself carry an unsafe byte.
    """
    safe = _UNSAFE_FILENAME_CHARS.sub("-", name).strip("-")
    return safe or "experiment"


def _experiment(experiment):
    return {
        "id": experiment.pk,
        "name": experiment.name,
        "description": experiment.description,
        "phases": experiment.phases,
        "phase2_model": experiment.phase2_model,
        "phase2_temperature": experiment.phase2_temperature,
        "phase3_model": experiment.phase3_model,
        "phase3_temperature": experiment.phase3_temperature,
        "generate_systems": experiment.generate_systems,
        "generate_scenarios": experiment.generate_scenarios,
        "n_systems": experiment.n_systems,
        "n_scenarios_per_system": experiment.n_scenarios_per_system,
        "variants": experiment.variants,
        "label_scenarios": sorted(experiment.label_scenarios.values_list("pk", flat=True)),
        "created_by": experiment.created_by_id,
        "created_at": experiment.created_at.isoformat(),
    }


def _taxonomy(phase_run_ids):
    """Every taxonomy actually pinned by one of this experiment's phase runs,
    verbatim.

    Driven from `PhaseRun.taxonomy_id` — the REAL pin — not
    `experiment.taxonomy`, which is a plain, editable FK. An operator can
    `PATCH` an experiment's `taxonomy` (or `phase2_prompt_a`/
    `phase2_prompt_b`/`phase3_prompt`, see `_prompts`) after it has already
    run; the archive must still say what a run actually used, not what the
    experiment currently points at, or a re-pinned experiment ships a bundle
    whose `labels` cite HCV ids from a taxonomy the bundle never lists.
    Always a list: an experiment edited
    mid-flight can pin more than one taxonomy across its runs, and every one
    must resolve for every run's `taxonomy` id in `_runs`.
    """
    taxonomy_ids = (
        PhaseRun.objects.filter(pk__in=phase_run_ids)
        .order_by("taxonomy_id")
        .values_list("taxonomy_id", flat=True)
        .distinct()
    )
    return [
        {
            "id": taxonomy.pk,
            "vocabulary_id": taxonomy.vocabulary_id,
            "version": taxonomy.version,
            "notes": taxonomy.notes,
            "source_json": taxonomy.source_json,
            "created_at": taxonomy.created_at.isoformat(),
            "rows": [
                {
                    "hcv_id": row.hcv_id,
                    "name": row.name,
                    "definition": row.definition,
                    "condition_classes": row.condition_classes,
                    "order": row.order,
                }
                # HCV.Meta.ordering = ["order", "hcv_id"]; hcv_id is unique
                # per taxonomy (unique_hcv_per_taxonomy), and this is always
                # scoped to one taxonomy, so this is already deterministic.
                for row in taxonomy.hcvs.all()
            ],
        }
        for taxonomy in Taxonomy.objects.filter(pk__in=taxonomy_ids).order_by("id")
    ]


def _prompts(phase_run_ids):
    """Every prompt template actually pinned by one of this experiment's
    phase runs, verbatim at the version used.

    Driven from `PhaseRun.prompt_template_id` — the REAL pin — not
    `experiment.phase2_prompt_a`/`phase2_prompt_b`/`phase3_prompt`. Those
    are plain, editable FKs: `PATCH {"phase2_prompt_a": <new>}` on an
    experiment that has already run succeeds (nothing freezes them), and
    reading them here would then ship the WRONG wording — the text that
    produced the archived results would be absent from the very record
    meant to preserve it. See `_taxonomy` for the identical reasoning.
    Always covers every distinct template id pinned across every phase
    run, not just three (`phase2_prompt_a`'s pin on variant A's generation
    run, `phase2_prompt_b`'s on variant B's, `phase3_prompt`'s on both
    labelling runs) — a run edited mid-flight can pin more across its own
    history the same way `_taxonomy` can pin more than one taxonomy.
    """
    template_ids = (
        PhaseRun.objects.filter(pk__in=phase_run_ids)
        .order_by("prompt_template_id")
        .values_list("prompt_template_id", flat=True)
        .distinct()
    )
    return [
        {
            "id": template.pk,
            "phase": template.phase,
            "name": template.name,
            "version": template.version,
            "system_prompt": template.system_prompt,
            "user_prompt": template.user_prompt,
            "output_schema": template.output_schema,
            "created_by": template.created_by_id,
            "created_at": template.created_at.isoformat(),
        }
        for template in PromptTemplate.objects.filter(pk__in=template_ids).order_by("id")
    ]


def _system_row(s):
    """One `SystemDescription` row, generated or seed/manual alike — reused
    by `_systems` so every GENERATED system in the bundle carries the same
    fields. Not used for a run's seed/target systems: those are read off the
    run's own `config` snapshot instead (see `pipeline.orchestration
    ._snapshot_system`), which carries a deliberately leaner, resolved-text
    shape, not this row's full audit trail.
    """
    return {
        "id": s.pk,
        "title": s.title,
        "slug": s.slug,
        "body": s.body,
        "origin": s.origin,
        # Only a GENERATED row has a generation_run at all; the FK is never
        # touched (no query) when it's None.
        "variant": s.generation_run.variant if s.generation_run_id else None,
        "generation_run": s.generation_run_id,
        "created_by": s.created_by_id,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


def _scenario_row(s):
    """One `Scenario` row, generated or seed/manual alike — reused by
    `_scenarios` for the same reason as `_system_row`.
    """
    return {
        "id": s.pk,
        "system_description": s.system_description_id,
        "scenario_id": s.scenario_id,
        **s.card,
        "narrative": s.narrative,
        "origin": s.origin,
        "variant": s.generation_run.variant if s.generation_run_id else None,
        # Sorted in Python, not left to queryset ordering: `intended_hcvs` is
        # an unscoped M2M to `taxonomy.HCV` and could in principle span more
        # than one taxonomy, where HCV's own (order, hcv_id) ordering is not
        # guaranteed unique across taxonomies.
        "intended_hcvs": sorted(h.hcv_id for h in s.intended_hcvs.all()),
        "abox": s.abox,
        "generation_run": s.generation_run_id,
        "order": s.order,
        "created_by": s.created_by_id,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
    }


# `_seed_scenarios`/`_inputs` used to live here, reading `experiment
# .seed_systems`/`.fewshots`/`.target_system` LIVE — exactly the hole the
# whole-branch review proved: `PATCH {"seed_systems": [other]}` on a
# COMPLETED experiment changed what an already-finished run's export
# reported. There is no live equivalent anymore. What a run's inputs
# actually were is `PipelineRun.config`, pinned once at launch by
# `pipeline.execution_config.config_snapshot` and never re-read afterward —
# `build_bundle` surfaces it per-run on each `runs[]` entry, and
# `build_run_bundle` surfaces one run's own copy of it directly as `inputs`.


def _call_sums(phase_run_ids):
    """Per-phase-run token/cost totals, summed from `LLMCall` rows in ONE
    query (not per phase run in a loop, which would be an N+1). These are a
    convenience rollup for a quick read of `runs` only — the individual
    `calls` rows remain the source of truth for anything that needs to
    reconcile against them.
    """
    return {
        row["phase_run_id"]: row
        for row in LLMCall.objects.filter(phase_run_id__in=phase_run_ids)
        .values("phase_run_id")
        .annotate(
            prompt_tokens=Sum("prompt_tokens"),
            completion_tokens=Sum("completion_tokens"),
            cost=Sum("cost"),
        )
    }


def _runs(pipeline_runs, phase_run_ids):
    """One row per (pipeline run, phase run) pair.

    Takes the `PipelineRun`s directly (already fetched/scoped by the
    caller — `build_bundle` passes every repetition of one experiment,
    `build_run_bundle` passes a single one-element list) rather than an
    `experiment`, so this same function serves both bundle shapes.

    Does NOT carry `config` on these rows — a review round found it
    repeated identically across every phase-run row of the same pipeline
    run (2-4x, one per phase run), a meaningful fraction of a small
    bundle's size for a value that is often the largest single field in it.
    `build_bundle` carries it once per repetition instead, in its own
    `run_configs` section (keyed by `pipeline_run` id); `build_run_bundle`
    already carries the one run it covers at `inputs` and needs nothing
    extra here.
    """
    call_sums = _call_sums(phase_run_ids)
    rows = []
    for run in pipeline_runs:
        for phase_run in run.phase_runs.all():
            sums = call_sums.get(
                phase_run.pk, {"prompt_tokens": 0, "completion_tokens": 0, "cost": None}
            )
            rows.append(
                {
                    "pipeline_run": run.pk,
                    "repetition_index": run.repetition_index,
                    "pipeline_run_status": run.status,
                    "pipeline_run_started_at": run.started_at.isoformat()
                    if run.started_at
                    else None,
                    "pipeline_run_finished_at": run.finished_at.isoformat()
                    if run.finished_at
                    else None,
                    "phase_run": phase_run.pk,
                    "phase": phase_run.phase,
                    "variant": phase_run.variant,
                    "model": phase_run.model,
                    "temperature": phase_run.temperature,
                    "prompt_template": phase_run.prompt_template_id,
                    "taxonomy": phase_run.taxonomy_id,
                    # Which phase-2 run a phase-3 run labels. Not inferable
                    # from `scenarios`/`labels` alone when a phase-3 run
                    # produced zero labels (nothing to join back through).
                    "source_phase_run": phase_run.source_phase_run_id,
                    "status": phase_run.status,
                    "error": phase_run.error,
                    "total_children": phase_run.total_children,
                    "completed_children": phase_run.completed_children,
                    "failed_children": phase_run.failed_children,
                    # Sums of the per-call values in `calls`, which remain
                    # the source of truth for anything that must reconcile.
                    "prompt_tokens": sums["prompt_tokens"] or 0,
                    "completion_tokens": sums["completion_tokens"] or 0,
                    "cost": sums["cost"],
                    "created_by": phase_run.created_by_id,
                    "started_at": phase_run.started_at.isoformat()
                    if phase_run.started_at
                    else None,
                    "finished_at": phase_run.finished_at.isoformat()
                    if phase_run.finished_at
                    else None,
                }
            )
    return rows


def _phase_run_ids(experiment):
    return list(
        PhaseRun.objects.filter(pipeline_run__experiment=experiment).values_list("pk", flat=True)
    )


def _chosen_scenario_ids(phase_run_ids):
    """Scenarios a phase-3-only repetition labelled from the corpus."""
    chosen = set()
    for ids in PhaseRun.objects.filter(pk__in=phase_run_ids, scenario_ids__isnull=False).values_list(
        "scenario_ids", flat=True
    ):
        chosen.update(ids)
    return chosen


def _systems(phase_run_ids):
    # `SystemDescription.Meta.ordering = ["title"]` is not unique: every
    # repetition of one experiment generates a system from the same prompt,
    # so "Riverbend" (say) repeats verbatim across repetitions, and rows tied
    # on title alone sort in database-implementation-defined order — the same
    # class of bug `Label.Meta.ordering` gained an `id` tiebreaker for. Two
    # exports of an unchanged experiment must be byte-identical, so it is
    # closed here explicitly rather than left to chance.
    return [
        _system_row(s)
        for s in SystemDescription.objects.filter(
            Q(generation_run_id__in=phase_run_ids)
            | Q(scenarios__id__in=_chosen_scenario_ids(phase_run_ids))
        )
        .distinct()
        .select_related("generation_run")
        .order_by("title", "id")
    ]


def _scenarios(phase_run_ids):
    # Same tie as `_systems`, one level deeper: `Scenario.Meta.ordering =
    # ["system_description", "order", "scenario_id"]` expands the FK through
    # SystemDescription's own (non-unique) title, and `scenario_id` is only
    # unique WITHIN one system_description+generation_run — so two identical
    # repetitions produce a system titled "Riverbend" with a scenario
    # "sc_01"/order 1 each, a complete tie on every field this ordering
    # names. Ordered here on the raw `system_description_id` column instead
    # of the relation name (bypassing SystemDescription's own ordering
    # entirely — see `PipelineRunViewSet.get_queryset` for why the `_id`
    # attname and the bare relation name are not interchangeable), with `id`
    # as the final tiebreaker.
    return [
        _scenario_row(s)
        for s in Scenario.objects.filter(
            Q(generation_run_id__in=phase_run_ids) | Q(id__in=_chosen_scenario_ids(phase_run_ids))
        )
        .select_related("generation_run")
        .prefetch_related("intended_hcvs")
        .order_by("system_description_id", "order", "scenario_id", "id")
    ]


def _labels(phase_run_ids):
    return [
        {
            "id": label.pk,
            "phase_run": label.phase_run_id,
            "scenario": label.scenario_id,
            "scenario_id": label.scenario.scenario_id,
            "hcv_id": label.hcv.hcv_id,
            "hcv_name": label.hcv.name,
            "evidence": label.evidence,
            "rationale": label.rationale,
            "source": label.source,
            "llm_call": label.llm_call_id,
            # The human reviewer identity for the deferred Phase-4 work —
            # inter-rater agreement cannot be computed without it. None for
            # an LLM-sourced label.
            "created_by": label.created_by_id,
            "created_at": label.created_at.isoformat(),
        }
        for label in Label.objects.filter(phase_run_id__in=phase_run_ids).select_related(
            "hcv", "scenario"
        )
    ]


def _calls(phase_run_ids):
    return [
        {
            "id": call.pk,
            "phase_run": call.phase_run_id,
            "sequence": call.sequence,
            "scenario": call.scenario_id,
            "rendered_system_prompt": call.rendered_system_prompt,
            "rendered_user_prompt": call.rendered_user_prompt,
            "request_json": call.request_json,
            "response_json": call.response_json,
            "raw_response": call.raw_response,
            "parsed_ok": call.parsed_ok,
            "parse_error": call.parse_error,
            "unknown_hcv_ids": call.unknown_hcv_ids,
            "stray_scenarios": call.stray_scenarios,
            "http_status": call.http_status,
            "transport_error": call.transport_error,
            "model": call.model,
            "finish_reason": call.finish_reason,
            "prompt_tokens": call.prompt_tokens,
            "completion_tokens": call.completion_tokens,
            "cost": call.cost,
            "latency_ms": call.latency_ms,
            "leak_scan_ran": call.leak_scan_ran,
            "guarded_by": call.guarded_by_id,
            "created_at": call.created_at.isoformat(),
        }
        for call in LLMCall.objects.filter(phase_run_id__in=phase_run_ids)
    ]


def _ordered(mapping, key_order):
    """Rebuild `mapping` with `key_order`'s keys first, in that order, then
    any OTHER key `mapping` happens to carry (sorted, so even an
    unanticipated key's position is at least deterministic rather than
    whatever the database handed back) — never dropped, never raised on: if
    `execution_config`'s declared shape and `PipelineRun.config`'s actual
    shape ever drift, that is `validate_snapshot`'s job to catch at
    execution time, not this function's job to hide by silently discarding
    data from an archival export.
    """
    ordered = {key: mapping[key] for key in key_order if key in mapping}
    for key in sorted(k for k in mapping if k not in key_order):
        ordered[key] = mapping[key]
    return ordered


def _canonical_system(system):
    """One seed/target system from a snapshot (`execution_config
    ._snapshot_system`'s own shape), with its own keys AND every one of its
    `scenarios`' keys canonicalised — a system dict nested inside
    `run_configs` hits the exact same `jsonb`-reordering problem the top
    level does, one level down.
    """
    ordered = _ordered(system, _SYSTEM_KEY_ORDER)
    ordered["scenarios"] = [_ordered(s, _SCENARIO_KEY_ORDER) for s in ordered["scenarios"]]
    return ordered


def _canonical_run_config(config):
    """Rebuild a `PipelineRun.config` snapshot with a fixed, canonical key
    order at EVERY nesting level it has, instead of trusting whatever order
    the database handed back.

    Why this is needed at all: `PipelineRun.config` is a Django `JSONField`,
    which maps to `jsonb` on PostgreSQL. `jsonb` does not preserve an
    object's key insertion order — it reorders keys on write — while
    SQLite's JSON1 storage happens to preserve it. `execution_config
    .config_snapshot` writes this dict's keys, and every nested system's and
    scenario's and few-shot's keys, in one fixed, deliberate order every
    time (see that function and `_snapshot_system`/`_snapshot_scenario`) —
    but by the time a row comes back OUT of a `jsonb` column, that order is
    gone; PostgreSQL hands back its own internal ordering instead, and two
    exports of the identical experiment produce different bytes the moment
    one export is read from SQLite (dev) and the other from PostgreSQL
    (production, see `docker-compose.yml`) — even though every VALUE is
    identical.

    The order restored here is `config_snapshot`'s OWN construction order —
    `execution_config._TOP_LEVEL_KEY_ORDER`/`_SYSTEM_KEY_ORDER`/
    `_SCENARIO_KEY_ORDER`/`_FEWSHOT_KEY_ORDER`, the exact tuples
    `validate_snapshot`'s exact-key-set checks are themselves built from —
    deliberately, not an arbitrary alternative such as alphabetical: it is
    the order SQLite's insertion-preserving JSON1 already returns today, so
    it is also the order this module's own CSV-header test already pins
    (`test_the_csv_zip_pins_the_exact_header_row_of_every_member`). Reusing
    it means PostgreSQL is brought INTO agreement with the one backend this
    was already correct on, rather than redefining "correct" for either.

    Every nesting level the snapshot has gets this treatment, not just the
    top: a seed/target system's own keys AND its `scenarios`' keys
    (`_canonical_system`), and every few-shot's keys. `jsonb` reorders an
    object's keys at every level of a nested document it stores, not only
    the outermost one — canonicalising only `config`'s own top-level keys
    would still leave a backend-dependent order inside every system,
    scenario, and few-shot nested underneath it, in both the JSON bundle
    and the JSON blob `_run_configs_csv_rows`/`_as_rows` write into a single
    CSV cell (where key order changes the cell's bytes just as much as it
    changes the JSON body's).
    """
    ordered = _ordered(config, _TOP_LEVEL_KEY_ORDER)
    ordered["seed_systems"] = [_canonical_system(s) for s in ordered["seed_systems"]]
    ordered["target_system"] = (
        _canonical_system(ordered["target_system"]) if ordered["target_system"] else None
    )
    ordered["fewshots"] = [_ordered(f, _FEWSHOT_KEY_ORDER) for f in ordered["fewshots"]]
    return ordered


def _run_config_or_none(config):
    """`None`, explicitly, for a run with no snapshot at all — never a bare
    `{}` a reader could mistake for "this run's config was genuinely empty".

    `PipelineRun.config` defaults to `{}`; the ONLY code path that ever
    writes anything else is `launch()` via `execution_config.config_snapshot`,
    which always writes all seven keys (empty lists/`None` for an unset
    one, but never an empty dict outright). So a bare `{}` can only mean one
    thing: this row predates the migration that added the field, or was
    created some other way that bypassed `launch()` — "never snapshotted",
    not "snapshotted with nothing in it".

    A non-empty config is run through `_canonical_run_config` before it goes
    anywhere near `json.dumps`/`csv.DictWriter` — see that function's own
    docstring for why a bare, un-reordered `PipelineRun.config` read back
    from PostgreSQL cannot be trusted to match what SQLite would have
    produced for the identical snapshot.
    """
    return _canonical_run_config(config) if config else None


def build_bundle(experiment):
    """Everything needed to reconstruct this experiment without the database.

    Covers every repetition this experiment has ever run. `experiment_current`
    is the experiment's CURRENT, live, editable configuration — named that way
    deliberately, so it is never mistaken for what any one run actually used.
    That is `run_configs[pipeline_run_id]`: each repetition's own pinned
    snapshot, which can legitimately differ from another repetition's if the
    experiment was edited between two launches (see `PipelineRun.config`).
    Carried ONCE per repetition here, in its own section, rather than
    repeated on every one of that repetition's phase-run rows in `runs[]`
    (a review round found that repeated a meaningful fraction of a small
    bundle's total size for what is often its single largest field).

    There is deliberately no single, experiment-wide `inputs` section here —
    with N repetitions potentially pinning N different configurations, no
    one section could represent "the" inputs without arbitrarily picking
    one and silently losing the rest. `build_run_bundle` (one repetition)
    does carry that singular `inputs`, because it is unambiguous there.
    """
    phase_run_ids = _phase_run_ids(experiment)
    # Materialised, not left as a lazy queryset: `_runs` below iterates it
    # fully (populating Django's own queryset result cache), so the second
    # iteration for `run_configs` costs no extra query either way — `list()`
    # here just makes that explicit rather than relying on the caching
    # behaviour being understood by whoever reads this next.
    pipeline_runs = list(
        PipelineRun.objects.filter(experiment=experiment).prefetch_related("phase_runs")
    )
    return {
        "experiment_current": _experiment(experiment),
        "taxonomy": _taxonomy(phase_run_ids),
        "prompts": _prompts(phase_run_ids),
        "runs": _runs(pipeline_runs, phase_run_ids),
        "run_configs": {run.pk: _run_config_or_none(run.config) for run in pipeline_runs},
        "systems": _systems(phase_run_ids),
        "scenarios": _scenarios(phase_run_ids),
        "labels": _labels(phase_run_ids),
        "calls": _calls(phase_run_ids),
    }


def build_run_bundle(run):
    """Everything needed to reconstruct ONE repetition without the database.

    Scoped to `run`'s own phase runs only — never another repetition's — and
    to `run.config`, the configuration PINNED at launch, surfaced here as
    `inputs` — `None`, explicitly, rather than a bare `{}`, for a run that
    predates the snapshot feature (see `_run_config_or_none`). Never the
    experiment's current, live fields: that is what let a `PATCH` to a
    completed experiment change what its export reported (see
    `PipelineRun.config`'s docstring). `experiment_current` is included too,
    the same live section `build_bundle` carries, purely for a reader's
    context — it is not what this run used.

    Reuses every per-`phase_run_ids` helper `build_bundle` already has
    (`_taxonomy`/`_prompts`/`_systems`/`_scenarios`/`_labels`/`_calls`/
    `_runs`) rather than a second, parallel assembly — only the scope
    (this run's phase runs, not every phase run of the experiment) differs.
    """
    phase_run_ids = list(PhaseRun.objects.filter(pipeline_run=run).values_list("pk", flat=True))
    return {
        "experiment_current": _experiment(run.experiment),
        "taxonomy": _taxonomy(phase_run_ids),
        "prompts": _prompts(phase_run_ids),
        "inputs": _run_config_or_none(run.config),
        "runs": _runs([run], phase_run_ids),
        "systems": _systems(phase_run_ids),
        "scenarios": _scenarios(phase_run_ids),
        "labels": _labels(phase_run_ids),
        "calls": _calls(phase_run_ids),
    }


def _as_rows(section):
    """Flatten a section to CSV rows. Nested values become compact JSON.

    Fieldnames are the UNION of every row's keys, in first-seen order — not
    just `rows[0]`'s. Concatenating two different shapes into one section
    used to raise `ValueError: dict contains fields not in fieldnames` from
    `csv.DictWriter.writerows` (a live 500) the moment the first row's keys
    didn't cover a later row's — `inputs`'s seed systems vs. few-shots was
    one such pair; splitting them into separate files (see `write_csv_zip`)
    fixes that specific case, but this keeps the NEXT heterogeneous section
    from hitting the same bug rather than trusting every future section to
    stay uniform by convention.

    `None` (a run-level export's `inputs` for a run with no snapshot at
    all — see `_run_config_or_none`) writes zero rows, the same as an empty
    list — matching `_write_csv`'s existing "an empty section writes no
    header line at all" convention rather than raising on `for entry in
    None`.
    """
    if section is None:
        return [], []
    if isinstance(section, dict):
        section = [section]
    rows = []
    fieldnames = []
    seen = set()
    for entry in section:
        row = {
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            for key, value in entry.items()
        }
        rows.append(row)
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    return rows, fieldnames


def _write_csv(archive, name, section):
    rows, fieldnames = _as_rows(section)
    text = io.StringIO()
    if rows:
        writer = csv.DictWriter(text, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    archive.writestr(f"{name}.csv", text.getvalue())


def _taxonomy_csv_rows(taxonomies):
    """Flatten every pinned taxonomy's HCV rows into one CSV.

    The nested JSON shape (`[{"id", "vocabulary_id", "version", ..., "rows":
    [...]}]`) loses which taxonomy a row belongs to once flattened — carrying
    `taxonomy_id`/`vocabulary_id`/`version` on every row keeps that join
    intact in a flat file.
    """
    return [
        {
            "taxonomy_id": taxonomy["id"],
            "vocabulary_id": taxonomy["vocabulary_id"],
            "version": taxonomy["version"],
            **row,
        }
        for taxonomy in taxonomies
        for row in taxonomy["rows"]
    ]


def _run_configs_csv_rows(run_configs):
    """Flatten `run_configs` (`{pipeline_run_id: config-or-None}`) into one
    row per repetition, tagged with which one it came from — the same join
    problem `_taxonomy_csv_rows` solves for taxonomy rows, one level up. A
    `None` config (no snapshot at all — see `_run_config_or_none`) stays a
    `None` value in its own `config` column rather than silently vanishing
    into blank cells across every OTHER column, so "never snapshotted" and
    "snapshotted, nothing in it" both stay distinguishable after flattening.
    """
    rows = []
    for pipeline_run_id, config in run_configs.items():
        if config is None:
            rows.append({"pipeline_run": pipeline_run_id, "config": None})
        else:
            rows.append({"pipeline_run": pipeline_run_id, **config})
    return rows


def write_csv_zip(bundle):
    """One CSV per section, zipped. Long columns are written in full.

    Iterates whatever sections `bundle` actually has, in the order
    `build_bundle`/`build_run_bundle` built it — the two shapes differ (only
    `build_bundle`'s carries `run_configs`; only `build_run_bundle`'s
    carries a singular `inputs`), and this stays correct for either without
    needing to know which one it was handed. `taxonomy` and `run_configs`
    are the two sections flattened first (see `_taxonomy_csv_rows`/
    `_run_configs_csv_rows`): both are dict/list shapes that would otherwise
    either lose which taxonomy/run a row belongs to, or (for `run_configs`,
    a dict KEYED by id) turn into one bizarre row with pipeline_run ids as
    column names. Every other section — `inputs` included, a single dict
    for one run's pinned config, nested `scenarios` and all — goes through
    the generic path: `_as_rows` already turns a bare dict into one row and
    JSON-encodes any nested value into its cell, the same way
    `experiment_current`'s `variants` list already was.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, section in bundle.items():
            if name == "taxonomy":
                _write_csv(archive, "taxonomy", _taxonomy_csv_rows(section))
            elif name == "run_configs":
                _write_csv(archive, "run_configs", _run_configs_csv_rows(section))
            else:
                _write_csv(archive, name, section)
    return buffer.getvalue()
