# Phase-3-only experiments — design

**Date:** 2026-09-30
**Extends:** `2026-09-07-hcv-pipeline-design.md` (phases 1–3)

## 1. Goal

Let an experiment run phase 3 (labelling) on its own, over a chosen set of
scenarios, for two uses:

- **Comparing labellers.** Label the same scenarios with different models or
  prompts, across experiments.
- **Labelling new material.** Label systems and scenarios the researcher writes
  while setting up the experiment.

Scenarios come from the corpus: seed and hand-written rows, and the output of
earlier runs. New material is written in the picker and saved to the corpus as
manual rows. Everything phase 3 labels is a `Scenario` row, because `Label`
references one.

## 2. Experiment model and validation

- `ALLOWED_PHASES` becomes `([2], [2, 3], [3])`. `PHASE3_NEEDS_PHASE2` no
  longer applies to `[3]`.
- New field `Experiment.label_scenarios = ManyToManyField("corpus.Scenario",
  blank=True)`. It is read only when `phases == [3]`.
- A `[3]` experiment requires `phase3_model`, `phase3_prompt`, `taxonomy`, and
  at least one scenario in `label_scenarios`.
- A `[3]` experiment ignores every phase 2 field: `phase2_model`,
  `phase2_temperature`, `phase2_prompt_a/b`, `seed_systems`, `fewshots`,
  `generate_systems`, `generate_scenarios`, `target_system`, `n_systems`,
  `n_scenarios_per_system`, `variants`. The validators that check them
  (`generation_toggles_off_error`, `missing_target_for_no_generation_error`,
  the seed/target generated-origin checks, the phase-2 dry render) are skipped.
- Scratch scenarios (`is_scratch`, the output of prompt-editor test runs) are
  rejected in `label_scenarios`, with a field error naming them. Exports
  exclude scratch material, so an experiment must not label it.

## 3. Launch and execution

- `launch` writes the chosen IDs into the run's config snapshot as
  `label_scenario_ids` (sorted list). `validate_snapshot` requires the key for
  `[3]` snapshots and forbids it otherwise.
- Each repetition creates one phase 3 `PhaseRun` with `variant=None`,
  `source_phase_run=None`, and `scenario_ids` set from the snapshot.
- `scenarios_for` currently honours `scenario_ids` only for ad-hoc runs
  (`not phase_run.pipeline_run_id`). It is widened so any phase run with
  `scenario_ids` labels exactly that list.
- `execute_pipeline_run` runs the labelling run directly when the snapshot's
  phases are `[3]`, instead of iterating phase 2 runs.
- Retries and repeat runs read the snapshot, so they label the same set even if
  `label_scenarios` is edited later.
- Chosen scenarios deleted before execution are skipped. If none remain, the
  run fails with the existing message "The chosen scenarios were deleted;
  nothing to label." (`finish_labelling_run`).

## 4. Scenario picker modal

Opened from the experiment builder when "Phase 3 only" is selected. Uses
`Modal` in `wide` mode and builds on `useScenarioGroups` from
`ScenarioPicker.jsx`.

**Top bar**
- Source filter: All · Hand-written · one entry per earlier pipeline run
  ("experiment · repetition N"). Choosing a run narrows the left pane to systems
  that run wrote scenarios for, and the right pane to that run's scenarios.
  Scratch runs are not listed.
- Search box over system titles and scenario text.
- "Copy selection from…": lists earlier `[3]` experiments. Choosing one
  replaces the current selection with that experiment's `label_scenarios`.

**Left pane: systems**
- Every system with at least one selectable scenario, with a count of selected
  / total. Generated systems carry a "generated" badge.
- "+ New system" opens a draft system (title, body) with its own draft
  scenarios.

**Right pane: scenarios of the chosen system**
- Grouped by source (hand-written first, then each run, newest first), with a
  checkbox per scenario and "select all" per group.
- Each row shows the scenario ID, a truncated narrative and the card fields
  (actor, trigger, standing state, asset, threat, outcome), as
  `ScenarioPicker` does now.
- "+ Add scenario" on non-generated systems adds a draft scenario.
- Existing rows are read-only here. Editing them stays on the Corpus page, so
  the modal cannot change what earlier labels refer to.

**Drafts**
- Draft systems and scenarios can be added, edited and removed freely until
  Apply. Required fields follow the existing corpus serializers.
- Apply saves drafts through the existing corpus endpoints (origin `manual`),
  then ticks the new scenarios. Server errors are shown against the draft that
  caused them, and nothing in the selection changes until every draft saves.
- Saving to the corpus is mandatory. A muted note under the draft forms reads
  "New systems and scenarios are saved to the corpus when you apply."

**Footer**
- "N scenarios from M systems selected", Cancel, Apply.

**On the builder form**
- The phase selector gains "Phase 3 only". Selecting it hides the phase 2 and
  variant fields.
- The selection shows as one line, "42 scenarios · 2 systems · Edit", with a
  field error slot for `label_scenarios`.

## 5. Results display

- Experiment and run pages show "Phase 3 only" and the scenario count where
  they show variants for other experiments (`PhasesLabel`).
- `RunDetail`'s phase run table already renders `variant=None` as "—".
- Export is unchanged apart from the phase 3 run having no variant.

### 5a. Labels page

`Labels.jsx` draws one `VariantMatrix` per variant and fetches each matrix's
scenarios with `?variant=&run=`, which selects by the generating run. A `[3]`
run generated nothing, so that query returns no rows.

- For a `[3]` run the page draws a single matrix headed "N chosen scenarios"
  instead of the A/B pair.
- Its rows come from a new scenario list filter, `?labelled_by=<phase_run_id>`,
  which returns the scenarios in that phase run's `scenario_ids`.
- Its labels come from `/api/labels/?run=<id>` without a variant, and its
  taxonomy check reads the one phase 3 run.
- Columns, the HCV filter, the taxonomy mismatch notice and label details are
  shared with `VariantMatrix`. The matrix takes its scenario query as a prop
  rather than building it from `variant`.

## 6. Testing

**Backend**
- Validation for each phase combination, including missing phase 3 fields,
  empty `label_scenarios` and scratch scenarios for `[3]`.
- Snapshot contains `label_scenario_ids` for `[3]` and not otherwise.
- A `[3]` run labels exactly the chosen scenarios; retry labels the same set
  after `label_scenarios` changes; deleted scenarios are skipped; all-deleted
  fails with the existing message.
- `?labelled_by=` returns the run's scenarios and nothing else.
- Existing `[2]` and `[2, 3]` tests pass unchanged.

**Frontend**
- Modal: source filter narrowing both panes, search, selecting across systems,
  select-all per group, copy selection, drafting and applying a new system and
  scenario, draft save errors, existing rows not editable.
- Builder: "Phase 3 only" hides phase 2 fields and shows the selection summary.
- Labels page: single matrix for a `[3]` run with the chosen scenarios as rows.

## 7. Out of scope

- Editing existing corpus rows from the modal.
- Labelling scratch (test-run) scenarios in an experiment.
- Phase 3 variants for phase-3-only experiments.
