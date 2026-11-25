import pytest

from analyses import preset, state
from analyses.models import AnalysisItem

pytestmark = pytest.mark.django_db


def test_extraction_defaults_are_the_recommended_extractor():
    assert state.default_settings("extraction") == {
        "model": "anthropic/claude-sonnet-5.5",
        "temperature": 0.0,
    }
    extractor = preset.load_extractor()
    assert extractor.name == "System description extractor"
    assert extractor.version == 1
    assert "{{SYSTEM_DESCRIPTION}}" in extractor.text


def test_extraction_status(analysis, extracted):
    assert state.extraction_status(analysis) == "current"

    analysis.description += " Now with valets."

    assert state.extraction_status(analysis) == "stale"


def test_no_extraction_yet(fresh_analysis):
    assert state.extraction_status(fresh_analysis) == "none"


def test_pending_items_are_counted(analysis, extracted):
    analysis.items.filter(ref="Residents", kind="element").update(decision="pending")

    assert state.pending_items(analysis) == 1


def test_system_body_holds_the_checked_values_and_the_text(analysis, extracted):
    body = state.system_body(analysis)

    assert "- Residents (Citizen)" in body
    assert "- ParkingApp: PasswordControl absent" in body
    assert body.rstrip().endswith(analysis.description)


def test_rejecting_then_approving_an_element_restores_its_facts(analysis, extracted):
    app = analysis.items.get(kind="element", ref="ParkingApp")

    app.decision = "rejected"
    app.save()
    assert "Residents uses" not in state.system_body(analysis)

    app.decision = "approved"
    app.save()
    assert "- Residents uses ParkingApp" in state.system_body(analysis)


def test_label_key_follows_the_system_body(analysis, extracted):
    scenario = analysis.scenarios.create(scenario_id="sc_01", narrative="x")
    before = state.label_key(analysis, scenario)

    analysis.items.filter(kind="control").update(decision="rejected")

    assert state.label_key(analysis, scenario) != before


def test_steps_and_resume_include_extract(analysis, extracted):
    steps = state.step_states(analysis)
    assert list(steps) == ["system", "extract", "generate", "label", "report"]
    assert (steps["system"], steps["extract"]) == (True, True)
    assert state.resume_step(analysis) == "generate"

    AnalysisItem.objects.filter(analysis=analysis).update(decision="pending")
    assert state.resume_step(analysis) == "extract"


def test_a_new_analysis_resumes_at_system(fresh_analysis):
    assert state.step_states(fresh_analysis)["system"] is False
    assert state.resume_step(fresh_analysis) == "system"
