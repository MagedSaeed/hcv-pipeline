import os

from django.core.wsgi import get_wsgi_application

from config.deployment import verify_deployment_configuration

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Gunicorn starts the app by importing this module, so an unsafely configured
# deployment fails here instead of serving.
verify_deployment_configuration()
application = get_wsgi_application()
