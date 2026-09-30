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
  `phase2_temperature`, `phase2_prompt_a/b`, `seed_systems`,
  `generate_systems`, `generate_scenarios`, `target_system`, `n_systems`,
  `n_scenarios_per_system`, `variants`. The validators that check them
  (`generation_config_errors`, the variant and per-variant template checks,
  the phase-2 dry render in `launch`) are skipped.
- `fewshots` still applies: its phase 3 few-shots are used when labelling.
  The builder shows only phase 3 few-shots for a `[3]` experiment.
- `phase2_model` becomes `blank=True` at the database level, and `clean()`
  requires it only when phase 2 runs.
- Scratch scenarios (`is_scratch`, the output of prompt-editor test runs) are
  rejected in `label_scenarios`, with a field error naming them. Exports
  exclude scratch material, so an experiment must not label it.

## 3. Launch and execution

- `config_snapshot` always writes `label_scenario_ids`: the sorted chosen IDs
  for `[3]`, `[]` otherwise. `validate_snapshot` checks the snapshot's exact
  key set, so the key is always present; it must be a non-empty list of ints
  for `[3]` and empty otherwise. A data migration adds
  `label_scenario_ids: []` to every existing non-empty snapshot.
- Each repetition creates one phase 3 `PhaseRun` with `variant=""`,
  `source_phase_run=None`, and `scenario_ids` set from the snapshot. The
  `scenario_ids_only_on_adhoc_labelling` check constraint is relaxed to
  "phase 3 only", so orchestrated labelling runs may carry `scenario_ids`.
- `scenarios_for` currently honours `scenario_ids` only for ad-hoc runs
  (`not phase_run.pipeline_run_id`). It is widened so any phase run with
  `scenario_ids` labels exactly that list.
- `finish_labelling_run` reports "Source phase run was deleted" for any
  orchestrated run with no source. That check is limited to runs without
  `scenario_ids`.
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
- "Load scenarios from an experiment…": lists earlier `[3]` experiments. Choosing one
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
- Apply saves all drafts in one request to a new endpoint,
  `POST /api/scenarios/bulk/`, which creates them in one transaction as
  `manual` rows and returns the new scenario IDs; the modal then ticks them.
  If any draft is invalid nothing is saved, and each error is shown against
  the draft that caused it.
- Draft scenario IDs are prefilled (`sc_01`, `sc_02`, … continuing after the
  system's existing IDs) and editable. Duplicates within a batch are rejected.
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
- Export: `_systems`/`_scenarios` currently include only rows the
  experiment's runs generated. They also include the scenarios named in those
  phase runs' `scenario_ids`, and those scenarios' systems, so every label in
  a bundle points at a scenario in the same bundle. `experiment_current`
  gains `label_scenarios` (sorted IDs).

### 5a. Labels page

`Labels.jsx` draws one `VariantMatrix` per variant and fetches each matrix's
scenarios with `?variant=&run=`, which selects by the generating run. A `[3]`
run generated nothing, so that query returns no rows.

- For a `[3]` run the page draws a single matrix headed "N chosen scenarios"
  instead of the A/B pair.
- Its rows come from a new scenario list filter,
  `?labelled_in_run=<pipeline_run_id>`, which returns the scenarios named in
  that repetition's phase 3 runs' `scenario_ids`.
- The page reads the selected run's `config.phases` to choose the layout, and
  hides the variant filter for a `[3]` run.
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
- `?labelled_in_run=` returns the run's chosen scenarios and nothing else.
- `POST /api/scenarios/bulk/` creates everything or nothing.
- Export of a `[3]` run includes the chosen scenarios and their systems.
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
