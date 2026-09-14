"""The final choke point: `pipeline.generation._preflight`, wired into
`llm.client`'s `preflight` parameter via `prompts.rendering
.assert_request_not_leaking_taxonomy` — every variant-A phase-2 request
passes through it immediately before the HTTP call.

The three pre-existing isolation layers (`build_phase2_context` withholding
the taxonomy for Run A, the resource/few-shot guards refusing TAXONOMY-kind
files, and `render()`'s own backstop scanning the rendered prompt strings)
all operate on the TEMPLATE and the two rendered prompt strings. None of them
can see a field a later step adds directly to the request dict — exactly the
shape of two real bugs review caught rather than any automated check: an
`output_schema` copied into the request body past a scan that only looked at
prompt text, and a config-snapshot reader that silently disarmed a guard
(`execution_config.validate_snapshot`'s own module docstring). This module's
first test constructs that exact shape — a leak injected into the request
AFTER rendering, somewhere the render-time scan does not look — and proves
the render-time scan misses it while the choke point catches it, with zero
provider calls and zero archived `LLMCall` rows. That contrast is the whole
point of having a second, independent checkpoint.
"""

import json

import pytest

from corpus.models import Origin, Scenario, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.execution_config import config_snapshot
from pipeline.generation import run_phase2
from pipeline.labelling import label_scenario
from pipeline.models import Experiment, LLMCall, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from prompts.rendering import build_phase2_context, render
from taxonomy.models import HCV, Taxonomy

CARD = {
    "scenario_id": "sc_01",
    "actor": "EP1 (ExternalParty)",
    "trigger": "After 22:00.",
    "standing_state": "One shared password.",
    "asset": "The SCADA Service",
    "threat": "Spoofing.",
    "outcome": "The feed ran at 11,100 ppm.",
    "narrative_steps": ["EP1 opens the session."],
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
def empty_taxonomy(db):
    """A real, saved Taxonomy with zero HCV rows — a broken import, not a
    missing one. `assert_no_taxonomy_leak` cannot scan against it (it returns
    False rather than raising); the choke point treats that as a refusal,
    unlike the render-time backstop, which only warns (see
    prompts/tests/test_isolation.py's R2 for that leniency, and
    prompts/tests/test_rendering.py's request-scan tests for this stricter
    behaviour in isolation)."""
    return Taxonomy.objects.create(vocabulary_id="HCV-EMPTY-CP", version="1.0")


@pytest.fixture
def phase2_template(db):
    return PromptTemplate.objects.create_version(
        phase=2,
        name="gen",
        system_prompt="You write scenarios.",
        user_prompt="Generate scenarios.",
    )


@pytest.fixture
def phase3_template(db):
    # Deliberately interpolates the taxonomy's own hcv_id, unlike
    # pipeline/tests/test_labelling.py's otherwise-identical fixture (which
    # only counts `taxonomy|length`): a real labelling template MUST expose
    # the actual identifiers for the model to choose from, and this test
    # needs the rendered prompt (and therefore the wire request) to
    # genuinely CARRY "HCV-07" — not just a count — so that "phase 3 is
    # unaffected" is a claim about material that would trip a scan if one
    # were ever mistakenly wired in, not a claim that happens to hold only
    # because nothing taxonomy-shaped ever reached the request body.
    return PromptTemplate.objects.create_version(
        phase=3,
        name="lab",
        system_prompt="You label scenarios.",
        user_prompt=(
            "Label {{ scenario.scenario_id }} from: "
            "{% for h in taxonomy %}{{ h.hcv_id }} {% endfor %}"
        ),
    )


@pytest.fixture
def experiment(db, taxonomy, phase2_template, phase3_template):
    return Experiment.objects.create(
        name="e",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt_a=phase2_template,
        phase2_prompt_b=phase2_template,
        phase3_model="m3",
        phase3_prompt=phase3_template,
        n_systems=1,
        n_scenarios_per_system=1,
    )


def make_run(experiment, taxonomy, template, variant="A"):
    """A phase-2 run wired to its experiment's PINNED config snapshot — see
    pipeline/tests/test_generation.py's identical helper. `resolve_generation
    _config` reads the pinned `PipelineRun.config`, never `Experiment`'s live
    fields, so an ad-hoc (no `pipeline_run`) PhaseRun would silently execute
    against empty defaults regardless of what `experiment` says.

    `taxonomy` is set directly on the returned `PhaseRun`, independent of
    `experiment.taxonomy` — `config_snapshot` never touches the taxonomy at
    all (it is pinned straight onto `PhaseRun`, not part of the snapshot), so
    a test can hand this helper a taxonomy that differs from the
    experiment's own, e.g. to exercise a run whose guard taxonomy cannot be
    scanned against without also breaking `Experiment.clean()`'s unrelated
    "taxonomy must have rows" validation (which `.objects.create()` never
    runs anyway, but there is no reason to rely on that).
    """
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
def test_the_render_time_scan_misses_a_post_render_injection_but_the_choke_point_catches_it(
    monkeypatch, _client, experiment, taxonomy, phase2_template
):
    """THE test this feature exists for.

    render()'s backstop only ever sees the two rendered prompt strings and
    the template's own `output_schema` — it has no way to see a field added
    to the request AFTER rendering. This simulates exactly that: a
    (hypothetical, future) `build_request` bug that appends a taxonomy
    identifier into `response_format` regardless of what the template said,
    well after `render()` has already run and returned clean.

    Both halves of the contrast are asserted explicitly:
    1. the render-time scan, run directly against this exact template and
       context, finds nothing — it genuinely misses the leak, because the
       leak does not exist yet at the point it runs;
    2. the full pipeline run is still blocked — FAILED, with a TaxonomyLeak
       reason, ZERO provider calls (`_client.calls` stays empty) and ZERO
       archived `LLMCall` rows — because the choke point scans the actual
       wire body, assembled after rendering, not the prompts.
    """
    # Half one: the render-time backstop, run directly, passes clean. Nothing
    # has been injected yet, and the template's own prompts never mention the
    # taxonomy at all.
    context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        guard_taxonomy=taxonomy,
    )
    rendered = render(phase2_template, context)
    assert rendered.leak_scan_ran is True
    assert "HCV-07" not in rendered.system
    assert "HCV-07" not in rendered.user

    # Half two: simulate the leak arriving AFTER rendering, in a request
    # field the render-time scan never inspects. Patching `llm.fake
    # .build_request` (the fake's own module-level name, exactly what
    # FakeLLMClient.complete() calls) reaches every phase-2 call made
    # through the fake client for the rest of this test — nothing about the
    # template or the render path changes; only what the CLIENT builds does.
    import llm.fake as fake_module
    from llm.client import build_request as real_build_request

    def leaky_build_request(*args, **kwargs):
        request = real_build_request(*args, **kwargs)
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": {"enum": ["HCV-07"]}},
        }
        return request

    monkeypatch.setattr(fake_module, "build_request", leaky_build_request)

    run = run_phase2(make_run(experiment, taxonomy, phase2_template, variant="A"))

    assert run.status == PhaseRun.Status.FAILED
    assert "Taxonomy check:" in run.error
    assert "HCV-07" in run.error
    assert run.calls.count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_a_legitimate_variant_a_call_still_succeeds_through_the_choke_point(
    _client, experiment, taxonomy, phase2_template
):
    """The choke point must not be a tax on the ordinary case: a clean
    variant-A request is scanned and sails through, exactly once."""
    _client.queue(PAYLOAD)
    run = run_phase2(make_run(experiment, taxonomy, phase2_template, variant="A"))

    assert run.status == PhaseRun.Status.COMPLETED
    call = run.calls.get()
    assert call.leak_scan_ran is True
    assert call.guarded_by == taxonomy
    assert len(_client.calls) == 1


@pytest.mark.django_db
def test_variant_b_is_unaffected_by_the_choke_point(_client, experiment, taxonomy):
    """Variant B legitimately carries the taxonomy — the choke point is
    attached to every phase-2 call, both variants, but must recognise B and
    skip scanning it rather than raising on material that belongs there."""
    guided_template = PromptTemplate.objects.create_version(
        phase=2, name="guided", user_prompt="{{ hcv_table() }}"
    )
    _client.queue(PAYLOAD)
    run = run_phase2(make_run(experiment, taxonomy, guided_template, variant="B"))

    assert run.status == PhaseRun.Status.COMPLETED
    call = run.calls.get()
    assert "HCV-07" in call.rendered_user_prompt
    assert call.leak_scan_ran is False
    assert len(_client.calls) == 1


@pytest.mark.django_db
def test_phase_3_is_unaffected_by_the_choke_point_for_either_variant(
    _client, taxonomy, phase3_template
):
    """Phase 3 legitimately receives the taxonomy for BOTH variants — it is
    the labeller (`build_phase3_context` always includes it) — and
    `pipeline.labelling.label_scenario` never attaches a `preflight` at all.
    A labelling `PhaseRun`'s `variant` field only records which phase-2
    variant it labels; it must not, by itself, ever trigger a scan."""
    system = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    scenario = Scenario.objects.create(
        system_description=system,
        scenario_id="sc_01",
        narrative="1. x",
        origin=Origin.SEED,
        order=1,
    )
    label_payload = json.dumps(
        {"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]}
    )

    for variant in ("A", "B"):
        run = PhaseRun.objects.create(
            phase=3,
            variant=variant,
            model="m3",
            prompt_template=phase3_template,
            taxonomy=taxonomy,
        )
        _client.queue(label_payload)
        call = label_scenario(run, scenario)

        assert call.parsed_ok is True
        # The taxonomy's own identifier genuinely reached this prompt (and
        # therefore the wire request) — for both variants, as it legitimately
        # must for phase 3 — not merely a count that would never trip a scan.
        assert "HCV-07" in call.rendered_user_prompt
        assert call.leak_scan_ran is False

    assert len(_client.calls) == 2


@pytest.mark.django_db
def test_the_choke_point_refuses_a_variant_a_call_when_the_taxonomy_cannot_be_resolved(
    _client, experiment, empty_taxonomy, phase2_template
):
    """Fail closed, proven end to end: a guard taxonomy with zero HCV rows
    cannot be scanned against. Contrast with the render-time backstop's own
    leniency in the identical situation (test_isolation.py's R2,
    `test_an_empty_guard_taxonomy_warns_and_still_renders`) — render() only
    WARNS and still lets the prompt through, because a broken import is not
    itself a leak and there is another layer behind it. This IS that other
    layer, and it is the last one before a real provider call, so the run is
    refused outright here — FAILED, zero calls, zero archived rows — rather
    than merely warned about."""
    run = run_phase2(make_run(experiment, empty_taxonomy, phase2_template, variant="A"))

    assert run.status == PhaseRun.Status.FAILED
    assert "Taxonomy check:" in run.error
    assert run.calls.count() == 0
    assert _client.calls == []


# --- Dict KEYS are request text: the reviewer's reproduction, end to end ----------
#
# A variant-A template whose output_schema names HCV ids ONLY as property keys —
# one boolean per HCV, exactly what a labelling schema looks like. Both scanners
# walked with `prompts.rendering._iter_request_text`, which used to yield dict
# values only, so this rendered clean, a real run_phase2 COMPLETED, `HCV-07` went
# to the provider, and the call was archived with leak_scan_ran=True.

KEY_ONLY_SCHEMA = {"type": "object", "properties": {"HCV-07": {"type": "boolean"}}}


@pytest.mark.django_db
def test_a_variant_a_schema_naming_hcv_ids_only_as_keys_fails_with_zero_calls(
    _client, experiment, taxonomy
):
    """A response IS queued, so a scan that misses the key lets the run
    COMPLETE and archive the call — the failure the reviewer reproduced —
    rather than failing for the unrelated reason of an empty fake queue."""
    key_only = PromptTemplate.objects.create_version(
        phase=2, name="key-only", user_prompt="Generate scenarios.", output_schema=KEY_ONLY_SCHEMA
    )
    _client.queue(PAYLOAD)

    run = run_phase2(make_run(experiment, taxonomy, key_only, variant="A"))

    assert run.status == PhaseRun.Status.FAILED
    assert "Taxonomy check:" in run.error
    assert "HCV-07" in run.error
    assert _client.calls == []
    assert run.calls.count() == 0
    assert LLMCall.objects.count() == 0


@pytest.mark.django_db
def test_the_choke_point_alone_catches_a_key_only_id_injected_after_rendering(
    monkeypatch, _client, experiment, taxonomy, phase2_template
):
    """The same key-only shape, but added to the request AFTER `render()` —
    so the render-time scan cannot see it and only the choke point stands
    between it and the provider. The choke point had been key-blind since it
    was built, before `render()` ever was."""
    import llm.fake as fake_module
    from llm.client import build_request as real_build_request

    def leaky_build_request(*args, **kwargs):
        request = real_build_request(*args, **kwargs)
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": KEY_ONLY_SCHEMA},
        }
        return request

    monkeypatch.setattr(fake_module, "build_request", leaky_build_request)
    _client.queue(PAYLOAD)

    run = run_phase2(make_run(experiment, taxonomy, phase2_template, variant="A"))

    assert run.status == PhaseRun.Status.FAILED
    assert "Taxonomy check:" in run.error
    assert "HCV-07" in run.error
    assert _client.calls == []
    assert LLMCall.objects.count() == 0


@pytest.mark.django_db
def test_variant_b_with_hcv_ids_as_schema_keys_still_runs(_client, experiment, taxonomy):
    """The mirror image: under variant B the key-only schema is the intended
    design, and the run completes with one call."""
    key_only = PromptTemplate.objects.create_version(
        phase=2, name="key-only-b", user_prompt="Generate scenarios.", output_schema=KEY_ONLY_SCHEMA
    )
    _client.queue(PAYLOAD)

    run = run_phase2(make_run(experiment, taxonomy, key_only, variant="B"))

    assert run.status == PhaseRun.Status.COMPLETED
    assert len(_client.calls) == 1
    assert run.calls.get().leak_scan_ran is False
