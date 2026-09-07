# HCV Pipeline — Design

**Date:** 2026-09-07
**Status:** Approved for planning
**Scope:** Phases 1–3 of the vulnerability-detection pipeline, as a web application.

## 1. Purpose

The research question is not "can an LLM find vulnerabilities" but **how much of
the human-centric vulnerability taxonomy an LLM already carries, and what
changes when it is shown the taxonomy**.

The pipeline (`resources/docs/pipeline.png`) answers this by generating scenario
material under two conditions and labelling all of it with a second, different
LLM:

- **Phase 1 — Expert seed.** Human experts author system descriptions and
  scenarios of human interaction that lead to exploitation of the 21
  human-centric vulnerabilities (HCVs).
- **Phase 2 — Generation, two variants.**
  - *Run A (unbiased):* the LLM sees the seed pack only. The taxonomy is
    withheld. What it produces reveals what its training data already encodes.
  - *Run B (guided):* the LLM sees the seed pack **and** the 21 HCVs, and is
    asked to include hidden potential vulnerabilities.
- **Phase 3 — Labelling.** A *different* LLM receives every scenario from Set 1
  and Set 2 plus the 21 HCVs, and labels each scenario with the vulnerabilities
  present, choosing only from the list. A scenario may take several labels or
  none.
- **Phase 4 — Human review.** Out of scope for this build (see §11).

The critical property the application must guarantee is **variant isolation**:
Run A must never see the taxonomy, directly or through leaked context.
Everything else in this design is in service of running that comparison
reproducibly.

## 2. Non-goals

- **No metrics or statistics.** v1 stores and exports raw material. What to
  measure is deferred until the corpus exists.
- **No human review.** Phase 4 is a separate design.
- **No ontology reasoning.** ABox encoding and reasoner comparison happen
  offline against the export, not in the web application.
- **No public access.** Every account is created by an admin.

## 3. Stack

LTS where an LTS track exists; current stable otherwise. npm packages have no
LTS concept, so those are pinned to current stable and noted as such.

| Layer | Package | Version | Basis |
|---|---|---|---|
| Runtime | Python | 3.13 | stable, full wheel coverage |
| Framework | Django | **5.2.17 LTS** | security support to Apr 2028 |
| API | djangorestframework | 3.18.0 | current stable |
| Queue | django-q2 | 1.11.1 | current stable |
| Broker/cache | redis / django-redis | 8.1.0 / 7.0.0 | current stable |
| LLM client | openai | 3.8.0 | OpenAI-compatible; targets OpenRouter |
| Templating | Jinja2 | 3.1.6 | current stable |
| Validation | pydantic | 2.13.5 | current stable |
| DB (prod) | PostgreSQL | 17 | current stable major |
| Serving | gunicorn / whitenoise | 26.2.0 / 6.12.0 | current stable |
| Runtime | Node | **24.20.0 LTS** ("Krypton") | active LTS |
| UI | react / react-dom | 19.2.8 | no LTS track |
| Build | vite | 8.2.2 | no LTS track |
| Routing | react-router-dom | 7.18.3 | no LTS track |
| Styling | tailwindcss | 4.3.3 | no LTS track; CSS-first config, no `tailwind.config.js` |
| Data fetching | @tanstack/react-query | 5.102.8 | no LTS track |
| HTTP | axios | 1.20.0 | no LTS track |

SQLite in development, PostgreSQL in production. Redis is required in both,
since Django-Q2 uses it as broker; `docker-compose.yml` provides it.

The architecture follows CoReview (Django + DRF + React SPA) with two deliberate
departures:

- **No DSPy.** DSPy constructs prompts from signatures, so the text an admin
  edits is not the text the model receives. This experiment turns on prompt
  wording, so prompts are hand-written Jinja2 templates and the rendered string
  is what is sent and what is stored.
- **No OAuth / allauth.** Admin-provisioned accounts only (§7).

## 4. Repository layout

```
backend/
  config/            settings, urls, wsgi, asgi
  core/              User, AppSettings, auth views, admin bootstrap commands
  taxonomy/          Taxonomy, HCV, import command
  corpus/            SystemDescription, Scenario, ResourceFile, FewShotExample
  prompts/           PromptTemplate, sandboxed Jinja2 rendering
  pipeline/          Experiment, PipelineRun, PhaseRun, LLMCall, Label, tasks, llm client
frontend/
  src/{pages,components,contexts,api}
resources/           static research inputs (see resources/README.md)
scripts/             extract_docx.py
docs/superpowers/    specs and plans
```

## 5. Data model

### 5.1 `taxonomy`

```
Taxonomy(vocabulary_id, version, notes, is_active, source_json, created_at)
HCV(taxonomy→, hcv_id, name, definition, condition_classes: JSON, order)
    unique_together (taxonomy, hcv_id)
```

Seeded from `resources/taxonomy/hcv21_v1.1.json` by a management command.
Taxonomies are immutable once referenced by a run; a change means a new version
row. Runs pin a `Taxonomy`, so a label always resolves against the exact list the
model saw.

4 of the 21 rows carry an empty `condition_classes` list by design (HCV-09,
HCV-14, HCV-19, HCV-20). They remain valid labels.

### 5.2 `corpus`

```
SystemDescription(
    title, slug, body: text,
    origin: seed | generated | manual,
    generation_run→ PhaseRun (null),
    created_by→ (null), created_at, updated_at)

Scenario(
    system_description→,
    scenario_id,                 # "sc_01"
    actor, trigger, standing_state, asset, threat, outcome,   # the card
    narrative: text,             # numbered steps
    intended_hcvs→ HCV (M2M, blank),   # Phase 1 expert intent; unused by phases 2-3
    abox: JSON (null),           # reserved for a future encoding phase
    origin: seed | generated | manual,
    generation_run→ PhaseRun (null),
    order, created_by→ (null), created_at, updated_at)

ResourceFile(
    name, kind: system_description | scenarios | vocabulary | taxonomy | other,
    file: upload, text: text, description, uploaded_by→, created_at)

FewShotExample(
    phase: 2 | 3, name,
    input_text: text (blank),  input_file→ ResourceFile (null),
    output_text: text (blank), output_file→ ResourceFile (null),
    order, is_active)
```

Notes on the shape:

- **`origin` decides what may be edited.** Human-authored material — the
  Phase 1 expert pack (`seed`) and anything the admin writes in the app
  (`manual`) — is freely editable. **`generated` rows are immutable.** They are
  the record of what the model produced, and editing them would silently
  contaminate the measurement. If Phase 2 output is unsatisfactory, the response
  is to change the prompt or the inputs and generate again, not to correct the
  result by hand. Enforced in the serializer: `PATCH` on a `generated` row is
  rejected.
- **Deletion is per run, never per row.** A misconfigured or failed run can be
  deleted whole, cascading to its systems and scenarios. Individual generated
  rows cannot be deleted, because cherry-picking which outputs survive is
  selective reporting.
- Seed and generated material still share the same tables, so Phase 3 labels
  every scenario through one code path regardless of where it came from.
- **`FewShotExample` is an input/output pair**, so a Phase 2 few-shot is
  *(system description → scenarios)* and a Phase 3 few-shot is
  *(scenario → labels)*. Each side is either inline text or a `ResourceFile`
  reference; the file's text is read at render time. A validator requires
  exactly one of `input_text`/`input_file` and exactly one of
  `output_text`/`output_file`, so a few-shot can never render half-empty.
- **`abox` is a nullable field, not a table.** It costs nothing now and means an
  encoding phase can be added later without restructuring.

### 5.3 `prompts`

```
PromptTemplate(
    phase: 2 | 3, name, version: int,
    system_prompt: text,   # Jinja2
    user_prompt: text,     # Jinja2
    output_schema: JSON,   # JSON Schema pinned into the request
    is_active, created_by→, created_at)
    unique_together (phase, name, version)
```

**Editing creates a new version rather than mutating a row.** A run pins a
specific `PromptTemplate` id, so the exact wording behind any stored result is
always recoverable. This is the whole reason prompts are hand-written.

Rendering uses a Jinja2 `SandboxedEnvironment` with `undefined=StrictUndefined`,
so a typo in a variable name fails loudly at preview time instead of silently
rendering an empty prompt. Two globals are exposed:

- `resource("name")` → the text of a `ResourceFile` by name.
- `hcv_table()` → the pinned taxonomy rendered as a readable list.

Template context, Phase 2:

| Variable | Meaning |
|---|---|
| `seed_systems` | selected `SystemDescription` objects |
| `fewshots` | list of `{input, output}` |
| `taxonomy` | the 21 HCV rows — **injected for Run B only; absent in Run A** |
| `generate_systems`, `generate_scenarios` | the two toggles |
| `n_systems`, `n_scenarios_per_system` | requested counts |
| `target_system` | set only when `generate_systems` is false |

Template context, Phase 3: `scenario`, `system_description`, `taxonomy`
(always), `fewshots`.

The Run A template is authored without any taxonomy reference, and the renderer
refuses to bind `taxonomy` for a variant-A run. Variant isolation is enforced
at the context-construction layer, not left to prompt discipline.

### 5.4 `pipeline`

```
Experiment(
    name, description, taxonomy→,
    phase2_model, phase2_temperature, phase2_prompt→ PromptTemplate,
    phase3_model, phase3_temperature, phase3_prompt→ PromptTemplate,
    seed_systems→ SystemDescription (M2M), fewshots→ FewShotExample (M2M),
    generate_systems: bool, generate_scenarios: bool,
    target_system→ SystemDescription (null),
    n_systems, n_scenarios_per_system,
    variants: JSON list, e.g. ["A", "B"],
    created_by→, created_at)

PipelineRun(experiment→, repetition_index, status, started_at, finished_at)

PhaseRun(
    pipeline_run→ (null),        # null for ad-hoc iterate-mode runs
    phase: 2 | 3, variant: A | B | null,
    model, temperature,
    prompt_template→,            # pinned version
    taxonomy→,                   # pinned always; injected into the prompt only for B
    source_phase_run→ (null),    # phase 3 records which phase-2 run it labels
    status: pending | running | completed | failed | cancelled,
    error, started_at, finished_at, created_by→)

LLMCall(
    phase_run→, sequence, scenario→ (null),   # which scenario, for phase 3
    rendered_system_prompt: text, rendered_user_prompt: text,
    request_json: JSON,          # the complete body sent to OpenRouter
    response_json: JSON,         # the complete OpenRouter response, verbatim
    raw_response: text,          # assistant content, from response_json
    parsed_ok: bool, parse_error: text,
    http_status, transport_error: text,       # set when no body came back
    model, finish_reason, prompt_tokens, completion_tokens, cost, latency_ms,
    created_at)

Label(
    phase_run→, scenario→, hcv→,
    evidence: text, rationale: text,
    source: llm | human, created_by→ (null), created_at)
    unique_together (phase_run, scenario, hcv)
```

`Experiment.generate_systems` and `generate_scenarios` are independent toggles;
a validator requires at least one to be true, and requires `target_system` when
`generate_systems` is false.

`LLMCall` stores **both complete payloads** for every call, successful or not:
`request_json` is the exact body sent to OpenRouter, `response_json` the entire
response object as returned. Nothing is truncated or normalised on the way in.

`response_json` is the authoritative record. The scalar columns beside it —
`model`, `finish_reason`, token counts, `cost` — are convenience projections
extracted at write time so they can be filtered and summed in SQL, but anything
not modelled as a column (the serving provider, generation id, native finish
reason, per-token pricing, moderation fields) is still recoverable from the dump
without a migration. `raw_response` is likewise a projection: the assistant
message content, which is what the UI shows and what parsing consumed, so a
malformed response is inspectable rather than lost.

When a call fails before a body exists — rate limit, timeout, 5xx —
`response_json` is null and `http_status` with `transport_error` carry the
reason, so a failed call is still a full record rather than a gap.
`finish_reason` separates a model that refused from one that hit the token
ceiling mid-JSON: different problems, different fixes.

Every call is therefore viewable in full, in both modes: after a single
iterate-mode call, and for each of the calls behind a batch run. The same
records are carried into the export (§9), so a downloaded batch is
self-contained.

`Label.source` and the nullable `created_by` are the Phase 4 seam: human labels
land in the same table with `source="human"`, needing no migration.

## 6. Execution

Django-Q2 with a Redis broker, run as a separate `qcluster` process.

- Launching a batch creates `PipelineRun` + its `PhaseRun` rows as `pending`,
  then enqueues one task per phase run.
- **Phase 2** is one task per variant per repetition.
- **Phase 3** fans out to **one task per scenario**, so labelling parallelises
  across workers and a single failed scenario does not fail the run. A group
  callback marks the `PhaseRun` complete when all children finish.
- A `PhaseRun` that finishes with any failed children is `failed` with a count,
  and `POST /api/phase-runs/{id}/retry/` re-enqueues only the failures.
- The frontend polls run status; TanStack Query handles the polling interval and
  cache invalidation.

Variant A and variant B are separate tasks with separately constructed
contexts. There is no shared conversation state between them at any point.

### Provider

All calls go to OpenRouter through the `openai` SDK with `base_url` set to
OpenRouter's endpoint, so every model is reachable through one adapter and
"try a different model for Phase 2" is a dropdown change. Structured output is
requested via `response_format` with the template's JSON Schema; the response is
then validated against a Pydantic model. A schema violation is recorded on the
`LLMCall` as `parse_error` rather than raising — malformed output is data.

The client sits behind a small interface with a `FakeLLMClient` implementation,
so the entire pipeline is testable with no network and no spend.

### API key

One key for the whole application, not one per user — every run is part of the
same research project and bills to the same account.

```
core.AppSettings              singleton, pk pinned to 1
    openrouter_key: text (blank)
    updated_by→, updated_at
```

Resolution order, highest first:

1. `AppSettings.openrouter_key` — set by an admin on the Settings screen.
2. `OPENROUTER_API_KEY` — read from the environment.

The admin-entered key wins, so a key can be rotated in the running app without a
redeploy or a container restart. The environment variable is the bootstrap path:
it lets a fresh deployment work before anyone has opened the UI, and lets
development and CI supply a key without writing one to the database.

Launching a run when neither resolves fails immediately with a clear message
rather than producing a run full of 401s, and `GET /api/models/` reports the
same.

The key is `write_only` in the serializer and shown masked (first four and last
four characters) on read. It is **never** written to `LLMCall`: the
`Authorization` header is stripped before `request_json` is stored, and the
storage layer asserts no stored payload contains a value matching `sk-or-`. That
assertion matters because the export ships request payloads, and an exported
bundle is the artifact most likely to be shared with a collaborator or attached
to a paper.

## 7. Authentication

Custom `User` (email as username, no separate username field) with
`must_change_password`. An admin creates accounts via Django admin or a
management command and hands the user a password; first login forces a change.
Session authentication with CSRF, `IsAuthenticated` as the DRF default. No
social auth, no self-registration, no password reset email flow in v1.

`is_staff` gates the destructive and configuration surfaces: the OpenRouter
key, taxonomy import, prompt editing, experiment creation and run launching.

## 8. Output schemas

**Phase 2**, both toggles on:

```json
{"system_descriptions": [
  {"title": "...", "body": "...",
   "scenarios": [
     {"scenario_id": "sc_01",
      "actor": "...", "trigger": "...", "standing_state": "...",
      "asset": "...", "threat": "...", "outcome": "...",
      "narrative_steps": ["...", "..."]}]}]}
```

With `generate_systems` false, the response is a bare `{"scenarios": [...]}`
against `target_system`. With `generate_scenarios` false, `scenarios` is omitted.

**Phase 3**, one call per scenario:

```json
{"labels": [{"hcv_id": "HCV-07", "evidence": "...", "rationale": "..."}]}
```

`labels` may be empty — a scenario carrying no vulnerability is a valid and
meaningful result. An `hcv_id` outside the pinned taxonomy is a schema error
recorded on the `LLMCall`, never silently dropped, since the rate of
out-of-vocabulary labels is itself informative.

## 9. API

Session auth; every endpoint requires authentication except login.

```
POST   /api/auth/login/                       GET /api/auth/me/
POST   /api/auth/logout/                      POST /api/auth/change-password/
GET    /api/settings/  PATCH /api/settings/   openrouter_key, staff only
                                              (write_only; masked on read)

GET    /api/taxonomies/                       GET /api/taxonomies/{id}/hcvs/
CRUD   /api/resources/                        upload + text extraction
CRUD   /api/fewshots/                         ?phase=2|3
CRUD   /api/system-descriptions/              ?origin=&run=
CRUD   /api/scenarios/                        ?origin=&run=&variant=  writes
                                              on seed/manual rows only
CRUD   /api/prompts/                          ?phase=  (PATCH creates a new version)
POST   /api/prompts/{id}/preview/             → exact rendered system+user strings
GET    /api/models/                           OpenRouter model list, cached 1h

CRUD   /api/experiments/
POST   /api/experiments/{id}/run/             {n_repetitions} → batch mode
GET    /api/experiments/{id}/export/          ?format=json|csv

POST   /api/phase-runs/                       ad-hoc single phase → iterate mode
GET    /api/phase-runs/{id}/                  status polling
POST   /api/phase-runs/{id}/retry/
GET    /api/phase-runs/{id}/calls/            LLMCall inspector

GET    /api/labels/                           ?scenario=&phase_run=
```

`POST /api/prompts/{id}/preview/` renders against a chosen experiment
configuration and returns the literal strings that would be sent. This is the
main tool for prompt refinement and must never diverge from what the task sends
— both go through the same renderer.

`PATCH` and `DELETE` on a system description or scenario with
`origin="generated"` return 403. Generated material is only removed by deleting
the run that produced it, via `DELETE /api/phase-runs/{id}/`, which cascades.

### Export contents

`GET /api/experiments/{id}/export/` returns everything needed to reconstruct
the experiment without database access:

```
experiment      config, model ids, temperatures, toggles, counts, N
taxonomy        the pinned version, all 21 rows verbatim
prompts         each pinned template, at the version used
inputs          seed systems and few-shot pairs as they were rendered from
runs            per repetition and variant: status, timings, tokens, cost
systems         generated system descriptions
scenarios       card fields, narrative, origin, source run and variant
labels          hcv, evidence, rationale, source, and the call that produced it
calls           every LLMCall in full — both rendered prompts, the complete
                request and response JSON, parse errors, and the projected
                model, finish_reason, tokens, cost and latency
```

`format=json` nests this as one bundle. `format=csv` emits a zip of flat tables
on the same content, with `calls.csv` carrying the prompt and response columns
in full rather than truncated — the export is an archival record, so size is
the wrong thing to optimise. Deferred metrics and the offline ontology step both
read this bundle, which is why it carries the prompts and calls rather than only
the labels.

## 10. Screens

| Screen | Purpose |
|---|---|
| Login | email + password; forced change on first login |
| Settings | staff only: OpenRouter key, masked, with which source is currently in effect |
| Dashboard | experiments and recent runs with status |
| Resources | upload text files, view extracted text, pair them into few-shots |
| Taxonomy | the 21 HCVs, read-only, with version selector |
| Corpus | system descriptions and scenarios; filter by origin, run, variant; seed and manual rows editable, generated rows read-only and marked as such |
| Prompt editor | Jinja2 editor, version history, live preview of the exact rendered prompt |
| Experiment builder | models, prompts, taxonomy, seeds, few-shots, the two toggles, counts, variants, N |
| Run detail | live status, per-scenario progress, retry; call inspector listing every call with the full rendered prompt beside the verbatim response, copyable, never truncated |
| Labels | scenario × HCV matrix with evidence and rationale on hover |

## 11. Deferred

Both deferrals are already accommodated by the schema and need no migration:

- **Phase 4 human review.** Reviewer assignment, blind labelling vs verification
  of Phase 3 output, inter-rater agreement and adjudication into an agreed label
  set. Lands in `Label` with `source="human"`.
- **Metrics.** Coverage, per-HCV frequency, cross-repetition variance, labeller
  self-consistency, and scoring against the seed pack's `intended_hcvs`. All
  computable from stored rows whenever the measurement question is settled.
- **Ontology validation.** ABox encoding into `Scenario.abox` and reasoner
  comparison, run offline against the export.

## 12. Testing

pytest-django, TDD throughout. `FakeLLMClient` covers the pipeline end to end
without network access. The tests that matter most:

- **Variant isolation** — rendering a Run A prompt with a taxonomy in scope
  must raise, and the rendered variant-A string must contain no HCV identifier
  or name.
- **Generated material is immutable** — `PATCH` and `DELETE` on a `generated`
  system description or scenario must be refused, while the same requests on a
  `seed` or `manual` row must succeed.
- **Call records are complete** — every call must expose its two rendered
  prompts and the untouched request and response JSON, including for calls that
  failed to parse and for calls that never got a body; projected columns must
  agree with the values inside `response_json`; and the export must round-trip
  all of it, verified by comparing a downloaded bundle against the rows.
- **No key leakage** — a stored `request_json`, an API response and an export
  bundle must none of them contain an OpenRouter key, asserted against a run
  made with a recognisable sentinel key.
- **Key precedence** — with both sources set, the admin-entered key is the one
  sent; clearing it must fall back to the environment variable; with neither,
  launching a run must fail before any HTTP call is made.
- **Prompt version pinning** — editing a template mid-batch must not change what
  an in-flight run sends.
- **Schema failure handling** — an out-of-vocabulary `hcv_id`, malformed JSON,
  and an empty `labels` list must each be recorded correctly and distinctly.
- **Toggle validation** — both toggles off is rejected; `generate_systems` false
  without `target_system` is rejected.
- **Retry semantics** — retrying a partially failed Phase 3 re-runs only the
  failed scenarios and creates no duplicate labels.
