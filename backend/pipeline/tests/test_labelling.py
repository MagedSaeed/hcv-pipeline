import json
from unittest.mock import patch

import pytest
from django.db import DatabaseError

from corpus.models import Origin, Scenario, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.labelling import finish_labelling_run, label_scenario, scenarios_for
from pipeline.models import Experiment, Label, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    for code, name, order in [
        ("HCV-07", "Malicious Insiders", 7),
        ("HCV-17", "Weak Passwords Practices", 17),
    ]:
        HCV.objects.create(taxonomy=tax, hcv_id=code, name=name, order=order)
    return tax


@pytest.fixture
def template(db):
    return PromptTemplate.objects.create_version(
        phase=3,
        name="lab",
        system_prompt="You are a careful vulnerability labeller.",
        user_prompt="Label {{ scenario.scenario_id }} from {{ taxonomy|length }} options.",
    )


@pytest.fixture
def scenario(db):
    system = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    return Scenario.objects.create(
        system_description=system,
        scenario_id="sc_01",
        actor="EP1",
        threat="Spoofing.",
        narrative="1. EP1 acts.",
        origin=Origin.SEED,
        order=1,
    )


@pytest.fixture
def run(db, taxonomy, template):
    return PhaseRun.objects.create(phase=3, model="m3", prompt_template=template, taxonomy=taxonomy)


def payload(*ids):
    return json.dumps({"labels": [{"hcv_id": i, "evidence": "e", "rationale": "r"} for i in ids]})


@pytest.mark.django_db
def test_labels_are_persisted(_client, run, scenario, taxonomy):
    _client.queue(payload("HCV-07"))
    label_scenario(run, scenario)

    label = Label.objects.get()
    assert label.hcv.hcv_id == "HCV-07"
    assert label.scenario == scenario
    assert label.phase_run == run
    assert label.source == Label.Source.LLM
    assert label.evidence == "e"


@pytest.mark.django_db
def test_an_empty_label_list_is_a_valid_result(_client, run, scenario):
    """A scenario carrying no vulnerability is a real finding, not a failure."""
    _client.queue(payload())
    call = label_scenario(run, scenario)

    assert call.parsed_ok is True
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_an_out_of_vocabulary_id_is_recorded_not_dropped(_client, run, scenario):
    _client.queue(payload("HCV-07", "HCV-99"))
    call = label_scenario(run, scenario)

    assert call.parsed_ok is True
    assert call.unknown_hcv_ids == ["HCV-99"]
    assert [label.hcv.hcv_id for label in Label.objects.all()] == ["HCV-07"]


@pytest.mark.django_db
def test_the_call_records_the_scenario_it_labelled(_client, run, scenario):
    _client.queue(payload("HCV-07"))
    call = label_scenario(run, scenario)
    assert call.scenario == scenario
    assert call.sequence == 1


@pytest.mark.django_db
def test_phase_three_always_sees_the_taxonomy(_client, run, scenario):
    _client.queue(payload("HCV-07"))
    call = label_scenario(run, scenario)
    assert "from 2 options" in call.rendered_user_prompt
    assert call.rendered_system_prompt == "You are a careful vulnerability labeller."
    assert call.leak_scan_ran is False


@pytest.mark.django_db
def test_a_duplicate_label_from_one_run_is_ignored(_client, run, scenario):
    _client.queue(payload("HCV-07", "HCV-07"))
    label_scenario(run, scenario)
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_a_transport_failure_records_the_call_without_labels(_client, run, scenario):
    _client.fail_next("timeout", http_status=None)
    call = label_scenario(run, scenario)

    assert call.ok is False
    assert call.parsed_ok is False
    assert Label.objects.count() == 0


def _other_scenario(*, generation_run):
    """A second scenario belonging to a DIFFERENT phase run, so a test that
    checks "returns only the source run's scenarios" cannot pass by
    accident against a corpus that only ever has one scenario in it — see
    I3: with a single scenario, "the source run's scenarios" and "everything"
    are indistinguishable."""
    system = SystemDescription.objects.create(title="Riverbend", body="y", origin=Origin.GENERATED)
    return Scenario.objects.create(
        system_description=system,
        scenario_id="sc_02",
        actor="EP2",
        threat="Tampering.",
        narrative="1. EP2 acts.",
        origin=Origin.GENERATED,
        generation_run=generation_run,
        order=1,
    )


@pytest.mark.django_db
def test_scenarios_for_reads_the_source_run(_client, run, scenario, taxonomy, template):
    source = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=template, taxonomy=taxonomy
    )
    other_source = PhaseRun.objects.create(
        phase=2, variant="B", model="m2", prompt_template=template, taxonomy=taxonomy
    )
    scenario.generation_run = source
    scenario.origin = Origin.GENERATED
    scenario.save()
    other = _other_scenario(generation_run=other_source)
    run.source_phase_run = source
    run.save()

    result = list(scenarios_for(run))
    assert result == [scenario]
    assert other not in result


@pytest.mark.django_db
def test_scenarios_for_with_no_source_labels_everything(_client, run, scenario, taxonomy, template):
    other_source = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=template, taxonomy=taxonomy
    )
    other = _other_scenario(generation_run=other_source)

    assert set(scenarios_for(run)) == {scenario, other}


@pytest.mark.django_db
def test_scenarios_for_returns_nothing_when_an_orchestrated_runs_source_was_deleted(
    _client, run, scenario, taxonomy, template
):
    """M8: source_phase_run is SET_NULL on delete. An ORCHESTRATED run
    (one with a pipeline_run) always had a source at launch, so a null
    source on one means the source was deleted afterward — this must not
    fall through to "everything" (the whole corpus, across every variant),
    which is the ad-hoc-only behaviour from the test above and must stay
    ad-hoc-only."""
    phase2_template = PromptTemplate.objects.create_version(phase=2, name="gen", user_prompt="g")
    experiment = Experiment.objects.create(
        name="e",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt=phase2_template,
        phase3_model="m3",
        phase3_prompt=template,
    )
    pipeline_run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    source = PhaseRun.objects.create(
        pipeline_run=pipeline_run,
        phase=2,
        variant="A",
        model="m2",
        prompt_template=phase2_template,
        taxonomy=taxonomy,
    )
    run.pipeline_run = pipeline_run
    run.source_phase_run = source
    run.save()
    scenario.generation_run = source
    scenario.origin = Origin.GENERATED
    scenario.save()

    assert list(scenarios_for(run)) == [scenario]  # sanity: source still there

    source.delete()  # SET_NULL cascades onto run.source_phase_run
    run.refresh_from_db()
    assert run.source_phase_run_id is None
    assert run.pipeline_run_id is not None

    assert list(scenarios_for(run)) == []


@pytest.mark.django_db
def test_finishing_a_run_whose_source_was_deleted_fails_rather_than_completes(
    _client, run, scenario, taxonomy, template
):
    """M8, the finish_labelling_run half: zero calls from an empty
    scenarios_for() fan-out must not read as a silent COMPLETED with
    failed_children=0 — that would hide that the source was deleted and
    nothing was actually labelled."""
    phase2_template = PromptTemplate.objects.create_version(phase=2, name="gen2", user_prompt="g")
    experiment = Experiment.objects.create(
        name="e2",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt=phase2_template,
        phase3_model="m3",
        phase3_prompt=template,
    )
    pipeline_run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    run.pipeline_run = pipeline_run
    run.save()
    assert run.source_phase_run_id is None

    finished = finish_labelling_run(run)

    assert finished.status == PhaseRun.Status.FAILED
    assert finished.failed_children == 0
    assert "deleted" in finished.error.lower()


@pytest.mark.django_db
def test_finishing_completes_a_run_with_no_failures(_client, run, scenario):
    _client.queue(payload("HCV-07"))
    label_scenario(run, scenario)
    finished = finish_labelling_run(run)

    assert finished.status == PhaseRun.Status.COMPLETED
    assert finished.failed_children == 0
    assert finished.finished_at is not None


@pytest.mark.django_db
def test_finishing_fails_a_run_that_had_a_failed_child(_client, run, scenario):
    _client.fail_next("boom")
    label_scenario(run, scenario)
    finished = finish_labelling_run(run)

    assert finished.status == PhaseRun.Status.FAILED
    assert finished.failed_children == 1
    assert "1" in finished.error


@pytest.mark.django_db
def test_finishing_never_overwrites_a_cancelled_run(_client, run, scenario):
    """Archive-fidelity fix, task 5 follow-up: this is the function that
    writes the FINAL status of a fan-out `run_labelling` just finished, and
    an operator's `POST .../cancel/` can land in the database at any point
    while that fan-out is still running (the cancel action's own caveat: it
    does not interrupt a worker already executing, so the worker's
    remaining writes — individual `LLMCall` rows, the counters below — may
    still land). What must never happen is the run's FINAL status silently
    flipping away from CANCELLED to COMPLETED/FAILED here — that would
    misreport a deliberately-cancelled run as one that ran to completion or
    failed on its own. A failed call is archived first, so this proves the
    guard checks the run's OWN current status, not merely "were there zero
    calls" — the same shape `failed_calls` would otherwise report FAILED for.
    """
    _client.fail_next("boom")
    label_scenario(run, scenario)
    run.status = PhaseRun.Status.CANCELLED
    run.error = "Cancelled by operator."
    run.completed_children = 0
    run.failed_children = 0
    run.save(update_fields=["status", "error", "completed_children", "failed_children"])

    finished = finish_labelling_run(run)

    assert finished.status == PhaseRun.Status.CANCELLED
    assert finished.error == "Cancelled by operator."
    assert finished.completed_children == 0
    assert finished.failed_children == 0


@pytest.mark.django_db(transaction=True)
def test_a_persist_failure_is_recorded_without_raising_or_lying_about_parsing(
    _client, run, scenario
):
    """A DatabaseError from _persist (I1/I2: e.g. a taxonomy row deleted out
    from under a labelling run) must not escape label_scenario, and must not
    be recorded by falsely flipping parsed_ok — that field is the
    schema-validation signal the admin's list_filter and the rest of the
    project read it as. It shows up in parse_error instead, and the run still
    ends up FAILED because failed_calls() also looks at parse_error."""
    _client.queue(payload("HCV-07"))
    with patch("pipeline.labelling._persist", side_effect=DatabaseError("locked")):
        call = label_scenario(run, scenario)

    assert call.parsed_ok is True
    assert "locked" in call.parse_error
    assert Label.objects.count() == 0

    finished = finish_labelling_run(run)
    assert finished.status == PhaseRun.Status.FAILED
    assert finished.failed_children == 1


@pytest.mark.django_db
def test_a_persisted_label_records_the_call_that_produced_it(_client, run, scenario):
    """I6: spec §9 requires the export to name the call behind each label. The
    (phase_run, scenario) join that derives it today stops being unique the
    moment a call is duplicated, and it cannot be backfilled afterwards — so the
    link is written at the moment the label is created."""
    _client.queue(payload("HCV-07"))
    call = label_scenario(run, scenario)

    label = Label.objects.get()
    assert label.llm_call == call
    assert list(call.labels.all()) == [label]
