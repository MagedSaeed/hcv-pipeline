import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def imported(db):
    call_command("import_taxonomy", "--activate")


@pytest.mark.django_db
def test_listing_requires_authentication(api_client, imported):
    assert api_client.get("/api/taxonomies/").status_code == 403


@pytest.mark.django_db
def test_lists_taxonomies_with_row_counts(api_client, member, imported):
    api_client.force_authenticate(user=member)
    body = api_client.get("/api/taxonomies/").json()
    assert body["count"] == 1
    row = body["results"][0]
    assert row["vocabulary_id"] == "HCV-21"
    assert row["version"] == "1.1"
    assert row["is_active"] is True
    assert row["hcv_count"] == 21


@pytest.mark.django_db
def test_hcvs_endpoint_returns_all_rows_in_order(api_client, member, imported):
    api_client.force_authenticate(user=member)
    taxonomy_id = api_client.get("/api/taxonomies/").json()["results"][0]["id"]

    rows = api_client.get(f"/api/taxonomies/{taxonomy_id}/hcvs/").json()["results"]
    assert [row["hcv_id"] for row in rows][:3] == ["HCV-01", "HCV-02", "HCV-03"]
    assert rows[6]["condition_classes"] == ["UnconstrainedInsiderAccessCondition"]
    assert rows[8]["condition_classes"] == []


@pytest.mark.django_db
def test_taxonomies_cannot_be_created_over_the_api(api_client, staff, imported):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/taxonomies/", {"vocabulary_id": "X", "version": "1"}, format="json"
    )
    assert response.status_code == 405


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="staff@example.com", password="pw-123456", is_staff=True)


def _taxonomy():
    from taxonomy.models import Taxonomy

    return Taxonomy.objects.get(vocabulary_id="HCV-21")


@pytest.mark.django_db
def test_staff_can_add_edit_and_delete_hcv_rows(api_client, staff, imported):
    api_client.force_authenticate(user=staff)
    taxonomy = _taxonomy()
    created = api_client.post(
        f"/api/taxonomies/{taxonomy.id}/hcvs/",
        {"hcv_id": "HCV-22", "name": "New row", "definition": "d", "order": 22},
        format="json",
    )
    assert created.status_code == 201, created.json()
    row_id = created.json()["id"]

    patched = api_client.patch(f"/api/hcvs/{row_id}/", {"name": "Renamed"}, format="json")
    assert patched.status_code == 200
    assert taxonomy.hcvs.get(pk=row_id).name == "Renamed"

    assert api_client.delete(f"/api/hcvs/{row_id}/").status_code == 204
    assert taxonomy.hcvs.count() == 21


@pytest.mark.django_db
def test_duplicate_hcv_id_and_blank_name_are_refused(api_client, staff, imported):
    api_client.force_authenticate(user=staff)
    taxonomy = _taxonomy()
    url = f"/api/taxonomies/{taxonomy.id}/hcvs/"
    assert api_client.post(url, {"hcv_id": "HCV-01", "name": "x"}, format="json").status_code == 400
    assert api_client.post(url, {"hcv_id": "HCV-99", "name": " "}, format="json").status_code == 400


@pytest.mark.django_db
def test_members_cannot_edit_hcv_rows(api_client, member, imported):
    api_client.force_authenticate(user=member)
    row = _taxonomy().hcvs.first()
    assert api_client.patch(f"/api/hcvs/{row.id}/", {"name": "x"}, format="json").status_code == 403


@pytest.mark.django_db
def test_a_taxonomy_used_by_a_run_is_locked_and_can_be_copied(api_client, staff, imported):
    from pipeline.models import PhaseRun
    from prompts.models import PromptTemplate

    taxonomy = _taxonomy()
    template = PromptTemplate.objects.create_version(phase=3, name="label", user_prompt="L")
    PhaseRun.objects.create(phase=3, model="m", prompt_template=template, taxonomy=taxonomy)
    api_client.force_authenticate(user=staff)

    listed = api_client.get("/api/taxonomies/").json()["results"][0]
    assert listed["is_locked"] is True and listed["locked_reason"]
    row = taxonomy.hcvs.first()
    refused = api_client.patch(f"/api/hcvs/{row.id}/", {"name": "x"}, format="json")
    assert refused.status_code == 400
    assert api_client.delete(f"/api/hcvs/{row.id}/").status_code == 400

    copied = api_client.post(f"/api/taxonomies/{taxonomy.id}/new-version/", {"version": "1.2"})
    assert copied.status_code == 201
    body = copied.json()
    assert (body["version"], body["is_active"], body["is_locked"], body["hcv_count"]) == (
        "1.2",
        False,
        False,
        21,
    )
    again = api_client.post(f"/api/taxonomies/{taxonomy.id}/new-version/", {"version": "1.2"})
    assert again.status_code == 400

    activated = api_client.post(f"/api/taxonomies/{body['id']}/activate/")
    assert activated.json()["is_active"] is True
    taxonomy.refresh_from_db()
    assert taxonomy.is_active is False
