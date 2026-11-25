import pytest
from django.db import IntegrityError, transaction

from analyses import state
from analyses.models import Analysis, AnalysisCall, AnalysisLabel, AnalysisRun, AnalysisScenario
from taxonomy.serializers import taxonomy_lock_reason

pytestmark = pytest.mark.django_db


def add_run(analysis, kind="generate", status="completed"):
    return AnalysisRun.objects.create(analysis=analysis, kind=kind, status=status, pinned={})


def add_scenario(analysis, scenario_id="sc_01", **fields):
    fields = {"trigger": "A morning shift.", "narrative": "PO1 opens the console.", **fields}
    return AnalysisScenario.objects.create(analysis=analysis, scenario_id=scenario_id, **fields)


def make_current(scenario):
    scenario.label_key = state.label_key(scenario.analysis, scenario)
    scenario.save()


def test_defaults_are_the_recommended_setup():
    assert state.default_settings("generation") == {
        "model": "anthropic/claude-sonnet-5",
        "temperature": 0.8,
        "prompt": "preset",
        "examples": [],
        "count": 8,
    }
    assert state.default_settings("labelling") == {
        "model": "openai/gpt-5.6-luna-pro",
        "temperature": 0.0,
        "prompt": "preset",
        "examples": ["preset:0", "preset:1", "preset:2", "preset:3", "preset:4"],
    }


@pytest.mark.parametrize("status", ["queued", "running"])
def test_second_active_run_is_refused_by_the_database(analysis, status):
    add_run(analysis, status=status)

    with pytest.raises(IntegrityError), transaction.atomic():
        add_run(analysis, kind="label", status="queued")


def test_finished_runs_do_not_block_a_new_one(analysis):
    for status in ("completed", "failed", "cancelled", "queued"):
        add_run(analysis, status=status)

    assert analysis.runs.count() == 4


def test_a_new_analysis_is_unlocked(analysis):
    assert state.is_locked(analysis) is False


@pytest.mark.parametrize("status", ["queued", "running"])
def test_generating_locks(analysis, status):
    add_run(analysis, status=status)

    assert state.is_locked(analysis) is True


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_generation_without_scenarios_unlocks(analysis, status):
    add_run(analysis, status=status)

    assert state.is_locked(analysis) is False


def test_generated_scenarios_lock(analysis):
    add_scenario(analysis)

    assert state.is_locked(analysis) is True


def test_manual_scenarios_alone_do_not_lock(analysis):
    add_scenario(analysis, "sc_101", is_manual=True)

    assert state.is_locked(analysis) is False


@pytest.mark.parametrize(
    "change",
    [
        {"model": "other/model"},
        {"temperature": 0.4},
        {"prompt": 12},
        {"examples": []},
    ],
)
def test_label_key_changes_with_each_setting_and_returns_when_reverted(analysis, change):
    scenario = add_scenario(analysis)
    before = state.label_key(analysis, scenario)
    original = dict(analysis.labelling_settings)

    analysis.labelling_settings = {**original, **change}
    assert state.label_key(analysis, scenario) != before

    analysis.labelling_settings = original
    assert state.label_key(analysis, scenario) == before


@pytest.mark.parametrize("field", ["narrative", "trigger", "actor", "asset", "threat", "outcome"])
def test_label_key_changes_with_content(analysis, field):
    scenario = add_scenario(analysis)
    before = state.label_key(analysis, scenario)

    setattr(scenario, field, "changed")

    assert state.label_key(analysis, scenario) != before


def test_set_aside_and_display_name_do_not_change_the_label_key(analysis):
    scenario = add_scenario(analysis)
    before = state.label_key(analysis, scenario)

    scenario.set_aside = True
    analysis.name = "Renamed"

    assert state.label_key(analysis, scenario) == before


def test_label_status(analysis):
    scenario = add_scenario(analysis)
    assert state.label_status(analysis, scenario) == "unlabelled"

    run = add_run(analysis, "label", "failed")
    AnalysisCall.objects.create(run=run, scenario=scenario, error="No credit.")
    assert state.label_status(analysis, scenario) == "failed"

    make_current(scenario)
    assert state.label_status(analysis, scenario) == "current"


def test_label_status_is_stale_after_an_edit(analysis):
    scenario = add_scenario(analysis, "sc_101", is_manual=True)
    make_current(scenario)

    scenario.narrative = "Something else happens."

    assert state.label_status(analysis, scenario) == "stale"


def test_resume_step_at_each_stage(fresh_analysis):
    analysis = fresh_analysis

    def now():
        fresh = Analysis.objects.get(pk=analysis.pk)
        return state.resume_step(fresh), state.step_states(fresh)

    step, steps = now()
    assert step == "system"
    assert not any(steps.values())

    generating = add_run(analysis, status="running")
    step, steps = now()
    assert (step, steps["system"], steps["generate"]) == ("generate", True, False)

    generating.status = "completed"
    generating.save()
    first, second = add_scenario(analysis), add_scenario(analysis, "sc_02")
    step, steps = now()
    assert (step, steps["generate"], steps["label"]) == ("generate", True, False)

    make_current(first)
    assert now()[0] == "label"

    make_current(second)
    step, steps = now()
    assert step == "report"
    assert all(steps.values())


def test_setting_every_scenario_aside_leaves_no_report(analysis):
    scenario = add_scenario(analysis, set_aside=True)
    make_current(scenario)

    assert state.step_states(analysis)["report"] is False


def test_delete_removes_every_child(analysis, v12):
    run = add_run(analysis, "label")
    scenario = add_scenario(analysis)
    call = AnalysisCall.objects.create(run=run, scenario=scenario)
    AnalysisLabel.objects.create(scenario=scenario, hcv=v12.hcvs.first(), call=call)
    scenario.labelled_call = call
    scenario.save()

    analysis.delete()

    for model in (AnalysisRun, AnalysisScenario, AnalysisCall, AnalysisLabel):
        assert model.objects.count() == 0


def test_taxonomy_is_frozen_by_a_run(analysis, v12):
    assert taxonomy_lock_reason(v12) == ""

    add_run(analysis)

    assert "frozen" in taxonomy_lock_reason(v12)
