import json

import pytest

from analyses.models import AnalysisItem

pytestmark = pytest.mark.django_db

REPLY = json.dumps(
    {
        "elements": [
            {
                "id": "Residents",
                "section": "",
                "field": "person_or_role",
                "value": "Citizen",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            },
            {
                "id": "MoveAI",
                "section": "",
                "field": "organization",
                "value": "Startup",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            },
            {
                "id": "TripStore",
                "section": "",
                "field": "data_store",
                "value": "DataStore",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            },
        ],
        "facts": [
            {
                "section": "",
                "field": "vulnerability_factor",
                "subject_id": "Residents",
                "value": "Factor_Disability",
                "fact_type": "Possible",
                "reason": "Some upload a medical letter.",
                "other_reading": "",
            },
            {
                "section": "",
                "field": "retention_days",
                "subject_id": "TripStore",
                "value": "1095",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            },
        ],
        "controls": [
            {
                "section": "8. Controls",
                "element_id": "TripStore",
                "control": "PasswordControl",
                "state": "absent",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            }
        ],
        "gaps": {"not_assessable": [{"area": "Physical entry", "reason": "None."}], "no_term": []},
    }
)


@pytest.fixture
def client(client_for, staff):
    return client_for(staff)


@pytest.fixture
def extracted_api(client, fresh_analysis, queue, fake):
    fake.queue(REPLY)
    assert client.post(f"/api/analyses/{fresh_analysis.pk}/extract/").status_code == 202
    queue.drain()
    return fresh_analysis


def url(analysis, tail=""):
    return f"/api/analyses/{analysis.pk}/{tail}"


def item(analysis, ref, field):
    return AnalysisItem.objects.get(analysis=analysis, ref=ref, field=field)


def test_detail_shows_the_extraction(client, extracted_api):
    body = client.get(url(extracted_api)).json()["extraction"]

    assert (body["status"], body["pending"]) == ("current", 6)
    assert body["settings"] == {"model": "anthropic/claude-sonnet-5.5", "temperature": 0.0}
    assert body["gaps"]["not_assessable"] == [{"area": "Physical entry", "reason": "None."}]
    assert body["run"]["status"] == "completed"
    first = body["items"][0]
    assert {
        key: first[key] for key in ("ref", "field", "value", "fact_type", "decision", "edited")
    } == {
        "ref": "Residents",
        "field": "person_or_role",
        "value": "Citizen",
        "fact_type": "Asserted",
        "decision": "pending",
        "edited": False,
    }
    assert "Not an allowed value" in body["items"][1]["problem"]


def test_editing_to_an_allowed_value_clears_the_problem(client, extracted_api):
    target = item(extracted_api, "MoveAI", "organization")

    response = client.patch(
        url(extracted_api, f"items/{target.pk}/"),
        {"value": "PrivateSectorOrganization"},
        format="json",
    )

    assert response.status_code == 200
    target.refresh_from_db()
    assert (target.value, target.original_value, target.problem) == (
        "PrivateSectorOrganization",
        "Startup",
        "",
    )
    assert response.json()["edited"] is True


@pytest.mark.parametrize(
    ("ref", "field", "body"),
    [
        ("MoveAI", "organization", {"value": "Startup Inc"}),
        ("TripStore", "retention_days", {"value": "three years"}),
        ("TripStore", "control", {"state": "missing"}),
        ("TripStore", "control", {"value": "Encryption"}),
        ("Residents", "person_or_role", {"decision": "maybe"}),
    ],
)
def test_invalid_edits_are_refused(client, extracted_api, ref, field, body):
    target = item(extracted_api, ref, field)

    response = client.patch(url(extracted_api, f"items/{target.pk}/"), body, format="json")

    assert response.status_code == 400


def test_controls_switch_state_and_family_members_are_allowed(client, extracted_api):
    target = item(extracted_api, "TripStore", "control")

    response = client.patch(
        url(extracted_api, f"items/{target.pk}/"),
        {"value": "MultiFactorAuthentication", "state": "present"},
        format="json",
    )

    assert response.status_code == 200
    target.refresh_from_db()
    assert (target.value, target.state, target.original_state) == (
        "MultiFactorAuthentication",
        "present",
        "absent",
    )


def test_a_value_with_a_problem_cannot_be_approved(client, extracted_api):
    target = item(extracted_api, "MoveAI", "organization")

    response = client.patch(
        url(extracted_api, f"items/{target.pk}/"), {"decision": "approved"}, format="json"
    )

    assert response.status_code == 409


def test_approve_all_asserted_skips_problems_and_possible(client, extracted_api):
    response = client.post(url(extracted_api, "items/approve-asserted/"))

    assert response.status_code == 200
    decisions = {(i.ref, i.field): i.decision for i in extracted_api.items.all()}
    assert decisions[("Residents", "person_or_role")] == "approved"
    assert decisions[("MoveAI", "organization")] == "pending"
    assert decisions[("Residents", "vulnerability_factor")] == "pending"


def test_detail_carries_the_body_the_models_read(client, extracted_api):
    target = item(extracted_api, "Residents", "person_or_role")
    client.patch(url(extracted_api, f"items/{target.pk}/"), {"decision": "approved"}, format="json")

    body = client.get(url(extracted_api)).json()["extraction"]["body"]

    assert body.startswith("## Structured description\n")
    assert "- Residents (Citizen)" in body
    assert "MoveAI" not in body.split("## Original description")[0]
    assert body.rstrip().endswith(extracted_api.description.rstrip())


def test_approve_section_approves_its_values_except_problems(client, extracted_api):
    response = client.post(
        url(extracted_api, "items/approve-section/"),
        {"section": "1. People and organizations"},
        format="json",
    )

    assert response.status_code == 200
    decisions = {(i.ref, i.field): i.decision for i in extracted_api.items.all()}
    assert decisions[("Residents", "person_or_role")] == "approved"
    assert decisions[("Residents", "vulnerability_factor")] == "approved"
    assert decisions[("MoveAI", "organization")] == "pending"
    others = extracted_api.items.exclude(section="1. People and organizations")
    assert set(others.values_list("decision", flat=True)) == {"pending"}


def test_approve_section_needs_a_known_section(client, extracted_api):
    response = client.post(
        url(extracted_api, "items/approve-section/"), {"section": "9. Nope"}, format="json"
    )

    assert response.status_code == 400


def test_rejected_element_marks_what_refers_to_it(client, extracted_api):
    store = item(extracted_api, "TripStore", "data_store")
    client.patch(url(extracted_api, f"items/{store.pk}/"), {"decision": "rejected"}, format="json")

    items = client.get(url(extracted_api)).json()["extraction"]["items"]

    retention = next(i for i in items if i["field"] == "retention_days")
    assert retention["refers_to_rejected"] is True


def test_extraction_settings_are_validated(client, fresh_analysis):
    good = client.patch(
        url(fresh_analysis),
        {"extraction_settings": {"model": "openai/x", "temperature": 0.3}},
        format="json",
    )
    bad = client.patch(
        url(fresh_analysis), {"extraction_settings": {"model": "", "temperature": 5}}, format="json"
    )

    assert good.status_code == 200
    assert good.json()["extraction"]["settings"] == {"model": "openai/x", "temperature": 0.3}
    assert bad.status_code == 400


def test_edits_are_refused_once_locked(client, extracted_api):
    extracted_api.scenarios.create(scenario_id="sc_01", narrative="x")
    target = item(extracted_api, "Residents", "person_or_role")

    edit = client.patch(
        url(extracted_api, f"items/{target.pk}/"), {"decision": "rejected"}, format="json"
    )
    again = client.post(url(extracted_api, "extract/"))
    approve_all = client.post(url(extracted_api, "items/approve-asserted/"))
    approve_section = client.post(
        url(extracted_api, "items/approve-section/"),
        {"section": "1. People and organizations"},
        format="json",
    )

    assert (edit.status_code, again.status_code, approve_all.status_code) == (409, 400, 409)
    assert approve_section.status_code == 409


def test_non_staff_cannot_extract_or_edit(client_for, member, extracted_api):
    member_client = client_for(member)
    target = item(extracted_api, "Residents", "person_or_role")

    assert member_client.post(url(extracted_api, "extract/")).status_code == 403
    assert (
        member_client.patch(
            url(extracted_api, f"items/{target.pk}/"), {"decision": "approved"}, format="json"
        ).status_code
        == 403
    )


def test_options_list_the_fields_for_editing(client, fresh_analysis):
    body = client.get("/api/analyses/options/").json()["extraction"]

    assert body["fields"]["organization"]["allowed"][0] == "GovernmentOrganization"
    assert body["fields"]["supplier"]["value_kind"] == "element"
    assert "MultiFactorAuthentication" in body["controls"]
    assert "PasswordControl" in body["controls"]
    assert body["defaults"] == {"model": "anthropic/claude-sonnet-5.5", "temperature": 0.0}
    assert body["prompt"] == "System description extractor v1"


def test_report_counts_the_review(client, extracted_api, queue, fake):
    client.post(url(extracted_api, "items/approve-asserted/"))
    for ref, field, change in [
        ("MoveAI", "organization", {"value": "PrivateSectorOrganization", "decision": "approved"}),
        ("Residents", "vulnerability_factor", {"decision": "rejected"}),
    ]:
        client.patch(
            url(extracted_api, f"items/{item(extracted_api, ref, field).pk}/"),
            change,
            format="json",
        )

    add(client, extracted_api, field="retention_days", ref="Residents", value="30")

    details = client.get(url(extracted_api, "report/")).json()["details"]["extraction"]

    assert details["model"] == "anthropic/claude-sonnet-5.5"
    assert details["prompt"] == "System description extractor v1"
    counts = ("approved", "edited", "added", "rejected")
    assert tuple(details[key] for key in counts) == (6, 1, 1, 1)


def add(client, analysis, **body):
    return client.post(url(analysis, "items/"), body, format="json")


def test_an_added_element_is_approved_and_marked(client, extracted_api):
    response = add(client, extracted_api, field="interface", ref="KioskScreen", value="mobileapp")

    assert response.status_code == 201
    added = response.json()
    assert {k: added[k] for k in ("kind", "section", "value", "decision", "added", "edited")} == {
        "kind": "element",
        "section": "2. System components",
        "value": "MobileApp",
        "decision": "approved",
        "added": True,
        "edited": False,
    }


def test_added_facts_and_controls_name_an_element(client, extracted_api):
    fact = add(client, extracted_api, field="retention_days", ref="TripStore", value="30")
    control = add(
        client,
        extracted_api,
        field="control",
        ref="TripStore",
        value="AnomalyAlerting",
        state="present",
    )
    dangling = add(client, extracted_api, field="retention_days", ref="Nobody", value="30")

    assert (fact.status_code, control.status_code, dangling.status_code) == (201, 201, 400)
    assert control.json()["section"] == "8. Controls"


@pytest.mark.parametrize(
    "body",
    [
        {"field": "interface", "ref": "Residents", "value": "MobileApp"},
        {"field": "interface", "ref": "two words", "value": "MobileApp"},
        {"field": "interface", "ref": "Kiosk", "value": "NotAType"},
        {"field": "nonsense", "ref": "Kiosk", "value": "MobileApp"},
        {"field": "control", "ref": "TripStore", "value": "AnomalyAlerting"},
    ],
)
def test_an_added_value_is_checked(client, extracted_api, body):
    assert add(client, extracted_api, **body).status_code == 400


def test_only_added_values_can_be_deleted(client, extracted_api):
    added = add(client, extracted_api, field="retention_days", ref="TripStore", value="30").json()
    extracted = item(extracted_api, "TripStore", "data_store")

    gone = client.delete(url(extracted_api, f"items/{added['id']}/"))
    kept = client.delete(url(extracted_api, f"items/{extracted.pk}/"))

    assert (gone.status_code, kept.status_code) == (204, 409)
    assert not AnalysisItem.objects.filter(pk=added["id"]).exists()


def test_renaming_an_element_carries_its_facts_and_controls(client, extracted_api):
    store = item(extracted_api, "TripStore", "data_store")
    add(client, extracted_api, field="source", ref="Residents", value="TripStore")

    response = client.patch(
        url(extracted_api, f"items/{store.pk}/"), {"ref": "TripDB"}, format="json"
    )

    assert response.status_code == 200
    refs = {(i.field, i.ref, i.value) for i in extracted_api.items.all()}
    assert ("retention_days", "TripDB", "1095") in refs
    assert ("control", "TripDB", "PasswordControl") in refs
    assert ("source", "Residents", "TripDB") in refs
    assert not extracted_api.items.filter(ref="TripStore").exists()


def test_a_rename_must_be_free_and_well_formed(client, extracted_api):
    store = item(extracted_api, "TripStore", "data_store")
    taken = client.patch(
        url(extracted_api, f"items/{store.pk}/"), {"ref": "Residents"}, format="json"
    )
    spaced = client.patch(
        url(extracted_api, f"items/{store.pk}/"), {"ref": "Trip DB"}, format="json"
    )

    assert (taken.status_code, spaced.status_code) == (400, 400)


def test_a_fact_moves_to_another_element(client, extracted_api):
    days = item(extracted_api, "TripStore", "retention_days")

    moved = client.patch(
        url(extracted_api, f"items/{days.pk}/"), {"ref": "Residents"}, format="json"
    )
    lost = client.patch(url(extracted_api, f"items/{days.pk}/"), {"ref": "Nobody"}, format="json")

    assert (moved.status_code, lost.status_code) == (200, 400)
    assert item(extracted_api, "Residents", "retention_days").pk == days.pk


def test_adding_and_deleting_are_refused_once_locked(client, extracted_api):
    added = add(client, extracted_api, field="retention_days", ref="TripStore", value="30").json()
    extracted_api.scenarios.create(scenario_id="sc_01", narrative="x")

    more = add(client, extracted_api, field="retention_days", ref="TripStore", value="31")
    gone = client.delete(url(extracted_api, f"items/{added['id']}/"))

    assert (more.status_code, gone.status_code) == (409, 409)
