"""Direct, unit-level coverage of `execution_config.py`'s validation logic —
complementing the end-to-end proofs in `test_generation.py`/
`test_orchestration.py` (which exercise the same code through `run_phase2`/
`execute_pipeline_run`) with fast, focused checks of `validate_snapshot`
itself: every way a snapshot can fail to match the shape `config_snapshot`
produces, and that a well-formed one round-trips cleanly.
"""

import copy

import pytest
from django.db.models import ProtectedError

from corpus.models import FewShotExample, Origin, ResourceFile, ResourceKind, SystemDescription
from pipeline.execution_config import (
    _ADHOC_GENERATION_CONFIG,
    BorrowedExperimentContaminated,
    BorrowedExperimentMisconfigured,
    MalformedSnapshot,
    UnboundGuardTaxonomy,
    resolve_generation_config,
    resolve_labelling_fewshots,
    validate_snapshot,
)
from pipeline.models import Experiment, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

_VALID_SYSTEM = {"id": 1, "title": "Oldsmar", "slug": "oldsmar", "body": "b", "scenarios": []}
_VALID_FEWSHOT = {
    "id": 1,
    "name": "ex",
    "phase": 2,
    "is_active": True,
    "input": "in",
    "output": "out",
    "input_file_id": None,
    "output_file_id": None,
    "input_resource_kind": None,
    "output_resource_kind": None,
}
_VALID_CONFIG = {
    "phases": [2, 3],
    "seed_systems": [_VALID_SYSTEM],
    "fewshots": [_VALID_FEWSHOT],
    "target_system": None,
    "generate_systems": True,
    "generate_scenarios": True,
    "n_systems": 1,
    "n_scenarios_per_system": 1,
    "label_scenario_ids": [],
}


def test_a_well_formed_snapshot_validates_cleanly():
    """The positive case: `validate_snapshot` must not raise on the exact
    shape `config_snapshot` actually produces — otherwise every real launch
    would fail closed too, not just a malformed one."""
    validate_snapshot(copy.deepcopy(_VALID_CONFIG))  # must not raise


def test_a_bare_empty_dict_is_malformed():
    """`PipelineRun.config`'s own field default — "never snapshotted" —
    must fail the same way any other malformed shape does, not be read as
    "an ad-hoc run with nothing to snapshot" (a different, legitimate case
    checked separately, before `validate_snapshot` is ever called)."""
    with pytest.raises(MalformedSnapshot):
        validate_snapshot({})


def test_a_fewshot_missing_input_resource_kind_is_malformed():
    """THE reviewer's exact probe: a few-shot dict lacking
    `input_resource_kind` used to be read with a permissive `.get()`
    default that silently disabled the variant-A taxonomy-resource guard.
    """
    config = copy.deepcopy(_VALID_CONFIG)
    del config["fewshots"][0]["input_resource_kind"]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_fewshot_missing_output_resource_kind_is_malformed():
    """The other half of the same guard-relevant pair."""
    config = copy.deepcopy(_VALID_CONFIG)
    del config["fewshots"][0]["output_resource_kind"]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_fewshot_missing_is_active_is_malformed():
    """`is_active` is what lets a snapshotted-but-inactive few-shot be
    correctly excluded at read time; missing it is exactly the same class
    of gap as missing a resource-kind field."""
    config = copy.deepcopy(_VALID_CONFIG)
    del config["fewshots"][0]["is_active"]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_missing_top_level_key_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    del config["n_systems"]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_an_extra_top_level_key_is_malformed():
    """An unrecognised shape — even a superset of the expected one — must
    also fail closed, not be "partially understood". Mirrors
    `prompts.rendering._is_phase3_context`'s own exact-match posture."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["unexpected_field"] = "surprise"
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_seed_system_missing_a_key_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    del config["seed_systems"][0]["slug"]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_seed_scenario_missing_a_key_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["seed_systems"][0]["scenarios"] = [
        {
            "scenario_id": "s1",
            "actor": "a",
            "trigger": "t",
            "standing_state": "s",
            "asset": "as",
            "threat": "th",
            "outcome": "o",
            "narrative": "n",
            "intended_hcvs": [],
            # "abox" deliberately omitted
        }
    ]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_target_system_missing_a_key_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["target_system"] = {"id": 2, "title": "Target", "body": "b"}  # missing slug, scenarios
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_generation_only_snapshot_validates_cleanly():
    config = copy.deepcopy(_VALID_CONFIG)
    config["phases"] = [2]
    validate_snapshot(config)  # must not raise


def test_a_snapshot_missing_phases_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    del config["phases"]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


@pytest.mark.parametrize(
    "phases", [[], [3, 2], [2, 2], [2, 3, 4], ["2", "3"], [True], (2, 3), "2,3", None]
)
def test_a_lying_phases_value_is_malformed(phases):
    """Only the shapes launch can pin — [2], [2, 3] and [3] — are accepted."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["phases"] = phases
    with pytest.raises(MalformedSnapshot, match="phases"):
        validate_snapshot(config)


def test_a_non_dict_config_is_malformed():
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(["not", "a", "dict"])


def test_a_bare_empty_dict_says_it_predates_the_snapshot_not_corruption():
    """Round 6 review, finding 4: a run predating this migration reads
    `config == {}`, which is deliberate and must still fail closed — but the
    message used to be the same generic "does not match the shape" wording
    as any other malformed shape, which reads like corruption. This is the
    single most likely malformed config to occur in practice (an ordinary
    pre-migration row), so it gets its own, less alarming explanation."""
    with pytest.raises(MalformedSnapshot, match="predates the config-snapshot feature"):
        validate_snapshot({})


@pytest.mark.parametrize("lying_value", ["", "prose", "TAXONOMY", 0, False])
def test_a_lying_input_resource_kind_is_malformed(lying_value):
    """Round 6 review, finding 1 (the blocker): shape-only validation (the
    previous round's fix) checks only that `input_resource_kind` is
    PRESENT, not that its value is honest. None of these five values is a
    real `ResourceKind` choice, and none is the legitimate `None` either —
    `prompts.rendering._fewshot_rows`' variant-A guard read every one of
    them as "no file backing", which let a TAXONOMY-kind resource's text
    into a Run A prompt. See `test_generation.py` for the same proof run
    end-to-end through `run_phase2`."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input_resource_kind"] = lying_value
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


@pytest.mark.parametrize("lying_value", ["", "prose", "TAXONOMY", 0, False])
def test_a_lying_output_resource_kind_is_malformed(lying_value):
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["output_resource_kind"] = lying_value
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_null_resource_kind_remains_legitimately_valid():
    """`_require_resource_kind` deliberately does NOT reject a bare `None`
    on its own: it is exactly what `config_snapshot` produces for a
    few-shot with no file backing at all. Here `input_file_id`/
    `output_file_id` are also `None` (from `_VALID_FEWSHOT`), so
    `_require_file_kind_pairing` agrees this is the honest "no file" case,
    not a lie — see `test_a_file_id_present_with_no_kind_recorded_is_malformed`
    below for the case this test is NOT: a file id present with the kind
    missing."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input_resource_kind"] = None
    config["fewshots"][0]["output_resource_kind"] = None
    validate_snapshot(config)  # must not raise


def test_a_file_id_present_with_no_kind_recorded_is_malformed():
    """The invariant the coordinator's follow-up closes: a lying bare
    `None` for `input_resource_kind` is not rejected by
    `_require_resource_kind` alone (see the test above), because it is
    indistinguishable from the honest "no file" case in isolation. But a
    file id present alongside it is the tell — `config_snapshot` writes
    both fields from the SAME `fewshot.input_file_id` (see its own
    docstring), so an honest snapshot can never have one without the
    other. This is precisely the shape of the original Run A leak: a
    writer that drops or nulls the kind while the file id survives."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input_file_id"] = 99
    config["fewshots"][0]["input_resource_kind"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_kind_recorded_with_no_file_id_is_malformed():
    """The other direction of the same pairing check: a kind recorded with
    no file id to back it — also not a shape `config_snapshot` can
    honestly produce."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input_file_id"] = None
    config["fewshots"][0]["input_resource_kind"] = ResourceKind.OTHER
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_the_output_side_file_kind_pairing_is_checked_too():
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["output_file_id"] = 99
    config["fewshots"][0]["output_resource_kind"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_consistent_file_id_and_kind_pairing_validates_cleanly():
    """The positive case: a real file id paired with its real kind — the
    honest, common shape for a file-backed few-shot — must not raise."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input_file_id"] = 99
    config["fewshots"][0]["input_resource_kind"] = ResourceKind.TAXONOMY
    validate_snapshot(config)  # must not raise


def test_a_non_int_file_id_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input_file_id"] = "99"
    config["fewshots"][0]["input_resource_kind"] = ResourceKind.TAXONOMY
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_fewshot_input_that_is_not_a_string_is_malformed():
    """The reviewer's probe: `input=None` was silently accepted by
    shape-only validation (the key is present)."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_fewshot_input_that_is_a_list_is_malformed():
    """The reviewer's other probe: `input=["a"]` was also accepted."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["input"] = ["a"]
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_fewshot_output_that_is_not_a_string_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["output"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_fewshot_is_active_that_is_not_a_bool_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["is_active"] = 1
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_fewshot_phase_outside_two_or_three_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"][0]["phase"] = 4
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_system_body_that_is_not_a_string_is_malformed():
    """The reviewer's third probe: `body=None` was accepted."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["seed_systems"][0]["body"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_system_id_that_is_not_an_int_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["seed_systems"][0]["id"] = "1"
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_n_systems_that_is_not_an_int_is_malformed():
    """The reviewer's fourth probe: `n_systems="many"` was accepted."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["n_systems"] = "many"
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_generate_systems_that_is_not_a_bool_is_malformed():
    config = copy.deepcopy(_VALID_CONFIG)
    config["generate_systems"] = 1
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_seed_systems_set_to_none_raises_malformed_snapshot_not_typeerror():
    """Finding 1's last sub-point: `seed_systems`/`fewshots`/a system's
    `scenarios` set to `None` used to raise a bare `TypeError` from `for x in
    None`, not `MalformedSnapshot` — still fail-closed via `run_phase2`'s
    broad except, but an unhelpful crash rather than the real exception."""
    config = copy.deepcopy(_VALID_CONFIG)
    config["seed_systems"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_fewshots_set_to_none_raises_malformed_snapshot_not_typeerror():
    config = copy.deepcopy(_VALID_CONFIG)
    config["fewshots"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


def test_a_systems_scenarios_set_to_none_raises_malformed_snapshot_not_typeerror():
    config = copy.deepcopy(_VALID_CONFIG)
    config["seed_systems"][0]["scenarios"] = None
    with pytest.raises(MalformedSnapshot):
        validate_snapshot(config)


@pytest.mark.django_db
def test_resolve_labelling_fewshots_fails_closed_on_a_malformed_snapshot():
    """Round 6 review, finding 3: `resolve_labelling_fewshots` calls
    `validate_snapshot` itself, exactly like `resolve_generation_config`
    does — but nothing exercised that call directly, so removing it left the
    WHOLE suite green (it degrades to a bare `KeyError` from `_snapshot_fewshots`
    reading a key that was never checked, which is still fail-closed by
    accident rather than by guarantee). Proven directly here, not only
    through `run_phase2`'s end-to-end path, so a regression is caught by
    this file, not by a downstream symptom two calls away.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-EC", version="1.0", is_active=True)
    gen = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    experiment = Experiment.objects.create(
        name="e",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=gen,
        phase2_prompt_b=gen,
        phase3_model="m3",
        phase3_prompt=lab,
    )
    config = copy.deepcopy(_VALID_CONFIG)
    del config["n_systems"]  # malformed: missing a required top-level key
    pipeline_run = PipelineRun.objects.create(
        experiment=experiment, repetition_index=1, config=config
    )
    phase_run = PhaseRun(pipeline_run=pipeline_run, phase=3)  # unsaved: nothing else reads it

    with pytest.raises(MalformedSnapshot):
        resolve_labelling_fewshots(phase_run)


@pytest.mark.django_db
def test_a_labelling_run_under_a_generation_only_snapshot_is_refused():
    """A phase-3 row under a run pinned to phases [2] contradicts its own
    record, so it fails closed rather than labelling anything."""
    tax = Taxonomy.objects.create(vocabulary_id="HCV-P2", version="1.0", is_active=True)
    gen = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    experiment = Experiment.objects.create(
        name="gen-only", taxonomy=tax, phase2_model="m2", phase2_prompt_a=gen, phases=[2]
    )
    config = copy.deepcopy(_VALID_CONFIG)
    config["phases"] = [2]
    pipeline_run = PipelineRun.objects.create(
        experiment=experiment, repetition_index=1, config=config
    )
    phase_run = PhaseRun(pipeline_run=pipeline_run, phase=3)

    with pytest.raises(MalformedSnapshot, match="phases"):
        resolve_labelling_fewshots(phase_run)


@pytest.mark.django_db
def test_adhoc_config_defaults_are_not_shared_between_calls(taxonomy):
    """Finding 4, task 5 second review round: the ad-hoc branch used to
    return `dict(_ADHOC_GENERATION_CONFIG)` — a SHALLOW copy, so every
    ad-hoc run's `seed_systems`/`fewshots` were the SAME list objects as
    the module global (and each other) — the exact footgun `launch()`'s
    own `copy.deepcopy` exists to avoid for the orchestrated path. Now
    `copy.deepcopy`d here too: mutating one call's result must never affect
    another's, or the module global itself.

    No longer DB-free: this test predates `unbound_guard_taxonomy_error`
    (coordinator review round 3), which added a REAL `Taxonomy.active()`
    query even on the "no experiment" branch — the `taxonomy` fixture
    supplies that active row. The deep-copy property this test actually
    checks is otherwise unaffected; `taxonomy` is pinned only so the new
    binding check has something correct to pass against.
    """
    ad_hoc = PhaseRun(taxonomy=taxonomy)  # unsaved: no `pipeline_run`
    assert ad_hoc.pipeline_run_id is None

    config_a = resolve_generation_config(ad_hoc)
    config_b = resolve_generation_config(ad_hoc)
    config_a["seed_systems"].append("mutated")
    config_a["fewshots"].append("mutated")

    assert config_b["seed_systems"] == []
    assert config_b["fewshots"] == []
    # And a THIRD call proves the module global itself was never touched.
    config_c = resolve_generation_config(ad_hoc)
    assert config_c["seed_systems"] == []
    assert config_c["fewshots"] == []


# ---------------------------------------------------------------------------
# Ad-hoc runs borrowing an experiment's LIVE inputs (iterate mode).
# ---------------------------------------------------------------------------


@pytest.fixture
def taxonomy(db):
    # `is_active=True`: coordinator review round 3 added
    # `unbound_guard_taxonomy_error` — an ad-hoc run with no borrowed
    # experiment must be pinned to the ACTIVE taxonomy, not just any real
    # one. Matches this file's other fixtures (and the rest of the suite's
    # established convention) rather than tripping that new rule for tests
    # that are not about it.
    return Taxonomy.objects.create(vocabulary_id="HCV-21-BORROW", version="1.0", is_active=True)


@pytest.fixture
def templates(db):
    return (
        PromptTemplate.objects.create_version(phase=2, name="gen-borrow", user_prompt="g"),
        PromptTemplate.objects.create_version(phase=3, name="lab-borrow", user_prompt="l"),
    )


@pytest.fixture
def seed(db):
    return SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)


@pytest.fixture
def experiment(db, taxonomy, templates, seed):
    gen, lab = templates
    made = Experiment.objects.create(
        name="borrowable",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt_a=gen,
        phase2_prompt_b=gen,
        phase3_model="m3",
        phase3_prompt=lab,
        n_systems=7,
        n_scenarios_per_system=9,
    )
    made.seed_systems.add(seed)
    return made


def _adhoc_phase_run(templates, taxonomy, experiment=None, **overrides):
    gen, _ = templates
    return PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=gen,
        taxonomy=taxonomy,
        experiment=experiment,
        **overrides,
    )


@pytest.mark.django_db
def test_borrowing_an_experiment_reads_its_seed_systems_and_counts(
    templates, taxonomy, experiment, seed
):
    """The base case: no per-run overrides, so the borrowed experiment's own
    `n_systems`/`n_scenarios_per_system` and `seed_systems` come through
    untouched."""
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    config = resolve_generation_config(phase_run)

    assert [s.pk for s in config["seed_systems"]] == [seed.pk]
    assert config["n_systems"] == 7
    assert config["n_scenarios_per_system"] == 9
    assert config["generate_systems"] is True
    assert config["generate_scenarios"] is True
    assert config["target_system"] is None
    assert config["target_system_id"] is None


@pytest.mark.django_db
def test_direct_overrides_win_over_the_borrowed_experiments_own_counts(
    templates, taxonomy, experiment
):
    """The actual iterate-mode use case named by the researcher: real seeds
    and few-shots, but ONE system and ONE scenario — cheap and fast."""
    phase_run = _adhoc_phase_run(
        templates, taxonomy, experiment=experiment, n_systems=1, n_scenarios_per_system=1
    )

    config = resolve_generation_config(phase_run)

    assert config["n_systems"] == 1
    assert config["n_scenarios_per_system"] == 1


@pytest.mark.django_db
def test_borrowing_reads_the_experiments_active_phase_2_fewshots(templates, taxonomy, experiment):
    active = FewShotExample.objects.create(
        phase=2, name="active", input_text="in", output_text="out", is_active=True
    )
    inactive = FewShotExample.objects.create(
        phase=2, name="inactive", input_text="in2", output_text="out2", is_active=False
    )
    phase_3_only = FewShotExample.objects.create(
        phase=3, name="phase3", input_text="in3", output_text="out3", is_active=True
    )
    experiment.fewshots.add(active, inactive, phase_3_only)
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    config = resolve_generation_config(phase_run)

    assert [f.name for f in config["fewshots"]] == ["active"]


@pytest.mark.django_db
def test_borrowing_is_live_not_pinned_at_creation_time(templates, taxonomy, experiment, seed):
    """THE property the whole feature is for: an edit made to the experiment
    AFTER the ad-hoc run was created, but before it is rendered, must be
    visible — the opposite of the orchestrated path's config snapshot,
    which is pinned once and never re-read (see `_adhoc_generation_config`'s
    own docstring).

    `experiment_id=experiment.pk` (an integer), never `experiment=experiment`
    (the instance) — passing the instance would populate `phase_run`'s own
    FK cache with that EXACT Python object, and mutating `experiment.n_systems`
    in Python would then appear to "work" through the cache by sheer object
    identity, proving nothing about whether the config resolver actually
    re-queries the database. The edits below go through `.update()`/a
    freshly-fetched instance instead of the `experiment` fixture object
    directly, for the same reason: nothing here shares a Python object with
    whatever `resolve_generation_config` reads.
    """
    phase_run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=templates[0],
        taxonomy=taxonomy,
        experiment_id=experiment.pk,
    )
    assert resolve_generation_config(phase_run)["n_systems"] == 7

    Experiment.objects.filter(pk=experiment.pk).update(n_systems=42)
    second_seed = SystemDescription.objects.create(
        title="Riverbend Seed", body="x", origin=Origin.SEED
    )
    Experiment.objects.get(pk=experiment.pk).seed_systems.add(second_seed)

    # Same `phase_run` Python object as the first call above — proving a
    # SECOND call re-reads the database rather than reusing whatever the
    # first call (or Django's own FK descriptor) may have cached.
    config = resolve_generation_config(phase_run)
    assert config["n_systems"] == 42
    assert {s.pk for s in config["seed_systems"]} == {seed.pk, second_seed.pk}


@pytest.mark.django_db
def test_a_generated_seed_system_on_the_borrowed_experiment_fails_closed(
    templates, taxonomy, experiment
):
    """`Experiment.clean()`'s equivalent guard only runs when the EXPERIMENT
    ITSELF is validated — an ad-hoc run reading its live `seed_systems`
    never triggers that. Re-checked here, at the one place that actually
    reads the live list, so a generated system added to the experiment
    AFTER it was saved (bypassing `full_clean()` entirely) still fails
    closed rather than silently reaching a variant-A prompt.
    """
    generated = SystemDescription.objects.create(
        title="From a model", body="x", origin=Origin.GENERATED
    )
    experiment.seed_systems.add(generated)
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    with pytest.raises(BorrowedExperimentContaminated, match="From a model"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_a_generated_target_system_on_the_borrowed_experiment_fails_closed(
    templates, taxonomy, experiment
):
    """Coordinator review round, finding 1 (CRITICAL): the first version of
    `_adhoc_generation_config` ported ONLY the seed-system contamination
    check and missed this one — `target_system` is the MORE dangerous route
    to miss, not an equivalent one: its `.body` is written into the
    variant-A prompt unconditionally, and the render-time/final-request
    leak backstops cannot catch it (generated prose is taxonomy-DERIVED, not
    a literal quote — see `generated_target_system_error`'s own docstring).
    Mirrors `test_a_generated_seed_system_on_the_borrowed_experiment_fails_closed`
    exactly, one field over.
    """
    generated_target = SystemDescription.objects.create(
        title="Target From a Model", body="x", origin=Origin.GENERATED
    )
    experiment.target_system = generated_target
    experiment.save(update_fields=["target_system"])
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    with pytest.raises(BorrowedExperimentContaminated, match="Target From a Model"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_a_decoy_taxonomy_on_the_borrowed_experiment_fails_closed(templates, experiment):
    """Coordinator review round 3, finding 1 (CRITICAL): `launch()` pins
    `taxonomy=experiment.taxonomy` on every `PhaseRun` — the guard taxonomy
    is structurally the experiment's own, never a free choice. The ad-hoc
    borrow path let the requester pin ANY taxonomy with at least one row, a
    decoy included — this proves the execution-time re-check closes it:
    a `PhaseRun` pinned to a DIFFERENT (but otherwise valid, non-empty)
    taxonomy than its borrowed experiment's own must fail closed, not
    silently scan against the wrong list.
    """
    decoy = Taxonomy.objects.create(vocabulary_id="HCV-DECOY", version="1.0", is_active=False)
    HCV.objects.create(taxonomy=decoy, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    phase_run = _adhoc_phase_run(templates, decoy, experiment=experiment)
    assert phase_run.taxonomy_id != experiment.taxonomy_id  # sanity: genuinely a decoy

    with pytest.raises(UnboundGuardTaxonomy, match="not a free choice"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_a_decoy_taxonomy_with_no_borrowed_experiment_fails_closed(templates):
    """The other half: no `experiment`, so the pinned taxonomy must be
    whichever one is currently ACTIVE — not merely any real taxonomy with
    rows (`taxonomy_missing_rows_error` alone let this exact case through
    before this round)."""
    active = Taxonomy.objects.create(vocabulary_id="HCV-ACTIVE", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=active, hcv_id="HCV-01", name="Active One", order=1)
    inactive = Taxonomy.objects.create(vocabulary_id="HCV-INACTIVE", version="1.0")
    HCV.objects.create(taxonomy=inactive, hcv_id="HCV-02", name="Inactive One", order=2)
    phase_run = _adhoc_phase_run(templates, inactive, experiment=None)

    with pytest.raises(UnboundGuardTaxonomy, match="active taxonomy"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_both_generation_toggles_off_on_the_borrowed_experiment_fails_closed(
    templates, taxonomy, experiment
):
    """Coordinator review round 3, finding 2: `launch()` refuses this
    (`Experiment.clean()`'s `generation_toggles_off_error`) with ZERO calls
    made — the ad-hoc borrow path checked neither this rule nor the next
    one, so a probe against it used to return 202 and SPEND one real
    provider call before failing on response validation.
    """
    experiment.generate_systems = False
    experiment.generate_scenarios = False
    experiment.save(update_fields=["generate_systems", "generate_scenarios"])
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    with pytest.raises(BorrowedExperimentMisconfigured, match="at least one"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_missing_target_when_not_generating_on_the_borrowed_experiment_fails_closed(
    templates, taxonomy, experiment
):
    """The second unported `Experiment.clean()` rule, same round: a
    targeting run needs an actual `target_system` — this is the SAME rule
    tested end to end (through a real, wasted provider call, before this
    fix) by `test_iterate_mode.py`'s API-level probe."""
    experiment.generate_systems = False
    experiment.target_system = None
    experiment.save(update_fields=["generate_systems", "target_system"])
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    with pytest.raises(BorrowedExperimentMisconfigured, match="target_system"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_phase_3_taxonomy_binding_is_checked_the_same_way_as_phase_2(templates, experiment):
    """`_check_adhoc_taxonomy_binding` applies to BOTH phases — taxonomy is
    not a phase-2-only concern the way seed_systems/target_system/the
    generation toggles are (finding 5's fix, below): phase 3 always renders
    the taxonomy too, so a decoy must be refused there exactly the same
    way."""
    decoy = Taxonomy.objects.create(vocabulary_id="HCV-DECOY-LAB", version="1.0")
    HCV.objects.create(taxonomy=decoy, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    _, lab = templates
    labelling = PhaseRun.objects.create(
        phase=3, model="m3", prompt_template=lab, taxonomy=decoy, experiment=experiment
    )

    with pytest.raises(UnboundGuardTaxonomy, match="not a free choice"):
        resolve_labelling_fewshots(labelling)


@pytest.mark.django_db
def test_phase_3_does_not_check_seed_or_target_or_toggle_contamination(templates, experiment):
    """Coordinator review round 3, finding 5 (deliberate OVER-enforcement
    fix): a phase-3 ad-hoc run borrowing an experiment whose `target_system`
    is GENERATED-origin (or whose toggles are misconfigured) must NOT be
    refused for it — `_adhoc_labelling_fewshots` reads neither
    `seed_systems`, `target_system`, nor the generation toggles at all
    (`build_phase3_context` takes only `scenario`, `system_description`,
    `taxonomy`, `fewshots`), so a contamination check here would refuse a
    request for a reason phase 3's own render path can never reach.
    """
    generated_target = SystemDescription.objects.create(
        title="Target From a Model", body="x", origin=Origin.GENERATED
    )
    experiment.target_system = generated_target
    experiment.generate_systems = False
    experiment.generate_scenarios = False
    experiment.save(update_fields=["target_system", "generate_systems", "generate_scenarios"])
    _, lab = templates
    labelling = PhaseRun.objects.create(
        phase=3,
        model="m3",
        prompt_template=lab,
        taxonomy=experiment.taxonomy,
        experiment=experiment,
    )

    fewshots = resolve_labelling_fewshots(labelling)  # must not raise

    assert fewshots == []


@pytest.mark.django_db
def test_borrowing_with_no_experiment_set_still_uses_bare_defaults(templates, taxonomy):
    """The explicit, documented choice the coordinator asked for: no
    `experiment` means today's unchanged bare defaults, not an error."""
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=None)

    config = resolve_generation_config(phase_run)

    assert config["seed_systems"] == []
    assert config["fewshots"] == []
    assert config["n_systems"] == 1
    assert config["n_scenarios_per_system"] == 3


@pytest.mark.django_db
def test_borrowing_reads_the_experiments_active_phase_3_fewshots(templates, taxonomy, experiment):
    active = FewShotExample.objects.create(
        phase=3, name="lab-active", input_text="in", output_text="out", is_active=True
    )
    inactive = FewShotExample.objects.create(
        phase=3, name="lab-inactive", input_text="in2", output_text="out2", is_active=False
    )
    phase_2_only = FewShotExample.objects.create(
        phase=2, name="gen-only", input_text="in3", output_text="out3", is_active=True
    )
    experiment.fewshots.add(active, inactive, phase_2_only)
    _, lab = templates
    labelling = PhaseRun.objects.create(
        phase=3, model="m3", prompt_template=lab, taxonomy=taxonomy, experiment=experiment
    )

    fewshots = resolve_labelling_fewshots(labelling)

    assert [f.name for f in fewshots] == ["lab-active"]


@pytest.mark.django_db
def test_labelling_fewshots_with_no_experiment_set_stay_empty(templates, taxonomy):
    _, lab = templates
    labelling = PhaseRun.objects.create(phase=3, model="m3", prompt_template=lab, taxonomy=taxonomy)

    assert resolve_labelling_fewshots(labelling) == []


@pytest.mark.django_db
def test_a_taxonomy_kind_fewshot_on_the_borrowed_experiment_is_refused_for_variant_a(
    templates, taxonomy, experiment
):
    """Proves the OTHER half of the isolation guarantee the API-level test
    (`test_iterate_mode.py`) exercises end to end: `prompts.rendering
    ._fewshot_rows`' variant-A taxonomy-resource guard reads a BORROWED
    few-shot's real `input_file`/`output_file` exactly as it would any
    other — no snapshot adapter needed, so nothing about borrowing an
    experiment weakens this guard.
    """
    from prompts.rendering import _fewshot_rows

    resource = ResourceFile.objects.create(
        name="hcv-for-borrow-test", kind=ResourceKind.TAXONOMY, text="HCV-01: x"
    )
    leaky = FewShotExample.objects.create(
        phase=2, name="leaky", input_file=resource, output_text="ok", is_active=True
    )
    experiment.fewshots.add(leaky)
    phase_run = _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    config = resolve_generation_config(phase_run)

    with pytest.raises(Exception, match="taxonomy resource"):
        _fewshot_rows(config["fewshots"], variant="A")


@pytest.mark.django_db
def test_an_experiment_borrowed_by_an_adhoc_run_cannot_be_deleted(templates, taxonomy, experiment):
    """`PhaseRun.experiment` is `on_delete=PROTECT` deliberately (see that
    field's own comment): SET_NULL would make "never borrowed" and "the
    borrowed experiment was since deleted" indistinguishable the instant the
    FK went null, which is exactly the silent fallback this field exists to
    prevent. Model-level proof that the constraint actually holds —
    `ExperimentViewSet.perform_destroy`/`ExperimentAdmin
    .has_delete_permission` are the friendly 409/hidden-button layers on top
    of this.
    """
    _adhoc_phase_run(templates, taxonomy, experiment=experiment)

    with pytest.raises(ProtectedError):
        experiment.delete()


@pytest.mark.django_db
def test_the_bare_adhoc_defaults_are_validated_like_any_other_config(
    monkeypatch, templates, taxonomy
):
    """Round 4's shared-checker seam, on the branch nobody would have
    thought to check: `_ADHOC_GENERATION_CONFIG`, the built-in defaults an
    ad-hoc run with NO borrowed experiment renders against.

    It is a configuration like any other, and the only one in this codebase
    that used to be waved through on the grounds of being obviously fine.
    `generation_config_errors` now runs over it too, so an edit to those
    defaults that would be refused on an `Experiment` — both generation
    toggles off, here — is refused here as well, instead of reaching a
    render and spending a real provider call before failing on response
    validation. The defaults are patched rather than edited so the test
    pins the CHECK, not one particular set of default values.
    """
    broken = dict(_ADHOC_GENERATION_CONFIG, generate_systems=False, generate_scenarios=False)
    monkeypatch.setattr("pipeline.execution_config._ADHOC_GENERATION_CONFIG", broken)
    phase_run = _adhoc_phase_run(templates, taxonomy)

    with pytest.raises(BorrowedExperimentMisconfigured, match="at least one"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_the_real_bare_adhoc_defaults_pass_that_check(templates, taxonomy):
    """The contrast case: the defaults as actually shipped are clean, so
    the test above is pinning the check rather than a broken default."""
    phase_run = _adhoc_phase_run(templates, taxonomy)

    config = resolve_generation_config(phase_run)

    assert config["generate_systems"] is True
    assert config["seed_systems"] == []
