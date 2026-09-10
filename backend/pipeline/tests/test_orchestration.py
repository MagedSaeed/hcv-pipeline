import json
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.keys import MissingAPIKey
from core.models import AppSettings
from corpus.models import (
    FewShotExample,
    Origin,
    ResourceFile,
    ResourceKind,
    Scenario,
    SystemDescription,
)
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.execution_config import config_snapshot
from pipeline.generation import run_phase2
from pipeline.labelling import finish_labelling_run
from pipeline.labelling import label_scenario as real_label_scenario
from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun
from pipeline.orchestration import (
    PhaseRunQueueingError,
    RunCancelled,
    RunInFlight,
    execute_pipeline_run,
    launch,
    queue_phase_run,
    retry_failed,
    roll_up,
    run_labelling,
)
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

CARD = {
    "scenario_id": "sc_01",
    "actor": "EP1",
    "trigger": "t",
    "standing_state": "s",
    "asset": "a",
    "threat": "Spoofing.",
    "outcome": "o",
    "narrative_steps": ["EP1 acts."],
}
CARD_2 = {**CARD, "scenario_id": "sc_02"}
GENERATED = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": [CARD]}]}
)
GENERATED_TWO_SCENARIOS = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": [CARD, CARD_2]}]}
)
LABELLED = json.dumps({"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]})


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def experiment(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    gen = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    lab = PromptTemplate.objects.create_version(
        phase=3, name="l", user_prompt="{{ scenario.scenario_id }}"
    )
    return Experiment.objects.create(
        name="e",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=gen,
        phase2_prompt_b=gen,
        phase3_model="m3",
        phase3_prompt=lab,
        n_systems=1,
        n_scenarios_per_system=1,
    )


@pytest.mark.django_db
def test_the_pinned_snapshot_is_authoritative_not_the_live_experiment_at_execution_time():
    """The exact proof a review round found: `generation._build_context`
    used to read `seed_systems`/`n_systems`/`fewshots`/`target_system` off
    the LIVE `Experiment` at EXECUTION time, while `PipelineRun.config` was
    only ever pinned at LAUNCH time — so editing the experiment while a run
    sat QUEUED, then executing it, made the archive contradict itself: the
    run's own `inputs.n_systems` (its pinned snapshot) said one thing while
    its archived `rendered_user_prompt` said another. That is the opposite
    of the brief's own words — "makes the structured record agree with the
    text record already archived verbatim" — task 5's entire point.

    Launch pins `n_systems=1`, `seed_systems=[OLDSEED]`. The experiment is
    then edited to `n_systems=42`, `seed_systems=[NEWSEED]` WHILE the run
    sits QUEUED (never re-launched). The rendered prompt must still read
    "n=1 seeds=OLDSEED" — what was pinned — and the run's own export
    (`build_run_bundle`) must agree with it.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-SNAP", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="x", order=1)
    echo_template = PromptTemplate.objects.create_version(
        phase=2,
        name="echo",
        user_prompt=(
            "n={{ n_systems }} seeds={% for s in seed_systems %}{{ s.title }}{% endfor %}"
        ),
    )
    labelling_template = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    old_seed = SystemDescription.objects.create(title="OLDSEED", body="old", origin=Origin.SEED)
    snapshot_experiment = Experiment.objects.create(
        name="snapshot-authority",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=echo_template,
        phase3_model="m3",
        phase3_prompt=labelling_template,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    snapshot_experiment.variants = ["A"]
    snapshot_experiment.save()
    snapshot_experiment.seed_systems.add(old_seed)

    run = launch(snapshot_experiment, n_repetitions=1)[0]
    assert run.config["n_systems"] == 1
    assert run.config["seed_systems"][0]["title"] == "OLDSEED"

    # Edit the experiment WHILE the run sits QUEUED (PENDING here — the
    # distinction between the two does not matter for this proof, neither
    # is re-launched) — matching the proof scenario exactly.
    new_seed = SystemDescription.objects.create(title="NEWSEED", body="new", origin=Origin.SEED)
    snapshot_experiment.n_systems = 42
    snapshot_experiment.seed_systems.set([new_seed])
    snapshot_experiment.save()

    fake = FakeLLMClient()
    fake.queue(GENERATED)
    set_llm_client(fake)
    execute_pipeline_run(run)

    generation = run.phase_runs.get(phase=2, variant="A")
    call = generation.calls.get()
    assert call.rendered_user_prompt == "n=1 seeds=OLDSEED"
    assert "42" not in call.rendered_user_prompt
    assert "NEWSEED" not in call.rendered_user_prompt

    # The structured record agrees with the text record just archived.
    from pipeline.export import build_run_bundle

    bundle = build_run_bundle(run)
    assert bundle["inputs"]["n_systems"] == 1
    assert bundle["inputs"]["seed_systems"][0]["title"] == "OLDSEED"


@pytest.mark.django_db
def test_a_malformed_snapshot_fails_the_run_instead_of_stranding_it(experiment):
    """Finding 2, task 5 second review round: `resolve_generation_config`
    used to be called OUTSIDE `run_phase2`'s try/except, before the
    RUNNING-transition write. A malformed snapshot (`MalformedSnapshot`,
    or a query error — anything at all) then escaped `run_phase2` entirely,
    breaking its own docstring's promise ("Always ends in a terminal
    status") and `execute_pipeline_run`'s one level up: the `PipelineRun`
    stayed RUNNING forever (set BEFORE `run_phase2` was ever called), this
    `PhaseRun` stayed PENDING (never reaching its own RUNNING write, which
    happens after config resolution), and a re-drive would hit the
    idempotency guard and merely re-roll it up — stranded with no way out
    except `cancel`. Now the whole batch reaches a real terminal state.
    """
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    run.config = {}
    run.save(update_fields=["config"])

    execute_pipeline_run(run)  # must not raise

    run.refresh_from_db()
    assert run.status == PipelineRun.Status.FAILED
    assert run.finished_at is not None
    for phase_run in run.phase_runs.all():
        assert phase_run.is_terminal
    generation = run.phase_runs.get(phase=2)
    assert generation.status == PhaseRun.Status.FAILED
    assert "MalformedSnapshot" in generation.error


@pytest.mark.django_db
def test_an_inactive_fewshot_is_snapshotted_but_never_rendered():
    """Finding 5, task 5 review round: `config_snapshot` had no `is_active`
    filter while the live renderer always did (`experiment.fewshots.filter(
    phase=2, is_active=True)`), so the snapshot could list a few-shot the
    model never actually saw. Fixed by recording `is_active` on every
    snapshotted row (active or not) and filtering on it at READ time
    (`pipeline.execution_config._snapshot_fewshots`) instead of at snapshot
    time — the archive still shows BOTH few-shots, a complete historical
    record of what existed at launch, but only the active one reaches the
    rendered prompt.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-ACTIVE", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="x", order=1)
    echo_template = PromptTemplate.objects.create_version(
        phase=2, name="echo-fs", user_prompt="{% for f in fewshots %}{{ f.input }}{% endfor %}"
    )
    labelling_template = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    fewshot_experiment = Experiment.objects.create(
        name="active-filter",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=echo_template,
        phase3_model="m3",
        phase3_prompt=labelling_template,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    fewshot_experiment.variants = ["A"]
    fewshot_experiment.save()
    FewShotExample.objects.create(
        phase=2, name="dead", input_text="DEAD", output_text="o", is_active=False
    )
    FewShotExample.objects.create(
        phase=2, name="live", input_text="LIVE", output_text="o", is_active=True
    )
    fewshot_experiment.fewshots.set(FewShotExample.objects.filter(name__in=["dead", "live"]))

    run = launch(fewshot_experiment, n_repetitions=1)[0]
    # The snapshot itself lists BOTH, with their real is_active values —
    # a complete record, not pre-filtered away.
    snapshotted = {f["name"]: f["is_active"] for f in run.config["fewshots"]}
    assert snapshotted == {"dead": False, "live": True}

    fake = FakeLLMClient()
    fake.queue(json.dumps({"system_descriptions": []}))
    set_llm_client(fake)
    execute_pipeline_run(run)

    generation = run.phase_runs.get(phase=2, variant="A")
    call = generation.calls.get()
    assert call.rendered_user_prompt == "LIVE"
    assert "DEAD" not in call.rendered_user_prompt


@pytest.mark.django_db
def test_phase_3_fewshots_are_read_from_the_snapshot_and_actually_rendered():
    """Finding 5, task 5 second review round: phase-3 few-shots read from a
    snapshot were entirely untested — forcing `resolve_labelling_fewshots`
    to return `[]` unconditionally left the whole suite green. Proven here
    end to end: a phase-3 few-shot's resolved `input` text must actually
    reach the archived `rendered_user_prompt` of a labelling call, the
    phase-3 sibling of `test_the_pinned_snapshot_is_authoritative_...` and
    `test_an_inactive_fewshot_is_snapshotted_but_never_rendered` above,
    which only ever exercised phase 2.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-P3FS", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="x", order=7)
    generation_template = PromptTemplate.objects.create_version(phase=2, name="g3", user_prompt="g")
    echo_labelling_template = PromptTemplate.objects.create_version(
        phase=3,
        name="echo-l",
        user_prompt="{% for f in fewshots %}{{ f.input }}{% endfor %} {{ scenario.scenario_id }}",
    )
    fewshot_experiment = Experiment.objects.create(
        name="p3-fewshots",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=generation_template,
        phase3_model="m3",
        phase3_prompt=echo_labelling_template,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    fewshot_experiment.variants = ["A"]
    fewshot_experiment.save()
    FewShotExample.objects.create(
        phase=3, name="p3-example", input_text="P3EXAMPLE", output_text="o"
    )
    fewshot_experiment.fewshots.set(FewShotExample.objects.filter(name="p3-example"))

    fake = FakeLLMClient()
    fake.queue(GENERATED)
    fake.queue(LABELLED)
    set_llm_client(fake)
    run = launch(fewshot_experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    call = labelling.calls.get()
    assert "P3EXAMPLE" in call.rendered_user_prompt


@pytest.mark.django_db
def test_launch_creates_one_pipeline_run_per_repetition(experiment):
    runs = launch(experiment, n_repetitions=3)
    assert [r.repetition_index for r in runs] == [1, 2, 3]
    assert PipelineRun.objects.count() == 3


@pytest.mark.django_db
def test_launch_creates_a_phase_run_per_variant_and_phase(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    made = {(p.phase, p.variant) for p in run.phase_runs.all()}
    assert made == {(2, "A"), (2, "B"), (3, "A"), (3, "B")}


@pytest.mark.django_db
def test_launch_honours_a_single_variant(experiment):
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    assert {p.variant for p in run.phase_runs.all()} == {"A"}


@pytest.mark.django_db
def test_a_phase_three_run_points_at_its_phase_two_run(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    labelling = run.phase_runs.get(phase=3, variant="A")
    generation = run.phase_runs.get(phase=2, variant="A")
    assert labelling.source_phase_run == generation


@pytest.mark.django_db
def test_phase_runs_pin_the_experiment_configuration(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    assert generation.model == "m2"
    assert generation.prompt_template == experiment.phase2_prompt_a
    assert generation.taxonomy == experiment.taxonomy


@pytest.mark.django_db
def test_launch_pins_a_different_template_onto_each_variants_generation_run():
    """The core guarantee this task adds: variant A and variant B of the
    SAME repetition pin two DIFFERENT `PromptTemplate` rows — never the
    same one, unlike the old single shared `phase2_prompt` FK. User
    decision, 2026-09-08: full independence between the two templates, no
    coupling. Proven here with genuinely distinct templates (not merely two
    FKs that happen to point at the same row, which `experiment`'s own
    default fixture would not distinguish from the old behaviour), and via
    a REAL `launch()`, not a hand-built `PhaseRun`, so this exercises the
    exact code path `Experiment.phase2_prompt_for` backs.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-AB-SPLIT", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="x", order=1)
    template_a = PromptTemplate.objects.create_version(
        phase=2, name="variant-a-template", user_prompt="A only, no taxonomy reference"
    )
    template_b = PromptTemplate.objects.create_version(
        phase=2, name="variant-b-template", user_prompt="B only: {{ taxonomy|length }} rows"
    )
    labelling_template = PromptTemplate.objects.create_version(
        phase=3, name="lab-ab", user_prompt="l"
    )
    split_experiment = Experiment.objects.create(
        name="a-b-split",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=template_a,
        phase2_prompt_b=template_b,
        phase3_model="m3",
        phase3_prompt=labelling_template,
        n_systems=1,
        n_scenarios_per_system=1,
    )

    run = launch(split_experiment, n_repetitions=1)[0]
    generation_a = run.phase_runs.get(phase=2, variant="A")
    generation_b = run.phase_runs.get(phase=2, variant="B")

    assert generation_a.prompt_template == template_a
    assert generation_b.prompt_template == template_b
    assert generation_a.prompt_template_id != generation_b.prompt_template_id


@pytest.mark.django_db
def test_a_variant_a_run_whose_own_template_references_taxonomy_still_fails_closed(_client):
    """Isolation is unchanged by this task and must stay intact. Even though
    each variant now pins its OWN, fully independent template
    (`phase2_prompt_a`/`phase2_prompt_b`, no coupling between them), a
    genuine authoring mistake — a stray `{{ taxonomy }}` left in variant
    A's template, say by copying variant B's and forgetting to strip it —
    must still fail loudly rather than silently rendering empty.
    `taxonomy` is genuinely ABSENT from variant A's render context (not
    merely falsy), so under `StrictUndefined` this raises
    `PromptRenderError`, caught by `run_phase2` and archived as a FAILED
    run with ZERO provider calls made — see `prompts/rendering.py`'s module
    docstring for the full explanation of why `StrictUndefined` still
    matters even though each variant is authored independently now.

    Task 10.4 moved the FIRST refusal of this template to `launch()`, which
    now dry-renders each variant's template under its own context and
    refuses before any row exists (asserted LAST below, and in depth by
    `test_launch_refuses_a_variant_a_template_that_fails_as_variant_a`).
    That does not retire this layer: a run pinned before that check
    existed, or by any route that bypasses `launch()`, must still fail
    closed at run time. So the run is pinned by hand here — the same
    snapshot and the same per-variant template `launch()` would have pinned
    — and `run_phase2` is driven against it directly, with a response
    queued so that a guard failing open would show up as a RECORDED call and
    an ARCHIVED `LLMCall`, not merely as nothing happening. The launch
    refusal comes last so it can never mask a run-time failure. The lower-level
    proof, `render()` alone, lives at `prompts/tests/test_rendering.py
    ::test_the_naive_taxonomy_guard_fails_variant_a_but_not_b`. Variant B's
    own template is left clean and is not exercised here — the point is
    that variant A's independent template still fails on its own mistake.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-FAILS-CLOSED", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="x", order=1)
    leaky_a_template = PromptTemplate.objects.create_version(
        phase=2,
        name="leaky-a",
        user_prompt="{% if taxonomy %}guided{% else %}unbiased{% endif %}",
    )
    clean_b_template = PromptTemplate.objects.create_version(
        phase=2, name="clean-b", user_prompt="{{ taxonomy|length }} rows"
    )
    labelling_template = PromptTemplate.objects.create_version(
        phase=3, name="lab-fails-closed", user_prompt="l"
    )
    leaky_experiment = Experiment.objects.create(
        name="fails-closed",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=leaky_a_template,
        phase2_prompt_b=clean_b_template,
        phase3_model="m3",
        phase3_prompt=labelling_template,
        n_systems=1,
        n_scenarios_per_system=1,
    )

    run = PipelineRun.objects.create(
        experiment=leaky_experiment, repetition_index=1, config=config_snapshot(leaky_experiment)
    )
    generation_a = PhaseRun.objects.create(
        pipeline_run=run,
        phase=2,
        variant="A",
        model="m2",
        prompt_template=leaky_experiment.phase2_prompt_for("A"),
        taxonomy=tax,
        total_children=1,
    )
    assert generation_a.prompt_template_id == leaky_a_template.pk
    _client.queue(GENERATED)

    result = run_phase2(generation_a)

    assert result.status == PhaseRun.Status.FAILED
    assert "taxonomy" in result.error
    assert _client.calls == []  # no provider call was ever made
    assert LLMCall.objects.filter(phase_run=result).count() == 0  # and none archived

    with pytest.raises(ValidationError):
        launch(leaky_experiment, n_repetitions=1)
    assert PipelineRun.objects.count() == 1  # only the hand-pinned run


# Templates that fail as variant A, each by a different route the render path
# ALREADY refuses: the taxonomy variable, its truthiness, `hcv_table()`, a
# literal identifier (the render-time leak scan) and a taxonomy-kind
# `resource()`. Every one renders cleanly as variant B. launch()'s dry run
# (task 10.4) reuses those existing rules; it adds no rule of its own.
WRITTEN_FOR_B = {
    "taxonomy_variable": "{{ taxonomy|length }} rows",
    "taxonomy_truthiness": "{% if taxonomy %}guided{% else %}unbiased{% endif %}",
    "hcv_table": "{{ hcv_table() }}",
    "literal_hcv_id": "Consider HCV-07 carefully.",
    "taxonomy_resource": '{{ resource("hcv-reference") }}',
}


def _taxonomy_resource():
    return ResourceFile.objects.create(
        name="hcv-reference", kind=ResourceKind.TAXONOMY, text="HCV-07: Malicious Insiders"
    )


def _queue_a_whole_repetition(client, experiment):
    """Queue a response for every call a repetition could make, so a stray
    provider call would be RECORDED rather than raise for want of a queued
    response — `client.calls` staying empty is then a measurement, not an
    assumption."""
    for _ in experiment.variants:
        client.queue(GENERATED)
        client.queue(LABELLED)


@pytest.mark.django_db
@pytest.mark.parametrize("user_prompt", list(WRITTEN_FOR_B.values()), ids=list(WRITTEN_FOR_B))
def test_launch_refuses_a_variant_a_template_that_fails_as_variant_a(
    _client, experiment, user_prompt
):
    """Task 10.4. A template written for variant B, pinned as
    `phase2_prompt_a`, never leaked — variant A's context has no `taxonomy`
    key, so its run-time render fails closed — but only once the repetition
    was launched, with variant B still billing and no A arm left to compare
    it against. `launch()` now dry-renders each variant's template under its
    own context first and refuses with a `ValidationError` on
    `phase2_prompt_a`: zero provider calls, zero `PipelineRun`/`PhaseRun`
    rows."""
    _taxonomy_resource()
    experiment.phase2_prompt_a = PromptTemplate.objects.create_version(
        phase=2, name="written-for-b", user_prompt=user_prompt
    )
    experiment.save()
    _queue_a_whole_repetition(_client, experiment)

    with pytest.raises(ValidationError) as refused:
        launch(experiment, n_repetitions=3)

    assert set(refused.value.message_dict) == {"phase2_prompt_a"}
    assert "as variant A" in refused.value.message_dict["phase2_prompt_a"][0]
    assert len(_client.calls) == 0
    assert PipelineRun.objects.count() == 0
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("user_prompt", list(WRITTEN_FOR_B.values()), ids=list(WRITTEN_FOR_B))
def test_the_same_template_launches_fine_in_the_b_slot(_client, experiment, user_prompt):
    """The dry run judges a template by the variant it is pinned to, not by
    its text alone: every template refused above renders cleanly as variant
    B, where the taxonomy is supplied, so pinned as `phase2_prompt_b` it
    launches — still without a single provider call."""
    _taxonomy_resource()
    written_for_b = PromptTemplate.objects.create_version(
        phase=2, name="written-for-b", user_prompt=user_prompt
    )
    experiment.phase2_prompt_b = written_for_b
    experiment.save()
    _queue_a_whole_repetition(_client, experiment)

    run = launch(experiment, n_repetitions=1)[0]

    assert run.phase_runs.get(phase=2, variant="B").prompt_template == written_for_b
    assert PhaseRun.objects.count() == 4
    assert len(_client.calls) == 0


@pytest.mark.django_db
def test_a_clean_variant_a_template_launches(_client, experiment):
    """A variant-A template that never touches the taxonomy launches as
    before — the dry run refuses failures, not variant A itself — alongside a
    variant-B template that genuinely uses `taxonomy` and `hcv_table()`."""
    clean_a = PromptTemplate.objects.create_version(
        phase=2,
        name="clean-a",
        user_prompt=(
            "Write {{ n_systems }} system(s), {{ n_scenarios_per_system }} scenario(s) "
            "each.{% for s in seed_systems %} Seed: {{ s.title }}.{% endfor %}"
        ),
    )
    guided_b = PromptTemplate.objects.create_version(
        phase=2, name="guided-b", user_prompt="{{ taxonomy|length }} HCVs:\n{{ hcv_table() }}"
    )
    experiment.phase2_prompt_a = clean_a
    experiment.phase2_prompt_b = guided_b
    experiment.save()
    _queue_a_whole_repetition(_client, experiment)

    runs = launch(experiment, n_repetitions=2)

    assert [run.repetition_index for run in runs] == [1, 2]
    assert PhaseRun.objects.count() == 8
    for run in runs:
        assert run.phase_runs.get(phase=2, variant="A").prompt_template == clean_a
        assert run.phase_runs.get(phase=2, variant="B").prompt_template == guided_b
    assert len(_client.calls) == 0


@pytest.mark.django_db
def test_launch_refuses_a_variant_b_template_under_its_own_field(_client, experiment):
    """The refusal names the field that holds the failing template, so the
    400 tells the researcher which slot to fix — `phase2_prompt_b` here, with
    variant A's clean template not blamed alongside it."""
    experiment.phase2_prompt_b = PromptTemplate.objects.create_version(
        phase=2, name="typo-b", user_prompt="{{ taxonmy|length }} rows"
    )
    experiment.save()
    _queue_a_whole_repetition(_client, experiment)

    with pytest.raises(ValidationError) as refused:
        launch(experiment, n_repetitions=1)

    assert set(refused.value.message_dict) == {"phase2_prompt_b"}
    assert "as variant B" in refused.value.message_dict["phase2_prompt_b"][0]
    assert len(_client.calls) == 0
    assert PipelineRun.objects.count() == 0
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_launch_dry_runs_against_the_inputs_the_run_will_pin(_client, experiment):
    """The dry run renders against the snapshot `launch()` is about to pin,
    not against empty defaults. The template here is the fixture's plain
    "g", clean on its own; variant A fails only because this experiment's
    own phase-2 few-shot is backed by a taxonomy-kind resource, which the
    variant-A context builder refuses. A dry run over defaults would have
    waved it through, and the run would then have failed on it."""
    fewshot = FewShotExample.objects.create(
        phase=2,
        name="taxonomy-backed",
        input_file=_taxonomy_resource(),
        output_text="o",
        is_active=True,
    )
    experiment.fewshots.add(fewshot)
    _queue_a_whole_repetition(_client, experiment)

    with pytest.raises(ValidationError) as refused:
        launch(experiment, n_repetitions=1)

    assert set(refused.value.message_dict) == {"phase2_prompt_a"}
    assert "taxonomy-backed" in refused.value.message_dict["phase2_prompt_a"][0]
    assert len(_client.calls) == 0
    assert PipelineRun.objects.count() == 0
    assert PhaseRun.objects.count() == 0


# Templates that raise while rendering, with the fragment the refusal must
# name. The first four raise something OTHER than a Jinja `TemplateError` or
# a `TypeError` — each escaped `render()` before the task 10.6 follow-up, so
# `launch()` let it out and `run` answered 500. The last two are Jinja errors
# that were always caught; they pin the whole contract, not just the fix.
RAISES_WHILE_RENDERING = {
    "zero_division": ("{{ 1/0 }}", "ZeroDivisionError"),
    "value_error": ('{{ "abc".index("z") }}', "ValueError"),
    "index_error": ("{{ [].pop() }}", "IndexError"),
    "sandbox_range_limit": ("{{ range(1000000)|list|length }}", "OverflowError"),
    "undefined_filter": ("{{ n_systems|no_such_filter }}", "no_such_filter"),
    "bad_attribute": ("{{ n_systems.nope }}", "nope"),
}


@pytest.mark.django_db
@pytest.mark.parametrize(("variant", "field"), [("A", "phase2_prompt_a"), ("B", "phase2_prompt_b")])
@pytest.mark.parametrize(
    ("user_prompt", "names"),
    list(RAISES_WHILE_RENDERING.values()),
    ids=list(RAISES_WHILE_RENDERING),
)
def test_launch_refuses_any_render_failure_on_the_right_field(
    _client, experiment, user_prompt, names, variant, field
):
    """Task 10.6 follow-up: whatever a template raises while rendering,
    `launch()` refuses it as a `ValidationError` on the field holding it,
    naming the variant and the underlying error — never an escaped exception
    (a 500 from `run`). The other slot keeps the fixture's clean template, so
    only the failing one is blamed. Zero provider calls, zero rows."""
    setattr(
        experiment,
        field,
        PromptTemplate.objects.create_version(phase=2, name="raises", user_prompt=user_prompt),
    )
    experiment.save()
    _queue_a_whole_repetition(_client, experiment)

    with pytest.raises(ValidationError) as refused:
        launch(experiment, n_repetitions=1)

    assert set(refused.value.message_dict) == {field}
    message = refused.value.message_dict[field][0]
    assert f"as variant {variant}" in message
    assert names in message
    assert len(_client.calls) == 0
    assert PipelineRun.objects.count() == 0
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_a_runtime_template_error_fails_the_run_instead_of_stranding_it(_client, experiment):
    """The same `render()` fix, seen from the run path. `run_phase2` has
    always failed cleanly on `PromptRenderError`; `{{ 1/0 }}` used to escape
    it as a bare `ZeroDivisionError` instead, leaving the phase run stuck
    RUNNING — the stranding its own docstring promises never happens.
    Reached without `launch()`, which would now refuse it: an ad-hoc,
    iterate-mode run."""
    adhoc = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=PromptTemplate.objects.create_version(
            phase=2, name="divides-by-zero", user_prompt="{{ 1/0 }}"
        ),
        taxonomy=experiment.taxonomy,
    )
    _client.queue(GENERATED)

    result = run_phase2(adhoc)

    assert result.status == PhaseRun.Status.FAILED
    assert "ZeroDivisionError" in result.error
    assert result.calls.count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_a_taxonomy_resource_still_fails_the_run_as_a_taxonomy_leak_not_a_render_error(
    _client, experiment
):
    """The distinction the `render()` catch-all must not blur. A taxonomy-kind
    `resource()` under variant A is an ISOLATION refusal, and the error a run
    archives must record it as a `TaxonomyLeak` — not as a
    `PromptRenderError` that merely mentions one. `render()` re-raises
    `TaxonomyLeak` before its catch-all; this pins that on the record a run
    leaves, not only on `render()` itself. Reached without `launch()`, which
    would refuse it: an ad-hoc run."""
    _taxonomy_resource()
    adhoc = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=PromptTemplate.objects.create_version(
            phase=2, name="reads-the-taxonomy", user_prompt='{{ resource("hcv-reference") }}'
        ),
        taxonomy=experiment.taxonomy,
    )
    _client.queue(GENERATED)

    result = run_phase2(adhoc)

    assert result.status == PhaseRun.Status.FAILED
    assert result.error.startswith("TaxonomyLeak: ")
    assert "taxonomy resource" in result.error
    assert result.calls.count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_launch_seeds_phase_twos_denominator_so_it_is_never_0_of_0(experiment):
    """M6: nothing else seeds a denominator before a worker actually picks a
    run up, so a freshly queued batch read 0/0 on every row — blank for
    exactly the window --watch exists to cover. Phase 2's total is knowable
    the instant it is created (it is always exactly one call), unlike
    Phase 3's (which depends on Phase 2's still-unknown output), so only
    Phase 2 is seeded here."""
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")

    assert generation.total_children == 1
    assert generation.progress == {"done": 0, "total": 1, "failed": 0, "percent": 0}
    assert labelling.total_children == 0


@pytest.mark.django_db
def test_executing_a_run_generates_then_labels(_client, experiment):
    for _ in experiment.variants:
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 2
    assert Scenario.objects.filter(origin=Origin.GENERATED).count() == 2
    assert Label.objects.count() == 2


@pytest.mark.django_db
def test_labelling_only_sees_its_own_variants_scenarios(_client, experiment):
    for _ in experiment.variants:
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    for variant in ("A", "B"):
        labelling = run.phase_runs.get(phase=3, variant=variant)
        generation = run.phase_runs.get(phase=2, variant=variant)
        for label in labelling.labels.all():
            assert label.scenario.generation_run == generation


@pytest.mark.django_db
def test_a_failed_generation_marks_the_pipeline_run_failed(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.fail_next("rate limited", http_status=429)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.FAILED


@pytest.mark.django_db
def test_labelling_is_skipped_when_generation_failed(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.fail_next("boom")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.CANCELLED


@pytest.mark.django_db
def test_execute_pipeline_run_does_not_overwrite_an_already_cancelled_labelling_reason(experiment):
    """The "generation did not complete -> skip labelling" branch used to
    write CANCELLED + a generic "Skipped: generation did not complete."
    unconditionally, using a `labelling` object fetched BEFORE
    `run_phase2(generation)` ran — stale by the time this branch executes if
    an operator's `POST .../cancel/` (which writes a MORE SPECIFIC
    "Cancelled by operator." error) landed in the real wall-clock time that
    call took. Simulated here by having `run_phase2` itself, as a side
    effect, mark the labelling run CANCELLED with that specific reason
    before failing generation — exactly the concurrent-cancel timing this
    guards against. The status was already correct either way (both are
    CANCELLED); what this proves is the more specific reason survives.
    """
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    labelling = run.phase_runs.get(phase=3, variant="A")

    def fake_run_phase2(phase_run):
        PhaseRun.objects.filter(pk=labelling.pk).update(
            status=PhaseRun.Status.CANCELLED, error="Cancelled by operator."
        )
        phase_run.status = PhaseRun.Status.FAILED
        phase_run.error = "boom"
        phase_run.save(update_fields=["status", "error"])
        return phase_run

    with patch("pipeline.orchestration.run_phase2", side_effect=fake_run_phase2):
        execute_pipeline_run(run)

    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.CANCELLED
    assert labelling.error == "Cancelled by operator."


@pytest.mark.django_db
def test_retry_reruns_only_the_failed_scenarios(_client, experiment):
    """M5: with only one scenario in play there is no healthy scenario to
    leave alone, so "only the failed one" was never actually proven. Two
    scenarios: sc_01 succeeds, sc_02 fails; retrying must touch only sc_02 —
    proven by exactly one more provider call being made, not two."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)  # sc_01 succeeds
    _client.fail_next("timeout")  # sc_02 fails
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED
    assert Label.objects.count() == 1
    calls_before_retry = len(_client.calls)

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert len(_client.calls) == calls_before_retry + 1
    assert retried.status == PhaseRun.Status.COMPLETED
    assert Label.objects.count() == 2


@pytest.mark.django_db
def test_retry_creates_no_duplicate_labels(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert Label.objects.count() == 1

    _client.queue(LABELLED)
    retry_failed(labelling)
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_retry_reruns_a_call_that_parsed_but_failed_to_persist(_client, experiment):
    """Task 4 established that a call which parsed fine but failed to PERSIST
    keeps parsed_ok=True and records the problem in parse_error instead (see
    pipeline.labelling.failed_calls). retry_failed must use that same helper —
    a bare `calls.filter(parsed_ok=False)` would never pick this call up, and
    a persistence failure would never be retried."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert Label.objects.count() == 1

    # Simulate the Task 4 persistence-failure shape directly on the archived
    # call: parsed_ok stays True, the problem lands in parse_error, and the
    # label that call claims to have written did not actually survive.
    call = labelling.calls.get()
    assert call.parsed_ok is True
    call.parse_error = "Could not persist the labels: simulated lock"
    call.save()
    Label.objects.all().delete()
    finish_labelling_run(labelling)
    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.FAILED

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert retried.status == PhaseRun.Status.COMPLETED
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_one_exploding_scenario_does_not_stop_the_fan_out(_client, experiment):
    """label_scenario is careful never to raise on a provider or database
    error, but the call to get_llm_client().complete() itself is unguarded —
    and OpenRouterClient deliberately raises SecretLeak if a key-shaped
    string reaches the request body, which a rendered scenario narrative
    could carry. run_labelling must survive whatever any one scenario does
    and keep going, archiving a call for the scenario that blew up instead
    of aborting the whole fan-out with nothing recorded at all."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    run = launch(experiment, n_repetitions=1)[0]

    def exploding(phase_run, scenario, sequence=1):
        if scenario.scenario_id == "sc_01":
            raise RuntimeError("simulated explosion")
        return real_label_scenario(phase_run, scenario, sequence=sequence)

    _client.queue(LABELLED)
    with patch("pipeline.orchestration.label_scenario", side_effect=exploding):
        execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.calls.count() == 2

    exploded = labelling.calls.get(scenario__scenario_id="sc_01")
    assert exploded.parsed_ok is False
    assert "simulated explosion" in exploded.parse_error

    survived = labelling.calls.get(scenario__scenario_id="sc_02")
    assert survived.parsed_ok is True

    assert Label.objects.count() == 1
    assert labelling.failed_children == 1
    assert labelling.status == PhaseRun.Status.FAILED


@pytest.mark.django_db
def test_reducing_variants_after_launch_still_completes_the_pinned_runs(_client, experiment):
    """I1: Experiment.variants is a plain, editable JSONField. Shrinking it
    (A,B -> A) after launch must not leave variant B's already-pinned phase
    runs stranded at PENDING forever with the pipeline run stuck RUNNING."""
    for _ in experiment.variants:  # A, B
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]

    experiment.variants = ["A"]
    experiment.save()

    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert {p.status for p in run.phase_runs.all()} == {PhaseRun.Status.COMPLETED}


@pytest.mark.django_db
def test_growing_variants_after_launch_does_not_raise(_client, experiment):
    """I1, the other direction: growing variants (A -> A,B) after launch must
    not make execute_pipeline_run try to find a B phase run that was never
    created — that used to raise PhaseRun.DoesNotExist and strand the
    repetition at RUNNING with roll_up never called."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]

    experiment.variants = ["A", "B"]
    experiment.save()

    execute_pipeline_run(run)  # must not raise PhaseRun.DoesNotExist
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert set(run.phase_runs.values_list("phase", "variant")) == {(2, "A"), (3, "A")}


@pytest.mark.django_db
def test_executing_a_run_twice_does_not_duplicate_anything(_client, experiment):
    """I3: execute_pipeline_run is not naturally idempotent — without a
    guard, a second call would run_phase2/run_labelling all over again and
    double every system, scenario, label and LLMCall this repetition already
    produced, silently corrupting the research record."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    systems_before = SystemDescription.objects.filter(origin=Origin.GENERATED).count()
    scenarios_before = Scenario.objects.filter(origin=Origin.GENERATED).count()
    labels_before = Label.objects.count()
    calls_before = LLMCall.objects.count()

    # Nothing queued for a second pass: if the guard is missing, this makes
    # another real provider call and raises "no queued response left."
    execute_pipeline_run(run)

    run.refresh_from_db()
    assert run.status == PipelineRun.Status.COMPLETED
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == systems_before
    assert Scenario.objects.filter(origin=Origin.GENERATED).count() == scenarios_before
    assert Label.objects.count() == labels_before
    assert LLMCall.objects.count() == calls_before


@pytest.mark.django_db
def test_retry_on_a_cancelled_run_is_a_true_no_op(_client, experiment):
    """I2: a labelling run CANCELLED because its generation failed has zero
    calls to retry. Handing it to finish_labelling_run anyway would report a
    false COMPLETED with failed_children=0, wiping the CANCELLED status and
    its "generation did not complete" error — a green status for work that
    never happened."""
    experiment.variants = ["A"]
    experiment.save()
    _client.fail_next("boom")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.CANCELLED

    retried = retry_failed(labelling)

    assert retried.status == PhaseRun.Status.CANCELLED
    assert retried.error == "Skipped: generation did not complete."
    assert retried.calls.count() == 0


@pytest.mark.django_db
def test_retry_refuses_a_cancelled_run_with_an_accurate_message(_client, experiment):
    """Finding 3, task 5 review round: retrying a run cancelled MID-FLIGHT
    (with failed calls already archived, unlike the zero-call case above)
    used to raise `RunInFlight` — "a worker may still be writing to it,
    wait for it to finish" — which is FALSE for a run that was deliberately
    cancelled and will never finish on its own. `RunCancelled` names the
    real reason, and the archived failed call must survive untouched, the
    same guarantee `RunInFlight`'s own case already proves.
    """
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    labelling = run.phase_runs.get(phase=3, variant="A")
    system = SystemDescription.objects.create(title="Riverbend", body="b", origin=Origin.SEED)
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    failed_call = LLMCall.objects.create(
        phase_run=labelling,
        sequence=1,
        scenario=scenario,
        parsed_ok=False,
        parse_error="timeout",
    )
    labelling.status = PhaseRun.Status.CANCELLED
    labelling.error = "Cancelled by operator."
    labelling.save(update_fields=["status", "error"])

    with pytest.raises(RunCancelled):
        retry_failed(labelling)

    assert LLMCall.objects.filter(pk=failed_call.pk).exists()
    failed_call.refresh_from_db()
    assert failed_call.parse_error == "timeout"
    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.CANCELLED
    assert labelling.error == "Cancelled by operator."


@pytest.mark.django_db
def test_retry_survives_one_scenario_exploding_mid_retry(_client, experiment):
    """The third instance of a stranding shape this plan has already been
    bitten by twice — `queue_runs`' missing broker guard, and
    `resolve_generation_config` sitting outside `run_phase2`'s try/except.
    `retry_failed` used to call `label_scenario` directly, with no guard, in
    a loop that had ALREADY DELETED the failed calls it was about to
    re-drive. One scenario raising (`label_scenario`'s own call to
    `get_llm_client().complete()` is unguarded — `OpenRouterClient`
    deliberately raises `SecretLeak` if a key-shaped string reaches the
    request body, the same case `run_labelling`'s own guard exists for) used
    to propagate straight out of `retry_failed`: the exploding scenario's
    archived call already deleted with nothing put back in its place,
    `finish_labelling_run` never reached, the run left mid-retry with stale
    counters and status.

    Now `_label_scenario_or_archive_failure` — the SAME function
    `run_labelling`'s own fan-out uses, not a second hand-written copy —
    guards `retry_failed`'s loop too: a failure is recorded against that one
    scenario, the OTHER scenario in the same retry batch still succeeds, and
    the run reaches a real terminal status with counters that match what is
    actually archived.
    """
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.fail_next("boom1")
    _client.fail_next("boom2")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED
    assert labelling.calls.count() == 2
    original_call_ids = set(labelling.calls.values_list("pk", flat=True))

    def exploding(phase_run, scenario, sequence=1):
        if scenario.scenario_id == "sc_01":
            raise RuntimeError("simulated explosion mid-retry")
        return real_label_scenario(phase_run, scenario, sequence=sequence)

    _client.queue(LABELLED)  # sc_02's successful re-drive
    with patch("pipeline.orchestration.label_scenario", side_effect=exploding):
        retried = retry_failed(labelling)

    # A real terminal status, not left mid-retry (one scenario still failed).
    assert retried.status == PhaseRun.Status.FAILED

    # Counters match what is ACTUALLY archived after the retry.
    assert retried.completed_children == 1
    assert retried.failed_children == 1
    assert retried.calls.count() == 2

    exploded = retried.calls.get(scenario__scenario_id="sc_01")
    assert exploded.parsed_ok is False
    assert "simulated explosion mid-retry" in exploded.parse_error

    survived = retried.calls.get(scenario__scenario_id="sc_02")
    assert survived.parsed_ok is True

    # No archived call was destroyed without a replacement: the two
    # ORIGINAL (pre-retry) rows are gone — retry_failed deletes what it
    # re-drives — but two NEW rows, one per scenario, took their place.
    # Never zero for either scenario.
    assert not LLMCall.objects.filter(pk__in=original_call_ids).exists()
    assert Label.objects.filter(phase_run=labelling).count() == 1


@pytest.mark.django_db
def test_retry_on_a_healthy_run_is_a_true_no_op(_client, experiment):
    """I2, the other direction: retrying a run with nothing failed must not
    make any provider call at all — proven here by leaving nothing queued."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.COMPLETED
    calls_before = labelling.calls.count()

    retried = retry_failed(labelling)  # nothing queued: must not call the client

    assert retried.status == PhaseRun.Status.COMPLETED
    assert retried.calls.count() == calls_before
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_a_repetition_fully_repaired_by_retry_is_rolled_up_out_of_failed(_client, experiment):
    """Task 10.2: `execute_pipeline_run` rolls a repetition up once, at the
    end of its own loop, and a retry lands after that. `finish_labelling_run`
    never rolls up, so a repetition whose ONE failed scenario was just
    repaired used to stay FAILED with every phase run under it COMPLETED.
    Both variants run here, so the roll-up has to see the whole repetition,
    not just the phase run that was retried."""
    _client.queue(GENERATED_TWO_SCENARIOS)  # A generates two scenarios
    _client.queue(LABELLED)  # A: sc_01 succeeds
    _client.fail_next("timeout")  # A: sc_02 fails
    _client.queue(GENERATED)  # B generates one scenario
    _client.queue(LABELLED)  # B: sc_01 succeeds
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.FAILED
    failed_at = run.finished_at

    _client.queue(LABELLED)
    retried = retry_failed(run.phase_runs.get(phase=3, variant="A"))

    assert retried.status == PhaseRun.Status.COMPLETED
    run.refresh_from_db()
    assert set(run.phase_runs.values_list("status", flat=True)) == {PhaseRun.Status.COMPLETED}
    assert run.status == PipelineRun.Status.COMPLETED
    assert run.finished_at >= failed_at


@pytest.mark.django_db
def test_a_repetition_only_partly_repaired_by_retry_stays_failed(_client, experiment):
    """The other half of the roll-up above: it re-derives the status, it does
    not simply declare a retried repetition COMPLETED. One of the two failed
    scenarios fails again on retry, so the repetition must stay FAILED."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.fail_next("timeout 1")
    _client.fail_next("timeout 2")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    _client.queue(LABELLED)  # sc_01 is repaired
    _client.fail_next("timeout again")  # sc_02 is not
    retried = retry_failed(run.phase_runs.get(phase=3, variant="A"))

    assert retried.status == PhaseRun.Status.FAILED
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.FAILED


@pytest.mark.django_db
def test_retry_never_rolls_a_cancelled_repetition_out_of_cancelled(_client, experiment):
    """CANCELLED is terminal, and a retry's roll-up must not overwrite it.

    `cancel` refuses a terminal repetition (409 `run_not_cancellable`), and it
    leaves an already-FAILED phase run FAILED. So the realistic way a
    cancelled repetition ends up holding a retriable phase run is an
    operator's cancel landing after labelling finished but before the
    worker's final roll-up. That state is written directly below, the same
    way `test_roll_up_never_overwrites_a_cancelled_run` builds its own.

    The retry itself is legitimate: it refuses only a CANCELLED phase run,
    and this one is FAILED. It repairs the phase run, leaving every phase run
    under the repetition COMPLETED — exactly the shape `roll_up` would read
    as COMPLETED if its CANCELLED guard were not consulted. The
    cancellation, and when it happened, must both survive.
    """
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED

    cancelled_at = timezone.now()
    PipelineRun.objects.filter(pk=run.pk).update(
        status=PipelineRun.Status.CANCELLED, finished_at=cancelled_at
    )

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert retried.status == PhaseRun.Status.COMPLETED
    run.refresh_from_db()
    assert set(run.phase_runs.values_list("status", flat=True)) == {PhaseRun.Status.COMPLETED}
    assert run.status == PipelineRun.Status.CANCELLED
    assert run.finished_at == cancelled_at


@pytest.mark.django_db
def test_retry_does_not_destroy_an_archived_call_whose_scenario_was_deleted(_client, experiment):
    """Round 6 review, finding 2 — CONFIRMED, pre-existing: `LLMCall.scenario`
    is SET_NULL, and the admin permits deleting a GENERATED scenario. The
    naive `retry_failed` built its re-drive list by dropping any failed call
    whose `scenario_id` was null — silently excluding it from the re-drive —
    but then deleted `failed_calls(phase_run)` WHOLESALE, that orphaned call
    included: the only archived record of that failure was destroyed with
    nothing left in its place, and the run flipped FAILED -> COMPLETED with
    counters (1/0/0) that no longer described anything real. That is
    precisely what `retry_failed`'s own docstring says must never happen —
    an archived call consumed without a replacement.

    sc_01 and sc_02 both fail; sc_01's scenario is then deleted (mirroring
    the admin's `Scenario` delete, which is exactly what a generated
    scenario permits). Retrying must leave sc_01's failed call archived
    untouched — there is no scenario left to re-label it against — while
    sc_02, still live, is retried normally. The run must still read FAILED,
    not a false COMPLETED.
    """
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.fail_next("timeout")  # sc_01 fails
    _client.fail_next("timeout")  # sc_02 fails
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED
    assert labelling.calls.count() == 2

    orphaned_call = labelling.calls.get(scenario__scenario_id="sc_01")
    orphaned_call.scenario.delete()
    orphaned_call.refresh_from_db()
    assert orphaned_call.scenario_id is None  # SET_NULL fired, as it does in production

    _client.queue(LABELLED)  # sc_02's retry succeeds
    retried = retry_failed(labelling)

    # sc_01's failure still counts: the run must not silently flip healthy.
    assert retried.status == PhaseRun.Status.FAILED
    assert retried.failed_children == 1
    assert retried.completed_children == 1
    # The orphaned call was never deleted and nothing replaced it — the
    # exact guarantee `_label_scenario_or_archive_failure`'s own docstring
    # names: "a retry must never be able to consume [an archived call]
    # without leaving a replacement in its place."
    assert LLMCall.objects.filter(pk=orphaned_call.pk).exists()
    assert labelling.calls.count() == 2
    assert Label.objects.count() == 1  # sc_02's label, from the successful retry


@pytest.mark.django_db
def test_a_non_database_error_from_persist_updates_the_existing_call_not_a_duplicate(
    _client, experiment
):
    """M7: label_scenario saves its call with parsed_ok=True BEFORE calling
    _persist. If _persist raises something other than DatabaseError, that
    escapes label_scenario uncaught and lands in run_labelling's broad
    guard (the same one the SecretLeak fix relies on) — which must update
    the call already archived for that scenario rather than leave it
    falsely claiming success next to a second, duplicate row."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    _client.queue(LABELLED)
    with patch("pipeline.labelling._persist", side_effect=RuntimeError("boom")):
        run_labelling(labelling)

    assert labelling.calls.count() == 1
    call = labelling.calls.get()
    assert call.parsed_ok is False
    assert "boom" in call.parse_error
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_a_labelling_run_whose_source_was_deleted_fails_instead_of_labelling_everything(
    _client, experiment
):
    """M8: source_phase_run is SET_NULL on delete. Deleting a phase-2 run
    must not silently make its labelling run fan out over the entire
    corpus — variant independence must not collapse just because one row
    was deleted — and zero calls from that empty fan-out must not read as a
    silent COMPLETED."""
    experiment.variants = ["A"]
    experiment.save()

    # Already in the corpus, unrelated to this run's own (not yet created)
    # generated material, so "fan out over everything" and "fan out over
    # nothing" are distinguishable.
    other_system = SystemDescription.objects.create(title="Other", body="x", origin=Origin.SEED)
    Scenario.objects.create(
        system_description=other_system,
        scenario_id="unrelated",
        origin=Origin.SEED,
        order=1,
    )

    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")

    generation.delete()
    labelling.refresh_from_db()
    assert labelling.source_phase_run_id is None

    result = run_labelling(labelling)

    assert result.status == PhaseRun.Status.FAILED
    assert result.calls.count() == 0
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_launch_continues_numbering_across_calls(experiment):
    """M4: repetition numbering must continue, not restart, across separate
    launch() calls for the same experiment."""
    first = launch(experiment, n_repetitions=2)
    assert [r.repetition_index for r in first] == [1, 2]

    second = launch(experiment, n_repetitions=3)
    assert [r.repetition_index for r in second] == [3, 4, 5]


@pytest.mark.django_db
def test_roll_up_reports_completed_only_when_every_phase_did(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.update(status=PhaseRun.Status.COMPLETED)
    assert roll_up(run).status == PipelineRun.Status.COMPLETED

    run.phase_runs.filter(phase=3, variant="B").update(status=PhaseRun.Status.FAILED)
    assert roll_up(run).status == PipelineRun.Status.FAILED


@pytest.mark.django_db
def test_roll_up_reports_running_while_any_phase_is_pending_or_running(experiment):
    """M1: no prior test covered the RUNNING branch at all."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.COMPLETED)
    run.phase_runs.filter(phase=3, variant="A").update(status=PhaseRun.Status.RUNNING)
    # phase 2/3 variant B are left at their default, PENDING.

    assert roll_up(run).status == PipelineRun.Status.RUNNING


@pytest.mark.django_db
def test_roll_up_does_not_stamp_finished_at_while_running(experiment):
    """M2: finished_at must only be set on a terminal transition — setting
    it while still RUNNING would claim the run finished when it has not."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.COMPLETED)

    rolled = roll_up(run)

    assert rolled.status == PipelineRun.Status.RUNNING
    assert rolled.finished_at is None


@pytest.mark.django_db
def test_roll_up_treats_queued_the_same_as_pending_or_running(experiment):
    """A repetition still sitting in Django-Q's queue, blocked on a free
    worker, must roll up as RUNNING (in progress), never FAILED — QUEUED has
    to join {PENDING, RUNNING} in roll_up's status set."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.COMPLETED)
    # Every other phase run QUEUED — none left PENDING or RUNNING — so this
    # isolates QUEUED itself; without it in roll_up's set, this rolls up FAILED.
    run.phase_runs.exclude(phase=2, variant="A").update(status=PhaseRun.Status.QUEUED)

    assert roll_up(run).status == PipelineRun.Status.RUNNING


@pytest.mark.django_db
def test_roll_up_reports_queued_when_every_phase_run_is_queued(experiment):
    """M11: roll_up had no way to express QUEUED at all — a fully-queued
    repetition (handed to Django-Q, no worker has touched it yet) rolled up
    to RUNNING, erasing the queued/started distinction this whole feature
    exists to expose for any future status endpoint built on top of it."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.update(status=PhaseRun.Status.QUEUED)

    assert roll_up(run).status == PipelineRun.Status.QUEUED


@pytest.mark.django_db
def test_roll_up_reports_queued_for_a_pending_and_queued_mix_with_none_running(experiment):
    """The broader case: some phase runs QUEUED, the rest still PENDING (not
    yet reached by the CLI's cascade — or a future partial-queue path), but
    none RUNNING yet. Still QUEUED, not RUNNING."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.QUEUED)
    # Every other phase run left PENDING.

    assert roll_up(run).status == PipelineRun.Status.QUEUED


@pytest.mark.django_db
def test_roll_up_reports_running_not_queued_when_nothing_was_ever_queued(experiment):
    """All-PENDING must NOT read as QUEUED — nothing was actually handed to
    Django-Q yet, so calling it "queued" would be a mislabel. Unchanged from
    before this fix: a fresh, untouched repetition still reports RUNNING."""
    run = launch(experiment, n_repetitions=1)[0]

    assert roll_up(run).status == PipelineRun.Status.RUNNING


@pytest.mark.django_db
def test_roll_up_of_zero_phase_runs_is_not_vacuously_completed(experiment):
    """M3: `set() <= {COMPLETED}` is vacuously True, so a PipelineRun with no
    phase runs at all must be guarded explicitly rather than falling
    through to a false COMPLETED."""
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    assert run.phase_runs.count() == 0

    rolled = roll_up(run)

    assert rolled.status == PipelineRun.Status.RUNNING
    assert rolled.finished_at is None


@pytest.mark.django_db
def test_roll_up_never_overwrites_a_cancelled_run(experiment):
    """Archive-fidelity fix, task 5 follow-up: `roll_up` used to re-derive
    `PipelineRun.status` purely from its phase runs' current statuses, with
    no idea CANCELLED could mean "an operator deliberately stopped this",
    not "still in progress". Every phase run CANCELLED is neither `<=
    {COMPLETED}` nor in `roll_up`'s own `in_progress` set, so it fell into
    the FAILED branch — silently turning a deliberate cancellation into a
    false "this run failed" the moment anything (a stale worker's
    `execute_pipeline_run` loop reaching its own `roll_up(pipeline_run)`
    call, say) rolled it up again afterward. Those are different, and
    wrong-in-a-different-way, facts about what happened to the experiment.
    Once CANCELLED, nothing should re-derive this run's status from its
    children again — proven here by cancelling AFTER already setting
    the phase runs to a shape that would otherwise roll up FAILED.
    """
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.COMPLETED)
    run.phase_runs.filter(phase=2, variant="B").update(status=PhaseRun.Status.FAILED)
    # Without the CANCELLED guard, {COMPLETED, FAILED, PENDING, ...} is
    # neither all-COMPLETED nor free of in-progress statuses in a way that
    # matters here — FAILED alone (not in `in_progress`) is enough to send
    # an un-cancelled run down the FAILED branch, which is exactly the
    # regression this test must catch.
    run.status = PipelineRun.Status.CANCELLED
    run.finished_at = None
    run.save(update_fields=["status", "finished_at"])

    rolled = roll_up(run)

    assert rolled.status == PipelineRun.Status.CANCELLED
    assert rolled.finished_at is None


@pytest.mark.django_db
def test_execute_pipeline_run_does_not_resurrect_a_run_cancelled_after_it_was_fetched(experiment):
    """Finding 2, task 5 review round: the CANCELLED sweep missed
    `execute_pipeline_run` itself. Its entry guard used to read the
    possibly-STALE in-memory `pipeline_run.status` and then unconditionally
    write RUNNING — precisely what would defeat `roll_up`'s own CANCELLED
    guard one call later, since `roll_up` would then read back RUNNING (this
    function's own write) and re-derive a status from the phase runs as if
    nothing had been cancelled. The window is widest in `run_experiment
    --inline`, which loops over `PipelineRun` objects fetched back at
    `launch()` time — repetition N's copy can be stale for however long
    repetitions 1..N-1 take to run. Reproduced here directly: a worker
    fetches a QUEUED run into `stale`, an operator cancels the REAL row in
    the database, then the worker's stale copy is finally handed to
    `execute_pipeline_run`.
    """
    run = launch(experiment, n_repetitions=1)[0]
    run.status = PipelineRun.Status.QUEUED
    run.save(update_fields=["status"])
    run.phase_runs.update(status=PhaseRun.Status.QUEUED)

    stale = PipelineRun.objects.get(pk=run.pk)  # the worker's own, soon-stale copy
    assert stale.status == PipelineRun.Status.QUEUED

    run.status = PipelineRun.Status.CANCELLED
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at"])
    run.phase_runs.update(status=PhaseRun.Status.CANCELLED, error="Cancelled by operator.")

    execute_pipeline_run(stale)

    run.refresh_from_db()
    assert run.status == PipelineRun.Status.CANCELLED
    assert set(run.phase_runs.values_list("status", flat=True)) == {PhaseRun.Status.CANCELLED}


@pytest.mark.django_db
def test_launch_with_no_key_configured_creates_nothing_and_raises(_client, experiment, settings):
    """C2a: spec §12 — with no key from either source, launching must fail
    before any HTTP call is made, and (because `launch` is atomic and resolves
    the key first) before a single row exists. Otherwise the repetition is
    created, `run_phase2` explodes on the unguarded client call, and the run is
    stranded RUNNING with no way to re-drive it past the idempotency guard."""
    settings.OPENROUTER_API_KEY = ""
    config = AppSettings.load()
    config.openrouter_key = ""
    config.save()

    with pytest.raises(MissingAPIKey):
        launch(experiment, n_repetitions=1)

    assert PipelineRun.objects.count() == 0
    assert PhaseRun.objects.count() == 0
    assert _client.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [MissingAPIKey("No OpenRouter API key is configured."), RuntimeError("something arbitrary")],
)
def test_an_arbitrary_client_error_fails_the_run_instead_of_stranding_it(
    _client, experiment, error
):
    """C2b: `run_phase2`'s client call was unguarded and `execute_pipeline_run`
    does not wrap it, so anything the client raises escaped the whole pipeline —
    leaving the PipelineRun `running` with its phase runs `running`/`pending`,
    and a second `execute_pipeline_run` just re-rolls it up to RUNNING forever."""
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]

    class Exploding:
        def complete(self, **kwargs):
            raise error

    set_llm_client(Exploding())
    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.FAILED
    assert run.finished_at is not None
    generation = run.phase_runs.get(phase=2, variant="A")
    assert generation.status == PhaseRun.Status.FAILED
    assert str(error) in generation.error
    assert generation.finished_at is not None


@pytest.mark.django_db
def test_execute_pipeline_run_executes_a_queued_run(_client, experiment):
    """A run the CLI marked QUEUED before dispatching to Django-Q must still
    actually execute once a worker picks it up — the idempotency guard must
    accept QUEUED alongside PENDING, not just roll up the (still-empty) run
    and leave it stranded."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    run.status = PipelineRun.Status.QUEUED
    run.save(update_fields=["status"])
    run.phase_runs.update(status=PhaseRun.Status.QUEUED)

    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_run_phase2_executes_a_queued_run(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    generation.status = PhaseRun.Status.QUEUED
    generation.save(update_fields=["status"])

    result = run_phase2(generation)

    assert result.status == PhaseRun.Status.COMPLETED


@pytest.mark.django_db
def test_run_labelling_executes_a_queued_run(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)
    labelling.status = PhaseRun.Status.QUEUED
    labelling.save(update_fields=["status"])

    result = run_labelling(labelling)

    assert result.status == PhaseRun.Status.COMPLETED


@pytest.mark.django_db
def test_run_labelling_refuses_a_phase_two_phase_run(_client, experiment):
    """The guard against a shape of bug this codebase has been bitten by more
    than once: a check that lives only at a CALLER (`execute_pipeline_run`
    only ever schedules labelling for a genuine `phase=3` row; so does the
    API) rather than at the function that actually owns the guarantee.
    `label_scenario` renders through `build_phase3_context`, which ALWAYS
    injects the taxonomy — correct for phase 3, the labeller — and never
    attaches the phase-2 final-request choke point
    (`pipeline.generation._preflight`) at all. A hand-invoked
    `run_labelling`/`run_phase3_task` against a phase-2 `PhaseRun` id would
    otherwise render that run's phase-2 template under a taxonomy-bearing
    context with no scan protecting it. Not reachable from any production
    caller today, but refused here regardless, before a provider is ever
    contacted."""
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")

    result = run_labelling(generation)

    assert result.status == PhaseRun.Status.FAILED
    assert "phase-3" in result.error
    assert LLMCall.objects.count() == 0
    assert len(_client.calls) == 0


@pytest.mark.django_db
def test_run_labelling_does_not_re_execute_a_finished_run(_client, experiment):
    """M1: `run_labelling` had no re-entry guard, so calling it twice made two
    provider calls for one scenario — double-counting tokens and cost — while
    `get_or_create` quietly hid the duplication on the labels themselves."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.COMPLETED
    calls_before = LLMCall.objects.count()
    finished_at = labelling.finished_at

    # Nothing queued: a second execution would make a real call and raise.
    again = run_labelling(labelling)

    assert again.status == PhaseRun.Status.COMPLETED
    assert again.finished_at == finished_at
    assert LLMCall.objects.count() == calls_before
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_run_labelling_does_not_resurrect_a_cancelled_run(_client, experiment):
    """The phase-3 sibling of `test_generation
    .test_run_phase2_does_not_resurrect_a_cancelled_run`: `is_terminal`
    deliberately excludes CANCELLED (so `retry_failed` can legitimately
    re-drive a run cancelled because its generation never completed — but
    that path never calls `run_labelling`, only `label_scenario` +
    `finish_labelling_run` directly, so this guard cannot interfere with
    it). Without it, a `phase_run` an operator cancelled moments before a
    worker reaches it gets resurrected: a whole new fan-out of real provider
    calls. Nothing is queued on `_client`, so any such call would raise.
    """
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    labelling = run.phase_runs.get(phase=3, variant="A")
    labelling.status = PhaseRun.Status.CANCELLED
    labelling.error = "Cancelled by operator."
    labelling.total_children = 3
    labelling.completed_children = 1
    labelling.failed_children = 0
    labelling.save(
        update_fields=["status", "error", "total_children", "completed_children", "failed_children"]
    )

    result = run_labelling(labelling)

    assert result.status == PhaseRun.Status.CANCELLED
    assert result.error == "Cancelled by operator."
    assert result.total_children == 3
    assert result.completed_children == 1
    assert _client.calls == []
    assert result.calls.count() == 0


@pytest.mark.django_db
def test_run_labelling_sets_total_children_before_the_fan_out(_client, experiment):
    """total_children must reflect the scenario count from the start, so a
    caller polling mid-run sees a real denominator, not zero."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    run_labelling(labelling)
    labelling.refresh_from_db()

    assert labelling.total_children == 2
    assert labelling.completed_children == 2
    assert labelling.failed_children == 0
    assert labelling.progress == {"done": 2, "total": 2, "failed": 0, "percent": 100}


@pytest.mark.django_db
def test_run_labelling_increments_completed_and_failed_children_live(_client, experiment):
    """A mix of one success and one failure must land in the right counter —
    and both must be visible on the SAME object the loop mutates, not only
    after a refresh, since that is what a live progress hook reads."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    finished = run_labelling(labelling)

    assert finished.completed_children == 1
    assert finished.failed_children == 1
    assert finished.progress == {"done": 2, "total": 2, "failed": 1, "percent": 100}


@pytest.mark.django_db
def test_run_labelling_counts_an_exploding_scenario_as_failed(_client, experiment):
    """The broad except-guard path (a scenario that raises rather than
    returning a failed call) must also land in failed_children, not be
    silently dropped from the live counters."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    def exploding(phase_run, scenario, sequence=1):
        if scenario.scenario_id == "sc_01":
            raise RuntimeError("simulated explosion")
        return real_label_scenario(phase_run, scenario, sequence=sequence)

    _client.queue(LABELLED)
    with patch("pipeline.orchestration.label_scenario", side_effect=exploding):
        finished = run_labelling(labelling)

    assert finished.total_children == 2
    assert finished.completed_children == 1
    assert finished.failed_children == 1


@pytest.mark.django_db
def test_run_labelling_calls_on_progress_after_each_scenario(_client, experiment):
    """The CLI's --inline progress lines hinge on this: on_progress must fire
    once per scenario, live, with the running totals at that point — not just
    once at the very end. M7 adds one more call after finish_labelling_run
    itself, so a zero-scenario fan-out still reports something (see
    test_run_labelling_calls_on_progress_once_for_zero_scenarios) — here,
    with two real scenarios, that shows up as a third call carrying the same
    final numbers as the second."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    seen = []
    run_labelling(labelling, on_progress=lambda pr: seen.append(dict(pr.progress)))

    assert len(seen) == 3
    assert seen[0] == {"done": 1, "total": 2, "failed": 0, "percent": 50}
    assert seen[1] == {"done": 2, "total": 2, "failed": 1, "percent": 100}
    assert seen[2] == {"done": 2, "total": 2, "failed": 1, "percent": 100}


@pytest.mark.django_db
def test_run_labelling_resets_completed_and_failed_children_on_re_entry(_client, experiment):
    """M4: total_children was reset before the fan-out but completed_children
    and failed_children were only ever += 'd — so re-entering a non-terminal
    run (a RUNNING phase run picked up again after, say, a Django-Q retry)
    accumulated on top of whatever a previous, unfinished attempt had left
    behind. Observed live: done=3/total=2 -> a 150% progress bar. Proven via
    the live on_progress callback, not the final state — finish_labelling_run
    already recomputes the final numbers fresh, which would mask a reset bug
    that only shows up mid-run."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    # A stale, half-finished previous attempt left these behind.
    labelling.status = PhaseRun.Status.RUNNING
    labelling.total_children = 2
    labelling.completed_children = 3
    labelling.failed_children = 1
    labelling.save()

    seen = []
    run_labelling(labelling, on_progress=lambda pr: seen.append(dict(pr.progress)))

    assert seen[0] == {"done": 1, "total": 2, "failed": 0, "percent": 50}


@pytest.mark.django_db
def test_run_labelling_calls_on_progress_once_for_zero_scenarios(_client, experiment):
    """M7: on_progress previously fired only inside the scenario loop, so a
    Phase 3 run over zero scenarios (its source phase run deleted after
    launch — scenarios_for returns none()) printed nothing at all, leaving
    that repetition looking stuck rather than reporting its (failed, 0/0)
    outcome."""
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    generation.delete()
    labelling.refresh_from_db()
    assert labelling.source_phase_run_id is None

    seen = []
    run_labelling(labelling, on_progress=lambda pr: seen.append(dict(pr.progress)))

    assert len(seen) == 1
    assert seen[0] == {"done": 0, "total": 0, "failed": 0, "percent": 0}


@pytest.mark.django_db
def test_retry_failed_reconciles_completed_children_too(_client, experiment):
    """M5's retry scenario, extended: once the previously-failed scenario
    succeeds on retry, completed_children must catch up too — not just
    failed_children — or progress would understate a fully-finished run."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.completed_children == 1
    assert labelling.failed_children == 1

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert retried.completed_children == 2
    assert retried.failed_children == 0
    assert retried.progress == {"done": 2, "total": 2, "failed": 0, "percent": 100}


@pytest.mark.django_db
def test_retry_refuses_a_run_still_in_flight(_client, experiment):
    """The whole-branch review's proof, reproduced directly: a phase-3 run
    RUNNING with total=5 completed=0 failed=1 (a worker mid fan-out) used to
    let `retry_failed` return normally — deleting the one archived failed
    call and flipping the run RUNNING -> completed with incoherent counters,
    while the worker kept writing to a row now marked terminal.

    `retry_failed` must raise `RunInFlight` here — called directly, not
    through the API view — because task 2 already guarded the view layer and
    this task's whole point is that the guard belongs INSIDE `retry_failed`
    itself, so a management command or any other future caller is protected
    the same way. `PhaseRunViewSet.retry`'s 409 (see test_run_api.py) is a
    thin wrapper around this same exception.

    Every value the bug destroyed is asserted survived byte-for-byte: the
    call's pk, its `parsed_ok`/`parse_error`, and the run's own status,
    counters and `finished_at`.
    """
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    labelling = run.phase_runs.get(phase=3, variant="A")
    system = SystemDescription.objects.create(title="Riverbend", body="b", origin=Origin.SEED)
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    labelling.status = PhaseRun.Status.RUNNING
    labelling.total_children = 5
    labelling.completed_children = 0
    labelling.failed_children = 1
    labelling.save(
        update_fields=["status", "total_children", "completed_children", "failed_children"]
    )
    failed_call = LLMCall.objects.create(
        phase_run=labelling,
        sequence=1,
        scenario=scenario,
        parsed_ok=False,
        parse_error="timeout",
    )
    call_pk = failed_call.pk

    with pytest.raises(RunInFlight):
        retry_failed(labelling)

    assert LLMCall.objects.filter(pk=call_pk).exists()
    failed_call.refresh_from_db()
    assert failed_call.parsed_ok is False
    assert failed_call.parse_error == "timeout"
    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.RUNNING
    assert labelling.total_children == 5
    assert labelling.completed_children == 0
    assert labelling.failed_children == 1
    assert labelling.finished_at is None


@pytest.mark.django_db
def test_run_labelling_does_not_re_execute_a_failed_run(_client, experiment):
    """The other terminal status: a FAILED run is retried through
    `retry_failed`, which re-drives only the failed children, never by calling
    `run_labelling` again over the whole fan-out."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED
    calls_before = LLMCall.objects.count()

    again = run_labelling(labelling)

    assert again.status == PhaseRun.Status.FAILED
    assert LLMCall.objects.count() == calls_before


@pytest.mark.django_db
def test_queue_phase_run_marks_it_queued_and_dispatches_to_the_right_task(experiment):
    """`queue_phase_run` is the ad-hoc (iterate-mode) counterpart of
    `queue_runs` — task 1's new enqueue seam, built on the same
    `_hand_to_broker` primitive rather than a third, hand-written
    `async_task` call. A phase-2 ad-hoc run dispatches to `run_phase2_task`;
    a phase-3 one to `run_phase3_task` — `PHASE_RUN_TASKS` keyed by
    `phase_run.phase`, mirroring `RUN_TASK`'s own "exact string, asserted
    on" contract for `run_pipeline_task`.
    """
    generation = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=experiment.phase2_prompt_a,
        taxonomy=experiment.taxonomy,
    )
    labelling = PhaseRun.objects.create(
        phase=3,
        model="m3",
        prompt_template=experiment.phase3_prompt,
        taxonomy=experiment.taxonomy,
        source_phase_run=generation,
    )

    with patch("pipeline.orchestration.async_task") as enqueued:
        queue_phase_run(generation)
        queue_phase_run(labelling)

    generation.refresh_from_db()
    labelling.refresh_from_db()
    assert generation.status == PhaseRun.Status.QUEUED
    assert labelling.status == PhaseRun.Status.QUEUED
    assert enqueued.call_args_list[0].args == ("pipeline.tasks.run_phase2_task", generation.pk)
    assert enqueued.call_args_list[1].args == ("pipeline.tasks.run_phase3_task", labelling.pk)


@pytest.mark.django_db
def test_queue_phase_run_reverts_to_pending_and_raises_clearly_on_a_broker_failure(experiment):
    """The same guarantee `queue_runs`' own broker-failure test proves for a
    `PipelineRun`, one level down for a bare ad-hoc `PhaseRun`: QUEUED
    claiming "handed to django-q" with nothing actually enqueued is worse
    than useless, so a broker error reverts this phase run to PENDING and
    raises a clear, specific error — never leaves it silently stuck at
    QUEUED forever.
    """
    generation = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=experiment.phase2_prompt_a,
        taxonomy=experiment.taxonomy,
    )

    with (
        patch("pipeline.orchestration.async_task", side_effect=RuntimeError("no broker")),
        pytest.raises(PhaseRunQueueingError, match="reverted to pending") as caught,
    ):
        queue_phase_run(generation)

    assert caught.value.phase_run == generation
    generation.refresh_from_db()
    assert generation.status == PhaseRun.Status.PENDING
