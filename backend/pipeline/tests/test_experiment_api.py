from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from corpus.models import Origin, SystemDescription
from pipeline.models import Experiment, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def prompts(db):
    return (
        PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g"),
        PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l"),
    )


@pytest.fixture
def seed(db):
    return SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)


@pytest.fixture
def experiment(db, taxonomy, prompts, seed):
    generation, labelling = prompts
    made = Experiment.objects.create(
        name="baseline",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
    )
    made.seed_systems.add(seed)
    return made


@pytest.mark.django_db
def test_listing_requires_authentication(api_client, experiment):
    assert api_client.get("/api/experiments/").status_code == 403


@pytest.mark.django_db
def test_any_authenticated_user_may_read(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    body = api_client.get("/api/experiments/").json()
    assert body["count"] == 1
    assert body["results"][0]["name"] == "baseline"


@pytest.mark.django_db
def test_the_list_carries_progress(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    row = api_client.get("/api/experiments/").json()["results"][0]
    assert row["progress"] == {"done": 0, "total": 0, "failed": 0, "percent": 0}


@pytest.mark.django_db
def test_non_staff_cannot_create(api_client, member, taxonomy, prompts):
    api_client.force_authenticate(user=member)
    generation, labelling = prompts
    response = api_client.post(
        "/api/experiments/",
        {
            "name": "x",
            "taxonomy": taxonomy.id,
            "phase2_model": "m",
            "phase2_prompt": generation.id,
            "phase3_model": "m",
            "phase3_prompt": labelling.id,
        },
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_creates_an_experiment(api_client, staff, taxonomy, prompts, seed):
    api_client.force_authenticate(user=staff)
    generation, labelling = prompts
    response = api_client.post(
        "/api/experiments/",
        {
            "name": "fresh",
            "taxonomy": taxonomy.id,
            "phase2_model": "m2",
            "phase2_prompt": generation.id,
            "phase3_model": "m3",
            "phase3_prompt": labelling.id,
            "seed_systems": [seed.id],
        },
        format="json",
    )
    assert response.status_code == 201
    assert Experiment.objects.get(name="fresh").created_by == staff


@pytest.mark.django_db
def test_an_invalid_experiment_is_refused_with_the_reason(api_client, staff, taxonomy, prompts):
    """Validation lives on the model; the API must surface it, not bypass it."""
    api_client.force_authenticate(user=staff)
    generation, labelling = prompts
    response = api_client.post(
        "/api/experiments/",
        {
            "name": "bad",
            "taxonomy": taxonomy.id,
            "phase2_model": "m2",
            "phase2_prompt": generation.id,
            "phase3_model": "m3",
            "phase3_prompt": labelling.id,
            "generate_systems": False,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "target_system" in str(response.json())


@pytest.mark.django_db
def test_a_generated_system_cannot_be_seeded(api_client, staff, taxonomy, prompts):
    """The isolation guarantee, enforced at the point of entry.

    Experiment.clean() cannot check seed_systems before the row has a pk, so
    the API must refuse this itself or a run's own output could seed its
    successor.
    """
    api_client.force_authenticate(user=staff)
    generation, labelling = prompts
    output = SystemDescription.objects.create(title="Riverbend", body="b", origin=Origin.GENERATED)
    response = api_client.post(
        "/api/experiments/",
        {
            "name": "contaminated",
            "taxonomy": taxonomy.id,
            "phase2_model": "m2",
            "phase2_prompt": generation.id,
            "phase3_model": "m3",
            "phase3_prompt": labelling.id,
            "seed_systems": [output.id],
        },
        format="json",
    )

    assert response.status_code == 400
    assert "Riverbend" in str(response.json()["seed_systems"])
    assert not Experiment.objects.filter(name="contaminated").exists()


@pytest.mark.django_db
def test_non_staff_cannot_launch_a_run(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 1}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_launches_repetitions_and_they_are_queued(api_client, staff, experiment):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/",
            {"n_repetitions": 2},
            format="json",
        )

    assert response.status_code == 202
    assert len(response.json()["runs"]) == 2
    assert PipelineRun.objects.count() == 2
    assert enqueued.call_count == 2
    assert enqueued.call_args_list[0].args[0] == "pipeline.tasks.run_pipeline_task"

    for run in PipelineRun.objects.all():
        assert run.status == PipelineRun.Status.QUEUED
        assert {p.status for p in run.phase_runs.all()} == {PhaseRun.Status.QUEUED}


@pytest.mark.django_db
def test_launching_defaults_to_one_repetition(api_client, staff, experiment):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task"):
        api_client.post(f"/api/experiments/{experiment.id}/run/", {}, format="json")
    assert PipelineRun.objects.count() == 1


@pytest.mark.django_db
def test_a_silly_repetition_count_is_refused(api_client, staff, experiment):
    api_client.force_authenticate(user=staff)
    for count in (0, -1, 101):
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/",
            {"n_repetitions": count},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
def test_launching_without_an_api_key_is_a_clear_503(api_client, staff, experiment, settings):
    """A missing key is an operator problem with a fix, not a server fault."""
    settings.OPENROUTER_API_KEY = ""
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/",
            {"n_repetitions": 1},
            format="json",
        )

    assert response.status_code == 503
    assert "No OpenRouter API key" in str(response.json())
    assert response.json()["code"] == "missing_api_key"
    assert PipelineRun.objects.count() == 0
    assert enqueued.call_count == 0


@pytest.mark.django_db
def test_launching_an_invalid_experiment_is_a_400_and_creates_nothing(
    api_client, staff, experiment
):
    experiment.generate_systems = False
    experiment.save()
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/",
            {"n_repetitions": 1},
            format="json",
        )

    assert response.status_code == 400
    assert PipelineRun.objects.count() == 0
    assert enqueued.call_count == 0


@pytest.mark.django_db
def test_the_progress_action_reports_the_roll_up(api_client, member, staff, experiment):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task"):
        api_client.post(
            f"/api/experiments/{experiment.id}/run/",
            {"n_repetitions": 1},
            format="json",
        )

    api_client.force_authenticate(user=member)
    body = api_client.get(f"/api/experiments/{experiment.id}/progress/").json()
    assert body["total"] == 2  # one generation call per variant, seeded at launch
    assert body["done"] == 0
    assert body["percent"] == 0


@pytest.mark.django_db
def test_non_staff_cannot_update_or_delete(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    url = f"/api/experiments/{experiment.id}/"
    assert api_client.put(url, {}, format="json").status_code == 403
    assert api_client.patch(url, {}, format="json").status_code == 403
    assert api_client.delete(url).status_code == 403


@pytest.mark.django_db
def test_a_generated_system_cannot_be_seeded_by_an_update(api_client, staff, experiment):
    """The same isolation guarantee as create-time, now on PATCH.

    Experiment.clean() only checks seed_systems against what is ALREADY
    persisted, so a PATCH that skipped _refuse_generated_seeds could still
    contaminate an existing experiment's seed list.
    """
    api_client.force_authenticate(user=staff)
    output = SystemDescription.objects.create(title="Riverbend", body="b", origin=Origin.GENERATED)
    response = api_client.patch(
        f"/api/experiments/{experiment.id}/",
        {"seed_systems": [output.id]},
        format="json",
    )
    assert response.status_code == 400
    assert "Riverbend" in str(response.json()["seed_systems"])
    assert output not in experiment.seed_systems.all()


@pytest.mark.django_db
def test_deleting_an_experiment_with_runs_is_refused(api_client, staff, experiment):
    """The archive is the research record — cascading it away via DELETE
    would erase what a model actually produced, even mid-run."""
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task"):
        api_client.post(
            f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 1}, format="json"
        )

    response = api_client.delete(f"/api/experiments/{experiment.id}/")
    assert response.status_code == 409
    body = str(response.json()).lower()
    assert "run" in body
    assert "/api/pipeline-runs/" in body
    assert Experiment.objects.filter(pk=experiment.id).exists()


@pytest.mark.django_db
def test_deleting_a_never_run_experiment_is_allowed(api_client, staff, experiment):
    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/experiments/{experiment.id}/")
    assert response.status_code == 204
    assert not Experiment.objects.filter(pk=experiment.id).exists()


@pytest.mark.django_db
def test_a_broker_failure_reverts_that_run_and_is_a_clear_503(api_client, staff, experiment):
    """A broker error must not strand a run QUEUED forever, nor orphan later
    repetitions in the same batch at PENDING with no explanation — it
    reverts only the run that failed and stops, the same guarantee
    `run_experiment`'s own regression test enforces for the CLI path."""
    api_client.force_authenticate(user=staff)
    with patch(
        "pipeline.orchestration.async_task",
        side_effect=[None, RuntimeError("no broker"), None],
    ):
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/",
            {"n_repetitions": 3},
            format="json",
        )

    assert response.status_code == 503
    assert "repetition 2" in str(response.json())
    assert response.json()["code"] == "broker_unavailable"

    runs = list(PipelineRun.objects.order_by("repetition_index"))
    assert [r.repetition_index for r in runs] == [1, 2, 3]
    assert runs[0].status == PipelineRun.Status.QUEUED
    assert runs[1].status == PipelineRun.Status.PENDING
    assert {p.status for p in runs[1].phase_runs.all()} == {PhaseRun.Status.PENDING}
    assert runs[2].status == PipelineRun.Status.PENDING


@pytest.mark.django_db
def test_the_list_endpoint_query_count_does_not_grow_with_rows(
    api_client, member, taxonomy, prompts
):
    """10 experiments used to cost 42 queries: a COUNT for run_count and a
    SUM for progress, per row. The annotated queryset must keep the query
    count fixed regardless of how many rows the page holds."""
    generation, labelling = prompts
    api_client.force_authenticate(user=member)

    def make(n, offset):
        for i in range(n):
            Experiment.objects.create(
                name=f"exp-{offset + i}",
                taxonomy=taxonomy,
                phase2_model="m2",
                phase2_prompt=generation,
                phase3_model="m3",
                phase3_prompt=labelling,
            )

    make(1, offset=0)
    with CaptureQueriesContext(connection) as few:
        response_few = api_client.get("/api/experiments/")
    assert response_few.status_code == 200

    make(9, offset=1)
    with CaptureQueriesContext(connection) as many:
        response_many = api_client.get("/api/experiments/")
    assert response_many.status_code == 200
    assert response_many.json()["count"] == 10

    assert len(many) == len(few)


@pytest.mark.django_db
def test_the_list_endpoints_progress_and_run_count_match_the_live_property(
    api_client, member, staff, experiment
):
    """The annotated shortcut must agree with the un-annotated `.progress`
    property and `runs.count()` on an experiment with multiple repetitions
    — not just be fast."""
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task"):
        api_client.post(
            f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 2}, format="json"
        )

    api_client.force_authenticate(user=member)
    row = api_client.get("/api/experiments/").json()["results"][0]
    assert row["progress"] == experiment.progress
    assert row["run_count"] == experiment.runs.count() == 2
