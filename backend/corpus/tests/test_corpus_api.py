import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, Scenario, SystemDescription

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def seed_system(db):
    return SystemDescription.objects.create(
        title="Seed system", body="original", origin=Origin.SEED
    )


@pytest.fixture
def generated_system(db):
    return SystemDescription.objects.create(
        title="Generated system", body="model output", origin=Origin.GENERATED
    )


@pytest.mark.django_db
def test_seed_rows_can_be_edited(api_client, member, seed_system):
    api_client.force_authenticate(user=member)
    response = api_client.patch(
        f"/api/system-descriptions/{seed_system.id}/",
        {"body": "revised"},
        format="json",
    )
    assert response.status_code == 200
    seed_system.refresh_from_db()
    assert seed_system.body == "revised"


@pytest.mark.django_db
def test_generated_rows_reject_patch(api_client, member, generated_system):
    api_client.force_authenticate(user=member)
    response = api_client.patch(
        f"/api/system-descriptions/{generated_system.id}/",
        {"body": "tampered"},
        format="json",
    )
    assert response.status_code == 403
    generated_system.refresh_from_db()
    assert generated_system.body == "model output"


@pytest.mark.django_db
def test_generated_rows_reject_delete(api_client, member, generated_system):
    api_client.force_authenticate(user=member)
    response = api_client.delete(f"/api/system-descriptions/{generated_system.id}/")
    assert response.status_code == 403
    assert SystemDescription.objects.filter(pk=generated_system.pk).exists()


@pytest.mark.django_db
def test_seed_rows_can_be_deleted(api_client, member, seed_system):
    api_client.force_authenticate(user=member)
    assert api_client.delete(f"/api/system-descriptions/{seed_system.id}/").status_code == 204


@pytest.mark.django_db
def test_generated_rows_reject_put(api_client, member, generated_system):
    api_client.force_authenticate(user=member)
    response = api_client.put(
        f"/api/system-descriptions/{generated_system.id}/",
        {"title": "Replaced", "body": "tampered"},
        format="json",
    )
    assert response.status_code == 403
    generated_system.refresh_from_db()
    assert generated_system.title == "Generated system"
    assert generated_system.body == "model output"


@pytest.mark.django_db
def test_generated_rows_cannot_unlock_themselves_via_origin(api_client, member, generated_system):
    scenario = Scenario.objects.create(
        system_description=generated_system,
        scenario_id="sc_01",
        actor="A",
        origin=Origin.GENERATED,
        order=1,
    )
    api_client.force_authenticate(user=member)
    response = api_client.patch(
        f"/api/scenarios/{scenario.id}/", {"origin": "manual"}, format="json"
    )
    assert response.status_code == 403
    scenario.refresh_from_db()
    assert scenario.origin == Origin.GENERATED


@pytest.mark.django_db
def test_generated_scenarios_are_also_immutable(api_client, member, generated_system):
    scenario = Scenario.objects.create(
        system_description=generated_system,
        scenario_id="sc_01",
        actor="A",
        origin=Origin.GENERATED,
        order=1,
    )
    api_client.force_authenticate(user=member)
    assert (
        api_client.patch(
            f"/api/scenarios/{scenario.id}/", {"actor": "B"}, format="json"
        ).status_code
        == 403
    )


@pytest.mark.django_db
def test_generated_rows_are_still_readable(api_client, member, generated_system):
    api_client.force_authenticate(user=member)
    body = api_client.get(f"/api/system-descriptions/{generated_system.id}/").json()
    assert body["origin"] == "generated"
    assert body["is_editable"] is False


@pytest.mark.django_db
def test_creating_a_row_through_the_api_is_manual_by_default(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/system-descriptions/", {"title": "Typed in", "body": "x"}, format="json"
    )
    assert response.status_code == 201
    assert response.json()["origin"] == "manual"


@pytest.mark.django_db
def test_origin_cannot_be_set_to_generated_through_the_api(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/system-descriptions/",
        {"title": "Faked", "body": "x", "origin": "generated"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["origin"] == "manual"


@pytest.mark.django_db
def test_filter_by_origin(api_client, member, seed_system, generated_system):
    api_client.force_authenticate(user=member)
    rows = api_client.get("/api/system-descriptions/?origin=seed").json()["results"]
    assert [row["title"] for row in rows] == ["Seed system"]


@pytest.mark.django_db
def test_a_system_with_generated_scenarios_cannot_be_deleted(api_client, member, seed_system):
    """Deleting the target system would cascade a run's output away.

    ReadOnlyIfGenerated is object-level on the addressed row, and a seed system
    is editable, so nothing else stops this. Generated material is removed by
    deleting the run, never a row at a time.
    """
    Scenario.objects.create(
        system_description=seed_system, scenario_id="sc_01", origin=Origin.GENERATED, order=1
    )
    api_client.force_authenticate(user=member)

    response = api_client.delete(f"/api/system-descriptions/{seed_system.id}/")

    assert response.status_code == 403
    assert "run" in str(response.json())
    assert SystemDescription.objects.filter(pk=seed_system.pk).exists()
    assert Scenario.objects.count() == 1


@pytest.mark.django_db
def test_a_system_with_only_manual_scenarios_can_still_be_deleted(api_client, member, seed_system):
    Scenario.objects.create(
        system_description=seed_system, scenario_id="sc_01", origin=Origin.MANUAL, order=1
    )
    api_client.force_authenticate(user=member)

    assert api_client.delete(f"/api/system-descriptions/{seed_system.id}/").status_code == 204
    assert Scenario.objects.count() == 0


@pytest.mark.django_db
def test_scenarios_can_be_filtered_by_system(api_client, member, seed_system, generated_system):
    Scenario.objects.create(system_description=seed_system, scenario_id="sc_01", order=1)
    Scenario.objects.create(system_description=generated_system, scenario_id="sc_02", order=1)
    api_client.force_authenticate(user=member)

    rows = api_client.get(f"/api/scenarios/?system={seed_system.id}").json()["results"]

    assert [row["scenario_id"] for row in rows] == ["sc_01"]


@pytest.mark.django_db
def test_a_non_integer_system_filter_is_a_bad_request(api_client, member):
    """Regression: the raw value used to reach the ORM and raise a 500."""
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/scenarios/?system=abc")

    assert response.status_code == 400
    assert "system" in response.json()


@pytest.mark.django_db
def test_posting_a_scenario_as_generated_still_stores_it_as_manual(api_client, member, seed_system):
    """`origin` is read-only on the serializer, which no test reached before:
    the immutability permission short-circuited every existing case."""
    api_client.force_authenticate(user=member)

    response = api_client.post(
        "/api/scenarios/",
        {
            "system_description": seed_system.id,
            "scenario_id": "sc_01",
            "actor": "A",
            "origin": "generated",
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["origin"] == "manual"
    assert Scenario.objects.get().origin == Origin.MANUAL


@pytest.mark.django_db
def test_a_scenario_reports_its_author_and_cannot_be_attributed_elsewhere(
    api_client, member, seed_system
):
    other = User.objects.create_user(email="other@example.com", password="pw-123456")
    api_client.force_authenticate(user=member)

    response = api_client.post(
        "/api/scenarios/",
        {
            "system_description": seed_system.id,
            "scenario_id": "sc_01",
            "actor": "A",
            "created_by": other.id,
        },
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["created_by"] == member.id
    assert Scenario.objects.get().created_by == member


@pytest.mark.django_db
def test_posting_a_duplicate_scenario_id_for_one_system_is_rejected(
    api_client, member, seed_system
):
    """The model's conditional unique constraint (system, scenario_id) for
    generation_run=NULL rows isn't derivable by DRF automatically, since
    generation_run isn't a serializer field — this exercises the explicit
    UniqueTogetherValidator that stands in for it."""
    api_client.force_authenticate(user=member)
    payload = {"system_description": seed_system.id, "scenario_id": "sc_01", "actor": "A"}

    first = api_client.post("/api/scenarios/", payload, format="json")
    assert first.status_code == 201

    second = api_client.post("/api/scenarios/", payload, format="json")
    assert second.status_code == 400
    assert Scenario.objects.filter(system_description=seed_system, scenario_id="sc_01").count() == 1


@pytest.mark.django_db
def test_list_endpoints_are_paginated(api_client, member, seed_system):
    """The response contract is an envelope from the start, before a frontend
    is written against a bare array."""
    Scenario.objects.bulk_create(
        Scenario(system_description=seed_system, scenario_id=f"sc_{index:03}", order=index)
        for index in range(1, 52)
    )
    api_client.force_authenticate(user=member)

    first = api_client.get("/api/scenarios/").json()
    assert first["count"] == 51
    assert len(first["results"]) == 50
    assert first["next"] is not None

    second = api_client.get(first["next"]).json()
    assert [row["scenario_id"] for row in second["results"]] == ["sc_051"]
