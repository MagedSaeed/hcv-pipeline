"""M6: minimal coverage for the django-q entry points. Each is a thin
lookup-by-id wrapper around already-tested logic; these confirm the lookup
and delegation actually work, not the logic itself."""

import json

import pytest

from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.generation import run_phase2
from pipeline.models import Experiment, Label, PhaseRun, PipelineRun
from pipeline.orchestration import launch
from pipeline.tasks import run_phase2_task, run_phase3_task, run_pipeline_task
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

CARD = {
    "scenario_id": "sc_01",
    "actor": "EP1",
    "trigger": "t",
    "standing_state": "s",
    "asset": "a",
    "threat": "Spoofing.",
    "outcome": "o",
    "narrative_steps": ["EP1 acts."],
}
GENERATED = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": [CARD]}]}
)
LABELLED = json.dumps({"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]})


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def experiment(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    gen = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    exp = Experiment.objects.create(
        name="e",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=gen,
        phase3_model="m3",
        phase3_prompt=lab,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    exp.variants = ["A"]
    exp.save()
    return exp


@pytest.mark.django_db
def test_run_phase2_task_runs_generation_by_id(_client, experiment):
    _client.queue(GENERATED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")

    status = run_phase2_task(generation.pk)

    assert status == PhaseRun.Status.COMPLETED
    generation.refresh_from_db()
    assert generation.status == PhaseRun.Status.COMPLETED


@pytest.mark.django_db
def test_run_phase3_task_runs_labelling_by_id(_client, experiment):
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    status = run_phase3_task(labelling.pk)

    assert status == PhaseRun.Status.COMPLETED
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_run_pipeline_task_runs_the_whole_repetition_by_id(_client, experiment):
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]

    status = run_pipeline_task(run.pk)

    assert status == PipelineRun.Status.COMPLETED
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.COMPLETED


@pytest.mark.django_db
def test_run_phase2_task_on_a_deleted_run_is_a_clean_no_op(_client, experiment):
    """Defect 2: cancelling then deleting a run leaves its task in the
    broker; a worker firing it later must get a legible no-op result, not a
    PhaseRun.DoesNotExist traceback."""
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    pk = generation.pk
    generation.delete()

    status = run_phase2_task(pk)

    assert isinstance(status, str)
    assert str(pk) in status
    assert "no longer exists" in status


@pytest.mark.django_db
def test_run_phase3_task_on_a_deleted_run_is_a_clean_no_op(_client, experiment):
    run = launch(experiment, n_repetitions=1)[0]
    labelling = run.phase_runs.get(phase=3, variant="A")
    pk = labelling.pk
    labelling.delete()

    status = run_phase3_task(pk)

    assert isinstance(status, str)
    assert str(pk) in status
    assert "no longer exists" in status


@pytest.mark.django_db
def test_run_pipeline_task_on_a_deleted_run_is_a_clean_no_op(_client, experiment):
    run = launch(experiment, n_repetitions=1)[0]
    pk = run.pk
    run.delete()

    status = run_pipeline_task(pk)

    assert isinstance(status, str)
    assert str(pk) in status
    assert "no longer exists" in status


@pytest.mark.django_db
def test_run_pipeline_task_runs_a_run_the_cli_marked_queued(_client, experiment):
    """This is the real production shape: `run_experiment` (without --inline)
    marks the run and its phase runs QUEUED before handing them to Django-Q,
    then a worker eventually calls this exact task. It must not just roll the
    still-untouched run back up to FAILED."""
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    run.status = PipelineRun.Status.QUEUED
    run.save(update_fields=["status"])
    run.phase_runs.update(status=PhaseRun.Status.QUEUED)

    status = run_pipeline_task(run.pk)

    assert status == PipelineRun.Status.COMPLETED
    assert Label.objects.count() == 1
