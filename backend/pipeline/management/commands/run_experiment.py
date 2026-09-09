import time

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Experiment, PipelineRun
from pipeline.orchestration import QueueingError, execute_pipeline_run, launch, queue_runs

# How often --watch re-polls the database. A module constant rather than a
# magic number inline, so a test can find and patch the one thing that would
# otherwise make it slow (see pipeline/tests/test_run_experiment.py).
WATCH_POLL_SECONDS = 3

# Default --watch-timeout: give up and exit non-zero rather than loop forever
# with nothing to show for it — the common way to hit this is running
# --watch with no `qcluster` process at all, so every row sits QUEUED
# indefinitely.
WATCH_DEFAULT_TIMEOUT_SECONDS = 3600

# A repetition reads as "still going" until every phase run of it lands here.
_TERMINAL_PIPELINE_STATUSES = {PipelineRun.Status.COMPLETED, PipelineRun.Status.FAILED}


class Command(BaseCommand):
    help = "Run an experiment for N repetitions."

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("--repetitions", type=int, default=1)
        parser.add_argument(
            "--inline",
            action="store_true",
            help="Run in this process instead of queueing onto django-q.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help=(
                "Poll and print progress until every repetition reaches a "
                "terminal status, then exit. Only meaningful for the queued "
                "(non-inline) path; ignored together with --inline."
            ),
        )
        parser.add_argument(
            "--watch-timeout",
            type=int,
            default=WATCH_DEFAULT_TIMEOUT_SECONDS,
            help=(
                "Give up (and exit non-zero) if --watch has not seen every "
                "repetition reach a terminal status within this many "
                f"seconds. Default {WATCH_DEFAULT_TIMEOUT_SECONDS}. Ignored "
                "without --watch."
            ),
        )

    def handle(self, *args, **options):
        try:
            experiment = Experiment.objects.get(name=options["name"])
        except Experiment.DoesNotExist as error:
            raise CommandError(f"No experiment named {options['name']!r}.") from error

        try:
            runs = launch(experiment, n_repetitions=options["repetitions"])
        except ValidationError as error:
            raise CommandError(f"Experiment is not runnable: {error}") from error

        total = len(runs)
        # repetition_index, not batch position: launch() numbers repetitions
        # continuing from wherever this experiment already left off, so a
        # SECOND batch's first run may well be repetition 3, not repetition
        # 1. runs[-1] is the highest-numbered repetition that exists for this
        # experiment as of right now — an accurate "out of how many total".
        rep_total = runs[-1].repetition_index if runs else 0

        if options["inline"]:
            for run in runs:
                execute_pipeline_run(
                    run, on_progress=self._printer(run.repetition_index, rep_total)
                )
        else:
            # QUEUED, not left at PENDING: PENDING now means "not queued
            # yet", QUEUED means "handed to Django-Q, waiting for a free
            # worker" — and that is exactly what is true from this line on,
            # for the run and for every phase run pinned under it.
            # `queue_runs` is shared with the API's `run` action, so a
            # broker failure reverts the one run it happened on (and stops)
            # the same way here as there.
            try:
                queue_runs(runs)
            except QueueingError as error:
                raise CommandError(str(error)) from error

            if options["watch"]:
                self._watch(runs, rep_total, options["watch_timeout"])

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Ran' if options['inline'] else 'Queued'} {total} repetition(s) "
                f"of {experiment.name}"
            )
        )

    def _printer(self, rep_index, rep_total):
        """A one-arg callback `execute_pipeline_run(on_progress=...)` can call
        with a `PhaseRun` every time one is worth reporting on."""

        def on_progress(phase_run):
            self._print_progress(phase_run, rep_index, rep_total)

        return on_progress

    def _print_progress(self, phase_run, rep_index, rep_total, progress=None):
        if progress is None:
            progress = phase_run.progress
        unit = "scenarios" if phase_run.phase == 3 else "call"
        self.stdout.write(
            f"rep {rep_index}/{rep_total} | phase {phase_run.phase} {phase_run.variant} | "
            f"{phase_run.status} | {progress['done']}/{progress['total']} {unit} | "
            f"{progress['failed']} failed"
        )

    def _watch(self, runs, rep_total, timeout_seconds):
        """Poll the DB every WATCH_POLL_SECONDS and print the same progress
        shape --inline does, until every repetition is terminal.

        Reads only — nothing here drives the work itself, which is the
        queued django-q worker's job. That is what keeps this testable
        without a broker: seed rows in the database (or, as in the real
        queued path, let the CLI's own QUEUED transition seed them) and this
        loop reports whatever it finds.

        Only a row whose status or progress actually CHANGED since the
        previous poll is reprinted — otherwise 3 repetitions means 12
        identical lines every 3 seconds, forever, for a batch sitting
        untouched behind a full worker pool. And this loop does not run
        forever unconditionally: with no qcluster process running at all
        (the ordinary way to end up staring at an unmoving --watch), every
        row stays QUEUED, so `timeout_seconds` bounds how long this waits
        before giving up with a clear, non-zero-exit error rather than
        hanging indefinitely.
        """
        ids = [run.pk for run in runs]
        self.stdout.write("Watching for progress. Press Ctrl-C to stop watching.")
        last_seen = {}
        elapsed = 0
        while True:
            current = list(PipelineRun.objects.filter(pk__in=ids).order_by("repetition_index"))
            for run in current:
                for phase_run in run.phase_runs.order_by("phase", "variant"):
                    progress = phase_run.progress
                    snapshot = (
                        phase_run.status,
                        progress["done"],
                        progress["total"],
                        progress["failed"],
                    )
                    if last_seen.get(phase_run.pk) != snapshot:
                        self._print_progress(
                            phase_run, run.repetition_index, rep_total, progress=progress
                        )
                        last_seen[phase_run.pk] = snapshot
            if all(run.status in _TERMINAL_PIPELINE_STATUSES for run in current):
                return
            if elapsed >= timeout_seconds:
                raise CommandError(
                    f"--watch gave up after {timeout_seconds}s without every "
                    "repetition reaching a terminal status. Is a django-q "
                    "worker (`manage.py qcluster`) running?"
                )
            time.sleep(WATCH_POLL_SECONDS)
            elapsed += WATCH_POLL_SECONDS
