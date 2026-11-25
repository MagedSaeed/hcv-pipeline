"""Starting, cancelling and expiring an analysis's runs."""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_q.tasks import async_task

from analyses import preset, selection, state
from analyses.models import Analysis, AnalysisRun
from core.keys import MissingAPIKey, resolve_openrouter_key

MAX_INCLUDED = 30
RUN_TASK = "analyses.tasks.run_call"


class RunRefused(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _already_running():
    return RunRefused("already_running", "A run is already running for this analysis.")


def enqueue(run, position):
    async_task(RUN_TASK, run.pk, position)


def expire_stuck(analysis=None):
    """Fail runs whose worker stopped (one analysis's, or all); queued ones are only waiting."""
    cutoff = timezone.now() - timedelta(seconds=settings.Q_CLUSTER["timeout"])
    stuck = AnalysisRun.objects.filter(status=AnalysisRun.Status.RUNNING, progressed_at__lt=cutoff)
    if analysis is not None:
        stuck = stuck.filter(analysis_id=analysis.pk)
    stuck.update(
        status=AnalysisRun.Status.FAILED,
        error="The run stopped responding. Try again.",
        finished_at=timezone.now(),
    )


def cancel(run):
    AnalysisRun.objects.filter(pk=run.pk, status__in=AnalysisRun.ACTIVE).update(
        status=AnalysisRun.Status.CANCELLED, finished_at=timezone.now()
    )
    run.refresh_from_db()
    return run


def _lock_idle(analysis):
    """The analysis row, held for this transaction, with no run in flight."""
    expire_stuck(analysis)
    held = Analysis.objects.select_for_update().get(pk=analysis.pk)
    if state.active_run(held):
        raise _already_running()
    return held


def _require_key():
    try:
        resolve_openrouter_key()
    except MissingAPIKey as error:
        raise RunRefused("missing_key", str(error)) from error


def _pin(analysis, kind, scenarios=()):
    try:
        return selection.pin(analysis, kind, scenarios)
    except selection.SelectionError as error:
        raise RunRefused("invalid", error.message) from error


def _create(analysis, user, **fields):
    try:
        with transaction.atomic():
            return AnalysisRun.objects.create(analysis=analysis, created_by=user, **fields)
    except IntegrityError as error:
        raise _already_running() from error


def _hand_off(run, previous_title):
    try:
        enqueue(run, 0)
    except Exception as error:  # noqa: BLE001 — broker errors vary by backend
        run.delete()
        Analysis.objects.filter(pk=run.analysis_id).update(prompt_title=previous_title)
        raise RunRefused(
            "queue_unavailable", "The job queue is unavailable. Try again in a moment."
        ) from error
    return run


def start_extraction(analysis, user):
    _require_key()
    with transaction.atomic():
        held = _lock_idle(analysis)
        if state.is_locked(held):
            raise RunRefused(
                "invalid", "The description is locked, so it can't be extracted again."
            )
        if not held.description.strip():
            raise RunRefused("invalid", "Describe the system first.")
        extractor = preset.load_extractor()
        settings = {**state.default_settings(state.EXTRACTION), **(held.extraction_settings or {})}
        pinned = {
            "model": settings["model"],
            "temperature": float(settings["temperature"]),
            "prompt_label": f"{extractor.name} v{extractor.version}",
            "is_custom": settings != state.default_settings(state.EXTRACTION),
            # The text the extraction reads, kept so later edits show as stale.
            "description": held.description,
        }
        run = _create(held, user, kind=AnalysisRun.Kind.EXTRACT, pinned=pinned, total=1)
        held.save(update_fields=["updated_at"])
    return _hand_off(run, held.prompt_title)


def _require_checked_extraction(held):
    status = state.extraction_status(held)
    if status == "none":
        raise RunRefused("invalid", "Extract and check the description's values first.")
    if status == "stale":
        raise RunRefused("invalid", "The description changed since extraction. Extract again.")
    if pending := state.pending_items(held):
        raise RunRefused(
            "invalid", f"{pending} extracted values still need approving or rejecting."
        )


def start_generation(analysis, user):
    _require_key()
    with transaction.atomic():
        held = _lock_idle(analysis)
        if not held.name.strip() or not held.description.strip():
            raise RunRefused("invalid", "Give the system a name and a description first.")
        _require_checked_extraction(held)
        previous_title = held.prompt_title
        if not state.is_locked(held):
            held.prompt_title = held.name.strip()
        pinned = _pin(held, state.GENERATION)
        manual = sum(1 for s in held.scenarios.all() if s.is_manual and not s.set_aside)
        if manual >= MAX_INCLUDED or manual + (pinned["count"] or 0) > MAX_INCLUDED:
            raise RunRefused(
                "over_limit",
                f"An analysis can include {MAX_INCLUDED} scenarios. Ask for fewer, "
                f"or set aside some of your own ({manual} included).",
            )
        pinned["system_body"] = state.system_body(held)
        run = _create(held, user, kind=AnalysisRun.Kind.GENERATE, pinned=pinned, total=1)
        held.save(update_fields=["prompt_title", "updated_at"])
    return _hand_off(run, previous_title)


def start_labelling(analysis, user, *, only_failed=False):
    _require_key()
    with transaction.atomic():
        held = _lock_idle(analysis)
        if not state.is_locked(held):
            raise RunRefused("invalid", "Generate scenarios before identifying vulnerabilities.")
        wanted = {"failed"} if only_failed else {"unlabelled", "stale", "failed"}
        targets = [
            scenario
            for scenario in held.scenarios.all()
            if not scenario.set_aside and state.label_status(held, scenario) in wanted
        ]
        if not targets:
            raise RunRefused("nothing_to_do", "There are no scenarios to check.")
        run = _create(
            held,
            user,
            kind=AnalysisRun.Kind.LABEL,
            pinned={**_pin(held, state.LABELLING, targets), "system_body": state.system_body(held)},
            targets=[scenario.pk for scenario in targets],
            total=len(targets),
        )
        held.save(update_fields=["updated_at"])
    return _hand_off(run, held.prompt_title)
