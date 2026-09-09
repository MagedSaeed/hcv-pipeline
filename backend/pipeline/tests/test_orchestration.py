import json
from unittest.mock import patch

import pytest

from core.keys import MissingAPIKey
from core.models import AppSettings
from corpus.models import Origin, Scenario, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.generation import run_phase2
from pipeline.labelling import finish_labelling_run
from pipeline.labelling import label_scenario as real_label_scenario
from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun
from pipeline.orchestration import (
    execute_pipeline_run,
    launch,
    retry_failed,
    roll_up,
    run_labelling,
)
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
    lab = PromptTemplate.objects.create_version(
        phase=3, name="l", user_prompt="{{ scenario.scenario_id }}"
    )
    return Experiment.objects.create(
        name="e",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt=gen,
        phase3_model="m3",
        phase3_prompt=lab,
        n_systems=1,
        n_scenarios_per_system=1,
    )


@pytest.mark.django_db
def test_launch_creates_one_pipeline_run_per_repetition(experiment):
    runs = launch(experiment, n_repetitions=3)
    assert [r.repetition_index for r in runs] == [1, 2, 3]
    assert PipelineRun.objects.count() == 3


@pytest.mark.django_db
def test_launch_creates_a_phase_run_per_variant_and_phase(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    made = {(p.phase, p.variant) for p in run.phase_runs.all()}
    assert made == {(2, "A"), (2, "B"), (3, "A"), (3, "B")}


@pytest.mark.django_db
def test_launch_honours_a_single_variant(experiment):
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    assert {p.variant for p in run.phase_runs.all()} == {"A"}


@pytest.mark.django_db
def test_a_phase_three_run_points_at_its_phase_two_run(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    labelling = run.phase_runs.get(phase=3, variant="A")
    generation = run.phase_runs.get(phase=2, variant="A")
    assert labelling.source_phase_run == generation


@pytest.mark.django_db
def test_phase_runs_pin_the_experiment_configuration(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    assert generation.model == "m2"
    assert generation.prompt_template == experiment.phase2_prompt
    assert generation.taxonomy == experiment.taxonomy


@pytest.mark.django_db
def test_launch_seeds_phase_twos_denominator_so_it_is_never_0_of_0(experiment):
    """M6: nothing else seeds a denominator before a worker actually picks a
    run up, so a freshly queued batch read 0/0 on every row — blank for
    exactly the window --watch exists to cover. Phase 2's total is knowable
    the instant it is created (it is always exactly one call), unlike
    Phase 3's (which depends on Phase 2's still-unknown output), so only
    Phase 2 is seeded here."""
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")

    assert generation.total_children == 1
    assert generation.progress == {"done": 0, "total": 1, "failed": 0, "percent": 0}
    assert labelling.total_children == 0


@pytest.mark.django_db
def test_executing_a_run_generates_then_labels(_client, experiment):
    for _ in experiment.variants:
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 2
    assert Scenario.objects.filter(origin=Origin.GENERATED).count() == 2
    assert Label.objects.count() == 2


@pytest.mark.django_db
def test_labelling_only_sees_its_own_variants_scenarios(_client, experiment):
    for _ in experiment.variants:
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    for variant in ("A", "B"):
        labelling = run.phase_runs.get(phase=3, variant=variant)
        generation = run.phase_runs.get(phase=2, variant=variant)
        for label in labelling.labels.all():
            assert label.scenario.generation_run == generation


@pytest.mark.django_db
def test_a_failed_generation_marks_the_pipeline_run_failed(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.fail_next("rate limited", http_status=429)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.FAILED


@pytest.mark.django_db
def test_labelling_is_skipped_when_generation_failed(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.fail_next("boom")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.CANCELLED


@pytest.mark.django_db
def test_retry_reruns_only_the_failed_scenarios(_client, experiment):
    """M5: with only one scenario in play there is no healthy scenario to
    leave alone, so "only the failed one" was never actually proven. Two
    scenarios: sc_01 succeeds, sc_02 fails; retrying must touch only sc_02 —
    proven by exactly one more provider call being made, not two."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)  # sc_01 succeeds
    _client.fail_next("timeout")  # sc_02 fails
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED
    assert Label.objects.count() == 1
    calls_before_retry = len(_client.calls)

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert len(_client.calls) == calls_before_retry + 1
    assert retried.status == PhaseRun.Status.COMPLETED
    assert Label.objects.count() == 2


@pytest.mark.django_db
def test_retry_creates_no_duplicate_labels(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert Label.objects.count() == 1

    _client.queue(LABELLED)
    retry_failed(labelling)
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_retry_reruns_a_call_that_parsed_but_failed_to_persist(_client, experiment):
    """Task 4 established that a call which parsed fine but failed to PERSIST
    keeps parsed_ok=True and records the problem in parse_error instead (see
    pipeline.labelling.failed_calls). retry_failed must use that same helper —
    a bare `calls.filter(parsed_ok=False)` would never pick this call up, and
    a persistence failure would never be retried."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert Label.objects.count() == 1

    # Simulate the Task 4 persistence-failure shape directly on the archived
    # call: parsed_ok stays True, the problem lands in parse_error, and the
    # label that call claims to have written did not actually survive.
    call = labelling.calls.get()
    assert call.parsed_ok is True
    call.parse_error = "Could not persist the labels: simulated lock"
    call.save()
    Label.objects.all().delete()
    finish_labelling_run(labelling)
    labelling.refresh_from_db()
    assert labelling.status == PhaseRun.Status.FAILED

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert retried.status == PhaseRun.Status.COMPLETED
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_one_exploding_scenario_does_not_stop_the_fan_out(_client, experiment):
    """label_scenario is careful never to raise on a provider or database
    error, but the call to get_llm_client().complete() itself is unguarded —
    and OpenRouterClient deliberately raises SecretLeak if a key-shaped
    string reaches the request body, which a rendered scenario narrative
    could carry. run_labelling must survive whatever any one scenario does
    and keep going, archiving a call for the scenario that blew up instead
    of aborting the whole fan-out with nothing recorded at all."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    run = launch(experiment, n_repetitions=1)[0]

    def exploding(phase_run, scenario, sequence=1):
        if scenario.scenario_id == "sc_01":
            raise RuntimeError("simulated explosion")
        return real_label_scenario(phase_run, scenario, sequence=sequence)

    _client.queue(LABELLED)
    with patch("pipeline.orchestration.label_scenario", side_effect=exploding):
        execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.calls.count() == 2

    exploded = labelling.calls.get(scenario__scenario_id="sc_01")
    assert exploded.parsed_ok is False
    assert "simulated explosion" in exploded.parse_error

    survived = labelling.calls.get(scenario__scenario_id="sc_02")
    assert survived.parsed_ok is True

    assert Label.objects.count() == 1
    assert labelling.failed_children == 1
    assert labelling.status == PhaseRun.Status.FAILED


@pytest.mark.django_db
def test_reducing_variants_after_launch_still_completes_the_pinned_runs(_client, experiment):
    """I1: Experiment.variants is a plain, editable JSONField. Shrinking it
    (A,B -> A) after launch must not leave variant B's already-pinned phase
    runs stranded at PENDING forever with the pipeline run stuck RUNNING."""
    for _ in experiment.variants:  # A, B
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]

    experiment.variants = ["A"]
    experiment.save()

    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert {p.status for p in run.phase_runs.all()} == {PhaseRun.Status.COMPLETED}


@pytest.mark.django_db
def test_growing_variants_after_launch_does_not_raise(_client, experiment):
    """I1, the other direction: growing variants (A -> A,B) after launch must
    not make execute_pipeline_run try to find a B phase run that was never
    created — that used to raise PhaseRun.DoesNotExist and strand the
    repetition at RUNNING with roll_up never called."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]

    experiment.variants = ["A", "B"]
    experiment.save()

    execute_pipeline_run(run)  # must not raise PhaseRun.DoesNotExist
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert set(run.phase_runs.values_list("phase", "variant")) == {(2, "A"), (3, "A")}


@pytest.mark.django_db
def test_executing_a_run_twice_does_not_duplicate_anything(_client, experiment):
    """I3: execute_pipeline_run is not naturally idempotent — without a
    guard, a second call would run_phase2/run_labelling all over again and
    double every system, scenario, label and LLMCall this repetition already
    produced, silently corrupting the research record."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    systems_before = SystemDescription.objects.filter(origin=Origin.GENERATED).count()
    scenarios_before = Scenario.objects.filter(origin=Origin.GENERATED).count()
    labels_before = Label.objects.count()
    calls_before = LLMCall.objects.count()

    # Nothing queued for a second pass: if the guard is missing, this makes
    # another real provider call and raises "no queued response left."
    execute_pipeline_run(run)

    run.refresh_from_db()
    assert run.status == PipelineRun.Status.COMPLETED
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == systems_before
    assert Scenario.objects.filter(origin=Origin.GENERATED).count() == scenarios_before
    assert Label.objects.count() == labels_before
    assert LLMCall.objects.count() == calls_before


@pytest.mark.django_db
def test_retry_on_a_cancelled_run_is_a_true_no_op(_client, experiment):
    """I2: a labelling run CANCELLED because its generation failed has zero
    calls to retry. Handing it to finish_labelling_run anyway would report a
    false COMPLETED with failed_children=0, wiping the CANCELLED status and
    its "generation did not complete" error — a green status for work that
    never happened."""
    experiment.variants = ["A"]
    experiment.save()
    _client.fail_next("boom")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.CANCELLED

    retried = retry_failed(labelling)

    assert retried.status == PhaseRun.Status.CANCELLED
    assert retried.error == "Skipped: generation did not complete."
    assert retried.calls.count() == 0


@pytest.mark.django_db
def test_retry_on_a_healthy_run_is_a_true_no_op(_client, experiment):
    """I2, the other direction: retrying a run with nothing failed must not
    make any provider call at all — proven here by leaving nothing queued."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.COMPLETED
    calls_before = labelling.calls.count()

    retried = retry_failed(labelling)  # nothing queued: must not call the client

    assert retried.status == PhaseRun.Status.COMPLETED
    assert retried.calls.count() == calls_before
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_a_non_database_error_from_persist_updates_the_existing_call_not_a_duplicate(
    _client, experiment
):
    """M7: label_scenario saves its call with parsed_ok=True BEFORE calling
    _persist. If _persist raises something other than DatabaseError, that
    escapes label_scenario uncaught and lands in run_labelling's broad
    guard (the same one the SecretLeak fix relies on) — which must update
    the call already archived for that scenario rather than leave it
    falsely claiming success next to a second, duplicate row."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    _client.queue(LABELLED)
    with patch("pipeline.labelling._persist", side_effect=RuntimeError("boom")):
        run_labelling(labelling)

    assert labelling.calls.count() == 1
    call = labelling.calls.get()
    assert call.parsed_ok is False
    assert "boom" in call.parse_error
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_a_labelling_run_whose_source_was_deleted_fails_instead_of_labelling_everything(
    _client, experiment
):
    """M8: source_phase_run is SET_NULL on delete. Deleting a phase-2 run
    must not silently make its labelling run fan out over the entire
    corpus — variant independence must not collapse just because one row
    was deleted — and zero calls from that empty fan-out must not read as a
    silent COMPLETED."""
    experiment.variants = ["A"]
    experiment.save()

    # Already in the corpus, unrelated to this run's own (not yet created)
    # generated material, so "fan out over everything" and "fan out over
    # nothing" are distinguishable.
    other_system = SystemDescription.objects.create(title="Other", body="x", origin=Origin.SEED)
    Scenario.objects.create(
        system_description=other_system,
        scenario_id="unrelated",
        origin=Origin.SEED,
        order=1,
    )

    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")

    generation.delete()
    labelling.refresh_from_db()
    assert labelling.source_phase_run_id is None

    result = run_labelling(labelling)

    assert result.status == PhaseRun.Status.FAILED
    assert result.calls.count() == 0
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_launch_continues_numbering_across_calls(experiment):
    """M4: repetition numbering must continue, not restart, across separate
    launch() calls for the same experiment."""
    first = launch(experiment, n_repetitions=2)
    assert [r.repetition_index for r in first] == [1, 2]

    second = launch(experiment, n_repetitions=3)
    assert [r.repetition_index for r in second] == [3, 4, 5]


@pytest.mark.django_db
def test_roll_up_reports_completed_only_when_every_phase_did(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.update(status=PhaseRun.Status.COMPLETED)
    assert roll_up(run).status == PipelineRun.Status.COMPLETED

    run.phase_runs.filter(phase=3, variant="B").update(status=PhaseRun.Status.FAILED)
    assert roll_up(run).status == PipelineRun.Status.FAILED


@pytest.mark.django_db
def test_roll_up_reports_running_while_any_phase_is_pending_or_running(experiment):
    """M1: no prior test covered the RUNNING branch at all."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.COMPLETED)
    run.phase_runs.filter(phase=3, variant="A").update(status=PhaseRun.Status.RUNNING)
    # phase 2/3 variant B are left at their default, PENDING.

    assert roll_up(run).status == PipelineRun.Status.RUNNING


@pytest.mark.django_db
def test_roll_up_does_not_stamp_finished_at_while_running(experiment):
    """M2: finished_at must only be set on a terminal transition — setting
    it while still RUNNING would claim the run finished when it has not."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.COMPLETED)

    rolled = roll_up(run)

    assert rolled.status == PipelineRun.Status.RUNNING
    assert rolled.finished_at is None


@pytest.mark.django_db
def test_roll_up_treats_queued_the_same_as_pending_or_running(experiment):
    """A repetition still sitting in Django-Q's queue, blocked on a free
    worker, must roll up as RUNNING (in progress), never FAILED — QUEUED has
    to join {PENDING, RUNNING} in roll_up's status set."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.COMPLETED)
    # Every other phase run QUEUED — none left PENDING or RUNNING — so this
    # isolates QUEUED itself; without it in roll_up's set, this rolls up FAILED.
    run.phase_runs.exclude(phase=2, variant="A").update(status=PhaseRun.Status.QUEUED)

    assert roll_up(run).status == PipelineRun.Status.RUNNING


@pytest.mark.django_db
def test_roll_up_reports_queued_when_every_phase_run_is_queued(experiment):
    """M11: roll_up had no way to express QUEUED at all — a fully-queued
    repetition (handed to Django-Q, no worker has touched it yet) rolled up
    to RUNNING, erasing the queued/started distinction this whole feature
    exists to expose for any future status endpoint built on top of it."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.update(status=PhaseRun.Status.QUEUED)

    assert roll_up(run).status == PipelineRun.Status.QUEUED


@pytest.mark.django_db
def test_roll_up_reports_queued_for_a_pending_and_queued_mix_with_none_running(experiment):
    """The broader case: some phase runs QUEUED, the rest still PENDING (not
    yet reached by the CLI's cascade — or a future partial-queue path), but
    none RUNNING yet. Still QUEUED, not RUNNING."""
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.filter(phase=2, variant="A").update(status=PhaseRun.Status.QUEUED)
    # Every other phase run left PENDING.

    assert roll_up(run).status == PipelineRun.Status.QUEUED


@pytest.mark.django_db
def test_roll_up_reports_running_not_queued_when_nothing_was_ever_queued(experiment):
    """All-PENDING must NOT read as QUEUED — nothing was actually handed to
    Django-Q yet, so calling it "queued" would be a mislabel. Unchanged from
    before this fix: a fresh, untouched repetition still reports RUNNING."""
    run = launch(experiment, n_repetitions=1)[0]

    assert roll_up(run).status == PipelineRun.Status.RUNNING


@pytest.mark.django_db
def test_roll_up_of_zero_phase_runs_is_not_vacuously_completed(experiment):
    """M3: `set() <= {COMPLETED}` is vacuously True, so a PipelineRun with no
    phase runs at all must be guarded explicitly rather than falling
    through to a false COMPLETED."""
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    assert run.phase_runs.count() == 0

    rolled = roll_up(run)

    assert rolled.status == PipelineRun.Status.RUNNING
    assert rolled.finished_at is None


@pytest.mark.django_db
def test_launch_with_no_key_configured_creates_nothing_and_raises(_client, experiment, settings):
    """C2a: spec §12 — with no key from either source, launching must fail
    before any HTTP call is made, and (because `launch` is atomic and resolves
    the key first) before a single row exists. Otherwise the repetition is
    created, `run_phase2` explodes on the unguarded client call, and the run is
    stranded RUNNING with no way to re-drive it past the idempotency guard."""
    settings.OPENROUTER_API_KEY = ""
    config = AppSettings.load()
    config.openrouter_key = ""
    config.save()

    with pytest.raises(MissingAPIKey):
        launch(experiment, n_repetitions=1)

    assert PipelineRun.objects.count() == 0
    assert PhaseRun.objects.count() == 0
    assert _client.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [MissingAPIKey("No OpenRouter API key is configured."), RuntimeError("something arbitrary")],
)
def test_an_arbitrary_client_error_fails_the_run_instead_of_stranding_it(
    _client, experiment, error
):
    """C2b: `run_phase2`'s client call was unguarded and `execute_pipeline_run`
    does not wrap it, so anything the client raises escaped the whole pipeline —
    leaving the PipelineRun `running` with its phase runs `running`/`pending`,
    and a second `execute_pipeline_run` just re-rolls it up to RUNNING forever."""
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]

    class Exploding:
        def complete(self, **kwargs):
            raise error

    set_llm_client(Exploding())
    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.FAILED
    assert run.finished_at is not None
    generation = run.phase_runs.get(phase=2, variant="A")
    assert generation.status == PhaseRun.Status.FAILED
    assert str(error) in generation.error
    assert generation.finished_at is not None


@pytest.mark.django_db
def test_execute_pipeline_run_executes_a_queued_run(_client, experiment):
    """A run the CLI marked QUEUED before dispatching to Django-Q must still
    actually execute once a worker picks it up — the idempotency guard must
    accept QUEUED alongside PENDING, not just roll up the (still-empty) run
    and leave it stranded."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    run.status = PipelineRun.Status.QUEUED
    run.save(update_fields=["status"])
    run.phase_runs.update(status=PhaseRun.Status.QUEUED)

    execute_pipeline_run(run)
    run.refresh_from_db()

    assert run.status == PipelineRun.Status.COMPLETED
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_run_phase2_executes_a_queued_run(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    generation.status = PhaseRun.Status.QUEUED
    generation.save(update_fields=["status"])

    result = run_phase2(generation)

    assert result.status == PhaseRun.Status.COMPLETED


@pytest.mark.django_db
def test_run_labelling_executes_a_queued_run(_client, experiment):
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)
    labelling.status = PhaseRun.Status.QUEUED
    labelling.save(update_fields=["status"])

    result = run_labelling(labelling)

    assert result.status == PhaseRun.Status.COMPLETED


@pytest.mark.django_db
def test_run_labelling_does_not_re_execute_a_finished_run(_client, experiment):
    """M1: `run_labelling` had no re-entry guard, so calling it twice made two
    provider calls for one scenario — double-counting tokens and cost — while
    `get_or_create` quietly hid the duplication on the labels themselves."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.COMPLETED
    calls_before = LLMCall.objects.count()
    finished_at = labelling.finished_at

    # Nothing queued: a second execution would make a real call and raise.
    again = run_labelling(labelling)

    assert again.status == PhaseRun.Status.COMPLETED
    assert again.finished_at == finished_at
    assert LLMCall.objects.count() == calls_before
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_run_labelling_sets_total_children_before_the_fan_out(_client, experiment):
    """total_children must reflect the scenario count from the start, so a
    caller polling mid-run sees a real denominator, not zero."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    run_labelling(labelling)
    labelling.refresh_from_db()

    assert labelling.total_children == 2
    assert labelling.completed_children == 2
    assert labelling.failed_children == 0
    assert labelling.progress == {"done": 2, "total": 2, "failed": 0, "percent": 100}


@pytest.mark.django_db
def test_run_labelling_increments_completed_and_failed_children_live(_client, experiment):
    """A mix of one success and one failure must land in the right counter —
    and both must be visible on the SAME object the loop mutates, not only
    after a refresh, since that is what a live progress hook reads."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    finished = run_labelling(labelling)

    assert finished.completed_children == 1
    assert finished.failed_children == 1
    assert finished.progress == {"done": 2, "total": 2, "failed": 1, "percent": 100}


@pytest.mark.django_db
def test_run_labelling_counts_an_exploding_scenario_as_failed(_client, experiment):
    """The broad except-guard path (a scenario that raises rather than
    returning a failed call) must also land in failed_children, not be
    silently dropped from the live counters."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    def exploding(phase_run, scenario, sequence=1):
        if scenario.scenario_id == "sc_01":
            raise RuntimeError("simulated explosion")
        return real_label_scenario(phase_run, scenario, sequence=sequence)

    _client.queue(LABELLED)
    with patch("pipeline.orchestration.label_scenario", side_effect=exploding):
        finished = run_labelling(labelling)

    assert finished.total_children == 2
    assert finished.completed_children == 1
    assert finished.failed_children == 1


@pytest.mark.django_db
def test_run_labelling_calls_on_progress_after_each_scenario(_client, experiment):
    """The CLI's --inline progress lines hinge on this: on_progress must fire
    once per scenario, live, with the running totals at that point — not just
    once at the very end. M7 adds one more call after finish_labelling_run
    itself, so a zero-scenario fan-out still reports something (see
    test_run_labelling_calls_on_progress_once_for_zero_scenarios) — here,
    with two real scenarios, that shows up as a third call carrying the same
    final numbers as the second."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    seen = []
    run_labelling(labelling, on_progress=lambda pr: seen.append(dict(pr.progress)))

    assert len(seen) == 3
    assert seen[0] == {"done": 1, "total": 2, "failed": 0, "percent": 50}
    assert seen[1] == {"done": 2, "total": 2, "failed": 1, "percent": 100}
    assert seen[2] == {"done": 2, "total": 2, "failed": 1, "percent": 100}


@pytest.mark.django_db
def test_run_labelling_resets_completed_and_failed_children_on_re_entry(_client, experiment):
    """M4: total_children was reset before the fan-out but completed_children
    and failed_children were only ever += 'd — so re-entering a non-terminal
    run (a RUNNING phase run picked up again after, say, a Django-Q retry)
    accumulated on top of whatever a previous, unfinished attempt had left
    behind. Observed live: done=3/total=2 -> a 150% progress bar. Proven via
    the live on_progress callback, not the final state — finish_labelling_run
    already recomputes the final numbers fresh, which would mask a reset bug
    that only shows up mid-run."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.queue(LABELLED)
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    run_phase2(generation)

    # A stale, half-finished previous attempt left these behind.
    labelling.status = PhaseRun.Status.RUNNING
    labelling.total_children = 2
    labelling.completed_children = 3
    labelling.failed_children = 1
    labelling.save()

    seen = []
    run_labelling(labelling, on_progress=lambda pr: seen.append(dict(pr.progress)))

    assert seen[0] == {"done": 1, "total": 2, "failed": 0, "percent": 50}


@pytest.mark.django_db
def test_run_labelling_calls_on_progress_once_for_zero_scenarios(_client, experiment):
    """M7: on_progress previously fired only inside the scenario loop, so a
    Phase 3 run over zero scenarios (its source phase run deleted after
    launch — scenarios_for returns none()) printed nothing at all, leaving
    that repetition looking stuck rather than reporting its (failed, 0/0)
    outcome."""
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    generation = run.phase_runs.get(phase=2, variant="A")
    labelling = run.phase_runs.get(phase=3, variant="A")
    generation.delete()
    labelling.refresh_from_db()
    assert labelling.source_phase_run_id is None

    seen = []
    run_labelling(labelling, on_progress=lambda pr: seen.append(dict(pr.progress)))

    assert len(seen) == 1
    assert seen[0] == {"done": 0, "total": 0, "failed": 0, "percent": 0}


@pytest.mark.django_db
def test_retry_failed_reconciles_completed_children_too(_client, experiment):
    """M5's retry scenario, extended: once the previously-failed scenario
    succeeds on retry, completed_children must catch up too — not just
    failed_children — or progress would understate a fully-finished run."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED_TWO_SCENARIOS)
    _client.queue(LABELLED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.completed_children == 1
    assert labelling.failed_children == 1

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert retried.completed_children == 2
    assert retried.failed_children == 0
    assert retried.progress == {"done": 2, "total": 2, "failed": 0, "percent": 100}


@pytest.mark.django_db
def test_run_labelling_does_not_re_execute_a_failed_run(_client, experiment):
    """The other terminal status: a FAILED run is retried through
    `retry_failed`, which re-drives only the failed children, never by calling
    `run_labelling` again over the whole fan-out."""
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED
    calls_before = LLMCall.objects.count()

    again = run_labelling(labelling)

    assert again.status == PhaseRun.Status.FAILED
    assert LLMCall.objects.count() == calls_before
