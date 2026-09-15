#!/bin/sh
# Runs each time the worker container starts. The backend applies migrations
# when it starts; wait for them so no task meets a half-migrated schema.
set -e

until python manage.py migrate --check >/dev/null 2>&1; do
    echo "Migrations not applied yet, or the database is unreachable; retrying in 5s"
    sleep 5
done

exec python manage.py qcluster
