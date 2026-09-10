# HCV pipeline — running it locally

Four processes make up the app in development:

| Process | Address | Role |
|---|---|---|
| Redis | `localhost:6379` | broker for Django-Q2 |
| Django API | `http://localhost:8000` | REST API, admin, auth |
| Django-Q worker (`qcluster`) | — | runs experiments and iterate-mode runs |
| Vite dev server | `http://localhost:5173` | this SPA; proxies `/api` to `:8000` |

Open the app at **http://localhost:5173**. The browser talks only to Vite, which forwards `/api` to Django, so session and CSRF cookies behave as same-origin.

## 1. Environment

Copy `.env.example` to `.env` at the **repository root** and set `OPENROUTER_API_KEY`.

Set `DEBUG=True` for local work. With `DEBUG` unset, Django refuses to start while `SECRET_KEY` is still the development placeholder — a deliberate guard in `backend/config/deployment.py`. If your `.env` holds only the API key, either add `DEBUG=True` to it or prefix the server command with `DEBUG=True`.

The defaults already match this setup, so you don't need to set them: `ALLOWED_HOSTS=localhost,127.0.0.1`, and `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS` = `http://localhost:5173`.

## 2. Redis

Run **one** of these, not both — they compete for port 6379:

- a system Redis already listening on `127.0.0.1:6379`, or
- `docker compose up -d redis` from the repository root.

PostgreSQL isn't needed in development; the backend uses SQLite. `docker-compose.yml` provides it for trying the production database locally.

## 3. Backend

From `backend/`:

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py import_taxonomy --activate   # loads HCV-21 and marks it active
uv run python manage.py seed_corpus                  # the Oldsmar expert seed pack
uv run python manage.py create_user you@example.com --staff
```

`create_user` prints a random password unless you pass `--password`, and you'll be asked to change it at first login. `--staff` is needed to create experiments, launch runs, and edit prompts.

Then, in two terminals:

```bash
DEBUG=True uv run python manage.py runserver   # API on :8000
uv run python manage.py qcluster               # the worker
```

Without the worker, launched runs stay **queued** indefinitely. That's safe — you can cancel them — but nothing progresses.

## 4. Frontend

From `frontend/`, on Node 22 LTS or newer:

```bash
npm ci
npm run dev    # http://localhost:5173
```

## 5. Checks

| | Command | Note |
|---|---|---|
| Backend tests | `uv run pytest` | ends with a one-line summary (`N passed …`); don't add `-q` — `addopts` already has it, and `-qq` hides that line |
| Backend lint | `uv run ruff check . && uv run ruff format --check .` | run both; `ruff check` alone doesn't catch formatting |
| Frontend tests | `npm run test` | vitest + msw; no backend needed |
| Frontend lint | `npm run lint` | oxlint |

## Worth knowing before a real run

- **Iterate-mode runs are scratch.** Runs started from the prompt editor's panel are never archived and never appear in an export bundle.
- **Cancel marks a run dead; it doesn't interrupt a worker mid-call.** A call already in flight may still finish and write its output.
- **Repetitions queue together** and run as workers free up (2 by default). At the default configuration each costs about **$1.61** and takes about **23 minutes**.
- **A key saved in Settings takes precedence over `OPENROUTER_API_KEY` in `.env`.** If calls start failing with a 401 after you've changed `.env`, check Settings — a stale key saved there wins. To fall back to `.env`, use **Clear — use the environment key** in Settings.
- **The export reflects what each run actually used.** Every repetition pins its own configuration at launch, so editing an experiment afterwards doesn't change an existing run's export.
