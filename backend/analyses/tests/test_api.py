from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from analyses import state
from analyses.models import Analysis, AnalysisCall, AnalysisRun
from taxonomy.models import HCV, Taxonomy

pytestmark = pytest.mark.django_db

BASE = "/api/analyses/"


def url(analysis, tail=""):
    return f"{BASE}{analysis.pk}/{tail}"


@pytest.fixture
def client(client_for, staff):
    return client_for(staff)


@pytest.fixture
def generated(analysis):
    run = analysis.runs.create(kind="generate", status="completed")
    analysis.scenarios.create(
        scenario_id="sc_01",
        trigger="\nA morning shift.\n1. PO1 signs in.",
        narrative="PO1 signs in.",
        generated_by=run,
        order=1,
    )
    analysis.prompt_title = analysis.name
    analysis.save()
    return analysis


def test_signed_out_is_refused(api_client, analysis):
    assert api_client.get(BASE).status_code == 403
    assert api_client.get(url(analysis)).status_code == 403


def test_non_staff_reads_everything_and_writes_nothing(client_for, member, generated):
    client = client_for(member)
    scenario = generated.scenarios.get()

    assert client.get(f"{BASE}?scope=all").json()["count"] == 1
    assert client.get(url(generated)).status_code == 200
    assert client.get(url(generated, "report/")).status_code == 200
    assert client.get(f"{BASE}options/").status_code == 200

    writes = [
        client.post(BASE, {}, format="json"),
        client.patch(url(generated), {"name": "x"}, format="json"),
        client.delete(url(generated)),
        client.post(url(generated, "start-new/")),
        client.post(url(generated, "generate/")),
        client.post(url(generated, "label/")),
        client.post(url(generated, "cancel/")),
        client.post(url(generated, "scenarios/"), {"narrative": "x"}, format="json"),
        client.patch(
            url(generated, f"scenarios/{scenario.pk}/"), {"set_aside": True}, format="json"
        ),
        client.delete(url(generated, f"scenarios/{scenario.pk}/")),
    ]
    assert [response.status_code for response in writes] == [403] * len(writes)


def test_staff_edit_others(client_for, analysis, django_user_model):
    other = django_user_model.objects.create_user(
        email="other@example.com", password="pw-123456", is_staff=True
    )

    response = client_for(other).patch(url(analysis), {"name": "Edited"}, format="json")

    assert response.status_code == 200
    assert response.json()["name"] == "Edited"


def test_list_is_mine_by_default(client_for, fresh_analysis, member):
    client = client_for(member)

    assert client.get(BASE).json()["results"] == []
    rows = client.get(f"{BASE}?scope=all").json()["results"]
    assert rows[0]["owner_email"] == "analyst@example.com"
    assert rows[0]["resume_step"] == "system"
    assert rows[0]["steps"]["system"] is False


def test_create_starts_with_the_recommended_setup(client, v12):
    response = client.post(BASE, {"description": "Sensors."}, format="json")

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["description"], body["locked"]) == ("", "Sensors.", False)
    assert body["taxonomy"]["version"] == "1.2"
    assert body["generation_settings"] == state.default_settings("generation")
    assert body["labelling_settings"] == state.default_settings("labelling")
    assert body["latest_runs"] == {"extract": None, "generate": None, "label": None}
    assert body["extraction"]["status"] == "none"
    assert body["extraction"]["settings"] == state.default_settings("extraction")
    assert body["extraction_settings"] == state.default_settings("extraction")
    assert body["scenarios"] == []


def test_no_default_taxonomy_is_a_clear_409(client, v12):
    Taxonomy.objects.update(is_analysis_default=False)

    response = client.post(BASE, {}, format="json")

    assert response.status_code == 409
    assert response.json()["code"] == "no_taxonomy"
    assert "default for analyses" in response.json()["detail"]


def test_description_limit(client, analysis):
    ok = client.patch(url(analysis), {"description": "x" * 50_000}, format="json")
    too_long = client.patch(url(analysis), {"description": "x" * 50_001}, format="json")

    assert (ok.status_code, too_long.status_code) == (200, 400)


def test_whitespace_in_a_description_is_kept(client, analysis):
    text = "Purpose and scope\n\n  A parking app.\n"

    assert client.patch(url(analysis), {"description": text}, format="json").status_code == 200
    analysis.refresh_from_db()
    assert analysis.description == text


def test_locked_description_and_taxonomy_are_409_rename_is_200(client, generated):
    v11 = Taxonomy.active()

    description = client.patch(url(generated), {"description": "New."}, format="json")
    taxonomy = client.patch(url(generated), {"taxonomy": v11.pk}, format="json")
    blank = client.patch(url(generated), {"name": " "}, format="json")
    renamed = client.patch(url(generated), {"name": "Riyadh pilot"}, format="json")

    assert (description.status_code, taxonomy.status_code) == (409, 409)
    assert description.json()["code"] == "locked"
    assert blank.status_code == 400
    assert renamed.status_code == 200
    generated.refresh_from_db()
    assert (generated.name, generated.prompt_title) == ("Riyadh pilot", "Smart Parking")


def test_settings_are_validated_and_saved(client, analysis):
    good = {**analysis.generation_settings, "count": 12, "temperature": 1}
    bad = {**analysis.generation_settings, "count": 40}

    assert (
        client.patch(url(analysis), {"generation_settings": bad}, format="json").status_code == 400
    )
    saved = client.patch(url(analysis), {"generation_settings": good}, format="json")

    assert saved.status_code == 200
    assert saved.json()["generation_settings"]["count"] == 12
    assert saved.json()["generation_settings"]["temperature"] == 1.0


def test_taxonomy_change_revalidates_both_settings(client, analysis):
    other = Taxonomy.objects.create(vocabulary_id="OTHER", version="1")
    HCV.objects.create(taxonomy=other, hcv_id="X-1", name="Other")

    refused = client.patch(url(analysis), {"taxonomy": other.pk}, format="json")
    zero_shot = {**analysis.labelling_settings, "examples": []}
    accepted = client.patch(
        url(analysis), {"taxonomy": other.pk, "labelling_settings": zero_shot}, format="json"
    )

    assert refused.status_code == 400
    assert "labelling_settings" in refused.json()
    assert accepted.status_code == 200


def test_options_follow_the_taxonomy(client, v12):
    body = client.get(f"{BASE}options/?taxonomy={v12.pk}").json()

    assert body["generation"]["prompts"][0]["recommended"] is True
    assert len(body["labelling"]["examples"]) == 5
    assert client.get(f"{BASE}options/?taxonomy=999").status_code == 404
    assert client.get(f"{BASE}options/?taxonomy=abc").status_code == 400


def test_scenario_json(client, generated):
    row = client.get(url(generated)).json()["scenarios"][0]

    assert row["title"] == "A morning shift."
    assert row["is_manual"] is False
    assert row["label_status"] == "unlabelled"
    assert (row["error"], row["labels"]) == ("", [])


def test_generated_content_is_immutable_and_set_aside_works(client, generated):
    path = url(generated, f"scenarios/{generated.scenarios.get().pk}/")

    edit = client.patch(path, {"narrative": "Rewritten."}, format="json")
    delete = client.delete(path)
    aside = client.patch(path, {"set_aside": True}, format="json")

    assert (edit.status_code, delete.status_code) == (409, 409)
    assert aside.status_code == 200
    scenario = generated.scenarios.get()
    assert (scenario.set_aside, scenario.narrative) == (True, "PO1 signs in.")
    assert client.patch(path, {"set_aside": False}, format="json").status_code == 200


def test_manual_scenario_can_be_edited_and_deleted(client, generated):
    created = client.post(
        url(generated, "scenarios/"), {"narrative": "A badge is found."}, format="json"
    )
    path = url(generated, f"scenarios/{created.json()['id']}/")

    assert created.status_code == 201
    assert created.json()["title"] == "A badge is found."
    assert created.json()["is_manual"] is True
    assert client.patch(path, {"narrative": "  "}, format="json").status_code == 400
    edited = client.patch(path, {"narrative": "A key is found.", "actor": "C1"}, format="json")
    assert (edited.status_code, edited.json()["actor"]) == (200, "C1")
    assert client.delete(path).status_code == 204


def test_manual_needs_narrative_and_a_first_generation(client, analysis, generated):
    assert (
        client.post(url(generated, "scenarios/"), {"actor": "x"}, format="json").status_code == 400
    )

    generated.scenarios.all().delete()
    response = client.post(url(generated, "scenarios/"), {"narrative": "x"}, format="json")

    assert response.status_code == 409


def test_manual_ids_start_at_101_and_are_never_reused(client, generated):
    path = url(generated, "scenarios/")

    first = client.post(path, {"narrative": "one"}, format="json").json()
    client.delete(f"{path}{first['id']}/")
    second = client.post(path, {"narrative": "two"}, format="json").json()

    assert (first["scenario_id"], second["scenario_id"]) == ("sc_101", "sc_102")


def test_manual_id_skips_a_generated_one(client, generated):
    generated.scenarios.create(scenario_id="sc_101", narrative="generated")

    created = client.post(url(generated, "scenarios/"), {"narrative": "mine"}, format="json")

    assert created.json()["scenario_id"] == "sc_102"


def test_adding_or_restoring_a_31st_is_refused(client, generated):
    for number in range(2, 31):
        generated.scenarios.create(scenario_id=f"sc_{number:02d}", narrative="x")
    extra = generated.scenarios.create(scenario_id="sc_31", narrative="x", set_aside=True)

    added = client.post(url(generated, "scenarios/"), {"narrative": "one more"}, format="json")
    restored = client.patch(
        url(generated, f"scenarios/{extra.pk}/"), {"set_aside": False}, format="json"
    )

    assert (added.status_code, restored.status_code) == (409, 409)
    assert added.json()["code"] == "over_limit"


def test_settings_and_scenarios_refused_during_a_run(client, generated):
    generated.runs.create(kind="label", status="running", progressed_at=timezone.now())
    scenario = generated.scenarios.get()

    responses = [
        client.patch(
            url(generated),
            {"labelling_settings": {**generated.labelling_settings, "examples": []}},
            format="json",
        ),
        client.post(url(generated, "scenarios/"), {"narrative": "x"}, format="json"),
        client.patch(
            url(generated, f"scenarios/{scenario.pk}/"), {"set_aside": True}, format="json"
        ),
    ]

    assert [response.status_code for response in responses] == [409] * 3
    assert responses[0].json()["code"] == "already_running"
    assert client.patch(url(generated), {"name": "Renamed"}, format="json").status_code == 200


def test_scenario_of_another_analysis_is_404(client, generated, staff, v12):
    other = Analysis.objects.create(owner=staff, taxonomy=v12, preset_version="1")
    scenario = generated.scenarios.get()

    response = client.patch(
        url(other, f"scenarios/{scenario.pk}/"), {"set_aside": True}, format="json"
    )

    assert response.status_code == 404


def test_start_new_copies_only_description(client, generated):
    generated.generation_settings = {**generated.generation_settings, "count": 3}
    generated.save()

    first = client.post(url(generated, "start-new/"))
    second = client.post(url(generated, "start-new/"))
    again = client.post(f"{BASE}{first.json()['id']}/start-new/")

    assert first.status_code == 201
    body = first.json()
    assert body["name"] == "Smart Parking (2)"
    assert second.json()["name"] == "Smart Parking (3)"
    assert again.json()["name"] == "Smart Parking (4)"
    assert body["description"] == generated.description
    assert body["locked"] is False
    assert body["scenarios"] == []
    assert body["generation_settings"]["count"] == 8


def test_generate_label_cancel(client, analysis, queue):
    started = client.post(url(analysis, "generate/"))
    again = client.post(url(analysis, "generate/"))
    cancelled = client.post(url(analysis, "cancel/"))

    assert started.status_code == 202
    assert started.json()["status"] == "queued"
    assert set(started.json()) == {
        "id",
        "kind",
        "status",
        "total",
        "done",
        "failed",
        "notice",
        "error",
        "created_at",
        "working_on",
    }
    assert (again.status_code, again.json()["code"]) == (409, "already_running")
    assert cancelled.json()["status"] == "cancelled"
    assert client.post(url(analysis, "cancel/")).status_code == 409
    assert client.post(url(analysis, "label/")).status_code == 400


def test_key_and_queue_refusals_are_503(client, analysis, queue, settings):
    queue.error = ConnectionError("redis is down")
    down = client.post(url(analysis, "generate/"))
    settings.OPENROUTER_API_KEY = ""
    keyless = client.post(url(analysis, "generate/"))

    assert (down.status_code, down.json()["code"]) == (503, "queue_unavailable")
    assert (keyless.status_code, keyless.json()["code"]) == (503, "missing_key")


def test_reading_expires_a_stuck_run(client, generated):
    long_ago = timezone.now() - timedelta(days=1)
    run = generated.runs.create(kind="label", status="running", progressed_at=long_ago)

    body = client.get(url(generated)).json()

    run.refresh_from_db()
    assert run.status == "failed"
    assert body["latest_runs"]["label"]["status"] == "failed"


def test_delete_works_during_a_run(client, generated):
    generated.runs.create(kind="label", status="running", progressed_at=timezone.now())

    assert client.delete(url(generated)).status_code == 204
    assert Analysis.objects.count() == 0
    assert AnalysisRun.objects.count() == 0


def test_polling_detail_does_not_query_per_scenario(client, generated):
    run = generated.runs.create(kind="label", status="completed")

    def queries():
        with CaptureQueriesContext(connection) as captured:
            assert client.get(url(generated)).status_code == 200
        return captured.captured_queries

    before = len(queries())
    for number in range(2, 8):
        scenario = generated.scenarios.create(scenario_id=f"sc_{number:02d}", narrative="x")
        AnalysisCall.objects.create(run=run, scenario=scenario, error="failed")
    after = queries()

    # One more query at most: the calls prefetch, once there are calls.
    assert len(after) <= before + 1
    assert not any("rendered_user_prompt" in query["sql"] for query in after)
