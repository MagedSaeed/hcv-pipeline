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
def test_taxonomies_are_read_only(api_client, member, imported):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/taxonomies/", {"vocabulary_id": "X", "version": "1"}, format="json"
    )
    assert response.status_code == 405
