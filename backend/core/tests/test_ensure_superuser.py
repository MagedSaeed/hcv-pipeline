from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

User = get_user_model()

VARIABLES = ("DJANGO_SUPERUSER_USERNAME", "DJANGO_SUPERUSER_EMAIL", "DJANGO_SUPERUSER_PASSWORD")


@pytest.fixture
def superuser_env(monkeypatch):
    def apply(**values):
        for name in VARIABLES:
            monkeypatch.delenv(name, raising=False)
        for key, value in values.items():
            monkeypatch.setenv(f"DJANGO_SUPERUSER_{key.upper()}", value)

    return apply


@pytest.mark.django_db
def test_creates_the_superuser_from_the_environment(superuser_env):
    superuser_env(username="Admin", email="Admin@Example.com", password="a-long-enough-password")

    call_command("ensure_superuser", stdout=StringIO())

    user = User.objects.get(email="Admin@example.com")
    assert user.is_superuser is True
    assert user.is_staff is True
    assert user.name == "Admin"
    assert user.check_password("a-long-enough-password")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "values",
    [
        {},
        {"email": "", "password": ""},
        {"email": "a@example.com"},
        {"username": "Admin", "password": "a-long-enough-password"},
    ],
)
def test_does_nothing_unless_email_and_password_are_both_set(superuser_env, values):
    superuser_env(**values)
    out = StringIO()

    call_command("ensure_superuser", stdout=out)

    assert not User.objects.exists()
    assert "skipped" in out.getvalue()


@pytest.mark.django_db
def test_leaves_an_existing_account_unchanged(superuser_env):
    User.objects.create_user(email="a@example.com", password="original-password-1")
    superuser_env(email="A@example.com", password="another-password-2")
    out = StringIO()

    call_command("ensure_superuser", stdout=out)

    user = User.objects.get()
    assert user.check_password("original-password-1")
    assert user.is_superuser is False
    assert "already exists" in out.getvalue()


@pytest.mark.django_db
def test_rejects_a_password_the_validators_refuse(superuser_env):
    superuser_env(email="a@example.com", password="password")

    with pytest.raises(CommandError, match="DJANGO_SUPERUSER_PASSWORD"):
        call_command("ensure_superuser", stdout=StringIO())

    assert not User.objects.exists()
