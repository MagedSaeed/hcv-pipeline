#!/usr/bin/env bash
# One-time setup: run this repository's GitHub `origin` on Railway, which then
# redeploys on every push to main. Run it once; later releases are just
# `git push origin main`.
#
# What it creates: a Railway project with Postgres, Redis and two services built
# from the repository:
#   backend  Django API serving the built SPA (deploy/backend.Dockerfile); public
#   worker   Django-Q, runs experiments (deploy/worker.Dockerfile)
#
# Before running
#   - `origin` points at the GitHub repository, with main pushed to it
#   - Railway's GitHub app can read that repository
#     (https://github.com/apps/railway-app/installations/new)
#   - signed in: `railway login`
#   - optional: OPENROUTER_API_KEY in your shell. Without it, save a key in the
#     app's Settings page after signing in.
set -euo pipefail
cd "$(dirname "$0")"

PROJECT_NAME="${PROJECT_NAME:-hcv-pipeline}"
DEPLOY_BRANCH=main
# The app ends up at https://$DOMAIN_LABEL.up.railway.app instead of Railway's
# generated backend-production-xxxx.up.railway.app.
DOMAIN_LABEL="${DOMAIN_LABEL:-$PROJECT_NAME}"
BACKEND_PORT=8000

die() {
  echo "$*" >&2
  exit 1
}

origin_url=$(git remote get-url origin 2>/dev/null) ||
  die "No 'origin' remote. Put the repository on GitHub first."
git ls-remote --exit-code --heads origin "$DEPLOY_BRANCH" >/dev/null ||
  die "origin has no '$DEPLOY_BRANCH' branch. Push it first."
if railway status --json >/dev/null 2>&1; then
  die "This directory is already linked to a Railway project; setup has run before."
fi

# owner/repo, from either https://github.com/owner/repo.git or git@github.com:owner/repo.git
repo_slug=$(printf '%s' "$origin_url" | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##')

# --- Project, databases, services -------------------------------------------

railway init --name "$PROJECT_NAME"
railway add --database postgres
railway add --database redis
railway add --service backend
railway add --service worker

railway domain --service backend --port "$BACKEND_PORT"
generated_domain=$(railway domain list --service backend --json |
  grep -o '[A-Za-z0-9.-]*\.up\.railway\.app' | head -n 1)
railway domain update "$generated_domain" --domain "$DOMAIN_LABEL" --service backend ||
  echo "Could not rename $generated_domain to $DOMAIN_LABEL (taken?); rename it in the dashboard." >&2

# --- Variables --------------------------------------------------------------
# Single quotes keep ${{...}} away from bash; Railway resolves these references.

shared_vars=(
  'DEBUG=False'
  'DATABASE_URL=${{Postgres.DATABASE_URL}}'
  'REDIS_URL=${{Redis.REDIS_URL}}'
  'Q_WORKERS=2'
  'Q_TIMEOUT=3600'
  'Q_RETRY=4200'
)

railway variable set --service backend --skip-deploys "${shared_vars[@]}" \
  'RAILWAY_DOCKERFILE_PATH=deploy/backend.Dockerfile' \
  "PORT=$BACKEND_PORT" \
  'ALLOWED_HOSTS=${{RAILWAY_PUBLIC_DOMAIN}}' \
  'CSRF_TRUSTED_ORIGINS=https://${{RAILWAY_PUBLIC_DOMAIN}}'

# Secrets go through stdin, out of the local process list and shell history.
python3 -c 'import secrets; print(secrets.token_urlsafe(50), end="")' |
  railway variable set SECRET_KEY --stdin --service backend --skip-deploys

railway variable set --service backend --skip-deploys \
  'DJANGO_SUPERUSER_USERNAME=' 'DJANGO_SUPERUSER_EMAIL=' 'DJANGO_SUPERUSER_PASSWORD=' ||
  echo "Could not add the empty DJANGO_SUPERUSER_* variables; add them in the dashboard." >&2

# The worker must share the backend's SECRET_KEY: Django-Q signs the tasks the backend enqueues.
railway variable set --service worker --skip-deploys "${shared_vars[@]}" \
  'RAILWAY_DOCKERFILE_PATH=deploy/worker.Dockerfile' \
  'SECRET_KEY=${{backend.SECRET_KEY}}'

if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
  printf '%s' "$OPENROUTER_API_KEY" |
    railway variable set OPENROUTER_API_KEY --stdin --service backend --skip-deploys
  railway variable set --service worker --skip-deploys \
    'OPENROUTER_API_KEY=${{backend.OPENROUTER_API_KEY}}'
fi

# --- Connect the repository: from here on, each push to main deploys ---------

railway service source connect --service backend --repo "$repo_slug" --branch "$DEPLOY_BRANCH"
railway service source connect --service worker --repo "$repo_slug" --branch "$DEPLOY_BRANCH"

echo
echo "Deploying from $repo_slug@$DEPLOY_BRANCH."
railway domain list --service backend
echo "Next: fill in DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD (and optionally"
echo "DJANGO_SUPERUSER_USERNAME) on the backend service; its redeploy creates the account."
