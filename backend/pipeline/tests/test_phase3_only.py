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
    assert "Choose the phases to run: [2], [2, 3] or [3]." in caught.value.message_dict["phases"]


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


@pytest.fixture
def staff(db):
    from django.contrib.auth import get_user_model

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


@pytest.mark.django_db
def test_snapshot_pins_the_chosen_ids(taxonomy, label_prompt, chosen):
    from pipeline.execution_config import config_snapshot, validate_snapshot

    experiment = make_phase3_only(taxonomy, label_prompt, list(reversed(chosen)))
    config = config_snapshot(experiment)
    assert config["phases"] == [3]
    assert config["label_scenario_ids"] == sorted(s.pk for s in chosen)
    validate_snapshot(config)


@pytest.mark.django_db
def test_snapshot_of_a_generating_experiment_has_no_chosen_ids(taxonomy, label_prompt, chosen):
    from pipeline.execution_config import config_snapshot

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
    from pipeline.execution_config import MalformedSnapshot, config_snapshot, validate_snapshot

    config = config_snapshot(make_phase3_only(taxonomy, label_prompt, chosen))
    config["phases"] = phases
    config["label_scenario_ids"] = ids
    with pytest.raises(MalformedSnapshot, match=message):
        validate_snapshot(config)


@pytest.fixture
def fake_llm():
    from llm.fake import FakeLLMClient
    from pipeline.clients import reset_llm_client, set_llm_client

    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.mark.django_db
def test_launch_creates_one_variantless_labelling_run(taxonomy, label_prompt, chosen):
    from pipeline.orchestration import launch

    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    phase_runs = list(run.phase_runs.all())
    assert len(phase_runs) == 1
    (labelling,) = phase_runs
    assert (labelling.phase, labelling.variant, labelling.source_phase_run_id) == (3, "", None)
    assert labelling.scenario_ids == sorted(s.pk for s in chosen)


@pytest.mark.django_db
def test_execute_labels_exactly_the_chosen_scenarios(taxonomy, label_prompt, chosen, fake_llm):
    from pipeline.models import Label
    from pipeline.orchestration import execute_pipeline_run, launch

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
    from pipeline.models import Label
    from pipeline.orchestration import execute_pipeline_run, launch, retry_failed

    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    fake_llm.queue("not json")
    fake_llm.queue(LABELLED)
    execute_pipeline_run(run)
    labelling = run.phase_runs.get()
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
    from pipeline.orchestration import launch

    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    Scenario.objects.filter(pk__in=[s.pk for s in chosen]).delete()
    with pytest.raises(ValidationError) as caught:
        launch(experiment, n_repetitions=1)
    assert "label_scenarios" in caught.value.message_dict
    assert not PipelineRun.objects.filter(experiment=experiment).exists()


@pytest.mark.django_db
def test_scenarios_deleted_after_launch_are_skipped(taxonomy, label_prompt, chosen, fake_llm):
    from pipeline.orchestration import execute_pipeline_run, launch

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
    from pipeline.orchestration import execute_pipeline_run, launch

    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    run = launch(experiment, n_repetitions=1)[0]
    Scenario.objects.filter(pk__in=[s.pk for s in chosen]).delete()
    execute_pipeline_run(run)
    labelling = run.phase_runs.get()
    assert labelling.status == PhaseRun.Status.FAILED
    assert labelling.error == "The chosen scenarios were deleted; nothing to label."


@pytest.mark.django_db
def test_export_of_a_phase3_only_run_includes_the_chosen_scenarios(
    taxonomy, label_prompt, chosen, fake_llm
):
    from pipeline.export import build_bundle, build_run_bundle
    from pipeline.orchestration import execute_pipeline_run, launch

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


def _completed_phase3_run(taxonomy, label_prompt, scenarios, fake_llm, name="p3"):
    from pipeline.orchestration import execute_pipeline_run, launch

    run = launch(make_phase3_only(taxonomy, label_prompt, scenarios, name=name), n_repetitions=1)[0]
    for _ in scenarios:
        fake_llm.queue(LABELLED)
    execute_pipeline_run(run)
    return run


@pytest.mark.django_db
def test_retry_api_relabels_a_failed_phase3_only_run(
    api_client, staff, taxonomy, label_prompt, chosen, fake_llm
):
    from pipeline.orchestration import execute_pipeline_run, launch

    run = launch(make_phase3_only(taxonomy, label_prompt, chosen), n_repetitions=1)[0]
    fake_llm.queue("not json")
    fake_llm.queue(LABELLED)
    execute_pipeline_run(run)
    labelling = run.phase_runs.get()
    assert labelling.status == PhaseRun.Status.FAILED

    fake_llm.queue(LABELLED)
    api_client.force_authenticate(user=staff)
    response = api_client.post(f"/api/phase-runs/{labelling.id}/retry/")
    assert response.status_code == 200, response.json()
    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.COMPLETED


@pytest.mark.django_db
def test_deleting_a_labelled_scenario_is_refused(api_client, staff, taxonomy, label_prompt, chosen, fake_llm):
    _completed_phase3_run(taxonomy, label_prompt, chosen, fake_llm)
    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/scenarios/{chosen[0].id}/")
    assert response.status_code == 403
    assert "p3" in response.json()["detail"]
    assert Scenario.objects.filter(pk=chosen[0].pk).exists()


@pytest.mark.django_db
def test_deleting_a_labelled_system_is_refused(api_client, staff, taxonomy, label_prompt, chosen, fake_llm):
    _completed_phase3_run(taxonomy, label_prompt, chosen, fake_llm)
    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/system-descriptions/{chosen[0].system_description_id}/")
    assert response.status_code == 403
    assert SystemDescription.objects.filter(pk=chosen[0].system_description_id).exists()


@pytest.mark.django_db
def test_deleting_the_run_that_generated_labelled_scenarios_is_refused(
    api_client, staff, taxonomy, label_prompt, fake_llm
):
    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    source = Experiment.objects.create(
        name="source", taxonomy=taxonomy, phases=[2], phase2_model="m2",
        phase2_prompt_a=generation, phase2_prompt_b=generation,
    )
    source_run = PipelineRun.objects.create(experiment=source, repetition_index=1)
    generation_run = PhaseRun.objects.create(
        pipeline_run=source_run, phase=2, variant="A", model="m2",
        prompt_template=generation, taxonomy=taxonomy,
    )
    system = SystemDescription.objects.create(title="Gen", origin=Origin.GENERATED, generation_run=generation_run)
    generated = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.GENERATED, generation_run=generation_run
    )
    _completed_phase3_run(taxonomy, label_prompt, [generated], fake_llm)
    source_run.status = PipelineRun.Status.COMPLETED
    source_run.save()

    api_client.force_authenticate(user=staff)
    for url in (
        f"/api/pipeline-runs/{source_run.id}/",
        f"/api/phase-runs/{generation_run.id}/",
        f"/api/experiments/{source.id}/",
    ):
        response = api_client.delete(url)
        assert response.status_code == 409, (url, response.status_code)
        assert "p3" in response.json()["detail"]
    assert Scenario.objects.filter(pk=generated.pk).exists()


@pytest.mark.django_db
def test_deleting_a_phase3_only_experiment_itself_is_allowed(
    api_client, staff, taxonomy, label_prompt, chosen, fake_llm
):
    run = _completed_phase3_run(taxonomy, label_prompt, chosen, fake_llm)
    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/experiments/{run.experiment_id}/")
    assert response.status_code == 204
    assert Scenario.objects.filter(pk=chosen[0].pk).exists()


@pytest.mark.django_db
def test_repetitions_help_text_fits_a_phase3_only_experiment(
    api_client, staff, taxonomy, label_prompt, chosen
):
    experiment = make_phase3_only(taxonomy, label_prompt, chosen)
    api_client.force_authenticate(user=staff)
    response = api_client.options(f"/api/experiments/{experiment.id}/run/")
    help_text = response.json()["actions"]["POST"]["n_repetitions"]["help_text"]
    assert "variants A and B" not in help_text
    assert "one repetition's result may be noise" in help_text
