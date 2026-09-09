import json

import pytest

from core.keys import MissingAPIKey
from corpus.models import Origin, Scenario, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.generation import run_phase2
from pipeline.models import Experiment, PhaseRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

CARD = {
    "scenario_id": "sc_01",
    "actor": "EP1 (ExternalParty)",
    "trigger": "After 22:00.",
    "standing_state": "One shared password.",
    "asset": "The SCADA Service",
    "threat": "Spoofing.",
    "outcome": "The feed ran at 11,100 ppm.",
    "narrative_steps": ["EP1 opens the session.", "EP1 sends a setpoint."],
}
PAYLOAD = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "A plant.", "scenarios": [CARD]}]}
)


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def template(db):
    return PromptTemplate.objects.create_version(
        phase=2,
        name="gen",
        system_prompt="You write scenarios.",
        user_prompt="Write {{ n_systems }} systems with {{ n_scenarios_per_system }} each.",
    )


@pytest.fixture
def experiment(db, taxonomy, template):
    labeller = PromptTemplate.objects.create_version(phase=3, name="lab", user_prompt="l")
    return Experiment.objects.create(
        name="e",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt=template,
        phase3_model="m3",
        phase3_prompt=labeller,
        n_systems=1,
        n_scenarios_per_system=1,
    )


def make_run(experiment, taxonomy, template, variant="A"):
    """A phase run wired to its experiment, so its configuration is honoured.

    A PhaseRun with no pipeline_run is the ad-hoc iterate-mode path and falls
    back to defaults — which would silently ignore target_system.
    """
    from pipeline.models import PipelineRun

    highest = (
        PipelineRun.objects.filter(experiment=experiment)
        .order_by("-repetition_index")
        .values_list("repetition_index", flat=True)
        .first()
    ) or 0
    pipeline_run = PipelineRun.objects.create(experiment=experiment, repetition_index=highest + 1)
    return PhaseRun.objects.create(
        pipeline_run=pipeline_run,
        phase=2,
        variant=variant,
        model="m2",
        temperature=1.0,
        prompt_template=template,
        taxonomy=taxonomy,
    )


@pytest.mark.django_db
def test_a_successful_run_sets_total_and_completed_children(
    _client, experiment, taxonomy, template
):
    """Phase 2 is one call: total_children=1 from the start, completed_children=1
    only once that one call actually succeeds."""
    _client.queue(PAYLOAD)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.COMPLETED
    assert run.total_children == 1
    assert run.completed_children == 1
    assert run.progress == {"done": 1, "total": 1, "failed": 0, "percent": 100}


@pytest.mark.django_db
def test_a_failed_run_counts_its_one_call_as_failed(_client, experiment, taxonomy, template):
    """I1: a failed Phase 2 run is one failed call, not a call that vanished.
    Leaving failed_children at 0 here made a batch containing this failure
    read as e.g. 75% with 0 failures forever — done never reaches total, and
    the failure a progress indicator exists to surface stays invisible."""
    _client.fail_next("rate limited", http_status=429)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert run.total_children == 1
    assert run.completed_children == 0
    assert run.failed_children == 1
    assert run.progress == {"done": 1, "total": 1, "failed": 1, "percent": 100}


@pytest.mark.django_db
def test_a_successful_run_persists_the_generated_material(_client, experiment, taxonomy, template):
    _client.queue(PAYLOAD)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.COMPLETED
    system = SystemDescription.objects.get(title="Riverbend")
    assert system.origin == Origin.GENERATED
    assert system.generation_run == run

    scenario = Scenario.objects.get(scenario_id="sc_01")
    assert scenario.origin == Origin.GENERATED
    assert scenario.threat == "Spoofing."
    assert scenario.narrative == "EP1 opens the session.\nEP1 sends a setpoint."
    assert scenario.system_description == system


@pytest.mark.django_db
def test_the_call_is_archived_with_its_rendered_prompts(_client, experiment, taxonomy, template):
    _client.queue(PAYLOAD)
    run = run_phase2(make_run(experiment, taxonomy, template))

    call = run.calls.get()
    assert call.rendered_system_prompt == "You write scenarios."
    assert call.rendered_user_prompt == "Write 1 systems with 1 each."
    assert call.parsed_ok is True
    assert call.response_json is not None
    assert call.leak_scan_ran is True
    assert call.guarded_by == taxonomy


@pytest.mark.django_db
def test_variant_a_is_scanned_and_variant_b_is_not(_client, experiment, taxonomy, template):
    _client.queue(PAYLOAD)
    a = run_phase2(make_run(experiment, taxonomy, template, variant="A"))
    _client.queue(PAYLOAD)
    b = run_phase2(make_run(experiment, taxonomy, template, variant="B"))

    assert a.calls.get().leak_scan_ran is True
    assert b.calls.get().leak_scan_ran is False


@pytest.mark.django_db
def test_variant_b_receives_the_taxonomy_and_a_does_not(_client, experiment, taxonomy, template):
    guided = PromptTemplate.objects.create_version(
        phase=2, name="guided", user_prompt="{{ hcv_table() }}"
    )
    _client.queue(PAYLOAD)
    b = run_phase2(make_run(experiment, taxonomy, guided, variant="B"))
    assert "HCV-07" in b.calls.get().rendered_user_prompt

    a = run_phase2(make_run(experiment, taxonomy, guided, variant="A"))
    assert a.status == PhaseRun.Status.FAILED
    assert "hcv_table" in a.error


@pytest.mark.django_db
def test_scenarios_go_to_the_target_system_when_not_generating_systems(
    _client, experiment, taxonomy, template
):
    target = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    _client.queue(json.dumps({"scenarios": [CARD]}))
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.COMPLETED
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0
    scenario = Scenario.objects.get(scenario_id="sc_01")
    assert scenario.system_description == target
    assert scenario.origin == Origin.GENERATED


@pytest.mark.django_db
def test_a_transport_failure_fails_the_run_but_archives_the_call(
    _client, experiment, taxonomy, template
):
    _client.fail_next("rate limited", http_status=429)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert "rate limited" in run.error
    call = run.calls.get()
    assert call.ok is False
    assert call.http_status == 429
    assert call.request_json["model"] == "m2"
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0


@pytest.mark.django_db
def test_malformed_json_is_recorded_as_a_parse_error(_client, experiment, taxonomy, template):
    _client.queue("this is not json")
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    call = run.calls.get()
    assert call.parsed_ok is False
    assert call.parse_error
    assert call.raw_response == "this is not json"
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0
    assert Scenario.objects.filter(origin=Origin.GENERATED).count() == 0


@pytest.mark.django_db
def test_a_schema_violation_is_recorded_not_raised(_client, experiment, taxonomy, template):
    _client.queue(json.dumps({"system_descriptions": [{"body": "no title"}]}))
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert run.calls.get().parsed_ok is False
    assert "title" in run.calls.get().parse_error
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0
    assert Scenario.objects.filter(origin=Origin.GENERATED).count() == 0


@pytest.mark.django_db
def test_a_json_array_response_is_recorded_as_a_parse_error(
    _client, experiment, taxonomy, template
):
    """Valid JSON, but not an object — parse_phase2 rejects it as a shape error,
    not a crash."""
    _client.queue("[]")
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    call = run.calls.get()
    assert call.parsed_ok is False
    assert call.parse_error
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0


@pytest.mark.django_db
def test_generate_scenarios_false_persists_systems_with_no_scenarios(
    _client, experiment, taxonomy, template
):
    experiment.generate_scenarios = False
    experiment.save()

    payload = json.dumps(
        {"system_descriptions": [{"title": "Riverbend", "body": "A plant.", "scenarios": []}]}
    )
    _client.queue(payload)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.COMPLETED
    system = SystemDescription.objects.get(title="Riverbend")
    assert system.origin == Origin.GENERATED
    assert Scenario.objects.filter(system_description=system).count() == 0


@pytest.mark.django_db
def test_systems_in_the_response_fail_the_run_when_generate_systems_is_false(
    _client, experiment, taxonomy, template
):
    """The model returning system_descriptions anyway, despite the experiment
    saying not to, is a disagreement between the prompt and the configuration —
    not something to silently honour."""
    target = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    _client.queue(PAYLOAD)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert "generate_systems" in run.error
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0


@pytest.mark.django_db
def test_a_bare_scenarios_list_with_no_target_fails_the_run(
    _client, experiment, taxonomy, template
):
    """Generated scenarios with nowhere to attach them must not vanish silently."""
    _client.queue(json.dumps({"scenarios": [CARD]}))
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert "target" in run.error
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 0


@pytest.mark.django_db
def test_a_bare_scenarios_list_fails_even_for_an_ad_hoc_run(_client, taxonomy, template):
    """Same rule for a PhaseRun with no pipeline_run (no Experiment behind it),
    where there is never a target_system to fall back on."""
    phase_run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        temperature=1.0,
        prompt_template=template,
        taxonomy=taxonomy,
    )
    _client.queue(json.dumps({"scenarios": [CARD]}))
    run = run_phase2(phase_run)

    assert run.status == PhaseRun.Status.FAILED
    assert "target" in run.error
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("body", ["{}", json.dumps({"system_descriptions": []})])
def test_a_vacuous_response_fails_the_run(_client, experiment, taxonomy, template, body):
    _client.queue(body)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert "no material" in run.error
    call = run.calls.get()
    # The empty body is still archived as evidence, even though it produced
    # nothing to persist.
    assert call.parsed_ok is True
    assert call.response_json is not None


@pytest.mark.django_db(transaction=True)
def test_a_duplicate_scenario_id_within_a_system_fails_the_run_not_raises(
    _client, experiment, taxonomy, template
):
    """A duplicated scenario_id in one system's scenarios list is routine
    malformed LLM output, not an exotic input — it must not escape as an
    uncaught IntegrityError and leave the run stuck non-terminal."""
    duplicate_payload = json.dumps(
        {
            "system_descriptions": [
                {"title": "Riverbend", "body": "A plant.", "scenarios": [CARD, dict(CARD)]}
            ]
        }
    )
    _client.queue(duplicate_payload)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert run.error
    assert run.finished_at is not None
    # The partial write (the system, plus whichever card landed first) must be
    # rolled back — no half-persisted material.
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 0


@pytest.mark.django_db(transaction=True)
def test_a_duplicate_scenario_id_in_the_bare_scenarios_list_fails_the_run_not_raises(
    _client, experiment, taxonomy, template
):
    target = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    duplicate_payload = json.dumps({"scenarios": [CARD, dict(CARD)]})
    _client.queue(duplicate_payload)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert run.error
    assert run.finished_at is not None
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 0


@pytest.mark.django_db
def test_the_output_schema_is_pinned_into_the_request(_client, experiment, taxonomy, template):
    _client.queue(PAYLOAD)
    run_phase2(make_run(experiment, taxonomy, template))
    assert _client.calls[0]["json_schema"]["properties"]["system_descriptions"]


@pytest.mark.django_db
def test_two_runs_against_one_target_do_not_collide(_client, experiment, taxonomy, template):
    target = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    for _ in range(2):
        _client.queue(json.dumps({"scenarios": [CARD]}))
        assert (
            run_phase2(make_run(experiment, taxonomy, template)).status == PhaseRun.Status.COMPLETED
        )

    assert Scenario.objects.filter(scenario_id="sc_01").count() == 2


@pytest.mark.django_db
def test_zero_scenarios_fails_the_run_when_scenarios_were_asked_for(
    _client, experiment, taxonomy, template
):
    """I1: `generate_scenarios` is True and the experiment asked for scenarios,
    but the response carried systems with empty scenario lists. That used to end
    the run COMPLETED with nothing to label — a repetition reported as a success
    that contributes zero labels to the measurement."""
    _client.queue(
        json.dumps({"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": []}]})
    )
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert "generate_scenarios" in run.error
    assert Scenario.objects.filter(origin=Origin.GENERATED).count() == 0
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0


@pytest.mark.django_db
def test_two_runs_generating_the_same_title_get_distinct_slugs(
    _client, experiment, taxonomy, template
):
    """I4: `SystemDescription._unique_slug` is SELECT-then-INSERT with no lock,
    so concurrent repetitions generating the same title race and one loses the
    whole repetition to an IntegrityError. Phase 2 sidesteps the race entirely
    by naming the slug itself, scoped to the run that produced the row."""
    slugs = []
    for _ in range(2):
        _client.queue(PAYLOAD)
        run = run_phase2(make_run(experiment, taxonomy, template))
        assert run.status == PhaseRun.Status.COMPLETED
        slugs.append(SystemDescription.objects.get(generation_run=run).slug)

    assert len(set(slugs)) == 2
    assert all(slug.startswith("riverbend-") for slug in slugs)


@pytest.mark.django_db
def test_a_phase_two_run_with_a_blank_variant_is_refused(_client, taxonomy, template):
    """M4: a blank variant made `_build_context` inject the taxonomy while
    declaring variant A, so the run died on a confusing (if fail-closed)
    TaxonomyLeak about a variant nobody had chosen."""
    phase_run = PhaseRun.objects.create(
        phase=2, variant="", model="m2", prompt_template=template, taxonomy=taxonomy
    )
    run = run_phase2(phase_run)

    assert run.status == PhaseRun.Status.FAILED
    assert "variant" in run.error
    assert run.finished_at is not None
    assert _client.calls == []


@pytest.mark.django_db
@pytest.mark.parametrize("status", [PhaseRun.Status.COMPLETED, PhaseRun.Status.FAILED])
def test_run_phase2_does_not_re_execute_a_finished_run(
    _client, experiment, taxonomy, template, status
):
    """M1: re-running a terminal phase run would duplicate every system and
    scenario it already produced and archive a second call against it."""
    phase_run = make_run(experiment, taxonomy, template)
    phase_run.status = status
    phase_run.save(update_fields=["status"])

    # Nothing queued: a second execution would make a real call and raise.
    again = run_phase2(phase_run)

    assert again.status == status
    assert _client.calls == []
    assert again.calls.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "error",
    [MissingAPIKey("No OpenRouter API key is configured."), RuntimeError("something arbitrary")],
)
def test_anything_the_client_raises_fails_the_run_rather_than_escaping(
    experiment, taxonomy, template, error
):
    """C2b: the `get_llm_client().complete(...)` call was unguarded, so
    MissingAPIKey (and SecretLeak, which `OpenRouterClient` raises deliberately)
    escaped `run_phase2` entirely and left the phase run stuck at RUNNING."""

    class Exploding:
        def complete(self, **kwargs):
            raise error

    set_llm_client(Exploding())
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert str(error) in run.error
    assert run.finished_at is not None
