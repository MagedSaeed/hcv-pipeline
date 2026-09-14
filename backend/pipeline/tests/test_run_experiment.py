import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.models import Experiment, Label, PhaseRun, PipelineRun
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
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    exp = Experiment.objects.create(
        name="baseline",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=gen,
        phase3_model="m3",
        phase3_prompt=lab,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    exp.variants = ["A"]
    exp.save()
    return exp


@pytest.mark.django_db
def test_the_command_runs_an_experiment_inline(_client, experiment):
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    call_command("run_experiment", "baseline", "--inline")

    assert PipelineRun.objects.count() == 1
    assert PipelineRun.objects.get().status == PipelineRun.Status.COMPLETED
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_the_command_honours_repetitions(_client, experiment):
    for _ in range(2):
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    call_command("run_experiment", "baseline", "--repetitions", "2", "--inline")
    assert PipelineRun.objects.count() == 2


@pytest.mark.django_db
def test_an_unknown_experiment_reports_clearly(_client):
    with pytest.raises(CommandError, match="No experiment"):
        call_command("run_experiment", "absent", "--inline")


@pytest.mark.django_db
def test_an_invalid_experiment_is_refused_before_any_call(_client, experiment):
    experiment.generate_systems = False
    experiment.save()
    with pytest.raises(CommandError, match="target_system"):
        call_command("run_experiment", "baseline", "--inline")
    assert PipelineRun.objects.count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_the_command_queues_onto_django_q_by_default(_client, experiment):
    """M6: without --inline, the command must queue via
    django_q.tasks.async_task with the exact string task path
    "pipeline.tasks.run_pipeline_task" and the run's pk — that path is a
    plain string nowhere else checked, and a typo there would ship
    silently. Patched rather than run for real: `async_task` is imported
    fine with no broker configured (verified independently), so this needs
    no Redis.

    The run (and its phase runs) must be QUEUED, not PENDING, the moment it
    is handed off — PENDING now means "not queued yet"; QUEUED means
    "waiting for a free worker", and this is the exact moment that becomes
    true."""
    with patch("pipeline.orchestration.async_task") as mock_async_task:
        call_command("run_experiment", "baseline")

    run = PipelineRun.objects.get()
    mock_async_task.assert_called_once_with("pipeline.tasks.run_pipeline_task", run.pk)
    assert run.status == PipelineRun.Status.QUEUED
    assert {p.status for p in run.phase_runs.all()} == {PhaseRun.Status.QUEUED}
    assert _client.calls == []


@pytest.mark.django_db
def test_inline_prints_progress_lines_as_work_completes(_client, experiment):
    """Task requirement: --inline prints a progress line as work completes,
    e.g. "rep 2/3 | phase 3 A | running | 7/12 scenarios | 1 failed". With
    two scenarios in play, phase 3 must print more than once — once per
    scenario, live, plus once more (M7) after the run itself finishes — not
    just once at the very end. I2: each line must carry the phase run's
    status, or a queued call and a just-started one print byte-identical
    lines."""
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.queue(LABELLED)
    out = StringIO()

    call_command("run_experiment", "baseline", "--inline", stdout=out)

    lines = out.getvalue().splitlines()
    assert "rep 1/1 | phase 2 A | completed | 1/1 call | 0 failed" in lines
    assert "rep 1/1 | phase 3 A | running | 1/2 scenarios | 0 failed" in lines
    assert "rep 1/1 | phase 3 A | running | 2/2 scenarios | 0 failed" in lines
    assert "rep 1/1 | phase 3 A | completed | 2/2 scenarios | 0 failed" in lines


@pytest.mark.django_db
def test_inline_progress_lines_number_each_repetition(_client, experiment):
    for _ in range(2):
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    out = StringIO()

    call_command("run_experiment", "baseline", "--repetitions", "2", "--inline", stdout=out)

    lines = out.getvalue().splitlines()
    assert "rep 1/2 | phase 2 A | completed | 1/1 call | 0 failed" in lines
    assert "rep 2/2 | phase 2 A | completed | 1/1 call | 0 failed" in lines


@pytest.mark.django_db
def test_inline_progress_lines_use_the_actual_repetition_index_across_batches(_client, experiment):
    """M8: 'rep i/N' must be the repetition's actual repetition_index, not
    its position within THIS invocation — a second, later batch used to
    print 'rep 1/2' for what was actually repetition 3, since launch()
    numbers repetitions continuing from wherever the experiment left off."""
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    call_command("run_experiment", "baseline", "--inline")  # repetition 1

    _client.queue(GENERATED)
    _client.queue(LABELLED)
    out = StringIO()
    call_command("run_experiment", "baseline", "--inline", stdout=out)  # repetition 2

    lines = out.getvalue().splitlines()
    assert "rep 2/2 | phase 2 A | completed | 1/1 call | 0 failed" in lines
    assert not any(line.startswith("rep 1/1") for line in lines)


@pytest.mark.django_db
def test_watch_polls_the_database_until_every_repetition_is_terminal(_client, experiment):
    """--watch (the queued, non-inline path) must poll the DB and print the
    same line shape until every repetition reaches a terminal status, then
    exit — proven here with no broker at all: async_task is mocked (as the
    existing queueing test does) and the poll's sleep is mocked with a side
    effect that advances the seeded rows to COMPLETED, simulating a worker
    finishing the work between polls. I2: the line must carry status — a
    queued row and a just-completed one must not print identically. Phase 2
    is seeded with total_children=1 at launch (M6), so it starts at "0/1",
    never "0/0"."""

    def advance_to_completed(seconds):
        PipelineRun.objects.update(status=PipelineRun.Status.COMPLETED)
        PhaseRun.objects.update(
            status=PhaseRun.Status.COMPLETED, total_children=1, completed_children=1
        )

    out = StringIO()
    with (
        patch("pipeline.orchestration.async_task"),
        patch(
            "pipeline.management.commands.run_experiment.time.sleep",
            side_effect=advance_to_completed,
        ) as mock_sleep,
    ):
        call_command("run_experiment", "baseline", "--watch", stdout=out)

    assert mock_sleep.call_count == 1
    lines = out.getvalue().splitlines()
    assert "rep 1/1 | phase 2 A | queued | 0/1 call | 0 failed" in lines
    assert "rep 1/1 | phase 2 A | completed | 1/1 call | 0 failed" in lines
    assert PipelineRun.objects.get().status == PipelineRun.Status.COMPLETED


@pytest.mark.django_db
def test_watch_prints_a_ctrl_c_hint_before_anything_else(_client, experiment):
    """I3(c): a --watch that can run for up to an hour by default should say,
    up front, how to get out of it."""

    def advance_to_completed(seconds):
        PipelineRun.objects.update(status=PipelineRun.Status.COMPLETED)
        PhaseRun.objects.update(
            status=PhaseRun.Status.COMPLETED, total_children=1, completed_children=1
        )

    out = StringIO()
    with (
        patch("pipeline.orchestration.async_task"),
        patch(
            "pipeline.management.commands.run_experiment.time.sleep",
            side_effect=advance_to_completed,
        ),
    ):
        call_command("run_experiment", "baseline", "--watch", stdout=out)

    lines = out.getvalue().splitlines()
    assert "Ctrl-C" in lines[0]


@pytest.mark.django_db
def test_watch_only_reprints_a_row_whose_status_or_progress_changed(_client, experiment):
    """I3(a): --watch must not reprint every row of every repetition on
    every poll regardless of whether anything changed — probed live at 3
    repetitions producing 12 identical lines per 3-second poll, forever.
    Here: the second poll changes nothing, so it must add no new lines for
    phase 2's row; the third poll (which does change it) must."""
    poll = {"count": 0}

    def maybe_advance(seconds):
        poll["count"] += 1
        if poll["count"] >= 2:
            PipelineRun.objects.update(status=PipelineRun.Status.COMPLETED)
            PhaseRun.objects.update(
                status=PhaseRun.Status.COMPLETED, total_children=1, completed_children=1
            )
        # else: first sleep is a no-op poll — nothing in the DB changes.

    out = StringIO()
    with (
        patch("pipeline.orchestration.async_task"),
        patch(
            "pipeline.management.commands.run_experiment.time.sleep",
            side_effect=maybe_advance,
        ) as mock_sleep,
    ):
        call_command("run_experiment", "baseline", "--watch", stdout=out)

    assert mock_sleep.call_count == 2
    phase2_lines = [line for line in out.getvalue().splitlines() if "phase 2 A" in line]
    # Initial queued state, then the completed state — never a third,
    # identical "still queued" repeat for the no-op poll in between.
    assert phase2_lines == [
        "rep 1/1 | phase 2 A | queued | 0/1 call | 0 failed",
        "rep 1/1 | phase 2 A | completed | 1/1 call | 0 failed",
    ]


@pytest.mark.django_db
def test_watch_gives_up_after_watch_timeout_and_exits_with_an_error(_client, experiment):
    """I3(b): nothing ever advances these rows here (async_task is mocked to
    a no-op and nothing else touches the DB), simulating --watch run with no
    qcluster process at all — the historically hanging-forever case. It must
    instead give up after --watch-timeout and raise a clear, non-zero-exit
    error rather than loop forever."""
    out = StringIO()
    with (
        patch("pipeline.orchestration.async_task"),
        patch("pipeline.management.commands.run_experiment.time.sleep"),
        pytest.raises(CommandError, match="gave up") as excinfo,
    ):
        call_command("run_experiment", "baseline", "--watch", "--watch-timeout", "5", stdout=out)
    assert "5" in str(excinfo.value)


@pytest.mark.django_db
def test_watch_is_ignored_when_inline_is_also_passed(_client, experiment):
    """--watch only makes sense for the queued path; inline already runs (and
    prints) synchronously, so passing both must not hang the command."""
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    out = StringIO()

    call_command("run_experiment", "baseline", "--inline", "--watch", stdout=out)

    assert PipelineRun.objects.get().status == PipelineRun.Status.COMPLETED


@pytest.mark.django_db
def test_a_broker_error_reverts_that_run_to_pending_and_raises_clearly(_client, experiment):
    """M9: the run is marked QUEUED before async_task is called (so it reads
    correctly for the window it really is waiting on a worker), but if the
    broker call itself then fails, a run claiming "waiting for a worker" that
    was never actually enqueued is worse than useless — and the old code
    aborted the whole loop silently, leaving every later repetition sitting
    at PENDING with no explanation at all. The one that failed must revert to
    PENDING and the failure must raise a clear, specific message; a
    repetition already successfully queued before it stays QUEUED; a
    repetition the loop never reached stays PENDING as it always was."""
    with (
        patch(
            "pipeline.orchestration.async_task",
            side_effect=[None, RuntimeError("no redis"), None],
        ),
        pytest.raises(CommandError, match="repetition 2"),
    ):
        call_command("run_experiment", "baseline", "--repetitions", "3")

    runs = list(PipelineRun.objects.order_by("repetition_index"))
    assert [r.repetition_index for r in runs] == [1, 2, 3]
    assert runs[0].status == PipelineRun.Status.QUEUED
    assert runs[1].status == PipelineRun.Status.PENDING
    assert {p.status for p in runs[1].phase_runs.all()} == {PhaseRun.Status.PENDING}
    assert runs[2].status == PipelineRun.Status.PENDING


def test_repetitions_help_text_explains_why_the_number_exists_and_stays_defaulted_to_one():
    """Task 2: nothing about `n_repetitions`/`--repetitions` is broken — the
    default is 1 in every place it is set and must STAY 1 (a researcher opts
    into more, deliberately) — the only gap was that a researcher had no
    explanation of why the field exists at all. `--repetitions --help` must
    now say, in plain language: repeated runs are what turn a single noisy
    A-vs-B difference into a trustworthy finding, and name the real cost
    anchor measured on this project so a researcher can decide how many they
    can afford.

    Coordinator review round 2: the FIRST version of this text claimed
    repetitions "run one after another, not in parallel" — false for the
    default (queued) path this command uses unless `--inline` is passed.
    `queue_runs` hands ALL N repetitions to django-q immediately, and
    `Q_CLUSTER["workers"]` defaults to 2, so two repetitions genuinely run
    CONCURRENTLY; only `--inline` is actually serial. Cost is still a
    straight multiply (N x $1.61); wall-clock on the default path is not.

    Coordinator review round 3 caught a SECOND bug in the fix for the
    first one: "N x 23 minutes divided by 2" reads as 11.5 minutes at
    N=1 — wrong, a single repetition takes the full 23 minutes regardless
    of how many workers are free. The text must read correctly at N=1.
    """
    from pipeline.management.commands.run_experiment import Command

    parser = Command().create_parser("manage.py", "run_experiment")
    action = next(a for a in parser._actions if "--repetitions" in a.option_strings)

    assert action.default == 1
    help_text = action.help.lower()
    assert "cost of one repetition" in help_text
    assert "queued together" in help_text
    assert "workers" in help_text
    assert "inline" in help_text
    assert "one after another" not in help_text
    assert "not in parallel" not in help_text
    assert "1 repetition takes as long as one run" in help_text
    assert "divided by 2" not in help_text
    assert "noise" in help_text or "stochastic" in help_text
