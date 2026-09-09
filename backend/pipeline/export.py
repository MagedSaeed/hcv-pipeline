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
"""

import csv
import io
import json
import re
import zipfile

from django.db.models import Sum

from corpus.models import Scenario, SystemDescription
from pipeline.models import Label, LLMCall, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import Taxonomy

SECTIONS = (
    "experiment",
    "taxonomy",
    "prompts",
    "inputs",
    "runs",
    "systems",
    "scenarios",
    "labels",
    "calls",
)

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
        "phase2_model": experiment.phase2_model,
        "phase2_temperature": experiment.phase2_temperature,
        "phase3_model": experiment.phase3_model,
        "phase3_temperature": experiment.phase3_temperature,
        "generate_systems": experiment.generate_systems,
        "generate_scenarios": experiment.generate_scenarios,
        "n_systems": experiment.n_systems,
        "n_scenarios_per_system": experiment.n_scenarios_per_system,
        "variants": experiment.variants,
        "created_by": experiment.created_by_id,
        "created_at": experiment.created_at.isoformat(),
    }


def _taxonomy(phase_run_ids):
    """Every taxonomy actually pinned by one of this experiment's phase runs,
    verbatim.

    Driven from `PhaseRun.taxonomy_id` — the REAL pin — not
    `experiment.taxonomy`, which is a plain, editable FK. An operator can
    `PATCH` an experiment's `taxonomy` (or `phase2_prompt`/`phase3_prompt`,
    see `_prompts`) after it has already run; the archive must still say what
    a run actually used, not what the experiment currently points at, or a
    re-pinned experiment ships a bundle whose `labels` cite HCV ids from a
    taxonomy the bundle never lists. Always a list: an experiment edited
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
    `experiment.phase2_prompt`/`phase3_prompt`. Those are plain, editable
    FKs: `PATCH {"phase2_prompt": <new>}` on an experiment that has already
    run succeeds (nothing freezes them), and reading them here would then
    ship the WRONG wording — the text that produced the archived results
    would be absent from the very record meant to preserve it. See
    `_taxonomy` for the identical reasoning. Always covers every distinct
    template id pinned across every phase run, not just two.
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
    by `_systems` and `_inputs`'s `seed_systems`/`target_system` so every
    place a system appears in the bundle carries the same fields.
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
    `_scenarios` and `_inputs`'s `seed_scenarios` for the same reason as
    `_system_row`.
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


def _seed_scenarios(experiment):
    """The Phase-1 expert seed pack attached to this experiment's inputs.

    `_scenarios` only covers what THIS experiment's own runs generated
    (`generation_run_id__in=phase_run_ids`) — without this, the seed pack,
    the material the whole experiment is anchored on, is invisible in the
    archive beyond its seed systems' bare title and body, and scoring a
    generated label against the expert ground truth is impossible from the
    bundle alone. Scoped to `generation_run__isnull=True` so a scenario THIS
    experiment generated against its own `target_system` (already covered by
    `_scenarios`) is not duplicated here.
    """
    system_ids = set(experiment.seed_systems.values_list("id", flat=True))
    if experiment.target_system_id:
        system_ids.add(experiment.target_system_id)
    if not system_ids:
        return []
    return [
        _scenario_row(s)
        for s in Scenario.objects.filter(
            system_description_id__in=system_ids, generation_run__isnull=True
        )
        .select_related("generation_run")
        .prefetch_related("intended_hcvs")
        .order_by("system_description_id", "order", "scenario_id", "id")
    ]


def _inputs(experiment):
    return {
        "seed_systems": [
            _system_row(s)
            for s in experiment.seed_systems.select_related("generation_run").order_by(
                "title", "id"
            )
        ],
        "seed_scenarios": _seed_scenarios(experiment),
        "fewshots": [
            {
                "id": f.pk,
                "phase": f.phase,
                "name": f.name,
                "input": f.resolved_input,
                "output": f.resolved_output,
            }
            for f in experiment.fewshots.all()
        ],
        "target_system": (
            _system_row(experiment.target_system) if experiment.target_system_id else None
        ),
    }


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


def _runs(experiment, phase_run_ids):
    call_sums = _call_sums(phase_run_ids)
    rows = []
    for run in PipelineRun.objects.filter(experiment=experiment).prefetch_related("phase_runs"):
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
        for s in SystemDescription.objects.filter(generation_run_id__in=phase_run_ids)
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
        for s in Scenario.objects.filter(generation_run_id__in=phase_run_ids)
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


def build_bundle(experiment):
    """Everything needed to reconstruct this experiment without the database."""
    phase_run_ids = _phase_run_ids(experiment)
    return {
        "experiment": _experiment(experiment),
        "taxonomy": _taxonomy(phase_run_ids),
        "prompts": _prompts(phase_run_ids),
        "inputs": _inputs(experiment),
        "runs": _runs(experiment, phase_run_ids),
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
    """
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


def write_csv_zip(bundle):
    """One CSV per section, zipped. Long columns are written in full.

    `inputs` is split into one file per input KIND (seed systems, seed
    scenarios, few-shots, target system) rather than a single `inputs.csv`:
    they are genuinely different shapes, and concatenating them used to raise
    `ValueError: dict contains fields not in fieldnames` — a live 500 — the
    moment an experiment had both a seed system and a few-shot, since every
    fixture in this test suite happened to have zero few-shots and never hit
    it. `taxonomy` is flattened the same way, for the same reason (see
    `_taxonomy_csv_rows`).
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in SECTIONS:
            if name == "taxonomy":
                _write_csv(archive, "taxonomy", _taxonomy_csv_rows(bundle["taxonomy"]))
            elif name == "inputs":
                inputs = bundle["inputs"]
                _write_csv(archive, "inputs_seed_systems", inputs["seed_systems"])
                _write_csv(archive, "inputs_seed_scenarios", inputs["seed_scenarios"])
                _write_csv(archive, "inputs_fewshots", inputs["fewshots"])
                target = inputs["target_system"]
                _write_csv(archive, "inputs_target_system", [target] if target else [])
            else:
                _write_csv(archive, name, bundle[name])
    return buffer.getvalue()
