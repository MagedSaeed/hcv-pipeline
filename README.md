# HCV Pipeline

A web app for studying how well AI language models understand the ways people make
smart-city systems vulnerable: shared passwords, unchecked remote access, and the
like. These are the 21 **human-centric vulnerabilities (HCVs)**.

## How it works

1. **Experts write an example.** A real system (the Oldsmar water treatment plant)
   and a few attack scenarios involving people.
2. **An AI writes more scenarios, two ways.** Once without seeing the list of 21
   vulnerabilities, and once with it. The difference shows what the model already
   knows.
3. **A second AI labels every scenario** with the vulnerabilities it finds.

Everything is stored and can be exported for analysis.

## Getting started

You'll need [Python 3.13](https://www.python.org/) with [uv](https://docs.astral.sh/uv/),
[Node.js 22](https://nodejs.org/), [Docker](https://www.docker.com/), and an
[OpenRouter](https://openrouter.ai/) API key.

**1. Get the code and add your settings**

```bash
git clone https://github.com/MagedSaeed/hcv-pipeline.git
cd hcv-pipeline
cp .env.example .env
```

Open `.env` and paste your key after `OPENROUTER_API_KEY=`.

**2. Start Redis** (the app's job queue)

```bash
docker compose up -d redis
```

**3. Set up the backend and create your account**

```bash
cd backend
uv sync
uv run python manage.py migrate
uv run python manage.py import_taxonomy --activate
uv run python manage.py seed_corpus
uv run python manage.py seed_prompts
uv run python manage.py create_user you@example.com --staff
```

The last command prints a temporary password. You'll choose a new one when you
first sign in.

**4. Run the app.** Use three terminals:

```bash
# Terminal 1, in backend/: the API
uv run python manage.py runserver

# Terminal 2, in backend/: runs experiments in the background
uv run python manage.py qcluster

# Terminal 3, in frontend/: the website
npm ci
npm run dev
```

**5. Open http://localhost:5173** and sign in.

## Deployment

The app runs on [Railway](https://railway.com). Every push to `main` deploys it.
Setting up a new Railway project is a one-time step: see
[`railway_deploy.sh`](railway_deploy.sh).

## What's where

| Folder | Contents |
|---|---|
| `backend/` | The Django API and the experiment runner |
| `frontend/` | The React website |
| `resources/` | The research inputs: vulnerability list, example system, scenarios |
| `deploy/` | Docker files used by Railway |
| `docs/` | Design notes and plans |

## Learn more

- [Running locally, in more detail](frontend/README.md), including tests and tips
  before a real run
- [The research inputs](resources/README.md)
- [The full design](docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md)
