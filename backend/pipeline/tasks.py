"""Django-Q entry points. Thin on purpose — the logic is importable and testable."""

from pipeline.generation import run_phase2
from pipeline.models import PhaseRun, PipelineRun
from pipeline.orchestration import execute_pipeline_run, run_labelling


def _find_or_report_missing(model, pk):
    """Look up `model.objects.get(pk=pk)`, or a clean "nothing to do" message
    instead of letting `DoesNotExist` reach Django-Q.

    Cancelling a run and then deleting it is a legitimate operator action
    (see the run-deletion feature), but it leaves that run's already-queued
    task sitting in the broker with nothing left to look up — a worker picks
    it up later and fires it against a row that is simply gone. A live
    worker hit exactly that: `PipelineRun.DoesNotExist` raised out of a bare
    `.objects.get(pk=...)`, Django-Q recorded a failed task (the cluster
    itself survived), but the traceback reads as a crash to whoever checks
    the task log — when the true story is "an operator deleted this, there
    is nothing here to run any more". Returning a plain string here, instead
    of raising, makes that the task's recorded RESULT rather than its
    exception — legible in the log as "ran, found nothing" rather than
    noise to investigate.
    """
    try:
        return model.objects.get(pk=pk), None
    except model.DoesNotExist:
        return None, (
            f"{model.__name__} {pk} no longer exists — it was deleted after this task was "
            "queued. Nothing to run."
        )


def run_phase2_task(phase_run_id):
    phase_run, missing = _find_or_report_missing(PhaseRun, phase_run_id)
    if missing is not None:
        return missing
    return run_phase2(phase_run).status


def run_phase3_task(phase_run_id):
    phase_run, missing = _find_or_report_missing(PhaseRun, phase_run_id)
    if missing is not None:
        return missing
    return run_labelling(phase_run).status


def run_pipeline_task(pipeline_run_id):
    pipeline_run, missing = _find_or_report_missing(PipelineRun, pipeline_run_id)
    if missing is not None:
        return missing
    return execute_pipeline_run(pipeline_run).status
