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
        phase2_prompt_a=gen,
        phase2_prompt_b=gen,
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
        phase2_prompt_a=templates[0],
        phase2_prompt_b=templates[0],
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
def test_a_taxonomy_of_content_free_rows_is_refused_too(experiment):
    """Round 4, leak 1 — the DEEPER of that leak's two fixes.

    `taxonomy_missing_rows_error` used to ask `taxonomy.hcvs.exists()`,
    which is not the property variant A's backstop actually needs.
    `assert_no_taxonomy_leak` skips every falsy `hcv_id`/`name`/
    `definition` it walks past, so a row blank in all three satisfies
    `.exists()` while contributing NOTHING to the scan: the taxonomy
    scanned for nothing, found nothing, and returned True — strictly worse
    than a zero-row taxonomy, which at least records
    `leak_scan_ran=False`. Fixing the precondition here (scannable ROWS,
    not row objects) is what stops the leak recurring through any route
    that produces such a row, not only through the admin add form that
    happened to produce them this time.
    """
    blank = Taxonomy.objects.create(vocabulary_id="HCV-21", version="blank")
    HCV.objects.create(taxonomy=blank, hcv_id="", name="", definition="", order=0)
    experiment.taxonomy = blank

    with pytest.raises(ValidationError, match="content-free"):
        experiment.full_clean()


@pytest.mark.django_db
def test_one_content_free_row_among_good_ones_is_refused(experiment):
    """EVERY row must be scannable, not merely one of them: 20 blanked rows
    out of 21 would otherwise pass while the guard silently covered 1/21 of
    the taxonomy and still certified itself. `import_taxonomy` refuses a
    row missing `hcv_id` or `name`, and the admin no longer offers an add
    form, so nothing legitimate produces one."""
    partly = Taxonomy.objects.create(vocabulary_id="HCV-21", version="partly")
    HCV.objects.create(taxonomy=partly, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    HCV.objects.create(taxonomy=partly, hcv_id="   ", name="", definition="  ", order=99)
    experiment.taxonomy = partly

    with pytest.raises(ValidationError, match="1 of 2"):
        experiment.full_clean()


@pytest.mark.django_db
def test_a_fully_populated_taxonomy_still_validates(experiment, taxonomy):
    """The contrast case, so the two tests above cannot be passing against
    a rule that simply refuses every taxonomy."""
    experiment.taxonomy = taxonomy

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
    experiment.phase2_prompt_a = labeller
    with pytest.raises(ValidationError, match="phase 2"):
        experiment.full_clean()


@pytest.mark.django_db
def test_a_phase_two_template_may_not_be_the_phase_three_prompt(experiment, templates):
    generator, _ = templates
    experiment.phase3_prompt = generator
    with pytest.raises(ValidationError, match="phase 3"):
        experiment.full_clean()


@pytest.mark.django_db
def test_phase2_prompt_b_may_not_be_a_phase_three_template(experiment, templates):
    """The phase-match rule applies to BOTH per-variant fields, not just
    `phase2_prompt_a` — reuses `template_phase_mismatch_error` exactly the
    same way for each."""
    _, labeller = templates
    experiment.phase2_prompt_b = labeller
    with pytest.raises(ValidationError, match="phase 2"):
        experiment.full_clean()


@pytest.mark.django_db
def test_a_single_variant_experiment_needs_only_its_own_template(templates):
    """`variants` may legitimately be `["A"]` or `["B"]`, not just both —
    requiring BOTH per-variant templates unconditionally would wrongly
    refuse a real single-variant experiment that has no use for the other
    one."""
    gen, lab = templates
    b_only_taxonomy = Taxonomy.objects.create(vocabulary_id="HCV-B-ONLY", version="1.0")
    b_only_taxonomy.hcvs.create(hcv_id="HCV-01", name="x", order=1)
    experiment = Experiment(
        name="b-only",
        taxonomy=b_only_taxonomy,
        phase2_model="m",
        phase2_prompt_b=gen,
        phase3_model="m",
        phase3_prompt=lab,
        variants=["B"],
    )
    experiment.full_clean()  # must not raise: no B-only run needs phase2_prompt_a


@pytest.mark.django_db
def test_a_variant_actually_run_must_have_its_own_phase2_template(taxonomy, templates):
    """The new per-variant rule this task adds: `variants=["A"]` demands
    `phase2_prompt_a` specifically, not merely SOME phase-2 template sitting
    on the experiment (`phase2_prompt_b`, left from a template swap, does
    not count for variant A)."""
    gen, lab = templates
    experiment = Experiment(
        name="missing-a",
        taxonomy=taxonomy,
        phase2_model="m",
        phase2_prompt_b=gen,
        phase3_model="m",
        phase3_prompt=lab,
        variants=["A"],
    )
    with pytest.raises(ValidationError, match="phase2_prompt_a"):
        experiment.full_clean()


@pytest.mark.django_db
def test_an_unsaved_experiment_with_no_variants_set_needs_both_templates(templates):
    """`clean()`/`full_clean()` runs on an UNSAVED instance too — that is
    exactly what `ExperimentSerializer.validate()` does at CREATE time,
    before `Experiment.save()`'s own "an empty `variants` list defaults to
    both" logic has ever had a chance to run. A freshly-constructed
    `Experiment()` carries `variants == []` (the bare Django field default)
    until `save()` runs, so a create request that never mentions `variants`
    at all must still be checked against what it will ACTUALLY run once
    saved — both A and B — not against a momentarily empty list that would
    let the per-variant template check pass vacuously with NEITHER
    `phase2_prompt_a` nor `phase2_prompt_b` set."""
    _, lab = templates
    tax = Taxonomy.objects.create(vocabulary_id="HCV-UNSAVED", version="1.0")
    tax.hcvs.create(hcv_id="HCV-01", name="x", order=1)
    experiment = Experiment(
        name="unsaved-no-variants",
        taxonomy=tax,
        phase2_model="m",
        phase3_model="m",
        phase3_prompt=lab,
        # variants deliberately left unset — Experiment()'s bare default.
    )
    assert experiment.variants == []

    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()

    assert "phase2_prompt_a" in caught.value.message_dict
    assert "phase2_prompt_b" in caught.value.message_dict


@pytest.mark.django_db
def test_a_variant_missing_its_template_is_refused_even_with_the_other_set(taxonomy, templates):
    """Symmetric to the A-side check above: `variants=["B"]` with only
    `phase2_prompt_a` set (and `phase2_prompt_b` left null) is refused too —
    the rule is per-variant, not "at least one template is set"."""
    gen, lab = templates
    experiment = Experiment(
        name="missing-b",
        taxonomy=taxonomy,
        phase2_model="m",
        phase2_prompt_a=gen,
        phase3_model="m",
        phase3_prompt=lab,
        variants=["B"],
    )
    with pytest.raises(ValidationError, match="phase2_prompt_b"):
        experiment.full_clean()


@pytest.mark.django_db
def test_an_experiment_runs_both_phases_by_default(experiment):
    assert experiment.phases == [2, 3]
    assert Experiment().phases == [2, 3]


@pytest.mark.django_db
def test_phase_three_alone_is_refused_because_it_needs_phase_two(experiment):
    experiment.phases = [3]
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert caught.value.message_dict["phases"] == [
        "Phase 3 labels the scenarios phase 2 generates, so it needs phase 2."
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("phases", [[], [2, 2], [2, 4], [1, 2, 3], "2,3", [True], None])
def test_a_malformed_phases_list_is_refused(experiment, phases):
    experiment.phases = phases
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert "phases" in caught.value.message_dict


@pytest.mark.django_db
def test_a_generation_only_experiment_needs_no_phase_three_fields(taxonomy, templates):
    gen, _ = templates
    experiment = Experiment(
        name="generation-only",
        taxonomy=taxonomy,
        phase2_model="m",
        phase2_prompt_a=gen,
        phase2_prompt_b=gen,
        variants=["A", "B"],
        phases=[2],
    )
    experiment.full_clean()  # must not raise: no phase-3 model or prompt needed
    experiment.save()
    assert Experiment.objects.get(pk=experiment.pk).phases == [2]


@pytest.mark.django_db
def test_an_experiment_running_phase_three_needs_its_model_and_prompt(taxonomy, templates):
    gen, _ = templates
    experiment = Experiment(
        name="labelling-without-fields",
        taxonomy=taxonomy,
        phase2_model="m",
        phase2_prompt_a=gen,
        phase2_prompt_b=gen,
        phases=[2, 3],
    )
    with pytest.raises(ValidationError) as caught:
        experiment.full_clean()
    assert "phase3_model" in caught.value.message_dict
    assert "phase3_prompt" in caught.value.message_dict


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


@pytest.mark.django_db
def test_the_shared_checker_reads_an_object_config_as_well_as_a_mapping():
    """`generation_config_errors` is duck-typed on purpose, and this is the
    branch none of the three production call sites exercises today.

    All three currently hand it a mapping — `Experiment.generation_config`,
    `_ADHOC_GENERATION_CONFIG`, and the live dict `_adhoc_generation_config`
    builds — so a checker that quietly only worked for mappings would look
    fine until the first caller with an attribute-shaped config, and then
    fail OPEN: `getattr(cfg, "seed_systems", None)` returning `None` reads
    as "no seeds", which is a clean config. That is precisely the
    permissive-reader failure mode `pipeline.execution_config`'s own module
    docstring records as its worst defect, so the other branch is pinned
    here rather than left as an untested claim in a docstring.
    """
    from types import SimpleNamespace

    from pipeline.models import generation_config_errors

    generated = SystemDescription.objects.create(
        title="Made by a run", body="x", origin=Origin.GENERATED
    )
    as_object = SimpleNamespace(
        seed_systems=[generated],
        fewshots=[],
        target_system=None,
        target_system_id=None,
        generate_systems=True,
        generate_scenarios=True,
        n_systems=3,
        n_scenarios_per_system=5,
    )
    as_mapping = dict(vars(as_object))

    assert "seed_systems" in generation_config_errors(as_object)
    assert generation_config_errors(as_object) == generation_config_errors(as_mapping)


@pytest.mark.django_db
def test_the_shared_checker_reads_a_related_manager_as_rows():
    """The other half of the same claim: `seed_systems` may arrive as a
    related MANAGER rather than a list (that is what an `Experiment` field
    is), and iterating a manager raises rather than yielding its rows — so
    "it has an `.all()`" has to be handled, not assumed away."""
    from pipeline.models import generation_config_errors

    experiment_like = Experiment.objects.create(
        name="manager-shaped",
        taxonomy=Taxonomy.objects.create(vocabulary_id="HCV-DUCK", version="1.0"),
        phase2_model="m2",
        phase2_prompt_a=PromptTemplate.objects.create_version(phase=2, name="g2", user_prompt="g"),
        phase2_prompt_b=PromptTemplate.objects.create_version(phase=2, name="g2b", user_prompt="g"),
        phase3_model="m3",
        phase3_prompt=PromptTemplate.objects.create_version(phase=3, name="l2", user_prompt="l"),
    )
    experiment_like.seed_systems.add(
        SystemDescription.objects.create(title="Made by a run", body="x", origin=Origin.GENERATED)
    )

    errors = generation_config_errors(
        {**experiment_like.generation_config, "seed_systems": experiment_like.seed_systems}
    )

    assert "seed_systems" in errors
