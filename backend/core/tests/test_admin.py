import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from core.admin import AppSettingsAdmin
from core.models import AppSettings

User = get_user_model()


@pytest.fixture
def request_from_a_superuser():
    request = RequestFactory().get("/admin/core/appsettings/")
    request.user = User(email="admin@example.com", is_staff=True, is_superuser=True)
    return request


@pytest.fixture
def settings_admin():
    return AppSettingsAdmin(AppSettings, admin.site)


@pytest.mark.django_db
def test_app_settings_can_never_be_added_by_hand(settings_admin, request_from_a_superuser):
    """Every field on this admin is read-only, so the add form could only ever
    save an empty row. AppSettings.load() is the only legitimate creator."""
    assert settings_admin.has_add_permission(request_from_a_superuser) is False

    AppSettings.load()
    assert settings_admin.has_add_permission(request_from_a_superuser) is False


@pytest.mark.django_db
def test_app_settings_can_never_be_deleted(settings_admin, request_from_a_superuser):
    assert settings_admin.has_delete_permission(request_from_a_superuser) is False


@pytest.mark.django_db
def test_the_admin_shows_the_key_masked(settings_admin):
    config = AppSettings.load()
    config.openrouter_key = "sk-or-v1-abcdefghijklmnop"
    config.save()

    assert settings_admin.masked_key(config) == "sk-o...mnop"
