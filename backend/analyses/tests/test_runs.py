from datetime import timedelta

import pytest
from django.conf import settings as django_settings
from django.utils import timezone

from analyses import runs, selection, state
from analyses.models import AnalysisCall, AnalysisRun
from analyses.runs import RunRefused
from prompts.models import PromptTemplate

pytestmark = pytest.mark.django_db


def add_manual(analysis, number):
    for index in range(number):
        analysis.scenarios.create(
            scenario_id=f"sc_{101 + index}", is_manual=True, narrative="Someone waits."
        )


def refusal(start, *args, **kwargs):
    with pytest.raises(RunRefused) as raised:
        start(*args, **kwargs)
    return raised.value.code


def assert_untouched(analysis, queue):
    analysis.refresh_from_db()
    assert analysis.runs.count() == 0
    assert analysis.prompt_title == ""
    assert queue.tasks == []
    assert state.is_locked(analysis) is False


def test_start_generation_locks_and_queues_the_first_position(analysis, staff, queue):
    run = runs.start_generation(analysis, staff)

    analysis.refresh_from_db()
    assert (run.kind, run.status, run.total, run.created_by) == ("generate", "queued", 1, staff)
    assert analysis.prompt_title == "Smart Parking"
    assert state.is_locked(analysis) is True
    assert queue.tasks == [("analyses.tasks.run_call", (run.pk, 0))]


def test_missing_key_leaves_it_unlocked(analysis, staff, queue, settings):
    settings.OPENROUTER_API_KEY = ""

    assert refusal(runs.start_generation, analysis, staff) == "missing_key"
    assert_untouched(analysis, queue)


def test_queue_failure_leaves_it_unlocked(analysis, staff, queue):
    queue.error = ConnectionError("redis is down")

    assert refusal(runs.start_generation, analysis, staff) == "queue_unavailable"
    assert_untouched(analysis, queue)


def test_bad_prompt_leaves_it_unlocked(analysis, staff, queue):
    row = PromptTemplate.objects.create(
        phase=2,
        name="broken",
        version=1,
        variant="B",
        is_active=True,
        user_prompt="{{ missing_variable }}",
    )
    analysis.generation_settings = {**analysis.generation_settings, "prompt": row.pk}
    analysis.save()

    assert refusal(runs.start_generation, analysis, staff) == "invalid"
    assert_untouched(analysis, queue)


@pytest.mark.parametrize("field", ["name", "description"])
def test_blank_name_or_description_is_invalid(analysis, staff, queue, field):
    setattr(analysis, field, "  \n ")
    analysis.save()

    assert refusal(runs.start_generation, analysis, staff) == "invalid"


def test_second_start_is_already_running_and_queues_once(analysis, staff, queue):
    runs.start_generation(analysis, staff)

    assert refusal(runs.start_generation, analysis, staff) == "already_running"
    assert refusal(runs.start_labelling, analysis, staff) == "already_running"
    assert analysis.runs.count() == 1
    assert len(queue.tasks) == 1


def test_template_syntax_in_description_is_literal(analysis, staff, queue, mark_extracted):
    text = "Our template reads {{ hcv_table() }} {% for x in y %} {braces} {# note #}"
    analysis.description = text
    mark_extracted(analysis)

    run = runs.start_generation(analysis, staff)

    rendered = selection.render_generation(run.pinned, "Smart Parking", text, analysis.taxonomy)
    assert text in rendered.user


def test_description_citing_hcv_ids_and_names_starts(analysis, staff, queue, mark_extracted):
    analysis.description = (
        "An audit flagged HCV-17. Policy covers tailgating, malicious insiders "
        "and weak access controls."
    )
    mark_extracted(analysis)

    assert runs.start_generation(analysis, staff).status == "queued"


@pytest.mark.parametrize(("manual", "refused"), [(10, False), (11, True)])
def test_count_plus_manual_must_fit_in_30(analysis, staff, queue, manual, refused):
    add_manual(analysis, manual)
    analysis.generation_settings = {**analysis.generation_settings, "count": 20}
    analysis.save()

    if refused:
        assert refusal(runs.start_generation, analysis, staff) == "over_limit"
    else:
        assert runs.start_generation(analysis, staff).status == "queued"


def test_30_manual_is_refused_even_without_a_count(analysis, staff, queue):
    row = PromptTemplate.objects.create(
        phase=2,
        name="no count",
        version=1,
        variant="B",
        is_active=True,
        user_prompt="Write scenarios.",
    )
    analysis.generation_settings = {**analysis.generation_settings, "prompt": row.pk}
    analysis.save()
    add_manual(analysis, 30)

    assert refusal(runs.start_generation, analysis, staff) == "over_limit"


def test_labelling_needs_generated_scenarios(analysis, staff, queue):
    assert refusal(runs.start_labelling, analysis, staff) == "invalid"


def test_labelling_targets_only_what_is_not_current(analysis, staff, queue):
    current = analysis.scenarios.create(scenario_id="sc_01", narrative="a", order=1)
    waiting = analysis.scenarios.create(scenario_id="sc_02", narrative="b", order=2)
    analysis.scenarios.create(scenario_id="sc_03", narrative="c", order=3, set_aside=True)
    current.label_key = state.label_key(analysis, current)
    current.save()

    run = runs.start_labelling(analysis, staff)

    assert (run.kind, run.targets, run.total) == ("label", [waiting.pk], 1)


def test_nothing_to_label_is_refused(analysis, staff, queue):
    scenario = analysis.scenarios.create(scenario_id="sc_01", narrative="a")
    scenario.label_key = state.label_key(analysis, scenario)
    scenario.save()

    assert refusal(runs.start_labelling, analysis, staff) == "nothing_to_do"


def test_retry_targets_only_failed(analysis, staff, queue):
    failed = analysis.scenarios.create(scenario_id="sc_01", narrative="a")
    analysis.scenarios.create(scenario_id="sc_02", narrative="b")
    earlier = analysis.runs.create(kind="label", status="failed")
    AnalysisCall.objects.create(run=earlier, scenario=failed, error="No credit.")

    run = runs.start_labelling(analysis, staff, only_failed=True)

    assert run.targets == [failed.pk]


def test_rename_after_lock_keeps_prompt_title(analysis, staff, queue):
    first = runs.start_generation(analysis, staff)
    first.status = "completed"
    first.save()
    analysis.scenarios.create(scenario_id="sc_01", narrative="a")
    analysis.refresh_from_db()
    analysis.name = "Smart Parking, Riyadh pilot"
    analysis.save()

    runs.start_generation(analysis, staff)

    analysis.refresh_from_db()
    assert analysis.prompt_title == "Smart Parking"


def test_rename_after_a_failed_first_run_is_used(analysis, staff, queue):
    first = runs.start_generation(analysis, staff)
    first.status = "failed"
    first.save()
    analysis.refresh_from_db()
    analysis.name = "Better name"
    analysis.save()

    runs.start_generation(analysis, staff)

    analysis.refresh_from_db()
    assert analysis.prompt_title == "Better name"


def test_cancel_unlocks_and_is_repeatable(analysis, staff, queue):
    run = runs.start_generation(analysis, staff)

    assert runs.cancel(run).status == "cancelled"
    assert runs.cancel(run).status == "cancelled"
    analysis.refresh_from_db()
    assert state.is_locked(analysis) is False


@pytest.mark.parametrize(
    ("status", "minutes_late", "expected"),
    [("running", 1, "failed"), ("running", -1, "running"), ("queued", 600, "queued")],
)
def test_only_running_runs_expire(analysis, status, minutes_late, expected):
    timeout = timedelta(seconds=django_settings.Q_CLUSTER["timeout"], minutes=minutes_late)
    run = AnalysisRun.objects.create(
        analysis=analysis,
        kind="label",
        status=status,
        progressed_at=timezone.now() - timeout,
    )

    runs.expire_stuck(analysis)

    run.refresh_from_db()
    assert run.status == expected
