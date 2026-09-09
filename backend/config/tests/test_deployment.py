import importlib.util

import decouple
import pytest
from django.core.exceptions import ImproperlyConfigured

import config.settings
from config.deployment import verify_deployment_configuration

CLICKJACKING_MIDDLEWARE = "django.middleware.clickjacking.XFrameOptionsMiddleware"


@pytest.fixture
def load_settings(monkeypatch):
    """Execute config/settings.py under a chosen environment.

    django.conf.settings is a snapshot taken once at startup, so re-running the
    module is the only way to exercise the DEBUG branches a deployment takes.
    The decouple repository is emptied first, so a developer's own .env cannot
    decide what these tests observe.
    """
    monkeypatch.setattr(decouple.config, "config", decouple.Config(decouple.RepositoryEmpty()))
    for name in ("DEBUG", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)

    def load(**environment):
        for key, value in environment.items():
            monkeypatch.setenv(key, value)
        # A fresh module object rather than importlib.reload: reload re-executes
        # in the existing namespace, where a previous run's SESSION_COOKIE_SECURE
        # would survive the DEBUG=True branch that never sets it. It also leaves
        # the live config.settings module untouched.
        spec = importlib.util.spec_from_file_location(
            "config.settings_probe", config.settings.__file__
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


def test_debug_is_off_unless_the_environment_asks_for_it(load_settings):
    """A deployment that forgets a variable must not serve tracebacks."""
    assert load_settings().DEBUG is False


def test_debug_off_secures_the_cookies_and_refuses_framing(load_settings):
    module = load_settings(SECRET_KEY="a-real-unique-key")

    assert module.SESSION_COOKIE_SECURE is True
    assert module.CSRF_COOKIE_SECURE is True
    assert CLICKJACKING_MIDDLEWARE in module.MIDDLEWARE


def test_debug_on_leaves_local_development_over_http_working(load_settings):
    module = load_settings(DEBUG="True")

    assert module.DEBUG is True
    assert getattr(module, "SESSION_COOKIE_SECURE", False) is False
    assert getattr(module, "CSRF_COOKIE_SECURE", False) is False
    assert CLICKJACKING_MIDDLEWARE not in module.MIDDLEWARE


def test_serving_with_the_placeholder_secret_key_is_refused(settings):
    settings.DEBUG = False
    settings.SECRET_KEY = settings.INSECURE_SECRET_KEY

    with pytest.raises(ImproperlyConfigured, match="SECRET_KEY"):
        verify_deployment_configuration()


def test_serving_with_a_configured_secret_key_is_allowed(settings):
    settings.DEBUG = False
    settings.SECRET_KEY = "a-real-unique-key"

    verify_deployment_configuration()


def test_local_development_may_keep_the_placeholder(settings):
    settings.DEBUG = True
    settings.SECRET_KEY = settings.INSECURE_SECRET_KEY

    verify_deployment_configuration()


def test_env_example_does_not_move_the_database_to_the_working_directory():
    """A relative sqlite:// URL makes the database location depend on where the
    command was run from, while the settings default is absolute."""
    example = config.settings.BASE_DIR.parent / ".env.example"
    active = [
        line.strip()
        for line in example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    assert not any(line.startswith("DATABASE_URL=sqlite") for line in active)
    assert "DEBUG=True" in active


def test_the_task_timeout_covers_a_whole_serial_repetition(load_settings):
    """A repetition currently runs serially inside ONE django-q task: every
    variant's generation call and then one labelling call per scenario. Ten
    minutes covered barely a handful of those, so the worker killed the task
    mid-repetition and left it stranded. The retry window must stay ahead of the
    timeout, or a task is re-queued while it is still running."""
    module = load_settings()

    assert module.Q_CLUSTER["timeout"] == 3600
    assert module.Q_CLUSTER["retry"] == 4200
    assert module.Q_CLUSTER["retry"] > module.Q_CLUSTER["timeout"]


def test_the_default_worker_count_is_two(load_settings):
    """Each worker runs one repetition end to end, serially — a higher
    default fans more repetitions out concurrently than a typical dev
    machine (or the OpenRouter rate limit) comfortably supports."""
    module = load_settings()

    assert module.Q_CLUSTER["workers"] == 2


def test_env_example_documents_the_django_q_settings():
    """Q_WORKERS, Q_TIMEOUT and Q_RETRY are read via python-decouple's
    `config()` in settings.py with defaults baked in — nothing forces a
    reader to open that file to learn they exist, or what they default to,
    unless .env.example says so."""
    example = config.settings.BASE_DIR.parent / ".env.example"
    text = example.read_text(encoding="utf-8")

    for name, default in (("Q_WORKERS", "2"), ("Q_TIMEOUT", "3600"), ("Q_RETRY", "4200")):
        assert f"{name}={default}" in text
