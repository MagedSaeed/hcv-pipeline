from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(email="researcher@example.com", password="pw-original-1")


@pytest.mark.django_db
def test_login_returns_user_and_opens_session(api_client, user):
    response = api_client.post(
        "/api/auth/login/",
        {"email": "researcher@example.com", "password": "pw-original-1"},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["email"] == "researcher@example.com"
    assert response.json()["must_change_password"] is True

    me = api_client.get("/api/auth/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "researcher@example.com"


@pytest.mark.django_db
def test_login_rejects_bad_password(api_client, user):
    response = api_client.post(
        "/api/auth/login/",
        {"email": "researcher@example.com", "password": "wrong"},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_me_requires_authentication(api_client):
    assert api_client.get("/api/auth/me/").status_code == 403


@pytest.mark.django_db
def test_logout_requires_authentication(api_client):
    assert api_client.post("/api/auth/logout/").status_code == 403


@pytest.mark.django_db
def test_change_password_requires_authentication(api_client):
    response = api_client.post(
        "/api/auth/change-password/",
        {"current_password": "x", "new_password": "y"},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_csrf_endpoint_is_public_and_sets_cookie(api_client):
    response = api_client.get("/api/auth/csrf/")
    assert response.status_code == 200
    assert "csrftoken" in response.cookies


@pytest.mark.django_db
def test_logout_closes_the_session(api_client, user):
    api_client.force_authenticate(user=user)
    assert api_client.post("/api/auth/logout/").status_code == 204
    api_client.force_authenticate(user=None)
    assert api_client.get("/api/auth/me/").status_code == 403


@pytest.mark.django_db
def test_change_password_clears_the_flag(api_client, user):
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/auth/change-password/",
        {"current_password": "pw-original-1", "new_password": "pw-replaced-2"},
        format="json",
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.must_change_password is False
    assert user.check_password("pw-replaced-2")


@pytest.mark.django_db
def test_change_password_rejects_wrong_current_password(api_client, user):
    api_client.force_authenticate(user=user)
    response = api_client.post(
        "/api/auth/change-password/",
        {"current_password": "nope", "new_password": "pw-replaced-2"},
        format="json",
    )
    assert response.status_code == 400
    user.refresh_from_db()
    assert user.must_change_password is True


@pytest.mark.django_db
def test_change_password_keeps_the_caller_logged_in(api_client, user):
    # Uses a real session (via the login endpoint), not force_authenticate,
    # so it actually exercises Django's session/password-hash tie-in.
    login_response = api_client.post(
        "/api/auth/login/",
        {"email": "researcher@example.com", "password": "pw-original-1"},
        format="json",
    )
    assert login_response.status_code == 200

    response = api_client.post(
        "/api/auth/change-password/",
        {"current_password": "pw-original-1", "new_password": "pw-replaced-2"},
        format="json",
    )
    assert response.status_code == 200

    me = api_client.get("/api/auth/me/")
    assert me.status_code == 200
    assert me.json()["email"] == "researcher@example.com"


@pytest.mark.django_db
def test_create_user_command_makes_an_account_that_must_change_password():
    call_command("create_user", "new@example.com", "--password", "pw-issued-123")

    created = User.objects.get(email="new@example.com")
    assert created.check_password("pw-issued-123")
    assert created.must_change_password is True
    assert created.is_staff is False


@pytest.mark.django_db
def test_create_user_command_can_grant_staff():
    call_command("create_user", "boss@example.com", "--password", "pw-issued-123", "--staff")
    assert User.objects.get(email="boss@example.com").is_staff is True


@pytest.mark.django_db
def test_create_user_command_refuses_a_duplicate_email(user):
    with pytest.raises(CommandError, match="already exists"):
        call_command("create_user", "researcher@example.com", "--password", "pw-issued-123")


@pytest.mark.django_db
def test_create_user_command_generates_and_prints_a_password_when_omitted():
    out = StringIO()
    call_command("create_user", "auto@example.com", stdout=out)

    created = User.objects.get(email="auto@example.com")
    assert created.has_usable_password()

    output = out.getvalue()
    assert "Temporary password:" in output
    printed_password = output.splitlines()[-1].removeprefix("Temporary password: ").strip()
    assert created.check_password(printed_password)


@pytest.mark.django_db
def test_change_password_rejects_reusing_the_current_password(api_client, user):
    """Retyping the same password would clear must_change_password and defeat
    the forced first-login change entirely."""
    api_client.force_authenticate(user=user)

    response = api_client.post(
        "/api/auth/change-password/",
        {"current_password": "pw-original-1", "new_password": "pw-original-1"},
        format="json",
    )

    assert response.status_code == 400
    assert "different" in str(response.json())
    user.refresh_from_db()
    assert user.must_change_password is True
    assert user.check_password("pw-original-1")


@pytest.mark.django_db
def test_login_rejects_an_inactive_user(api_client, db):
    User.objects.create_user(email="retired@example.com", password="pw-original-1", is_active=False)

    response = api_client.post(
        "/api/auth/login/",
        {"email": "retired@example.com", "password": "pw-original-1"},
        format="json",
    )

    assert response.status_code == 400
    assert api_client.get("/api/auth/me/").status_code == 403


@pytest.mark.django_db
def test_an_unsafe_request_on_a_real_session_needs_a_csrf_token(user):
    """The production path is SessionAuthentication plus CSRF, which every
    other test skips by using force_authenticate. The whole session-auth design
    rests on an unsafe request being refused without a token.
    """
    client = APIClient(enforce_csrf_checks=True)

    login = client.post(
        "/api/auth/login/",
        {"email": "researcher@example.com", "password": "pw-original-1"},
        format="json",
    )
    assert login.status_code == 200
    # Logging in rotates the CSRF token, so the frontend re-reads it afterwards.
    assert client.get("/api/auth/csrf/").status_code == 200
    token = client.cookies["csrftoken"].value

    without_token = client.post("/api/auth/logout/")
    assert without_token.status_code == 403
    assert "CSRF" in str(without_token.json())
    # Refused, not merely unauthenticated: the session is untouched.
    assert client.get("/api/auth/me/").status_code == 200

    with_token = client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=token)
    assert with_token.status_code == 204
    assert client.get("/api/auth/me/").status_code == 403
