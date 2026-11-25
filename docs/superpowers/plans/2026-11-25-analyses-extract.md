# Analyses Extract Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Extract step that turns the analyst's free text into reviewed structured values, and feed the approved values plus the original text to the generator and labeller.

**Architecture:** One more run kind ("extract") on the existing runner. Values are stored one per `AnalysisItem` row, checked against a fields module copied from the owner's prompt, and written out as the system body the existing prompts read. The frontend gains a fifth step.

**Tech Stack:** as before (Django, DRF, django-q2, React, react-query, Tailwind). No new dependency.

**Spec:** `docs/superpowers/specs/2026-11-25-analyses-extract-design.md` (section 8 settles the open points) and its base `2026-11-22-analyses-design.md`.

## Global Constraints

- Branch `analyses`; no commits, no push. Each task ends as an uncommitted checkpoint.
- Prompt text: `resources/presets/extractor_v1.txt`, verbatim; `{{SYSTEM_DESCRIPTION}}` replaced by plain string replacement; one user message, no system prompt.
- Recommended extraction: `anthropic/claude-sonnet-5.5`, temperature 0.0.
- Generation and labelling prompts unchanged; parity tests keep passing.
- Analyses migrations folded into `0001_initial` (never deployed).
- The analyst can edit every value: allowed-value dropdowns, element ids for element-valued facts, a whole number for `retention_days`, true/false for `removable_media`, present/absent for controls.
- Copy: "Continue to …" for step changes; `[ Generate [ 8 ] scenarios ]`; "Approve all Asserted"; "The description changed since extraction"; "refers to a rejected element".

## Review Focus

1. A reply with a value in a different case or with spaces ("citizen ", "Asserted ") — normalise case-insensitively to the allowed spelling before marking a problem (Task 1).
2. An extracted element id reused by a fact before the element appears in the reply — resolve ids after reading all elements (Task 1).
3. Editing the description after approving values — Generate disabled with the stale notice, values kept for reference (Task 3).
4. Rejecting an element then approving it again — dependent facts return (Task 2, body test).
5. A very long description — the extractor request is one message; no limit beyond the 50,000 characters already enforced (Task 3 test with 50,000 characters).

---

### Task 1: Fields module, reply parser and body writer

**Files:** Create `backend/analyses/extraction.py`; Test `backend/analyses/tests/test_extraction.py`.

**Produces:**
- `FIELDS: dict[str, FieldSpec]` — `FieldSpec(kind: "element"|"fact", section: str, allowed: tuple[str] | None, value_kind: "choice"|"element"|"number"|"boolean", fact_type: "either"|"asserted"|"possible"|"asserted_if_named")`, exactly the prompt's tables.
- `CONTROLS: tuple[str]`, `CONTROL_FAMILIES: dict[str, tuple[str]]`.
- `ExtractError(ValueError)`.
- `parse_reply(text: str) -> tuple[list[dict], dict]` — item dicts `{kind, section, field, ref, value, state, fact_type, reason, other_reading, problem}` and gaps `{not_assessable: [{area, reason}], no_term: [{statement, reason}]}`; raises `ExtractError` for the run-failure cases of spec §8.
- `item_problem(item: dict, element_ids: set[str]) -> str`.
- `structured_body(items, gaps, description: str) -> str` — approved, problem-free items only; spec §8 format.

- [ ] Tests: `test_every_field_of_the_prompt_is_known` (counts: 14 element fields, 29 fact fields, 43 controls, 5 families); `test_always_possible_fields_become_possible`; `test_out_of_list_value_unknown_field_and_dangling_id_are_marked`; `test_case_and_spacing_are_normalised`; `test_ids_resolve_after_all_elements`; `test_duplicate_and_missing_ids_are_marked`; `test_wrong_kinds_are_marked` (`retention_days: "30 days"`, `removable_media: "yes"`); `test_reply_shape_failures_raise` (not JSON, list at top level, `elements` not a list, `gaps` not an object); `test_body_lists_sections_in_order_and_only_approved` (rejected line absent; facts of a rejected element absent; original text last).
- [ ] Run, see failures; implement; run, see passes.

### Task 2: Storage, state and preset

**Files:** Modify `backend/analyses/{models,state,preset,admin}.py`, regenerate `backend/analyses/migrations/0001_initial.py`; Test `backend/analyses/tests/test_extract_state.py`.

**Produces:**
- `AnalysisItem(analysis, kind, section, field, ref, value, original_value, state, original_state, fact_type, reason, other_reading, problem, decision, order)`.
- `Analysis.extraction_settings`, `extraction_gaps`, `extraction_digest`; `AnalysisRun.Kind.EXTRACT`.
- `preset.load_extractor() -> ExtractorPreset(model, temperature, name, text)`.
- `state.default_settings("extraction")`; `state.extraction_status(analysis) -> "none"|"current"|"stale"`; `state.pending_items(analysis) -> int`; `state.system_body(analysis) -> str`; `label_key` includes `sha256(system_body)`; steps and `resume_step` cover "extract".
- `state.item_view(item, element_ids, rejected_ids) -> dict` adds `problem` "refers to a rejected element".

- [ ] Tests: defaults (Sonnet 5.5, 0.0); stale after description edit; label key changes with the body; resume at extract; reject-then-approve restores dependent facts in the body.
- [ ] Run, fail, implement, pass.

### Task 3: Running extraction; generation reads the body

**Files:** Modify `backend/analyses/{runs,runner,selection}.py`; Test `backend/analyses/tests/test_extract_run.py`.

**Produces:** `runs.start_extraction(analysis, user)`; runner kind extract replaces items and gaps on success, sets digest; `start_generation` refuses (`invalid`) unless extraction is current with no pending item, and pins `system_body`; generation and labelling render with the body.

- [ ] Tests: request is one user message containing the description verbatim and `"{{SYSTEM_DESCRIPTION}}"` absent; failed reply keeps old items; extract refused while locked; generate refused when none/stale/pending; generation prompt contains "## Structured description" then "## Original description"; 50,000-character description extracts.
- [ ] Run, fail, implement, pass.

### Task 4: API, report, PDF

**Files:** Modify `backend/analyses/{views,serializers,report,pdf}.py`; Test `backend/analyses/tests/test_extract_api.py`.

**Produces:** detail JSON `extraction {status, settings, items[], gaps, pending, run}`; `POST analyses/{id}/extract/`; `PATCH analyses/{id}/items/{iid}/` `{decision?, value?, state?}` validated against the field (409 when locked); `POST analyses/{id}/items/approve-asserted/`; `GET analyses/options/` adds `extraction.fields` (allowed values per field) and `extraction.defaults`; report and PDF details add the extraction setup and counts.

- [ ] Tests: edit to an allowed value clears the problem and keeps `original_value`; edit to a disallowed value is 400; approve-asserted skips problems and Possible; locked edits 409; non-staff 403; report counts approved/edited/rejected.
- [ ] Run, fail, implement, pass.

### Task 5: Frontend

**Files:** Create `frontend/src/pages/analyses/ExtractStep.jsx`; modify `components/analyses/{steps.js,SettingsPanel.jsx}`, `pages/analyses/{AnalysisPage,GenerateStep,LabelStep,SystemStep,exampleSystem}.jsx|js`, `api/analyses.js`, `test/analysesApi.js`; Test `pages/analyses/__tests__/ExtractStep.test.jsx` and updates.

- [ ] Tests: five steps in order; Extract shows sections with value dropdowns, fact type, reason, problem, approve/reject; edit sends PATCH with the chosen value; "Approve all Asserted"; gaps listed; stale notice; Continue to generate disabled until none pending; Generate button reads "Generate [8] scenarios" with the count inside; step buttons read "Continue to labelling →" / "Continue to report →"; the example is SmartStop.
- [ ] Run, fail, implement, pass; lint; build.

### Task 6: Whole-flow verification

- [ ] Both suites, lint, build, `makemigrations --check`.
- [ ] Local site with a canned extractor reply for SmartStop: System → Extract (edit, reject, approve all) → Generate → Label → Report → PDF. Screenshots for the owner.
