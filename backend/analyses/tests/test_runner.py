import json
from datetime import timedelta

import pytest
from django.db import DatabaseError
from django.utils import timezone

from analyses import runner, runs, state
from analyses.models import AnalysisCall, AnalysisLabel, AnalysisRun, AnalysisScenario
from llm.client import OpenRouterClient
from llm.fake import FakeLLMClient
from pipeline.clients import set_llm_client

pytestmark = pytest.mark.django_db


def card(scenario_id, text="PO1 opens the console."):
    return {
        "scenario_id": scenario_id,
        "actor": "PO1",
        "trigger": "A morning shift.",
        "standing_state": "One shared password.",
        "asset": "SCADA",
        "threat": "Spoofing",
        "outcome": "8 hours of wrong readings.",
        "narrative_steps": [text, "The system accepts it."],
    }


def scenarios_reply(*ids):
    ids = ids or [f"sc_{number:02d}" for number in range(1, 9)]
    return json.dumps({"scenarios": [card(scenario_id) for scenario_id in ids]})


def labels_reply(*ids):
    return json.dumps(
        {"labels": [{"hcv_id": i, "evidence": "shared password", "rationale": "Why."} for i in ids]}
    )


@pytest.fixture
def generate(analysis, staff, queue, fake):
    def generate(reply=None):
        fake.queue(reply if reply is not None else scenarios_reply())
        run = runs.start_generation(analysis, staff)
        queue.drain()
        run.refresh_from_db()
        analysis.refresh_from_db()
        return run

    return generate


@pytest.fixture
def label(analysis, staff, queue, fake):
    def label(*replies, only_failed=False):
        for reply in replies:
            fake.queue(reply)
        run = runs.start_labelling(analysis, staff, only_failed=only_failed)
        queue.drain()
        run.refresh_from_db()
        return run

    return label


def snapshot(analysis):
    return [
        (
            s.pk,
            s.scenario_id,
            s.set_aside,
            s.label_key,
            sorted(s.labels.values_list("hcv__hcv_id", flat=True)),
        )
        for s in AnalysisScenario.objects.filter(analysis=analysis)
    ]


def test_generation_uses_target_mode(analysis, generate, fake):
    run = generate()

    sent = fake.calls[0]
    assert "Write 8 scenarios against this system." in sent["user_prompt"]
    assert analysis.description in sent["user_prompt"]
    assert "scenarios" in sent["json_schema"]["properties"]
    assert "system_descriptions" not in sent["json_schema"]["properties"]
    assert (run.status, run.done, run.failed, run.notice) == ("completed", 1, 0, "")
    saved = list(analysis.scenarios.all())
    assert [s.scenario_id for s in saved] == [f"sc_{number:02d}" for number in range(1, 9)]
    assert saved[0].narrative == "PO1 opens the console.\nThe system accepts it."
    assert saved[0].generated_by == run
    assert not any(s.is_manual or s.set_aside for s in saved)
    assert run.calls.get().parsed_ok is True


@pytest.mark.parametrize(
    "reply",
    [
        '{"scenarios": [{"scenario_id": "sc_01", "act',
        "I'm sorry, I can't help with that.",
        "",
        '{"scenarios": []}',
        None,
    ],
    ids=["truncated", "not-json", "blank", "empty-list", "transport"],
)
def test_bad_reply_keeps_previous_results(analysis, staff, queue, fake, generate, label, reply):
    generate(scenarios_reply("sc_01", "sc_02"))
    label(labels_reply("HCV-17"), labels_reply())
    analysis.scenarios.filter(scenario_id="sc_02").update(set_aside=True)
    before = snapshot(analysis)

    if reply is None:
        fake.fail_next("Timeout")
    else:
        fake.queue(reply)
    run = runs.start_generation(analysis, staff)
    queue.drain()

    run.refresh_from_db()
    assert run.status == "failed"
    assert run.error
    assert snapshot(analysis) == before


def test_regenerate_replaces_generated_and_keeps_manual_with_labels(analysis, generate, label):
    generate(scenarios_reply("sc_01"))
    manual = analysis.scenarios.create(
        scenario_id="sc_101", is_manual=True, narrative="A badge is found."
    )
    label(labels_reply("HCV-03"), labels_reply("HCV-10"))
    old = analysis.scenarios.get(scenario_id="sc_01")

    generate(scenarios_reply("sc_01", "sc_02"))

    assert not AnalysisScenario.objects.filter(pk=old.pk).exists()
    manual.refresh_from_db()
    assert [row.hcv.hcv_id for row in manual.labels.all()] == ["HCV-10"]
    assert state.label_status(analysis, manual) == "current"
    fresh = analysis.scenarios.get(scenario_id="sc_01")
    assert state.label_status(analysis, fresh) == "unlabelled"


def test_overflow_is_set_aside_with_a_notice(analysis, generate):
    for number in range(22):
        analysis.scenarios.create(
            scenario_id=f"sc_{101 + number}", is_manual=True, narrative="Mine."
        )

    run = generate(scenarios_reply(*[f"sc_{number:02d}" for number in range(1, 11)]))

    generated = analysis.scenarios.filter(is_manual=False)
    assert [s.set_aside for s in generated] == [False] * 8 + [True] * 2
    assert "set aside" in run.notice


def test_count_mismatch_is_noted(generate):
    run = generate(scenarios_reply("sc_01", "sc_02", "sc_03"))

    assert run.status == "completed"
    assert "3" in run.notice
    assert "8" in run.notice


def test_duplicate_blank_long_and_colliding_ids_are_renumbered(analysis, generate):
    analysis.scenarios.create(scenario_id="sc_101", is_manual=True, narrative="Mine.")

    generate(scenarios_reply("sc_01", "sc_01", "", "x" * 80, "sc_101", "sc_02"))

    ids = [s.scenario_id for s in analysis.scenarios.filter(is_manual=False)]
    assert len(set(ids)) == len(ids) == 6
    assert ids[0] == "sc_01"
    assert ids[5] == "sc_02"
    assert "sc_101" not in ids
    assert all(i and len(i) <= 50 for i in ids)


def test_labelling_queues_one_task_per_scenario_in_order(analysis, staff, queue, fake, generate):
    generate(scenarios_reply("sc_01", "sc_02", "sc_03"))
    for _ in range(3):
        fake.queue(labels_reply("HCV-17"))
    run = runs.start_labelling(analysis, staff)

    for position in range(3):
        assert queue.tasks == [("analyses.tasks.run_call", (run.pk, position))]
        queue.step()
        run.refresh_from_db()
        assert run.done == position + 1
        if position < 2:
            assert (run.status, run.position) == ("queued", position + 1)

    assert run.status == "completed"
    sent = [call["user_prompt"] for call in fake.calls[1:]]
    assert ["## Scenario sc_0" + str(n) in text for n, text in enumerate(sent, 1)] == [True] * 3


def test_failure_continues_then_retry_completes(analysis, staff, queue, fake, generate, label):
    generate(scenarios_reply("sc_01", "sc_02"))
    fake.fail_next("APIStatusError: no credit", http_status=402)
    run = label(labels_reply("HCV-17"))

    assert (run.status, run.done, run.failed) == ("failed", 1, 1)
    assert "Not enough OpenRouter credit" in run.error
    first, second = analysis.scenarios.all()
    assert state.label_status(analysis, first) == "failed"
    assert "Not enough OpenRouter credit" in first.calls.get().error
    assert state.label_status(analysis, second) == "current"

    retry = label(labels_reply("HCV-03"), only_failed=True)

    assert (retry.status, retry.total) == ("completed", 1)
    first.refresh_from_db()
    assert state.label_status(analysis, first) == "current"


def test_unknown_ids_stay_on_the_call_and_duplicates_save_once(analysis, generate, label):
    generate(scenarios_reply("sc_01"))

    run = label(labels_reply("HCV-17", "HCV-17", "HCV-99"))

    assert run.status == "completed"
    assert run.calls.get().unknown_hcv_ids == ["HCV-99"]
    assert [row.hcv.hcv_id for row in AnalysisLabel.objects.all()] == ["HCV-17"]


def test_empty_labels_is_current(analysis, generate, label):
    generate(scenarios_reply("sc_01"))

    run = label(labels_reply())

    scenario = analysis.scenarios.get()
    assert run.status == "completed"
    assert state.label_status(analysis, scenario) == "current"
    assert scenario.labelled_call == run.calls.get()


def test_relabelling_replaces_the_old_labels(analysis, generate, label):
    generate(scenarios_reply("sc_01"))
    label(labels_reply("HCV-17"))
    analysis.refresh_from_db()
    analysis.labelling_settings = {**analysis.labelling_settings, "examples": []}
    analysis.save()

    label(labels_reply("HCV-03"))

    assert [row.hcv.hcv_id for row in AnalysisLabel.objects.all()] == ["HCV-03"]


class Interrupting(FakeLLMClient):
    """Does something to the analysis while the provider is 'thinking'."""

    def __init__(self, reply, meanwhile):
        super().__init__([reply])
        self._meanwhile = meanwhile

    def complete(self, **kwargs):
        self._meanwhile()
        return super().complete(**kwargs)


@pytest.mark.parametrize("what", ["cancel", "delete"])
def test_cancel_or_delete_during_the_call_saves_nothing(analysis, staff, queue, what):
    run = runs.start_generation(analysis, staff)
    meanwhile = (lambda: runs.cancel(run)) if what == "cancel" else analysis.delete
    set_llm_client(Interrupting(scenarios_reply(), meanwhile))

    queue.drain()

    assert AnalysisScenario.objects.count() == 0
    assert AnalysisCall.objects.count() == 0
    if what == "cancel":
        run.refresh_from_db()
        assert run.status == "cancelled"


def test_expired_run_saves_nothing_when_it_returns(analysis, staff, queue):
    run = runs.start_generation(analysis, staff)

    def expire():
        long_ago = timezone.now() - timedelta(days=1)
        AnalysisRun.objects.filter(pk=run.pk).update(progressed_at=long_ago)
        runs.expire_stuck(analysis)

    set_llm_client(Interrupting(scenarios_reply(), expire))

    queue.drain()

    run.refresh_from_db()
    assert run.status == "failed"
    assert AnalysisScenario.objects.count() == 0


def test_duplicate_task_makes_no_second_call(analysis, staff, queue, fake):
    fake.queue(scenarios_reply())
    run = runs.start_generation(analysis, staff)
    queue.drain()

    runner.execute(run.pk, 0)

    assert len(fake.calls) == 1


def test_task_of_a_deleted_run_says_so(analysis, staff, queue, fake):
    runs.start_generation(analysis, staff)
    analysis.delete()

    assert "nothing to run" in queue.step().lower()
    assert fake.calls == []


def test_secret_in_description_fails_without_storing_a_call_body(
    analysis, staff, queue, mark_extracted
):
    client = OpenRouterClient(api_key="sk-or-v1-configured-key")

    def never(**kwargs):
        raise AssertionError("the provider must not be called")

    client._create = never
    set_llm_client(client)
    analysis.description = "The operator's key is sk-or-v1-abcdef0123456789."
    mark_extracted(analysis)
    run = runs.start_generation(analysis, staff)

    queue.drain()

    run.refresh_from_db()
    assert run.status == "failed"
    assert "Secret in request" in run.error
    call = run.calls.get()
    assert (call.rendered_user_prompt, call.request_json) == ("", None)


def test_broker_failure_mid_run_fails_it(analysis, staff, queue, fake, generate):
    generate(scenarios_reply("sc_01", "sc_02"))
    fake.queue(labels_reply())
    run = runs.start_labelling(analysis, staff)
    queue.error = ConnectionError("redis is down")

    queue.step()

    run.refresh_from_db()
    assert run.status == "failed"
    assert "queue" in run.error


def test_a_reply_the_database_refuses_fails_the_run(analysis, staff, queue, fake, monkeypatch):
    def refuse(*args, **kwargs):
        raise DatabaseError("unsupported Unicode escape sequence")

    monkeypatch.setattr(runner, "_save_scenarios", refuse)
    fake.queue(scenarios_reply())
    run = runs.start_generation(analysis, staff)

    queue.drain()

    run.refresh_from_db()
    assert run.status == "failed"
    assert run.error


def test_manual_flag_is_absent_from_the_request(analysis, generate, label, fake):
    generate(scenarios_reply("sc_01"))
    analysis.scenarios.create(scenario_id="sc_101", is_manual=True, narrative="A badge is found.")

    label(labels_reply(), labels_reply())

    prompt = fake.calls[-1]["user_prompt"]
    tail = prompt[prompt.rindex("## Scenario sc_101") :].lower()
    assert "a badge is found." in tail
    assert "manual" not in tail
    assert "added" not in tail


def test_arabic_round_trips(analysis, generate, fake, mark_extracted):
    text = "تطبيق مواقف ذكية يخدم السكان via the Parking App."
    analysis.description = text
    mark_extracted(analysis)

    generate(json.dumps({"scenarios": [card("sc_01", "يفتح المشغل لوحة التحكم.")]}))

    assert text in fake.calls[0]["user_prompt"]
    assert analysis.scenarios.get().narrative.startswith("يفتح المشغل")


def test_temperature_is_sent_for_both_steps(generate, label):
    generated = generate(scenarios_reply("sc_01"))
    labelled = label(labels_reply())

    assert generated.calls.get().request_json["temperature"] == 0.8
    assert labelled.calls.get().request_json["temperature"] == 0.0


def test_renders_with_the_frozen_title(analysis, staff, queue, fake):
    fake.queue(scenarios_reply())
    runs.start_generation(analysis, staff)
    type(analysis).objects.filter(pk=analysis.pk).update(name="Renamed meanwhile")

    queue.drain()

    assert "Smart Parking" in fake.calls[0]["user_prompt"]
    assert "Renamed meanwhile" not in fake.calls[0]["user_prompt"]


def test_a_label_run_names_the_scenario_it_is_working_on(analysis, staff, queue, fake, generate):
    from analyses.serializers import run_json

    generate(scenarios_reply("sc_01", "sc_02"))
    generation = analysis.runs.get(kind="generate")
    for _ in range(2):
        fake.queue(labels_reply("HCV-17"))
    run = runs.start_labelling(analysis, staff)
    first, second = run.targets

    assert run_json(run)["working_on"] == first
    queue.step()
    run.refresh_from_db()
    assert run_json(run)["working_on"] == second
    queue.step()
    run.refresh_from_db()
    assert run_json(run)["working_on"] is None
    assert run_json(generation)["working_on"] is None
