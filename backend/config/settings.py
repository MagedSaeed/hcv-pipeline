from pathlib import Path

import dj_database_url
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent

# The development placeholder, also committed in .env.example. Serving with it
# is refused by config.deployment.verify_deployment_configuration().
INSECURE_SECRET_KEY = "dev-insecure-key-change-me"

SECRET_KEY = config("SECRET_KEY", default=INSECURE_SECRET_KEY)
# Off unless explicitly asked for: a deployment missing one environment
# variable must not end up serving tracebacks. Local development turns it on
# through .env (see .env.example).
DEBUG = config("DEBUG", default=False, cast=bool)
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
    "django_q",
    "core",
    "taxonomy",
    "corpus",
    "prompts",
    "pipeline",
]

AUTH_USER_MODEL = "core.User"

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

if not DEBUG:
    # Session and CSRF cookies only over HTTPS, and no framing of the app.
    # HSTS and the HTTP->HTTPS redirect belong to the reverse proxy in front.
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    MIDDLEWARE.append("django.middleware.clickjacking.XFrameOptionsMiddleware")

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
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # Every list endpoint is an envelope from the start. The pipeline will
    # produce thousands of scenarios and call records, and adding pagination
    # after a frontend reads bare arrays would break its response contract.
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

# Local-memory cache is enough here: the only cached value is the OpenRouter
# model catalogue, and a per-process copy costs one extra fetch per worker.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hcv-pipeline",
    }
}

# Django-Q2. The pipeline fans Phase 3 out to one task per scenario, so a
# single bad scenario fails only itself.
Q_CLUSTER = {
    "name": "hcv-pipeline",
    "workers": config("Q_WORKERS", default=2, cast=int),
    # A repetition currently runs serially inside ONE task: every variant's
    # generation call, then one labelling call per scenario it produced. The
    # timeout has to cover every provider call in that repetition end to end,
    # not just the slowest one — a shorter window kills the worker mid-run and
    # strands the repetition.
    "timeout": config("Q_TIMEOUT", default=3600, cast=int),
    # Must exceed `timeout`, or a task is re-queued while it is still running.
    "retry": config("Q_RETRY", default=4200, cast=int),
    "max_attempts": 1,
    "save_limit": 0,
    "catch_up": False,
    "redis": config("REDIS_URL", default="redis://localhost:6379/0"),
}

# Vite dev server.
CORS_ALLOWED_ORIGINS = config("CORS_ALLOWED_ORIGINS", default="http://localhost:5173", cast=Csv())
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="http://localhost:5173", cast=Csv())

# Research inputs live outside backend/, at the repository root.
RESOURCES_DIR = BASE_DIR.parent / "resources"

OPENROUTER_API_KEY = config("OPENROUTER_API_KEY", default="")
