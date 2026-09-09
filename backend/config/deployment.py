"""Guards that a served deployment must satisfy.

These run from wsgi.py and asgi.py rather than from settings.py, because
`manage.py` and the test suite import settings too: refusing there would block
routine local work (a developer who has not copied .env.example yet) instead of
an unsafe deployment. Importing config.wsgi is what gunicorn does to start, so
this is where "the app refuses to start" actually bites.
"""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def verify_deployment_configuration():
    """Refuse to serve with the publicly known development SECRET_KEY."""
    if not settings.DEBUG and settings.SECRET_KEY == settings.INSECURE_SECRET_KEY:
        raise ImproperlyConfigured(
            "SECRET_KEY is still the development placeholder from .env.example. "
            "Set SECRET_KEY in the environment to a unique random value before "
            "serving with DEBUG=False."
        )
