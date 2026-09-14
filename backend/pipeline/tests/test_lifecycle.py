"""Task 5: the lifecycle seams between tasks 1-4 — nobody owned these before.

`POST /api/pipeline-runs/{id}/cancel/` (step 3) is the focus of this file:
the one route out of a run stuck QUEUED or RUNNING forever, and the caveat
that must accompany it both here and at launch time. Step 1 (`retry_failed`
refusing a run in flight) and step 2 (the config snapshot) have their own,
more natural homes — `test_orchestration.py`/`test_run_api.py` and
`test_export.py` respectively — and are not duplicated here.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError

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
def experiment(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-LIFECYCLE", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="x", order=1)
    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    made = Experiment.objects.create(
        name="lifecycle",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed — a real single-variant experiment has no use for _b.
        phase2_prompt_a=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    made.variants = ["A"]
    made.save()
    return made


def _launch(api_client, staff, experiment, n_repetitions=1):
    """Launch through the real API, leaving every run QUEUED — `async_task`
    is patched to a no-op rather than actually reaching django-q, which is
    exactly the "broker accepted the task" half of the proof scenario this
    file's journey test reproduces (see `queue_runs`).
    """
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task"):
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/",
            {"n_repetitions": n_repetitions},
            format="json",
        )
    assert response.status_code == 202
    return response


# --- The launch caveat (USER DECISION: surfaced at launch, not only at
# cancel time) --------------------------------------------------------------


@pytest.mark.django_db
def test_the_launch_response_carries_the_cancel_caveat(api_client, staff, experiment):
    """USER DECISION (2026-09-09): the operator must learn AT LAUNCH TIME
    that cancelling a run later only marks it dead — it does not interrupt a
    worker already executing it — not discover that caveat for the first
    time while hunting for a way to stop a stuck run.
    """
    response = _launch(api_client, staff, experiment)
    body = response.json()
    assert "detail" in body
    assert "cancel" in body["detail"].lower()
    assert "does not" in body["detail"].lower() or "does not interrupt" in body["detail"].lower()
    assert "cancel a repetition from its run page" in body["detail"]


# --- Cancel: allowed from any non-terminal state ---------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status", [PipelineRun.Status.PENDING, PipelineRun.Status.QUEUED, PipelineRun.Status.RUNNING]
)
def test_cancel_succeeds_from_every_non_terminal_status(api_client, staff, experiment, status):
    """USER DECISION: cancel is allowed from any non-terminal state — PENDING,
    QUEUED **and** RUNNING, not only the "stuck QUEUED" case that motivated
    it.
    """
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)
    run.status = status
    run.save(update_fields=["status"])
    # `PipelineRun.Status`/`PhaseRun.Status` share the same string values
    # ("pending"/"queued"/"running"), so the parametrized value is reused
    # directly rather than re-deriving a `PhaseRun.Status` member from it.
    run.phase_runs.update(status=status)

    response = api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert "detail" in response.json()
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.CANCELLED
    assert run.finished_at is not None
    assert set(run.phase_runs.values_list("status", flat=True)) == {PhaseRun.Status.CANCELLED}


@pytest.mark.django_db
def test_cancels_response_carries_the_same_caveat_as_launch(api_client, staff, experiment):
    """One wording, not two that could drift apart — `run`'s 202 and
    `cancel`'s 200 both surface `CANCEL_CAVEAT`."""
    launch_body = _launch(api_client, staff, experiment).json()
    run = PipelineRun.objects.get(experiment=experiment)

    cancel_body = api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/").json()

    assert cancel_body["detail"] == launch_body["detail"]


@pytest.mark.django_db
def test_cancel_is_atomic_a_failure_partway_through_rolls_back(api_client, staff, experiment):
    """Finding 5, task 5 review round: `cancel` used to be two separate
    writes — the run's own `.save()`, then its phase runs' bulk
    `.update()` — with no transaction wrapping them. A failure between the
    two could leave the run CANCELLED with its phase runs still open (or
    vice versa), an inconsistent state no reader could make sense of.
    Wrapped in `transaction.atomic()`; proven here by making the SECOND
    write fail and confirming the FIRST is rolled back with it, not left
    half-applied.
    """
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)
    assert run.status == PipelineRun.Status.QUEUED

    with (
        patch("django.db.models.QuerySet.update", side_effect=DatabaseError("boom")),
        pytest.raises(DatabaseError),
    ):
        api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/")

    run.refresh_from_db()
    assert run.status == PipelineRun.Status.QUEUED
    assert run.finished_at is None
    assert set(run.phase_runs.values_list("status", flat=True)) == {PhaseRun.Status.QUEUED}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "status",
    [PipelineRun.Status.COMPLETED, PipelineRun.Status.FAILED, PipelineRun.Status.CANCELLED],
)
def test_cancel_refuses_a_terminal_run(api_client, staff, experiment, status):
    """A terminal run — COMPLETED, FAILED, or already CANCELLED — is a 409:
    there is nothing left to cancel."""
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)
    run.status = status
    run.finished_at = None
    run.save(update_fields=["status", "finished_at"])

    response = api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/")

    assert response.status_code == 409
    assert response.json()["code"] == "run_not_cancellable"
    run.refresh_from_db()
    assert run.status == status


@pytest.mark.django_db
def test_non_staff_cannot_cancel(api_client, member, staff, experiment):
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)

    api_client.force_authenticate(user=member)
    response = api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/")

    assert response.status_code == 403
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.QUEUED


@pytest.mark.django_db
def test_cancelling_an_unauthenticated_request_is_refused(api_client, staff, experiment):
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)
    api_client.force_authenticate(user=None)
    response = api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_cancel_does_not_overwrite_a_phase_run_already_cancelled_for_its_own_reason(
    api_client, staff, experiment
):
    """A phase-3 run CANCELLED because its own generation failed already
    carries a specific, more useful `error` ("Skipped: generation did not
    complete.") — `cancel` must not clobber that with the generic "Cancelled
    by operator." message, and must leave an already-terminal phase run
    (COMPLETED here) alone entirely.
    """
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)
    run.status = PipelineRun.Status.RUNNING
    run.save(update_fields=["status"])

    generation, labelling = run.phase_runs.order_by("phase")
    generation.status = PhaseRun.Status.COMPLETED
    generation.save(update_fields=["status"])
    labelling.status = PhaseRun.Status.CANCELLED
    labelling.error = "Skipped: generation did not complete."
    labelling.save(update_fields=["status", "error"])

    response = api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/")
    assert response.status_code == 200

    generation.refresh_from_db()
    labelling.refresh_from_db()
    assert generation.status == PhaseRun.Status.COMPLETED
    assert labelling.status == PhaseRun.Status.CANCELLED
    assert labelling.error == "Skipped: generation did not complete."


# --- The escape journey: what was impossible before this task --------------


@pytest.mark.django_db
def test_the_full_escape_journey_from_a_permanently_stuck_run(api_client, staff, experiment):
    """The dead end, confirmed and permanent (per the brief): a run stuck
    QUEUED — the broker accepted the task then died — had NO route out
    across every registered route. This is that exact journey, now open:
    launch -> leave the run QUEUED -> DELETE run 409 -> cancel 200 ->
    DELETE run 204 -> DELETE experiment 204.
    """
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)
    assert run.status == PipelineRun.Status.QUEUED

    # DELETE run: still refused, a worker may be about to pick it up.
    response = api_client.delete(f"/api/pipeline-runs/{run.pk}/")
    assert response.status_code == 409
    assert PipelineRun.objects.filter(pk=run.pk).exists()

    # cancel: the new way out.
    response = api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/")
    assert response.status_code == 200
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.CANCELLED

    # DELETE run: now succeeds.
    response = api_client.delete(f"/api/pipeline-runs/{run.pk}/")
    assert response.status_code == 204
    assert not PipelineRun.objects.filter(pk=run.pk).exists()

    # DELETE experiment: no runs left, so this is unblocked too.
    response = api_client.delete(f"/api/experiments/{experiment.pk}/")
    assert response.status_code == 204
    assert not Experiment.objects.filter(pk=experiment.pk).exists()


@pytest.mark.django_db
def test_cancel_then_delete_works_from_running_too(api_client, staff, experiment):
    """The journey above proves QUEUED; RUNNING is the other state task 2's
    409 could never previously escape from either."""
    _launch(api_client, staff, experiment)
    run = PipelineRun.objects.get(experiment=experiment)
    run.status = PipelineRun.Status.RUNNING
    run.save(update_fields=["status"])
    run.phase_runs.update(status=PhaseRun.Status.RUNNING)

    assert api_client.delete(f"/api/pipeline-runs/{run.pk}/").status_code == 409
    assert api_client.post(f"/api/pipeline-runs/{run.pk}/cancel/").status_code == 200
    assert api_client.delete(f"/api/pipeline-runs/{run.pk}/").status_code == 204
