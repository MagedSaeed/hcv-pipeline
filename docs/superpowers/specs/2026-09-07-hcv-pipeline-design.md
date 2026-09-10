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
| Middleware | django-cors-headers | 4.9.0 | current stable; CORS for the Vite dev server |
| DB driver | psycopg[binary] | 3.3.5 | current stable; the PostgreSQL driver |
| Config | python-decouple / dj-database-url | 3.8 / 3.1.2 | current stable; environment-driven settings (`DATABASE_URL`, etc.) |
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
| Frontend tests | vitest | 4.0.6 | no LTS track; Vite-native test runner |
| Frontend tests | @testing-library/react | 17.0.1 | no LTS track |
| API mocking | msw | 2.12.4 | no LTS track; mocks at the network boundary, not by stubbing modules |

The backend rows above are pinned versions verified against the running code
(`backend/pyproject.toml`). The frontend rows are not yet verifiable against
anything: no `frontend/` directory and no `package.json` exist in the
repository yet. Those versions are pinned in advance, for when that work
starts — aspirational, not descriptive, until a frontend actually exists.

SQLite in development, PostgreSQL in production. Redis is required in both,
since Django-Q2 uses it as broker. `docker-compose.yml` provisions Redis and
PostgreSQL for local development only — it runs no Django, `qcluster`, or
frontend service of its own; those are started directly on the host in this
build, not containerized.

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
  prompts/           PromptTemplate, sandboxed Jinja2 rendering, isolation guards
  llm/               OpenRouter client, fake client, response schemas, model catalogue
  pipeline/          Experiment, PipelineRun, PhaseRun, LLMCall, Label, orchestration,
                     execution_config, generation, labelling, export, redaction, tasks
frontend/            not built yet — see §10
  src/{pages,components,contexts,api}
resources/           static research inputs (see resources/README.md)
scripts/             extract_docx.py
docs/superpowers/    specs and plans
```

## 5. Data model

### 5.1 `taxonomy`

```
Taxonomy(vocabulary_id, version, notes, is_active, source_json, created_at)
    unique_together (vocabulary_id, version)
    at most one row may carry is_active=True — a partial database constraint,
    not a save() override, so it holds against .update(), bulk writes and
    data migrations too
    ordered by (vocabulary_id, version)
HCV(taxonomy→, hcv_id, name, definition, condition_classes: JSON, order)
    unique_together (taxonomy, hcv_id)
    ordered by (order, hcv_id)
```

Seeded from `resources/taxonomy/hcv21_v1.1.json` by a management command.
Taxonomies are immutable once referenced by a run; a change means a new version
row. Runs pin a `Taxonomy`, so a label always resolves against the exact list the
model saw.

4 of the 21 rows carry an empty `condition_classes` list by design (HCV-09,
HCV-14, HCV-19, HCV-20). They remain valid labels (`HCV.has_condition_class`
is a plain `bool(condition_classes)` convenience for the UI).

`Taxonomy.active()` returns the one active taxonomy, or `None`. It fails
closed — raising `MultipleActiveTaxonomies` — rather than silently picking
one, if the constraint above is ever somehow violated anyway (raw SQL, a
restored dump predating it). This matters beyond tidiness: with no experiment
borrowed, an ad-hoc variant-A run's guard taxonomy — the list its leak
backstop scans against, §5.3 — *is* `Taxonomy.active()`. An earlier
implementation simply took the newest active row, which let a freshly
imported decoy taxonomy silently re-bind every unborrowed Run A's guard to
the wrong list: the scan still ran, still found nothing, and still archived
`leak_scan_ran=True` as proof — against content that had nothing to do with
what was actually sent. Raising instead of guessing means a violated
invariant fails the run, not the evidence.

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
    unique_together (system_description, generation_run, scenario_id), and,
    scoped to generation_run IS NULL only, unique_together
    (system_description, scenario_id)

ResourceFile(
    name, kind: system_description | scenarios | vocabulary | taxonomy | other,
    file: upload, text: text, description, uploaded_by→, created_at)
    ordered by name

FewShotExample(
    phase: 2 | 3, name,
    input_text: text (blank),  input_file→ ResourceFile (null),
    output_text: text (blank), output_file→ ResourceFile (null),
    order, is_active)
    unique_together (phase, name)
    ordered by (phase, order, name)
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
- **`Scenario` needs two uniqueness constraints, not one.** A generated row's
  `generation_run` is set; a seed or manual row's is `NULL`, and NULLs never
  compare equal, so one `unique_together (system_description, generation_run,
  scenario_id)` alone would not stop two hand-created rows from colliding on
  the same `scenario_id`. A second constraint, scoped to `generation_run IS
  NULL`, closes that gap for seed and manual rows specifically.
- `FewShotExample` additionally enforces a unique `(phase, name)` pair, since a
  few-shot is referred to by name, not just by id.

### 5.3 `prompts`

```
PromptTemplate(
    phase: 2 | 3, name, version: int,
    system_prompt: text,   # Jinja2
    user_prompt: text,     # Jinja2
    output_schema: JSON,   # JSON Schema pinned into the request
    is_active, created_by→, created_at)
    unique_together (phase, name, version)
    ordered by (phase, name, -version) — the latest version of a named
    template sorts first
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
at the context-construction layer, not left to prompt discipline — and, since
one guard can always be missed or bypassed by a route nobody has thought of
yet, it is enforced in layers, not by a single check:

- **the context has no `taxonomy` key at all for variant A** — not `None`,
  not an empty list, genuinely absent from the dict handed to the renderer,
  which is what makes `StrictUndefined` (above) load-bearing rather than
  decorative (see the closing paragraphs of this subsection for why that
  matters);
- **a taxonomy-kind resource is refused for variant A**, whether reached via
  the `resource()` template function or via a few-shot example's input/output
  file — the two known routes that could otherwise carry taxonomy content into
  a Run A prompt without ever touching the `taxonomy` context variable;
- **a generated-origin system may never seed or target a run** (§5.4). Its
  prose is taxonomy-*derived* — written by a model that saw the taxonomy on
  some earlier run — not a literal quote of it, so none of the text-scanning
  layers below can catch it; refusing the origin outright, before a single
  call is made, is the only thing that closes this route;
- **a run's pinned configuration snapshot validates fail-closed before
  anything renders against it** (`PipelineRun.config`, §5.4): a field a guard
  depends on — whether a few-shot's file was taxonomy-kind, say — must be
  genuinely present and of the right shape, or the run refuses to execute
  rather than guessing a default;
- **the two rendered prompt strings, plus the template's `output_schema`, are
  scanned** as a backstop once rendering finishes, against the pinned
  taxonomy's identifiers, names, and definitions — any single identifier
  match fails outright; three or more distinct name/definition matches
  combined is treated as a dumped list rather than incidental English. The
  schema is included because it ships in the same request body as the two
  prompts, so a forked or renamed template could carry Run B's schema — an
  enum of every `hcv_id`, definitions in its descriptions — into a Run A
  template whose prompts look entirely innocent;
- **a final scan walks the complete, serialised request dict immediately
  before the HTTP call** — after every other piece of request construction,
  including anything added to the body *after* rendering. This is the one
  layer that can catch a field a later refactor copies straight into the
  request (an `output_schema` added outside the template, say), which none of
  the layers above can see no matter how thorough they are —
  `assert_request_not_leaking_taxonomy`, wired into the LLM client as a
  `preflight` callable invoked on every phase-2 call, both variants, so
  nobody has to remember to attach it only for Run A (§6).

Whether the render-time scan actually ran, and against which taxonomy, is
recorded on the render result and then on the `LLMCall` itself
(`leak_scan_ran`, `guarded_by` — §5.4): for a claim as central as "Run A never
saw the taxonomy," the evidence belongs archived next to the material it
vouches for, not asserted in a comment. The taxonomy a run's guard scans
against is always **derived**, never a value a caller can choose freely — the
experiment's own pinned taxonomy for an orchestrated or borrowed run, or, with
none borrowed, whichever taxonomy is currently active (§5.4). A caller-chosen
guard taxonomy was a real, closed defect: a decoy taxonomy sharing no content
with the real one let the scans run, find nothing, and archive
`leak_scan_ran=True` as proof of an isolation check that had actually verified
nothing at all.

**Each variant gets its own, fully independent `PromptTemplate`**
(`Experiment.phase2_prompt_a` and `Experiment.phase2_prompt_b`, two separate
FKs — see §5.4; user decision, 2026-09-08, choosing full independence over a
shared-core or drift-warning alternative). A phase-2 template therefore never
needs to branch on which variant it is rendering: variant A's template is
authored WITHOUT any `taxonomy` reference at all, and variant B's is authored
assuming `taxonomy` is always present. Phase 3 is unchanged — one
`phase3_prompt` still serves both variants, because phase 3 always receives
the taxonomy regardless of which variant it is labelling.

The researcher was shown the alternative before choosing this: a single
shared phase-2 template, branching only around the taxonomy block, would
*guarantee* that everything else in the prompt — instructions, framing,
formatting — is identical between variants, so any measured difference in
what the model produces could only be attributed to the taxonomy itself.
Two fully independent templates give up that guarantee. Nothing stops them
from drifting apart in wording, structure, or instructions beyond the
taxonomy block, and a difference in results could then be a confound of
prompt drift rather than of the taxonomy — a risk the researcher weighed and
accepted in exchange for not having to write phase-2 prompts that
conditionally branch on variant. Keeping two independent templates
textually close, where that matters to the comparison, is therefore an
authoring discipline, not something the system enforces.

`StrictUndefined` (above) still matters for phase 2, even so. `taxonomy` is
*absent from the context entirely* under Run A — not `None`, not an empty
list, genuinely not a key in the context dict — so a stray `{{ taxonomy }}`
or `{% if taxonomy %}` left in a variant-A template by a copy/paste mistake
does not silently render empty: it **raises** `PromptRenderError: 'taxonomy'
is undefined` and fails the run outright, the instant a Run A repetition
reaches it. This is correct, deliberate, fail-closed behaviour — a template
must not be able to silently treat a missing taxonomy as "empty" and carry
on — and it is what keeps a stray reference from shipping a Run A prompt
that merely *looks* unbiased. See `prompts/rendering.py`'s module docstring
for the same note next to the isolation guarantee it documents.

### 5.4 `pipeline`

```
Experiment(
    name, description, taxonomy→,
    phase2_model, phase2_temperature,
    phase2_prompt_a→ PromptTemplate (null),  # variant A's own template
    phase2_prompt_b→ PromptTemplate (null),  # variant B's own template — independent, no coupling
    phase3_model, phase3_temperature, phase3_prompt→ PromptTemplate,
    seed_systems→ SystemDescription (M2M), fewshots→ FewShotExample (M2M),
    generate_systems: bool, generate_scenarios: bool,
    target_system→ SystemDescription (null),
    n_systems, n_scenarios_per_system,
    variants: JSON list, e.g. ["A", "B"],
    created_by→, created_at)

PipelineRun(
    experiment→, repetition_index, status,
    config: JSON,                 # this repetition's own pinned inputs — see below
    started_at, finished_at)

PhaseRun(
    pipeline_run→ (null),        # null for ad-hoc iterate-mode runs
    phase: 2 | 3, variant: A | B | null,
    model, temperature,
    prompt_template→,            # pinned version
    taxonomy→,                   # pinned always; injected into the prompt only for B
    source_phase_run→ (null),    # phase 3 records which phase-2 run it labels
    experiment→ (null),          # iterate mode only: borrow this experiment's LIVE inputs
    n_systems (null), n_scenarios_per_system (null),  # iterate-mode per-run overrides
    status: pending | queued | running | completed | failed | cancelled,
    error, total_children, completed_children, failed_children,
    started_at, finished_at, created_by→)

LLMCall(
    phase_run→, sequence, scenario→ (null),   # which scenario, for phase 3
    rendered_system_prompt: text, rendered_user_prompt: text,
    leak_scan_ran: bool, guarded_by→ Taxonomy (null),  # isolation evidence — §5.3
    request_json: JSON,          # the complete body sent to OpenRouter
    response_json: JSON,         # the complete OpenRouter response, verbatim
    raw_response: text,          # assistant content, from response_json
    parsed_ok: bool, parse_error: text,
    unknown_hcv_ids: JSON list,  # phase 3: hcv_ids outside the pinned taxonomy
    stray_scenarios: JSON list,  # phase 2: a redundant top-level scenarios list — §8
    http_status, transport_error: text,       # set when no body came back
    model, finish_reason, prompt_tokens, completion_tokens, cost, latency_ms,
    created_at)

Label(
    phase_run→, scenario→, hcv→,
    evidence: text, rationale: text,
    source: llm | human,
    llm_call→ (null),            # the call this label came from; null for a human label
    created_by→ (null), created_at)
    unique_together (phase_run, scenario, hcv)
    ordered by (scenario, hcv, id) — id is a deliberate final tiebreaker, so a
    paginated listing stays stable across two labels that tie on
    (scenario, hcv) alone, which a re-run or a retry can genuinely produce
```

`Experiment.generate_systems` and `generate_scenarios` are independent toggles;
a validator requires at least one to be true, and requires `target_system` when
`generate_systems` is false.

A validator also requires `phase2_prompt_a`/`phase2_prompt_b` to be set for
every variant actually listed in `variants` — not both unconditionally, since
`variants` may be `["A"]`, `["B"]`, or both — and requires whichever of the
two is set to be phase-2 templates (`phase3_prompt` likewise must be a
phase-3 template).

A validator also refuses a **generated-origin** `SystemDescription` anywhere
in `seed_systems`, and refuses one as `target_system` outright. Seeding a run
with a prior run's own output contaminates the comparison in the obvious way;
targeting is the more dangerous of the two, not merely a second instance of
it — `target_system.body` is written into the variant-A prompt
unconditionally, and its prose is taxonomy-*derived*, not a literal quote, so
none of §5.3's text-scanning guards can catch it. The origin check is the only
thing that closes this route.

These config-shaped rules — generated seeds, a generated or missing target,
both generation toggles off — live in one shared checker that
`Experiment.clean()` calls, and that the ad-hoc iterate-mode path calls too,
at both its own create-time validation and again, live, at execution time.
That sharing exists because the same rule, hand-ported separately across
`Experiment` and the ad-hoc path, drifted out of sync more than once; a
dedicated parity test now fails the build if a future rule reaches only one
of the two paths.

`PhaseRun.taxonomy` is never a free choice either. For an orchestrated or
borrowed run it is always `experiment.taxonomy`; for an ad-hoc run borrowing
no experiment it must be whichever `Taxonomy` is currently active. Both are
re-checked at execution time, not only when the row was created, because
either half of that binding — the experiment's own taxonomy, or which
taxonomy is active — can change in the meantime. A decoy taxonomy chosen
freely by a caller is exactly the failure this closes (see §5.3's closing
paragraph): the leak backstop would genuinely run and genuinely find nothing,
because it was scanning the wrong list, and archive `leak_scan_ran=True` as
false proof.

`PipelineRun.config` is a snapshot of the seven fields that live only on
`Experiment` — `seed_systems`, `fewshots`, `target_system`,
`generate_systems`, `generate_scenarios`, `n_systems`,
`n_scenarios_per_system` — taken once, at launch, and never re-read from the
experiment afterward. `Experiment` stays completely free to edit at any time,
including after it has runs (§9); this snapshot is what keeps an
already-launched run's prompt, and its export, truthful against a later edit.
Reading a run's config back validates it fail-closed (§5.3): a field a guard
depends on must be genuinely present and of the right shape, or the run
refuses to execute rather than guessing a default. A permissive reader here
was a real, since-closed leak — a malformed snapshot once let a
taxonomy-kind resource's text render into a Run A prompt anyway, because a
missing field silently read as "no file, so nothing to guard against."

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

**One task per repetition, run serially inside it — a considered decision,
not the original design.** Launching a batch creates `PipelineRun` + its
`PhaseRun` rows as `pending`, then enqueues exactly one django-q task per
`PipelineRun`. That single task drives every variant of that repetition end
to end, in order: Phase 2 for variant A, then Phase 3 labelling everything it
produced, then Phase 2 for variant B, then its own Phase 3 — one provider
call at a time, inside one worker process. Phase 3 does not fan out to a task
per scenario; it is a plain loop over the generation's scenarios, one call
per scenario, writing `completed_children`/`failed_children` as it goes so a
client polling mid-run still sees a live denominator.

An earlier design fanned Phase 2 out to one task per variant per repetition
and Phase 3 to one task per scenario, with a django-q group callback marking
the `PhaseRun` complete once every child finished. The researcher was shown
that trade-off directly and chose to keep the simpler, serial shape instead:

- **failure granularity.** The only thing the fan-out design bought was "a
  bad scenario fails only itself" — true, but the serial loop already gets
  that: one scenario's failure is recorded on its own `LLMCall` and the loop
  moves on, without needing a separate worker per scenario to achieve it;
- **timeout exposure.** A fanned-out design times each task individually — a
  tighter, easier number to reason about. The serial design's timeout
  (`Q_TIMEOUT`, default 3600s) has to cover every provider call in a
  repetition end to end, not just its slowest one; a shorter window kills the
  worker mid-run and strands the whole repetition. This is a real cost the
  researcher accepted, not an oversight;
- **scaling headroom.** Fan-out buys parallelism within one repetition; the
  serial design buys it across repetitions instead — every repetition in a
  batch is queued at once and they run concurrently as workers free up
  (`Q_WORKERS`, 2 by default), rather than strictly one after another. For a
  batch of several repetitions, the normal case here, that is most of the
  benefit fan-out would have offered, at a fraction of the moving parts: no
  group callbacks, no task-per-scenario bookkeeping, one failure mode to
  reason about per repetition instead of two.

A `PhaseRun` that finishes with any failed children is `failed` with a count,
and `POST /api/phase-runs/{id}/retry/` re-enqueues only the failures — for a
phase-3 (labelling) run; a phase-2 generation run is a single call, so there
is nothing partial to retry, only a whole rerun. Repetitions queued together
in a batch all run concurrently, not strictly in sequence: with 2 workers,
two repetitions finish together in about the time one takes alone, and each
further pair waits for a free worker. A batch launch is capped at 100
repetitions per call; at the default configuration (3 systems x 5 scenarios,
both variants, 32 provider calls) one repetition costs roughly $1.61 and
takes about 23 minutes.

The frontend polls run status; TanStack Query handles the polling interval and
cache invalidation.

Variant A and variant B run one after the other inside the same task, each
against its own, separately constructed context (§5.3) — never a shared task,
and no shared conversation state between them at any point in the call.

### Provider

All calls go to OpenRouter through the `openai` SDK with `base_url` set to
OpenRouter's endpoint, so every model is reachable through one adapter and
"try a different model for Phase 2" is a dropdown change. Structured output is
requested via `response_format` with the template's JSON Schema; the response is
then validated against a Pydantic model. A schema violation is recorded on the
`LLMCall` as `parse_error` rather than raising — malformed output is data.

The client sits behind a small interface with a `FakeLLMClient` implementation,
so the entire pipeline is testable with no network and no spend.

`complete()` also accepts an optional `preflight` callable, invoked on the
final, fully-built request dict immediately before the HTTP call — the seam
Phase 2's taxonomy choke point attaches to (§5.3).

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

The JSON Schema pinned into the request is pruned to match: whichever
top-level field a run's mode does not use (`scenarios` when generating fresh
systems, `system_descriptions` when targeting an existing one) is removed
from the schema itself, not only from this prose description of the expected
shape. A live run showed a model will fill in both fields whenever the schema
offers both places to; this is the fix upstream of parsing, not a rule
`_persist_error` (below) has to enforce after the fact.

`strict` is deliberately not pinned on the request — a provider enforcing
strict structured-output rules would reject this project's schemas outright,
failing every schema-pinned call — so the pruned schema narrows what the
model is *told* to do but cannot make the redundant shape structurally
impossible. If a response still carries a populated `system_descriptions`
alongside a non-empty top-level `scenarios`, the nested material is persisted
as authoritative and the redundant top-level list is recorded on the call as
`stray_scenarios` (§5.4) — a visible research signal, never silently dropped
and never treated as a parse failure.

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
GET    /api/auth/csrf/                        sets the CSRF cookie; call before
                                              any POST while unauthenticated
POST   /api/auth/login/                       GET /api/auth/me/
POST   /api/auth/logout/                      POST /api/auth/change-password/
GET    /api/settings/  PATCH /api/settings/   openrouter_key, staff only
                                              (write_only; masked on read)

GET    /api/taxonomies/                       GET /api/taxonomies/{id}/hcvs/
CRUD   /api/resources/                        upload + text extraction
CRUD   /api/fewshots/                         ?phase=2|3
CRUD   /api/system-descriptions/              ?origin=&variant=&run=
                                              &phase_run=   writes on
                                              seed/manual rows only
CRUD   /api/scenarios/                        ?origin=&system=&variant=
                                              &run=&phase_run=   writes on
                                              seed/manual rows only
GET/POST/PATCH /api/prompts/                  ?phase=  (PATCH creates a new
                                              version; DELETE is a plain 405,
                                              by design — a version is a
                                              provenance record)
POST   /api/prompts/{id}/activate/            staff; makes this version the
                                              active one for its (phase, name)
POST   /api/prompts/{id}/preview/             → exact rendered system+user
                                              strings, plus leak_scan_ran/
                                              guarded_by for a variant-A preview
GET    /api/models/                           OpenRouter model list, cached 1h

CRUD   /api/experiments/                      read: any authenticated;
                                              write: staff
GET    /api/experiments/{id}/progress/        {done, total, failed, percent} —
                                              the same shape already embedded
                                              on the list/detail serializer
POST   /api/experiments/{id}/run/             staff; {n_repetitions} → batch
                                              mode; 202 carries the cancel
                                              caveat below
GET    /api/experiments/{id}/export/          ?format=json|csv

GET    /api/pipeline-runs/                    one row per repetition; ?experiment=
GET    /api/pipeline-runs/{id}/               status, progress, own pinned config
DELETE /api/pipeline-runs/{id}/               staff; 409 while pending/queued/running
POST   /api/pipeline-runs/{id}/cancel/        staff; pending/queued/running →
                                              cancelled; does not interrupt a
                                              worker already executing it, only
                                              marks the run (and its open phase
                                              runs) dead so it can be deleted
GET    /api/pipeline-runs/{id}/export/        ?format=json|csv — one repetition,
                                              carrying that run's own config

GET    /api/phase-runs/                       ?pipeline_run=&phase=&variant=
                                              &status=&experiment=
POST   /api/phase-runs/                       staff; ad-hoc single phase →
                                              iterate mode (see below)
GET    /api/phase-runs/{id}/
DELETE /api/phase-runs/{id}/                  staff
POST   /api/phase-runs/{id}/retry/            staff; phase 3 (labelling) only
GET    /api/phase-runs/{id}/calls/            LLMCall inspector

GET    /api/labels/                           ?scenario=&phase_run=&hcv=&source=
```

Every list endpoint above returns a paginated envelope
(`{"count", "next", "previous", "results"}`, 50 rows per page), never a bare
array — a frontend data layer needs to unwrap `results` and follow
`next`/`previous`.

A 503 (no API key configured; the broker is unreachable) and a 409 (a
conflicting run state, a protected deletion) each carry a machine-readable
`code` alongside the human-readable `detail` — `missing_api_key` vs.
`broker_unavailable` for the two 503s; `run_in_flight`,
`run_not_cancellable`, `experiment_has_runs`, `experiment_has_adhoc_runs`,
`missing_source_phase_run`, and similarly-named codes for the 409s — so a
frontend can branch on `code` instead of pattern-matching the English
message.

**Iterate mode.** `POST /api/phase-runs/` runs a single phase, ad-hoc, outside
any `Experiment` or `PipelineRun` — `pipeline_run` stays null. It exists so a
prompt or a configuration can be tuned by actually running it, one call at a
time, without paying for a whole batch: the researcher's own framing is "if I
am in phase 2, I can rerun manually until I am satisfied." An ad-hoc run is
**deliberately never archived** — it has no `PipelineRun` to roll up under,
and the export bundles (below) are scoped to real, launched repetitions only,
so nothing about an iterate-mode call ever appears in either export. It may
optionally borrow an `Experiment`'s CURRENT, LIVE inputs (seed systems,
few-shots, target system, the two toggles) instead of bare built-in defaults —
read live, at execution time, never pinned or snapshotted, because the whole
point is iterating against the experiment as it stands right now, including
an edit made five minutes ago. `n_systems`/`n_scenarios_per_system` may
additionally be overridden per ad-hoc run (capped at 50, well above a real
experiment's usual 3x5) — the actual common case: real seeds and few-shots,
but one system and one scenario, cheap to tune against. Every isolation guard
(§5.3) still applies in full to a borrowed run exactly as it does to an
orchestrated one, since none of them read `pipeline_run` or a config
snapshot — and a borrowed experiment's taxonomy, seeds, and target system are
re-checked, live, against the same contamination and binding rules
`Experiment.clean()` enforces (§5.4), at the moment a worker actually
executes the run, since the experiment can be edited between when the ad-hoc
run was created and when a worker picks it up.

A repetition (`PipelineRun`) is never edited over the API — deletion is the
one exception, and it is refused while a worker may still hold the run
(QUEUED or RUNNING). `cancel` is the route out of a run stuck in either of
those states with no worker coming for it (a broker that accepted a task and
then died, say): it marks the run, and every phase run under it that is not
already terminal, CANCELLED, which is a genuinely terminal status (nothing
re-drives a cancelled run through the ordinary execution path). It does
**not** reach into a worker already executing — confirmed directly against a
running worker, not only reasoned about — so any write that worker is
mid-way through (an `LLMCall` save, a status roll-up) may still land after
`cancel` returns. Both `run`'s 202 body and `cancel`'s own response return
this exact caveat as their `detail` field, not only here in prose, so a
frontend can surface it automatically the moment a batch starts, rather than
only when an operator goes looking for a way to stop a stuck run.

`Experiment` stays completely unrestricted to edit, at any time, whether or
not it has runs — nothing freezes it. What keeps an already-launched run's
record truthful despite that is `PipelineRun.config`: a snapshot of the
seven fields that live only on `Experiment` (`seed_systems`, `fewshots`,
`target_system`, `generate_systems`, `generate_scenarios`, `n_systems`,
`n_scenarios_per_system`), pinned once at launch and read back at BOTH
render time and export time — never re-read from the experiment afterward.
Editing an experiment while a run sits QUEUED cannot change what that run
prompts a model with, nor what its export reports it prompted with. Deleting
an `Experiment` is refused (409) not only while it has any `PipelineRun`
recorded against it, but also while any ad-hoc iterate-mode `PhaseRun` is
still borrowing its inputs — deleting the experiment out from under a
still-queued ad-hoc run would otherwise leave that run rendering against an
experiment that no longer exists.

`POST /api/prompts/{id}/preview/` does **not** take an experiment reference.
It takes a free-standing bag of the same fields an experiment would have
(`variant`, `seed_systems`, `fewshots`, the two toggles, counts,
`target_system`, `scenario` — and, kept separate from `taxonomy` itself,
`guard_taxonomy`, which taxonomy to scan a variant-A preview against) and
renders exactly as a run would, through the same renderer. There is no "pick
an experiment, then preview" flow; assembling the same fields by hand (or
copying them from an experiment) is the whole interface. This must never
diverge from what a real run sends — both go through the same renderer.
Unlike a real run's guard taxonomy, which is always derived (§5.3),
`guard_taxonomy` here is a free choice: preview creates no `LLMCall` and
archives no isolation evidence, so there is nothing for a freely-chosen
value to falsify — an admin previewing is allowed to check a variant-A
render against a taxonomy other than the active one on purpose.

`PATCH` and `DELETE` on a system description or scenario with
`origin="generated"` return 403. Generated material is only removed by deleting
the run that produced it, via `DELETE /api/phase-runs/{id}/`, which cascades.

### Export contents

Two routes share one assembly. `GET /api/experiments/{id}/export/` covers
every repetition the experiment has ever run; `GET
/api/pipeline-runs/{id}/export/` covers exactly one. Both return everything
needed to reconstruct their scope without database access:

```
experiment_current   the experiment's CURRENT, live, editable configuration —
                      model ids, temperatures, toggles, counts, N. Named
                      "current" deliberately: it is NOT what any particular
                      run used, since Experiment stays freely editable even
                      after it has runs. What a run actually used is below.
taxonomy              every taxonomy actually pinned by a phase run in scope,
                      all 21 rows verbatim
prompts               each pinned template, at the version used
runs                  per repetition and variant: status, timings, tokens, cost
run_configs           experiment-level export only — one entry per
                      repetition, keyed by its PipelineRun id: the seed
                      systems (with their own expert scenario pack, resolved
                      text), few-shots (resolved text), target system, and
                      the generate/count toggles, exactly as PINNED at that
                      repetition's launch. Two repetitions of the same
                      experiment can carry genuinely different snapshots if
                      the experiment was edited between the two launches.
inputs                per-run export only — the ONE run's own entry from
                      run_configs above, unambiguous at this scope
systems               generated system descriptions
scenarios             card fields, narrative, origin, source run and variant
labels                hcv, evidence, rationale, source, and the call that
                      produced it
calls                 every LLMCall in full — both rendered prompts, the
                      complete request and response JSON, parse errors, and
                      the projected model, finish_reason, tokens, cost and
                      latency
```

There is no single, experiment-wide `inputs` section on the experiment-level
export: with N repetitions potentially pinning N different configurations,
no one section could represent "the" inputs without arbitrarily picking one
and silently losing the rest — `run_configs` carries each repetition's own
instead. Both the archived `rendered_system_prompt`/`rendered_user_prompt`
on every call and each run's own entry in `run_configs`/`inputs` are read
from what was PINNED at that run's launch, never from `experiment_current`
— the prompt a variant actually saw and the structured record of what it was
given always agree with each other, and an edit to the experiment after
launch can never retroactively change either.

`format=json` nests this as one bundle. `format=csv` emits a zip of flat
tables on the same content, with `calls.csv` carrying the prompt and
response columns in full rather than truncated — the export is an archival
record, so size is the wrong thing to optimise. Deferred metrics and the
offline ontology step both read this bundle, which is why it carries the
prompts and calls rather than only the labels.

Known limitation: there is no streaming. The whole bundle is assembled as one
in-memory dict, and `json.dumps`/the CSV zip writer each build a further full
copy of it on top — at the 100-repetition cap (§6), a two-variant experiment
with a modest scenario count is roughly 160MB resident for the bundle alone,
several times that at peak while a second copy is being built alongside the
first. This is a boundary to design around later (a background export job, or
a streaming writer), not a bug to silently hit.

`run_configs`/`inputs`, and every seed/target system and few-shot nested
inside them, are rebuilt into a fixed, canonical key order before they are
serialised, regardless of which database produced the row. PostgreSQL's
`jsonb` does not preserve an object's key insertion order on write (SQLite's
JSON1 storage does), so reading a snapshot back and handing it straight to
`json.dumps` would put two byte-identical exports of the same experiment in
different byte order depending on which database produced them. This only
reorders keys; it never changes a value, and the documented contents above
are unaffected.

## 10. Screens

Ten screens. The researcher's day is two loops: tune a prompt until the output is
worth running, then launch, watch, and read the result. The layout follows those
loops rather than the data model.

| Screen | Purpose |
|---|---|
| Login | email + password; fetches `GET /api/auth/csrf/` first; forced change on first login |
| Settings | staff only: OpenRouter key, masked, with which source is currently in effect |
| Dashboard | experiments and recent runs with status and progress |
| Resources | upload text files, view extracted text, pair them into few-shots |
| Taxonomy | the 21 HCVs, read-only, with version selector; the active taxonomy is marked |
| Corpus | system descriptions and scenarios; filter by origin, run and variant; seed and manual rows editable, generated rows read-only and marked as such |
| Prompt editor | Jinja2 editor, version history, per-variant preview, and the iterate panel below |
| Experiment builder | models, a phase-2 prompt **per variant**, phase-3 prompt, taxonomy, seeds, few-shots, the two toggles, counts, variants, repetitions |
| Run detail | live progress, cancel, retry, per-run export, the pinned config, isolation evidence, and the call inspector |
| Labels | per-variant scenario x HCV matrix, evidence and rationale on hover |

### Prompt editor and the iterate loop

Phase 2 has two independent templates (§5.3), so the editor edits one variant's
template at a time and says plainly which variant it is. Preview renders against
that variant's real context — meaning variant A's preview has no `taxonomy` in
scope at all, so a stray reference fails there rather than in a paid run.

A template is validated against its own variant before it can be saved. This is
not a nicety: templates render under `StrictUndefined`, so a mistake in the A
template fails only the A run while B succeeds, which is the most confusing
symptom the system can produce.

The iterate panel sits beneath the editor and drives `POST /api/phase-runs/`:
pick an experiment to borrow inputs from, override the counts down to one system
and one scenario, run, and read the output beside the template that produced it.
The panel must state that these runs are scratch — they are never archived and
never appear in an export bundle (§9).

### Run detail

Beyond live progress and the call inspector, this screen carries the things that
make a run interpretable after the fact: the configuration **pinned** to that
repetition (not the experiment's current configuration, which may have moved
on), and the isolation evidence — `leak_scan_ran` and `guarded_by` per call — so
that variant A can be confirmed clean without leaving the page. Cancel is
available while a run is pending, queued or running, and the screen must repeat
the caveat that cancelling marks a run dead but does not interrupt a worker
already mid-call.

The call inspector lists every call with the full rendered prompt beside the
verbatim response, copyable, never truncated.

### Labels

Variants A and B generate **different** scenarios, so there is no row-by-row
comparison to draw; the screen shows one scenario x HCV matrix per variant, with
evidence and rationale on hover. Reading the two side by side is how coverage is
compared. No statistics are computed — metrics are deferred (§11), and a
difference at low repetition counts is not yet a finding.

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

pytest 9.1.1 / pytest-django 4.14.0, TDD throughout; ruff 0.16.6 lints (and
formats) everything outside the generated migrations. `FakeLLMClient` covers
the pipeline end to end without network access. The tests that matter most:

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

These eight are the tests that matter most, not the only tests. The suite
covers substantially more ground across every app: a meta-test
(`test_adhoc_rule_parity.py`) that fails the build if a validation rule
added to `Experiment.clean()` is not also reachable from the ad-hoc
iterate-mode path; end-to-end coverage of iterate mode itself; the
`leak_scan_ran`/`guarded_by` isolation-evidence fields; the `run_experiment`
management command; and admin-layer tests in every app.
