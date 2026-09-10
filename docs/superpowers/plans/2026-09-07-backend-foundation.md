# Backend Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Django backend with authentication, the app-level OpenRouter key, the 21-HCV taxonomy, and the corpus models — seeded from `resources/` and reachable over a REST API.

**Architecture:** A Django 5.2 LTS project (`backend/config`) with four apps: `core` (custom email User, singleton AppSettings, auth endpoints), `taxonomy` (immutable versioned Taxonomy + HCV, imported from JSON), and `corpus` (SystemDescription, Scenario, ResourceFile, FewShotExample, seeded from the Oldsmar Markdown pack). DRF session authentication, `IsAuthenticated` by default, `is_staff` gating configuration surfaces. No LLM calls and no pipeline in this plan — those are plans 2 and 3.

**Tech Stack:** Python 3.13, Django 5.2.17 LTS, DRF 3.18, pytest-django, SQLite (dev) / PostgreSQL 17 (prod), python-decouple. Dependencies and the Python toolchain are managed by **uv**; **ruff** is the formatter and linter.

**Spec:** `docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md`

## Global Constraints

- Python **3.13**; Django **5.2.17** (LTS); djangorestframework **3.18.0**.
- All backend code lives under `backend/`. Django settings module is `config.settings`.
- DRF defaults: `SessionAuthentication` only, `IsAuthenticated` only. Every new endpoint is authenticated unless explicitly listed otherwise in the spec §9.
- `is_staff` gates: the OpenRouter key, taxonomy import, prompt editing, experiment creation, run launching.
- `User` uses **email as `USERNAME_FIELD`**; there is no `username` field.
- A `SystemDescription` or `Scenario` with `origin="generated"` is **immutable**: `PATCH` and `DELETE` return 403.
- The OpenRouter key is **never** returned unmasked and never stored in any payload column.
- Dependencies are managed by **uv** from `backend/pyproject.toml`. Never call
  `pip` and never activate a virtualenv by hand: every command runs as
  `uv run …` from `backend/`. `uv.lock` is committed.
- uv provisions Python 3.13 itself, so the system Python version is irrelevant.
- **ruff** is both formatter and linter. Before every commit run
  `uv run ruff format . && uv run ruff check --fix .`; the commit steps below
  include it. Migrations are excluded from linting.
- When a step says *append to* a module, put any new imports at the **top** of
  that file beside the existing ones, merging them into an existing `from X
  import …` line for the same module rather than adding a second one. Ruff's
  E402 flags imports after code and F811 flags redefinitions; `--fix` will not
  restructure them for you.
- Tests are pytest-django, written before implementation (TDD). Run from `backend/`.
- Commit after every task.

---

## File Structure

```
backend/
  manage.py
  pyproject.toml                    deps, pytest and ruff config (uv project)
  uv.lock                           committed
  conftest.py                       shared fixtures (users, api client)
  config/
    __init__.py  settings.py  urls.py  wsgi.py  asgi.py
  core/
    models.py                       User, AppSettings
    managers.py                     UserManager
    keys.py                         resolve_openrouter_key, mask_key
    serializers.py  views.py  urls.py  admin.py  apps.py
    management/commands/create_user.py
    tests/test_user.py  test_auth_api.py  test_keys.py  test_settings_api.py
  taxonomy/
    models.py  serializers.py  views.py  urls.py  admin.py  apps.py
    management/commands/import_taxonomy.py
    tests/test_models.py  test_import.py  test_api.py
  corpus/
    models.py                       ResourceFile, SystemDescription, Scenario, FewShotExample
    parsers.py                      Markdown seed-pack parsing
    serializers.py  views.py  urls.py  permissions.py  admin.py  apps.py
    management/commands/seed_corpus.py
    tests/test_resource_file.py  test_corpus_models.py  test_fewshot.py
    tests/test_parsers.py  test_corpus_api.py  test_seed_corpus.py
docker-compose.yml                  redis + postgres for local dev
.env.example
```

Responsibilities: `core/keys.py` is the single place the OpenRouter key is
resolved or masked, so plan 2's client has one import. `corpus/parsers.py` holds
all Markdown parsing so `seed_corpus.py` stays a thin command. `corpus/permissions.py`
holds the generated-row rule so it is enforced in one place rather than per view.

---

### Task 1: Project scaffolding and test harness

**Files:**
- Create: `backend/manage.py`, `backend/config/__init__.py`, `backend/config/settings.py`, `backend/config/urls.py`, `backend/config/wsgi.py`, `backend/config/asgi.py`
- Create: `backend/pyproject.toml`, `backend/conftest.py`, `.env.example`, `docker-compose.yml`
- Test: `backend/config/tests/test_health.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a uv project at `backend/`; a Django project importable as `config.settings`; `GET /api/health/` returning `{"status": "ok"}`; `uv run pytest` works from `backend/`.

- [x] **Step 1: Create the uv project and install dependencies**

Run:

```bash
mkdir -p backend
cd backend
uv init --bare --python 3.13
```

Replace the generated `backend/pyproject.toml` with:

```toml
[project]
name = "hcv-pipeline-backend"
version = "0.1.0"
description = "Backend for the human-centric vulnerability pipeline"
requires-python = ">=3.13"
dependencies = [
    "django==5.2.17",
    "djangorestframework==3.18.0",
    "django-cors-headers==4.9.0",
    "django-q2==1.11.1",
    "redis==8.1.0",
    "django-redis==7.0.0",
    "openai==3.8.0",
    "jinja2==3.1.6",
    "pydantic==2.13.5",
    "psycopg[binary]==3.3.5",
    "gunicorn==26.2.0",
    "whitenoise==6.12.0",
    "python-decouple==3.8",
    "dj-database-url==3.1.2",
]

[dependency-groups]
dev = [
    "pytest==9.1.1",
    "pytest-django==4.14.0",
    "ruff==0.16.6",
]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings"
python_files = ["test_*.py"]
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py313"
# Migrations are generated; linting them fights the generator.
extend-exclude = ["*/migrations/*"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "C4", "SIM", "DJ"]

[tool.ruff.format]
quote-style = "double"
```

Then install and verify:

```bash
cd backend
uv sync
uv run python -c "import django; print(django.get_version())"
```

Expected: uv downloads Python 3.13 if needed, writes `uv.lock`, and prints
`5.2.17`. This exact pin set was resolved and installed on 2026-09-07 against
uv 0.8.12, so a resolution failure means a pin has been edited.

- [x] **Step 2: Write the failing test**

Create `backend/config/tests/__init__.py` (empty) and `backend/config/tests/test_health.py`:

```python
import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_health_is_public_and_ok(client):
    response = client.get(reverse("health"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [x] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest config/tests/test_health.py -v`
Expected: FAIL — pytest cannot find `DJANGO_SETTINGS_MODULE` / no `config` module.

- [x] **Step 4: Write the project files**

`backend/manage.py`:

```python
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

`backend/config/__init__.py`: empty.

`backend/config/settings.py`:

```python
from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config("SECRET_KEY", default="dev-insecure-key-change-me")
DEBUG = config("DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

DATABASES = {
    "default": dj_database_url.parse(
        config("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}

# Vite dev server.
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS", default="http://localhost:5173", cast=Csv()
)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS", default="http://localhost:5173", cast=Csv()
)

# Research inputs live outside backend/, at the repository root.
RESOURCES_DIR = BASE_DIR.parent / "resources"

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default="")
```

`backend/config/urls.py`:

```python
from django.contrib import admin
from django.http import JsonResponse
from django.urls import path


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health/", health, name="health"),
]
```

`backend/config/wsgi.py`:

```python
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
```

`backend/config/asgi.py`:

```python
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_asgi_application()
```

`backend/conftest.py`:

```python
import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client():
    return APIClient()
```

- [x] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest config/tests/test_health.py -v`
Expected: PASS.

- [x] **Step 6: Add developer scaffolding**

`.env.example` at the repository root:

```
SECRET_KEY=dev-insecure-key-change-me
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=sqlite:///db.sqlite3
REDIS_URL=redis://localhost:6379/0
CORS_ALLOWED_ORIGINS=http://localhost:5173
CSRF_TRUSTED_ORIGINS=http://localhost:5173
OPENROUTER_API_KEY=
```

`docker-compose.yml` at the repository root:

```yaml
services:
  redis:
    image: redis:8-alpine
    ports:
      - "6379:6379"
  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_USER: hcv
      POSTGRES_PASSWORD: hcv
      POSTGRES_DB: hcv
    ports:
      - "5432:5432"
```

Add to the repository `.gitignore`:

```
backend/.venv/
backend/db.sqlite3
backend/media/
backend/staticfiles/
.ruff_cache/
```

`uv.lock` is **not** ignored — it is committed so every machine and CI resolve
the same versions.

- [x] **Step 7: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend .env.example docker-compose.yml .gitignore
git commit -m "feat: scaffold Django project with health endpoint and pytest"
```

---

### Task 2: Custom User model

**Files:**
- Create: `backend/core/__init__.py`, `backend/core/apps.py`, `backend/core/managers.py`, `backend/core/models.py`, `backend/core/admin.py`
- Create: `backend/core/tests/__init__.py`, `backend/core/tests/test_user.py`
- Modify: `backend/config/settings.py` (add `core` to `INSTALLED_APPS`, set `AUTH_USER_MODEL`)

**Interfaces:**
- Consumes: `config.settings` from Task 1.
- Produces: `core.models.User` with fields `email`, `name`, `is_active`, `is_staff`, `must_change_password`; manager methods `User.objects.create_user(email, password, **extra)` and `User.objects.create_superuser(email, password, **extra)`.

- [x] **Step 1: Write the failing test**

Create `backend/core/tests/test_user.py`:

```python
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
def test_create_user_uses_email_as_identifier():
    user = User.objects.create_user(email="A@Example.COM", password="pw-123456")
    assert user.email == "A@example.com"
    assert user.check_password("pw-123456")
    assert user.is_staff is False
    assert User.USERNAME_FIELD == "email"


@pytest.mark.django_db
def test_admin_created_user_must_change_password():
    user = User.objects.create_user(email="r@example.com", password="pw-123456")
    assert user.must_change_password is True


@pytest.mark.django_db
def test_superuser_is_staff_and_keeps_its_password():
    user = User.objects.create_superuser(email="a@example.com", password="pw-123456")
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.must_change_password is False


@pytest.mark.django_db
def test_email_is_required():
    with pytest.raises(ValueError, match="Email is required"):
        User.objects.create_user(email="", password="pw-123456")
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest core/tests/test_user.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core'`.

- [x] **Step 3: Write the implementation**

`backend/core/__init__.py`: empty.

`backend/core/apps.py`:

```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
```

`backend/core/managers.py`:

```python
from django.contrib.auth.models import BaseUserManager


class UserManager(BaseUserManager):
    """Creates users keyed by email. There is no username field."""

    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email:
            raise ValueError("Email is required")
        extra.setdefault("must_change_password", True)
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        # A superuser chooses their own password at creation, so nothing to force.
        extra.setdefault("must_change_password", False)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")
        return self.create_user(email, password, **extra)
```

`backend/core/models.py`:

```python
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models

from core.managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # Admins hand out passwords, so a new account changes it on first login.
    must_change_password = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email
```

`backend/core/admin.py`:

```python
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.models import User


@admin.register(User)
class AppUserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "name", "is_staff", "must_change_password")
    search_fields = ("email", "name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name",)}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "must_change_password")},
        ),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),
    )
```

In `backend/config/settings.py`, add `"core"` to the end of `INSTALLED_APPS` and add below it:

```python
AUTH_USER_MODEL = "core.User"
```

- [x] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations core
uv run pytest core/tests/test_user.py -v
```

Expected: migration `core/migrations/0001_initial.py` created; all four tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/core backend/config/settings.py
git commit -m "feat: add email-keyed custom User model"
```

---

### Task 3: Authentication API

**Files:**
- Create: `backend/core/serializers.py`, `backend/core/views.py`, `backend/core/urls.py`
- Create: `backend/core/tests/test_auth_api.py`
- Modify: `backend/config/urls.py` (include `core.urls`)

**Interfaces:**
- Consumes: `core.models.User` from Task 2.
- Produces: `POST /api/auth/login/`, `POST /api/auth/logout/`, `GET /api/auth/me/`, `POST /api/auth/change-password/`, `GET /api/auth/csrf/`. `UserSerializer` exposing `id, email, name, is_staff, must_change_password`.

- [x] **Step 1: Write the failing test**

Create `backend/core/tests/test_auth_api.py`:

```python
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="researcher@example.com", password="pw-original-1"
    )


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
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest core/tests/test_auth_api.py -v`
Expected: FAIL — 404 on every route.

- [x] **Step 3: Write the implementation**

`backend/core/serializers.py`:

```python
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "email", "name", "is_staff", "must_change_password"]
        read_only_fields = fields


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            self.context["request"],
            username=attrs["email"],
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("Current password is incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value, self.context["request"].user)
        return value
```

`backend/core/views.py`:

```python
from django.contrib.auth import login, logout, update_session_auth_hash
from django.views.decorators.csrf import ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.serializers import ChangePasswordSerializer, LoginSerializer, UserSerializer


@method_decorator(ensure_csrf_cookie, name="get")
class CSRFView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"detail": "CSRF cookie set."})


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        login(request, user)
        return Response(UserSerializer(user).data)


class LogoutView(APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = request.user
        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.save(update_fields=["password", "must_change_password"])
        # Django ties the session to the password hash; without this the user is
        # silently logged out by the very change they just made.
        update_session_auth_hash(request, user)
        return Response(UserSerializer(user).data)
```

`backend/core/urls.py`:

```python
from django.urls import path

from core import views

urlpatterns = [
    path("auth/csrf/", views.CSRFView.as_view(), name="csrf"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("auth/me/", views.MeView.as_view(), name="me"),
    path(
        "auth/change-password/",
        views.ChangePasswordView.as_view(),
        name="change-password",
    ),
]
```

In `backend/config/urls.py`, add `include` to the imports and this entry to `urlpatterns`:

```python
    path("api/", include("core.urls")),
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest core/tests/ -v`
Expected: all tests PASS.

- [x] **Step 5: Write the failing test for the account-creation command**

Append to `backend/core/tests/test_auth_api.py`:

```python
from django.core.management import call_command
from django.core.management.base import CommandError


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
        call_command(
            "create_user", "researcher@example.com", "--password", "pw-issued-123"
        )
```

- [x] **Step 6: Run it to verify it fails**

Run: `cd backend && uv run pytest core/tests/test_auth_api.py -k create_user_command -v`
Expected: FAIL — `CommandError: Unknown command: 'create_user'`.

- [x] **Step 7: Write the command**

Create `backend/core/management/__init__.py` and
`backend/core/management/commands/__init__.py`, both empty.

`backend/core/management/commands/create_user.py`:

```python
import secrets

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Create an account. The user is forced to change the password on first login."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="")
        parser.add_argument(
            "--password",
            default=None,
            help="Password to issue. A random one is generated and printed if omitted.",
        )
        parser.add_argument("--staff", action="store_true", help="Grant is_staff.")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise CommandError(f"An account for {email} already exists.")

        password = options["password"] or secrets.token_urlsafe(12)
        User.objects.create_user(
            email=email,
            password=password,
            name=options["name"],
            is_staff=options["staff"],
        )
        self.stdout.write(self.style.SUCCESS(f"Created {email}"))
        if not options["password"]:
            self.stdout.write(f"Temporary password: {password}")

```

- [x] **Step 8: Run the tests to verify they pass**

Run: `cd backend && uv run pytest core/tests/ -v`
Expected: all tests PASS.

- [x] **Step 9: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/core backend/config/urls.py
git commit -m "feat: add session auth endpoints and create_user command"
```

---

### Task 4: AppSettings singleton and OpenRouter key resolution

**Files:**
- Modify: `backend/core/models.py` (add `AppSettings`)
- Create: `backend/core/keys.py`
- Create: `backend/core/tests/test_keys.py`

**Interfaces:**
- Consumes: `core.models` from Task 2.
- Produces: `core.models.AppSettings.load() -> AppSettings`; `core.keys.resolve_openrouter_key() -> str` (raises `MissingAPIKey`); `core.keys.key_source() -> "settings" | "environment" | "none"`; `core.keys.mask_key(value) -> str`; exception `core.keys.MissingAPIKey`.

- [x] **Step 1: Write the failing test**

Create `backend/core/tests/test_keys.py`:

```python
import pytest

from core.keys import MissingAPIKey, key_source, mask_key, resolve_openrouter_key
from core.models import AppSettings


@pytest.mark.django_db
def test_settings_key_wins_over_environment(settings):
    settings.OPENROUTER_API_KEY = "sk-or-from-environment"
    config = AppSettings.load()
    config.openrouter_key = "sk-or-from-database"
    config.save()

    assert resolve_openrouter_key() == "sk-or-from-database"
    assert key_source() == "settings"


@pytest.mark.django_db
def test_falls_back_to_environment_when_settings_blank(settings):
    settings.OPENROUTER_API_KEY = "sk-or-from-environment"
    assert resolve_openrouter_key() == "sk-or-from-environment"
    assert key_source() == "environment"


@pytest.mark.django_db
def test_clearing_the_stored_key_falls_back(settings):
    settings.OPENROUTER_API_KEY = "sk-or-from-environment"
    config = AppSettings.load()
    config.openrouter_key = "sk-or-from-database"
    config.save()
    config.openrouter_key = ""
    config.save()

    assert resolve_openrouter_key() == "sk-or-from-environment"


@pytest.mark.django_db
def test_missing_key_raises_with_actionable_message(settings):
    settings.OPENROUTER_API_KEY = ""
    assert key_source() == "none"
    with pytest.raises(MissingAPIKey, match="No OpenRouter API key"):
        resolve_openrouter_key()


@pytest.mark.django_db
def test_app_settings_is_a_singleton():
    first = AppSettings.load()
    second = AppSettings.load()
    assert first.pk == second.pk == 1
    assert AppSettings.objects.count() == 1


def test_mask_key_shows_only_the_ends():
    assert mask_key("sk-or-v1-abcdefghijklmnop") == "sk-o...mnop"


def test_mask_key_of_blank_is_blank():
    assert mask_key("") == ""


def test_mask_key_never_reveals_a_short_key():
    # At most MASK_VISIBLE*2 characters: showing both ends would show all of it.
    assert mask_key("sk-or123") == "********"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest core/tests/test_keys.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.keys'`.

- [x] **Step 3: Write the implementation**

Append to `backend/core/models.py`:

```python
class AppSettings(models.Model):
    """Application-wide configuration. Exactly one row, pk=1.

    The OpenRouter key lives here rather than on the user because every run
    belongs to the same research project and bills to the same account.
    """

    openrouter_key = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "app settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("AppSettings is a singleton and cannot be deleted.")

    @classmethod
    def load(cls):
        instance, _ = cls.objects.get_or_create(pk=1)
        return instance

    def __str__(self):
        return "Application settings"
```

`backend/core/keys.py`:

```python
"""Single source of truth for resolving and masking the OpenRouter API key.

Resolution order, highest priority first:

1. ``AppSettings.openrouter_key`` — set by an admin in the running app, so a
   key can be rotated without a redeploy.
2. ``settings.OPENROUTER_API_KEY`` — the environment, which is the bootstrap
   path for a fresh deployment and for CI.
"""

from django.conf import settings

from core.models import AppSettings

MASK_VISIBLE = 4


class MissingAPIKey(RuntimeError):
    """Raised when no key is configured from either source."""


def _stored_key():
    return (AppSettings.load().openrouter_key or "").strip()


def _environment_key():
    return (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()


def key_source():
    """Return which source a call would use: settings, environment, or none."""
    if _stored_key():
        return "settings"
    if _environment_key():
        return "environment"
    return "none"


def resolve_openrouter_key():
    key = _stored_key() or _environment_key()
    if not key:
        raise MissingAPIKey(
            "No OpenRouter API key is configured. Set one on the Settings "
            "screen, or provide OPENROUTER_API_KEY in the environment."
        )
    return key


def mask_key(value):
    """Show only the first and last few characters, never the middle."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= MASK_VISIBLE * 2:
        return "*" * 8
    return f"{value[:MASK_VISIBLE]}...{value[-MASK_VISIBLE:]}"
```

- [x] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations core
uv run pytest core/tests/test_keys.py -v
```

Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/core
git commit -m "feat: add AppSettings singleton and OpenRouter key resolution"
```

---

### Task 5: Settings API, staff-gated and masked

**Files:**
- Modify: `backend/core/serializers.py`, `backend/core/views.py`, `backend/core/urls.py`, `backend/core/admin.py`
- Create: `backend/core/tests/test_settings_api.py`

**Interfaces:**
- Consumes: `AppSettings`, `mask_key`, `key_source` from Task 4.
- Produces: `GET /api/settings/` returning `{"openrouter_key_masked": str, "key_source": str, "updated_at": str|null}`; `PATCH /api/settings/` accepting `{"openrouter_key": str}`. Staff only.

- [x] **Step 1: Write the failing test**

Create `backend/core/tests/test_settings_api.py`:

```python
import pytest
from django.contrib.auth import get_user_model

from core.models import AppSettings

User = get_user_model()


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        email="admin@example.com", password="pw-123456", is_staff=True
    )


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
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest core/tests/test_settings_api.py -v`
Expected: FAIL — 404 on `/api/settings/`.

- [x] **Step 3: Write the implementation**

Append to `backend/core/serializers.py`:

```python
from core.keys import key_source, mask_key
from core.models import AppSettings


class AppSettingsSerializer(serializers.ModelSerializer):
    openrouter_key = serializers.CharField(
        write_only=True, allow_blank=True, required=False, trim_whitespace=True
    )
    openrouter_key_masked = serializers.SerializerMethodField()
    key_source = serializers.SerializerMethodField()

    class Meta:
        model = AppSettings
        fields = ["openrouter_key", "openrouter_key_masked", "key_source", "updated_at"]
        read_only_fields = ["updated_at"]

    def get_openrouter_key_masked(self, obj):
        return mask_key(obj.openrouter_key)

    def get_key_source(self, _obj):
        return key_source()
```

Append to `backend/core/views.py`:

```python
from rest_framework.permissions import IsAdminUser

from core.models import AppSettings
from core.serializers import AppSettingsSerializer


class AppSettingsView(APIView):
    """Read and update application settings. Staff only.

    IsAdminUser checks is_staff, which is the gate the spec puts on the key.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        return Response(AppSettingsSerializer(AppSettings.load()).data)

    def patch(self, request):
        instance = AppSettings.load()
        serializer = AppSettingsSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=request.user)
        return Response(AppSettingsSerializer(instance).data)
```

Add to `backend/core/urls.py`'s `urlpatterns`:

```python
    path("settings/", views.AppSettingsView.as_view(), name="app-settings"),
```

Append to `backend/core/admin.py` (merging `AppSettings` into the existing
`from core.models import User` line and adding `from core.keys import mask_key`
at the top):

```python
from core.keys import mask_key
from core.models import AppSettings


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    """Audit surface only. The key is set through the API, never shown here.

    A staff account granted view-only access for auditing must be able to see
    who changed the key and when, without being handed the secret itself.
    """

    list_display = ("__str__", "updated_by", "updated_at")
    fields = ("masked_key", "updated_by", "updated_at")
    readonly_fields = ("masked_key", "updated_by", "updated_at")

    @admin.display(description="OpenRouter key")
    def masked_key(self, obj):
        return mask_key(obj.openrouter_key)

    def has_add_permission(self, request):
        # Singleton: the row is created on first load().
        return not AppSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest core/tests/ -v`
Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/core
git commit -m "feat: add staff-gated settings API with masked OpenRouter key"
```

---

### Task 6: Taxonomy models

**Files:**
- Create: `backend/taxonomy/__init__.py`, `backend/taxonomy/apps.py`, `backend/taxonomy/models.py`, `backend/taxonomy/admin.py`
- Create: `backend/taxonomy/tests/__init__.py`, `backend/taxonomy/tests/test_models.py`
- Modify: `backend/config/settings.py` (add `taxonomy` to `INSTALLED_APPS`)

**Interfaces:**
- Consumes: nothing beyond Django.
- Produces: `taxonomy.models.Taxonomy(vocabulary_id, version, notes, is_active, source_json, created_at)` and `taxonomy.models.HCV(taxonomy, hcv_id, name, definition, condition_classes, order)`; `HCV.has_condition_class` property; `Taxonomy.active()` classmethod.

- [x] **Step 1: Write the failing test**

Create `backend/taxonomy/tests/test_models.py`:

```python
import pytest
from django.db import IntegrityError

from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def taxonomy(db):
    return Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1")


@pytest.mark.django_db
def test_hcv_id_is_unique_within_a_taxonomy(taxonomy):
    HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-01", name="A", order=1)
    with pytest.raises(IntegrityError):
        HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-01", name="B", order=2)


@pytest.mark.django_db
def test_the_same_hcv_id_may_exist_in_another_version(taxonomy):
    other = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.2")
    HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-01", name="A", order=1)
    HCV.objects.create(taxonomy=other, hcv_id="HCV-01", name="A", order=1)
    assert HCV.objects.filter(hcv_id="HCV-01").count() == 2


@pytest.mark.django_db
def test_condition_classes_defaults_to_an_empty_list(taxonomy):
    row = HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-09", name="Propaganda", order=9)
    assert row.condition_classes == []
    assert row.has_condition_class is False


@pytest.mark.django_db
def test_has_condition_class_is_true_when_populated(taxonomy):
    row = HCV.objects.create(
        taxonomy=taxonomy,
        hcv_id="HCV-07",
        name="Malicious Insiders",
        condition_classes=["UnconstrainedInsiderAccessCondition"],
        order=7,
    )
    assert row.has_condition_class is True


@pytest.mark.django_db
def test_hcvs_come_back_in_taxonomy_order(taxonomy):
    for index in (3, 1, 2):
        HCV.objects.create(
            taxonomy=taxonomy, hcv_id=f"HCV-0{index}", name=str(index), order=index
        )
    assert [row.order for row in taxonomy.hcvs.all()] == [1, 2, 3]


@pytest.mark.django_db
def test_active_returns_the_flagged_taxonomy(taxonomy):
    Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.2", is_active=True)
    assert Taxonomy.active().version == "1.2"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest taxonomy/tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'taxonomy'`.

- [x] **Step 3: Write the implementation**

`backend/taxonomy/__init__.py`: empty.

`backend/taxonomy/apps.py`:

```python
from django.apps import AppConfig


class TaxonomyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "taxonomy"
```

`backend/taxonomy/models.py`:

```python
from django.db import models


class Taxonomy(models.Model):
    """A versioned closed label list. Immutable once a run references it."""

    vocabulary_id = models.CharField(max_length=50)
    version = models.CharField(max_length=20)
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)
    source_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "taxonomies"
        constraints = [
            models.UniqueConstraint(
                fields=["vocabulary_id", "version"], name="unique_taxonomy_version"
            )
        ]
        ordering = ["vocabulary_id", "version"]

    @classmethod
    def active(cls):
        return cls.objects.filter(is_active=True).order_by("-created_at").first()

    def __str__(self):
        return f"{self.vocabulary_id} v{self.version}"


class HCV(models.Model):
    """One human-centric vulnerability row of a taxonomy."""

    taxonomy = models.ForeignKey(Taxonomy, related_name="hcvs", on_delete=models.CASCADE)
    hcv_id = models.CharField(max_length=20)
    name = models.CharField(max_length=300)
    definition = models.TextField(blank=True, default="")
    # Empty by design for rows the ontology carries no condition class for.
    condition_classes = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "HCV"
        verbose_name_plural = "HCVs"
        constraints = [
            models.UniqueConstraint(
                fields=["taxonomy", "hcv_id"], name="unique_hcv_per_taxonomy"
            )
        ]
        ordering = ["order", "hcv_id"]

    @property
    def has_condition_class(self):
        return bool(self.condition_classes)

    def __str__(self):
        return f"{self.hcv_id} {self.name}"
```

`backend/taxonomy/admin.py`:

```python
from django.contrib import admin

from taxonomy.models import HCV, Taxonomy


class HCVInline(admin.TabularInline):
    model = HCV
    extra = 0


@admin.register(Taxonomy)
class TaxonomyAdmin(admin.ModelAdmin):
    list_display = ("vocabulary_id", "version", "is_active", "created_at")
    inlines = [HCVInline]


@admin.register(HCV)
class HCVAdmin(admin.ModelAdmin):
    list_display = ("hcv_id", "name", "taxonomy", "has_condition_class")
    list_filter = ("taxonomy",)
    search_fields = ("hcv_id", "name", "definition")
```

Add `"taxonomy"` to `INSTALLED_APPS` in `backend/config/settings.py`.

- [x] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations taxonomy
uv run pytest taxonomy/tests/test_models.py -v
```

Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/taxonomy backend/config/settings.py
git commit -m "feat: add versioned Taxonomy and HCV models"
```

---

### Task 7: Taxonomy import command

**Files:**
- Create: `backend/taxonomy/management/__init__.py`, `backend/taxonomy/management/commands/__init__.py`, `backend/taxonomy/management/commands/import_taxonomy.py`
- Create: `backend/taxonomy/tests/test_import.py`

**Interfaces:**
- Consumes: `Taxonomy`, `HCV` from Task 6; `settings.RESOURCES_DIR` from Task 1.
- Produces: `python manage.py import_taxonomy [path] [--activate]`, importing the JSON at `resources/taxonomy/hcv21_v1.1.json` by default. Re-importing the same `vocabulary_id`+`version` replaces that version's rows rather than duplicating them.

- [x] **Step 1: Write the failing test**

Create `backend/taxonomy/tests/test_import.py`:

```python
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from taxonomy.models import HCV, Taxonomy

# Rows the ontology deliberately carries no condition class for.
ROWS_WITHOUT_CONDITION_CLASSES = {"HCV-09", "HCV-14", "HCV-19", "HCV-20"}


@pytest.mark.django_db
def test_imports_the_real_seed_file():
    call_command("import_taxonomy", "--activate")

    taxonomy = Taxonomy.objects.get(vocabulary_id="HCV-21", version="1.1")
    assert taxonomy.is_active is True
    assert taxonomy.hcvs.count() == 21

    first = taxonomy.hcvs.get(hcv_id="HCV-01")
    assert first.name == "Limited User Involvement in AI-based Authentication"
    assert first.condition_classes == ["LimitedAuthenticationInvolvementCondition"]
    assert first.order == 1

    empty = {row.hcv_id for row in taxonomy.hcvs.all() if not row.condition_classes}
    assert empty == ROWS_WITHOUT_CONDITION_CLASSES


@pytest.mark.django_db
def test_source_json_is_kept_verbatim():
    call_command("import_taxonomy")
    taxonomy = Taxonomy.objects.get(version="1.1")
    assert len(taxonomy.source_json["rows"]) == 21


@pytest.mark.django_db
def test_reimport_replaces_rows_instead_of_duplicating():
    call_command("import_taxonomy")
    call_command("import_taxonomy")
    assert Taxonomy.objects.count() == 1
    assert HCV.objects.count() == 21


@pytest.mark.django_db
def test_activate_deactivates_other_versions(tmp_path):
    call_command("import_taxonomy", "--activate")
    other = tmp_path / "v2.json"
    other.write_text(
        json.dumps(
            {
                "vocabulary_id": "HCV-21",
                "version": "2.0",
                "rows": [{"hcv_id": "HCV-01", "name": "A", "definition": "d",
                          "condition_classes": []}],
            }
        )
    )
    call_command("import_taxonomy", str(other), "--activate")

    assert Taxonomy.objects.get(version="1.1").is_active is False
    assert Taxonomy.objects.get(version="2.0").is_active is True


@pytest.mark.django_db
def test_missing_file_reports_clearly(tmp_path):
    with pytest.raises(CommandError, match="not found"):
        call_command("import_taxonomy", str(tmp_path / "absent.json"))


@pytest.mark.django_db
def test_rejects_a_file_with_no_rows(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"vocabulary_id": "X", "version": "1", "rows": []}))
    with pytest.raises(CommandError, match="no rows"):
        call_command("import_taxonomy", str(bad))
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest taxonomy/tests/test_import.py -v`
Expected: FAIL — `CommandError: Unknown command: 'import_taxonomy'`.

- [x] **Step 3: Write the implementation**

Create `backend/taxonomy/management/__init__.py` and
`backend/taxonomy/management/commands/__init__.py`, both empty.

`backend/taxonomy/management/commands/import_taxonomy.py`:

```python
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from taxonomy.models import HCV, Taxonomy

DEFAULT_PATH = "taxonomy/hcv21_v1.1.json"


class Command(BaseCommand):
    help = "Import an HCV taxonomy JSON file into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "path",
            nargs="?",
            default=None,
            help=f"Path to the taxonomy JSON. Defaults to resources/{DEFAULT_PATH}.",
        )
        parser.add_argument(
            "--activate",
            action="store_true",
            help="Mark this version active and deactivate all others.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"]) if options["path"] else settings.RESOURCES_DIR / DEFAULT_PATH
        if not path.exists():
            raise CommandError(f"Taxonomy file not found: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CommandError(f"{path} is not valid JSON: {error}") from error

        rows = payload.get("rows") or []
        if not rows:
            raise CommandError(f"{path} contains no rows.")

        with transaction.atomic():
            taxonomy, _ = Taxonomy.objects.update_or_create(
                vocabulary_id=payload["vocabulary_id"],
                version=str(payload["version"]),
                defaults={
                    "notes": payload.get("What are these row", ""),
                    "source_json": payload,
                },
            )
            # Replace rather than merge, so a re-import mirrors the file exactly.
            taxonomy.hcvs.all().delete()
            HCV.objects.bulk_create(
                [
                    HCV(
                        taxonomy=taxonomy,
                        hcv_id=row["hcv_id"],
                        name=row["name"],
                        definition=row.get("definition", ""),
                        condition_classes=row.get("condition_classes", []),
                        order=index,
                    )
                    for index, row in enumerate(rows, start=1)
                ]
            )

            if options["activate"]:
                Taxonomy.objects.exclude(pk=taxonomy.pk).update(is_active=False)
                taxonomy.is_active = True
                taxonomy.save(update_fields=["is_active"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {len(rows)} rows into {taxonomy}"
                + (" (active)" if options["activate"] else "")
            )
        )
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest taxonomy/tests/ -v`
Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/taxonomy
git commit -m "feat: add import_taxonomy command seeding the 21 HCVs"
```

---

### Task 8: Taxonomy read API

**Files:**
- Create: `backend/taxonomy/serializers.py`, `backend/taxonomy/views.py`, `backend/taxonomy/urls.py`
- Create: `backend/taxonomy/tests/test_api.py`
- Modify: `backend/config/urls.py`

**Interfaces:**
- Consumes: `Taxonomy`, `HCV` from Task 6.
- Produces: `GET /api/taxonomies/`, `GET /api/taxonomies/{id}/`, `GET /api/taxonomies/{id}/hcvs/`. Read-only; authenticated.

- [x] **Step 1: Write the failing test**

Create `backend/taxonomy/tests/test_api.py`:

```python
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
    assert len(body) == 1
    assert body[0]["vocabulary_id"] == "HCV-21"
    assert body[0]["version"] == "1.1"
    assert body[0]["is_active"] is True
    assert body[0]["hcv_count"] == 21


@pytest.mark.django_db
def test_hcvs_endpoint_returns_all_rows_in_order(api_client, member, imported):
    api_client.force_authenticate(user=member)
    taxonomy_id = api_client.get("/api/taxonomies/").json()[0]["id"]

    rows = api_client.get(f"/api/taxonomies/{taxonomy_id}/hcvs/").json()
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
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest taxonomy/tests/test_api.py -v`
Expected: FAIL — 404 on `/api/taxonomies/`.

- [x] **Step 3: Write the implementation**

`backend/taxonomy/serializers.py`:

```python
from rest_framework import serializers

from taxonomy.models import HCV, Taxonomy


class HCVSerializer(serializers.ModelSerializer):
    class Meta:
        model = HCV
        fields = ["id", "hcv_id", "name", "definition", "condition_classes", "order"]


class TaxonomySerializer(serializers.ModelSerializer):
    hcv_count = serializers.IntegerField(source="hcvs.count", read_only=True)

    class Meta:
        model = Taxonomy
        fields = [
            "id",
            "vocabulary_id",
            "version",
            "notes",
            "is_active",
            "hcv_count",
            "created_at",
        ]
```

`backend/taxonomy/views.py`:

```python
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from taxonomy.models import Taxonomy
from taxonomy.serializers import HCVSerializer, TaxonomySerializer


class TaxonomyViewSet(viewsets.ReadOnlyModelViewSet):
    """Taxonomies are read-only over the API; they arrive by import command."""

    queryset = Taxonomy.objects.all()
    serializer_class = TaxonomySerializer

    @action(detail=True, methods=["get"])
    def hcvs(self, request, pk=None):
        rows = self.get_object().hcvs.all()
        return Response(HCVSerializer(rows, many=True).data)
```

`backend/taxonomy/urls.py`:

```python
from rest_framework.routers import DefaultRouter

from taxonomy.views import TaxonomyViewSet

router = DefaultRouter()
router.register("taxonomies", TaxonomyViewSet, basename="taxonomy")

urlpatterns = router.urls
```

Add to `backend/config/urls.py`'s `urlpatterns`:

```python
    path("api/", include("taxonomy.urls")),
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest taxonomy/tests/ -v`
Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/taxonomy backend/config/urls.py
git commit -m "feat: add read-only taxonomy API"
```

---

### Task 9: ResourceFile model and upload API

**Files:**
- Create: `backend/corpus/__init__.py`, `backend/corpus/apps.py`, `backend/corpus/models.py`, `backend/corpus/serializers.py`, `backend/corpus/views.py`, `backend/corpus/urls.py`, `backend/corpus/admin.py`
- Create: `backend/corpus/tests/__init__.py`, `backend/corpus/tests/test_resource_file.py`
- Modify: `backend/config/settings.py` (add `corpus`), `backend/config/urls.py`

**Interfaces:**
- Consumes: `core.models.User`.
- Produces: `corpus.models.ResourceFile(name, kind, file, text, description, uploaded_by, created_at)`; `CRUD /api/resources/`. Uploading a UTF-8 text file populates `text` automatically.

- [x] **Step 1: Write the failing test**

Create `backend/corpus/tests/test_resource_file.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from corpus.models import ResourceFile

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


def upload(name, body):
    return SimpleUploadedFile(name, body.encode("utf-8"), content_type="text/plain")


@pytest.mark.django_db
def test_upload_extracts_text(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/resources/",
        {
            "name": "oldsmar-scenarios",
            "kind": "scenarios",
            "file": upload("scenarios.md", "# sc_01\n\nbody text"),
        },
        format="multipart",
    )
    assert response.status_code == 201
    assert response.json()["text"] == "# sc_01\n\nbody text"
    assert ResourceFile.objects.get().uploaded_by == member


@pytest.mark.django_db
def test_text_may_be_supplied_without_a_file(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/resources/",
        {"name": "inline", "kind": "other", "text": "pasted content"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["text"] == "pasted content"


@pytest.mark.django_db
def test_binary_upload_is_rejected(api_client, member):
    api_client.force_authenticate(user=member)
    binary = SimpleUploadedFile(
        "image.png", b"\x89PNG\r\n\x1a\n\x00\xff", content_type="image/png"
    )
    response = api_client.post(
        "/api/resources/",
        {"name": "bad", "kind": "other", "file": binary},
        format="multipart",
    )
    assert response.status_code == 400
    assert "UTF-8 text" in str(response.json())


@pytest.mark.django_db
def test_name_is_unique(api_client, member):
    api_client.force_authenticate(user=member)
    payload = {"name": "dup", "kind": "other", "text": "a"}
    assert api_client.post("/api/resources/", payload, format="json").status_code == 201
    assert api_client.post("/api/resources/", payload, format="json").status_code == 400


@pytest.mark.django_db
def test_resources_require_authentication(api_client):
    assert api_client.get("/api/resources/").status_code == 403
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest corpus/tests/test_resource_file.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpus'`.

- [x] **Step 3: Write the implementation**

`backend/corpus/__init__.py`: empty.

`backend/corpus/apps.py`:

```python
from django.apps import AppConfig


class CorpusConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "corpus"
```

`backend/corpus/models.py`:

```python
from django.db import models


class ResourceKind(models.TextChoices):
    SYSTEM_DESCRIPTION = "system_description", "System description"
    SCENARIOS = "scenarios", "Scenarios"
    VOCABULARY = "vocabulary", "Vocabulary"
    TAXONOMY = "taxonomy", "Taxonomy"
    OTHER = "other", "Other"


class ResourceFile(models.Model):
    """An uploaded or pasted text resource, referencable from a prompt template."""

    name = models.CharField(max_length=200, unique=True)
    kind = models.CharField(
        max_length=32, choices=ResourceKind.choices, default=ResourceKind.OTHER
    )
    file = models.FileField(upload_to="resources/", null=True, blank=True)
    text = models.TextField(blank=True, default="")
    description = models.TextField(blank=True, default="")
    uploaded_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
```

`backend/corpus/serializers.py`:

```python
from rest_framework import serializers

from corpus.models import ResourceFile

# The largest real research resource is about 30 KB; 2 MB is generous headroom.
MAX_RESOURCE_BYTES = 2 * 1024 * 1024


class ResourceFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceFile
        fields = [
            "id",
            "name",
            "kind",
            "file",
            "text",
            "description",
            "uploaded_by",
            "created_at",
        ]
        read_only_fields = ["uploaded_by", "created_at"]

    def validate(self, attrs):
        """Read an uploaded file into `text`; the app never re-reads from disk."""
        upload = attrs.get("file")
        if upload is not None:
            # A resource is injected verbatim into LLM prompts, so an oversized
            # one is a context and cost problem long before it is a memory one.
            # Django's DATA_UPLOAD_MAX_MEMORY_SIZE does not bound file uploads.
            if upload.size > MAX_RESOURCE_BYTES:
                raise serializers.ValidationError(
                    {"file": f"File exceeds the {MAX_RESOURCE_BYTES // 1024} KB limit."}
                )
            upload.seek(0)
            try:
                attrs["text"] = upload.read().decode("utf-8")
            except UnicodeDecodeError as error:
                raise serializers.ValidationError(
                    {"file": "File must be UTF-8 text (.txt, .md, .json)."}
                ) from error
            upload.seek(0)
        return attrs
```

`backend/corpus/views.py`:

```python
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from corpus.models import ResourceFile
from corpus.serializers import ResourceFileSerializer


class ResourceFileViewSet(viewsets.ModelViewSet):
    queryset = ResourceFile.objects.all()
    serializer_class = ResourceFileSerializer

    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)
```

`backend/corpus/urls.py`:

```python
from rest_framework.routers import DefaultRouter

from corpus.views import ResourceFileViewSet

router = DefaultRouter()
router.register("resources", ResourceFileViewSet, basename="resource")

urlpatterns = router.urls
```

`backend/corpus/admin.py`:

```python
from django.contrib import admin

from corpus.models import ResourceFile


@admin.register(ResourceFile)
class ResourceFileAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "uploaded_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("name", "description")
```

Add `"corpus"` to `INSTALLED_APPS`, and add to `backend/config/urls.py`:

```python
    path("api/", include("corpus.urls")),
```

- [x] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations corpus
uv run pytest corpus/tests/test_resource_file.py -v
```

Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/corpus backend/config
git commit -m "feat: add ResourceFile upload with UTF-8 text extraction"
```

---

### Task 10: SystemDescription and Scenario models

**Files:**
- Modify: `backend/corpus/models.py`, `backend/corpus/admin.py`
- Create: `backend/corpus/tests/test_corpus_models.py`

**Interfaces:**
- Consumes: `taxonomy.models.HCV` from Task 6.
- Produces: `corpus.models.Origin` (`SEED`, `GENERATED`, `MANUAL`); `SystemDescription(title, slug, body, origin, generation_run, created_by, ...)` with `is_editable` property; `Scenario(system_description, scenario_id, actor, trigger, standing_state, asset, threat, outcome, narrative, intended_hcvs, abox, origin, generation_run, order, ...)` with `is_editable` and `card` property.

Note: `generation_run` points at `pipeline.PhaseRun`, which does not exist until
plan 3. Declare it now as a nullable `CharField`-free placeholder is **not**
acceptable — instead omit the field here and add it in plan 3's first task via a
migration. `origin` alone distinguishes the rows for this plan.

- [x] **Step 1: Write the failing test**

Create `backend/corpus/tests/test_corpus_models.py`:

```python
import pytest
from django.core.management import call_command

from corpus.models import Origin, Scenario, SystemDescription
from taxonomy.models import HCV


@pytest.fixture
def system(db):
    return SystemDescription.objects.create(
        title="Oldsmar Water Treatment", body="...", origin=Origin.SEED
    )


@pytest.mark.django_db
def test_slug_is_derived_from_the_title(system):
    assert system.slug == "oldsmar-water-treatment"


@pytest.mark.django_db
def test_slug_stays_unique(system):
    second = SystemDescription.objects.create(
        title="Oldsmar Water Treatment", body="...", origin=Origin.MANUAL
    )
    assert second.slug == "oldsmar-water-treatment-2"


@pytest.mark.django_db
def test_seed_and_manual_are_editable_but_generated_is_not(system):
    assert system.is_editable is True

    manual = SystemDescription.objects.create(
        title="Hand written", body="...", origin=Origin.MANUAL
    )
    assert manual.is_editable is True

    generated = SystemDescription.objects.create(
        title="From the model", body="...", origin=Origin.GENERATED
    )
    assert generated.is_editable is False


@pytest.mark.django_db
def test_scenario_card_collects_the_six_fields(system):
    scenario = Scenario.objects.create(
        system_description=system,
        scenario_id="sc_01",
        actor="EP1",
        trigger="After 22:00.",
        standing_state="PlantConsoleAuth protects the SCADA Service.",
        asset="The SCADA Service",
        threat="Spoofing, then Tampering.",
        outcome="The feed ran at 11,100 ppm for 8 hours.",
        narrative="1. EP1 opens the session.",
        origin=Origin.SEED,
        order=1,
    )
    assert scenario.card == {
        "actor": "EP1",
        "trigger": "After 22:00.",
        "standing_state": "PlantConsoleAuth protects the SCADA Service.",
        "asset": "The SCADA Service",
        "threat": "Spoofing, then Tampering.",
        "outcome": "The feed ran at 11,100 ppm for 8 hours.",
    }
    assert scenario.abox is None
    assert scenario.is_editable is True


@pytest.mark.django_db
def test_scenario_id_is_unique_within_a_system(system):
    Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    with pytest.raises(Exception):
        Scenario.objects.create(
            system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=2
        )


@pytest.mark.django_db
def test_deleting_a_system_removes_its_scenarios(system):
    Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    system.delete()
    assert Scenario.objects.count() == 0


@pytest.mark.django_db
def test_intended_hcvs_records_expert_intent(system):
    call_command("import_taxonomy", "--activate")
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    scenario.intended_hcvs.add(HCV.objects.get(hcv_id="HCV-07"))
    assert [row.hcv_id for row in scenario.intended_hcvs.all()] == ["HCV-07"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest corpus/tests/test_corpus_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'Origin' from 'corpus.models'`.

- [x] **Step 3: Write the implementation**

Append to `backend/corpus/models.py`:

```python
from django.utils.text import slugify


class Origin(models.TextChoices):
    SEED = "seed", "Expert seed"
    GENERATED = "generated", "LLM generated"
    MANUAL = "manual", "Hand authored"


class OriginMixin(models.Model):
    """Shared origin semantics.

    Generated rows are the record of what a model produced. Editing one would
    silently contaminate the measurement, so they are read-only everywhere.
    """

    origin = models.CharField(
        max_length=16, choices=Origin.choices, default=Origin.MANUAL, db_index=True
    )

    class Meta:
        abstract = True

    @property
    def is_editable(self):
        return self.origin != Origin.GENERATED


class SystemDescription(OriginMixin):
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=320, unique=True, blank=True)
    body = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug(slugify(self.title) or "system")
        super().save(*args, **kwargs)

    def _unique_slug(self, base):
        candidate, suffix = base, 1
        while SystemDescription.objects.filter(slug=candidate).exclude(pk=self.pk).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    def __str__(self):
        return self.title


class Scenario(OriginMixin):
    CARD_FIELDS = ("actor", "trigger", "standing_state", "asset", "threat", "outcome")

    system_description = models.ForeignKey(
        SystemDescription, related_name="scenarios", on_delete=models.CASCADE
    )
    scenario_id = models.CharField(max_length=50)

    actor = models.TextField(blank=True, default="")
    trigger = models.TextField(blank=True, default="")
    standing_state = models.TextField(blank=True, default="")
    asset = models.TextField(blank=True, default="")
    threat = models.TextField(blank=True, default="")
    outcome = models.TextField(blank=True, default="")

    narrative = models.TextField(blank=True, default="")
    # Phase 1 expert intent. Unused by phases 2 and 3; kept for later scoring.
    intended_hcvs = models.ManyToManyField("taxonomy.HCV", blank=True)
    # Reserved for a future ontology-encoding phase.
    abox = models.JSONField(null=True, blank=True)

    order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["system_description", "order", "scenario_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["system_description", "scenario_id"],
                name="unique_scenario_id_per_system",
            )
        ]

    @property
    def card(self):
        return {field: getattr(self, field) for field in self.CARD_FIELDS}

    def __str__(self):
        return f"{self.system_description.title} / {self.scenario_id}"
```

Append to `backend/corpus/admin.py`:

```python
from corpus.models import Scenario, SystemDescription


class ScenarioInline(admin.TabularInline):
    model = Scenario
    extra = 0


@admin.register(SystemDescription)
class SystemDescriptionAdmin(admin.ModelAdmin):
    list_display = ("title", "origin", "created_at")
    list_filter = ("origin",)
    inlines = [ScenarioInline]


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("scenario_id", "system_description", "origin", "threat")
    list_filter = ("origin",)
    search_fields = ("scenario_id", "actor", "narrative")
```

- [x] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations corpus
uv run pytest corpus/tests/ -v
```

Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/corpus
git commit -m "feat: add SystemDescription and Scenario with origin semantics"
```

---

### Task 11: FewShotExample with exactly-one-of validation

**Files:**
- Modify: `backend/corpus/models.py`, `backend/corpus/serializers.py`, `backend/corpus/views.py`, `backend/corpus/urls.py`, `backend/corpus/admin.py`
- Create: `backend/corpus/tests/test_fewshot.py`

**Interfaces:**
- Consumes: `ResourceFile` from Task 9.
- Produces: `corpus.models.FewShotExample(phase, name, input_text, input_file, output_text, output_file, order, is_active)` with `resolved_input` and `resolved_output` properties and a `clean()` that enforces exactly one source per side; `CRUD /api/fewshots/?phase=`.

- [x] **Step 1: Write the failing test**

Create `backend/corpus/tests/test_fewshot.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from corpus.models import FewShotExample, ResourceFile

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def resource(db):
    return ResourceFile.objects.create(name="scenarios-md", text="card content")


@pytest.mark.django_db
def test_inline_text_resolves(db):
    example = FewShotExample(
        phase=2, name="oldsmar", input_text="system", output_text="scenarios"
    )
    example.full_clean()
    example.save()
    assert example.resolved_input == "system"
    assert example.resolved_output == "scenarios"


@pytest.mark.django_db
def test_file_reference_resolves_at_read_time(resource):
    example = FewShotExample(
        phase=2, name="from-file", input_text="system", output_file=resource
    )
    example.full_clean()
    example.save()
    assert example.resolved_output == "card content"

    resource.text = "edited content"
    resource.save()
    assert FewShotExample.objects.get(pk=example.pk).resolved_output == "edited content"


@pytest.mark.django_db
def test_both_sources_on_one_side_is_rejected(resource):
    example = FewShotExample(
        phase=2,
        name="ambiguous",
        input_text="system",
        output_text="inline",
        output_file=resource,
    )
    with pytest.raises(ValidationError, match="exactly one"):
        example.full_clean()


@pytest.mark.django_db
def test_neither_source_on_one_side_is_rejected():
    example = FewShotExample(phase=2, name="empty", input_text="system")
    with pytest.raises(ValidationError, match="exactly one"):
        example.full_clean()


@pytest.mark.django_db
def test_api_filters_by_phase(api_client, member):
    FewShotExample.objects.create(
        phase=2, name="gen", input_text="a", output_text="b"
    )
    FewShotExample.objects.create(
        phase=3, name="label", input_text="c", output_text="d"
    )
    api_client.force_authenticate(user=member)

    rows = api_client.get("/api/fewshots/?phase=3").json()
    assert [row["name"] for row in rows] == ["label"]


@pytest.mark.django_db
def test_api_rejects_a_half_empty_pair(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/fewshots/", {"phase": 2, "name": "bad", "input_text": "a"}, format="json"
    )
    assert response.status_code == 400
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest corpus/tests/test_fewshot.py -v`
Expected: FAIL — `ImportError: cannot import name 'FewShotExample'`.

- [x] **Step 3: Write the implementation**

Append to `backend/corpus/models.py`:

```python
from django.core.exceptions import ValidationError


class FewShotExample(models.Model):
    """An input/output demonstration pair injected into a prompt.

    Phase 2 pairs are (system description -> scenarios); phase 3 pairs are
    (scenario -> labels). Each side is inline text or a ResourceFile, never
    both and never neither, so a rendered prompt is never half empty.
    """

    PHASE_CHOICES = [(2, "Phase 2 — generation"), (3, "Phase 3 — labelling")]

    phase = models.PositiveSmallIntegerField(choices=PHASE_CHOICES, db_index=True)
    name = models.CharField(max_length=200)
    input_text = models.TextField(blank=True, default="")
    input_file = models.ForeignKey(
        ResourceFile,
        null=True,
        blank=True,
        related_name="fewshot_inputs",
        on_delete=models.PROTECT,
    )
    output_text = models.TextField(blank=True, default="")
    output_file = models.ForeignKey(
        ResourceFile,
        null=True,
        blank=True,
        related_name="fewshot_outputs",
        on_delete=models.PROTECT,
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["phase", "order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["phase", "name"], name="unique_fewshot_name")
        ]

    def clean(self):
        errors = {}
        for side in ("input", "output"):
            text = bool((getattr(self, f"{side}_text") or "").strip())
            file = getattr(self, f"{side}_file_id") is not None
            if text == file:  # both set, or neither
                errors[f"{side}_text"] = (
                    f"Provide exactly one of {side}_text or {side}_file."
                )
        if errors:
            raise ValidationError(errors)

    def _resolve(self, side):
        file = getattr(self, f"{side}_file")
        if file is not None:
            return file.text
        return getattr(self, f"{side}_text")

    @property
    def resolved_input(self):
        return self._resolve("input")

    @property
    def resolved_output(self):
        return self._resolve("output")

    def __str__(self):
        return f"[phase {self.phase}] {self.name}"
```

Append to `backend/corpus/serializers.py`:

```python
from django.core.exceptions import ValidationError as DjangoValidationError

from corpus.models import FewShotExample


class FewShotExampleSerializer(serializers.ModelSerializer):
    resolved_input = serializers.CharField(read_only=True)
    resolved_output = serializers.CharField(read_only=True)

    class Meta:
        model = FewShotExample
        fields = [
            "id",
            "phase",
            "name",
            "input_text",
            "input_file",
            "output_text",
            "output_file",
            "resolved_input",
            "resolved_output",
            "order",
            "is_active",
        ]

    def validate(self, attrs):
        """Run the model's exactly-one-of rule through the API too."""
        instance = FewShotExample(**{**self._existing(), **attrs})
        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error
        return attrs

    def _existing(self):
        """Current values, keyed exactly as incoming attrs are, so a PATCH merges.

        The foreign-key entries must use the field names (`input_file`), not the
        attnames (`input_file_id`). DRF hands validate() the related object under
        the field name, and a dict merge only overrides a key that matches — key
        these by attname and a PATCH validates stale state while the raw payload
        is saved unchecked.
        """
        if self.instance is None:
            return {}
        return {
            field: getattr(self.instance, field)
            for field in (
                "phase",
                "name",
                "input_text",
                "input_file",
                "output_text",
                "output_file",
            )
        }
```

Append to `backend/corpus/views.py`:

```python
from corpus.models import FewShotExample
from corpus.serializers import FewShotExampleSerializer


class FewShotExampleViewSet(viewsets.ModelViewSet):
    serializer_class = FewShotExampleSerializer

    def get_queryset(self):
        queryset = FewShotExample.objects.all()
        phase = self.request.query_params.get("phase")
        return queryset.filter(phase=phase) if phase else queryset
```

Register in `backend/corpus/urls.py`:

```python
from corpus.views import FewShotExampleViewSet

router.register("fewshots", FewShotExampleViewSet, basename="fewshot")
```

Append to `backend/corpus/admin.py`:

```python
from corpus.models import FewShotExample


@admin.register(FewShotExample)
class FewShotExampleAdmin(admin.ModelAdmin):
    list_display = ("name", "phase", "order", "is_active")
    list_filter = ("phase", "is_active")
```

- [x] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations corpus
uv run pytest corpus/tests/ -v
```

Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/corpus
git commit -m "feat: add FewShotExample pairs with exactly-one-of validation"
```

---

### Task 12: Corpus API enforcing generated-row immutability

**Files:**
- Create: `backend/corpus/permissions.py`
- Modify: `backend/corpus/serializers.py`, `backend/corpus/views.py`, `backend/corpus/urls.py`
- Create: `backend/corpus/tests/test_corpus_api.py`

**Interfaces:**
- Consumes: `SystemDescription`, `Scenario`, `Origin` from Task 10.
- Produces: `corpus.permissions.ReadOnlyIfGenerated`; `CRUD /api/system-descriptions/?origin=`, `CRUD /api/scenarios/?origin=&system=`. `PATCH`/`DELETE` on a generated row returns 403.

- [x] **Step 1: Write the failing test**

Create `backend/corpus/tests/test_corpus_api.py`:

```python
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
    rows = api_client.get("/api/system-descriptions/?origin=seed").json()
    assert [row["title"] for row in rows] == ["Seed system"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest corpus/tests/test_corpus_api.py -v`
Expected: FAIL — 404 on `/api/system-descriptions/`.

- [x] **Step 3: Write the implementation**

`backend/corpus/permissions.py`:

```python
from rest_framework.permissions import SAFE_METHODS, BasePermission


class ReadOnlyIfGenerated(BasePermission):
    """Generated rows are experimental records, never edited or cherry-picked.

    Removing generated material is done by deleting the run that produced it,
    which cascades. Deleting one row at a time would be selective reporting.
    """

    message = (
        "Generated material is immutable. Change the prompt or inputs and "
        "generate again, or delete the run that produced it."
    )

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.is_editable
```

Append to `backend/corpus/serializers.py`:

```python
from corpus.models import Scenario, SystemDescription


class SystemDescriptionSerializer(serializers.ModelSerializer):
    is_editable = serializers.BooleanField(read_only=True)

    class Meta:
        model = SystemDescription
        fields = [
            "id",
            "title",
            "slug",
            "body",
            "origin",
            "is_editable",
            "created_by",
            "created_at",
            "updated_at",
        ]
        # Only the pipeline may write `origin`; the API always creates `manual`.
        read_only_fields = ["slug", "origin", "created_by", "created_at", "updated_at"]


class ScenarioSerializer(serializers.ModelSerializer):
    is_editable = serializers.BooleanField(read_only=True)
    card = serializers.DictField(read_only=True)

    class Meta:
        model = Scenario
        fields = [
            "id",
            "system_description",
            "scenario_id",
            "actor",
            "trigger",
            "standing_state",
            "asset",
            "threat",
            "outcome",
            "narrative",
            "intended_hcvs",
            "abox",
            "card",
            "origin",
            "is_editable",
            "order",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["origin", "card", "created_at", "updated_at"]
```

Append to `backend/corpus/views.py`:

```python
from corpus.models import Origin, Scenario, SystemDescription
from corpus.permissions import ReadOnlyIfGenerated
from corpus.serializers import ScenarioSerializer, SystemDescriptionSerializer


class SystemDescriptionViewSet(viewsets.ModelViewSet):
    serializer_class = SystemDescriptionSerializer
    permission_classes = [IsAuthenticated, ReadOnlyIfGenerated]

    def get_queryset(self):
        queryset = SystemDescription.objects.all()
        origin = self.request.query_params.get("origin")
        return queryset.filter(origin=origin) if origin else queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, origin=Origin.MANUAL)


class ScenarioViewSet(viewsets.ModelViewSet):
    serializer_class = ScenarioSerializer
    permission_classes = [IsAuthenticated, ReadOnlyIfGenerated]

    def get_queryset(self):
        queryset = Scenario.objects.all()
        params = self.request.query_params
        if origin := params.get("origin"):
            queryset = queryset.filter(origin=origin)
        if system := params.get("system"):
            queryset = queryset.filter(system_description_id=system)
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, origin=Origin.MANUAL)
```

Register in `backend/corpus/urls.py`:

```python
from corpus.views import ScenarioViewSet, SystemDescriptionViewSet

router.register(
    "system-descriptions", SystemDescriptionViewSet, basename="system-description"
)
router.register("scenarios", ScenarioViewSet, basename="scenario")
```

- [x] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest corpus/tests/ -v`
Expected: all tests PASS.

- [x] **Step 5: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/corpus
git commit -m "feat: add corpus API refusing writes to generated material"
```

---

### Task 13: Seed-pack parser and seed_corpus command

**Files:**
- Create: `backend/corpus/parsers.py`
- Create: `backend/corpus/management/__init__.py`, `backend/corpus/management/commands/__init__.py`, `backend/corpus/management/commands/seed_corpus.py`
- Create: `backend/corpus/tests/test_parsers.py`, `backend/corpus/tests/test_seed_corpus.py`

**Interfaces:**
- Consumes: `SystemDescription`, `Scenario`, `Origin`, `ResourceFile`, `FewShotExample`; `settings.RESOURCES_DIR`.
- Produces: `corpus.parsers.parse_scenarios(markdown) -> list[dict]` with keys `scenario_id, actor, trigger, standing_state, asset, threat, outcome, narrative`; `python manage.py seed_corpus [--pack oldsmar]`.

The seed Markdown looks like this (from `resources/seeds/oldsmar/scenarios.md`):
each scenario opens with an `# sc_NN` heading, then a headerless two-column
table of the six card fields, then a `## The scenario` section holding the
narrative. Content before the first `# sc_` heading is the template preamble and
must be skipped.

- [x] **Step 1: Write the failing parser test**

Create `backend/corpus/tests/test_parsers.py`:

```python
from corpus.parsers import parse_scenarios

SAMPLE = """Scenario Cards and Scenarios

## The template

|  |  |
| --- | --- |
| Actor | Everyone involved, by their id. |
| Outcome | What is now true. |

# sc_01

|  |  |
| --- | --- |
| Actor | EP1 (ExternalParty), who opens the session. |
| Trigger | After 22:00. 1. EP1 opens the session. |
| Standing state | PlantConsoleAuth protects the SCADA Service. |
| Asset | The SCADA Service (Service). |
| Threat | Spoofing, then Tampering. |
| Outcome | The feed ran at 11,100 parts per million for 8 hours. |

## The scenario

After 22:00. The last shift has ended.

1. EP1 opens the TeamViewer Session to W2.

Outcome: the feed ran at 11,100 parts per million for 8 hours.

# sc_02

|  |  |
| --- | --- |
| Actor | R4471 (SeniorCitizen), aged 78. |
| Threat | Spoofing. |

## The scenario

A Thursday at the reception desk.
"""


def test_skips_the_template_preamble():
    scenarios = parse_scenarios(SAMPLE)
    assert [row["scenario_id"] for row in scenarios] == ["sc_01", "sc_02"]


def test_reads_every_card_field():
    first = parse_scenarios(SAMPLE)[0]
    assert first["actor"] == "EP1 (ExternalParty), who opens the session."
    assert first["trigger"] == "After 22:00. 1. EP1 opens the session."
    assert first["standing_state"] == "PlantConsoleAuth protects the SCADA Service."
    assert first["asset"] == "The SCADA Service (Service)."
    assert first["threat"] == "Spoofing, then Tampering."
    assert first["outcome"] == "The feed ran at 11,100 parts per million for 8 hours."


def test_narrative_is_everything_after_the_scenario_heading():
    first = parse_scenarios(SAMPLE)[0]
    assert first["narrative"].startswith("After 22:00. The last shift has ended.")
    assert "1. EP1 opens the TeamViewer Session to W2." in first["narrative"]
    assert "| Actor |" not in first["narrative"]


def test_absent_card_fields_become_empty_strings():
    second = parse_scenarios(SAMPLE)[1]
    assert second["threat"] == "Spoofing."
    assert second["trigger"] == ""
    assert second["outcome"] == ""


def test_empty_input_yields_no_scenarios():
    assert parse_scenarios("") == []


def test_parses_the_real_seed_file(settings):
    text = (settings.RESOURCES_DIR / "seeds/oldsmar/scenarios.md").read_text()
    scenarios = parse_scenarios(text)
    assert [row["scenario_id"] for row in scenarios] == ["sc_01", "sc_02", "sc_03"]
    assert scenarios[0]["threat"] == "Spoofing, then Tampering."
    assert scenarios[2]["threat"] == "Unawareness."
    assert all(row["narrative"] for row in scenarios)
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest corpus/tests/test_parsers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'corpus.parsers'`.

- [x] **Step 3: Write the parser**

`backend/corpus/parsers.py`:

```python
"""Parse the expert seed pack out of the Markdown produced by extract_docx.py.

Each scenario is an `# sc_NN` heading, a headerless two-column table of the six
card fields, and a `## The scenario` section holding the narrative. Anything
before the first `# sc_` heading is the template preamble and is skipped.
"""

import re

CARD_FIELDS = {
    "actor": "actor",
    "trigger": "trigger",
    "standing state": "standing_state",
    "asset": "asset",
    "threat": "threat",
    "outcome": "outcome",
}

SCENARIO_HEADING = re.compile(r"^#\s+(sc_\w+)\s*$", re.MULTILINE)
NARRATIVE_HEADING = re.compile(r"^##\s+The scenario\s*$", re.MULTILINE | re.IGNORECASE)
TABLE_ROW = re.compile(r"^\|\s*(?P<label>[^|]*?)\s*\|\s*(?P<value>.*?)\s*\|\s*$")
SEPARATOR_ROW = re.compile(r"^\|[\s:|-]+\|$")


def _parse_card(block):
    card = {field: "" for field in CARD_FIELDS.values()}
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|") or SEPARATOR_ROW.match(line):
            continue
        match = TABLE_ROW.match(line)
        if not match:
            continue
        field = CARD_FIELDS.get(match.group("label").strip().lower())
        if field:
            card[field] = match.group("value").replace("\\|", "|").strip()
    return card


def _split_body(block):
    """Return (card_block, narrative) for one scenario's body."""
    match = NARRATIVE_HEADING.search(block)
    if not match:
        return block, ""
    return block[: match.start()], block[match.end() :].strip()


def parse_scenarios(markdown):
    """Return one dict per scenario, in document order."""
    if not markdown:
        return []

    matches = list(SCENARIO_HEADING.finditer(markdown))
    scenarios = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        card_block, narrative = _split_body(markdown[match.end() : end])
        scenarios.append(
            {"scenario_id": match.group(1), **_parse_card(card_block), "narrative": narrative}
        )
    return scenarios
```

- [x] **Step 4: Run the parser test to verify it passes**

Run: `cd backend && uv run pytest corpus/tests/test_parsers.py -v`
Expected: all tests PASS.

- [x] **Step 5: Write the failing command test**

Create `backend/corpus/tests/test_seed_corpus.py`:

```python
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from corpus.models import FewShotExample, Origin, ResourceFile, Scenario, SystemDescription


@pytest.mark.django_db
def test_seeds_the_oldsmar_pack():
    call_command("seed_corpus")

    system = SystemDescription.objects.get(origin=Origin.SEED)
    assert system.title == "Oldsmar Water Treatment"
    assert "Bruce T. Haddock Water Treatment Plant" in system.body

    scenarios = list(system.scenarios.all())
    assert [row.scenario_id for row in scenarios] == ["sc_01", "sc_02", "sc_03"]
    assert scenarios[0].origin == Origin.SEED
    assert scenarios[0].threat == "Spoofing, then Tampering."
    assert scenarios[0].order == 1


@pytest.mark.django_db
def test_registers_resources_and_a_phase_2_fewshot():
    call_command("seed_corpus")

    assert ResourceFile.objects.filter(name="oldsmar-system-description").exists()
    assert ResourceFile.objects.filter(name="oldsmar-scenarios").exists()
    assert ResourceFile.objects.filter(name="hcv-vocabulary").exists()

    example = FewShotExample.objects.get(phase=2, name="oldsmar")
    assert "Bruce T. Haddock" in example.resolved_input
    assert "sc_01" in example.resolved_output


@pytest.mark.django_db
def test_reseeding_is_idempotent():
    call_command("seed_corpus")
    call_command("seed_corpus")
    assert SystemDescription.objects.count() == 1
    assert Scenario.objects.count() == 3
    assert FewShotExample.objects.count() == 1


@pytest.mark.django_db
def test_unknown_pack_reports_clearly():
    with pytest.raises(CommandError, match="No seed pack"):
        call_command("seed_corpus", "--pack", "atlantis")
```

- [x] **Step 6: Run the command test to verify it fails**

Run: `cd backend && uv run pytest corpus/tests/test_seed_corpus.py -v`
Expected: FAIL — `CommandError: Unknown command: 'seed_corpus'`.

- [x] **Step 7: Write the command**

Create `backend/corpus/management/__init__.py` and
`backend/corpus/management/commands/__init__.py`, both empty.

`backend/corpus/management/commands/seed_corpus.py`:

```python
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from corpus.models import (
    FewShotExample,
    Origin,
    ResourceFile,
    ResourceKind,
    Scenario,
    SystemDescription,
)
from corpus.parsers import parse_scenarios

PACK_TITLES = {"oldsmar": "Oldsmar Water Treatment"}


class Command(BaseCommand):
    help = "Load an expert seed pack from resources/seeds/ into the corpus."

    def add_arguments(self, parser):
        parser.add_argument("--pack", default="oldsmar", help="Seed pack directory name.")

    def handle(self, *args, **options):
        pack = options["pack"]
        directory = settings.RESOURCES_DIR / "seeds" / pack
        if not directory.is_dir():
            raise CommandError(f"No seed pack at {directory}")

        system_md = directory / "system_description.md"
        scenarios_md = directory / "scenarios.md"
        for path in (system_md, scenarios_md):
            if not path.exists():
                raise CommandError(f"No seed pack file: {path}")

        system_text = system_md.read_text(encoding="utf-8")
        scenarios_text = scenarios_md.read_text(encoding="utf-8")

        with transaction.atomic():
            system = self._seed_system(pack, system_text)
            self._seed_scenarios(system, scenarios_text)
            self._seed_resources(pack, system_text, scenarios_text)
            self._seed_fewshot(pack)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {pack}: 1 system description, "
                f"{system.scenarios.count()} scenarios"
            )
        )

    def _seed_system(self, pack, text):
        system, _ = SystemDescription.objects.update_or_create(
            slug=pack,
            defaults={
                "title": PACK_TITLES.get(pack, pack.title()),
                "body": text,
                "origin": Origin.SEED,
            },
        )
        return system

    def _seed_scenarios(self, system, text):
        for index, parsed in enumerate(parse_scenarios(text), start=1):
            Scenario.objects.update_or_create(
                system_description=system,
                scenario_id=parsed.pop("scenario_id"),
                defaults={**parsed, "origin": Origin.SEED, "order": index},
            )

    def _seed_resources(self, pack, system_text, scenarios_text):
        vocabulary = settings.RESOURCES_DIR / "ontology/vocabulary_2026-08-26.md"
        entries = [
            (f"{pack}-system-description", ResourceKind.SYSTEM_DESCRIPTION, system_text),
            (f"{pack}-scenarios", ResourceKind.SCENARIOS, scenarios_text),
        ]
        if vocabulary.exists():
            entries.append(
                ("hcv-vocabulary", ResourceKind.VOCABULARY, vocabulary.read_text("utf-8"))
            )
        for name, kind, text in entries:
            ResourceFile.objects.update_or_create(
                name=name, defaults={"kind": kind, "text": text}
            )

    def _seed_fewshot(self, pack):
        FewShotExample.objects.update_or_create(
            phase=2,
            name=pack,
            defaults={
                "input_file": ResourceFile.objects.get(name=f"{pack}-system-description"),
                "output_file": ResourceFile.objects.get(name=f"{pack}-scenarios"),
                "input_text": "",
                "output_text": "",
                "order": 1,
                "is_active": True,
            },
        )
```

- [x] **Step 8: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: every test across `config`, `core`, `taxonomy` and `corpus` PASSES.

- [x] **Step 9: Verify the seeded state end to end**

Run:

```bash
cd backend
uv run python manage.py migrate
uv run python manage.py import_taxonomy --activate
uv run python manage.py seed_corpus
uv run python manage.py shell -c "
from taxonomy.models import HCV
from corpus.models import Scenario, SystemDescription
print('HCVs:', HCV.objects.count())
print('systems:', SystemDescription.objects.count())
print('scenarios:', [s.scenario_id for s in Scenario.objects.all()])
"
```

Expected output: `HCVs: 21`, `systems: 1`, `scenarios: ['sc_01', 'sc_02', 'sc_03']`.

- [x] **Step 10: Format, lint and commit**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix . && cd ..
git add backend/corpus
git commit -m "feat: add seed-pack parser and seed_corpus command"
```

---

## Done when

- `cd backend && uv run pytest` is green.
- `cd backend && uv run ruff format --check . && uv run ruff check .` is clean.
- `manage.py migrate && manage.py import_taxonomy --activate && manage.py seed_corpus`
  produces 21 HCVs, one seed system description, three seed scenarios, three
  resource files and one Phase 2 few-shot pair.
- An admin can log in, is forced to change their password, and can set the
  OpenRouter key on `/api/settings/` while a non-staff user gets 403.
- `PATCH` on a generated corpus row returns 403; on a seed row it returns 200.

Plan 2 (prompts and the LLM client) consumes `core.keys.resolve_openrouter_key`,
`corpus.models.FewShotExample.resolved_input`/`resolved_output`, and
`taxonomy.models.Taxonomy`/`HCV`. Plan 3 adds `pipeline.PhaseRun` and the
`generation_run` foreign keys on `SystemDescription` and `Scenario`.
