"""Phase 2 — generate scenario material under one variant.

The run reaches a terminal status whatever happens. A provider failure, a
malformed body and a schema violation are all recorded on the call and end the
run as FAILED — none of them raises, because a run that dies mid-flight leaves
no evidence of what went wrong.
"""

import json

from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.text import slugify

from corpus.models import Origin, Scenario, SystemDescription
from llm.schemas import Phase2Response, SchemaError, json_schema, parse_phase2
from pipeline.clients import get_llm_client
from pipeline.models import LLMCall, PhaseRun
from prompts.rendering import (
    VARIANT_UNBIASED,
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    render,
)


def _experiment_for(phase_run):
    """The configuration behind this run, or None for an ad-hoc run."""
    if phase_run.pipeline_run_id:
        return phase_run.pipeline_run.experiment
    return None


def _build_context(phase_run, experiment):
    """Always via the builder — never a hand-assembled dict (see plan 2)."""
    unbiased = phase_run.is_unbiased
    seeds = experiment.seed_systems.all() if experiment else []
    fewshots = experiment.fewshots.filter(phase=2, is_active=True) if experiment else []
    return build_phase2_context(
        variant=phase_run.variant or VARIANT_UNBIASED,
        seed_systems=list(seeds),
        fewshots=list(fewshots),
        generate_systems=experiment.generate_systems if experiment else True,
        generate_scenarios=experiment.generate_scenarios if experiment else True,
        n_systems=experiment.n_systems if experiment else 3,
        n_scenarios_per_system=experiment.n_scenarios_per_system if experiment else 5,
        target_system=experiment.target_system if experiment else None,
        # A never receives the taxonomy; it is passed only so the leak backstop
        # has a reference list to scan the finished prompt against.
        taxonomy=None if unbiased else phase_run.taxonomy,
        guard_taxonomy=phase_run.taxonomy if unbiased else None,
    )


def _fail(phase_run, message):
    """Every exit through here is a failed run — and a failed run is one
    failed call, not a call that vanished from the count. total_children is
    set here too (not just on the RUNNING transition in run_phase2) because
    the earliest failure — the blank-variant refusal — never reaches that
    line at all; without it that path leaves total_children at 0 forever,
    the same "invisible failure" bug this fixes for every other exit.
    """
    phase_run.status = PhaseRun.Status.FAILED
    phase_run.error = message
    phase_run.total_children = 1
    phase_run.failed_children = 1
    phase_run.finished_at = timezone.now()
    phase_run.save(
        update_fields=["status", "error", "total_children", "failed_children", "finished_at"]
    )
    return phase_run


def _narrative(card):
    return "\n".join(card.narrative_steps)


def _persist_error(parsed, experiment):
    """Why this response cannot be honoured, or None if it can be.

    A model's output and the run's configuration can disagree in ways that
    are easy to miss if `_persist` is simply left to do its best:

    - the experiment says not to generate systems, but the response carries
      system_descriptions anyway — the prompt and the configuration disagree,
      and honouring one of them silently hides that;
    - the response carries a bare scenarios list but there is no
      target_system to attach it to — generated material with nowhere to go
      must not simply vanish;
    - the response carries neither — an empty body that would otherwise end
      the run COMPLETED having persisted nothing, with no record that
      anything was wrong;
    - the experiment asked for scenarios but the response carries none, only
      systems with empty scenario lists — the run would end COMPLETED with
      nothing for phase 3 to label, reporting a repetition that contributes
      zero labels to the measurement as a success.

    Each is reported by name rather than either dropped silently or left to
    surface as a confusing downstream symptom.
    """
    generate_systems = experiment.generate_systems if experiment else True
    generate_scenarios = experiment.generate_scenarios if experiment else True
    target = experiment.target_system if experiment else None

    if not generate_systems and parsed.system_descriptions:
        return (
            "generate_systems is False for this experiment, but the response "
            "included system_descriptions. The prompt and the configuration "
            "disagree."
        )
    if parsed.scenarios and target is None:
        return (
            "The response included a bare scenarios list, but there is no "
            "target_system to attach them to."
        )
    if not parsed.system_descriptions and not parsed.scenarios:
        return "The response contained no material to persist."
    scenario_count = len(parsed.scenarios) + sum(
        len(system.scenarios) for system in parsed.system_descriptions
    )
    if generate_scenarios and not scenario_count:
        return (
            "generate_scenarios is True for this experiment, but the response "
            "contained no scenarios at all. There would be nothing for phase 3 "
            "to label, so this is not a completed repetition."
        )
    return None


@transaction.atomic
def _persist(phase_run, parsed, experiment):
    """Write the generated material. Everything lands as origin=generated.

    Wrapped in one atomic block so a mid-write failure (a duplicate
    scenario_id, an over-length field) leaves nothing half-persisted — the
    caller catches the DatabaseError and rolls this transaction back with it.
    """
    order = 0
    for index, system in enumerate(parsed.system_descriptions, start=1):
        row = SystemDescription.objects.create(
            title=system.title,
            body=system.body,
            # Named here rather than left to SystemDescription._unique_slug,
            # which is SELECT-then-INSERT with no lock: concurrent repetitions
            # generating the same title race on it, and the loser dies on an
            # IntegrityError that fails the phase run, CANCELS its labelling
            # counterpart and loses the whole repetition. A slug scoped to the
            # run that produced the row cannot collide with another run's in
            # the first place.
            slug=f"{slugify(system.title) or 'system'}-r{phase_run.pk}-{index}",
            origin=Origin.GENERATED,
            generation_run=phase_run,
        )
        for card in system.scenarios:
            order += 1
            Scenario.objects.create(
                system_description=row,
                scenario_id=card.scenario_id,
                **{field: getattr(card, field) for field in Scenario.CARD_FIELDS},
                narrative=_narrative(card),
                origin=Origin.GENERATED,
                generation_run=phase_run,
                order=order,
            )

    # _persist_error has already ruled out a non-empty scenarios list with no
    # target, so target is guaranteed not None here whenever there is
    # anything in parsed.scenarios to write.
    target = experiment.target_system if experiment else None
    if parsed.scenarios:
        for card in parsed.scenarios:
            order += 1
            Scenario.objects.create(
                system_description=target,
                scenario_id=card.scenario_id,
                **{field: getattr(card, field) for field in Scenario.CARD_FIELDS},
                narrative=_narrative(card),
                origin=Origin.GENERATED,
                generation_run=phase_run,
                order=order,
            )


def run_phase2(phase_run):
    """Render, call, parse and persist. Always ends in a terminal status.

    Re-entrant only in the sense that a run which already reached a terminal
    status is returned untouched — matching `execute_pipeline_run`'s guard.
    Actually re-executing one would duplicate every system and scenario it
    produced and archive a second call against it, double-counting its tokens
    and cost.
    """
    if phase_run.is_terminal:
        return phase_run
    if phase_run.phase == 2 and not phase_run.variant:
        # Without this, `_build_context` injects the pinned taxonomy (the run
        # is not `is_unbiased`) while telling the builder the variant is A (the
        # `or VARIANT_UNBIASED` fallback) — which fails closed, but on a
        # baffling TaxonomyLeak about a variant nobody chose.
        return _fail(
            phase_run,
            "This phase 2 run has no variant set. Generation is always run as "
            "variant A (unbiased, taxonomy withheld) or B (guided, taxonomy "
            "supplied); there is no unmarked generation.",
        )

    experiment = _experiment_for(phase_run)
    phase_run.status = PhaseRun.Status.RUNNING
    phase_run.started_at = timezone.now()
    # One call makes the whole run: total_children is always 1 from here, and
    # completed_children flips to 1 only once that one call actually succeeds
    # (see the end of _call_and_persist).
    phase_run.total_children = 1
    phase_run.save(update_fields=["status", "started_at", "total_children"])

    try:
        context = _build_context(phase_run, experiment)
        rendered = render(phase_run.prompt_template, context)
    except (TaxonomyLeak, PromptRenderError) as error:
        return _fail(phase_run, f"{type(error).__name__}: {error}")

    try:
        return _call_and_persist(phase_run, rendered, experiment)
    except Exception as error:  # noqa: BLE001 — nothing may strand this run
        # Deliberately as broad as run_labelling's guard, and for the same
        # reason. The client call raises MissingAPIKey when no key is
        # configured and SecretLeak when a key-shaped string reaches the
        # request body; either escaping leaves this phase run stuck at RUNNING
        # with its PipelineRun stuck RUNNING too — and a second
        # execute_pipeline_run then hits the idempotency guard and merely
        # re-rolls it up, so the repetition can never be re-driven at all. A
        # run that dies mid-flight leaves no evidence of what went wrong;
        # every exit from here leaves a terminal status and a message.
        return _fail(phase_run, f"{type(error).__name__}: {error}")


def _call_and_persist(phase_run, rendered, experiment):
    """Call the provider, archive it, and write what came back.

    Every failure this can anticipate is recorded and returned as a FAILED run;
    anything it cannot is caught by the broad guard in `run_phase2`.
    """
    result = get_llm_client().complete(
        model=phase_run.model,
        system_prompt=rendered.system,
        user_prompt=rendered.user,
        temperature=phase_run.temperature,
        json_schema=phase_run.prompt_template.output_schema or json_schema(Phase2Response),
    )

    call = LLMCall(
        phase_run=phase_run,
        sequence=1,
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
        return _fail(phase_run, result.transport_error or "The call returned no body.")

    try:
        parsed = parse_phase2(json.loads(result.raw_response))
    except (ValueError, SchemaError) as error:
        call.parse_error = str(error)
        call.save()
        return _fail(phase_run, f"Could not parse the response: {error}")

    call.parsed_ok = True
    call.save()

    reason = _persist_error(parsed, experiment)
    if reason is not None:
        return _fail(phase_run, reason)

    try:
        _persist(phase_run, parsed, experiment)
    except DatabaseError as error:
        return _fail(phase_run, f"Could not persist the generated material: {error}")
    except Exception as error:  # noqa: BLE001 — a half-written run must still be terminal
        return _fail(
            phase_run,
            f"Could not persist the generated material: {type(error).__name__}: {error}",
        )

    phase_run.status = PhaseRun.Status.COMPLETED
    phase_run.completed_children = 1
    phase_run.finished_at = timezone.now()
    phase_run.save(update_fields=["status", "completed_children", "finished_at"])
    return phase_run
