# Phase-3-only Experiments Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an experiment run phase 3 (labelling) alone over a hand-picked set of corpus scenarios, chosen (or newly written) in a modal, with results on the Labels page and in exports.

**Architecture:** `Experiment` gains `phases=[3]` and a `label_scenarios` M2M. `launch` freezes the chosen IDs into the run's config snapshot and creates one variant-less phase 3 `PhaseRun` per repetition carrying `scenario_ids`, which the existing labelling fan-out already knows how to honour. The frontend adds a two-pane picker modal (built on `useScenarioGroups`) that can also save new systems/scenarios through a new all-or-nothing `POST /api/scenarios/bulk/`, and the Labels page gets a single-matrix layout for these runs.

**Tech Stack:** Django 5.2 + DRF, pytest (`uv run pytest`); React + TanStack Query, Vitest + Testing Library + MSW (`npm test`).

**Spec:** `docs/superpowers/specs/2026-09-30-phase3-only-experiments-design.md`

## Global Constraints

- **Do not commit.** The user reviews the whole change as an uncommitted diff. No task has a commit step; never run `git commit`, `git stash` or `git reset`.
- Code comments: one or two lines, saying what and why. No investigation notes, and never describe something that is absent ("there is no X").
- Match surrounding style: DRF `integer_param` for integer query params, `ValidationError` dicts keyed by field name, Tailwind classes as used in neighbouring JSX.
- Allowed phase lists are exactly `[2]`, `[2, 3]`, `[3]`.
- A `[3]` experiment ignores every phase 2 field except `fewshots` (its phase 3 few-shots still apply).
- Scratch scenarios (`Scenario.is_scratch`, prompt-test output) can never be chosen.
- Muted note text, verbatim: "New systems and scenarios are saved to the corpus when you apply."
- Backend tests: run from `backend/` with `uv run pytest`. Frontend tests: run from `frontend/` with `npm test -- --run <path>`.

## Review Focus

1. **PATCH that switches an experiment to `[3]` and sends `label_scenarios` in the same request.** `clean()` must validate the *submitted* scenarios, not the old saved (empty) set. Pinned by `test_patch_to_phase3_only_validates_the_submitted_scenarios` (Task 2).
2. **A chosen scenario deleted after saving but before launch.** The M2M row cascades away; if none are left, `launch` must refuse with the "choose at least one" error rather than launch an empty run. Pinned by `test_launch_refuses_when_every_chosen_scenario_was_deleted` (Task 4).
3. **Retrying a failed phase-3-only run after the experiment's selection was edited.** Retry must stay on the frozen set. Pinned by `test_retry_stays_on_the_frozen_selection` (Task 4).
4. **Runs launched before this change.** Their snapshots lack `label_scenario_ids`; the data migration must add it or every old run becomes unexecutable and unexportable. Pinned by `test_backfill_adds_label_scenario_ids_to_old_snapshots` (Task 1).
5. **The Labels page for an old run whose `config` has no `phases`.** Must still render the A/B layout. Pinned by `renders the A/B matrices for a run whose config has no phases` (Task 10).

---

## File Structure

**Backend**
- `backend/pipeline/models.py`: `ALLOWED_PHASES`, `_phases_error`, `label_scenarios_error`, `Experiment.label_scenarios`, `Experiment.labels_only`, `Experiment.label_scenario_rows()`, `clean()`; `PhaseRun` constraint.
- `backend/pipeline/migrations/0013_phase3_only_experiments.py`: schema + snapshot backfill.
- `backend/pipeline/serializers.py`: `ExperimentSerializer` gains `label_scenarios`.
- `backend/pipeline/execution_config.py`: snapshot key `label_scenario_ids`.
- `backend/pipeline/orchestration.py`: `launch`, `execute_pipeline_run`.
- `backend/pipeline/labelling.py`: `scenarios_for`, `finish_labelling_run`.
- `backend/pipeline/export.py`: include chosen scenarios; `experiment_current.label_scenarios`.
- `backend/corpus/serializers.py`: `ScenarioBulkCreateSerializer`.
- `backend/corpus/views.py`: `?labelled_in_run=` filter, `bulk` action.
- Tests: `backend/pipeline/tests/test_phase3_only.py` (new), `backend/corpus/tests/test_scenario_bulk.py` (new), plus fixture updates in existing snapshot tests.

**Frontend**
- `frontend/src/api/queries.js`: `useBulkCreateScenarios`.
- `frontend/src/components/ScenarioPicker.jsx`: export `SEED_GROUP`, `CARD_FIELDS`.
- `frontend/src/components/labelScenarioSelection.js` (new): pure helpers.
- `frontend/src/components/LabelScenariosModal.jsx` (new): the modal.
- `frontend/src/pages/ExperimentBuilder.jsx`: phase toggles, "Scenarios to label" fieldset.
- `frontend/src/components/PhasesLabel.jsx`, `frontend/src/pages/ExperimentDetail.jsx`: `[3]` display.
- `frontend/src/pages/Labels.jsx`: generic `LabelMatrix`, single matrix for `[3]` runs.
- Tests: `components/__tests__/labelScenarioSelection.test.js`, `components/__tests__/LabelScenariosModal.test.jsx` (new); `pages/__tests__/ExperimentBuilder.test.jsx`, `pages/__tests__/Labels.test.jsx` (extended).

---

### Task 1: Model, migration and validation rules

**Files:**
- Modify: `backend/pipeline/models.py` (lines 17–21 constants; `_phases_error` ~436; `Experiment` fields ~462 and ~500; `clean()` ~560–650; `PhaseRun.Meta.constraints` ~812)
- Create: `backend/pipeline/migrations/0013_phase3_only_experiments.py`
- Test: `backend/pipeline/tests/test_phase3_only.py` (new)

**Interfaces:**
- Produces: `ALLOWED_PHASES` including `[3]`; `PHASE_LABELLING_ONLY = [3]`; `label_scenarios_error(scenarios) -> str | None`; `Experiment.label_scenarios` (M2M to `corpus.Scenario`, `related_name="labelled_by_experiments"`); `Experiment.labels_only -> bool`; `Experiment.label_scenario_rows() -> list[Scenario]` (honours `self._pending_label_scenarios` when set); migration function `backfill_label_scenario_ids(apps, schema_editor)`.

- [ ] **Step 1: Write the failing tests**

Create `backend/pipeline/tests/test_phase3_only.py`:

```python
import importlib
import json

import pytest
from django.core.exceptions import ValidationError

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Experiment, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

LABELLED = json.dumps({"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]})


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def label_prompt(db):
    return PromptTemplate.objects.create_version(
        phase=3, name="l", user_prompt="{{ scenario.scenario_id }}"
    )


@pytest.fixture
def chosen(db):
    system = SystemDescription.objects.create(title="Oldsmar", body="b", origin=Origin.SEED)
    return [
        Scenario.objects.create(
            system_description=system, scenario_id=f"sc_0{i}", origin=Origin.SEED, narrative="n"
        )
        for i in (1, 2)
    ]


def make_phase3_only(taxonomy, label_prompt, scenarios, name="p3"):
    experiment = Experiment.objects.create(
        name=name,
        taxonomy=taxonomy,
        phases=[3],
        phase3_model="m3",
        phase3_prompt=label_prompt,
    )
    experiment.label_scenarios.set(scenarios)
    return experiment


def scratch_scenario(taxonomy):
    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    test_run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=generation, taxonomy=taxonomy
    )
    system = SystemDescription.objects.create(
        title="Scratch", origin=Origin.GENERATED, generation_run=test_run
    )
    return Scenario.objects.create(
        system_description=system,
        scenario_id="sc_99",
        origin=Origin.GENERATED,
        generation_run=test_run,
    )


@pytest.mark.django_db
def test_phase3_only_experiment_validates_without_any_phase2_fields(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    experiment.full_clean()


@pytest.mark.django_db
def test_phase3_only_requires_phase3_settings(taxonomy, chosen):
    experiment = Experiment.objects.create(name="p3", taxonomy=taxonomy, phases=[3])
    experiment.label_scenarios.set(chosen)
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert set(caught.value.message_dict) == {"phase3_model", "phase3_prompt"}


@pytest.mark.django_db
def test_phase3_only_requires_at_least_one_scenario(taxonomy, label_prompt):
    experiment = make_phase3_only(taxonomy, label_prompt, [])
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert "Choose at least one scenario" in caught.value.message_dict["label_scenarios"][0]


@pytest.mark.django_db
def test_phase3_only_refuses_scratch_scenarios(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, [*chosen, scratch_scenario(taxonomy)])
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert "sc_99" in caught.value.message_dict["label_scenarios"][0]


@pytest.mark.django_db
def test_generation_experiments_still_require_phase2_model(taxonomy, label_prompt):
    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    experiment = Experiment.objects.create(
        name="gen",
        taxonomy=taxonomy,
        phases=[2, 3],
        phase2_prompt_a=generation,
        phase2_prompt_b=generation,
        phase3_model="m3",
        phase3_prompt=label_prompt,
    )
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert "phase2_model" in caught.value.message_dict


@pytest.mark.django_db
def test_empty_phases_is_still_refused(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    experiment.phases = []
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert caught.value.message_dict["phases"] == ["Choose the phases to run: [2], [2, 3] or [3]."]


@pytest.mark.django_db
def test_orchestrated_labelling_runs_may_carry_scenario_ids(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    PhaseRun.objects.create(
        pipeline_run=run,
        phase=3,
        model="m3",
        prompt_template=label_prompt,
        taxonomy=taxonomy,
        scenario_ids=[s.pk for s in chosen],
    )


@pytest.mark.django_db
def test_backfill_adds_label_scenario_ids_to_old_snapshots(taxonomy, label_prompt, chosen):
    from django.apps import apps

    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    old = PipelineRun.objects.create(
        experiment=experiment, repetition_index=1, config={"phases": [2, 3], "n_systems": 1}
    )
    never_snapshotted = PipelineRun.objects.create(experiment=experiment, repetition_index=2)
    migration = importlib.import_module("pipeline.migrations.0013_phase3_only_experiments")
    migration.backfill_label_scenario_ids(apps, None)
    old.refresh_from_db()
    never_snapshotted.refresh_from_db()
    assert old.config["label_scenario_ids"] == []
    assert never_snapshotted.config == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest pipeline/tests/test_phase3_only.py -v`
Expected: FAIL, collection or `AttributeError` on `label_scenarios`, and `ModuleNotFoundError` for the migration.

- [ ] **Step 3: Update the constants and `_phases_error`**

In `backend/pipeline/models.py`, replace lines 19–21:

```python
# The only two pipeline shapes: labelling always needs this run's own generation.
ALLOWED_PHASES = ([PHASE_GENERATION], [PHASE_GENERATION, PHASE_LABELLING])
PHASE3_NEEDS_PHASE2 = "Phase 3 labels the scenarios phase 2 generates, so it needs phase 2."
```

with:

```python
PHASE_LABELLING_ONLY = [PHASE_LABELLING]
# Generate only, generate then label that output, or label chosen corpus scenarios.
ALLOWED_PHASES = ([PHASE_GENERATION], [PHASE_GENERATION, PHASE_LABELLING], PHASE_LABELLING_ONLY)
```

In `_phases_error`, change the first message and delete the last check:

```python
def _phases_error(phases):
    """Inline `Experiment.clean()` rule (not a `*_error` helper): an ad-hoc run
    has a single `phase` field and nothing equivalent to check."""
    if not isinstance(phases, list) or not phases:
        return "Choose the phases to run: [2], [2, 3] or [3]."
    if any(isinstance(p, bool) or p not in (PHASE_GENERATION, PHASE_LABELLING) for p in phases):
        return f"Unknown phase(s) in {phases!r}; expected 2 and/or 3."
    if len(set(phases)) != len(phases):
        return f"Duplicate phase(s) in {phases!r}; each phase may appear at most once."
    return None
```

Directly after `_phases_error`, add:

```python
def label_scenarios_error(scenarios):
    """The chosen scenarios of a phase-3-only experiment, or `None` when valid.

    Scratch rows are refused because exports leave prompt-test output out.
    """
    if not scenarios:
        return "Choose at least one scenario to label: this experiment runs phase 3 only."
    scratch = sorted(scenario.scenario_id for scenario in scenarios if scenario.is_scratch)
    if scratch:
        return (
            "Scenarios from prompt test runs can't be labelled in an experiment: "
            f"{', '.join(scratch)}."
        )
    return None
```

- [ ] **Step 4: Update the `Experiment` fields**

Change `phase2_model = models.CharField(max_length=200)` to:

```python
    # Blank for a phase-3-only experiment; `clean()` requires it when phase 2 runs.
    phase2_model = models.CharField(max_length=200, blank=True, default="")
```

Change the `phases` comment and add the M2M after `fewshots`:

```python
    # [2] (generate only), [2, 3] (generate, then label that output) or [3]
    # (label `label_scenarios`).
    phases = models.JSONField(default=default_phases)

    seed_systems = models.ManyToManyField("corpus.SystemDescription", blank=True)
    fewshots = models.ManyToManyField("corpus.FewShotExample", blank=True)
    # What a phase-3-only experiment labels; ignored for other phase lists.
    label_scenarios = models.ManyToManyField(
        "corpus.Scenario", blank=True, related_name="labelled_by_experiments"
    )
```

After the `runs_labelling` property, add:

```python
    @property
    def labels_only(self):
        return self.phases == PHASE_LABELLING_ONLY

    def label_scenario_rows(self):
        """The scenarios `clean()` checks: the serializer's pending list when it
        set one, else the saved M2M (empty for an unsaved row)."""
        pending = getattr(self, "_pending_label_scenarios", None)
        if pending is not None:
            return list(pending)
        if not self.pk:
            return []
        return list(self.label_scenarios.select_related("generation_run"))
```

- [ ] **Step 5: Update `clean()`**

Replace the first statement of `clean()`:

```python
        errors = dict(generation_config_errors(self.generation_config))
```

with:

```python
        errors = {} if self.labels_only else dict(generation_config_errors(self.generation_config))
```

Wrap the variant checks so they only run for generating experiments. Replace:

```python
        if variant_errors:
            errors["variants"] = variant_errors
```

with:

```python
        if variant_errors and not self.labels_only:
            errors["variants"] = variant_errors
```

Replace the per-variant template loop's condition:

```python
            if variant in effective_variants and getattr(self, f"{field}_id") is None:
```

with:

```python
            if (
                not self.labels_only
                and variant in effective_variants
                and getattr(self, f"{field}_id") is None
            ):
```

Directly before `# Phase-3 settings are required only when this experiment labels.`, add:

```python
        if not self.labels_only and not (self.phase2_model or "").strip():
            errors["phase2_model"] = "Set phase2_model: phase 2 is enabled."
        if self.labels_only:
            label_error = label_scenarios_error(self.label_scenario_rows())
            if label_error:
                errors["label_scenarios"] = label_error
```

- [ ] **Step 6: Relax the `PhaseRun` constraint**

In `PhaseRun.Meta.constraints`, replace the first `CheckConstraint`:

```python
            models.CheckConstraint(
                condition=Q(scenario_ids__isnull=True) | Q(phase=3),
                name="scenario_ids_only_on_labelling",
            ),
```

- [ ] **Step 7: Generate and hand-edit the migration**

Run: `cd backend && uv run python manage.py makemigrations pipeline --name phase3_only_experiments`
Expected: creates `pipeline/migrations/0013_phase3_only_experiments.py` with `AlterField phase2_model`, `AddField label_scenarios`, `RemoveConstraint scenario_ids_only_on_adhoc_labelling`, `AddConstraint scenario_ids_only_on_labelling`.

Add the backfill to that file, above `class Migration`, and a `RunPython` as the last operation:

```python
def backfill_label_scenario_ids(apps, schema_editor):
    """`validate_snapshot` checks the exact key set, so every existing snapshot
    gets the new key (empty: those runs all generated their own scenarios)."""
    PipelineRun = apps.get_model("pipeline", "PipelineRun")
    for run in PipelineRun.objects.all().iterator():
        if run.config and "label_scenario_ids" not in run.config:
            run.config = {**run.config, "label_scenario_ids": []}
            run.save(update_fields=["config"])


def remove_label_scenario_ids(apps, schema_editor):
    PipelineRun = apps.get_model("pipeline", "PipelineRun")
    for run in PipelineRun.objects.all().iterator():
        if run.config and "label_scenario_ids" in run.config:
            run.config = {k: v for k, v in run.config.items() if k != "label_scenario_ids"}
            run.save(update_fields=["config"])
```

```python
        migrations.RunPython(backfill_label_scenario_ids, remove_label_scenario_ids),
```

Also add `('corpus', '0006_scenario_unique_manual_scenario_id_per_system')` to `dependencies` if `makemigrations` did not.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `cd backend && uv run pytest pipeline/tests/test_phase3_only.py -v`
Expected: PASS (8 tests).

- [ ] **Step 9: Run the pipeline suite for regressions**

Run: `cd backend && uv run pytest pipeline -q`
Expected: PASS except tests that assert the old `PHASE3_NEEDS_PHASE2` message or that `[3]` is refused. Update each such assertion: a `[3]` experiment is now valid when it has phase 3 settings and `label_scenarios`, and the empty/invalid-phases message is "Choose the phases to run: [2], [2, 3] or [3]." Do not weaken any other assertion.

---

### Task 2: Experiment API accepts `label_scenarios`

**Files:**
- Modify: `backend/pipeline/serializers.py:72-170` (`ExperimentSerializer`)
- Test: `backend/pipeline/tests/test_phase3_only.py` (append)

**Interfaces:**
- Consumes: `Experiment.label_scenario_rows()` and `_pending_label_scenarios` (Task 1).
- Produces: `label_scenarios` read/write field (list of scenario PKs) on `/api/experiments/`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/pipeline/tests/test_phase3_only.py`:

```python
from django.contrib.auth import get_user_model


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(
        email="admin@example.com", password="pw-123456", is_staff=True
    )


@pytest.mark.django_db
def test_staff_creates_a_phase3_only_experiment(api_client, staff, taxonomy, label_prompt, chosen):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/experiments/",
        {
            "name": "labellers",
            "taxonomy": taxonomy.id,
            "phases": [3],
            "phase3_model": "m3",
            "phase3_prompt": label_prompt.id,
            "label_scenarios": [s.id for s in chosen],
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    assert sorted(response.json()["label_scenarios"]) == sorted(s.id for s in chosen)


@pytest.mark.django_db
def test_create_phase3_only_without_scenarios_is_a_field_error(
    api_client, staff, taxonomy, label_prompt
):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/experiments/",
        {
            "name": "empty",
            "taxonomy": taxonomy.id,
            "phases": [3],
            "phase3_model": "m3",
            "phase3_prompt": label_prompt.id,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "label_scenarios" in response.json()


@pytest.mark.django_db
def test_patch_to_phase3_only_validates_the_submitted_scenarios(
    api_client, staff, taxonomy, label_prompt, chosen
):
    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    experiment = Experiment.objects.create(
        name="switch",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt_a=generation,
        phase2_prompt_b=generation,
        phase3_model="m3",
        phase3_prompt=label_prompt,
    )
    api_client.force_authenticate(user=staff)
    response = api_client.patch(
        f"/api/experiments/{experiment.id}/",
        {"phases": [3], "label_scenarios": [chosen[0].id]},
        format="json",
    )
    assert response.status_code == 200, response.json()
    assert list(experiment.label_scenarios.values_list("id", flat=True)) == [chosen[0].id]


@pytest.mark.django_db
def test_create_refuses_scratch_scenarios(api_client, staff, taxonomy, label_prompt, chosen):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/experiments/",
        {
            "name": "scratchy",
            "taxonomy": taxonomy.id,
            "phases": [3],
            "phase3_model": "m3",
            "phase3_prompt": label_prompt.id,
            "label_scenarios": [chosen[0].id, scratch_scenario(taxonomy).id],
        },
        format="json",
    )
    assert response.status_code == 400
    assert "sc_99" in response.json()["label_scenarios"][0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest pipeline/tests/test_phase3_only.py -k "creates_a_phase3 or without_scenarios or patch_to or refuses_scratch_scenarios" -v`
Expected: FAIL (`label_scenarios` is not a serializer field, so it is ignored and validation errors differ).

- [ ] **Step 3: Implement**

In `ExperimentSerializer.Meta.fields`, add `"label_scenarios",` after `"fewshots",`, and change `m2m_fields`:

```python
        m2m_fields = frozenset({"seed_systems", "fewshots", "label_scenarios"})
```

In `validate()`, after the `for field, value in attrs.items():` loop and before `try:`, add:

```python
        if "label_scenarios" in attrs:
            # clean() validates the submitted selection, not the saved one.
            instance._pending_label_scenarios = attrs["label_scenarios"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest pipeline/tests/test_phase3_only.py pipeline/tests/test_experiment_api.py -v`
Expected: PASS.

---

### Task 3: Snapshot key `label_scenario_ids`

**Files:**
- Modify: `backend/pipeline/execution_config.py` (`_TOP_LEVEL_KEY_ORDER` ~493, `config_snapshot` ~571, `validate_snapshot` ~834)
- Modify: any test that builds a snapshot dict by hand (find with `grep -rn '"n_scenarios_per_system":' backend/*/tests`)
- Test: `backend/pipeline/tests/test_phase3_only.py` (append)

**Interfaces:**
- Consumes: `Experiment.labels_only`, `PHASE_LABELLING_ONLY` (Task 1).
- Produces: `config["label_scenario_ids"]`, a sorted `list[int]`, non-empty exactly when `config["phases"] == [3]`.

- [ ] **Step 1: Write the failing tests**

Append:

```python
from pipeline.execution_config import MalformedSnapshot, config_snapshot, validate_snapshot


@pytest.mark.django_db
def test_snapshot_pins_the_chosen_ids(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, list(reversed(chosen)))
    config = config_snapshot(experiment)
    assert config["phases"] == [3]
    assert config["label_scenario_ids"] == sorted(s.pk for s in chosen)
    validate_snapshot(config)


@pytest.mark.django_db
def test_snapshot_of_a_generating_experiment_has_no_chosen_ids(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    experiment.phases = [2, 3]
    assert config_snapshot(experiment)["label_scenario_ids"] == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "phases, ids, message",
    [
        ([3], [], "must name at least one scenario"),
        ([2, 3], [1], "must be empty"),
        ([3], [True], "must be an integer"),
        ([3], "1", "must be a list"),
    ],
)
def test_validate_snapshot_checks_label_scenario_ids(
    taxonomy, label_prompt, chosen, phases, ids, message
):
    config = config_snapshot(make_phase3_only(taxonomy, label_prompt, chosen))
    config["phases"] = phases
    config["label_scenario_ids"] = ids
    with pytest.raises(MalformedSnapshot, match=message):
        validate_snapshot(config)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest pipeline/tests/test_phase3_only.py -k snapshot -v`
Expected: FAIL with `KeyError: 'label_scenario_ids'`.

- [ ] **Step 3: Implement**

Add `"label_scenario_ids",` as the last entry of `_TOP_LEVEL_KEY_ORDER`.

Add to the imports from `pipeline.models` at the top of `execution_config.py`: `PHASE_LABELLING_ONLY`.

At the end of the dict returned by `config_snapshot`, add:

```python
        # What a phase-3-only run labels, frozen so retries and repeats agree.
        "label_scenario_ids": (
            sorted(experiment.label_scenarios.values_list("pk", flat=True))
            if experiment.labels_only
            else []
        ),
```

At the end of `validate_snapshot`, add:

```python
    ids = config["label_scenario_ids"]
    _require_list(ids, "label_scenario_ids")
    for index, scenario_pk in enumerate(ids):
        _require_int(scenario_pk, f"label_scenario_ids[{index}]")
    if config["phases"] == PHASE_LABELLING_ONLY and not ids:
        raise MalformedSnapshot("label_scenario_ids must name at least one scenario for phases [3].")
    if config["phases"] != PHASE_LABELLING_ONLY and ids:
        raise MalformedSnapshot(
            f"label_scenario_ids must be empty for phases {config['phases']!r}, got {ids!r}."
        )
```

- [ ] **Step 4: Update hand-built snapshots in existing tests**

Run: `grep -rln '"n_scenarios_per_system":' backend/*/tests`
In each test that builds a full `PipelineRun.config` dict literally (as opposed to an ad-hoc request payload), add `"label_scenario_ids": [],`. Tests that build their config through `config_snapshot()` need no change.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest pipeline prompts -q`
Expected: PASS.

---

### Task 4: Launching and running a phase-3-only experiment

**Files:**
- Modify: `backend/pipeline/orchestration.py` (`launch` ~124–190, `execute_pipeline_run` ~312–393)
- Modify: `backend/pipeline/labelling.py` (`scenarios_for` ~34–56, `finish_labelling_run` ~190–240)
- Test: `backend/pipeline/tests/test_phase3_only.py` (append)

**Interfaces:**
- Consumes: `config["label_scenario_ids"]` (Task 3), `PHASE_GENERATION`.
- Produces: per repetition, one `PhaseRun(phase=3, variant="", source_phase_run=None, scenario_ids=[...])`.

- [ ] **Step 1: Write the failing tests**

Append:

```python
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.models import Label
from pipeline.orchestration import execute_pipeline_run, launch, retry_failed


@pytest.fixture
def fake_llm():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.mark.django_db
def test_launch_creates_one_variantless_labelling_run(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    phase_runs = list(run.phase_runs.all())
    assert len(phase_runs) == 1
    (labelling,) = phase_runs
    assert (labelling.phase, labelling.variant, labelling.source_phase_run_id) == (3, "", None)
    assert labelling.scenario_ids == sorted(s.pk for s in chosen)


@pytest.mark.django_db
def test_execute_labels_exactly_the_chosen_scenarios(taxonomy, label_prompt, chosen, fake_llm):
    other = Scenario.objects.create(
        system_description=chosen[0].system_description, scenario_id="sc_09", origin=Origin.SEED
    )
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    fake_llm.queue(LABELLED)
    fake_llm.queue(LABELLED)
    execute_pipeline_run(run)
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.COMPLETED
    labelled = set(Label.objects.values_list("scenario_id", flat=True))
    assert labelled == {s.pk for s in chosen}
    assert other.pk not in labelled


@pytest.mark.django_db
def test_retry_stays_on_the_frozen_selection(taxonomy, label_prompt, chosen, fake_llm):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    fake_llm.queue("not json")
    fake_llm.queue(LABELLED)
    execute_pipeline_run(run)
    labelling = run.phase_runs.get()
    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.FAILED

    experiment.label_scenarios.set([chosen[1]])
    fake_llm.queue(LABELLED)
    retry_failed(labelling)
    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.COMPLETED
    assert labelling.scenario_ids == sorted(s.pk for s in chosen)
    assert set(Label.objects.values_list("scenario_id", flat=True)) == {s.pk for s in chosen}


@pytest.mark.django_db
def test_launch_refuses_when_every_chosen_scenario_was_deleted(taxonomy, label_prompt, chosen):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    Scenario.objects.filter(pk__in=[s.pk for s in chosen]).delete()
    with pytest.raises(ValidationError) as caught:
        launch(experiment, n_repetitions=1)
    assert "label_scenarios" in caught.value.message_dict
    assert not PipelineRun.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db
def test_scenarios_deleted_after_launch_are_skipped(taxonomy, label_prompt, chosen, fake_llm):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    chosen[0].delete()
    fake_llm.queue(LABELLED)
    execute_pipeline_run(run)
    labelling = run.phase_runs.get()
    assert labelling.status == PhaseRun.Status.COMPLETED
    assert labelling.total_children == 1


@pytest.mark.django_db
def test_all_chosen_deleted_after_launch_fails_with_the_existing_message(
    taxonomy, label_prompt, chosen, fake_llm
):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    Scenario.objects.filter(pk__in=[s.pk for s in chosen]).delete()
    execute_pipeline_run(run)
    labelling = run.phase_runs.get()
    assert labelling.status == PhaseRun.Status.FAILED
    assert labelling.error == "The chosen scenarios were deleted; nothing to label."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest pipeline/tests/test_phase3_only.py -k "launch or execute or retry or deleted" -v`
Expected: FAIL. `launch` creates generation runs for variants A and B and dry-renders missing phase 2 templates.

- [ ] **Step 3: Update `launch`**

Add `PHASE_GENERATION` to the `pipeline.models` import in `orchestration.py`. Replace:

```python
    config = config_snapshot(experiment)
    _refuse_templates_that_fail_as_their_own_variant(experiment, config)
    labels = PHASE_LABELLING in config["phases"]

    for offset in range(n_repetitions):
        run = PipelineRun.objects.create(
            experiment=experiment,
            repetition_index=highest + offset + 1,
            config=copy.deepcopy(config),
        )
        for variant in experiment.variants:
```

with:

```python
    config = config_snapshot(experiment)
    generates = PHASE_GENERATION in config["phases"]
    if generates:
        _refuse_templates_that_fail_as_their_own_variant(experiment, config)
    labels = PHASE_LABELLING in config["phases"]

    for offset in range(n_repetitions):
        run = PipelineRun.objects.create(
            experiment=experiment,
            repetition_index=highest + offset + 1,
            config=copy.deepcopy(config),
        )
        if not generates:
            # Phase 3 only: one labelling run over the frozen selection.
            PhaseRun.objects.create(
                pipeline_run=run,
                phase=3,
                model=experiment.phase3_model,
                temperature=experiment.phase3_temperature,
                prompt_template=experiment.phase3_prompt,
                taxonomy=experiment.taxonomy,
                scenario_ids=list(config["label_scenario_ids"]),
            )
            created.append(run)
            continue
        for variant in experiment.variants:
```

Update the docstring's last paragraph to:

```python
    Labelling `PhaseRun`s are created only when the pinned snapshot's
    `phases` includes 3 — read from `config`, so the rows match the record.
    A phase-3-only snapshot gets one labelling run over `label_scenario_ids`.
    """
```

- [ ] **Step 4: Update `execute_pipeline_run`**

Directly before the final `return roll_up(pipeline_run)` of `execute_pipeline_run`, add:

```python
    # Phase-3-only repetitions: labelling runs over a frozen selection.
    for labelling in pipeline_run.phase_runs.filter(
        phase=3, scenario_ids__isnull=False
    ).order_by("id"):
        run_labelling(labelling, on_progress=on_progress)

```

- [ ] **Step 5: Update `scenarios_for` and `finish_labelling_run`**

In `pipeline/labelling.py`, replace in `scenarios_for`:

```python
    if phase_run.scenario_ids is not None and not phase_run.pipeline_run_id:
        return Scenario.objects.filter(id__in=phase_run.scenario_ids)
```

with:

```python
    if phase_run.scenario_ids is not None:
        return Scenario.objects.filter(id__in=phase_run.scenario_ids)
```

and change its docstring's last paragraph to:

```python
    A run created with a chosen subset (`scenario_ids`: an ad-hoc test run or
    a phase-3-only repetition) labels exactly those that still exist — never
    everything, even if all were deleted since.
```

In `finish_labelling_run`, replace:

```python
    if phase_run.pipeline_run_id and not phase_run.source_phase_run_id:
```

with:

```python
    if (
        phase_run.pipeline_run_id
        and not phase_run.source_phase_run_id
        and phase_run.scenario_ids is None
    ):
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend && uv run pytest pipeline -q`
Expected: PASS.

---

### Task 5: Export includes the chosen scenarios

**Files:**
- Modify: `backend/pipeline/export.py` (`_experiment` ~80–95, `_systems` ~338, `_scenarios` ~354)
- Test: `backend/pipeline/tests/test_phase3_only.py` (append)

**Interfaces:**
- Consumes: `PhaseRun.scenario_ids` on orchestrated runs (Task 4).
- Produces: bundles whose `systems`/`scenarios` cover every labelled scenario; `experiment_current["label_scenarios"]`.

- [ ] **Step 1: Write the failing test**

Append:

```python
from pipeline.export import build_bundle, build_run_bundle


@pytest.mark.django_db
def test_export_of_a_phase3_only_run_includes_the_chosen_scenarios(
    taxonomy, label_prompt, chosen, fake_llm
):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    fake_llm.queue(LABELLED)
    fake_llm.queue(LABELLED)
    execute_pipeline_run(run)

    for bundle in (build_bundle(experiment), build_run_bundle(run)):
        scenario_ids = {row["id"] for row in bundle["scenarios"]}
        assert scenario_ids == {s.pk for s in chosen}
        assert {row["scenario"] for row in bundle["labels"]} <= scenario_ids
        assert [row["title"] for row in bundle["systems"]] == ["Oldsmar"]
        assert bundle["experiment_current"]["label_scenarios"] == sorted(s.pk for s in chosen)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_phase3_only.py -k export -v`
Expected: FAIL, `bundle["scenarios"]` is empty.

- [ ] **Step 3: Implement**

In `export.py`, add `from django.db.models import Q, Sum` (replacing the `Sum`-only import). Add above `_systems`:

```python
def _chosen_scenario_ids(phase_run_ids):
    """Scenarios a phase-3-only repetition labelled from the corpus."""
    chosen = set()
    for ids in PhaseRun.objects.filter(
        pk__in=phase_run_ids, scenario_ids__isnull=False
    ).values_list("scenario_ids", flat=True):
        chosen.update(ids)
    return chosen
```

In `_systems`, replace the queryset filter:

```python
        for s in SystemDescription.objects.filter(
            Q(generation_run_id__in=phase_run_ids)
            | Q(scenarios__id__in=_chosen_scenario_ids(phase_run_ids))
        )
        .distinct()
        .select_related("generation_run")
        .order_by("title", "id")
```

In `_scenarios`, replace the queryset filter:

```python
        for s in Scenario.objects.filter(
            Q(generation_run_id__in=phase_run_ids)
            | Q(id__in=_chosen_scenario_ids(phase_run_ids))
        )
        .select_related("generation_run")
        .prefetch_related("intended_hcvs")
        .order_by("system_description_id", "order", "scenario_id", "id")
```

In `_experiment`, after `"variants": experiment.variants,` add:

```python
        "label_scenarios": sorted(experiment.label_scenarios.values_list("pk", flat=True)),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && uv run pytest pipeline -q`
Expected: PASS. If a `test_export.py` assertion compares `experiment_current` to an exact dict, add `"label_scenarios": []` to its expected value.

---

### Task 6: Corpus API: `?labelled_in_run=` and `POST /api/scenarios/bulk/`

**Files:**
- Modify: `backend/corpus/serializers.py` (append new serializers)
- Modify: `backend/corpus/views.py` (`ScenarioViewSet`)
- Test: `backend/corpus/tests/test_scenario_bulk.py` (new)

**Interfaces:**
- Produces:
  - `GET /api/scenarios/?labelled_in_run=<pipeline_run_id>`: scenarios named in that repetition's phase 3 runs' `scenario_ids`.
  - `POST /api/scenarios/bulk/` with body `{"systems": [{"title", "body", "scenarios": [card]}], "scenarios": [{"system_description": id, ...card}]}` where `card = {scenario_id, actor, trigger, standing_state, asset, threat, outcome, narrative}`. Returns 201 `{"scenario_ids": [int, ...]}` (new systems' scenarios first, in order, then existing-system scenarios). On any error returns 400 and creates nothing; errors mirror the body's shape (`{"systems": [{...}, {"scenarios": [{}, {"scenario_id": [...]}]}], "scenarios": [...]}`), with `non_field_errors` for an empty body.

- [ ] **Step 1: Write the failing tests**

Create `backend/corpus/tests/test_scenario_bulk.py`:

```python
import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Experiment, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def member(db):
    return get_user_model().objects.create_user(email="m@example.com", password="pw-123456")


@pytest.fixture
def oldsmar(db):
    system = SystemDescription.objects.create(title="Oldsmar", body="b", origin=Origin.SEED)
    Scenario.objects.create(system_description=system, scenario_id="sc_01", origin=Origin.SEED)
    return system


def card(scenario_id, **fields):
    return {"scenario_id": scenario_id, "threat": "t", "narrative": "n", **fields}


@pytest.mark.django_db
def test_bulk_creates_new_systems_and_scenarios_as_manual_rows(api_client, member, oldsmar):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {
            "systems": [{"title": "Riverbend", "body": "rb", "scenarios": [card("sc_01"), card("sc_02")]}],
            "scenarios": [{"system_description": oldsmar.id, **card("sc_02")}],
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    created = list(Scenario.objects.filter(pk__in=response.json()["scenario_ids"]))
    assert len(created) == 3
    assert {s.origin for s in created} == {Origin.MANUAL}
    assert {s.created_by_id for s in created} == {member.id}
    riverbend = SystemDescription.objects.get(title="Riverbend")
    assert riverbend.origin == Origin.MANUAL
    assert riverbend.scenarios.count() == 2


@pytest.mark.django_db
def test_bulk_creates_nothing_when_one_draft_is_invalid(api_client, member, oldsmar):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {
            "systems": [{"title": "Riverbend", "body": "", "scenarios": [card("sc_01")]}],
            "scenarios": [{"system_description": oldsmar.id, **card("sc_01")}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["scenarios"][0]["non_field_errors"]
    assert not SystemDescription.objects.filter(title="Riverbend").exists()


@pytest.mark.django_db
def test_bulk_rejects_duplicate_ids_within_one_new_system(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {"systems": [{"title": "R", "scenarios": [card("sc_01"), card("sc_01")]}]},
        format="json",
    )
    assert response.status_code == 400
    errors = response.json()["systems"][0]["scenarios"]
    assert errors[0] == {}
    assert "sc_01" in errors[1]["scenario_id"][0]


@pytest.mark.django_db
def test_bulk_rejects_duplicate_ids_for_one_existing_system(api_client, member, oldsmar):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {
            "scenarios": [
                {"system_description": oldsmar.id, **card("sc_05")},
                {"system_description": oldsmar.id, **card("sc_05")},
            ]
        },
        format="json",
    )
    assert response.status_code == 400
    assert "sc_05" in response.json()["scenarios"][1]["scenario_id"][0]


@pytest.mark.django_db
def test_bulk_refuses_scenarios_on_a_generated_system(api_client, member):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1", is_active=True)
    gen = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    run = PhaseRun.objects.create(phase=2, variant="A", model="m", prompt_template=gen, taxonomy=tax)
    generated = SystemDescription.objects.create(
        title="G", origin=Origin.GENERATED, generation_run=run
    )
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/scenarios/bulk/",
        {"scenarios": [{"system_description": generated.id, **card("sc_01")}]},
        format="json",
    )
    assert response.status_code == 400
    assert "generated" in response.json()["scenarios"][0]["system_description"][0]


@pytest.mark.django_db
def test_bulk_refuses_an_empty_body(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post("/api/scenarios/bulk/", {}, format="json")
    assert response.status_code == 400
    assert response.json()["non_field_errors"]


@pytest.mark.django_db
def test_bulk_requires_authentication(api_client):
    response = api_client.post("/api/scenarios/bulk/", {"systems": []}, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_labelled_in_run_lists_only_that_runs_chosen_scenarios(api_client, member, oldsmar):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="x", order=7)
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    chosen = oldsmar.scenarios.get()
    other = Scenario.objects.create(system_description=oldsmar, scenario_id="sc_02", origin=Origin.SEED)
    experiment = Experiment.objects.create(
        name="p3", taxonomy=tax, phases=[3], phase3_model="m3", phase3_prompt=lab
    )
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    PhaseRun.objects.create(
        pipeline_run=run, phase=3, model="m3", prompt_template=lab, taxonomy=tax,
        scenario_ids=[chosen.id],
    )
    api_client.force_authenticate(user=member)
    ids = [row["id"] for row in api_client.get(f"/api/scenarios/?labelled_in_run={run.id}").json()["results"]]
    assert ids == [chosen.id]
    assert other.id not in ids
    assert api_client.get("/api/scenarios/?labelled_in_run=0").json()["results"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && uv run pytest corpus/tests/test_scenario_bulk.py -v`
Expected: FAIL (404 for `/bulk/`; `labelled_in_run` ignored).

- [ ] **Step 3: Add the serializers**

Append to `backend/corpus/serializers.py` (add `from django.db import transaction` to the imports):

```python
DRAFT_CARD_FIELDS = [
    "scenario_id",
    "actor",
    "trigger",
    "standing_state",
    "asset",
    "threat",
    "outcome",
    "narrative",
]


class _DraftScenarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scenario
        fields = DRAFT_CARD_FIELDS


class _DraftSystemSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300)
    body = serializers.CharField(allow_blank=True, required=False, default="")
    scenarios = _DraftScenarioSerializer(many=True)


class _DraftExistingScenarioSerializer(serializers.ModelSerializer):
    class Meta:
        model = Scenario
        fields = ["system_description", *DRAFT_CARD_FIELDS]
        validators = [
            UniqueTogetherValidator(
                queryset=Scenario.objects.filter(generation_run__isnull=True),
                fields=("system_description", "scenario_id"),
            )
        ]

    def validate_system_description(self, system):
        if system.origin == Origin.GENERATED:
            raise serializers.ValidationError(
                "Scenarios can't be added to a generated system: it is a record of model output."
            )
        return system


def _duplicate_ids_errors(rows, key=lambda row: row["scenario_id"]):
    """Per-row errors for repeated scenario IDs within one batch, or `None`."""
    seen, errors, found = set(), [], False
    for row in rows:
        if key(row) in seen:
            errors.append({"scenario_id": [f"{row['scenario_id']} is used twice in this batch."]})
            found = True
        else:
            seen.add(key(row))
            errors.append({})
    return errors if found else None


class ScenarioBulkCreateSerializer(serializers.Serializer):
    """New systems with their scenarios, and new scenarios for existing
    systems, created together or not at all."""

    systems = _DraftSystemSerializer(many=True, required=False, default=list)
    scenarios = _DraftExistingScenarioSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        if not attrs["systems"] and not attrs["scenarios"]:
            raise serializers.ValidationError("Add at least one system or scenario.")
        errors = {}
        system_errors = [
            {"scenarios": dup} if (dup := _duplicate_ids_errors(system["scenarios"])) else {}
            for system in attrs["systems"]
        ]
        if any(system_errors):
            errors["systems"] = system_errors
        existing = _duplicate_ids_errors(
            attrs["scenarios"],
            key=lambda row: (row["system_description"].pk, row["scenario_id"]),
        )
        if existing:
            errors["scenarios"] = existing
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        created = []
        for draft in validated_data["systems"]:
            system = SystemDescription.objects.create(
                title=draft["title"], body=draft["body"], origin=Origin.MANUAL, created_by=user
            )
            for order, card in enumerate(draft["scenarios"], start=1):
                created.append(
                    Scenario.objects.create(
                        system_description=system,
                        origin=Origin.MANUAL,
                        created_by=user,
                        order=order,
                        **card,
                    )
                )
        for card in validated_data["scenarios"]:
            created.append(
                Scenario.objects.create(origin=Origin.MANUAL, created_by=user, **card)
            )
        return created
```

Make sure `Origin`, `Scenario`, `SystemDescription` and `UniqueTogetherValidator` are imported at the top of the file (they are used by the existing serializers; add any that are missing).

- [ ] **Step 4: Add the filter and the action**

In `backend/corpus/views.py`, add imports:

```python
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
```

and add `ScenarioBulkCreateSerializer` to the `corpus.serializers` import.

In `ScenarioViewSet.get_queryset`, before `return queryset`, add:

```python
        labelled_in_run = integer_param(params, "labelled_in_run")
        if labelled_in_run is not None:
            from pipeline.models import PhaseRun

            chosen = set()
            for ids in PhaseRun.objects.filter(
                pipeline_run_id=labelled_in_run, phase=3, scenario_ids__isnull=False
            ).values_list("scenario_ids", flat=True):
                chosen.update(ids)
            queryset = queryset.filter(id__in=chosen)
```

Add to `ScenarioViewSet`, after `perform_create`:

```python
    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        """Save drafted systems and scenarios in one transaction."""
        serializer = ScenarioBulkCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        created = serializer.save()
        return Response(
            {"scenario_ids": [scenario.pk for scenario in created]},
            status=status.HTTP_201_CREATED,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && uv run pytest corpus -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS.

---

### Task 7: Frontend helpers and the bulk-create hook

**Files:**
- Modify: `frontend/src/components/ScenarioPicker.jsx` (lines 14–24: export constants)
- Modify: `frontend/src/api/queries.js` (after `useScenarios`, ~line 805)
- Create: `frontend/src/components/labelScenarioSelection.js`
- Test: `frontend/src/components/__tests__/labelScenarioSelection.test.js`

**Interfaces:**
- Consumes: `useScenarioGroups()` group shape `{ key, source: 'seed'|'experiment'|'test', title, rows: [{ id, system_description, scenario_id, searchText, ... }] }`.
- Produces:
  - `export const SEED_GROUP`, `export const CARD_FIELDS` from `ScenarioPicker.jsx`.
  - `useBulkCreateScenarios()`: mutation; `mutateAsync(payload) -> { scenario_ids: number[] }`.
  - From `labelScenarioSelection.js`: `sourceOptions(groups) -> [{value, label}]`; `visibleRows(groups, {source, search}) -> row[]` (each row plus `groupKey`, `groupTitle`); `systemEntries(systems, rows, {source, search}) -> [{...system, rows}]`; `rowsBySource(rows) -> [{key, title, rows}]`; `nextScenarioId(takenIds: string[]) -> string`; `emptyDraftScenario(scenarioId) -> draft`; `bulkPayload(drafts) -> body`; `draftErrors(body, path) -> object`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/__tests__/labelScenarioSelection.test.js`:

```js
import { describe, expect, it } from 'vitest';
import {
  bulkPayload,
  draftErrors,
  nextScenarioId,
  rowsBySource,
  sourceOptions,
  systemEntries,
  visibleRows,
} from '../labelScenarioSelection';

const row = (id, system, text) => ({ id, system_description: system, scenario_id: `sc_0${id}`, searchText: text });
const groups = [
  { key: 'seed', source: 'seed', title: 'Seed & hand-written scenarios', rows: [row(1, 1, 'remote login'), row(2, 2, 'badge')] },
  { key: 'run-20', source: 'experiment', title: 'Baseline · repetition 2 · Variant B', rows: [row(3, 1, 'spoofed sensor')] },
  { key: 'run-10', source: 'test', title: 'Prompt test run · Variant A', rows: [row(4, 3, 'scratch')] },
];
const systems = [
  { id: 1, title: 'Oldsmar', origin: 'seed', is_scratch: false },
  { id: 2, title: 'Riverbend', origin: 'manual', is_scratch: false },
  { id: 3, title: 'Scratch', origin: 'generated', is_scratch: true },
  { id: 4, title: 'Empty plant', origin: 'manual', is_scratch: false },
];

describe('labelScenarioSelection', () => {
  it('offers hand-written and experiment sources, never prompt test runs', () => {
    expect(sourceOptions(groups)).toEqual([
      { value: 'seed', label: 'Seed & hand-written scenarios' },
      { value: 'run-20', label: 'Baseline · repetition 2 · Variant B' },
    ]);
  });

  it('filters rows by source and search, tagging each with its group', () => {
    expect(visibleRows(groups).map((r) => r.id)).toEqual([1, 2, 3]);
    expect(visibleRows(groups, { source: 'run-20' })).toEqual([
      expect.objectContaining({ id: 3, groupKey: 'run-20', groupTitle: 'Baseline · repetition 2 · Variant B' }),
    ]);
    expect(visibleRows(groups, { search: 'SPOOFED' }).map((r) => r.id)).toEqual([3]);
  });

  it('lists every non-scratch system for all sources, and only matching ones for a run', () => {
    const all = systemEntries(systems, visibleRows(groups));
    expect(all.map((s) => [s.title, s.rows.length])).toEqual([
      ['Oldsmar', 2],
      ['Riverbend', 1],
      ['Empty plant', 0],
    ]);
    const run = systemEntries(systems, visibleRows(groups, { source: 'run-20' }), { source: 'run-20' });
    expect(run.map((s) => s.title)).toEqual(['Oldsmar']);
  });

  it('groups one system rows by source in arrival order', () => {
    const oldsmar = systemEntries(systems, visibleRows(groups))[0];
    expect(rowsBySource(oldsmar.rows).map((g) => [g.title, g.rows.length])).toEqual([
      ['Seed & hand-written scenarios', 1],
      ['Baseline · repetition 2 · Variant B', 1],
    ]);
  });

  it('continues scenario ids after the highest sc_NN', () => {
    expect(nextScenarioId([])).toBe('sc_01');
    expect(nextScenarioId(['sc_01', 'sc_09', 'custom'])).toBe('sc_10');
  });

  it('builds the bulk body without client-only keys', () => {
    const drafts = {
      systems: [{ key: 's1', title: 'New', body: 'b', scenarios: [{ key: 'd1', scenario_id: 'sc_01', threat: 't' }] }],
      scenarios: [{ key: 'd2', systemId: 1, scenario_id: 'sc_03', threat: 'x' }],
    };
    expect(bulkPayload(drafts)).toEqual({
      systems: [{ title: 'New', body: 'b', scenarios: [{ scenario_id: 'sc_01', threat: 't' }] }],
      scenarios: [{ system_description: 1, scenario_id: 'sc_03', threat: 'x' }],
    });
  });

  it('reads one draft errors out of the nested error body', () => {
    const body = { systems: [{}, { title: ['Required.'], scenarios: [{}, { scenario_id: ['Dup.'] }] }] };
    expect(draftErrors(body, ['systems', 1])).toEqual(body.systems[1]);
    expect(draftErrors(body, ['systems', 1, 'scenarios', 1])).toEqual({ scenario_id: ['Dup.'] });
    expect(draftErrors(body, ['scenarios', 0])).toEqual({});
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- --run src/components/__tests__/labelScenarioSelection.test.js`
Expected: FAIL, module not found.

- [ ] **Step 3: Export the picker constants**

In `frontend/src/components/ScenarioPicker.jsx`, change `const SEED_GROUP = 'seed';` to `export const SEED_GROUP = 'seed';` and `const CARD_FIELDS = [` to `export const CARD_FIELDS = [`.

- [ ] **Step 4: Create the helpers**

Create `frontend/src/components/labelScenarioSelection.js`:

```js
import { CARD_FIELDS } from './ScenarioPicker';

// Prompt-test output can't be labelled in an experiment, so its groups are never offered.
function selectableGroups(groups) {
  return groups.filter((group) => group.source !== 'test');
}

function searchWords(search) {
  return search.toLowerCase().split(/\s+/).filter(Boolean);
}

export function sourceOptions(groups) {
  return selectableGroups(groups).map((group) => ({ value: group.key, label: group.title }));
}

export function visibleRows(groups, { source = '', search = '' } = {}) {
  const words = searchWords(search);
  return selectableGroups(groups)
    .filter((group) => !source || group.key === source)
    .flatMap((group) =>
      group.rows
        .filter((row) => words.every((word) => row.searchText.includes(word)))
        .map((row) => ({ ...row, groupKey: group.key, groupTitle: group.title }))
    );
}

// With no source chosen, systems without scenarios stay listed so new ones can be added to them.
export function systemEntries(systems, rows, { source = '', search = '' } = {}) {
  const bySystem = new Map();
  for (const row of rows) {
    if (!bySystem.has(row.system_description)) bySystem.set(row.system_description, []);
    bySystem.get(row.system_description).push(row);
  }
  const words = searchWords(search);
  return systems
    .filter((system) => !system.is_scratch)
    .filter((system) => {
      if (bySystem.has(system.id)) return true;
      if (source) return false;
      return words.every((word) => system.title.toLowerCase().includes(word));
    })
    .map((system) => ({ ...system, rows: bySystem.get(system.id) ?? [] }));
}

export function rowsBySource(rows) {
  const bySource = new Map();
  for (const row of rows) {
    if (!bySource.has(row.groupKey)) bySource.set(row.groupKey, { key: row.groupKey, title: row.groupTitle, rows: [] });
    bySource.get(row.groupKey).rows.push(row);
  }
  return [...bySource.values()];
}

export function nextScenarioId(takenIds) {
  let highest = 0;
  for (const id of takenIds) {
    const match = /^sc_(\d+)$/.exec(id);
    if (match) highest = Math.max(highest, Number(match[1]));
  }
  return `sc_${String(highest + 1).padStart(2, '0')}`;
}

let draftCounter = 0;
export function draftKey() {
  draftCounter += 1;
  return `draft-${draftCounter}`;
}

export function emptyDraftScenario(scenarioId) {
  return {
    key: draftKey(),
    scenario_id: scenarioId,
    ...Object.fromEntries(CARD_FIELDS.map(([field]) => [field, ''])),
    narrative: '',
  };
}

function withoutKey({ key: _key, ...fields }) {
  return fields;
}

export function bulkPayload(drafts) {
  return {
    systems: drafts.systems.map(({ title, body, scenarios }) => ({ title, body, scenarios: scenarios.map(withoutKey) })),
    scenarios: drafts.scenarios.map(({ systemId, ...rest }) => ({ system_description: systemId, ...withoutKey(rest) })),
  };
}

export function draftErrors(body, path) {
  let node = body;
  for (const step of path) node = node?.[step];
  return node && typeof node === 'object' && !Array.isArray(node) ? node : {};
}
```

- [ ] **Step 5: Add the mutation hook**

In `frontend/src/api/queries.js`, after `useUpdateScenario` (or directly after `useScenarios` if that hook sits elsewhere), add:

```js
// `POST /api/scenarios/bulk/` - saves drafted systems and scenarios in one
// transaction; returns `{ scenario_ids }` for the new scenarios.
export function useBulkCreateScenarios() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload) => {
      const { data } = await apiClient.post('/scenarios/bulk/', payload);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.scenarios.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.systemDescriptions.all });
    },
  });
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd frontend && npm test -- --run src/components/__tests__/labelScenarioSelection.test.js src/components/__tests__/ScenarioPicker.test.jsx`
Expected: PASS.

---

### Task 8: The picker modal

**Files:**
- Create: `frontend/src/components/LabelScenariosModal.jsx`
- Test: `frontend/src/components/__tests__/LabelScenariosModal.test.jsx`

**Interfaces:**
- Consumes: `Modal`, `useScenarioGroups`, `CARD_FIELDS`, `useSystemDescriptions`, `useExperiments`, `useBulkCreateScenarios` (Task 7), helpers from `labelScenarioSelection.js` (Task 7).
- Produces: `<LabelScenariosModal initialIds={number[]} excludeExperimentId={number|null} onApply={(ids: number[]) => void} onClose={() => void} />`. `onApply` receives the final selection including newly saved scenario IDs; the parent closes the modal.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/__tests__/LabelScenariosModal.test.jsx`:

```jsx
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '../../test/server';
import { paginated } from '../../test/mockApi';
import { setQueryClient } from '../../api/client';
import LabelScenariosModal from '../LabelScenariosModal';

function scenario(id, systemId, generationRun, fields = {}) {
  return {
    id,
    system_description: systemId,
    scenario_id: `sc_0${id}`,
    actor: '', trigger: '', standing_state: '', asset: '', threat: '', outcome: '', narrative: '',
    origin: generationRun ? 'generated' : 'seed',
    generation_run: generationRun,
    ...fields,
  };
}

let bulkBody;

beforeEach(() => {
  bulkBody = null;
  server.use(
    http.get('/api/scenarios/', () =>
      HttpResponse.json(
        paginated([
          scenario(1, 1, null, { threat: 'Remote login abuse' }),
          scenario(2, 1, null, { origin: 'manual', threat: 'Insider edits setpoints' }),
          scenario(3, 2, 20, { threat: 'Spoofed sensor readings' }),
          scenario(4, 3, 10, { threat: 'Scratch only' }),
        ])
      )
    ),
    http.get('/api/system-descriptions/', () =>
      HttpResponse.json(
        paginated([
          { id: 1, title: 'Oldsmar', origin: 'seed', is_scratch: false },
          { id: 2, title: 'Riverbend', origin: 'generated', is_scratch: false },
          { id: 3, title: 'Scratch plant', origin: 'generated', is_scratch: true },
        ])
      )
    ),
    http.get('/api/phase-runs/', () =>
      HttpResponse.json(
        paginated([
          { id: 10, phase: 2, variant: 'A', pipeline_run: null, prompt_template: 100, model: 'm', status: 'completed' },
          { id: 20, phase: 2, variant: 'B', pipeline_run: 7, prompt_template: 100, model: 'm', status: 'completed', finished_at: '2026-09-12T00:00:00Z' },
        ])
      )
    ),
    http.get('/api/prompts/', () => HttpResponse.json(paginated([{ id: 100, name: 'gen', version: 1 }]))),
    http.get('/api/pipeline-runs/', () => HttpResponse.json(paginated([{ id: 7, experiment: 3, repetition_index: 2 }]))),
    http.get('/api/experiments/', () =>
      HttpResponse.json(
        paginated([
          { id: 3, name: 'Baseline', phases: [2, 3], label_scenarios: [] },
          { id: 5, name: 'Labeller GPT', phases: [3], label_scenarios: [2, 3] },
        ])
      )
    ),
    http.post('/api/scenarios/bulk/', async ({ request }) => {
      bulkBody = await request.json();
      return HttpResponse.json({ scenario_ids: [50, 51] }, { status: 201 });
    })
  );
});

function renderModal({ initialIds = [], onApply = vi.fn(), onClose = vi.fn() } = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  setQueryClient(queryClient);
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <LabelScenariosModal initialIds={initialIds} excludeExperimentId={null} onApply={onApply} onClose={onClose} />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { onApply, onClose };
}

describe('LabelScenariosModal', () => {
  it('lists non-scratch systems and shows the chosen system scenarios grouped by source', async () => {
    const user = userEvent.setup();
    renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    expect(within(systems).getByRole('button', { name: /oldsmar/i })).toBeInTheDocument();
    expect(within(systems).getByRole('button', { name: /riverbend.*generated/i })).toBeInTheDocument();
    expect(within(systems).queryByRole('button', { name: /scratch plant/i })).not.toBeInTheDocument();

    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    expect(screen.getByRole('checkbox', { name: /select all in seed & hand-written/i })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /sc_01/ })).toBeInTheDocument();
  });

  it('narrows both panes to one run with the source filter, and never offers prompt test runs', async () => {
    const user = userEvent.setup();
    renderModal();
    const source = await screen.findByRole('combobox', { name: /source/i });
    expect(within(source).queryByRole('option', { name: /prompt test run/i })).not.toBeInTheDocument();
    await user.selectOptions(source, within(source).getByRole('option', { name: /baseline · repetition 2/i }));
    const systems = screen.getByRole('list', { name: /systems/i });
    expect(within(systems).getAllByRole('button').map((b) => b.textContent)).toEqual([expect.stringMatching(/Riverbend/)]);
  });

  it('selects across systems, counts them in the footer, and applies the ids', async () => {
    const user = userEvent.setup();
    const { onApply } = renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    await user.click(screen.getByRole('checkbox', { name: /select all in seed & hand-written/i }));
    await user.click(within(systems).getByRole('button', { name: /riverbend/i }));
    await user.click(screen.getByRole('checkbox', { name: /sc_03/ }));
    expect(screen.getByText('3 scenarios from 2 systems selected')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(onApply).toHaveBeenCalledWith([1, 2, 3]);
    expect(bulkBody).toBeNull();
  });

  it('copies another phase-3-only experiment selection', async () => {
    const user = userEvent.setup();
    renderModal({ initialIds: [1] });
    const copy = await screen.findByRole('combobox', { name: /copy selection from/i });
    expect(within(copy).queryByRole('option', { name: /baseline/i })).not.toBeInTheDocument();
    await user.selectOptions(copy, within(copy).getByRole('option', { name: /labeller gpt/i }));
    expect(screen.getByText('2 scenarios from 2 systems selected')).toBeInTheDocument();
  });

  it('drafts a new system and a scenario for an existing one, saves them in one request, and selects them', async () => {
    const user = userEvent.setup();
    const { onApply } = renderModal({ initialIds: [1] });
    const systems = await screen.findByRole('list', { name: /systems/i });

    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    await user.click(screen.getByRole('button', { name: /add scenario/i }));
    expect(screen.getByLabelText(/^scenario id$/i)).toHaveValue('sc_03');
    await user.type(screen.getByLabelText(/^threat$/i), 'Phished operator');

    await user.click(screen.getByRole('button', { name: /new system/i }));
    await user.type(screen.getByLabelText(/^system title$/i), 'Lakeside');
    await user.click(screen.getByRole('button', { name: /add scenario/i }));
    expect(screen.getByText('New systems and scenarios are saved to the corpus when you apply.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(bulkBody).toEqual({
      systems: [expect.objectContaining({ title: 'Lakeside', scenarios: [expect.objectContaining({ scenario_id: 'sc_01' })] })],
      scenarios: [expect.objectContaining({ system_description: 1, scenario_id: 'sc_03', threat: 'Phished operator' })],
    });
    expect(onApply).toHaveBeenCalledWith([1, 50, 51]);
  });

  it('shows a draft save error next to its draft and keeps the modal open', async () => {
    server.use(
      http.post('/api/scenarios/bulk/', () =>
        HttpResponse.json({ scenarios: [{ scenario_id: ['sc_03 is used twice in this batch.'] }] }, { status: 400 })
      )
    );
    const user = userEvent.setup();
    const { onApply } = renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    await user.click(within(systems).getByRole('button', { name: /oldsmar/i }));
    await user.click(screen.getByRole('button', { name: /add scenario/i }));
    await user.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(await screen.findByText('sc_03 is used twice in this batch.')).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  it('offers no way to edit existing rows or add to a generated system', async () => {
    const user = userEvent.setup();
    renderModal();
    const systems = await screen.findByRole('list', { name: /systems/i });
    await user.click(within(systems).getByRole('button', { name: /riverbend/i }));
    expect(screen.queryByRole('button', { name: /add scenario/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: /threat/i })).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- --run src/components/__tests__/LabelScenariosModal.test.jsx`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement the modal**

Create `frontend/src/components/LabelScenariosModal.jsx`:

```jsx
import { useMemo, useState } from 'react';
import { useBulkCreateScenarios, useExperiments, useSystemDescriptions } from '../api/queries';
import ErrorBanner from './ErrorBanner';
import Modal from './Modal';
import { CARD_FIELDS, useScenarioGroups } from './ScenarioPicker';
import {
  bulkPayload,
  draftErrors,
  draftKey,
  emptyDraftScenario,
  nextScenarioId,
  rowsBySource,
  sourceOptions,
  systemEntries,
  visibleRows,
} from './labelScenarioSelection';

const DRAFT_FIELDS = [...CARD_FIELDS, ['narrative', 'Narrative']];
const inputClass = 'mt-1 block w-full rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm';

function plural(count, word) {
  return `${count} ${count === 1 ? word : `${word}s`}`;
}

function FieldMessages({ messages }) {
  if (!messages) return null;
  return <p className="mt-1 text-xs text-red-600">{[].concat(messages).join(' ')}</p>;
}

function DraftScenarioFields({ draft, errors, onChange, onRemove }) {
  return (
    <div className="space-y-2 rounded-md border border-dashed border-gray-300 p-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-700">New scenario</span>
        <button type="button" onClick={onRemove} className="text-xs font-medium text-red-700 hover:underline">
          Remove
        </button>
      </div>
      <label className="block text-xs font-medium text-gray-700">
        Scenario ID
        <input
          value={draft.scenario_id}
          onChange={(event) => onChange({ ...draft, scenario_id: event.target.value })}
          className={inputClass}
        />
        <FieldMessages messages={errors.scenario_id} />
      </label>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {DRAFT_FIELDS.map(([field, label]) => (
          <label key={field} className={`block text-xs font-medium text-gray-700 ${field === 'narrative' ? 'sm:col-span-2' : ''}`}>
            {label}
            {field === 'narrative' ? (
              <textarea
                rows={3}
                value={draft[field]}
                onChange={(event) => onChange({ ...draft, [field]: event.target.value })}
                className={inputClass}
              />
            ) : (
              <input
                value={draft[field]}
                onChange={(event) => onChange({ ...draft, [field]: event.target.value })}
                className={inputClass}
              />
            )}
            <FieldMessages messages={errors[field]} />
          </label>
        ))}
      </div>
      <FieldMessages messages={errors.non_field_errors} />
    </div>
  );
}

export default function LabelScenariosModal({ initialIds, excludeExperimentId, onApply, onClose }) {
  const { groups, isLoading, error } = useScenarioGroups();
  const systemsQuery = useSystemDescriptions();
  const experimentsQuery = useExperiments();
  const bulkCreate = useBulkCreateScenarios();

  const [selected, setSelected] = useState(() => new Set(initialIds.map(Number)));
  const [source, setSource] = useState('');
  const [search, setSearch] = useState('');
  const [active, setActive] = useState(null);
  const [drafts, setDrafts] = useState({ systems: [], scenarios: [] });

  const systems = systemsQuery.data?.results ?? [];
  const rows = useMemo(() => visibleRows(groups, { source, search }), [groups, source, search]);
  const entries = useMemo(() => systemEntries(systems, rows, { source, search }), [systems, rows, source, search]);
  const systemOfScenario = useMemo(
    () => new Map(groups.flatMap((group) => group.rows.map((row) => [row.id, row.system_description]))),
    [groups]
  );
  const copyOptions = (experimentsQuery.data?.results ?? []).filter(
    (experiment) =>
      experiment.id !== excludeExperimentId &&
      experiment.phases?.length === 1 &&
      experiment.phases[0] === 3 &&
      (experiment.label_scenarios ?? []).length > 0
  );
  const saveErrors = bulkCreate.error?.response?.data ?? {};

  const activeEntry = typeof active === 'number' ? entries.find((entry) => entry.id === active) ?? null : null;
  const activeDraftIndex = typeof active === 'string' ? drafts.systems.findIndex((system) => system.key === active) : -1;

  const draftScenarioCount =
    drafts.scenarios.length + drafts.systems.reduce((total, system) => total + system.scenarios.length, 0);
  const selectedSystems = new Set(
    [...selected].map((id) => systemOfScenario.get(id)).filter((id) => id != null)
  );
  for (const draft of drafts.scenarios) selectedSystems.add(draft.systemId);
  for (const system of drafts.systems) if (system.scenarios.length > 0) selectedSystems.add(system.key);
  const scenarioCount = selected.size + draftScenarioCount;

  function toggle(ids, checked) {
    setSelected((current) => {
      const next = new Set(current);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }

  function addDraftToExisting(entry) {
    const taken = [
      ...entry.rows.map((row) => row.scenario_id),
      ...drafts.scenarios.filter((draft) => draft.systemId === entry.id).map((draft) => draft.scenario_id),
    ];
    setDrafts((current) => ({
      ...current,
      scenarios: [...current.scenarios, { ...emptyDraftScenario(nextScenarioId(taken)), systemId: entry.id }],
    }));
  }

  function updateExistingDraft(key, next) {
    setDrafts((current) => ({
      ...current,
      scenarios: current.scenarios.map((draft) => (draft.key === key ? next : draft)),
    }));
  }

  function removeExistingDraft(key) {
    setDrafts((current) => ({ ...current, scenarios: current.scenarios.filter((draft) => draft.key !== key) }));
  }

  function addDraftSystem() {
    const key = draftKey();
    setDrafts((current) => ({ ...current, systems: [...current.systems, { key, title: '', body: '', scenarios: [] }] }));
    setActive(key);
  }

  function updateDraftSystem(index, changes) {
    setDrafts((current) => ({
      ...current,
      systems: current.systems.map((system, i) => (i === index ? { ...system, ...changes } : system)),
    }));
  }

  function removeDraftSystem(index) {
    setDrafts((current) => ({ ...current, systems: current.systems.filter((_, i) => i !== index) }));
    setActive(null);
  }

  async function handleApply() {
    if (draftScenarioCount === 0 && drafts.systems.length === 0) {
      onApply([...selected]);
      return;
    }
    try {
      const { scenario_ids: created } = await bulkCreate.mutateAsync(bulkPayload(drafts));
      onApply([...selected, ...created]);
    } catch {
      // Shown next to each draft via saveErrors.
    }
  }

  const hasDrafts = drafts.systems.length > 0 || drafts.scenarios.length > 0;
  const bannerError =
    bulkCreate.isError && (typeof saveErrors.detail === 'string' || saveErrors.non_field_errors) ? bulkCreate.error : null;

  return (
    <Modal title="Choose scenarios to label" onClose={onClose} wide>
      {isLoading && <p className="text-xs text-gray-500">Loading scenarios…</p>}
      {error && <ErrorBanner error={error} />}
      {!isLoading && !error && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <select
              aria-label="Source"
              value={source}
              onChange={(event) => setSource(event.target.value)}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
            >
              <option value="">All sources</option>
              {sourceOptions(groups).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <input
              type="search"
              aria-label="Search scenarios"
              placeholder="Search systems and scenario text…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
            />
            <select
              aria-label="Copy selection from"
              value=""
              onChange={(event) => {
                const experiment = copyOptions.find((option) => String(option.id) === event.target.value);
                if (experiment) setSelected(new Set(experiment.label_scenarios.map(Number)));
              }}
              className="rounded-md border border-gray-300 px-2 py-1 text-xs shadow-sm"
            >
              <option value="">Copy selection from…</option>
              {copyOptions.map((experiment) => (
                <option key={experiment.id} value={experiment.id}>
                  {experiment.name}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-[14rem_1fr]">
            <div className="space-y-2">
              <ul aria-label="Systems" className="max-h-96 divide-y divide-gray-100 overflow-y-auto rounded-md border border-gray-200">
                {entries.map((entry) => {
                  const chosen = entry.rows.filter((row) => selected.has(row.id)).length;
                  return (
                    <li key={entry.id}>
                      <button
                        type="button"
                        onClick={() => setActive(entry.id)}
                        aria-current={active === entry.id}
                        className={`flex w-full items-start justify-between gap-2 px-2 py-1.5 text-left text-xs ${active === entry.id ? 'bg-brand-50' : 'hover:bg-gray-50'}`}
                      >
                        <span className="min-w-0">
                          <span className="block truncate font-medium text-gray-900">{entry.title}</span>
                          {entry.origin === 'generated' && <span className="text-gray-500">generated</span>}
                        </span>
                        <span className="shrink-0 text-gray-500">
                          {chosen}/{entry.rows.length}
                        </span>
                      </button>
                    </li>
                  );
                })}
                {drafts.systems.map((system) => (
                  <li key={system.key}>
                    <button
                      type="button"
                      onClick={() => setActive(system.key)}
                      aria-current={active === system.key}
                      className={`flex w-full justify-between gap-2 px-2 py-1.5 text-left text-xs ${active === system.key ? 'bg-brand-50' : 'hover:bg-gray-50'}`}
                    >
                      <span className="truncate font-medium text-gray-900">{system.title || 'Untitled system'}</span>
                      <span className="shrink-0 text-gray-500">new</span>
                    </button>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={addDraftSystem}
                className="w-full rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
              >
                + New system
              </button>
            </div>

            <div className="max-h-[28rem] space-y-3 overflow-y-auto">
              {active == null && <p className="text-xs text-gray-500">Choose a system to see its scenarios.</p>}

              {activeEntry && (
                <>
                  <h3 className="text-sm font-semibold text-gray-900">{activeEntry.title}</h3>
                  {rowsBySource(activeEntry.rows).map((group) => {
                    const ids = group.rows.map((row) => row.id);
                    const chosen = ids.filter((id) => selected.has(id)).length;
                    return (
                      <fieldset key={group.key} className="rounded-md border border-gray-200">
                        <legend className="sr-only">{group.title}</legend>
                        <label className="flex items-center gap-2 bg-gray-50 px-2 py-1.5 text-xs font-medium text-gray-900">
                          <input
                            type="checkbox"
                            aria-label={`Select all in ${group.title}`}
                            checked={chosen > 0 && chosen === ids.length}
                            ref={(element) => {
                              if (element) element.indeterminate = chosen > 0 && chosen < ids.length;
                            }}
                            onChange={(event) => toggle(ids, event.target.checked)}
                          />
                          {group.title} · {plural(ids.length, 'scenario')}
                        </label>
                        <ul className="divide-y divide-gray-100">
                          {group.rows.map((row) => (
                            <li key={row.id} className="px-2 py-1.5">
                              <label className="flex items-start gap-2 text-xs text-gray-700">
                                <input
                                  type="checkbox"
                                  checked={selected.has(row.id)}
                                  onChange={(event) => toggle([row.id], event.target.checked)}
                                  className="mt-0.5"
                                />
                                <span className="min-w-0">
                                  <span className="font-mono font-medium text-gray-900">{row.scenario_id}</span>
                                  <span className="block truncate text-gray-500">
                                    {[row.threat, row.outcome].filter(Boolean).join(' — ') || row.narrative}
                                  </span>
                                </span>
                              </label>
                            </li>
                          ))}
                        </ul>
                      </fieldset>
                    );
                  })}
                  {activeEntry.origin !== 'generated' && (
                    <>
                      {drafts.scenarios.map((draft, index) =>
                        draft.systemId === activeEntry.id ? (
                          <DraftScenarioFields
                            key={draft.key}
                            draft={draft}
                            errors={draftErrors(saveErrors, ['scenarios', index])}
                            onChange={(next) => updateExistingDraft(draft.key, next)}
                            onRemove={() => removeExistingDraft(draft.key)}
                          />
                        ) : null
                      )}
                      <button
                        type="button"
                        onClick={() => addDraftToExisting(activeEntry)}
                        className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
                      >
                        + Add scenario
                      </button>
                    </>
                  )}
                </>
              )}

              {activeDraftIndex >= 0 && (() => {
                const system = drafts.systems[activeDraftIndex];
                const errors = draftErrors(saveErrors, ['systems', activeDraftIndex]);
                return (
                  <div className="space-y-2">
                    <label className="block text-xs font-medium text-gray-700">
                      System title
                      <input
                        value={system.title}
                        onChange={(event) => updateDraftSystem(activeDraftIndex, { title: event.target.value })}
                        className={inputClass}
                      />
                      <FieldMessages messages={errors.title} />
                    </label>
                    <label className="block text-xs font-medium text-gray-700">
                      System description
                      <textarea
                        rows={4}
                        value={system.body}
                        onChange={(event) => updateDraftSystem(activeDraftIndex, { body: event.target.value })}
                        className={inputClass}
                      />
                      <FieldMessages messages={errors.body} />
                    </label>
                    {system.scenarios.map((draft, index) => (
                      <DraftScenarioFields
                        key={draft.key}
                        draft={draft}
                        errors={draftErrors(saveErrors, ['systems', activeDraftIndex, 'scenarios', index])}
                        onChange={(next) =>
                          updateDraftSystem(activeDraftIndex, {
                            scenarios: system.scenarios.map((item) => (item.key === draft.key ? next : item)),
                          })
                        }
                        onRemove={() =>
                          updateDraftSystem(activeDraftIndex, {
                            scenarios: system.scenarios.filter((item) => item.key !== draft.key),
                          })
                        }
                      />
                    ))}
                    <FieldMessages messages={Array.isArray(errors.scenarios) ? null : errors.scenarios} />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          updateDraftSystem(activeDraftIndex, {
                            scenarios: [
                              ...system.scenarios,
                              emptyDraftScenario(nextScenarioId(system.scenarios.map((item) => item.scenario_id))),
                            ],
                          })
                        }
                        className="rounded-md border border-gray-300 px-2 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
                      >
                        + Add scenario
                      </button>
                      <button
                        type="button"
                        onClick={() => removeDraftSystem(activeDraftIndex)}
                        className="rounded-md px-2 py-1 text-xs font-medium text-red-700 hover:underline"
                      >
                        Remove system
                      </button>
                    </div>
                  </div>
                );
              })()}

              {hasDrafts && (
                <p className="text-xs text-gray-500">New systems and scenarios are saved to the corpus when you apply.</p>
              )}
            </div>
          </div>

          {bannerError && <ErrorBanner error={bannerError} />}

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-200 pt-3">
            <span className="text-xs text-gray-600">
              {`${plural(scenarioCount, 'scenario')} from ${plural(selectedSystems.size, 'system')} selected`}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-100"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleApply}
                disabled={bulkCreate.isPending}
                className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-60"
              >
                {bulkCreate.isPending ? 'Saving…' : 'Apply'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
```

Note on the footer test "3 scenarios from 2 systems selected": `plural` returns `3 scenarios` / `2 systems`, matching.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test -- --run src/components/__tests__/LabelScenariosModal.test.jsx`
Expected: PASS. If a query by accessible name fails because of how a label wraps its text, fix the markup (for example add `htmlFor`/`id` pairs), not the test's intent.

---

### Task 9: Experiment builder, phase labels and experiment detail

**Files:**
- Modify: `frontend/src/pages/ExperimentBuilder.jsx` (`emptyForm` ~30–53, `formFromExperiment` ~61–87, `handleSave` ~296–320, Phases fieldset ~390–412, conditional sections)
- Modify: `frontend/src/components/PhasesLabel.jsx`
- Modify: `frontend/src/pages/ExperimentDetail.jsx:295-298`
- Test: `frontend/src/pages/__tests__/ExperimentBuilder.test.jsx`

**Interfaces:**
- Consumes: `LabelScenariosModal` (Task 8), `useScenarios`.
- Produces: form state `runPhase2: boolean`, `labelScenarioIds: number[]`; payload `phases` and `label_scenarios`.

- [ ] **Step 1: Rewrite the existing phase test and add new ones**

In `frontend/src/pages/__tests__/ExperimentBuilder.test.jsx`, replace the test `'always runs phase 2; unticking phase 3 hides its section and saves phases [2]'` with:

```jsx
  it('unticking phase 3 hides its section and saves phases [2]', async () => {
    const user = userEvent.setup();
    let posted = null;
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          posted = await request.json();
          return HttpResponse.json({ phases: ['Some phases error.'] }, { status: 400 });
        })
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    const phase3 = await screen.findByRole('checkbox', { name: /phase 3 — label scenarios/i });
    expect(phase3).toBeChecked();
    await fillMinimalValidForm(user);
    await user.click(phase3);
    expect(screen.queryByLabelText(/^phase 3 prompt$/i)).not.toBeInTheDocument();
    expect(screen.getByText(/only generates scenarios; nothing is labelled/i)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /create experiment/i }));
    expect(await screen.findByText(/some phases error/i)).toBeInTheDocument();
    expect(posted.phases).toEqual([2]);
    expect(posted.label_scenarios).toEqual([]);
  });

  it('phase 3 only hides generation settings, picks scenarios in the modal, and saves them', async () => {
    const user = userEvent.setup();
    let posted = null;
    server.use(
      http.get('/api/scenarios/', () =>
        HttpResponse.json(
          paginated([
            { id: 1, system_description: 10, scenario_id: 'sc_01', origin: 'seed', generation_run: null, threat: 'Remote login' },
            { id: 2, system_description: 10, scenario_id: 'sc_02', origin: 'seed', generation_run: null, threat: 'Insider' },
          ])
        )
      ),
      http.get('/api/phase-runs/', () => HttpResponse.json(paginated([]))),
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(async ({ request }) => {
          posted = await request.json();
          return HttpResponse.json({ phase3_model: ['Stop here.'] }, { status: 400 });
        })
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });

    await user.click(await screen.findByRole('checkbox', { name: /phase 2 — generate scenarios/i }));
    expect(screen.queryByLabelText(/^phase 2 model$/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /seed systems/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /what to generate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: /variants/i })).not.toBeInTheDocument();
    expect(screen.getByText(/labels the scenarios you choose below/i)).toBeInTheDocument();
    expect(screen.getByText('No scenarios chosen yet.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /choose scenarios/i }));
    const dialog = await screen.findByRole('dialog', { name: /choose scenarios to label/i });
    const systems = await within(dialog).findByRole('list', { name: /systems/i });
    await user.click(within(systems).getByRole('button', { name: /oldsmar water plant/i }));
    await user.click(within(dialog).getByRole('checkbox', { name: /select all in seed & hand-written/i }));
    await user.click(within(dialog).getByRole('button', { name: /^apply$/i }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByText('2 scenarios · 1 system')).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^name$/i), 'Labellers');
    await user.click(screen.getByRole('button', { name: /create experiment/i }));
    expect(await screen.findByText('Stop here.')).toBeInTheDocument();
    expect(posted.phases).toEqual([3]);
    expect(posted.label_scenarios).toEqual([1, 2]);
  });

  it('shows a label_scenarios field error under the selection', async () => {
    const user = userEvent.setup();
    server.use(
      http.post(
        '/api/experiments/',
        guardedExperimentWrite(() =>
          HttpResponse.json({ label_scenarios: ['Choose at least one scenario to label.'] }, { status: 400 })
        )
      )
    );
    renderApp({ initialEntries: ['/experiments/new'] });
    await user.click(await screen.findByRole('checkbox', { name: /phase 2 — generate scenarios/i }));
    await user.click(screen.getByRole('button', { name: /create experiment/i }));
    expect(await screen.findByText('Choose at least one scenario to label.')).toBeInTheDocument();
  });
```

MSW is set to `onUnhandledRequest: 'error'` (`test/setup.js`), so every endpoint the builder and modal call must be mocked. `installBuilderFixtures` already mocks `/api/prompts/` and `/api/system-descriptions/` (`SYSTEMS_SEED`), and `test/server.js` mocks `/api/experiments/` and `/api/pipeline-runs/`. The second test above adds `/api/scenarios/` and `/api/phase-runs/`; the third test must add the same two handlers returning `paginated([])`, because phase-3-only mode fetches scenarios. The scenario rows use system id 10, `SYSTEMS_SEED`'s "Oldsmar water plant".

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- --run src/pages/__tests__/ExperimentBuilder.test.jsx`
Expected: FAIL on the three new/rewritten tests.

- [ ] **Step 3: Update form state**

In `emptyForm`, add after `taxonomy`/before `phase2Model` (keep `runPhase3` where it is):

```js
  runPhase2: true,
  labelScenarioIds: [],
```

In `formFromExperiment`, add:

```js
    runPhase2: (experiment.phases ?? [2, 3]).includes(2),
    labelScenarioIds: (experiment.label_scenarios ?? []).map(Number),
```

- [ ] **Step 4: Update the payload**

In `handleSave`, before `const payload = {`, add (`labelsOnly` and `visibleFewshots` are defined at component level in Step 5):

```js
    const visibleFewshotIds = new Set(visibleFewshots.map((fewshot) => String(fewshot.id)));
```

and change these payload entries:

```js
      phases: [...(form.runPhase2 ? [2] : []), ...(form.runPhase3 ? [3] : [])],
      fewshots: form.fewshotIds.filter((fewshotId) => visibleFewshotIds.has(fewshotId)).map(Number),
      label_scenarios: labelsOnly ? form.labelScenarioIds : [],
```

- [ ] **Step 5: Add derived values and the modal state**

Add imports at the top of `ExperimentBuilder.jsx`:

```js
import LabelScenariosModal from '../components/LabelScenariosModal';
```

and add `useScenarios` to the existing `../api/queries` import.

The component returns early (around lines 225 and 240: non-staff notice, loading an existing experiment), so the new hooks must sit **above the first early return**, next to the other `use…` calls near the top of `ExperimentBuilder`. Add there:

```js
  const [pickerOpen, setPickerOpen] = useState(false);
  const labelsOnly = !form.runPhase2 && form.runPhase3;
  const labelScenariosQuery = useScenarios({}, { enabled: labelsOnly });
```

Then, after `const selectableSystems = ...` (plain values, no hooks), add:

```js
  // A phase-3-only experiment only uses phase 3 few-shots.
  const visibleFewshots = form.runPhase2 ? fewshots : fewshots.filter((fewshot) => fewshot.phase === 3);
  const scenarioSystem = new Map((labelScenariosQuery.data?.results ?? []).map((row) => [row.id, row.system_description]));
  const chosenSystemCount = new Set(
    form.labelScenarioIds.map((scenarioId) => scenarioSystem.get(scenarioId)).filter((systemId) => systemId != null)
  ).size;
```

`useState` is already imported in this file.

- [ ] **Step 6: Replace the Phases fieldset**

Replace the whole `<fieldset>` whose legend is `Phases` with:

```jsx
        <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
          <legend className="px-1 text-sm font-semibold text-gray-900">Phases</legend>
          <label htmlFor="phase-2" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="phase-2"
              type="checkbox"
              checked={form.runPhase2}
              onChange={(event) => updateField('runPhase2', event.target.checked)}
            />
            Phase 2 — generate scenarios
          </label>
          <label htmlFor="phase-3" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="phase-3"
              type="checkbox"
              checked={form.runPhase3}
              onChange={(event) => updateField('runPhase3', event.target.checked)}
            />
            Phase 3 — label scenarios
          </label>
          <p className="text-xs text-gray-500">
            {form.runPhase2 && form.runPhase3 && 'Each repetition generates scenarios, then labels them against the taxonomy.'}
            {form.runPhase2 && !form.runPhase3 && 'Each repetition only generates scenarios; nothing is labelled.'}
            {labelsOnly && 'Each repetition labels the scenarios you choose below; nothing is generated.'}
            {!form.runPhase2 && !form.runPhase3 && 'Choose at least one phase.'}
          </p>
          <FieldError message={fieldErrors.phases} />
        </fieldset>
```

- [ ] **Step 7: Hide generation sections and add the selection fieldset**

Wrap each of these existing blocks in `{form.runPhase2 && ( ... )}`: the `Phase 2 — generation` fieldset, the `Seed systems` fieldset, the `What to generate` fieldset, and the `Variants` fieldset.

In the `Few-shots` fieldset, change `{fewshots.map((fewshot) => (` to `{visibleFewshots.map((fewshot) => (`.

Directly after the `Phase 3 — labelling` fieldset block, add:

```jsx
        {labelsOnly && (
          <fieldset className="space-y-2 rounded-md border border-gray-200 p-3">
            <legend className="px-1 text-sm font-semibold text-gray-900">Scenarios to label</legend>
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-sm text-gray-700">
                {form.labelScenarioIds.length === 0
                  ? 'No scenarios chosen yet.'
                  : `${form.labelScenarioIds.length} ${form.labelScenarioIds.length === 1 ? 'scenario' : 'scenarios'} · ${chosenSystemCount} ${chosenSystemCount === 1 ? 'system' : 'systems'}`}
              </span>
              <button
                type="button"
                onClick={() => setPickerOpen(true)}
                className="rounded-md border border-gray-300 px-3 py-1 text-sm font-medium text-gray-700 hover:bg-gray-100"
              >
                {form.labelScenarioIds.length === 0 ? 'Choose scenarios…' : 'Edit'}
              </button>
            </div>
            <FieldError message={fieldErrors.label_scenarios} />
          </fieldset>
        )}
        {pickerOpen && (
          <LabelScenariosModal
            initialIds={form.labelScenarioIds}
            excludeExperimentId={isEditing ? Number(id) : null}
            onApply={(ids) => {
              updateField('labelScenarioIds', ids);
              setPickerOpen(false);
            }}
            onClose={() => setPickerOpen(false)}
          />
        )}
```

The test's `/choose scenarios/i` button name matches "Choose scenarios…".

- [ ] **Step 8: Update `PhasesLabel` and the experiment detail**

Replace `frontend/src/components/PhasesLabel.jsx` with:

```jsx
// An experiment or pinned snapshot's `phases`: [2], [2, 3] or [3]. Missing means both
// (every run launched before the field existed ran both phases).
export default function PhasesLabel({ phases }) {
  const list = phases ?? [2, 3];
  if (!list.includes(2)) return 'Phase 3 only (label chosen scenarios)';
  return list.includes(3) ? 'Phase 2 (generate) → Phase 3 (label)' : 'Phase 2 only (generate, no labelling)';
}
```

In `frontend/src/pages/ExperimentDetail.jsx`, replace:

```jsx
          <div>
            <dt className="font-medium text-gray-500">Variants</dt>
            <dd>{(experiment.variants ?? []).join(', ') || '(none)'}</dd>
          </div>
```

with:

```jsx
          {(experiment.phases ?? [2, 3]).includes(2) ? (
            <div>
              <dt className="font-medium text-gray-500">Variants</dt>
              <dd>{(experiment.variants ?? []).join(', ') || '(none)'}</dd>
            </div>
          ) : (
            <div>
              <dt className="font-medium text-gray-500">Scenarios to label</dt>
              <dd>{(experiment.label_scenarios ?? []).length}</dd>
            </div>
          )}
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `cd frontend && npm test -- --run src/pages/__tests__/ExperimentBuilder.test.jsx src/pages/__tests__/ExperimentDetail.test.jsx src/pages/__tests__/RunDetail.test.jsx`
Expected: PASS. Any other builder test that relied on phase 2 being a disabled checkbox gets the same update: phase 2 is now a normal checkbox, ticked by default.

---

### Task 10: Labels page for phase-3-only runs

**Files:**
- Modify: `frontend/src/pages/Labels.jsx` (`VariantMatrix` ~124–270, `Labels` ~272–422)
- Test: `frontend/src/pages/__tests__/Labels.test.jsx`

**Interfaces:**
- Consumes: `GET /api/scenarios/?labelled_in_run=<id>` (Task 6); `PipelineRun.config.phases`.
- Produces: `LabelMatrix({ testId, heading, what, scenarioParams, phaseRunParams, labelParams, noScenariosTitle, noScenariosMessage, hcvFilter, taxonomies, activeTaxonomy, systemTitleById })`; `VariantMatrix` becomes a wrapper.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/pages/__tests__/Labels.test.jsx`, inside its top-level `describe` (reuse the file's existing taxonomy/HCV handlers; add these runs and rows in `server.use`):

```jsx
  it('shows one matrix of the chosen scenarios for a phase-3-only run, without the variant filter', async () => {
    server.use(
      http.get('/api/pipeline-runs/', () =>
        HttpResponse.json(paginated([{ id: 90, experiment: 9, repetition_index: 1, status: 'completed', config: { phases: [3], label_scenario_ids: [5] } }]))
      ),
      http.get('/api/experiments/', () => HttpResponse.json(paginated([{ id: 9, name: 'Labeller GPT' }]))),
      http.get('/api/phase-runs/', () =>
        HttpResponse.json(paginated([{ id: 91, pipeline_run: 90, phase: 3, variant: '', taxonomy: 2, status: 'completed' }]))
      ),
      http.get('/api/scenarios/', ({ request }) => {
        const url = new URL(request.url);
        if (url.searchParams.get('labelled_in_run') !== '90') return HttpResponse.json(paginated([]));
        return HttpResponse.json(
          paginated([{ id: 5, system_description: 1, scenario_id: 'sc_05', origin: 'seed', generation_run: null }])
        );
      })
    );
    renderApp({ initialEntries: ['/labels?run=90'] });

    const matrix = await screen.findByTestId('chosen-matrix');
    expect(within(matrix).getByRole('heading', { name: '1 chosen scenario' })).toBeInTheDocument();
    expect(await within(matrix).findByRole('rowheader', { name: /sc_05/ })).toBeInTheDocument();
    expect(screen.queryByTestId('variant-A-matrix')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/^variant$/i)).not.toBeInTheDocument();
  });

  it('renders the A/B matrices for a run whose config has no phases', async () => {
    server.use(
      http.get('/api/pipeline-runs/', () =>
        HttpResponse.json(paginated([{ id: 80, experiment: 9, repetition_index: 1, status: 'completed', config: {} }]))
      ),
      http.get('/api/experiments/', () => HttpResponse.json(paginated([{ id: 9, name: 'Old run' }])))
    );
    renderApp({ initialEntries: ['/labels?run=80'] });
    expect(await screen.findByTestId('variant-A-matrix')).toBeInTheDocument();
    expect(screen.getByTestId('variant-B-matrix')).toBeInTheDocument();
    expect(screen.queryByTestId('chosen-matrix')).not.toBeInTheDocument();
  });
```

If this file names its taxonomy fixture id differently than `2`, use that id in the phase run's `taxonomy`. The system with id 1 in the file's system-descriptions fixture must exist; add `{ id: 1, title: 'Oldsmar' }` if it does not.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- --run src/pages/__tests__/Labels.test.jsx`
Expected: FAIL, no `chosen-matrix`.

- [ ] **Step 3: Generalise `VariantMatrix` into `LabelMatrix`**

Rename `function VariantMatrix({ variant, runId, hcvFilter, taxonomies, activeTaxonomy, systemTitleById })` to:

```jsx
// One scenario x HCV matrix over one set of scenarios and its labelling run(s).
function LabelMatrix({
  testId,
  heading,
  what,
  scenarioParams,
  phaseRunParams,
  labelParams,
  noScenariosTitle,
  noScenariosMessage,
  hcvFilter,
  taxonomies,
  activeTaxonomy,
  systemTitleById,
}) {
```

Inside it, make these replacements:
- `useScenarios({ variant, run: runId })` → `useScenarios(scenarioParams)`
- `usePhaseRuns({ pipeline_run: runId, variant, phase: 3 })` → `usePhaseRuns(phaseRunParams)`
- `{ run: runId, variant, ...(hcvFilter ? { hcv: hcvFilter } : {}) }` → `{ ...labelParams, ...(hcvFilter ? { hcv: hcvFilter } : {}) }`
- `aria-labelledby={`variant-${variant}-heading`} data-testid={`variant-${variant}-matrix`}` → `aria-labelledby={`${testId}-heading`} data-testid={testId}`
- `<h2 id={`variant-${variant}-heading`} ...>Variant {variant}</h2>` → `<h2 id={`${testId}-heading`} ...>{heading}</h2>`
- `Loading variant {variant}…` → `Loading {what}…`
- `the taxonomy this variant&apos;s labelling run used` → `the taxonomy {what}&apos;s labelling run used`
- `title="This variant used more than one taxonomy version"` → `` title={`${heading} used more than one taxonomy version`} ``
- `` title={`No labelling run for variant ${variant}`} `` → `` title={`No labelling run for ${what}`} ``
- `message="This run may not have reached phase 3 for this variant yet."` → `` message={`This run may not have reached phase 3 for ${what} yet.`} ``
- `` title={`No variant ${variant} scenarios for this run`} `` → `title={noScenariosTitle}`
- `message="This run may not have generated scenarios for this variant yet."` → `message={noScenariosMessage}`

Then add the two wrappers after it:

```jsx
function VariantMatrix({ variant, runId, ...shared }) {
  return (
    <LabelMatrix
      testId={`variant-${variant}-matrix`}
      heading={`Variant ${variant}`}
      what={`variant ${variant}`}
      scenarioParams={{ variant, run: runId }}
      phaseRunParams={{ pipeline_run: runId, variant, phase: 3 }}
      labelParams={{ run: runId, variant }}
      noScenariosTitle={`No variant ${variant} scenarios for this run`}
      noScenariosMessage="This run may not have generated scenarios for this variant yet."
      {...shared}
    />
  );
}

// A phase-3-only run labels chosen corpus scenarios, so there is one matrix and no variant.
function ChosenMatrix({ runId, chosenCount, ...shared }) {
  return (
    <LabelMatrix
      testId="chosen-matrix"
      heading={`${chosenCount} chosen ${chosenCount === 1 ? 'scenario' : 'scenarios'}`}
      what="this run"
      scenarioParams={{ labelled_in_run: runId }}
      phaseRunParams={{ pipeline_run: runId, phase: 3 }}
      labelParams={{ run: runId }}
      noScenariosTitle="The chosen scenarios were deleted"
      noScenariosMessage="None of the scenarios this run was launched with still exist."
      {...shared}
    />
  );
}
```

`chosenCount` comes from the run's frozen config (Step 4), so the heading renders before the scenarios load. When the scenarios query has loaded, it equals the row count unless rows were deleted.

- [ ] **Step 4: Choose the layout in `Labels`**

After `const runId = ...`, add:

```js
  const selectedRun = (pipelineRunsQuery.data?.results ?? []).find((run) => run.id === runId) ?? null;
  const runPhases = selectedRun?.config?.phases;
  const labelsOnlyRun = Array.isArray(runPhases) && runPhases.length === 1 && runPhases[0] === 3;
  const chosenCount = selectedRun?.config?.label_scenario_ids?.length ?? 0;
```

Wrap the Variant filter `<div>` (the one containing `labels-filter-variant`) in `{!labelsOnlyRun && ( ... )}`.

Replace the `{runId != null && ( ... )}` matrices block with:

```jsx
      {runId != null && (
        <div className="space-y-8">
          {labelsOnlyRun ? (
            <ChosenMatrix
              runId={runId}
              chosenCount={chosenCount}
              hcvFilter={filters.hcv}
              taxonomies={taxonomies}
              activeTaxonomy={activeTaxonomy}
              systemTitleById={systemTitleById}
            />
          ) : (
            variantsToShow.map((variant) => (
              <VariantMatrix
                key={variant}
                variant={variant}
                runId={runId}
                hcvFilter={filters.hcv}
                taxonomies={taxonomies}
                activeTaxonomy={activeTaxonomy}
                systemTitleById={systemTitleById}
              />
            ))
          )}
        </div>
      )}
```

Update the page intro paragraph to cover both layouts:

```jsx
          One scenario × HCV matrix per variant, or a single matrix for a run that labels chosen
          scenarios. Variant A and variant B generate different scenarios by design — read the two
          matrices side by side rather than expecting matching rows between them. This screen
          presents what was labelled; drawing a conclusion from it is the researcher&apos;s job, not
          this page&apos;s.
```


- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npm test -- --run src/pages/__tests__/Labels.test.jsx`
Expected: PASS, including every pre-existing Labels test (their headings, test ids and messages are unchanged for variant matrices).

---

### Task 11: Full verification

**Files:** none new.

- [ ] **Step 1: Backend suite and migration check**

Run: `cd backend && uv run pytest -q && uv run python manage.py makemigrations --check --dry-run`
Expected: all tests PASS; "No changes detected".

- [ ] **Step 2: Frontend suite and lint**

Run: `cd frontend && npm test -- --run && npm run lint`
Expected: PASS, no lint errors.

- [ ] **Step 3: Manual run through the app**

With Redis, `runserver`, `qcluster` and `npm run dev` running (see README), sign in as staff and:
1. Create an experiment, untick Phase 2, choose scenarios in the modal (including one new system with one scenario), save.
2. Launch one repetition and wait for it to finish.
3. Open the run, then "View labels": one matrix titled "N chosen scenarios".
4. Export the run and confirm the chosen scenarios and their system are in the bundle.

Report what you saw. If you can't run the app, say so rather than claiming it works.

- [ ] **Step 4: Leave everything uncommitted**

Run: `git status --short`
Expected: modified and new files listed; nothing committed.
