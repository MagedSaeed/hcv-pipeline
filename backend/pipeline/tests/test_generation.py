import json

import pytest

from core.keys import MissingAPIKey
from corpus.models import (
    FewShotExample,
    Origin,
    ResourceFile,
    ResourceKind,
    Scenario,
    SystemDescription,
)
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.generation import run_phase2
from pipeline.models import Experiment, PhaseRun
from prompts.models import PromptTemplate
from prompts.rendering import format_hcv_table
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

    The `PipelineRun` this creates carries a real `config` snapshot, built
    the same way `launch()` builds one — `run_phase2` now reads `seed_systems`
    /`fewshots`/`n_systems`/`target_system`/etc from THAT pinned snapshot,
    never live off `experiment` (see `pipeline.execution_config`), so a
    `PipelineRun` created without one would silently execute against empty
    defaults regardless of what `experiment` actually says. Snapshotted at
    the moment this helper is called — every test that edits `experiment`
    does so BEFORE calling this, never between this and `run_phase2`, so
    that is the same moment the original live-read path would have read it.
    """
    from pipeline.execution_config import config_snapshot
    from pipeline.models import PipelineRun

    highest = (
        PipelineRun.objects.filter(experiment=experiment)
        .order_by("-repetition_index")
        .values_list("repetition_index", flat=True)
        .first()
    ) or 0
    pipeline_run = PipelineRun.objects.create(
        experiment=experiment,
        repetition_index=highest + 1,
        config=config_snapshot(experiment),
    )
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


STRAY_CARD = {**CARD, "scenario_id": "sc_stray"}


@pytest.mark.django_db
def test_a_redundant_top_level_scenarios_list_does_not_discard_a_valid_generation(
    _client, experiment, taxonomy, template
):
    """Defect 1: a live run against a real provider returned a well-formed
    `system_descriptions` (with its own nested scenarios) AND a redundant
    top-level `scenarios` list the schema never should have offered a place
    for. The old behaviour discarded the ENTIRE response — a complete,
    paid-for generation — because a bare scenarios list with no target_system
    was (rightly, in every OTHER case) treated as material with nowhere to
    go. The nested material is authoritative and must survive; the stray
    list must not silently vanish either — it lands on the call, not in the
    run's outcome.
    """
    payload = json.dumps(
        {
            "system_descriptions": [
                {"title": "Riverbend", "body": "A plant.", "scenarios": [CARD]}
            ],
            "scenarios": [STRAY_CARD],
        }
    )
    _client.queue(payload)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.COMPLETED
    system = SystemDescription.objects.get(title="Riverbend")
    assert Scenario.objects.filter(system_description=system, scenario_id="sc_01").exists()
    # The stray top-level scenario is never persisted — there is no target to
    # attach it to, and it duplicates nothing the nested material doesn't
    # already carry.
    assert not Scenario.objects.filter(scenario_id="sc_stray").exists()

    call = run.calls.get()
    assert call.stray_scenarios == [STRAY_CARD]
    # The redundant list is a research signal about the RESPONSE SHAPE, not a
    # parse failure — parsed_ok/parse_error must stay exactly what they would
    # have been for a clean response with no stray list at all.
    assert call.parsed_ok is True
    assert call.parse_error == ""


@pytest.mark.django_db
def test_a_redundant_scenarios_list_with_no_system_descriptions_still_fails(
    _client, experiment, taxonomy, template
):
    """The safety net is narrow: if system_descriptions is EMPTY, a bare
    scenarios list with no target is still the genuine "nowhere to go"
    error — there is no authoritative nested material to fall back on."""
    payload = json.dumps({"system_descriptions": [], "scenarios": [CARD]})
    _client.queue(payload)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert "target" in run.error
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 0


@pytest.mark.django_db
def test_a_redundant_scenarios_list_is_still_an_error_when_generate_systems_is_false(
    _client, experiment, taxonomy, template
):
    """When generate_systems is False, the response's bare `scenarios` field
    IS the field the run actually asked for — a `system_descriptions` list
    alongside it (however populated) does not make an otherwise-legitimate
    target-mode response "redundant"; `generate_systems is False` fails it on
    its own, unaffected by the new safety net."""
    target = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    payload = json.dumps(
        {
            "system_descriptions": [
                {"title": "Riverbend", "body": "A plant.", "scenarios": [CARD]}
            ],
            "scenarios": [STRAY_CARD],
        }
    )
    _client.queue(payload)
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert "generate_systems" in run.error
    assert SystemDescription.objects.filter(origin=Origin.GENERATED).count() == 0


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
def test_the_pinned_schema_is_mode_specific(_client, experiment, taxonomy, template):
    """Defect 1's root-cause fix: the schema shown to the model must not
    offer the field the run's mode does not use, so a well-behaved model
    cannot fill in both. `experiment` here defaults to generate_systems=True
    with no target_system — the schema must not carry a top-level
    `scenarios` field at all in that mode."""
    _client.queue(PAYLOAD)
    run_phase2(make_run(experiment, taxonomy, template))
    schema = _client.calls[0]["json_schema"]
    assert "system_descriptions" in schema["properties"]
    assert "scenarios" not in schema["properties"]


@pytest.mark.django_db
def test_the_pinned_schema_omits_system_descriptions_in_target_mode(
    _client, experiment, taxonomy, template
):
    target = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    _client.queue(json.dumps({"scenarios": [CARD]}))
    run_phase2(make_run(experiment, taxonomy, template))
    schema = _client.calls[0]["json_schema"]
    assert "scenarios" in schema["properties"]
    assert "system_descriptions" not in schema["properties"]


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
def test_run_phase2_does_not_resurrect_a_cancelled_run(_client, experiment, taxonomy, template):
    """`PhaseRun.is_terminal` now treats CANCELLED as terminal too (task 5
    review round), precisely so `run_phase2` hard-stops here: an operator's
    `POST .../cancel/` on a run a worker is about to pick up must not be
    resurrected into a real, brand-new provider call the moment that worker
    gets to it — nothing is queued on `_client`, so if this actually tried
    to call the provider it would raise "no queued response left" instead
    of quietly returning.
    """
    phase_run = make_run(experiment, taxonomy, template)
    phase_run.status = PhaseRun.Status.CANCELLED
    phase_run.error = "Cancelled by operator."
    phase_run.save(update_fields=["status", "error"])

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.CANCELLED
    assert result.error == "Cancelled by operator."
    assert _client.calls == []
    assert result.calls.count() == 0


@pytest.mark.django_db
def test_a_fewshot_referencing_a_taxonomy_resource_still_leaks_via_the_snapshot_path(
    _client, experiment, taxonomy, template
):
    """The isolation guarantee, re-verified through the NEW snapshot-reading
    path (task 5 review round, finding 1): `config_snapshot` captures
    `input_resource_kind`/`output_resource_kind` specifically so
    `prompts.rendering._fewshot_rows`' variant-A taxonomy-resource guard
    still fires when a run executes against its PINNED config instead of
    the live experiment — losing that would silently widen Run A's
    taxonomy exposure the moment a few-shot referencing the taxonomy was
    used through `run_phase2` rather than `build_phase2_context` directly.
    Mirrors `prompts.tests.test_isolation
    .test_no_hcv_survives_via_a_fewshot_file_pointing_at_a_taxonomy_resource`,
    end to end through `run_phase2` this time, via `make_run` (which now
    snapshots the experiment the same way `launch()` does).
    """
    resource = ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    FewShotExample.objects.create(phase=2, name="leaky", input_file=resource, output_text="ok")
    experiment.fewshots.add(FewShotExample.objects.get(name="leaky"))

    phase_run = make_run(experiment, taxonomy, template, variant="A")
    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.FAILED
    assert "leaky" in result.error
    assert "TaxonomyLeak" in result.error
    # Refused before any provider call was ever made.
    assert _client.calls == []


@pytest.mark.django_db
def test_a_snapshot_missing_a_guard_relevant_field_fails_the_run_not_renders(
    _client, experiment, taxonomy, template
):
    """THE reviewer's exact probe, task 5 second review round: a few-shot
    dict lacking `input_resource_kind` — the precise shape a bug in
    `config_snapshot` produced one round earlier — used to be read with a
    permissive `.get("input_resource_kind")` default, silently switching
    OFF `prompts.rendering._fewshot_rows`' variant-A taxonomy-resource
    guard. A TAXONOMY-kind resource's text then rendered into a Run A
    prompt and the run completed successfully: a taxonomy leak into the
    unbiased variant, invisible in the results — the precise failure the
    isolation guarantee's layers of guards exist to prevent.

    `execution_config.validate_snapshot` now fails CLOSED instead: every
    field a guard depends on is required, so this malformed snapshot —
    missing exactly the field the exploit needed — must fail the run
    outright, before any provider call, rather than quietly rendering with
    the guard disabled.
    """
    resource = ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    FewShotExample.objects.create(phase=2, name="leaky", input_file=resource, output_text="ok")
    experiment.fewshots.add(FewShotExample.objects.get(name="leaky"))

    phase_run = make_run(experiment, taxonomy, template, variant="A")
    # Corrupt the pinned snapshot into the exact malformed shape the exploit
    # needed: the few-shot dict is present, but missing `input_resource_kind`.
    pipeline_run = phase_run.pipeline_run
    del pipeline_run.config["fewshots"][0]["input_resource_kind"]
    pipeline_run.save(update_fields=["config"])

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.FAILED
    assert "MalformedSnapshot" in result.error
    # The leak this exact defect used to allow: refused before any provider
    # call was made, so a taxonomy-kind resource's text never reached a
    # rendered prompt at all, let alone the model.
    assert _client.calls == []
    assert result.calls.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("lying_value", ["", "prose", "TAXONOMY", 0, False])
def test_a_snapshot_with_a_lying_resource_kind_fails_the_run_not_renders(
    _client, experiment, taxonomy, template, lying_value
):
    """Round 6 review, finding 1 (the blocker): the reviewer re-ran the same
    exploit as the test above, but with `input_resource_kind` PRESENT — not
    missing — and LYING: `""`, `"prose"`, wrong-case `"TAXONOMY"`, `0`, and
    `False` all rendered a TAXONOMY-kind resource's text into a Run A prompt
    and the run COMPLETED, because shape-only validation (the previous
    round's fix) only checks the key exists, never that its value is
    honest — every one of these reads as "no file backing" to
    `_fewshot_rows`' variant-A guard, same as the missing-key case did.

    `validate_snapshot` now checks `input_resource_kind` against the real
    `ResourceKind` choices, so all five of these must fail the run outright,
    before any provider call — the same proof as the missing-key test above,
    for a present-but-dishonest value instead of an absent one. (A bare
    `None` is deliberately NOT one of these values — see
    `test_execution_config.py::test_a_null_resource_kind_remains_legitimately_valid`
    for why that one is closed a different way.)
    """
    resource = ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    FewShotExample.objects.create(phase=2, name="leaky", input_file=resource, output_text="ok")
    experiment.fewshots.add(FewShotExample.objects.get(name="leaky"))

    phase_run = make_run(experiment, taxonomy, template, variant="A")
    pipeline_run = phase_run.pipeline_run
    pipeline_run.config["fewshots"][0]["input_resource_kind"] = lying_value
    pipeline_run.save(update_fields=["config"])

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.FAILED
    assert "MalformedSnapshot" in result.error
    # The leak this exact defect used to allow: refused before any provider
    # call was made, so a taxonomy-kind resource's text never reached a
    # rendered prompt at all, let alone the model.
    assert _client.calls == []
    assert result.calls.count() == 0


@pytest.mark.django_db
def test_a_file_backed_fewshot_with_its_kind_nulled_fails_the_run_not_renders(
    _client, experiment, taxonomy, template
):
    """Coordinator follow-up after the value-validation round: closing the
    lying-`None` case by making `PipelineRunAdmin.config` read-only alone
    rests on trusting `config_snapshot` to always keep `input_file_id` and
    `input_resource_kind` in lock-step — and a writer that drops or nulls
    exactly the kind while the file id survives is precisely the bug that
    caused the ORIGINAL Run A leak, one review round earlier. This plan has
    now been bitten by "trust the other side of this boundary" twice.

    Proof, held to the same bar as the earlier exploit probes: a snapshot
    with `input_file_id` PRESENT (a real TAXONOMY-kind resource) but
    `input_resource_kind` nulled out, on variant A. The pairing check
    (`_require_file_kind_pairing`) must refuse this before any provider
    call — FAILED, `MalformedSnapshot`, and ZERO archived `LLMCall` rows,
    exactly the bar the missing-key and lying-value probes were both held
    to above.
    """
    resource = ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    FewShotExample.objects.create(phase=2, name="leaky", input_file=resource, output_text="ok")
    experiment.fewshots.add(FewShotExample.objects.get(name="leaky"))

    phase_run = make_run(experiment, taxonomy, template, variant="A")
    pipeline_run = phase_run.pipeline_run
    # Confirm the snapshot really does carry the file id before corrupting
    # only the kind — otherwise this would not be testing the pairing check
    # at all, just a bare missing/lying kind again.
    assert pipeline_run.config["fewshots"][0]["input_file_id"] == resource.pk
    pipeline_run.config["fewshots"][0]["input_resource_kind"] = None
    pipeline_run.save(update_fields=["config"])

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.FAILED
    assert "MalformedSnapshot" in result.error
    assert _client.calls == []
    assert result.calls.count() == 0


@pytest.mark.django_db
def test_a_bare_empty_config_refuses_to_execute_rather_than_using_adhoc_defaults(
    _client, experiment, taxonomy, template
):
    """A `PipelineRun.config` of `{}` — the field's bare default, meaning
    "never actually snapshotted" (a pre-migration row, or any path that
    bypasses `launch()`) — used to fall through every permissive `.get(key,
    default)` in the old reader and execute as if it were a legitimate
    ad-hoc (iterate-mode) run: `n_systems=3`, no seeds, no few-shots,
    silently substituting a DIFFERENT configuration than the one the
    experiment actually pins. An orchestrated run (one WITH a
    `pipeline_run`) whose config does not validate must refuse to execute
    at all, not guess. Round 6 review, finding 4: the failure message must
    also say this run PREDATES the snapshot feature and needs re-launching,
    not just "does not match the shape" — this is the single most likely
    malformed config in practice, an ordinary pre-migration row, not
    corruption.
    """
    phase_run = make_run(experiment, taxonomy, template)
    phase_run.pipeline_run.config = {}
    phase_run.pipeline_run.save(update_fields=["config"])

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.FAILED
    assert "MalformedSnapshot" in result.error
    assert "predates the config-snapshot feature" in result.error
    assert _client.calls == []


@pytest.mark.django_db
def test_a_deleted_target_system_fails_cleanly_before_any_provider_call(
    _client, experiment, taxonomy, template
):
    """Finding 6, task 5 second review round: a `target_system` deleted
    between launch and execution used to fail only AFTER a real provider
    call was made and paid for (inside `_persist`, once the response with
    bare scenarios came back), with an opaque `DoesNotExist:
    SystemDescription matching query does not exist.` `resolve_generation_config`
    now checks existence up front, before any call, and names what is
    actually missing.
    """
    target = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    phase_run = make_run(experiment, taxonomy, template)
    target_id = target.pk
    # Simulate the target being removed from the experiment and then
    # deleted, after this run's snapshot already pinned it.
    experiment.target_system = None
    experiment.save()
    target.delete()

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.FAILED
    assert "ReferencedRowMissing" in result.error
    assert "Oldsmar" in result.error
    assert str(target_id) in result.error
    assert _client.calls == []


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
