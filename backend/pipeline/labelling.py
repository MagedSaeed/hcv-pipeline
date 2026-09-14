"""Phase 3 — label one scenario against the pinned taxonomy.

One scenario per call, so a single bad scenario fails only itself. A label
outside the pinned list is recorded on the call rather than dropped: how often
a labeller invents an identifier is itself a result.
"""

from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from corpus.models import Scenario
from llm.schemas import (
    Phase3Response,
    SchemaError,
    json_schema,
    loads_response,
    parse_failure_message,
    parse_phase3,
    split_known_labels,
)
from pipeline.clients import get_llm_client
from pipeline.execution_config import resolve_labelling_fewshots
from pipeline.models import Label, LLMCall, PhaseRun
from prompts.rendering import (
    PromptRenderError,
    TaxonomyLeak,
    build_phase3_context,
    render,
)


def scenarios_for(phase_run):
    """The scenarios this run labels: one Phase 2 run's output, or everything.

    An ORCHESTRATED run (one with a pipeline_run) always had a source at
    launch — `source_phase_run` is SET_NULL on delete, so a null source on a
    run that HAS a pipeline_run means the source was deleted afterward, not
    that this run was ever meant to label the whole corpus. Falling through
    to "everything" there would let one deleted phase-2 run silently turn
    its labelling counterpart into a fan-out over every OTHER variant's
    scenarios too — the exact isolation this design exists to keep. An
    ad-hoc run (no pipeline_run) with no source genuinely does mean "label
    everything", and keeps that behaviour.

    An ad-hoc run created with a chosen subset (`scenario_ids`) labels exactly
    those that still exist — never everything, even if all were deleted since.
    """
    if phase_run.scenario_ids is not None and not phase_run.pipeline_run_id:
        return Scenario.objects.filter(id__in=phase_run.scenario_ids)
    if phase_run.source_phase_run_id:
        return Scenario.objects.filter(generation_run_id=phase_run.source_phase_run_id)
    if phase_run.pipeline_run_id:
        return Scenario.objects.none()
    return Scenario.objects.all()


def _fewshots(phase_run):
    """This run's active phase-3 few-shots, from its PINNED config snapshot
    — never the experiment's live fields. See `pipeline.execution_config`'s
    module docstring: reading these live let an operator's edit while a run
    sat QUEUED silently change what got rendered, contradicting the run's
    own archived `inputs`/`rendered_user_prompt`.
    """
    return resolve_labelling_fewshots(phase_run)


@transaction.atomic
def _persist(phase_run, scenario, known, by_id, call=None):
    """Write the labels, each pointing at the call that produced it.

    `call` is passed through rather than looked up later: spec §9 requires the
    export to name the call behind every label, and the (phase_run, scenario)
    join that would otherwise derive it stops being unique the moment a call is
    duplicated. It cannot be backfilled after the fact either, so it is written
    here or never.
    """
    for entry in known:
        Label.objects.get_or_create(
            phase_run=phase_run,
            scenario=scenario,
            hcv=by_id[entry.hcv_id],
            defaults={
                "evidence": entry.evidence,
                "rationale": entry.rationale,
                "llm_call": call,
            },
        )


def label_scenario(phase_run, scenario, sequence=1):
    """Label one scenario. Returns the archived call; never raises on provider error."""
    try:
        context = build_phase3_context(
            scenario=scenario,
            system_description=scenario.system_description,
            taxonomy=phase_run.taxonomy,
            fewshots=_fewshots(phase_run),
        )
        rendered = render(phase_run.prompt_template, context)
    except (TaxonomyLeak, PromptRenderError) as error:
        return LLMCall.objects.create(
            phase_run=phase_run,
            sequence=sequence,
            scenario=scenario,
            parse_error=f"{type(error).__name__}: {error}",
        )

    result = get_llm_client().complete(
        model=phase_run.model,
        system_prompt=rendered.system,
        user_prompt=rendered.user,
        temperature=phase_run.temperature,
        json_schema=phase_run.prompt_template.output_schema or json_schema(Phase3Response),
    )

    call = LLMCall(
        phase_run=phase_run,
        sequence=sequence,
        scenario=scenario,
        rendered_system_prompt=rendered.system,
        rendered_user_prompt=rendered.user,
        leak_scan_ran=rendered.leak_scan_ran,
        guarded_by_id=rendered.guarded_by,
        request_json=result.request_json,
        response_json=result.response_json,
        raw_response=result.raw_response,
        http_status=result.http_status,
        transport_error=result.transport_error,
        model=result.model,
        finish_reason=result.finish_reason,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        cost=result.cost,
        latency_ms=result.latency_ms,
    )
    if not result.ok:
        call.save()
        return call

    try:
        parsed = parse_phase3(loads_response(result.raw_response))
    except (ValueError, SchemaError) as error:
        call.parse_error = parse_failure_message(
            error, result.finish_reason, result.completion_tokens
        )
        call.save()
        return call

    # Built once, from a single snapshot: `allowed` is derived from `by_id`
    # rather than a second, separate query, so a taxonomy re-import or an
    # admin deletion racing between the split and the persist cannot leave
    # `known` holding an hcv_id that `by_id` no longer has (see I1 — that
    # KeyError previously escaped label_scenario entirely).
    by_id = {row.hcv_id: row for row in phase_run.taxonomy.hcvs.all()}
    known, unknown = split_known_labels(parsed, set(by_id))
    call.parsed_ok = True
    call.unknown_hcv_ids = unknown
    call.save()

    try:
        _persist(phase_run, scenario, known, by_id, call=call)
    except DatabaseError as error:
        # A persistence failure must not escape label_scenario (see module
        # docstring / Task 3's Critical). `parsed_ok` stays True: it is the
        # schema-validation signal documented on LLMCall (and read by the
        # admin's list_filter) — "did the response parse against the pinned
        # schema" — and a later infrastructure failure must not corrupt that
        # research output by masquerading as a parse failure. The failure is
        # recorded in `parse_error` instead, and `failed_calls` below is what
        # makes finish_labelling_run still count this as a failed child.
        call.parse_error = f"Could not persist the labels: {error}"
        call.save()

    return call


def failed_calls(phase_run):
    """A child failed if it did not parse, or parsed but left an error behind.

    Kept as one predicate so finish_labelling_run and retry_failed cannot
    drift. `parsed_ok` stays the schema-validation signal it is documented to
    be: a persistence failure is recorded in `parse_error`, not by lying
    about parsing.
    """
    return phase_run.calls.filter(Q(parsed_ok=False) | ~Q(parse_error=""))


def finish_labelling_run(phase_run):
    """Roll the fan-out up into one terminal status.

    An orchestrated run whose source phase run was deleted after launch
    (see `scenarios_for`) fans out over nothing at all — zero calls. Left to
    the count below, that reads as indistinguishable from a legitimate run
    that simply had no calls fail: a silent COMPLETED for work that never
    ran. Caught here, once, rather than by every caller.

    `completed_children` is recomputed fresh from the calls table here too,
    not just `failed_children` — `run_labelling`'s loop already keeps both
    live as it goes, but `retry_failed` re-labels a subset of scenarios
    without touching either counter itself, relying entirely on this
    reconciliation to catch a scenario up from failed to completed.

    Re-checks CANCELLED from the database, not the possibly-stale in-memory
    `phase_run` passed in: this is the function that writes the FINAL
    status of a fan-out `run_labelling` just finished, and an operator's
    `POST .../cancel/` can land in the database at any point while that
    fan-out is still running (the cancel action's own caveat: it does not
    interrupt a worker already executing, so the worker's remaining writes —
    individual `LLMCall` rows, `completed_children`/`failed_children` — may
    still land). What must never happen is the run's FINAL status silently
    flipping away from CANCELLED to COMPLETED/FAILED here — that would
    misreport a deliberately-cancelled run as one that ran to completion or
    failed on its own, a different, wrong fact about what happened. Every
    other caller of `retry_failed` (the only other place that reaches this
    function) can never reach it with a CANCELLED `phase_run` in the first
    place — `retry_failed` refuses a CANCELLED run outright, with its own
    `RunCancelled`, before it ever gets this far — but this checks directly
    rather than relying on that being true of every future caller too.
    Deliberately does NOT use `phase_run.is_terminal` for this (that
    property now includes CANCELLED, precisely so `run_phase2`/
    `run_labelling` hard-stop on it) — `retry_failed`'s whole point is to
    re-derive a NEW terminal status for an already COMPLETED/FAILED run,
    which a blanket `is_terminal` guard here would block.
    """
    phase_run.refresh_from_db(fields=["status"])
    if phase_run.status == PhaseRun.Status.CANCELLED:
        return phase_run
    if phase_run.pipeline_run_id and not phase_run.source_phase_run_id:
        phase_run.status = PhaseRun.Status.FAILED
        phase_run.error = "Source phase run was deleted; nothing to label."
        phase_run.failed_children = 0
        phase_run.finished_at = timezone.now()
        phase_run.save(update_fields=["status", "error", "failed_children", "finished_at"])
        return phase_run
    if phase_run.scenario_ids is not None and not phase_run.calls.exists():
        phase_run.status = PhaseRun.Status.FAILED
        phase_run.error = "The chosen scenarios were deleted; nothing to label."
        phase_run.failed_children = 0
        phase_run.finished_at = timezone.now()
        phase_run.save(update_fields=["status", "error", "failed_children", "finished_at"])
        return phase_run

    failed = failed_calls(phase_run).count()
    phase_run.completed_children = phase_run.calls.filter(parsed_ok=True, parse_error="").count()
    phase_run.failed_children = failed
    phase_run.finished_at = timezone.now()
    if failed:
        phase_run.status = PhaseRun.Status.FAILED
        phase_run.error = f"{failed} scenario(s) failed to label."
    else:
        phase_run.status = PhaseRun.Status.COMPLETED
        phase_run.error = ""
    phase_run.save(
        update_fields=["status", "error", "completed_children", "failed_children", "finished_at"]
    )
    return phase_run
