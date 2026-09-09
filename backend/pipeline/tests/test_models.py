import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def templates(db):
    return (
        PromptTemplate.objects.create_version(phase=2, name="gen", user_prompt="g"),
        PromptTemplate.objects.create_version(phase=3, name="lab", user_prompt="l"),
    )


@pytest.fixture
def seed(db):
    return SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)


@pytest.fixture
def experiment(db, taxonomy, templates, seed):
    gen, lab = templates
    exp = Experiment.objects.create(
        name="baseline",
        taxonomy=taxonomy,
        phase2_model="anthropic/claude-sonnet-4",
        phase2_prompt=gen,
        phase3_model="openai/gpt-5",
        phase3_prompt=lab,
        n_systems=2,
        n_scenarios_per_system=3,
    )
    exp.seed_systems.add(seed)
    return exp


@pytest.mark.django_db
def test_defaults_run_both_variants(experiment):
    assert experiment.variants == ["A", "B"]
    assert experiment.generate_systems is True
    assert experiment.generate_scenarios is True


@pytest.mark.django_db
def test_both_toggles_off_is_rejected(experiment):
    experiment.generate_systems = False
    experiment.generate_scenarios = False
    with pytest.raises(ValidationError, match="at least one"):
        experiment.full_clean()


@pytest.mark.django_db
def test_not_generating_systems_requires_a_target(experiment):
    experiment.generate_systems = False
    with pytest.raises(ValidationError, match="target_system"):
        experiment.full_clean()


@pytest.mark.django_db
def test_not_generating_systems_is_fine_with_a_target(experiment, seed):
    experiment.generate_systems = False
    experiment.target_system = seed
    experiment.full_clean()


@pytest.mark.django_db
def test_a_generated_system_may_not_seed_an_experiment(experiment):
    """Seeding a run with a prior run's output would contaminate the comparison."""
    generated = SystemDescription.objects.create(
        title="From a model", body="x", origin=Origin.GENERATED
    )
    experiment.seed_systems.add(generated)
    with pytest.raises(ValidationError, match="generated"):
        experiment.full_clean()


@pytest.mark.django_db
def test_an_unknown_variant_is_rejected(experiment):
    experiment.variants = ["A", "C"]
    with pytest.raises(ValidationError, match="C"):
        experiment.full_clean()


@pytest.mark.django_db
def test_phase_run_knows_whether_it_is_unbiased(experiment, taxonomy, templates):
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    a = PhaseRun.objects.create(
        pipeline_run=run,
        phase=2,
        variant="A",
        model="m",
        prompt_template=templates[0],
        taxonomy=taxonomy,
    )
    b = PhaseRun.objects.create(
        pipeline_run=run,
        phase=2,
        variant="B",
        model="m",
        prompt_template=templates[0],
        taxonomy=taxonomy,
    )
    assert a.is_unbiased is True
    assert b.is_unbiased is False


@pytest.mark.django_db
def test_a_phase_run_may_be_ad_hoc_with_no_pipeline_run(experiment, taxonomy, templates):
    run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m",
        prompt_template=templates[0],
        taxonomy=taxonomy,
    )
    assert run.pipeline_run is None
    assert run.status == PhaseRun.Status.PENDING


@pytest.mark.django_db
def test_a_repetition_index_is_unique_per_experiment(experiment):
    PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    with pytest.raises(IntegrityError):
        PipelineRun.objects.create(experiment=experiment, repetition_index=1)


@pytest.mark.django_db
def test_a_repetition_index_may_repeat_across_experiments(experiment, taxonomy, templates, seed):
    """The constraint is scoped to one experiment, not global."""
    other = Experiment.objects.create(
        name="second experiment",
        taxonomy=taxonomy,
        phase2_model="m",
        phase2_prompt=templates[0],
        phase3_model="m",
        phase3_prompt=templates[1],
    )
    PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    second_run = PipelineRun.objects.create(experiment=other, repetition_index=1)
    assert second_run.repetition_index == 1


@pytest.mark.django_db
def test_generated_material_points_back_at_the_run(experiment, taxonomy, templates, seed):
    run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m",
        prompt_template=templates[0],
        taxonomy=taxonomy,
    )
    system = SystemDescription.objects.create(
        title="Generated", body="x", origin=Origin.GENERATED, generation_run=run
    )
    scenario = Scenario.objects.create(
        system_description=system,
        scenario_id="sc_01",
        origin=Origin.GENERATED,
        generation_run=run,
        order=1,
    )
    assert system.generation_run == run
    assert scenario.generation_run == run
    assert run.systems.count() == 1
    assert run.scenarios.count() == 1


@pytest.mark.django_db
def test_deleting_a_run_cascades_to_the_material_it_produced(experiment, taxonomy, templates, seed):
    """Generated material is removed per run, never per row — CASCADE is how."""
    run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m",
        prompt_template=templates[0],
        taxonomy=taxonomy,
    )
    system = SystemDescription.objects.create(
        title="Generated", body="x", origin=Origin.GENERATED, generation_run=run
    )
    scenario = Scenario.objects.create(
        system_description=system,
        scenario_id="sc_01",
        origin=Origin.GENERATED,
        generation_run=run,
        order=1,
    )
    run.delete()
    assert not SystemDescription.objects.filter(pk=system.pk).exists()
    assert not Scenario.objects.filter(pk=scenario.pk).exists()


@pytest.mark.django_db
def test_two_runs_may_produce_the_same_scenario_id_for_one_system(
    experiment, taxonomy, templates, seed
):
    """Generating repeatedly against one target system must not collide on sc_01."""
    first = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    second = PhaseRun.objects.create(
        phase=2, variant="B", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    Scenario.objects.create(
        system_description=seed,
        scenario_id="sc_01",
        origin=Origin.GENERATED,
        generation_run=first,
        order=1,
    )
    Scenario.objects.create(
        system_description=seed,
        scenario_id="sc_01",
        origin=Origin.GENERATED,
        generation_run=second,
        order=1,
    )
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 2


@pytest.mark.django_db
def test_one_run_may_not_repeat_a_scenario_id_for_one_system(experiment, taxonomy, templates, seed):
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    Scenario.objects.create(
        system_description=seed,
        scenario_id="sc_01",
        origin=Origin.GENERATED,
        generation_run=run,
        order=1,
    )
    with pytest.raises(IntegrityError):
        Scenario.objects.create(
            system_description=seed,
            scenario_id="sc_01",
            origin=Origin.GENERATED,
            generation_run=run,
            order=2,
        )


@pytest.mark.django_db
def test_a_label_is_unique_per_run_scenario_and_hcv(experiment, taxonomy, templates, seed):
    run = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=templates[1], taxonomy=taxonomy
    )
    scenario = Scenario.objects.create(
        system_description=seed, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    hcv = HCV.objects.get(hcv_id="HCV-07")
    Label.objects.create(phase_run=run, scenario=scenario, hcv=hcv, evidence="e")
    with pytest.raises(IntegrityError):
        Label.objects.create(phase_run=run, scenario=scenario, hcv=hcv, evidence="e2")


@pytest.mark.django_db
def test_a_label_defaults_to_an_llm_source(experiment, taxonomy, templates, seed):
    run = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=templates[1], taxonomy=taxonomy
    )
    scenario = Scenario.objects.create(
        system_description=seed, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    label = Label.objects.create(
        phase_run=run, scenario=scenario, hcv=HCV.objects.get(hcv_id="HCV-07")
    )
    assert label.source == Label.Source.LLM
    assert label.created_by is None


@pytest.mark.django_db
def test_an_llm_call_records_ok_from_its_payloads(experiment, taxonomy, templates):
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    good = LLMCall.objects.create(
        phase_run=run,
        sequence=1,
        request_json={"model": "m"},
        response_json={"id": "x"},
        raw_response="{}",
        parsed_ok=True,
    )
    failed = LLMCall.objects.create(
        phase_run=run,
        sequence=2,
        request_json={"model": "m"},
        transport_error="RateLimitError: 429",
    )
    partial = LLMCall.objects.create(
        phase_run=run,
        sequence=3,
        request_json={"model": "m"},
        response_json={"id": "x"},
        transport_error="Timeout after response headers",
    )
    assert good.ok is True
    assert failed.ok is False
    assert partial.ok is False


@pytest.mark.django_db
def test_a_generated_system_may_not_be_the_target_system(experiment):
    """C1: `target_system.body` is exposed to the prompt of BOTH variants, so a
    system written by a model that had the 21 HCVs in front of it would reach a
    variant A prompt. The leak backstop cannot catch that — paraphrased,
    taxonomy-derived prose trips no identifier match and stays under the
    three-distinct-name threshold — so it has to be refused here, exactly as a
    generated seed_systems entry already is."""
    generated = SystemDescription.objects.create(
        title="From a model",
        body="Watch for cognitive overload and alarm fatigue.",
        origin=Origin.GENERATED,
    )
    experiment.generate_systems = False
    experiment.target_system = generated

    with pytest.raises(ValidationError, match="generated"):
        experiment.full_clean()


@pytest.mark.django_db
@pytest.mark.parametrize("origin", [Origin.SEED, Origin.MANUAL])
def test_a_seed_or_manual_system_is_still_an_acceptable_target(experiment, origin):
    target = SystemDescription.objects.create(title=f"Target {origin}", body="x", origin=origin)
    experiment.generate_systems = False
    experiment.target_system = target

    experiment.full_clean()


@pytest.mark.django_db
def test_a_taxonomy_with_no_hcv_rows_is_refused_at_validation(experiment):
    """I5: variant A's leak backstop scans the rendered prompt against the guard
    taxonomy's rows. With zero rows it cannot run at all — the run completes with
    `leak_scan_ran: False` and only a swallowed warning, which is the isolation
    guarantee silently switched off. Caught at launch instead."""
    empty = Taxonomy.objects.create(vocabulary_id="HCV-21", version="empty")
    experiment.taxonomy = empty

    with pytest.raises(ValidationError, match="HCV"):
        experiment.full_clean()


@pytest.mark.django_db
def test_a_repeated_variant_is_rejected(experiment):
    """M2: ["A", "A"] used to pass, producing a repetition with two A arms."""
    experiment.variants = ["A", "A"]
    with pytest.raises(ValidationError, match="[Dd]uplicate"):
        experiment.full_clean()


@pytest.mark.django_db
def test_an_unknown_and_a_duplicate_variant_are_both_reported(experiment):
    experiment.variants = ["A", "A", "C"]
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    reported = " ".join(caught.value.message_dict["variants"])
    assert "C" in reported
    assert "duplicate" in reported.lower()


@pytest.mark.django_db
def test_a_phase_three_template_may_not_be_the_phase_two_prompt(experiment, templates):
    """M3: nothing stopped an experiment pinning a labelling template as its
    generation prompt (or the reverse) — the mistake only surfaced as a baffling
    render or a wasted run."""
    _, labeller = templates
    experiment.phase2_prompt = labeller
    with pytest.raises(ValidationError, match="phase 2"):
        experiment.full_clean()


@pytest.mark.django_db
def test_a_phase_two_template_may_not_be_the_phase_three_prompt(experiment, templates):
    generator, _ = templates
    experiment.phase3_prompt = generator
    with pytest.raises(ValidationError, match="phase 3"):
        experiment.full_clean()


@pytest.mark.django_db
def test_a_label_records_the_call_that_produced_it(experiment, taxonomy, templates, seed):
    """I6: spec §9 requires the export to carry the call behind each label. The
    (phase_run, scenario) join that derives it today stops being unique the
    moment a call is duplicated, and it cannot be backfilled later."""
    run = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=templates[1], taxonomy=taxonomy
    )
    scenario = Scenario.objects.create(
        system_description=seed, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    call = LLMCall.objects.create(phase_run=run, sequence=1, scenario=scenario)
    label = Label.objects.create(
        phase_run=run, scenario=scenario, hcv=HCV.objects.get(hcv_id="HCV-07"), llm_call=call
    )

    assert label.llm_call == call
    assert list(call.labels.all()) == [label]


@pytest.mark.django_db
def test_a_call_is_unique_per_run_scenario_and_sequence(experiment, taxonomy, templates, seed):
    """M8: the enabler for the double-counting M1 guards against."""
    run = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=templates[1], taxonomy=taxonomy
    )
    scenario = Scenario.objects.create(
        system_description=seed, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    LLMCall.objects.create(phase_run=run, sequence=1, scenario=scenario)
    with pytest.raises(IntegrityError):
        LLMCall.objects.create(phase_run=run, sequence=1, scenario=scenario)


@pytest.mark.django_db
def test_phase_two_calls_with_no_scenario_are_unaffected_by_that_constraint(
    experiment, taxonomy, templates
):
    """`scenario` is nullable and NULLs compare as distinct, so a phase-2 run's
    calls (scenario NULL) are not restricted by the constraint above."""
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    LLMCall.objects.create(phase_run=run, sequence=1)
    LLMCall.objects.create(phase_run=run, sequence=1)
    assert run.calls.count() == 2


@pytest.mark.django_db
def test_queued_is_a_valid_phase_run_status(experiment, taxonomy, templates):
    """PENDING now means "not queued yet"; QUEUED means "waiting for a worker"."""
    run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m",
        prompt_template=templates[0],
        taxonomy=taxonomy,
        status=PhaseRun.Status.QUEUED,
    )
    run.refresh_from_db()
    assert run.status == PhaseRun.Status.QUEUED
    assert run.is_terminal is False


@pytest.mark.django_db
def test_queued_is_a_valid_pipeline_run_status(experiment):
    run = PipelineRun.objects.create(
        experiment=experiment, repetition_index=1, status=PipelineRun.Status.QUEUED
    )
    run.refresh_from_db()
    assert run.status == PipelineRun.Status.QUEUED


@pytest.mark.django_db
def test_phase_run_progress_counters_default_to_zero(experiment, taxonomy, templates):
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    assert run.total_children == 0
    assert run.completed_children == 0
    assert run.failed_children == 0


@pytest.mark.django_db
def test_phase_run_progress_is_zero_percent_with_no_total(experiment, taxonomy, templates):
    """total=0 must report percent=0, never raise a ZeroDivisionError."""
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    assert run.progress == {"done": 0, "total": 0, "failed": 0, "percent": 0}


@pytest.mark.django_db
def test_phase_run_progress_combines_completed_and_failed(experiment, taxonomy, templates):
    run = PhaseRun.objects.create(
        phase=3,
        model="m",
        prompt_template=templates[1],
        taxonomy=taxonomy,
        total_children=12,
        completed_children=6,
        failed_children=1,
    )
    assert run.progress == {"done": 7, "total": 12, "failed": 1, "percent": 58}


@pytest.mark.django_db
def test_pipeline_run_progress_sums_its_phase_runs(experiment, taxonomy, templates):
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    PhaseRun.objects.create(
        pipeline_run=run,
        phase=2,
        variant="A",
        model="m",
        prompt_template=templates[0],
        taxonomy=taxonomy,
        total_children=1,
        completed_children=1,
    )
    PhaseRun.objects.create(
        pipeline_run=run,
        phase=3,
        variant="A",
        model="m",
        prompt_template=templates[1],
        taxonomy=taxonomy,
        total_children=5,
        completed_children=3,
        failed_children=1,
    )
    assert run.progress == {"done": 5, "total": 6, "failed": 1, "percent": 83}


@pytest.mark.django_db
def test_pipeline_run_progress_with_no_phase_runs_is_zero(experiment):
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    assert run.progress == {"done": 0, "total": 0, "failed": 0, "percent": 0}


@pytest.mark.django_db
def test_experiment_progress_sums_its_pipeline_runs(experiment, taxonomy, templates):
    for repetition in (1, 2):
        run = PipelineRun.objects.create(experiment=experiment, repetition_index=repetition)
        PhaseRun.objects.create(
            pipeline_run=run,
            phase=2,
            variant="A",
            model="m",
            prompt_template=templates[0],
            taxonomy=taxonomy,
            total_children=1,
            completed_children=1,
        )
    assert experiment.progress == {"done": 2, "total": 2, "failed": 0, "percent": 100}


@pytest.mark.django_db
def test_experiment_progress_with_no_runs_is_zero(experiment):
    assert experiment.progress == {"done": 0, "total": 0, "failed": 0, "percent": 0}


@pytest.mark.django_db
def test_experiment_progress_is_a_single_query(
    django_assert_num_queries, experiment, taxonomy, templates
):
    """M5: summing via `run.progress for run in self.runs.all()` cost 1 query
    for the repetitions plus one more PER repetition (each `PipelineRun.progress`
    re-querying its own phase runs) — N+1, 11 queries for 10 repetitions.
    `Experiment.progress` must instead aggregate across every phase run of
    every repetition of this experiment in one query."""
    for repetition in range(1, 4):
        run = PipelineRun.objects.create(experiment=experiment, repetition_index=repetition)
        PhaseRun.objects.create(
            pipeline_run=run,
            phase=2,
            variant="A",
            model="m",
            prompt_template=templates[0],
            taxonomy=taxonomy,
            total_children=1,
            completed_children=1,
        )
        PhaseRun.objects.create(
            pipeline_run=run,
            phase=3,
            variant="A",
            model="m",
            prompt_template=templates[1],
            taxonomy=taxonomy,
            total_children=2,
            completed_children=1,
            failed_children=1,
        )

    with django_assert_num_queries(1):
        progress = experiment.progress

    assert progress == {"done": 9, "total": 9, "failed": 3, "percent": 100}
