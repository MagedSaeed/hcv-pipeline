"""Direct, unit-level coverage of `execution_config.py`'s validation logic —
complementing the end-to-end proofs in `test_generation.py`/
`test_orchestration.py` (which exercise the same code through `run_phase2`/
`execute_pipeline_run`) with fast, focused checks of `validate_snapshot`
itself: every way a snapshot can fail to match the shape `config_snapshot`
produces, and that a well-formed one round-trips cleanly.
"""

import copy

import pytest

from corpus.models import ResourceKind
from pipeline.execution_config import (
    MalformedSnapshot,
    resolve_generation_config,
    resolve_labelling_fewshots,
    validate_snapshot,
)
from pipeline.models import Experiment, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import Taxonomy

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
    "seed_systems": [_VALID_SYSTEM],
    "fewshots": [_VALID_FEWSHOT],
    "target_system": None,
    "generate_systems": True,
    "generate_scenarios": True,
    "n_systems": 1,
    "n_scenarios_per_system": 1,
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
        phase2_prompt=gen,
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


def test_adhoc_config_defaults_are_not_shared_between_calls():
    """Finding 4, task 5 second review round: the ad-hoc branch used to
    return `dict(_ADHOC_GENERATION_CONFIG)` — a SHALLOW copy, so every
    ad-hoc run's `seed_systems`/`fewshots` were the SAME list objects as
    the module global (and each other) — the exact footgun `launch()`'s
    own `copy.deepcopy` exists to avoid for the orchestrated path. Now
    `copy.deepcopy`d here too: mutating one call's result must never affect
    another's, or the module global itself.
    """
    ad_hoc = PhaseRun()  # unsaved: no `pipeline_run`, so no DB access needed
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
