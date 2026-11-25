"""Executes one model call of a run and saves what came back."""

import hashlib
import logging
from dataclasses import asdict, dataclass, field

from django.db import transaction
from django.utils import timezone

from analyses import extraction, runs, selection, state
from analyses.models import (
    Analysis,
    AnalysisCall,
    AnalysisItem,
    AnalysisLabel,
    AnalysisRun,
    AnalysisScenario,
)
from llm.schemas import (
    Phase3Response,
    SchemaError,
    json_schema,
    loads_response,
    parse_failure_message,
    parse_phase2,
    parse_phase3,
    phase2_json_schema,
    split_known_labels,
)
from pipeline.clients import get_llm_client
from pipeline.failures import describe_failure, describe_provider_error

logger = logging.getLogger(__name__)

_ID_MAX_LENGTH = AnalysisScenario._meta.get_field("scenario_id").max_length


@dataclass
class _Outcome:
    """What one call produced, before anything is saved."""

    call: dict = field(default_factory=dict)
    error: str = ""
    parsed: object = None


def execute(run_id, position):
    now = timezone.now()
    claimed = AnalysisRun.objects.filter(
        pk=run_id, status=AnalysisRun.Status.QUEUED, position=position
    ).update(status=AnalysisRun.Status.RUNNING, progressed_at=now)
    if not claimed:
        return f"Run {run_id} is gone, finished or past this call: nothing to run."
    run = AnalysisRun.objects.select_related("analysis__taxonomy").filter(pk=run_id).first()
    if run is None:
        return f"Run {run_id} was deleted: nothing to run."

    scenario = None
    if run.kind == AnalysisRun.Kind.LABEL:
        scenario = AnalysisScenario.objects.filter(pk=run.targets[position]).first()

    outcome = _call(run, scenario)
    try:
        return _save(run_id, position, scenario, outcome)
    except Exception as error:  # noqa: BLE001 — a claimed run must reach a terminal status
        logger.exception("Could not save analysis run %s", run_id)
        _fail(run_id, f"The reply could not be saved ({type(error).__name__}). Try again.")
        return f"Run {run_id} failed while saving."


def _call(run, scenario):
    """Render and call outside any transaction; a failure is a result, never a raise."""
    if run.kind == AnalysisRun.Kind.EXTRACT:
        return _extract_call(run)
    generating = run.kind == AnalysisRun.Kind.GENERATE
    if not generating and scenario is None:
        return _Outcome(error="The scenario was deleted before it could be labelled.")
    analysis, pinned = run.analysis, run.pinned
    body = pinned.get("system_body") or analysis.description
    system = (analysis.prompt_title, body, analysis.taxonomy)
    try:
        if generating:
            rendered = selection.render_generation(pinned, *system)
            default_schema = phase2_json_schema(generate_systems=False)
        else:
            rendered = selection.render_labelling(pinned, *system, scenario)
            default_schema = json_schema(Phase3Response)
        result = get_llm_client().complete(
            model=pinned["model"],
            system_prompt=rendered.system,
            user_prompt=rendered.user,
            temperature=pinned["temperature"],
            json_schema=pinned["output_schema"] or default_schema,
        )
    except Exception as error:  # noqa: BLE001 — recorded on the call
        # The prompts are left out: a SecretLeak means they hold a key.
        return _Outcome(error=describe_failure(error))

    call = {
        **asdict(result),
        "rendered_system_prompt": rendered.system,
        "rendered_user_prompt": rendered.user,
    }
    if not result.ok:
        error = describe_provider_error(result.http_status, result.transport_error)
        return _Outcome(call, error or result.transport_error or "The model call failed.")
    try:
        payload = loads_response(result.raw_response)
        parsed = parse_phase2(payload) if generating else parse_phase3(payload)
    except (ValueError, SchemaError) as error:
        message = parse_failure_message(error, result.finish_reason, result.completion_tokens)
        return _Outcome(call, message)
    if generating and not parsed.scenarios:
        return _Outcome(call, "The model returned no scenarios.")
    return _Outcome(call, parsed=parsed)


def _save(run_id, position, scenario, outcome):
    with transaction.atomic():
        run = AnalysisRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None or run.status != AnalysisRun.Status.RUNNING or run.position != position:
            return f"Run {run_id} was cancelled, deleted or expired: nothing saved."
        call = AnalysisCall.objects.create(
            run=run,
            scenario=scenario,
            sequence=position + 1,
            parsed_ok=outcome.parsed is not None,
            error=outcome.error,
            **outcome.call,
        )
        if outcome.error:
            run.failed += 1
        else:
            if run.kind == AnalysisRun.Kind.EXTRACT:
                _save_extraction(run, outcome.parsed)
            elif scenario is None:
                _save_scenarios(run, outcome.parsed)
            else:
                _save_labels(run, scenario, call, outcome.parsed)
            run.done += 1

        run.progressed_at = timezone.now()
        if position + 1 < run.total:
            run.status = AnalysisRun.Status.QUEUED
            run.position = position + 1
            transaction.on_commit(lambda: _enqueue_next(run))
        else:
            _finish(run)
        run.save()
        Analysis.objects.filter(pk=run.analysis_id).update(updated_at=timezone.now())
    return run.status


def _extract_call(run):
    pinned = run.pinned
    try:
        result = get_llm_client().complete(
            model=pinned["model"],
            system_prompt="",
            user_prompt=extraction.request_text(pinned["description"]),
            temperature=pinned["temperature"],
            json_schema=extraction.reply_schema(),
        )
    except Exception as error:  # noqa: BLE001 — recorded on the call
        return _Outcome(error=describe_failure(error))
    call = {
        **asdict(result),
        "rendered_system_prompt": "",
        "rendered_user_prompt": result.request_json["messages"][-1]["content"],
    }
    if not result.ok:
        error = describe_provider_error(result.http_status, result.transport_error)
        return _Outcome(call, error or result.transport_error or "The model call failed.")
    try:
        parsed = extraction.parse_reply(result.raw_response)
    except extraction.ExtractError as error:
        return _Outcome(call, str(error))
    return _Outcome(call, parsed=parsed)


def _save_extraction(run, parsed):
    items, gaps = parsed
    analysis = run.analysis
    analysis.items.all().delete()
    AnalysisItem.objects.bulk_create(
        AnalysisItem(
            analysis=analysis,
            order=order,
            original_value=item["value"],
            original_state=item["state"],
            **{
                key: item[key]
                for key in (
                    "kind",
                    "section",
                    "field",
                    "ref",
                    "value",
                    "state",
                    "fact_type",
                    "reason",
                    "other_reading",
                    "problem",
                )
            },
        )
        for order, item in enumerate(items)
    )
    analysis.extraction_gaps = gaps
    analysis.extraction_digest = hashlib.sha256(run.pinned["description"].encode()).hexdigest()
    analysis.save(update_fields=["extraction_gaps", "extraction_digest", "updated_at"])


def _save_scenarios(run, parsed):
    analysis = run.analysis
    analysis.scenarios.filter(is_manual=False).delete()
    manual = list(analysis.scenarios.all())
    taken = {scenario.scenario_id for scenario in manual}
    room = runs.MAX_INCLUDED - sum(1 for scenario in manual if not scenario.set_aside)
    cards = parsed.scenarios
    proposed = {card.scenario_id.strip() for card in cards}

    rows, number = [], 1
    for index, card in enumerate(cards):
        scenario_id = card.scenario_id.strip()
        if not scenario_id or len(scenario_id) > _ID_MAX_LENGTH or scenario_id in taken:
            while f"sc_{number:02d}" in taken | proposed:
                number += 1
            scenario_id = f"sc_{number:02d}"
        taken.add(scenario_id)
        rows.append(
            AnalysisScenario(
                analysis=analysis,
                scenario_id=scenario_id,
                **{name: getattr(card, name) for name in AnalysisScenario.CARD_FIELDS},
                narrative="\n".join(card.narrative_steps),
                generated_by=run,
                set_aside=index >= room,
                order=index + 1,
            )
        )
    AnalysisScenario.objects.bulk_create(rows)

    notes = []
    asked = run.pinned.get("count")
    if asked and asked != len(rows):
        notes.append(f"The model returned {len(rows)} scenarios instead of {asked}.")
    if len(rows) > room:
        notes.append(
            f"{len(rows) - max(room, 0)} were set aside, as an analysis can include "
            f"{runs.MAX_INCLUDED}."
        )
    run.notice = " ".join(notes)


def _save_labels(run, scenario, call, parsed):
    analysis = run.analysis
    by_id = {row.hcv_id: row for row in analysis.taxonomy.hcvs.all()}
    known, unknown = split_known_labels(parsed, set(by_id))
    scenario.labels.all().delete()
    unique = {}
    for label in known:
        unique.setdefault(label.hcv_id, label)
    AnalysisLabel.objects.bulk_create(
        AnalysisLabel(
            scenario=scenario,
            hcv=by_id[label.hcv_id],
            evidence=label.evidence,
            rationale=label.rationale,
            call=call,
        )
        for label in unique.values()
    )
    # Settings and content can't change during a run, so this is the key of what was sent.
    scenario.label_key = state.label_key(analysis, scenario)
    scenario.labelled_call = call
    scenario.save(update_fields=["label_key", "labelled_call"])
    if unknown:
        call.unknown_hcv_ids = unknown
        call.save(update_fields=["unknown_hcv_ids"])


def _finish(run):
    run.finished_at = timezone.now()
    if not run.failed:
        run.status = AnalysisRun.Status.COMPLETED
        return
    run.status = AnalysisRun.Status.FAILED
    reasons = {call.error for call in run.calls.all() if call.error}
    reason = reasons.pop() if len(reasons) == 1 else ""
    if run.kind == AnalysisRun.Kind.GENERATE:
        run.error = reason
    else:
        run.error = f"{run.failed} of {run.total} scenarios failed to label. {reason}".strip()


def _fail(run_id, message):
    AnalysisRun.objects.filter(pk=run_id, status__in=AnalysisRun.ACTIVE).update(
        status=AnalysisRun.Status.FAILED, error=message, finished_at=timezone.now()
    )


def _enqueue_next(run):
    try:
        runs.enqueue(run, run.position)
    except Exception:  # noqa: BLE001 — broker errors vary by backend
        logger.exception("Could not queue the next call of analysis run %s", run.pk)
        _fail(run.pk, "The job queue became unavailable. Label the remaining scenarios again.")
