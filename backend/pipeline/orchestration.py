"""Turn an Experiment into runs, and roll their status back up.

Phase 3 depends on Phase 2's output, so within a variant the phases are ordered.
Variants are independent and never share context — that independence is what
makes the A/B comparison meaningful.
"""

from django.db import transaction
from django.utils import timezone
from django_q.tasks import async_task

from core.keys import resolve_openrouter_key
from pipeline.generation import run_phase2
from pipeline.labelling import failed_calls, finish_labelling_run, label_scenario, scenarios_for
from pipeline.models import LLMCall, PhaseRun, PipelineRun

# The django-q task path a queued run is handed to. A plain string — the
# task itself is looked up by import path when a worker picks it up — so a
# typo here would ship silently; `test_experiment_api.py` and
# `test_run_experiment.py` both assert on this exact literal.
RUN_TASK = "pipeline.tasks.run_pipeline_task"


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

    for offset in range(n_repetitions):
        run = PipelineRun.objects.create(
            experiment=experiment, repetition_index=highest + offset + 1
        )
        for variant in experiment.variants:
            generation = PhaseRun.objects.create(
                pipeline_run=run,
                phase=2,
                variant=variant,
                model=experiment.phase2_model,
                temperature=experiment.phase2_temperature,
                prompt_template=experiment.phase2_prompt,
                taxonomy=experiment.taxonomy,
                # Known the instant this row exists — Phase 2 is always
                # exactly one call — so a freshly queued run has a real
                # denominator from the start instead of reading 0/0 for the
                # whole window before a worker actually picks it up. Phase 3
                # gets no such head start: its scenario count depends on
                # Phase 2's still-unknown output.
                total_children=1,
            )
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
        try:
            async_task(RUN_TASK, run.pk)
        except Exception as error:  # noqa: BLE001 — broker errors vary by backend
            run.status = PipelineRun.Status.PENDING
            run.save(update_fields=["status"])
            run.phase_runs.update(status=PhaseRun.Status.PENDING)
            raise QueueingError(run, error) from error


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
    """
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
            # launch() always creates a phase-3 run pointing at every
            # phase-2 run it creates. Its absence here means the
            # configuration changed after launch in a way that left this
            # generation without a labelling counterpart — nothing further
            # to drive for it, and nothing to raise over.
            continue

        if generation.status != PhaseRun.Status.COMPLETED:
            # Labelling nothing is not a success, and not a failure of its own.
            labelling.status = PhaseRun.Status.CANCELLED
            labelling.error = "Skipped: generation did not complete."
            labelling.finished_at = timezone.now()
            labelling.save(update_fields=["status", "error", "finished_at"])
            if on_progress:
                on_progress(labelling)
            continue
        run_labelling(labelling, on_progress=on_progress)

    return roll_up(pipeline_run)


def run_labelling(phase_run, on_progress=None):
    """Fan out over the scenarios this run is responsible for.

    `label_scenario` is careful never to raise on a provider or database
    error — but the call it makes to `get_llm_client().complete()` is
    otherwise unguarded, and `OpenRouterClient` deliberately raises
    SecretLeak if a key-shaped string reaches the request body, which a
    rendered scenario narrative could carry. Catching broadly here, rather
    than naming that one exception, is the point: whatever any single
    scenario does must not take the rest of the fan-out down with it. The
    archived call keeps the default parsed_ok=False, so `failed_calls` (and
    therefore `finish_labelling_run`) counts it correctly without any special
    casing.

    A run that already reached a terminal status is returned untouched, the
    same way `execute_pipeline_run` guards a finished repetition. Re-running
    one would make a second provider call for every scenario — double-counting
    tokens and cost on the record — while `get_or_create` quietly hid the
    duplication on the labels themselves. Re-driving only what failed is
    `retry_failed`'s job.

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
    """
    if phase_run.is_terminal:
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
        try:
            call = label_scenario(phase_run, scenario, sequence=sequence)
            # Mirrors failed_calls' predicate exactly: a call counts as
            # succeeded only if it parsed AND left no error behind.
            if call.parsed_ok and not call.parse_error:
                phase_run.completed_children += 1
            else:
                phase_run.failed_children += 1
        except Exception as error:  # noqa: BLE001 — one scenario must not sink the run
            # label_scenario may have already archived (and saved) this
            # call — with parsed_ok=True — before raising further down (a
            # non-DatabaseError from _persist, say). Update that record
            # instead of adding a duplicate: a second row would leave the
            # first one sitting there falsely claiming success.
            existing = phase_run.calls.filter(scenario=scenario, sequence=sequence).first()
            if existing is not None:
                existing.parsed_ok = False
                existing.parse_error = f"{type(error).__name__}: {error}"
                existing.save()
            else:
                LLMCall.objects.create(
                    phase_run=phase_run,
                    sequence=sequence,
                    scenario=scenario,
                    parse_error=f"{type(error).__name__}: {error}",
                )
            phase_run.failed_children += 1
        phase_run.save(update_fields=["completed_children", "failed_children"])
        if on_progress:
            on_progress(phase_run)
    finished = finish_labelling_run(phase_run)
    if on_progress:
        on_progress(finished)
    return finished


def retry_failed(phase_run):
    """Re-label only the scenarios whose call failed.

    Uses `failed_calls` — the same predicate `finish_labelling_run` uses —
    rather than a bare `parsed_ok=False` filter: a call that parsed fine but
    failed to PERSIST keeps parsed_ok=True and records the problem in
    parse_error instead (see pipeline.labelling.failed_calls). Filtering on
    parsed_ok alone would silently skip retrying that call forever.

    A run with nothing to retry is left untouched rather than handed to
    `finish_labelling_run`: a phase run CANCELLED because its generation
    never completed has zero calls, and finishing it would report a false
    COMPLETED with failed_children=0 — wiping the CANCELLED status and its
    error for work that never happened. This also makes retrying an
    already-healthy run a true no-op.
    """
    if not failed_calls(phase_run).exists():
        return phase_run

    failed = failed_calls(phase_run)
    scenario_ids = [call.scenario_id for call in failed if call.scenario_id]
    failed.delete()

    highest = (
        phase_run.calls.order_by("-sequence").values_list("sequence", flat=True).first()
    ) or 0
    for offset, scenario in enumerate(
        scenarios_for(phase_run).filter(id__in=scenario_ids), start=1
    ):
        label_scenario(phase_run, scenario, sequence=highest + offset)
    return finish_labelling_run(phase_run)


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
    """
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
