import json

import pytest

from analyses import runs, state
from analyses.models import AnalysisItem
from analyses.runs import RunRefused

pytestmark = pytest.mark.django_db

REPLY = json.dumps(
    {
        "elements": [
            {
                "id": "Residents",
                "section": "1. People and organizations",
                "field": "person_or_role",
                "value": "Citizen",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            },
            {
                "id": "SmartStopApp",
                "section": "2. System components",
                "field": "interface",
                "value": "MobileApp",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            },
        ],
        "facts": [
            {
                "section": "3. Use and responsibility",
                "field": "uses",
                "subject_id": "Residents",
                "value": "SmartStopApp",
                "fact_type": "Asserted",
                "reason": "",
                "other_reading": "",
            }
        ],
        "controls": [],
        "gaps": {"not_assessable": [{"area": "Physical entry", "reason": ""}], "no_term": []},
    }
)


def extract(analysis, staff, queue, fake, reply=REPLY):
    fake.queue(reply)
    run = runs.start_extraction(analysis, staff)
    queue.drain()
    run.refresh_from_db()
    analysis.refresh_from_db()
    return run


def test_extraction_sends_the_prompt_with_the_description(fresh_analysis, staff, queue, fake):
    run = extract(fresh_analysis, staff, queue, fake)

    sent = fake.calls[0]
    assert sent["system_prompt"] == ""
    assert fresh_analysis.description in sent["user_prompt"]
    assert "{{SYSTEM_DESCRIPTION}}" not in sent["user_prompt"]
    assert sent["user_prompt"].startswith("=====")
    assert sent["json_schema"]["required"] == ["elements", "facts", "controls", "gaps"]
    assert (sent["model"], sent["temperature"]) == ("anthropic/claude-sonnet-5.5", 0.0)
    assert run.status == "completed"
    assert [i.ref for i in fresh_analysis.items.all()] == ["Residents", "SmartStopApp", "Residents"]
    assert all(i.decision == "pending" for i in fresh_analysis.items.all())
    assert fresh_analysis.extraction_gaps["not_assessable"][0]["area"] == "Physical entry"
    assert state.extraction_status(fresh_analysis) == "current"


def test_a_bad_reply_keeps_the_previous_extraction(fresh_analysis, staff, queue, fake):
    extract(fresh_analysis, staff, queue, fake)
    before = list(fresh_analysis.items.values_list("pk", flat=True))

    run = extract(fresh_analysis, staff, queue, fake, reply="Sorry, I cannot.")

    assert run.status == "failed"
    assert "not JSON" in run.error
    assert list(fresh_analysis.items.values_list("pk", flat=True)) == before


def test_extracting_again_replaces_the_values(fresh_analysis, staff, queue, fake):
    extract(fresh_analysis, staff, queue, fake)
    AnalysisItem.objects.filter(analysis=fresh_analysis).update(decision="approved")

    extract(fresh_analysis, staff, queue, fake)

    assert fresh_analysis.items.count() == 3
    assert all(i.decision == "pending" for i in fresh_analysis.items.all())


def test_extraction_reads_the_description_of_when_it_started(fresh_analysis, staff, queue, fake):
    fake.queue(REPLY)
    runs.start_extraction(fresh_analysis, staff)
    type(fresh_analysis).objects.filter(pk=fresh_analysis.pk).update(
        description="Edited meanwhile."
    )

    queue.drain()

    fresh_analysis.refresh_from_db()
    assert "Edited meanwhile." not in fake.calls[0]["user_prompt"]
    assert state.extraction_status(fresh_analysis) == "stale"


def test_a_very_long_description_is_extracted(fresh_analysis, staff, queue, fake):
    fresh_analysis.description = "x" * 50_000
    fresh_analysis.save()

    assert extract(fresh_analysis, staff, queue, fake).status == "completed"


def test_extraction_is_refused_once_locked(analysis, staff, queue):
    analysis.scenarios.create(scenario_id="sc_01", narrative="x")

    with pytest.raises(RunRefused) as raised:
        runs.start_extraction(analysis, staff)
    assert raised.value.code == "invalid"


@pytest.mark.parametrize("case", ["none", "stale", "pending"])
def test_generation_needs_a_checked_current_extraction(fresh_analysis, staff, queue, fake, case):
    if case != "none":
        extract(fresh_analysis, staff, queue, fake)
        if case == "stale":
            AnalysisItem.objects.filter(analysis=fresh_analysis).update(decision="approved")
            fresh_analysis.description += " More."
            fresh_analysis.save()

    with pytest.raises(RunRefused) as raised:
        runs.start_generation(fresh_analysis, staff)
    assert raised.value.code == "invalid"


def test_generation_reads_the_checked_values_then_the_text(fresh_analysis, staff, queue, fake):
    extract(fresh_analysis, staff, queue, fake)
    AnalysisItem.objects.filter(analysis=fresh_analysis).update(decision="approved")
    fake.queue(json.dumps({"scenarios": [{"scenario_id": "sc_01", "narrative_steps": ["x"]}]}))

    runs.start_generation(fresh_analysis, staff)
    queue.drain()

    prompt = fake.calls[-1]["user_prompt"]
    assert "Write 8 scenarios against this system." in prompt
    structured = prompt.index("## Structured description\n")
    assert "- Residents (Citizen)" in prompt
    assert (
        structured
        < prompt.index("## Original description")
        < prompt.index(fresh_analysis.description)
    )


def test_labelling_reads_the_same_body(fresh_analysis, staff, queue, fake):
    extract(fresh_analysis, staff, queue, fake)
    AnalysisItem.objects.filter(analysis=fresh_analysis).update(decision="approved")
    fake.queue(json.dumps({"scenarios": [{"scenario_id": "sc_01", "narrative_steps": ["x"]}]}))
    runs.start_generation(fresh_analysis, staff)
    queue.drain()
    fake.queue(json.dumps({"labels": []}))

    runs.start_labelling(fresh_analysis, staff)
    queue.drain()

    assert "- Residents (Citizen)" in fake.calls[-1]["user_prompt"]
