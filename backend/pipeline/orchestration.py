"""Turn an Experiment into runs, and roll their status back up.

Phase 3 depends on Phase 2's output, so within a variant the phases are ordered.
Variants are independent and never share context — that independence is what
makes the A/B comparison meaningful.
"""

import copy

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from core.keys import resolve_openrouter_key
from pipeline.execution_config import config_snapshot, generation_config_from_snapshot
from pipeline.failures import describe_failure
from pipeline.generation import render_generation_prompt, run_phase2
from pipeline.labelling import failed_calls, finish_labelling_run, label_scenario, scenarios_for
from pipeline.models import PHASE_GENERATION, PHASE_LABELLING, LLMCall, PhaseRun, PipelineRun
from prompts.rendering import VARIANT_GUIDED, VARIANT_UNBIASED, PromptRenderError, TaxonomyLeak

# The django-q task path a queued run is handed to. A plain string — the
# task itself is looked up by import path when a worker picks it up — so a
# typo here would ship silently; `test_experiment_api.py` and
# `test_run_experiment.py` both assert on this exact literal.
RUN_TASK = "pipeline.tasks.run_pipeline_task"

# The django-q task path an ad-hoc (iterate-mode) PhaseRun is handed to,
# keyed by phase — mirrors RUN_TASK's own "plain string, asserted on
# exactly" reasoning above; a typo here would ship just as silently.
# `pipeline/tests/test_orchestration.py` asserts on these exact literals.
PHASE_RUN_TASKS = {
    2: "pipeline.tasks.run_phase2_task",
    3: "pipeline.tasks.run_phase3_task",
}

# The `Experiment` field holding each variant's phase-2 template — the key a
# launch-time refusal is reported under, so the `run` action's 400 names the
# field to fix. Mirrors `Experiment.phase2_prompt_for`'s own mapping; the
# launch tests pin both entries, since a swap would blame the wrong slot.
_PHASE2_TEMPLATE_FIELD = {VARIANT_UNBIASED: "phase2_prompt_a", VARIANT_GUIDED: "phase2_prompt_b"}


def _generation_run_fields(experiment, variant):
    """The fields `launch` creates `variant`'s generation `PhaseRun` with —
    assembled in ONE place, so the unsaved probe
    `_refuse_templates_that_fail_as_their_own_variant` dry-renders is built
    from exactly what the real row will pin, never from a hand-copied subset
    that could drift from it.
    """
    return {
        "phase": 2,
        "variant": variant,
        "model": experiment.phase2_model,
        "temperature": experiment.phase2_temperature,
        # Per-variant, fully independent template — see
        # `Experiment.phase2_prompt_for`/`phase2_prompt_a`/
        # `phase2_prompt_b`'s own comments. Variant A and variant B
        # of the SAME repetition therefore pin two DIFFERENT
        # `PromptTemplate` rows here, never the same one.
        "prompt_template": experiment.phase2_prompt_for(variant),
        "taxonomy": experiment.taxonomy,
        # Known the instant this row exists — Phase 2 is always
        # exactly one call — so a freshly queued run has a real
        # denominator from the start instead of reading 0/0 for the
        # whole window before a worker actually picks it up. Phase 3
        # gets no such head start: its scenario count depends on
        # Phase 2's still-unknown output.
        "total_children": 1,
    }


def _refuse_templates_that_fail_as_their_own_variant(experiment, config):
    """Dry-render each variant's phase-2 template exactly as its run will,
    and refuse the launch if any of them fails. Task 10.4.

    `phase2_prompt_a` and `phase2_prompt_b` are two fully independent
    templates (the researcher's 2026-09-08 decision), and a template records
    no variant of its own, so nothing stopped a template written for variant
    B — one that references `taxonomy` — from being pinned as
    `phase2_prompt_a`. Nothing leaked: variant A's context has no `taxonomy`
    key at all, so under `StrictUndefined` its render fails closed at run
    time, with zero calls for A. But by then the repetition was launched,
    variant B still ran and billed, and the repetition had no A arm left to
    compare it against. This moves that same failure to launch time, where
    it costs nothing.

    Exactly the inputs the run will use. `probe` is built from the same
    `_generation_run_fields` the real generation `PhaseRun` is created from
    (template, variant, taxonomy); `config` is the snapshot `launch` is
    about to pin, read back through `generation_config_from_snapshot`, the
    reader `resolve_generation_config` uses at execution time. And the
    render is `pipeline.generation.render_generation_prompt`, the one
    `run_phase2` calls: same context builder, same renderer, same leak
    rules — no second renderer and no second rule here. `probe` is an
    UNSAVED `PhaseRun` and is never saved, and rendering never reaches
    `get_llm_client()`, so a refusal creates no row and makes no call.

    Catches what `run_phase2` treats as a render failure — `TaxonomyLeak`
    and `PromptRenderError` — and nothing wider, so what launch refuses is
    exactly what would have failed the run. Every failing variant is
    reported, each keyed on the field that holds its template.
    """
    generation_config = generation_config_from_snapshot(config)
    errors = {}
    for variant in experiment.variants:
        probe = PhaseRun(**_generation_run_fields(experiment, variant))
        try:
            render_generation_prompt(probe, generation_config)
        except (TaxonomyLeak, PromptRenderError) as error:
            context = "taxonomy withheld" if variant == VARIANT_UNBIASED else "taxonomy supplied"
            errors[_PHASE2_TEMPLATE_FIELD[variant]] = (
                f"{probe.prompt_template} does not render as variant {variant} "
                f"({context}) against this experiment's inputs, so nothing was "
                "launched and no provider call was made. "
                f"{describe_failure(error)}"
            )
    if errors:
        raise ValidationError(errors)


@transaction.atomic
def launch(experiment, n_repetitions=1, inline=False):
    """Create the runs for N repetitions, pinning the configuration onto each.

    The key is resolved FIRST, before validation and before a single row is
    written. Spec §6 wants a run with no key to fail immediately with a clear
    message rather than produce a run full of 401s, and §12 wants it to fail
    before any HTTP call is made. Doing it here — inside the atomic block, at
    the top — means MissingAPIKey surfaces to the caller with nothing created
    at all. Left to `run_phase2`, it would instead strand a half-executed
    repetition: the PipelineRun RUNNING, its phase runs RUNNING/PENDING, and
    `execute_pipeline_run`'s idempotency guard refusing to re-drive it.

    Each variant's phase-2 template is then dry-rendered under its OWN
    variant's context, against the very snapshot about to be pinned — see
    `_refuse_templates_that_fail_as_their_own_variant` (task 10.4). That is
    still before the first row: a template that fails as its own variant is
    refused with a `ValidationError` naming its field, with nothing created
    and no provider call made, instead of launching a repetition whose
    variant A is doomed while variant B still bills.

    Labelling `PhaseRun`s are created only when the pinned snapshot's
    `phases` includes 3 — read from `config`, so the rows match the record.
    A phase-3-only snapshot gets one labelling run over `label_scenario_ids`.
    """
    resolve_openrouter_key()
    experiment.full_clean()
    created = []
    highest = (
        PipelineRun.objects.filter(experiment=experiment)
        .order_by("-repetition_index")
        .values_list("repetition_index", flat=True)
        .first()
    ) or 0
    # Snapshotted once per `launch()` call, not once per repetition — see
    # `config_snapshot`'s own docstring for why. `deepcopy`d per row so no
    # two PipelineRuns share the same in-memory dict (harmless today, since
    # nothing mutates a `.config` in place after creation, but cheap
    # insurance against that changing later).
    config = config_snapshot(experiment)
    generates = PHASE_GENERATION in config["phases"]
    if generates:
        _refuse_templates_that_fail_as_their_own_variant(experiment, config)
    labels = PHASE_LABELLING in config["phases"]

    for offset in range(n_repetitions):
        run = PipelineRun.objects.create(
            experiment=experiment,
            repetition_index=highest + offset + 1,
            config=copy.deepcopy(config),
        )
        if not generates:
            # Phase 3 only: one labelling run over the frozen selection.
            PhaseRun.objects.create(
                pipeline_run=run,
                phase=3,
                model=experiment.phase3_model,
                temperature=experiment.phase3_temperature,
                prompt_template=experiment.phase3_prompt,
                taxonomy=experiment.taxonomy,
                scenario_ids=list(config["label_scenario_ids"]),
            )
            created.append(run)
            continue
        for variant in experiment.variants:
            generation = PhaseRun.objects.create(
                pipeline_run=run, **_generation_run_fields(experiment, variant)
            )
            if not labels:
                continue
            PhaseRun.objects.create(
                pipeline_run=run,
                phase=3,
                variant=variant,
                model=experiment.phase3_model,
                temperature=experiment.phase3_temperature,
                prompt_template=experiment.phase3_prompt,
                taxonomy=experiment.taxonomy,
                source_phase_run=generation,
            )
        created.append(run)
    return created


class QueueingError(RuntimeError):
    """A run could not be handed to django-q. That run has been reverted to PENDING."""

    def __init__(self, run, error):
        self.run = run
        self.error = error
        super().__init__(
            f"Could not queue repetition {run.repetition_index} of "
            f"{run.experiment.name!r} onto django-q: {type(error).__name__}: {error}. "
            "It has been reverted to pending; any later repetitions in this batch "
            "were not queued at all."
        )


def _hand_to_broker(task_name, pk, *, revert, wrap):
    """Attempt `async_task(task_name, pk)`; on failure, call `revert()` (put
    whatever this was tracking back to PENDING) and raise `wrap(error)`.

    The ONE place both `queue_runs` (a batch of `PipelineRun`s, each cascading
    to its own phase runs) and `queue_phase_run` (a single ad-hoc `PhaseRun`,
    iterate mode) share the "mark QUEUED, hand off, revert to PENDING and
    raise a clear error on a broker failure" mechanics — not a third
    hand-written `async_task` try/except repeating it, exactly the defect
    pattern this codebase has already been bitten by (see `queue_runs`'
    module-level constants and this module's own history: `queue_runs`'
    missing broker guard was one of three near-identical stranding bugs a
    single review round found). The two callers differ only in WHAT they
    revert (a `PipelineRun` cascades to its phase runs; a bare `PhaseRun` has
    none of its own) and WHICH exception they raise — both are supplied as
    callables so this stays the one copy of the mechanism itself.
    """
    try:
        async_task(task_name, pk)
    except Exception as error:  # noqa: BLE001 — broker errors vary by backend
        revert()
        raise wrap(error) from error


def queue_runs(runs):
    """Mark each run QUEUED and hand it to django-q, reverting the one that fails.

    PENDING means "not queued yet"; QUEUED means "handed to django-q, waiting for a
    free worker". A run claiming the latter that was never actually enqueued is worse
    than useless, so a broker error reverts only THAT run and stops — anything queued
    before it really is waiting on a worker, and anything after it was never reached
    at all and is left exactly as `launch` created it, at PENDING.

    The one function both callers — `run_experiment --inline`-less path and the API's
    `run` action — go through, so the revert-on-failure guarantee cannot drift between
    them the way two hand-copied try/except blocks would.
    """
    for run in runs:
        run.status = PipelineRun.Status.QUEUED
        run.save(update_fields=["status"])
        run.phase_runs.update(status=PhaseRun.Status.QUEUED)

        # `run=run` binds THIS iteration's run as each closure's own default,
        # not a shared reference to the loop variable — each is built and
        # consumed synchronously within this same iteration, before `run` is
        # reassigned, so nothing here could actually observe the difference,
        # but it is what a loop-variable-capturing closure is meant to look
        # like regardless (and what ruff's B023 checks for).
        def _revert(run=run):
            run.status = PipelineRun.Status.PENDING
            run.save(update_fields=["status"])
            run.phase_runs.update(status=PhaseRun.Status.PENDING)

        _hand_to_broker(
            RUN_TASK,
            run.pk,
            revert=_revert,
            wrap=lambda error, run=run: QueueingError(run, error),
        )


class PhaseRunQueueingError(RuntimeError):
    """An ad-hoc `PhaseRun` (iterate mode — no `pipeline_run`) could not be
    handed to django-q. That phase run has been reverted to PENDING.

    Distinct from `QueueingError`, not a reuse of it under a new name: that
    class's message names a `PipelineRun`'s `repetition_index` and
    `experiment.name`, neither of which an ad-hoc run has — see
    `PhaseRunViewSet.create`, the one caller.
    """

    def __init__(self, phase_run, error):
        self.phase_run = phase_run
        self.error = error
        super().__init__(
            f"Could not queue {phase_run} (id={phase_run.pk}) onto django-q: "
            f"{type(error).__name__}: {error}. It has been reverted to pending."
        )


def queue_phase_run(phase_run):
    """Hand one ad-hoc `PhaseRun` (no `pipeline_run` — iterate mode) to django-q.

    Mirrors `queue_runs`'s QUEUED -> hand-off -> revert-to-PENDING-on-failure
    shape for a single `PhaseRun` instead of a `PipelineRun` and its cascade
    of phase runs — built on the SAME `_hand_to_broker` primitive rather than
    a third, hand-written `async_task` try/except block (see that function's
    own docstring for why that matters here specifically). Dispatches to
    `run_phase2_task`/`run_phase3_task` by `phase_run.phase`, via
    `PHASE_RUN_TASKS`, the same "plain string, looked up by import path"
    contract `RUN_TASK` already uses for `run_pipeline_task`.
    """
    phase_run.status = PhaseRun.Status.QUEUED
    phase_run.save(update_fields=["status"])

    def _revert():
        phase_run.status = PhaseRun.Status.PENDING
        phase_run.save(update_fields=["status"])

    _hand_to_broker(
        PHASE_RUN_TASKS[phase_run.phase],
        phase_run.pk,
        revert=_revert,
        wrap=lambda error: PhaseRunQueueingError(phase_run, error),
    )


def execute_pipeline_run(pipeline_run, on_progress=None):
    """Run every variant's Phase 2 then Phase 3, in order, inline.

    Driven off the PINNED phase runs `launch` created, filtered straight off
    `pipeline_run`, never off the live `experiment.variants`.
    `Experiment.variants` is a plain, editable JSONField — the whole point of
    pinning configuration at launch is that execution must not re-read
    something an operator could edit afterward. Re-reading it here used to
    mean editing variants after launch could either strand already-created
    phase runs at PENDING forever (shrinking the list) or raise
    `PhaseRun.DoesNotExist` outright (growing it), aborting the repetition
    with `roll_up` never called at all.

    `on_progress`, if given, is called with a `PhaseRun` every time one
    reaches a new state worth reporting — once for Phase 2 (whatever it
    ended up doing), once per scenario during Phase 3's fan-out (see
    `run_labelling`), and once for a Phase 3 run CANCELLED outright. It is
    the seam `run_experiment --inline` uses to print progress as it goes;
    nothing here depends on it being set.

    `pipeline_run.status` is re-fetched from the database FIRST, before the
    entry guard reads it — a review round found this the one function the
    CANCELLED sweep missed: the caller's copy can be stale (a worker fetched
    a QUEUED run, an operator cancelled it before the worker actually got
    here — `run_experiment --inline` loops over PipelineRun objects it
    fetched back at `launch()` time, so repetition N's copy can be stale for
    the whole time repetitions 1..N-1 take to run), and without this,
    the guard below would trust that stale QUEUED/PENDING reading and
    unconditionally write RUNNING over a row the database already has as
    CANCELLED — the exact write `roll_up`'s own CANCELLED guard exists to
    never let happen, defeated one line before it would ever run.
    """
    pipeline_run.refresh_from_db(fields=["status"])
    if pipeline_run.status not in (PipelineRun.Status.PENDING, PipelineRun.Status.QUEUED):
        # Not idempotent on its own: re-running would call run_phase2 and
        # run_labelling again and duplicate every system, scenario, label
        # and LLMCall this repetition already produced. A repeat call just
        # reports the current state instead. QUEUED must be accepted here
        # alongside PENDING — the CLI marks a run QUEUED the moment it is
        # handed to Django-Q, before any worker has picked it up, and this is
        # the guard that worker runs through.
        return roll_up(pipeline_run)

    pipeline_run.status = PipelineRun.Status.RUNNING
    pipeline_run.started_at = timezone.now()
    pipeline_run.save(update_fields=["status", "started_at"])

    for generation in pipeline_run.phase_runs.filter(phase=2).order_by("variant"):
        labelling = pipeline_run.phase_runs.filter(phase=3, source_phase_run=generation).first()

        run_phase2(generation)
        generation.refresh_from_db()
        if on_progress:
            on_progress(generation)

        if labelling is None:
            # A generation-only repetition (pinned `phases` == [2]) has no
            # labelling counterpart by design — nothing further to drive.
            continue

        if generation.status != PhaseRun.Status.COMPLETED:
            # Labelling nothing is not a success, and not a failure of its own.
            # `labelling` was fetched at the top of THIS iteration, before
            # `run_phase2(generation)` ran — real wall-clock time an operator
            # could have cancelled the run in — so re-check its CURRENT status
            # rather than trust the possibly-stale local copy: an already
            # CANCELLED labelling run (via `POST .../cancel/`, carrying its
            # own "Cancelled by operator." error) must not have that more
            # specific reason silently overwritten with this generic
            # "skipped" one.
            labelling.refresh_from_db(fields=["status"])
            if labelling.status != PhaseRun.Status.CANCELLED:
                labelling.status = PhaseRun.Status.CANCELLED
                labelling.error = "Skipped: generation did not complete."
                labelling.finished_at = timezone.now()
                labelling.save(update_fields=["status", "error", "finished_at"])
                if on_progress:
                    on_progress(labelling)
            continue
        run_labelling(labelling, on_progress=on_progress)

    # Phase-3-only repetitions: labelling runs over a frozen selection.
    for labelling in pipeline_run.phase_runs.filter(phase=3, scenario_ids__isnull=False).order_by(
        "id"
    ):
        run_labelling(labelling, on_progress=on_progress)

    return roll_up(pipeline_run)


def _label_scenario_or_archive_failure(phase_run, scenario, sequence):
    """Call `label_scenario`, and if it raises, archive the failure instead
    of letting the exception escape — the ONE guard `run_labelling`'s
    fan-out and `retry_failed`'s re-drive both need, shared here rather than
    hand-written twice. Two independent copies that must agree is precisely
    how `KEY_PATTERN` and `format=None` each survived their first fix in
    this plan; a third stranding defect (this one: `retry_failed` calling
    `label_scenario` with no guard at all, discovered after the identical
    shape was already fixed twice — `queue_runs`' missing broker guard, and
    `resolve_generation_config` sitting outside `run_phase2`'s try/except)
    is exactly what a second hand-written guard would risk again.

    `label_scenario` is careful never to raise on a provider or database
    error — but the call it makes to `get_llm_client().complete()` is
    otherwise unguarded, and `OpenRouterClient` deliberately raises
    SecretLeak if a key-shaped string reaches the request body, which a
    rendered scenario narrative could carry. Catching broadly here, rather
    than naming that one exception, is the point: whatever any single
    scenario does must not take the rest of the fan-out — or the retry —
    down with it.

    Always returns the archived `LLMCall`, success or failure — never
    leaves this scenario's slot unarchived. That matters most for
    `retry_failed`, which deletes a failed call before re-labelling it: an
    exception escaping here, unguarded, would destroy that archived row
    with nothing left to replace it. This project's standard is that
    archived calls are the record; a retry must never be able to consume
    one without leaving a replacement in its place.
    """
    try:
        call = label_scenario(phase_run, scenario, sequence=sequence)
    except Exception as error:  # noqa: BLE001 — one scenario must not sink the run
        # label_scenario may have already archived (and saved) this call —
        # with parsed_ok=True — before raising further down (a
        # non-DatabaseError from _persist, say). Update that record instead
        # of adding a duplicate: a second row would leave the first one
        # sitting there falsely claiming success.
        existing = phase_run.calls.filter(scenario=scenario, sequence=sequence).first()
        if existing is not None:
            existing.parsed_ok = False
            existing.parse_error = f"{type(error).__name__}: {error}"
            existing.save()
            call = existing
        else:
            call = LLMCall.objects.create(
                phase_run=phase_run,
                sequence=sequence,
                scenario=scenario,
                parse_error=f"{type(error).__name__}: {error}",
            )
    return call


def run_labelling(phase_run, on_progress=None):
    """Fan out over the scenarios this run is responsible for.

    Each scenario is labelled through `_label_scenario_or_archive_failure`,
    which guards against whatever any single scenario does taking the rest
    of the fan-out down with it — see that function's own docstring. The
    archived call keeps the default parsed_ok=False on a genuine failure, so
    `failed_calls` (and therefore `finish_labelling_run`) counts it
    correctly without any special casing.

    A run that already reached a terminal status — COMPLETED, FAILED, or
    CANCELLED — is returned untouched, the same way `execute_pipeline_run`
    guards a finished repetition. Re-running a COMPLETED/FAILED one would
    make a second provider call for every scenario — double-counting tokens
    and cost on the record — while `get_or_create` quietly hid the
    duplication on the labels themselves; re-running a CANCELLED one is
    worse — a whole new round of real provider calls for a run an operator
    deliberately stopped. Re-driving only what failed on an already-terminal
    (COMPLETED/FAILED, never CANCELLED) run is `retry_failed`'s job.

    `total_children` is set to the scenario count BEFORE the loop starts, so
    a caller polling this row mid-run (the CLI's --watch, say) sees a real
    denominator from the first moment rather than 0. `completed_children` and
    `failed_children` are reset to 0 in that same write — not just
    total_children — because `is_terminal` only blocks COMPLETED/FAILED:
    re-entering a still-RUNNING run (a Django-Q retry, say) with those two
    left at whatever a previous, unfinished attempt last wrote would
    accumulate on top of them instead of starting this attempt's count from
    zero. `completed_children` and `failed_children` are then bumped, and
    saved, after EACH scenario — live, not just once at the end — which is
    what makes that same polling useful partway through a long fan-out.
    `on_progress`, if given, is called with this same `phase_run` right after
    each of those saves, and once more after `finish_labelling_run` — the
    latter is what still reports something for a zero-scenario fan-out (the
    source phase run was deleted, say), which the per-scenario calls alone
    would never fire for at all.

    `phase_run` is re-fetched from the database rather than trusted as
    passed in: without it, a `phase_run` an operator cancelled moments
    before this was invoked — `execute_pipeline_run`'s `labelling` can be
    stale, real wall-clock time passing while `run_phase2` ran earlier in
    the same loop iteration — would get resurrected: `is_terminal` reads
    whatever `.status` the in-memory object currently holds, which is not
    necessarily what the database says right now.

    Refuses outright if `phase_run.phase != 3`. `label_scenario` renders
    through `build_phase3_context`, which ALWAYS injects the taxonomy — that
    is correct for phase 3, the labeller, but it means `label_scenario`
    never attaches the phase-2 final-request choke point
    (`pipeline.generation._preflight`; see `prompts.rendering
    .assert_request_not_leaking_taxonomy`) at all. `execute_pipeline_run`
    only ever creates a labelling task for a genuine `phase=3` row, and
    `RunViewSet`'s retry action guards the same way — but both of those are
    guards living at a CALLER, and this codebase has already been bitten
    more than once by exactly that shape of gap (see
    `pipeline.execution_config`'s own module docstring for the taxonomy leak
    a permissive reader one layer away from the guarantee actually produced
    once). A hand-invoked `run_phase3_task(<a phase-2 run id>)` — or any
    future caller — would otherwise render whatever template that phase-2
    `PhaseRun` carries under a taxonomy-bearing phase-3 context with no scan
    protecting it at all. Checked here, where the guarantee actually lives,
    not only at whoever currently happens to call in correctly: refused
    before a single scenario is looked up or a provider is ever contacted,
    the same way `run_phase2` fails a phase-2 run with no variant set.
    """
    phase_run.refresh_from_db(fields=["status"])
    if phase_run.is_terminal:
        return phase_run
    if phase_run.phase != 3:
        phase_run.status = PhaseRun.Status.FAILED
        phase_run.error = (
            "run_labelling requires a phase-3 PhaseRun; this one is phase "
            f"{phase_run.phase}. Labelling always renders under a taxonomy-"
            "bearing context, which is only safe for phase 3."
        )
        phase_run.failed_children = 0
        phase_run.finished_at = timezone.now()
        phase_run.save(update_fields=["status", "error", "failed_children", "finished_at"])
        return phase_run

    phase_run.status = PhaseRun.Status.RUNNING
    phase_run.started_at = timezone.now()
    scenarios = list(scenarios_for(phase_run))
    phase_run.total_children = len(scenarios)
    phase_run.completed_children = 0
    phase_run.failed_children = 0
    phase_run.save(
        update_fields=[
            "status",
            "started_at",
            "total_children",
            "completed_children",
            "failed_children",
        ]
    )

    for sequence, scenario in enumerate(scenarios, start=1):
        call = _label_scenario_or_archive_failure(phase_run, scenario, sequence)
        # Mirrors failed_calls' predicate exactly: a call counts as
        # succeeded only if it parsed AND left no error behind.
        if call.parsed_ok and not call.parse_error:
            phase_run.completed_children += 1
        else:
            phase_run.failed_children += 1
        phase_run.save(update_fields=["completed_children", "failed_children"])
        if on_progress:
            on_progress(phase_run)
    finished = finish_labelling_run(phase_run)
    if on_progress:
        on_progress(finished)
    return finished


class RunInFlight(RuntimeError):
    """The run is not finished, so its archived calls are not safe to touch."""


class RunCancelled(RuntimeError):
    """The run was deliberately cancelled — it is terminal, and it will
    never finish on its own, so `RunInFlight`'s "wait for it to finish"
    would be actively misleading. Retrying is refused outright: re-driving
    a cancelled run would resurrect it with brand-new provider calls, the
    exact thing `run_phase2`/`run_labelling` already hard-stop on for the
    same reason (see `PhaseRun.is_terminal`).
    """


def retry_failed(phase_run):
    """Re-label only the scenarios whose call failed.

    Uses `failed_calls` — the same predicate `finish_labelling_run` uses —
    rather than a bare `parsed_ok=False` filter: a call that parsed fine but
    failed to PERSIST keeps parsed_ok=True and records the problem in
    parse_error instead (see pipeline.labelling.failed_calls). Filtering on
    parsed_ok alone would silently skip retrying that call forever.

    Deletes the failed calls it is about to re-drive BEFORE re-labelling
    them (below), so each scenario in the loop goes through
    `_label_scenario_or_archive_failure` — the same guard `run_labelling`'s
    own fan-out uses, shared rather than hand-written a second time here.
    A bare, unguarded `label_scenario(...)` call used to mean one scenario
    raising (the `SecretLeak`/provider path `_label_scenario_or_archive_failure`'s
    own docstring describes) propagated straight out of this function —
    with the failed call already deleted and nothing archived in its
    place, `finish_labelling_run` never reached, and the run left mid-retry
    with counters and status unreconciled. Archived calls are this
    project's record; a retry must never be able to consume one without
    leaving a replacement.

    A run with nothing to retry is left untouched rather than handed to
    `finish_labelling_run`: a phase run CANCELLED because its generation
    never completed has zero calls, and finishing it would report a false
    COMPLETED with failed_children=0 — wiping the CANCELLED status and its
    error for work that never happened. This also makes retrying an
    already-healthy run a true no-op.

    CANCELLED is refused OUTRIGHT next, regardless of `is_terminal` (which
    now includes it): a review round found the previous framing genuinely
    unreachable in a way that mattered — the docstring said re-driving a
    cancelled run was "legitimate", but nothing here ever actually did
    that (a zero-call CANCELLED run always hit the early return above; a
    CANCELLED run WITH failed calls hit `RunInFlight` instead, whose
    wording — "wait for it to finish" — is false for a run that will never
    finish on its own, having been deliberately stopped). `RunCancelled`
    names the real reason plainly instead of leaving that case to fall
    through to a message written for a different one.

    The whole-branch review proved the deeper bug the `RunInFlight` guard
    below closes: a phase-3 run RUNNING with total=5 completed=0 failed=1
    (a worker mid fan-out) let `POST .../retry/` return 200, delete the one
    archived failed call, and flip the run RUNNING -> completed with
    incoherent counters — while the worker kept writing to a row now marked
    terminal. Raising `RunInFlight` unless `phase_run.is_terminal` closes
    that at the SOURCE, inside this function, so the management command and
    any future caller are protected the same way the view already is — not
    just at the one layer that happened to call this first.

    A CONFIRMED, pre-existing sibling of that same bug: `LLMCall.scenario`
    is `SET_NULL`, and the admin permits deleting a GENERATED scenario, so a
    failed call's `scenario_id` can go null while the call itself is still
    sitting here, archived, waiting to be retried. The naive version of this
    function built `scenario_ids` by dropping every call with no
    `scenario_id` — silently excluding it from the re-drive — and then
    deleted `failed_calls(phase_run)` WHOLESALE, that null-scenario call
    included: one delete, zero replacement, exactly the "archived call
    consumed with nothing left in its place" this function's own docstring
    (above) says must never happen. `finish_labelling_run` would then
    recount from an emptied table and report a false COMPLETED. Only the
    REDRIVABLE calls — the ones whose scenario still exists — are deleted
    below; a call whose scenario is gone has nothing to re-label it against,
    so it is left exactly where it is, still archived, still counted as
    failed by `finish_labelling_run`'s own fresh recount. This is the sibling
    of the view's `missing_source_phase_run` guard, but scoped to one call
    rather than the whole run — refusing the WHOLE retry over one orphaned
    call would block every OTHER genuinely retriable failure for no reason.
    Guarded here, inside `retry_failed` itself, rather than only in the view,
    for the same reason `RunInFlight` is: `retry_failed(phase_run)` called
    directly — the management command's path — bypasses the view entirely.

    After an actual re-drive, an orchestrated run's repetition is rolled up
    again (task 10.2). `execute_pipeline_run` rolls a repetition up exactly
    once, at the end of its own loop; a retry lands AFTER that, so without
    this a repetition whose last failed scenario was just repaired kept the
    FAILED status that roll-up gave it, even with every phase run under it
    now COMPLETED. It goes through `roll_up` — never a direct write to
    `PipelineRun.status` here — so `roll_up`'s own CANCELLED guard still
    decides: a repetition an operator cancelled stays cancelled however
    thoroughly one of its FAILED phase runs is repaired afterwards (see
    `roll_up`'s docstring, and the resurrection it and two sibling guards
    exist to prevent). The two early returns above never roll up: nothing
    was re-driven, so nothing a roll-up reads has changed, and re-rolling
    would only re-stamp `finished_at` on a no-op.
    """
    if not failed_calls(phase_run).exists():
        return phase_run

    if phase_run.status == PhaseRun.Status.CANCELLED:
        raise RunCancelled(
            f"{phase_run} was cancelled and is terminal — it will never "
            "finish on its own, and retrying it would resurrect it with new "
            "provider calls. Launch a new run instead."
        )

    if not phase_run.is_terminal:
        raise RunInFlight(
            f"{phase_run} is {phase_run.status}, not finished — a worker may "
            "still be writing to it, so its archived calls cannot be touched. "
            "Wait for it to finish, or cancel the pipeline run first."
        )

    failed = failed_calls(phase_run)
    redrivable = failed.exclude(scenario_id=None)
    scenario_ids = list(redrivable.values_list("scenario_id", flat=True))
    redrivable.delete()

    highest = (
        phase_run.calls.order_by("-sequence").values_list("sequence", flat=True).first()
    ) or 0
    for offset, scenario in enumerate(
        scenarios_for(phase_run).filter(id__in=scenario_ids), start=1
    ):
        _label_scenario_or_archive_failure(phase_run, scenario, sequence=highest + offset)
    finished = finish_labelling_run(phase_run)
    if finished.pipeline_run_id:
        roll_up(finished.pipeline_run)
    return finished


def roll_up(pipeline_run):
    """A repetition is complete only when every phase run of it completed.

    `finished_at` is stamped only on a terminal transition (COMPLETED or
    FAILED) — stamping it while still RUNNING would claim a run finished
    when it has not. A PipelineRun with zero phase runs at all — a state
    `launch` never produces on its own, but not one this function should
    silently mishandle — is guarded explicitly: `set() <= {COMPLETED}` is
    vacuously true and would otherwise read an empty repetition as
    COMPLETED.

    QUEUED joins {PENDING, RUNNING} in the "still in progress" set below —
    it means "handed to Django-Q, waiting for a free worker", which is no
    more a failure than PENDING or RUNNING is. Leaving it out would read a
    repetition that is merely waiting its turn as FAILED.

    M11: without the QUEUED branch below, a repetition sitting ENTIRELY in
    {PENDING, QUEUED} (no worker has touched any phase run of it yet, but at
    least one WAS actually queued) fell through to the same RUNNING the
    "still in progress" fallback reports for a run already under way —
    erasing the distinction between "waiting for a worker" and "a worker is
    on it" for anything built on top of this status later. All-PENDING (no
    QUEUED at all — nothing has actually been queued) deliberately stays
    RUNNING: calling that "queued" would be a mislabel.

    Re-checks CANCELLED from the database before doing any of that, not the
    possibly-stale `pipeline_run` passed in: `execute_pipeline_run` calls
    this both on its early-return guard (a `pipeline_run` fetched at task
    entry, and by the time this runs an operator's `POST .../cancel/` may
    already have landed) and at the very end of its loop (a `pipeline_run`
    whose in-memory `.status` was set to RUNNING at the top of that function
    and never refreshed since, however long the loop's own phase-2/phase-3
    work took) — and `retry_failed` calls it after re-deriving a repaired
    phase run's status, by which point an operator may have cancelled the
    repetition that phase run belongs to. Without this, a cancelled run's phase runs — themselves
    correctly left CANCELLED by `cancel` — would still get rolled up into
    FAILED here (every phase run CANCELLED is neither `<= {COMPLETED}` nor
    in `in_progress`, so it falls into the FAILED branch below), silently
    overwriting a deliberate cancellation with a false "this run failed".
    Once CANCELLED, a `PipelineRun`'s status is never rederived from its
    phase runs again — `cancel` is the one place that writes it, same as
    every other terminal transition here already treats COMPLETED/FAILED as
    something nothing re-derives after the fact.
    """
    pipeline_run.refresh_from_db(fields=["status"])
    if pipeline_run.status == PipelineRun.Status.CANCELLED:
        return pipeline_run
    statuses = set(pipeline_run.phase_runs.values_list("status", flat=True))
    in_progress = {PhaseRun.Status.PENDING, PhaseRun.Status.QUEUED, PhaseRun.Status.RUNNING}
    not_yet_started = {PhaseRun.Status.PENDING, PhaseRun.Status.QUEUED}
    if statuses and statuses <= {PhaseRun.Status.COMPLETED}:
        pipeline_run.status = PipelineRun.Status.COMPLETED
        pipeline_run.finished_at = timezone.now()
        update_fields = ["status", "finished_at"]
    elif statuses and not statuses & in_progress:
        pipeline_run.status = PipelineRun.Status.FAILED
        pipeline_run.finished_at = timezone.now()
        update_fields = ["status", "finished_at"]
    elif statuses and statuses <= not_yet_started and PhaseRun.Status.QUEUED in statuses:
        pipeline_run.status = PipelineRun.Status.QUEUED
        update_fields = ["status"]
    else:
        pipeline_run.status = PipelineRun.Status.RUNNING
        update_fields = ["status"]
    pipeline_run.save(update_fields=update_fields)
    return pipeline_run
