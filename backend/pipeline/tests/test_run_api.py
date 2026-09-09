import json

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from llm.fake import FakeLLMClient
from pipeline.clients import get_llm_client, set_llm_client
from pipeline.models import Experiment, LLMCall, PhaseRun, PipelineRun
from pipeline.orchestration import execute_pipeline_run, launch
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()

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


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def executed(db, settings):
    settings.OPENROUTER_API_KEY = "sk-or-test"
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(
        phase=3, name="l", user_prompt="{{ scenario.scenario_id }}"
    )
    experiment = Experiment.objects.create(
        name="e",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    experiment.variants = ["A"]
    experiment.save()

    fake = FakeLLMClient()
    fake.queue(GENERATED)
    fake.queue(LABELLED)
    set_llm_client(fake)

    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    run.refresh_from_db()
    return run


@pytest.fixture
def noise(db, settings):
    """A second, un-executed experiment and run.

    Every filter test in this file needs at least one row that a broken
    (no-op) filter would wrongly include, or the assertion passes whether or
    not the filter actually runs — `executed` alone is never enough, since it
    is the ONLY row and every filter matches all of it. This fixture pins a
    different experiment, a different variant (B), and phase runs left at
    PENDING (never executed), so `?experiment=`, `?variant=` and `?status=`
    each have a genuine non-match to exclude.
    """
    settings.OPENROUTER_API_KEY = "sk-or-test"
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21-noise", version="1.0")
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="Noise", order=1)
    generation = PromptTemplate.objects.create_version(phase=2, name="ng", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(phase=3, name="nl", user_prompt="x")
    experiment = Experiment.objects.create(
        name="noise-experiment",
        taxonomy=tax,
        phase2_model="nm2",
        phase2_prompt=generation,
        phase3_model="nm3",
        phase3_prompt=labelling,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    experiment.variants = ["B"]
    experiment.save()
    return launch(experiment, n_repetitions=1)[0]


@pytest.mark.django_db
def test_runs_require_authentication(api_client, executed):
    assert api_client.get("/api/pipeline-runs/").status_code == 403


@pytest.mark.django_db
def test_a_run_reports_its_status_and_progress(api_client, member, executed):
    api_client.force_authenticate(user=member)
    row = api_client.get(f"/api/pipeline-runs/{executed.pk}/").json()

    assert row["status"] == "completed"
    assert row["repetition_index"] == 1
    assert row["progress"]["percent"] == 100
    assert row["progress"]["failed"] == 0


@pytest.mark.django_db
def test_runs_filter_by_experiment(api_client, member, executed, noise):
    api_client.force_authenticate(user=member)
    body = api_client.get(f"/api/pipeline-runs/?experiment={executed.experiment_id}").json()
    assert body["count"] == 1
    assert body["results"][0]["id"] == executed.pk

    # Without the filter both experiments' runs show up — proves the count
    # of 1 above came from the filter actually excluding `noise`, not from
    # there only ever being one row in the database.
    assert api_client.get("/api/pipeline-runs/").json()["count"] == 2


@pytest.mark.django_db
def test_a_nonexistent_experiment_id_matches_nothing(api_client, member, executed, noise):
    """`?experiment=0` is a real (if nonexistent) id and must filter to
    empty — `filter(experiment_id=0)` — not fall through to "no filter was
    given" the way `queryset.filter(...) if experiment else queryset` would
    for the falsy integer 0."""
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/pipeline-runs/?experiment=0").json()["count"] == 0


@pytest.mark.django_db
def test_pipeline_runs_list_query_count_does_not_grow_with_rows(api_client, member, executed):
    """10 runs must not cost 10x the queries of 1.

    Without annotating progress the way `ExperimentViewSet` does,
    `ProgressField` falls back to `PipelineRun.progress`, which runs its own
    aggregate query per row.
    """
    api_client.force_authenticate(user=member)

    with CaptureQueriesContext(connection) as few:
        response_few = api_client.get("/api/pipeline-runs/")
    assert response_few.status_code == 200

    launch(executed.experiment, n_repetitions=9)

    with CaptureQueriesContext(connection) as many:
        response_many = api_client.get("/api/pipeline-runs/")
    assert response_many.status_code == 200
    assert response_many.json()["count"] == 10

    assert len(many) == len(few)


@pytest.mark.django_db
def test_pipeline_runs_list_matches_the_models_default_order(api_client, member, executed, noise):
    """`PipelineRun.Meta.ordering = ["experiment", "repetition_index"]`
    resolves THROUGH the FK to `Experiment.Meta.ordering` (`-created_at`),
    not by the raw `experiment_id` column — comparing against the model's
    OWN default order (rather than hand-picking an expected id list, which
    could get the real order wrong the same way the bug did) is what would
    have caught ordering by `experiment_id` instead.
    """
    api_client.force_authenticate(user=member)
    model_order = list(PipelineRun.objects.values_list("id", flat=True))
    api_order = [row["id"] for row in api_client.get("/api/pipeline-runs/").json()["results"]]
    assert api_order == model_order


@pytest.mark.django_db
def test_a_runs_progress_matches_the_live_property_with_mixed_counts(api_client, member, executed):
    """The annotated shortcut must agree with the un-annotated `.progress`
    property on a repetition with BOTH completed and failed children — not
    just on an all-succeeded run, where a fan-out bug in the annotation
    could hide behind failed always reading 0."""
    labelling = executed.phase_runs.get(phase=3)
    labelling.total_children = 3
    labelling.completed_children = 1
    labelling.failed_children = 2
    labelling.save(update_fields=["total_children", "completed_children", "failed_children"])

    api_client.force_authenticate(user=member)
    row = api_client.get(f"/api/pipeline-runs/{executed.pk}/").json()
    assert row["progress"] == executed.progress
    assert row["progress"] == {"done": 4, "total": 4, "failed": 2, "percent": 100}


@pytest.mark.django_db
def test_runs_are_read_only(api_client, staff, member, executed):
    """POST is unsupported for anyone — runs only ever come from `launch`.

    DRF checks permissions before it resolves a handler (`APIView.dispatch`
    calls `self.initial()`, which runs permissions, before it looks up
    `request.method.lower()` on the view), so a non-staff POST/DELETE is
    always refused with 403 first — 405 only surfaces once permission has
    already been granted, which is why this asserts POST as staff.
    """
    api_client.force_authenticate(user=staff)
    assert api_client.post("/api/pipeline-runs/", {}, format="json").status_code == 405

    api_client.force_authenticate(user=member)
    assert api_client.delete(f"/api/pipeline-runs/{executed.pk}/").status_code == 403


@pytest.mark.django_db
def test_non_staff_cannot_delete_a_run(api_client, member, executed):
    api_client.force_authenticate(user=member)
    assert api_client.delete(f"/api/pipeline-runs/{executed.pk}/").status_code == 403
    assert PipelineRun.objects.filter(pk=executed.pk).exists()


@pytest.mark.django_db
def test_staff_deletes_a_finished_run_and_its_output_goes_with_it(api_client, staff, executed):
    """All-or-nothing. Deleting individual generated rows would be cherry-picking."""
    from corpus.models import Scenario, SystemDescription
    from pipeline.models import Label

    assert SystemDescription.objects.filter(origin="generated").exists()

    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/pipeline-runs/{executed.pk}/")

    assert response.status_code == 204
    assert not PipelineRun.objects.filter(pk=executed.pk).exists()
    assert not PhaseRun.objects.filter(pipeline_run_id=executed.pk).exists()
    assert not SystemDescription.objects.filter(origin="generated").exists()
    assert not Scenario.objects.filter(origin="generated").exists()
    assert LLMCall.objects.count() == 0
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_a_run_in_flight_cannot_be_deleted(api_client, staff, executed):
    """A worker still holds it; deleting rows out from under it is a crash, not a delete."""
    api_client.force_authenticate(user=staff)
    for state in (PipelineRun.Status.QUEUED, PipelineRun.Status.RUNNING):
        executed.status = state
        executed.save(update_fields=["status"])
        response = api_client.delete(f"/api/pipeline-runs/{executed.pk}/")
        assert response.status_code == 409
        assert PipelineRun.objects.filter(pk=executed.pk).exists()


@pytest.mark.django_db
def test_deleting_the_last_run_frees_the_experiment_for_deletion(api_client, staff, executed):
    """Task 1's 409 tells the operator to delete the runs first. Prove that works."""
    experiment_id = executed.experiment_id
    api_client.force_authenticate(user=staff)

    assert api_client.delete(f"/api/experiments/{experiment_id}/").status_code == 409
    assert api_client.delete(f"/api/pipeline-runs/{executed.pk}/").status_code == 204
    assert api_client.delete(f"/api/experiments/{experiment_id}/").status_code == 204


@pytest.mark.django_db
def test_phase_runs_carry_the_isolation_evidence(api_client, member, executed):
    """A Run A phase run must be able to prove its leak scan ran."""
    api_client.force_authenticate(user=member)
    rows = api_client.get(f"/api/phase-runs/?pipeline_run={executed.pk}&phase=2").json()
    generation = rows["results"][0]

    assert generation["variant"] == "A"
    assert generation["leak_scan_ran"] is True
    assert generation["guarded_by"] == [executed.experiment.taxonomy_id]


@pytest.mark.django_db
def test_phase_runs_filter_by_phase_and_variant(api_client, member, executed, noise):
    """`noise` adds a second experiment's variant-B, PENDING phase runs, so
    each assertion below has a genuine non-match to exclude — without it,
    every one of these counts would be identical whether or not the filter
    it names actually ran."""
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/phase-runs/").json()["count"] == 4
    assert api_client.get("/api/phase-runs/?phase=3").json()["count"] == 2
    assert api_client.get("/api/phase-runs/?variant=A").json()["count"] == 2
    assert api_client.get("/api/phase-runs/?variant=B").json()["count"] == 2
    assert api_client.get("/api/phase-runs/?status=completed").json()["count"] == 2
    assert api_client.get("/api/phase-runs/?status=pending").json()["count"] == 2


@pytest.mark.django_db
def test_phase_runs_filter_by_pipeline_run(api_client, member, executed, noise):
    """`?pipeline_run=` was the one filter clause deletable with every test
    still green — its only prior user had a single pipeline run in the
    database, so `&phase=2` alone already narrowed to the same row. `noise`
    supplies a second pipeline run so this filter has a genuine non-match to
    exclude."""
    api_client.force_authenticate(user=member)
    body = api_client.get(f"/api/phase-runs/?pipeline_run={executed.pk}").json()
    assert body["count"] == 2
    assert {row["id"] for row in body["results"]} == set(
        executed.phase_runs.values_list("id", flat=True)
    )
    assert api_client.get("/api/phase-runs/").json()["count"] == 4


@pytest.mark.django_db
def test_leak_scan_ran_is_false_unless_every_call_ran_the_scan(api_client, member, executed):
    """`any(...)` used to report True off a single scanned call, even with
    other calls on the same run that had not been scanned — read by an
    operator as "isolation proven" when it was only partly proven. A second,
    UNSCANNED call on the same phase run must drag the whole run to False;
    the taxonomy that DID guard the first call must still show up."""
    api_client.force_authenticate(user=member)
    generation = executed.phase_runs.get(phase=2)
    scanned_call = generation.calls.get()
    assert scanned_call.leak_scan_ran is True  # sanity: the real run did scan

    LLMCall.objects.create(
        phase_run=generation, sequence=2, leak_scan_ran=False, guarded_by=None, parsed_ok=True
    )

    body = api_client.get(f"/api/phase-runs/{generation.pk}/").json()
    assert body["leak_scan_ran"] is False
    assert body["guarded_by"] == [scanned_call.guarded_by_id]


@pytest.mark.django_db
def test_leak_scan_ran_is_false_with_no_calls_archived_at_all(api_client, member, executed):
    """No calls at all must read as 'not proven', not vacuously True."""
    generation = executed.phase_runs.get(phase=2)
    bare = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m",
        prompt_template=generation.prompt_template,
        taxonomy=generation.taxonomy,
    )
    api_client.force_authenticate(user=member)
    body = api_client.get(f"/api/phase-runs/{bare.pk}/").json()
    assert body["leak_scan_ran"] is False
    assert body["guarded_by"] == []


@pytest.mark.django_db
def test_guarded_by_reports_disagreement_instead_of_picking_the_first(api_client, member, executed):
    """Two calls on the same run guarded by DIFFERENT taxonomies is a fact
    an operator needs, not something to silently resolve to whichever call
    happened to be first."""
    api_client.force_authenticate(user=member)
    generation = executed.phase_runs.get(phase=2)
    first_call = generation.calls.get()
    other_taxonomy = Taxonomy.objects.create(vocabulary_id="HCV-21-other", version="1.0")
    LLMCall.objects.create(
        phase_run=generation,
        sequence=2,
        leak_scan_ran=True,
        guarded_by=other_taxonomy,
        parsed_ok=True,
    )

    body = api_client.get(f"/api/phase-runs/{generation.pk}/").json()
    assert body["guarded_by"] == sorted([first_call.guarded_by_id, other_taxonomy.pk])


@pytest.mark.django_db
def test_a_bad_filter_value_is_a_400_not_a_500(api_client, member, executed):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/phase-runs/?phase=abc").status_code == 400
    assert api_client.get("/api/pipeline-runs/?experiment=abc").status_code == 400


@pytest.mark.django_db
def test_the_call_inspector_returns_both_prompts_and_the_raw_response(api_client, member, executed):
    api_client.force_authenticate(user=member)
    generation = executed.phase_runs.get(phase=2)
    body = api_client.get(f"/api/phase-runs/{generation.pk}/calls/").json()

    call = body["results"][0]
    assert call["rendered_user_prompt"]
    assert call["raw_response"]
    assert call["request_json"]["model"] == "m2"
    assert call["response_json"] is not None
    assert call["parsed_ok"] is True


@pytest.mark.django_db
def test_the_call_inspector_never_returns_a_key(api_client, member, executed, settings):
    """A key-shaped string archived on a call must never reach the inspector.

    `FakeLLMClient` never writes a key into any archived column on its own —
    asserting "sk-or- not in the response" against calls it produced alone
    proves nothing (that assertion still passes with `redact_call`
    monkeypatched to a no-op). Writing a real sentinel directly onto the
    call and saving it through the model exercises the exact write path
    `LLMCall.save()` -> `redact_call` guards, so this can actually fail if
    redaction breaks.
    """
    api_client.force_authenticate(user=member)
    generation = executed.phase_runs.get(phase=2)
    call = generation.calls.get()
    sentinel = settings.OPENROUTER_API_KEY  # "sk-or-test", set by the `executed` fixture
    call.raw_response = f"here is the leaked key: {sentinel}"
    call.response_json = {"error": {"message": f"bad key: {sentinel}"}}
    call.save()  # must go through .save(), never .update(), to be scrubbed

    blob = str(api_client.get(f"/api/phase-runs/{generation.pk}/calls/").json())
    assert sentinel not in blob
    assert "sk-or-" not in blob


@pytest.mark.django_db
def test_non_staff_cannot_retry(api_client, member, executed):
    api_client.force_authenticate(user=member)
    labelling = executed.phase_runs.get(phase=3)
    assert api_client.post(f"/api/phase-runs/{labelling.pk}/retry/").status_code == 403


@pytest.mark.django_db
def test_retrying_a_healthy_run_is_a_no_op(api_client, staff, executed):
    api_client.force_authenticate(user=staff)
    labelling = executed.phase_runs.get(phase=3)
    before = labelling.calls.count()

    response = api_client.post(f"/api/phase-runs/{labelling.pk}/retry/")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    labelling.refresh_from_db()
    assert labelling.calls.count() == before


@pytest.mark.django_db
def test_retrying_a_phase_two_run_is_refused(api_client, staff, executed):
    """Retry re-labels scenarios; it has no meaning for a generation run."""
    api_client.force_authenticate(user=staff)
    generation = executed.phase_runs.get(phase=2)
    response = api_client.post(f"/api/phase-runs/{generation.pk}/retry/")
    assert response.status_code == 400
    assert "phase 3" in str(response.json()).lower()


@pytest.mark.django_db
def test_retry_response_matches_a_fresh_get_not_a_stale_prefetch(api_client, staff, executed):
    """Regression test: `self.get_object()` in the `retry` action comes from
    a queryset that `prefetch_related("calls")`s; `retry_failed` deletes and
    re-creates that run's calls underneath it, which used to leave the
    cached `.calls.all()` on the response serializer stale. Force a failing
    call carrying isolation evidence the REAL retried call cannot reproduce
    (phase 3's render path never sets `leak_scan_ran`/`guarded_by`), so a
    stale response is distinguishable from a fresh one, then require the
    retry response to equal a subsequent plain GET byte for byte.
    """
    labelling = executed.phase_runs.get(phase=3)
    call = labelling.calls.get()
    call.parsed_ok = False
    call.parse_error = "forced failure for this test"
    call.leak_scan_ran = True
    call.guarded_by_id = executed.experiment.taxonomy_id
    call.save()

    get_llm_client().queue(LABELLED)
    api_client.force_authenticate(user=staff)
    retry_body = api_client.post(f"/api/phase-runs/{labelling.pk}/retry/").json()
    fresh_body = api_client.get(f"/api/phase-runs/{labelling.pk}/").json()

    assert retry_body == fresh_body
    assert retry_body["leak_scan_ran"] is False
    assert retry_body["guarded_by"] == []


@pytest.mark.django_db
def test_retry_refuses_when_the_generation_run_is_gone(api_client, staff, executed):
    """`retry_failed` re-derives its scenario list from `source_phase_run`;
    with it deleted, that list is empty, so `retry_failed` would delete this
    run's failed calls and archive nothing back in their place — an
    irreversible loss of the only record of what happened, for nothing. The
    view must refuse before that runs, and must not touch the archived call.
    """
    generation = executed.phase_runs.get(phase=2)
    labelling = executed.phase_runs.get(phase=3)
    call = labelling.calls.get()
    call.parsed_ok = False
    call.parse_error = "forced failure for this test"
    call.save()

    generation.delete()  # SET_NULL: labelling.source_phase_run_id -> NULL
    labelling.refresh_from_db()
    assert labelling.source_phase_run_id is None

    api_client.force_authenticate(user=staff)
    response = api_client.post(f"/api/phase-runs/{labelling.pk}/retry/")

    assert response.status_code == 409
    assert "no longer exists" in str(response.json()).lower()
    assert labelling.calls.filter(pk=call.pk).exists()
