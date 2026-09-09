import pytest
from django.contrib.auth import get_user_model

from core.models import AppSettings

User = get_user_model()


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.mark.django_db
def test_non_staff_cannot_read_settings(api_client, member):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/settings/").status_code == 403


@pytest.mark.django_db
def test_anonymous_cannot_read_settings(api_client):
    assert api_client.get("/api/settings/").status_code == 403


@pytest.mark.django_db
def test_non_staff_cannot_patch_settings(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.patch(
        "/api/settings/", {"openrouter_key": "sk-or-v1-abcdefghijklmnop"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_anonymous_cannot_patch_settings(api_client):
    response = api_client.patch(
        "/api/settings/", {"openrouter_key": "sk-or-v1-abcdefghijklmnop"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_sets_key_and_reads_it_back_masked(api_client, staff, settings):
    settings.OPENROUTER_API_KEY = ""
    api_client.force_authenticate(user=staff)

    patched = api_client.patch(
        "/api/settings/",
        {"openrouter_key": "sk-or-v1-abcdefghijklmnop"},
        format="json",
    )
    assert patched.status_code == 200

    body = api_client.get("/api/settings/").json()
    assert body["openrouter_key_masked"] == "sk-o...mnop"
    assert body["key_source"] == "settings"
    assert "openrouter_key" not in body
    assert AppSettings.load().updated_by == staff


@pytest.mark.django_db
def test_key_source_reports_environment_when_unset(api_client, staff, settings):
    settings.OPENROUTER_API_KEY = "sk-or-from-environment"
    api_client.force_authenticate(user=staff)
    body = api_client.get("/api/settings/").json()
    assert body["key_source"] == "environment"
    assert body["openrouter_key_masked"] == ""


@pytest.mark.django_db
def test_key_source_reports_none_when_nothing_configured(api_client, staff, settings):
    settings.OPENROUTER_API_KEY = ""
    api_client.force_authenticate(user=staff)
    assert api_client.get("/api/settings/").json()["key_source"] == "none"
