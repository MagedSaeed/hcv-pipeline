"""Django-Q entry points. Thin on purpose — the logic is importable and testable."""

from pipeline.generation import run_phase2
from pipeline.models import PhaseRun, PipelineRun
from pipeline.orchestration import execute_pipeline_run, run_labelling


def run_phase2_task(phase_run_id):
    return run_phase2(PhaseRun.objects.get(pk=phase_run_id)).status


def run_phase3_task(phase_run_id):
    return run_labelling(PhaseRun.objects.get(pk=phase_run_id)).status


def run_pipeline_task(pipeline_run_id):
    return execute_pipeline_run(PipelineRun.objects.get(pk=pipeline_run_id)).status
