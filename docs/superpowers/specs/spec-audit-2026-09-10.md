# Spec Audit — HCV Pipeline Design vs. Built Backend

**Date:** 2026-09-10
**Spec audited:** `docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md`
**Code audited:** `backend/{pipeline,prompts,corpus,taxonomy,llm,core,config}`
**Method:** read-only comparison, section by section. No files changed, no commands that mutate state, no provider calls.

Legend: **CONTRADICTION** = spec says X, code does Y. **OMISSION** = code has X, spec silent. **STALE** = spec described something that was true and no longer is (often because a later plan/review round changed it — many of these are attested in the code's own comments, which is why quotes from the code are so exhaustive below).

---

## 0. Summary

This backend is **meaningfully ahead of, and in three places structurally different from**, the spec. The single biggest fact for frontend design: **`docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md` §10 has no screen, anywhere, for iterate mode** — a whole feature surface (`POST /api/phase-runs/`, ad-hoc single-phase runs) that the task brief already flagged as known-stale. The second biggest: **the Corpus screen's documented `?run=`/`?variant=` filters do not exist on the API** — only `?origin=` (both) and `?system=` (scenarios only) are implemented. The third: **the execution model in §6 is not what got built** — there is no per-scenario task fan-out and no django-q group callback; a whole repetition runs inside one task, serially, and the codebase's own settings.py comment says so in as many words.

Counts (this report's findings list, not an exhaustive line-count): **11 CONTRADICTIONS**, **29 OMISSIONS**, **6 STALE**.

---

## 1. §5 Data model

### 1.1 `taxonomy` (spec §5.1, lines 101–116)

- **OMISSION** — `Taxonomy.Meta.constraints` adds `only_one_active_taxonomy`, a partial `UniqueConstraint` on `is_active=True` (`taxonomy/models.py:67-78`), plus `unique_taxonomy_version` on `(vocabulary_id, version)` (`models.py:43-46`) and `ordering = ["vocabulary_id", "version"]` (`models.py:80`). Spec's model block (lines 104-107) shows only the field list and a prose `unique_together (taxonomy, hcv_id)` for `HCV` — it says nothing about a taxonomy-level uniqueness constraint or the single-active invariant at all. This is the "single-active-taxonomy database constraint" flagged in the task brief — confirmed present, confirmed undocumented.
- **OMISSION** — `Taxonomy.active()` now fails closed via a new `MultipleActiveTaxonomies` exception (`taxonomy/models.py:5-28, 85-110`) if more than one row is somehow flagged active. The exception's docstring explicitly narrates a real leak this closed (a decoy second "active" taxonomy silently re-binding every unborrowed Run A's guard taxonomy). Spec has no concept of `Taxonomy.active()` as a structural notion at all.
- **OMISSION** — `HCV.Meta` adds `ordering = ["order", "hcv_id"]` and a `has_condition_class` property (`taxonomy/models.py:124-137`); spec doesn't mention either.

### 1.2 `corpus` (spec §5.2, lines 117-171)

- **OMISSION** — `Scenario.Meta.constraints` adds two `UniqueConstraint`s not in spec: `unique_scenario_id_per_run` (scoped to `system_description, generation_run, scenario_id`) and a second, conditional one, `unique_manual_scenario_id_per_system` (same fields minus `generation_run`, `condition=Q(generation_run__isnull=True)`) — `corpus/models.py:127-148`. The code's own comment explains why both are needed (NULLs compare distinct, so the first constraint alone doesn't protect manual/seed rows). Spec's `Scenario` block (lines 126-135) lists no constraints.
- **OMISSION** — `ResourceFile.Meta.ordering = ["name"]`, `SystemDescription.Meta.ordering = ["title"]`, `FewShotExample.Meta.ordering = ["phase", "order", "name"]` and its `unique_fewshot_name` constraint (`corpus/models.py`) — none are in spec's field lists.
- **OMISSION** — `OriginMixin.is_editable` property (`corpus/models.py:52-54`) and `origin` field's `db_index=True` — implementation detail, not mentioned, harmless, noted for completeness.
- Everything else in this section (the `origin` choices, the `FewShotExample` exactly-one-of validator, the "generated rows immutable" policy, `abox` as a nullable JSON field) **matches the spec closely** — this subsection held up well.

### 1.3 `prompts` (spec §5.3, lines 173-234)

This subsection is the best-aligned part of the whole spec — `prompts/rendering.py`'s module docstring *literally cites the spec by path and section* ("See the spec (`docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md`, §5.3)..." — `prompts/rendering.py:65`). No contradictions found in the rendering/isolation model described in lines 189-234.

- **OMISSION** — `PromptTemplate.Meta.ordering = ["phase", "name", "-version"]` (`prompts/models.py:43`) — not in spec.
- **OMISSION (significant — the taxonomy choke point named in the brief)** — the spec's isolation story (lines 210-234) stops at the render-time backstop (`assert_no_taxonomy_leak`, scanning the two rendered strings + `output_schema`). The code adds a **second, independent layer**: `assert_request_not_leaking_taxonomy` (`prompts/rendering.py:306-404`), wired in as the LLM client's `preflight` callable (`pipeline/generation.py:66-98`, `llm/client.py:104,220-221`). It walks the *final, fully-serialized request dict* — not the two prompt strings — immediately before the HTTP call, specifically so a field added to the request body *after* rendering (the module docstring's example: `output_schema` copied straight into the body by a future refactor) is still caught. This is the "taxonomy choke point" (`assert_request_not_leaking_taxonomy`) called out in the task brief as possibly unmentioned — confirmed: §5.3/§5.4/§6 say nothing about it.
- **OMISSION** — `LLMCall.leak_scan_ran` (bool) and `LLMCall.guarded_by` (FK to `Taxonomy`) are new fields (`pipeline/models.py:841-848`) recording whether the backstop(s) actually ran and against which taxonomy — archived evidence for the isolation guarantee. Spec's `LLMCall` field list (lines 264-273) does not include either field.

### 1.4 `pipeline` (spec §5.4, lines 236-318)

- **OMISSION** — `PipelineRun.config` (a `JSONField`, `pipeline/models.py:672`) does not appear in spec's `PipelineRun` definition at line 252 (`PipelineRun(experiment→, repetition_index, status, started_at, finished_at)`) even though §9 (lines 480-487) discusses `PipelineRun.config` at length as if it already exists in the data model. The spec is internally inconsistent here — §9 assumes a field §5.4 never lists.
- **OMISSION** — `PhaseRun` gained fields not in spec's block (lines 254-262): `experiment` FK (iterate mode's "borrow a live experiment" — `pipeline/models.py:740-746`), `n_systems`/`n_scenarios_per_system` per-run overrides (`models.py:747-755`), and the progress triad `total_children`/`completed_children`/`failed_children` (`models.py:758-760`). The `PipelineRun.Status`/`PhaseRun.Status` enums both include `CANCELLED` (`models.py:658, 702`) — the task brief asked to confirm this; confirmed present on **both** models, though spec's prose for `PhaseRun.status` (line 261) already lists `cancelled`, while spec's `PipelineRun` block names no status values at all, so there's nothing there to literally contradict — just nothing to confirm against either.
- **OMISSION** — `LLMCall` gained `unknown_hcv_ids` (JSON list) and `stray_scenarios` (JSON list) (`models.py:856, 868`), plus `leak_scan_ran`/`guarded_by` (§1.3 above). `stray_scenarios` is the field named in the task brief; confirmed present, with a detailed docstring about a real production incident (a model filling in both `system_descriptions` and a redundant top-level `scenarios`). Spec's `LLMCall` block (lines 264-273) lists neither.
- **OMISSION** — `Label.llm_call` FK (`models.py:930-936`) — the model's own comment says plainly: *"Spec §9 requires the export to name it... Nullable for a human label, which has no call."* Spec's `Label` block (line 276-279) does not list it, even though §9's export-contents table (line 526) promises "labels ... and the call that produced it" — so this is a field the export section *anticipates* but the data-model section never added.
- **OMISSION** — `Label.Meta.ordering = ["scenario", "hcv", "id"]` with the `id` tiebreaker (`models.py:941-950`) — the task brief asked to confirm this; confirmed, with the model's comment explaining why (pagination stability across ties). Not in spec.
- **OMISSION** — A large family of shared validation helpers now exists at module level in `pipeline/models.py` (`template_phase_mismatch_error`, `taxonomy_missing_rows_error`, `generated_seed_systems_error`, `generated_target_system_error`, `generation_toggles_off_error`, `missing_target_for_no_generation_error`, `generation_config_errors`, `unbound_guard_taxonomy_error`) — these exist specifically because the ad-hoc/iterate-mode path kept drifting out of sync with `Experiment.clean()`'s rules across review rounds (see `generation_config_errors`' docstring, `models.py:254-336`, which names a dedicated parity test, `pipeline/tests/test_adhoc_rule_parity.py`). Spec describes the *validators'* effect in prose (lines 282-290) correctly, but has no notion of this shared-checker architecture or of the ad-hoc path needing to reach it at all.

---

## 2. §6 Execution

**This is the report's most consequential contradiction.**

> Spec, lines 323-328: *"Launching a batch creates `PipelineRun` + its `PhaseRun` rows as `pending`, then enqueues one task per phase run. **Phase 2** is one task per variant per repetition. **Phase 3** fans out to **one task per scenario**, so labelling parallelises across workers and a single failed scenario does not fail the run. A group callback marks the `PhaseRun` complete when all children finish."*

- **CONTRADICTION** — `pipeline/orchestration.py:145-180` (`queue_runs`) enqueues exactly **one django-q task per `PipelineRun`** (`RUN_TASK = "pipeline.tasks.run_pipeline_task"`, `orchestration.py:24`), not one per phase run. That single task runs `execute_pipeline_run` (`orchestration.py:228-312`), which loops over every variant **inline, synchronously, in the same worker process**, calling `run_phase2(generation)` then `run_labelling(labelling)` for each variant in turn.
- **CONTRADICTION** — `run_labelling` (`pipeline/orchestration.py:368-478`) does **not** fan out to one django-q task per scenario. It is a plain Python `for sequence, scenario in enumerate(scenarios, start=1):` loop (`orchestration.py:464-474`) calling `_label_scenario_or_archive_failure` synchronously. There is **no `async_task` call per scenario anywhere**, and **no django-q group/group-callback** in the codebase at all — `total_children`/`completed_children`/`failed_children` are updated by this same loop writing to the database as it goes, which is what makes polling still work, but it is not the worker-fan-out model the spec describes.
- **CONTRADICTION confirmed in the code's own comment** — `backend/config/settings.py:115-124`:
  ```
  # Django-Q2. The pipeline fans Phase 3 out to one task per scenario, so a
  # single bad scenario fails only itself.
  Q_CLUSTER = {
      ...
      # A repetition currently runs serially inside ONE task: every variant's
      # generation call, then one labelling call per scenario it produced. The
      # timeout has to cover every provider call in that repetition end to end,
      # not just the slowest one — a shorter window kills the worker mid-run and
      # strands the repetition.
      "timeout": config("Q_TIMEOUT", default=3600, cast=int),
  ```
  The comment directly above `Q_CLUSTER` repeats the spec's old claim ("fans Phase 3 out to one task per scenario") and the comment *inside* `Q_CLUSTER`, two lines later, says the opposite and current truth ("A repetition currently runs serially inside ONE task"). The settings file itself is mid-drift from the old design to the new one — strong independent confirmation this is a real, acknowledged architecture change, not a misreading on this audit's part.
- Separately, `run_phase2_task`/`run_phase3_task` (`pipeline/tasks.py:34-45`) **do** exist as real, separate django-q entry points — but they are used only by **iterate mode** (`PhaseRunViewSet.create` → `queue_phase_run` → `PHASE_RUN_TASKS[phase]`, `orchestration.py:201-225`), for a single ad-hoc `PhaseRun` with no `pipeline_run`. They are not used by the batch/repetition path at all.
- **Practical effect for frontend work:** the *progress API shape* (`PhaseRun.progress` → `{done, total, failed, percent}`, `pipeline/models.py:13-20`) still updates live and is still safe to poll — so a status-polling UI built against §6's *API contract* will work. What's wrong is any assumption that phase 3 scenarios are processed by independent workers that can be individually retried mid-flight, or that a "group callback" is a real mechanism to reason about (it isn't; it doesn't exist).

- **OMISSION** — The "Provider"/"API key" subsections (lines 337-380) match the code closely (`core/keys.py`, `llm/client.py`) — no contradictions there. One addition: `llm/client.py`'s `preflight` parameter (§1.3 above) is the mechanism that makes the taxonomy choke point possible; spec's Provider section doesn't mention it.
- **OMISSION** — Repetition queueing is capped at `n_repetitions <= 100` (`RunRequestSerializer`, `pipeline/serializers.py:168-193`), and the serializer's `help_text` documents a concrete cost/time model not in spec: **~$1.61 and ~23 minutes per repetition** at the default configuration (3 systems × 5 scenarios, both variants, 32 calls), with **2 workers by default** (`Q_CLUSTER["workers"]`, `config/settings.py:119`) so repetitions overlap in pairs rather than running strictly sequentially. This is exactly the kind of number an Experiment-builder screen's "estimated cost" UI would want, and spec has nothing like it.

---

## 3. §8 Output schemas

Spec (lines 393-419) is **largely accurate**: `llm/schemas.py`'s `Phase2Response` (`system_descriptions`/`scenarios`) and `Phase3Response` (`labels`) match the documented JSON shapes, and `split_known_labels` matches "an `hcv_id` outside the pinned taxonomy is a schema error recorded on the `LLMCall`, never silently dropped."

- **OMISSION** — `phase2_json_schema(*, generate_systems)` (`llm/schemas.py:53-78`) is mode-specific in a way spec doesn't describe: it **pops the unused top-level field** (`scenarios` when generating fresh systems, `system_descriptions` when targeting an existing one) out of the JSON Schema sent to the model, specifically because a live run proved a model will fill in both fields if the schema offers both places. Spec's text ("With `generate_systems` false, the response is a bare `{"scenarios": [...]}`... With `generate_scenarios` false, `scenarios` is omitted") describes the *intended* response shape correctly but says nothing about the schema itself being pruned to make that the *only* structurally offered shape.
- **OMISSION** — the `_redundant_top_level_scenarios`/`stray_scenarios` handling in `pipeline/generation.py:124-153, 453-455` is the documented safety net for exactly the case above (model fills in both fields anyway, since `strict` is deliberately not pinned on the schema — `llm/client.py:141-156`). Already flagged as a known-unmentioned field in §1.4; restated here because it's really an §8 concern (response validation), not just a data-model one.

---

## 4. §9 API

### 4.1 Full route inventory vs. spec's table (lines 425-464)

Registered via `config/urls.py` + each app's router:

| Route | Method(s) | Permission | In spec? |
|---|---|---|---|
| `/api/health/` | GET | AllowAny | not in spec |
| `/api/auth/csrf/` | GET | AllowAny | **not in spec** |
| `/api/auth/login/` | POST | AllowAny | yes |
| `/api/auth/logout/` | POST | any authenticated | yes |
| `/api/auth/me/` | GET | any authenticated | yes |
| `/api/auth/change-password/` | POST | any authenticated | yes |
| `/api/settings/` | GET, PATCH | `IsAdminUser` (staff) | yes |
| `/api/taxonomies/` | GET | any authenticated | yes |
| `/api/taxonomies/{id}/hcvs/` | GET | any authenticated | yes |
| `/api/resources/` | full CRUD | any authenticated (no staff gate) | yes |
| `/api/fewshots/` | full CRUD, `?phase=` | any authenticated (no staff gate) | yes |
| `/api/system-descriptions/` | CRUD, `?origin=` only | any authenticated + `ReadOnlyIfGenerated` | `?origin=&run=` claimed |
| `/api/scenarios/` | CRUD, `?origin=&system=` only | any authenticated + `ReadOnlyIfGenerated` | `?origin=&run=&variant=` claimed |
| `/api/prompts/` | **GET/POST/PATCH only — no DELETE (405)** | `IsAuthenticated` + `StaffWrites` | labelled "CRUD" in spec |
| `/api/prompts/{id}/activate/` | POST | staff | **not in spec** |
| `/api/prompts/{id}/preview/` | POST | staff (any authenticated to read, preview is a POST so staff) | yes, but see note below |
| `/api/models/` | GET | any authenticated | yes |
| `/api/experiments/` | full CRUD | read: any authenticated; write: staff | yes |
| `/api/experiments/{id}/run/` | POST | staff | yes |
| `/api/experiments/{id}/progress/` | GET | any authenticated | **not in spec** |
| `/api/experiments/{id}/export/` | GET, `?format=` | any authenticated | yes |
| `/api/pipeline-runs/` | GET (list/retrieve), DELETE | read: any authenticated; DELETE: staff | yes |
| `/api/pipeline-runs/{id}/cancel/` | POST | staff | yes (named in prompt) |
| `/api/pipeline-runs/{id}/export/` | GET, `?format=` | any authenticated | yes |
| `/api/phase-runs/` | GET (list), POST (create = iterate mode) | read: any authenticated; create: staff | yes |
| `/api/phase-runs/{id}/` | GET, DELETE | read: any authenticated; DELETE: staff | yes |
| `/api/phase-runs/{id}/calls/` | GET | any authenticated | yes |
| `/api/phase-runs/{id}/retry/` | POST | staff | yes |
| `/api/labels/` | GET, `?scenario=&phase_run=&hcv=&source=` | any authenticated | `?scenario=&phase_run=` claimed (hcv/source undocumented extras) |

Also note every `@action` route additionally gets a DRF format-suffix sibling (e.g. `.../run.json`, `.../cancel.json`) — harmless, but worth knowing since multiple view docstrings call out having to add `format=None` to every action signature specifically to stop that route from 500ing.

### 4.2 Findings

- **OMISSION** — `GET /api/auth/csrf/` (`core/urls.py:6`, `core/views.py:18-23`) is new and required: it's decorated `@method_decorator(ensure_csrf_cookie, name="get")`, i.e. a frontend must `GET` it before it can `POST` to `/api/auth/login/` (or any other unsafe method) under Django's session+CSRF auth, per spec §7 ("Session authentication with CSRF"). Spec's API table has no mention of it at all — a Login screen built strictly from §9/§10 would be missing a required first call.
- **CONTRADICTION** — `/api/system-descriptions/` supports **only** `?origin=` (`corpus/views.py:65-68`); `/api/scenarios/` supports **only** `?origin=` and an *undocumented* `?system=` (system-description id) filter (`corpus/views.py:91-99`). Spec explicitly documents `?origin=&run=` for system-descriptions and `?origin=&run=&variant=` for scenarios (lines 434-436). **There is no `?run=` filter and no `?variant=` filter anywhere in `corpus/views.py`.** A Corpus screen built to spec's filter contract (filter scenarios/systems by which run or which variant produced them) cannot be built against the API as it exists today — the frontend would have to fetch everything and filter client-side using each row's `generation_run`/the run's `variant`, or the backend needs new filters added.
- **CONTRADICTION (labelling, not a route)** — spec's table header literally reads `CRUD /api/prompts/` (line 437). The viewset (`prompts/views.py:29-45`) deliberately omits `DestroyModelMixin` — `DELETE /api/prompts/{id}/` is a plain 405, by design ("a template version is a provenance record... deleting one is unrecoverable"). "CRUD" is the wrong word for what was built; Create/Read/Update only.
- **OMISSION** — `POST /api/prompts/{id}/activate/` (`prompts/views.py:85-109`) is entirely new: makes one version "the active one" for its `(phase, name)`, deactivating siblings atomically. Not in spec at all — a Prompt Editor screen needs an "activate this version" affordance that §10 doesn't describe.
- **OMISSION** — `GET /api/experiments/{id}/progress/` (`pipeline/views.py:306-309`) is new, returning the same `{done, total, failed, percent}` shape as the `progress` field already embedded in `ExperimentSerializer`/`PipelineRunSerializer`/`PhaseRunSerializer`. Redundant with the embedded field in practice, but it exists and is undocumented.
- **STALE** — spec's `POST /api/prompts/{id}/preview/` description (line 488) says it "renders against **a chosen experiment configuration**." The actual `PreviewSerializer` (`prompts/serializers.py:39-65`) takes no experiment reference at all — it takes a free-standing parameter bag (`variant`, `taxonomy`, `guard_taxonomy`, `seed_systems` ids, `fewshots` ids, the two toggles, counts, `target_system`, `scenario`) that merely *mirrors* `Experiment`'s shape. A Prompt Editor screen built expecting "pick an experiment from a dropdown, then preview" would be built against a UX the API doesn't support; it's "assemble the same fields an experiment would have, by hand (or by copying them from one), and preview those."
- **OMISSION** — `?hcv=` (accepts either the numeric PK or the human `hcv_id` string like `"HCV-07"`) and `?source=` filters on `/api/labels/` (`pipeline/views.py:822-844`) are not in spec's `?scenario=&phase_run=` (line 463).
- **CONTRADICTION, permissions not named in §7** — spec §7 (lines 390-391) names exactly five staff-gated surfaces: *"the OpenRouter key, taxonomy import, prompt editing, experiment creation and run launching."* The actual staff gate (`StaffWrites`, `core/permissions.py`) is also applied to: `PipelineRunViewSet` (DELETE, `cancel`), `PhaseRunViewSet` (`create` = iterate mode, DELETE, `retry`), and prompt `activate`. Cancelling/deleting runs and iterate-mode creation are real, separate staff-only actions the spec's enumeration omits — a frontend that shows/hides buttons by role needs to know this and can't derive it from §7 alone.
- **OMISSION** — `ExperimentViewSet.perform_destroy` (`pipeline/views.py:209-253`) refuses deletion (409) not only when the experiment "has runs" (spec implies this via the `PipelineRun` deletion-first requirement) but **also** when it has any ad-hoc iterate-mode `PhaseRun`s borrowing it (`PhaseRun.experiment`, `on_delete=PROTECT`) — naming the exact ids in the error. Spec's deletion story (lines 493-495) only discusses `generated`-row immutability and run cascade; it has no concept of an experiment being "borrowed" by iterate mode at all, so this whole failure mode is unanticipated.
- **OMISSION** — Every list endpoint is paginated (`PageNumberPagination`, `PAGE_SIZE=50`, `config/settings.py:102-104`) — responses are `{"count","next","previous","results"}` envelopes, not bare arrays. Spec's API section never states this; any frontend data-fetching layer needs to know to unwrap `results` and handle `next`/`previous`.
- **OMISSION** — `ExperimentViewSet.run`'s 202 response body and `PipelineRunViewSet.cancel`'s response both carry a `detail` field with the verbatim `CANCEL_CAVEAT` string (`pipeline/views.py:44-49`) explaining that cancel does not interrupt an in-flight worker. Spec's prose (lines 466-476) describes this caveat accurately but doesn't note it's *returned in the API response itself*, which matters for a frontend deciding whether to surface it as a toast/banner automatically.
- **OMISSION** — 503 responses for a missing API key or a dead broker, and 409s for conflicting run states, carry a machine-readable `code` field alongside `detail` (`MissingAPIKeyError`/`BrokerUnavailableError`/`Conflict`, `pipeline/views.py:67-102, 139-160`) specifically so a frontend can branch on `code` instead of pattern-matching English text. Spec doesn't mention this error-shape contract at all; worth documenting for the frontend's error-handling layer.

### 4.3 Export contents (§9, lines 497-550)

**This part of the spec is accurate.** `pipeline/export.py`'s `build_bundle`/`build_run_bundle` emit exactly the section names spec promises:
- `build_bundle`: `experiment_current, taxonomy, prompts, runs, run_configs, systems, scenarios, labels, calls` — matches spec's experiment-level list exactly, including the `run_configs` (not a singular `inputs`) framing.
- `build_run_bundle`: `experiment_current, taxonomy, prompts, inputs, runs, systems, scenarios, labels, calls` — matches spec's run-level list exactly, including `inputs` replacing `run_configs`.

No contradictions found here. One nuance not in spec: `pipeline/export.py` has a documented **known size limitation** (no streaming; ~160MB resident at the serializer's own max of 100 repetitions) and a **JSON key-order canonicalization step** (`_canonical_run_config`/`_ordered`) needed purely because PostgreSQL's `jsonb` doesn't preserve key insertion order the way SQLite's JSON1 does — neither is mentioned in spec, and neither changes the *documented* contents, just implementation risk worth knowing about.

---

## 5. §10 Screens

Going screen by screen (spec lines 554-565):

1. **Login** — OMISSION: needs the new `GET /api/auth/csrf/` call before the POST (§4.2).
2. **Settings** — matches; no findings beyond the stack-level ones already noted.
3. **Dashboard** ("experiments and recent runs with status") — OMISSION: doesn't mention the `progress` shape (`{done,total,failed,percent}`) now embedded on both `Experiment` and `PipelineRun`, doesn't mention cancellation as a dashboard-level action, and doesn't mention ad-hoc/iterate-mode `PhaseRun`s at all — which have no natural home on this screen as described (they have no `PipelineRun`/repetition to sit under).
4. **Resources** — matches.
5. **Taxonomy** ("read-only, with version selector") — matches the read-only API surface; note `is_active` can only be flipped via Django admin (no API write route at all for it — `TaxonomyViewSet` is fully read-only), consistent with "read-only" but worth the frontend team knowing there's no in-app way to switch the active taxonomy.
6. **Corpus** — **CONTRADICTION** (§4.2 above, repeated here because it is squarely this screen's problem): the documented `?run=`/`?variant=` filters don't exist on either `/api/system-descriptions/` or `/api/scenarios/`. This is the screen most directly broken by that gap.
7. **Prompt editor** — OMISSION: no mention of the `activate` action (§4.2) — version history without a way to mark one active is an incomplete picture of this screen's needs. Also OMISSION: because §5.4/§5.3 now split phase 2 into `phase2_prompt_a`/`phase2_prompt_b`, a "Jinja2 editor" screen conceptually needs to let an operator author/browse *two independent template families* for phase 2 (one per variant) plus one shared phase-3 family — not "a" prompt editor for "the" phase-2 template. This is the already-known-stale item from the task brief, confirmed structurally (see §1.3/§1.4): `Experiment.phase2_prompt_for(variant)` (`pipeline/models.py:483-495`) explicitly maps variant A → `phase2_prompt_a`, B → `phase2_prompt_b`, with **no shared core and no drift warning between them** (a deliberate 2026-09-08 decision recorded in both `pipeline/models.py` and `prompts/rendering.py`'s docstrings).
8. **Experiment builder** ("models, prompts, taxonomy, seeds, few-shots, the two toggles, counts, variants, N") — CONTRADICTION (restated from #7): "prompts" (singular) should be "two independent phase-2 prompts (one per variant) plus one phase-3 prompt." OMISSION: no mention of the per-repetition cost/time estimate (~$1.61/~23min, `RunRequestSerializer` help text) or the `n_repetitions` cap of 100 — both are exactly the kind of guidance this screen's "N" control should surface.
9. **Run detail** — the single most under-described screen relative to what's built:
   - **OMISSION**: no mention of **cancellation** (`POST /api/pipeline-runs/{id}/cancel/`) at all, despite the task brief calling this out as a known backend feature. A run-detail screen's status/actions area needs a Cancel button with the "does not interrupt an in-flight worker" caveat surfaced (the API literally hands the frontend this string — §4.2).
   - **OMISSION**: no mention of **per-run export** (`GET /api/pipeline-runs/{id}/export/`) — the screen as described has "retry" and a call inspector, but no export affordance, even though the spec's own §9 documents this route in detail.
   - **OMISSION**: no mention of the run's own pinned **config snapshot** (`PipelineRun.config`) being visible here — per the 2026-09-09 user decision quoted in `pipeline/models.py:663-671` ("even if these config data change, browsing an older run should show its previous config"), and surfaced deliberately in `PipelineRunSerializer` for exactly this reason, not just via `/export/`.
   - **OMISSION**: no mention of `leak_scan_ran`/`guarded_by` (the per-phase-run isolation evidence, aggregated by `PhaseRunSerializer.get_leak_scan_ran`/`get_guarded_by`, `pipeline/serializers.py:254-291`) — given the spec's own emphasis elsewhere that "the evidence belongs in the archive next to the material it vouches for" (rendering.py docstring), a Run Detail screen that doesn't show this is arguably failing the spec's own stated design philosophy, not just an omission of a minor field.
   - **OMISSION**: retry (`POST /api/phase-runs/{id}/retry/`) only applies to phase-3 (labelling) runs — a phase-2 generation run has no retry at all (it's a single call; failure just means re-launching). The screen description's bare "retry" doesn't scope this.
10. **Labels** ("scenario × HCV matrix with evidence and rationale on hover") — OMISSION: doesn't mention that each label now carries a `llm_call` reference (§1.4) — a natural deep-link from the matrix into the call inspector that the data model supports but the screen description doesn't anticipate.

- **OMISSION, whole-screen-level, the single biggest frontend gap**: **no screen in §10 covers iterate mode at all.** `POST /api/phase-runs/` (ad-hoc single-phase runs, deliberately un-archived, optionally borrowing a live experiment's seeds/few-shots/toggles with per-run `n_systems`/`n_scenarios_per_system` overrides), its list/detail/calls/retry/delete routes, and its distinct un-staff-gated-vs-staff-gated split are a first-class, heavily-tested feature (`pipeline/tests/test_iterate_mode.py`, `test_adhoc_rule_parity.py`) with no corresponding screen, panel, or even a passing mention anywhere in §10. This was flagged as a known-stale area in the task brief and is confirmed here as a structural gap, not a minor one — it's the researcher's own stated primary workflow ("if I am in phase 2, I can rerun manually until I am satisfied," quoted in `pipeline/execution_config.py:259-262`).

---

## 6. §12 Testing and §3 Stack

### 6.1 §3 Stack (lines 45-71)

Compared against `backend/pyproject.toml`:

| Package | Spec version | `pyproject.toml` | Match? |
|---|---|---|---|
| Python | 3.13 | `requires-python = ">=3.13"` | yes |
| django | 5.2.17 | `django==5.2.17` | yes |
| djangorestframework | 3.18.0 | `djangorestframework==3.18.0` | yes |
| django-q2 | 1.11.1 | `django-q2==1.11.1` | yes |
| redis / django-redis | 8.1.0 / 7.0.0 | `redis==8.1.0`, `django-redis==7.0.0` | yes |
| openai | 3.8.0 | `openai==3.8.0` | yes |
| jinja2 | 3.1.6 | `jinja2==3.1.6` | yes |
| pydantic | 2.13.5 | `pydantic==2.13.5` | yes |
| gunicorn / whitenoise | 26.2.0 / 6.12.0 | `gunicorn==26.2.0`, `whitenoise==6.12.0` | yes |

All Python-side pinned versions in spec's table **match exactly** — no drift found there.

- **OMISSION** — `pyproject.toml` lists four dependencies not in spec's table at all: `django-cors-headers==4.9.0`, `psycopg[binary]==3.3.5`, `python-decouple==3.8`, `dj-database-url==3.1.2`. All four are load-bearing (`config/settings.py` uses `corsheaders` middleware, `dj_database_url`/`decouple` for all environment-driven config, `psycopg` as the Postgres driver) — not optional extras.
- **OMISSION** — dev/test tooling pins aren't in spec's table either (spec's table is frontend+backend runtime only): `pytest==9.1.1`, `pytest-django==4.14.0`, `ruff==0.16.6`. Spec §12 says "pytest-django, TDD throughout" with no version claim, so this is an omission, not a contradiction — flagging since the task explicitly asked to check stated versions, and these ones are simply absent rather than wrong.
- **STALE** — spec §4's repository layout (line 91) groups "llm client" under the `pipeline/` bullet (`pipeline/ Experiment, PipelineRun, PhaseRun, LLMCall, Label, tasks, llm client`). The LLM client actually lives in its **own top-level package**, `backend/llm/` (`client.py`, `fake.py`, `schemas.py`, `models_api.py`), not inside `pipeline/`. (Note `llm` is also not in `INSTALLED_APPS` — it's a plain package, correctly, since it has no models — but its *location* in the tree doesn't match §4's description.)
- **STALE / not-yet-true** — spec §4 and §3 both describe a `frontend/` tree (`src/{pages,components,contexts,api}`) and an npm stack (react, vite, react-router-dom, tailwindcss, @tanstack/react-query, axios). **No `frontend/` directory and no `package.json` exist anywhere in the repository.** This is expected — the audit's own premise is "before I write the frontend against it" — but it means §3's npm version table is entirely unverifiable against real code today; it's aspirational, not descriptive, and should probably be labelled as such (or moved) in any spec rewrite.

### 6.2 §12 Testing (lines 580-609)

Every one of spec's eight named "tests that matter most" bullets has a real, identifiable counterpart in the test suite (variant isolation → `prompts/tests/test_isolation.py` + `pipeline/tests/test_choke_point.py`; generated-material immutability → `corpus/tests/test_corpus_api.py`; call-record completeness → `pipeline/tests/test_export.py`; no-key-leakage → `pipeline/tests/test_redaction.py`; key precedence → `core/tests/test_keys.py`; prompt-version pinning → `pipeline/tests/test_lifecycle.py`/`test_orchestration.py`; schema-failure handling → `llm/tests/test_schemas.py`; toggle validation → `pipeline/tests/test_models.py`; retry semantics → `pipeline/tests/test_orchestration.py`). No contradictions found in §12's claims.

- **OMISSION** — the suite (46 test files across `pipeline/corpus/prompts/taxonomy/llm/core/config`) covers substantially more ground than spec's eight bullets: `pipeline/tests/test_adhoc_rule_parity.py` (a meta-test that fails the build if a new validation rule reaches only one of the two code paths that need it), `pipeline/tests/test_iterate_mode.py`, `pipeline/tests/test_guard_evidence.py` (the `leak_scan_ran`/`guarded_by` evidence fields), `pipeline/tests/test_run_experiment.py` (the CLI), and admin-layer tests in every app. None of this is a contradiction — spec said "the tests that matter most," not "the only tests" — but a spec rewrite's testing section is currently a significant undercount of what actually guards this codebase.

---

## 7. docker-compose.yml

```yaml
services:
  redis:
    image: redis:8-alpine
  postgres:
    image: postgres:17-alpine
    environment: {POSTGRES_USER: hcv, POSTGRES_PASSWORD: hcv, POSTGRES_DB: hcv}
```

Two services only: `redis` and `postgres`. No backend/Django service, no `qcluster` worker service, no frontend/Vite service.

- **Matches spec, no contradiction**: spec's only explicit claim about `docker-compose.yml` is "Redis is required in both [dev and prod], since Django-Q2 uses it as broker; `docker-compose.yml` provides it" (line 71) — true, confirmed (`redis:8-alpine`). Postgres's presence and version (`postgres:17-alpine`) also line up with spec's "DB (prod) | PostgreSQL | 17" stack-table row (line 60).
- **OMISSION** — spec never claims `docker-compose.yml` runs the full stack, so this isn't a contradiction, but it's worth noting explicitly for a spec rewrite: there is **no containerized path to running the Django app, the `qcluster` worker, or (eventually) the frontend** — `docker-compose.yml` is dev-infrastructure-only (cache/broker + database), not a deployment manifest. Anyone reading "the architecture follows CoReview... `docker-compose.yml` provides it" (§3) without checking the file could reasonably assume more is containerized than actually is.

---

## 8. Things promised but never built / things built that contradict rather than extend

**Promised, not found:**
- Nothing in scope (§§1-9, 12) was found to be entirely unbuilt. Phase 4 and metrics (§11, explicitly deferred) are confirmed still absent, as spec says they should be — `Label.source`/`created_by` exist and are unused beyond the seam spec describes; no metrics code exists anywhere. This is spec behaving correctly, not a finding.

**Built in a way that contradicts rather than extends (ranked):**
1. **Execution topology** (§2 above) — the task-per-phase-run / task-per-scenario / group-callback model in §6 was replaced with one-task-per-repetition, serial inside that task. This is the sharpest contradiction in the whole spec: it's not "the spec under-described a detail," it's "the spec describes a mechanism that was tried and is visibly not what `config/settings.py`'s own comments say is true today."
2. **Corpus filtering** (§4.2/§5 above) — `?run=`/`?variant=` are spec'd API contract that simply isn't there; `?system=` is an unspec'd substitute for part of what `?run=` would have done.
3. **Two-variant phase-2 prompts** — already known-stale per the task brief; confirmed structurally throughout §§1.3, 1.4, 5.
4. **"CRUD" on `/api/prompts/`** — labelled CRUD, built without D, deliberately.
5. **Preview's input shape** — spec says "a chosen experiment configuration"; built as a free-standing parameter bag that merely resembles one.

---

## Findings count

- **CONTRADICTION:** 11
- **OMISSION:** 29
- **STALE:** 6

(Some findings straddle two categories — e.g., the execution topology is reported once as CONTRADICTION even though it touches both §6's mechanism and §10's Run Detail screen; counted once at its primary location.)
