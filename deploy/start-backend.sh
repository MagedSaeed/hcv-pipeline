#!/bin/sh
# Runs each time the backend container starts, where the database is reachable.
set -e

python manage.py migrate --noinput

# Seed data is added only when missing, so what researchers change in the app
# survives every redeploy.
python manage.py import_taxonomy --activate --if-missing
python manage.py seed_corpus --if-missing
python manage.py seed_prompts

# Skips itself unless DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD are
# set. A refused password is logged but does not stop the app from starting.
python manage.py ensure_superuser || echo "Superuser not created; see the error above." >&2

exec gunicorn config.wsgi:application \
    --bind "[::]:${PORT:-8000}" \
    --workers 2 \
    --timeout 120 \
    --access-logfile -
