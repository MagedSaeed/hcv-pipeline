import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Experiment, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def member(db):
    return get_user_model().objects.create_user(email="m@example.com", password="pw-123456")


@pytest.fixture
def oldsmar(db):
    system = SystemDescription.objects.create(title="Oldsmar", body="b", origin=Origin.SEED)
    Scenario.objects.create(system_description=system, scenario_id="sc_01", origin=Origin.SEED)
    return system


def card(scenario_id, **fields):
    return {"scenario_id": scenario_id, "threat": "t", "narrative": "n", **fields}


@pytest.mark.django_db
def test_bulk_creates_new_systems_and_scenarios_as_manual_rows(api_client, member, oldsmar):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {
            "systems": [
                {"title": "Riverbend", "body": "rb", "scenarios": [card("sc_01"), card("sc_02")]}
            ],
            "scenarios": [{"system_description": oldsmar.id, **card("sc_02")}],
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    created = list(Scenario.objects.filter(pk__in=response.json()["scenario_ids"]))
    assert len(created) == 3
    assert {s.origin for s in created} == {Origin.MANUAL}
    assert {s.created_by_id for s in created} == {member.id}
    riverbend = SystemDescription.objects.get(title="Riverbend")
    assert riverbend.origin == Origin.MANUAL
    assert riverbend.scenarios.count() == 2


@pytest.mark.django_db
def test_bulk_creates_nothing_when_one_draft_is_invalid(api_client, member, oldsmar):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {
            "systems": [{"title": "Riverbend", "body": "", "scenarios": [card("sc_01")]}],
            "scenarios": [{"system_description": oldsmar.id, **card("sc_01")}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["scenarios"]["0"]["non_field_errors"]
    assert not SystemDescription.objects.filter(title="Riverbend").exists()


@pytest.mark.django_db
def test_bulk_rejects_duplicate_ids_within_one_new_system(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {"systems": [{"title": "R", "scenarios": [card("sc_01"), card("sc_01")]}]},
        format="json",
    )
    assert response.status_code == 400
    errors = response.json()["systems"]["0"]["scenarios"]
    assert "0" not in errors
    assert "sc_01" in errors["1"]["scenario_id"][0]


@pytest.mark.django_db
def test_bulk_rejects_duplicate_ids_for_one_existing_system(api_client, member, oldsmar):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {
            "scenarios": [
                {"system_description": oldsmar.id, **card("sc_05")},
                {"system_description": oldsmar.id, **card("sc_05")},
            ]
        },
        format="json",
    )
    assert response.status_code == 400
    assert "sc_05" in response.json()["scenarios"]["1"]["scenario_id"][0]


@pytest.mark.django_db
def test_bulk_refuses_scenarios_on_a_generated_system(api_client, member):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1", is_active=True)
    gen = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=gen, taxonomy=tax
    )
    generated = SystemDescription.objects.create(
        title="G", origin=Origin.GENERATED, generation_run=run
    )
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {"scenarios": [{"system_description": generated.id, **card("sc_01")}]},
        format="json",
    )
    assert response.status_code == 400
    assert "generated" in response.json()["scenarios"]["0"]["system_description"][0]


@pytest.mark.django_db
def test_bulk_refuses_an_empty_body(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post("/api/scenarios/bulk/", {}, format="json")
    assert response.status_code == 400
    assert response.json()["non_field_errors"]


@pytest.mark.django_db
def test_bulk_requires_authentication(api_client):
    response = api_client.post("/api/scenarios/bulk/", {"systems": []}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_labelled_in_run_lists_only_that_runs_chosen_scenarios(api_client, member, oldsmar):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="x", order=7)
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    chosen = oldsmar.scenarios.get()
    other = Scenario.objects.create(
        system_description=oldsmar, scenario_id="sc_02", origin=Origin.SEED
    )
    experiment = Experiment.objects.create(
        name="p3", taxonomy=tax, phases=[3], phase3_model="m3", phase3_prompt=lab
    )
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    PhaseRun.objects.create(
        pipeline_run=run,
        phase=3,
        model="m3",
        prompt_template=lab,
        taxonomy=tax,
        scenario_ids=[chosen.id],
    )
    api_client.force_authenticate(user=member)
    rows = api_client.get(f"/api/scenarios/?labelled_in_run={run.id}").json()["results"]
    ids = [row["id"] for row in rows]
    assert ids == [chosen.id]
    assert other.id not in ids
    assert api_client.get("/api/scenarios/?labelled_in_run=0").json()["results"] == []
