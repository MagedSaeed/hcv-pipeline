import csv
import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import force_authenticate

from corpus.models import FewShotExample, Origin, Scenario, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import get_llm_client, set_llm_client
from pipeline.export import build_bundle, build_run_bundle, write_csv_zip
from pipeline.models import Experiment, LLMCall, PipelineRun
from pipeline.orchestration import execute_pipeline_run, launch
from pipeline.views import ExperimentViewSet
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()

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
GENERATED = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": [CARD]}]}
)
LABELLED = json.dumps({"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]})
SENTINEL = "sk-or-v1-EXPORTSENTINEL"


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def experiment(db, settings):
    settings.OPENROUTER_API_KEY = SENTINEL
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(
        taxonomy=tax,
        hcv_id="HCV-07",
        name="Malicious Insiders",
        definition="Authorized insiders misuse access.",
        order=7,
    )
    generation = PromptTemplate.objects.create_version(
        phase=2, name="g", system_prompt="sys", user_prompt="Write {{ n_systems }}."
    )
    labelling = PromptTemplate.objects.create_version(
        phase=3, name="l", user_prompt="{{ scenario.scenario_id }}"
    )
    seed = SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)
    made = Experiment.objects.create(
        name="baseline",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    made.variants = ["A"]
    made.save()
    made.seed_systems.add(seed)

    fake = FakeLLMClient()
    fake.queue(GENERATED)
    fake.queue(LABELLED)
    set_llm_client(fake)
    execute_pipeline_run(launch(made, n_repetitions=1)[0])
    return made


@pytest.mark.django_db
def test_the_bundle_has_every_section_the_spec_names(experiment):
    """No experiment-wide `inputs` section any more (task 5, step 2): with N
    repetitions potentially pinning N different configurations, no single
    section could represent "the" inputs — each repetition's own pinned
    snapshot lives in `run_configs` (keyed by `pipeline_run` id) instead,
    and `experiment_current` (renamed from `experiment`) is unmistakably
    the experiment's CURRENT, live configuration, never what a run actually
    used. See `test_a_run_export_carries_its_own_pinned_inputs` for where a
    singular `inputs` section does still appear — `build_run_bundle`, one
    repetition at a time, where it is unambiguous.
    """
    bundle = build_bundle(experiment)
    assert set(bundle) == {
        "experiment_current",
        "taxonomy",
        "prompts",
        "runs",
        "run_configs",
        "systems",
        "scenarios",
        "labels",
        "calls",
    }


@pytest.mark.django_db
def test_the_bundle_pins_exact_key_sets_per_section(experiment):
    """A field silently vanishing from the export is exactly the failure
    this guards against: an earlier review stripped 18 exported fields at
    once (`parse_error`, `unknown_hcv_ids`, `sequence`, `description`,
    `version`, `output_schema`, `condition_classes`, `evidence`,
    `rationale`, `narrative`, `origin`, `raw_response`, `cost`,
    `prompt_tokens`, `rendered_system_prompt`, `variant`, `status`,
    `guarded_by`) and only one existing test caught it. Pinning the full key
    set of a representative row of every section means removing OR renaming
    any exported field fails a test here, not just a lucky few.

    `systems`/`scenarios` share one shape by construction (`_system_row`/
    `_scenario_row`), so one `system_keys`/`scenario_keys` set below covers
    both — `build_run_bundle`'s `inputs` has its OWN, deliberately leaner
    shape (see `test_a_run_export_carries_its_own_pinned_inputs`), not this
    one.
    """
    bundle = build_bundle(experiment)

    assert set(bundle["experiment_current"]) == {
        "id",
        "name",
        "description",
        "phase2_model",
        "phase2_temperature",
        "phase3_model",
        "phase3_temperature",
        "generate_systems",
        "generate_scenarios",
        "n_systems",
        "n_scenarios_per_system",
        "variants",
        "created_by",
        "created_at",
    }

    assert len(bundle["taxonomy"]) == 1
    assert set(bundle["taxonomy"][0]) == {
        "id",
        "vocabulary_id",
        "version",
        "notes",
        "source_json",
        "created_at",
        "rows",
    }
    assert set(bundle["taxonomy"][0]["rows"][0]) == {
        "hcv_id",
        "name",
        "definition",
        "condition_classes",
        "order",
    }

    assert len(bundle["prompts"]) == 2
    for prompt in bundle["prompts"]:
        assert set(prompt) == {
            "id",
            "phase",
            "name",
            "version",
            "system_prompt",
            "user_prompt",
            "output_schema",
            "created_by",
            "created_at",
        }

    system_keys = {
        "id",
        "title",
        "slug",
        "body",
        "origin",
        "variant",
        "generation_run",
        "created_by",
        "created_at",
        "updated_at",
    }
    scenario_keys = {
        "id",
        "system_description",
        "scenario_id",
        "actor",
        "trigger",
        "standing_state",
        "asset",
        "threat",
        "outcome",
        "narrative",
        "origin",
        "variant",
        "intended_hcvs",
        "abox",
        "generation_run",
        "order",
        "created_by",
        "created_at",
        "updated_at",
    }

    assert len(bundle["runs"]) == 2
    assert set(bundle["runs"][0]) == {
        "pipeline_run",
        "repetition_index",
        "pipeline_run_status",
        "pipeline_run_started_at",
        "pipeline_run_finished_at",
        "phase_run",
        "phase",
        "variant",
        "model",
        "temperature",
        "prompt_template",
        "taxonomy",
        "source_phase_run",
        "status",
        "error",
        "total_children",
        "completed_children",
        "failed_children",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "created_by",
        "started_at",
        "finished_at",
    }

    assert set(bundle["systems"][0]) == system_keys
    assert set(bundle["scenarios"][0]) == scenario_keys

    assert set(bundle["labels"][0]) == {
        "id",
        "phase_run",
        "scenario",
        "scenario_id",
        "hcv_id",
        "hcv_name",
        "evidence",
        "rationale",
        "source",
        "llm_call",
        "created_by",
        "created_at",
    }

    assert set(bundle["calls"][0]) == {
        "id",
        "phase_run",
        "sequence",
        "scenario",
        "rendered_system_prompt",
        "rendered_user_prompt",
        "request_json",
        "response_json",
        "raw_response",
        "parsed_ok",
        "parse_error",
        "unknown_hcv_ids",
        "stray_scenarios",
        "http_status",
        "transport_error",
        "model",
        "finish_reason",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "latency_ms",
        "leak_scan_ran",
        "guarded_by",
        "created_at",
    }


@pytest.mark.django_db
def test_the_bundle_pins_the_configuration(experiment):
    bundle = build_bundle(experiment)
    assert bundle["experiment_current"]["name"] == "baseline"
    assert bundle["experiment_current"]["phase2_model"] == "m2"
    assert bundle["experiment_current"]["variants"] == ["A"]


@pytest.mark.django_db
def test_the_bundle_carries_the_taxonomy_verbatim(experiment):
    """`taxonomy` is a LIST — see
    `test_the_bundle_pins_prompts_and_taxonomy_from_what_actually_ran` below
    for why more than one can legitimately appear."""
    taxonomies = build_bundle(experiment)["taxonomy"]
    assert len(taxonomies) == 1
    rows = taxonomies[0]["rows"]
    assert rows[0]["hcv_id"] == "HCV-07"
    assert rows[0]["definition"] == "Authorized insiders misuse access."


@pytest.mark.django_db
def test_the_bundle_carries_the_prompts_at_the_version_used(experiment):
    prompts = build_bundle(experiment)["prompts"]
    assert {p["phase"] for p in prompts} == {2, 3}
    assert any(p["user_prompt"] == "Write {{ n_systems }}." for p in prompts)


@pytest.mark.django_db
def test_the_bundle_pins_prompts_and_taxonomy_from_what_actually_ran(experiment):
    """Editing an experiment's `phase2_prompt_a`/`taxonomy` AFTER it has
    already run must not change what the archive says was used.

    `experiment.phase2_prompt_a`/`.taxonomy` are plain, editable FKs —
    nothing freezes them once runs exist, and `PATCH {"phase2_prompt_a":
    <new>}` returns 200. The archive's job is to record what a run actually
    used, which is `PhaseRun.prompt_template_id`/`.taxonomy_id` — pinned
    once, at launch, and never rewritten. Reading the experiment's CURRENT
    FKs instead used to ship a bundle whose `prompts`/`taxonomy` disagreed
    with `runs` (whose `prompt_template`/`taxonomy` ids stayed correct
    throughout) and, worse, whose `taxonomy` didn't even contain the HCV
    ids `labels` cites.
    """
    original_prompt_id = experiment.phase2_prompt_a_id
    original_taxonomy_id = experiment.taxonomy_id

    other_prompt = PromptTemplate.objects.create_version(
        phase=2, name="other", user_prompt="unrelated, never used by any run"
    )
    other_taxonomy = Taxonomy.objects.create(vocabulary_id="HCV-99", version="2.0")
    HCV.objects.create(taxonomy=other_taxonomy, hcv_id="HCV-99", name="Unrelated", order=1)

    # The exact edit the reviewer probed via `PATCH` — done at the model
    # level here since the point under test is `build_bundle`, not the view.
    experiment.phase2_prompt_a = other_prompt
    experiment.taxonomy = other_taxonomy
    experiment.save()

    bundle = build_bundle(experiment)

    prompt_ids = {p["id"] for p in bundle["prompts"]}
    assert original_prompt_id in prompt_ids
    assert other_prompt.pk not in prompt_ids

    taxonomy_ids = {t["id"] for t in bundle["taxonomy"]}
    assert original_taxonomy_id in taxonomy_ids
    assert other_taxonomy.pk not in taxonomy_ids

    # The labels still resolve against the taxonomy actually shipped.
    hcv_ids = {row["hcv_id"] for t in bundle["taxonomy"] for row in t["rows"]}
    assert bundle["labels"][0]["hcv_id"] in hcv_ids
    assert "HCV-99" not in hcv_ids

    # And every run's own `prompt_template`/`taxonomy` id resolves against
    # what `prompts`/`taxonomy` actually ship — the whole point of pinning.
    for run in bundle["runs"]:
        assert run["prompt_template"] in prompt_ids
        assert run["taxonomy"] in taxonomy_ids


@pytest.mark.django_db
def test_the_bundle_resolves_every_run_after_being_repinned_between_two_real_launches(experiment):
    """The highest-value scenario in this task: an experiment that ACTUALLY
    ran under two different pinned configurations, not one that was merely
    edited and never re-run (that's the test above). `PATCH`ing
    `phase2_prompt_a`/`phase3_prompt`/`taxonomy` between two real launches is
    exactly the sequence that produced a confidently-wrong archive before
    `_prompts`/`_taxonomy` were driven off `PhaseRun`'s own pins instead of
    the experiment's current, editable FKs — every run, from EITHER
    repetition, must resolve against the bundle's own `prompts`/`taxonomy`
    sections, and every label's `hcv_id` must resolve against the bundle's
    own taxonomy rows.
    """
    original_prompt_ids = {experiment.phase2_prompt_a_id, experiment.phase3_prompt_id}
    original_taxonomy_id = experiment.taxonomy_id

    other_generation = PromptTemplate.objects.create_version(
        phase=2, name="g2", user_prompt="Write {{ n_systems }} more."
    )
    other_labelling = PromptTemplate.objects.create_version(
        phase=3, name="l2", user_prompt="{{ scenario.scenario_id }} again"
    )
    other_taxonomy = Taxonomy.objects.create(vocabulary_id="HCV-99", version="2.0")
    HCV.objects.create(
        taxonomy=other_taxonomy, hcv_id="HCV-99", name="Second taxonomy's HCV", order=1
    )

    experiment.phase2_prompt_a = other_generation
    experiment.phase3_prompt = other_labelling
    experiment.taxonomy = other_taxonomy
    experiment.save()

    # A REAL second launch under the new pins — not just an edit.
    fake = FakeLLMClient()
    fake.queue(GENERATED)
    fake.queue(json.dumps({"labels": [{"hcv_id": "HCV-99", "evidence": "e2", "rationale": "r2"}]}))
    set_llm_client(fake)
    execute_pipeline_run(launch(experiment, n_repetitions=1)[0])

    bundle = build_bundle(experiment)

    prompt_ids = {p["id"] for p in bundle["prompts"]}
    taxonomy_ids = {t["id"] for t in bundle["taxonomy"]}

    # Both configurations must be present — neither the old one dropped nor
    # only the new one kept.
    assert original_prompt_ids <= prompt_ids
    assert {other_generation.pk, other_labelling.pk} <= prompt_ids
    assert len(bundle["taxonomy"]) == 2
    assert original_taxonomy_id in taxonomy_ids
    assert other_taxonomy.pk in taxonomy_ids

    for run in bundle["runs"]:
        assert run["prompt_template"] in prompt_ids
        assert run["taxonomy"] in taxonomy_ids

    hcv_ids = {row["hcv_id"] for t in bundle["taxonomy"] for row in t["rows"]}
    label_hcv_ids = {label["hcv_id"] for label in bundle["labels"]}
    assert label_hcv_ids <= hcv_ids
    # Both repetitions' labels are present — the point of this whole test.
    assert {"HCV-07", "HCV-99"} <= label_hcv_ids


@pytest.mark.django_db
def test_editing_every_pinned_field_does_not_change_what_a_run_reports(
    api_client, staff, experiment
):
    """USER DECISION proof, closed. On the pre-fix code, `PATCH
    {"seed_systems": [other]}` on a COMPLETED experiment changed
    `bundle["inputs"]["seed_systems"]` in the very same export — the expert
    ground-truth anchor and the record of what variant A actually saw both
    "left the archive". `PipelineRun.config`, pinned once at launch and read
    by `build_run_bundle` (never `Experiment`'s live fields), is what keeps
    that from happening now. Editing the experiment itself stays completely
    unrestricted — the PATCH below returns 200, not 409 or 400 — matching
    the user's explicit ruling that nothing freezes.

    Covers six of the seven newly-snapshotted fields in one PATCH;
    `generate_scenarios` is covered separately in the next test — flipping
    BOTH `generate_systems` and `generate_scenarios` off in the same request
    is refused outright by `Experiment.clean()`, so they cannot share one.
    """
    run = experiment.runs.get()
    assert run.config["seed_systems"][0]["title"] == "Oldsmar"
    assert run.config["fewshots"] == []
    assert run.config["target_system"] is None
    assert run.config["generate_systems"] is True
    assert run.config["n_systems"] == 1
    assert run.config["n_scenarios_per_system"] == 1

    swapped = SystemDescription.objects.create(
        title="Swapped", body="A different plant entirely.", origin=Origin.SEED
    )
    target = SystemDescription.objects.create(title="Target", body="t", origin=Origin.MANUAL)
    FewShotExample.objects.create(phase=2, name="ex", input_text="in", output_text="out")

    api_client.force_authenticate(user=staff)
    response = api_client.patch(
        f"/api/experiments/{experiment.pk}/",
        {
            "seed_systems": [swapped.pk],
            "fewshots": [FewShotExample.objects.get(name="ex").pk],
            "target_system": target.pk,
            "n_systems": 5,
            "n_scenarios_per_system": 9,
            "generate_systems": False,
        },
        format="json",
    )
    assert response.status_code == 200

    bundle = build_run_bundle(PipelineRun.objects.get(pk=run.pk))
    assert bundle["inputs"]["seed_systems"][0]["title"] == "Oldsmar"
    assert bundle["inputs"]["fewshots"] == []
    assert bundle["inputs"]["target_system"] is None
    assert bundle["inputs"]["generate_systems"] is True
    assert bundle["inputs"]["n_systems"] == 1
    assert bundle["inputs"]["n_scenarios_per_system"] == 1

    # The distinction is real, not a no-op: `experiment_current` DOES show
    # the edit — it is simply not what this run's own `inputs` reports.
    assert bundle["experiment_current"]["n_systems"] == 5


@pytest.mark.django_db
def test_editing_generate_scenarios_after_launch_does_not_change_what_a_run_reports(
    api_client, staff, experiment
):
    """The seventh snapshotted scalar, covered on its own — see the previous
    test's docstring for why it cannot share a single PATCH with
    `generate_systems`.
    """
    run = experiment.runs.get()
    assert run.config["generate_scenarios"] is True

    api_client.force_authenticate(user=staff)
    response = api_client.patch(
        f"/api/experiments/{experiment.pk}/", {"generate_scenarios": False}, format="json"
    )
    assert response.status_code == 200

    bundle = build_run_bundle(PipelineRun.objects.get(pk=run.pk))
    assert bundle["inputs"]["generate_scenarios"] is True
    assert bundle["experiment_current"]["generate_scenarios"] is False


@pytest.mark.django_db
def test_editing_a_seed_systems_body_after_launch_keeps_the_snapshots_original_text():
    """Pinning ids alone is not enough: `ReadOnlyIfGenerated` only protects
    a GENERATED row, so a seed `SystemDescription` (and its `Scenario`s)
    stay editable after the fact, and an id could still point at text that
    has since changed. Proof, made concrete: a seed system's `body` and its
    scenario's `narrative`, both edited AFTER launch — the run's own
    snapshot must keep the ORIGINAL text, matching what `rendered_system_prompt`
    /`rendered_user_prompt` already archived verbatim on the call.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-BODY", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="x", order=1)
    generation = PromptTemplate.objects.create_version(phase=2, name="g3", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(phase=3, name="l3", user_prompt="l")
    seed = SystemDescription.objects.create(
        title="Oldsmar", body="Original body.", origin=Origin.SEED
    )
    Scenario.objects.create(
        system_description=seed,
        scenario_id="seed_01",
        actor="A",
        trigger="T",
        standing_state="S",
        asset="As",
        threat="Th",
        outcome="O",
        narrative="Original narrative.",
        origin=Origin.SEED,
    )
    made = Experiment.objects.create(
        name="body-edit",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    made.variants = ["A"]
    made.save()
    made.seed_systems.add(seed)

    run = launch(made, n_repetitions=1)[0]

    seed.body = "Edited body."
    seed.save()
    scenario = seed.scenarios.get()
    scenario.narrative = "Edited narrative."
    scenario.save()

    bundle = build_run_bundle(PipelineRun.objects.get(pk=run.pk))
    snapshot_system = bundle["inputs"]["seed_systems"][0]
    assert snapshot_system["body"] == "Original body."
    assert len(snapshot_system["scenarios"]) == 1
    assert snapshot_system["scenarios"][0]["narrative"] == "Original narrative."

    # The live rows genuinely changed — proves this isn't a no-op edit.
    seed.refresh_from_db()
    assert seed.body == "Edited body."
    scenario.refresh_from_db()
    assert scenario.narrative == "Edited narrative."


@pytest.mark.django_db
def test_two_runs_of_one_experiment_report_different_pinned_configs(api_client, staff, experiment):
    """Two repetitions of the SAME experiment, with a config edit between
    the two launches — each run must report ITS OWN snapshot, and the two
    must differ, proving the pin is per-run, not per-experiment.
    """
    first_run = experiment.runs.get()
    assert first_run.config["n_systems"] == 1

    api_client.force_authenticate(user=staff)
    response = api_client.patch(
        f"/api/experiments/{experiment.pk}/", {"n_systems": 7}, format="json"
    )
    assert response.status_code == 200
    # The PATCH went through a DIFFERENT `Experiment` instance (DRF's own
    # `get_object()`); this test's local `experiment` is now stale in
    # memory and must be refreshed before `launch()` reads its fields.
    experiment.refresh_from_db()

    second_run = launch(experiment, n_repetitions=1)[0]

    first_run.refresh_from_db()
    assert first_run.config["n_systems"] == 1
    assert second_run.config["n_systems"] == 7
    assert first_run.config != second_run.config

    bundle = build_bundle(experiment)
    assert bundle["run_configs"][first_run.pk]["n_systems"] == 1
    assert bundle["run_configs"][second_run.pk]["n_systems"] == 7


@pytest.mark.django_db
def test_the_bundle_carries_generated_material_and_labels(experiment):
    bundle = build_bundle(experiment)
    assert bundle["systems"][0]["title"] == "Riverbend"
    assert bundle["scenarios"][0]["scenario_id"] == "sc_01"
    assert bundle["scenarios"][0]["threat"] == "Spoofing."
    assert bundle["labels"][0]["hcv_id"] == "HCV-07"
    assert bundle["labels"][0]["llm_call"] is not None


@pytest.mark.django_db
def test_the_bundle_carries_every_call_in_full(experiment):
    calls = build_bundle(experiment)["calls"]
    assert len(calls) == 2
    for call in calls:
        assert "rendered_user_prompt" in call
        assert "request_json" in call
        assert "response_json" in call
    assert any(c["rendered_user_prompt"] == "Write 1." for c in calls)


@pytest.mark.django_db
def test_the_bundle_records_the_isolation_evidence(experiment):
    """A Run A call must carry proof its leak scan ran."""
    generation = [c for c in build_bundle(experiment)["calls"] if c["scenario"] is None]
    assert generation[0]["leak_scan_ran"] is True
    assert generation[0]["guarded_by"] is not None


@pytest.mark.django_db
def test_the_bundle_contains_no_api_key(experiment):
    assert SENTINEL not in json.dumps(build_bundle(experiment))
    assert "sk-or-" not in json.dumps(build_bundle(experiment))


@pytest.mark.django_db
def test_the_bundle_contains_no_api_key_even_when_one_was_actually_written(experiment):
    """The test above proves nothing on its own: nothing in the ordinary
    fixture flow ever writes a key-shaped string into any `LLMCall` field —
    the real client sends the key as a header, never in the body, and
    `FakeLLMClient`'s queued text (`GENERATED`/`LABELLED`) contains no key
    either — so it would pass even if redaction, or this export, were
    entirely broken. This writes the sentinel directly into a call's fields
    through the real model instead — the one thing that WOULD leak if
    `LLMCall.save()`'s write-time redaction (or `build_bundle`) stopped
    working — and confirms it never reaches the export.
    """
    phase_run = experiment.runs.get().phase_runs.get(phase=2)
    call = LLMCall(
        phase_run=phase_run,
        sequence=99,
        rendered_system_prompt=f"system prompt with a key: {SENTINEL}",
        rendered_user_prompt=f"user prompt with a key: {SENTINEL}",
        request_json={"messages": [{"role": "user", "content": SENTINEL}]},
        response_json={"choices": [{"message": {"content": SENTINEL}}]},
        raw_response=SENTINEL,
        transport_error=f"401 from provider, echoed key: {SENTINEL}",
        # A provider that returns something key-shaped where a string was
        # expected puts that value into pydantic's validation message
        # (`input_value=...`) — landing in `parse_error`, not `raw_response`.
        # `unknown_hcv_ids`, `stray_scenarios`, `model` and `finish_reason`
        # are all read off provider output too, after the client returns —
        # every column this export touches must be exercised here, not just
        # the first six.
        parse_error=f"1 validation error: input_value='{SENTINEL}'",
        unknown_hcv_ids=[SENTINEL],
        stray_scenarios=[{"scenario_id": SENTINEL}],
        model=f"echo: {SENTINEL}",
        finish_reason=f"stop ({SENTINEL})",
    )
    call.save()  # LLMCall.save() redacts every one of these fields in place.

    bundle_text = json.dumps(build_bundle(experiment))
    assert SENTINEL not in bundle_text
    assert "sk-or-" not in bundle_text


@pytest.mark.django_db
def test_the_csv_zip_holds_one_file_per_section(experiment):
    """No `inputs_*.csv` files at the experiment level any more: `build_bundle`
    carries no experiment-wide `inputs` section (see
    `test_the_bundle_has_every_section_the_spec_names`) — each repetition's
    pinned config lives in its own `run_configs.csv` row instead (one CSV,
    not repeated across every phase-run row of `runs.csv`).
    `test_a_run_export_writes_a_single_inputs_csv_row` covers the per-run
    export, which DOES carry a singular `inputs.csv`.
    """
    archive = zipfile.ZipFile(io.BytesIO(write_csv_zip(build_bundle(experiment))))
    assert set(archive.namelist()) == {
        "experiment_current.csv",
        "taxonomy.csv",
        "prompts.csv",
        "runs.csv",
        "run_configs.csv",
        "systems.csv",
        "scenarios.csv",
        "labels.csv",
        "calls.csv",
    }


@pytest.mark.django_db
def test_the_csv_zip_pins_the_exact_header_row_of_every_member(experiment):
    """Pinning the JSON key sets (see `test_the_bundle_pins_exact_key_sets_per_section`)
    does not, by itself, protect a CSV column: `_taxonomy_csv_rows` injects
    `taxonomy_id`/`vocabulary_id`/`version` onto every flattened HCV row
    ONLY inside `write_csv_zip`, entirely separately from the JSON `taxonomy`
    section those JSON-level tests check — deleting that injection left the
    whole suite green until this test existed. Every CSV member's header is
    pinned here, in the exact column ORDER `csv.DictWriter` actually writes
    (an empty section writes no header line at all — see `_write_csv`).
    """
    archive = zipfile.ZipFile(io.BytesIO(write_csv_zip(build_bundle(experiment))))

    def header(name):
        return archive.read(f"{name}.csv").decode("utf-8").splitlines()[0].split(",")

    system_header = [
        "id",
        "title",
        "slug",
        "body",
        "origin",
        "variant",
        "generation_run",
        "created_by",
        "created_at",
        "updated_at",
    ]
    scenario_header = [
        "id",
        "system_description",
        "scenario_id",
        "actor",
        "trigger",
        "standing_state",
        "asset",
        "threat",
        "outcome",
        "narrative",
        "origin",
        "variant",
        "intended_hcvs",
        "abox",
        "generation_run",
        "order",
        "created_by",
        "created_at",
        "updated_at",
    ]

    assert header("experiment_current") == [
        "id",
        "name",
        "description",
        "phase2_model",
        "phase2_temperature",
        "phase3_model",
        "phase3_temperature",
        "generate_systems",
        "generate_scenarios",
        "n_systems",
        "n_scenarios_per_system",
        "variants",
        "created_by",
        "created_at",
    ]
    assert header("taxonomy") == [
        "taxonomy_id",
        "vocabulary_id",
        "version",
        "hcv_id",
        "name",
        "definition",
        "condition_classes",
        "order",
    ]
    assert header("prompts") == [
        "id",
        "phase",
        "name",
        "version",
        "system_prompt",
        "user_prompt",
        "output_schema",
        "created_by",
        "created_at",
    ]
    assert header("systems") == system_header
    assert header("scenarios") == scenario_header
    assert header("runs") == [
        "pipeline_run",
        "repetition_index",
        "pipeline_run_status",
        "pipeline_run_started_at",
        "pipeline_run_finished_at",
        "phase_run",
        "phase",
        "variant",
        "model",
        "temperature",
        "prompt_template",
        "taxonomy",
        "source_phase_run",
        "status",
        "error",
        "total_children",
        "completed_children",
        "failed_children",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "created_by",
        "started_at",
        "finished_at",
    ]
    assert header("run_configs") == [
        "pipeline_run",
        "seed_systems",
        "fewshots",
        "target_system",
        "generate_systems",
        "generate_scenarios",
        "n_systems",
        "n_scenarios_per_system",
    ]
    assert header("labels") == [
        "id",
        "phase_run",
        "scenario",
        "scenario_id",
        "hcv_id",
        "hcv_name",
        "evidence",
        "rationale",
        "source",
        "llm_call",
        "created_by",
        "created_at",
    ]
    assert header("calls") == [
        "id",
        "phase_run",
        "sequence",
        "scenario",
        "rendered_system_prompt",
        "rendered_user_prompt",
        "request_json",
        "response_json",
        "raw_response",
        "parsed_ok",
        "parse_error",
        "unknown_hcv_ids",
        "stray_scenarios",
        "http_status",
        "transport_error",
        "model",
        "finish_reason",
        "prompt_tokens",
        "completion_tokens",
        "cost",
        "latency_ms",
        "leak_scan_ran",
        "guarded_by",
        "created_at",
    ]


@pytest.mark.django_db
def test_the_csv_calls_are_not_truncated(experiment):
    archive = zipfile.ZipFile(io.BytesIO(write_csv_zip(build_bundle(experiment))))
    text = archive.read("calls.csv").decode("utf-8")
    assert "Write 1." in text
    assert SENTINEL not in text


@pytest.mark.django_db
def test_a_run_export_writes_a_single_inputs_csv_row(experiment):
    """`inputs.csv` exists only on the PER-RUN export (`build_run_bundle`,
    task 5 step 4) — `build_bundle`'s experiment-wide bundle carries no
    `inputs` section at all any more (see
    `test_the_csv_zip_holds_one_file_per_section`). It is always exactly ONE
    row: `run.config` is a single dict, not a list of differently-shaped
    items, so the historical crash this used to guard (`inputs`
    concatenating seed systems and few-shots — two different shapes — into
    one list, which raised `ValueError: dict contains fields not in
    fieldnames` the moment both were non-empty) cannot recur here. Confirms
    both a seed system and a few-shot come through the ONE row without
    crashing the writer.
    """
    FewShotExample.objects.create(phase=2, name="ex", input_text="in", output_text="out")
    experiment.fewshots.add(FewShotExample.objects.get(name="ex"))

    # A second repetition, so its `.config` snapshot is fresh and includes
    # the few-shot just added — `launch()` is all that is needed here, this
    # does not need to actually execute.
    run = launch(experiment, n_repetitions=1)[0]

    archive = zipfile.ZipFile(io.BytesIO(write_csv_zip(build_run_bundle(run))))
    text = archive.read("inputs.csv").decode("utf-8")
    assert "Oldsmar" in text
    assert "ex" in text


@pytest.mark.django_db
def test_write_csv_zip_unions_fieldnames_across_heterogeneous_rows(experiment):
    """Direct defense-in-depth check on `_as_rows`'s union-fieldnames fix,
    not exercised by any CURRENT bundle section on its own — every section
    is internally homogeneous by construction (see `_runs`/`_systems`/etc,
    and `inputs`'s own single-dict shape on the per-run export). But
    `csv.DictWriter` trusting only the FIRST row's keys is exactly what made
    the old, multi-shape `inputs` 500 the moment a second, differently-shaped
    row showed up; a future section going heterogeneous must get a blank
    cell, not a `ValueError: dict contains fields not in fieldnames`. Built
    directly (not via `build_bundle`) since the fix guards against a shape no
    current section actually produces.
    """
    bundle = build_bundle(experiment)
    bundle["calls"] = [
        {"id": 1, "sequence": 1},
        {"id": 2, "sequence": 2, "only_the_second_row_has_this": "value"},
    ]

    archive = zipfile.ZipFile(io.BytesIO(write_csv_zip(bundle)))
    rows = list(csv.DictReader(io.StringIO(archive.read("calls.csv").decode("utf-8"))))

    assert rows[0]["only_the_second_row_has_this"] == ""
    assert rows[1]["only_the_second_row_has_this"] == "value"


@pytest.mark.django_db
def test_export_calls_determine_version_before_dispatching(rf, member, experiment):
    """`initial()`'s `export`-only branch skips DRF's own content
    negotiation (see that method's docstring) but must still call
    `determine_version`, matching what `super().initial()` does for every
    OTHER action. Omitting it left `request.version`/`.versioning_scheme`
    entirely UNSET rather than the documented `(None, None)` default this
    project's unconfigured `DEFAULT_VERSIONING_CLASS` implies — harmless
    only because nothing currently reads them, and an `AttributeError`
    waiting for the day something does.
    """
    view = ExperimentViewSet()
    view.action_map = {"get": "export"}
    django_request = rf.get(f"/api/experiments/{experiment.pk}/export/")
    force_authenticate(django_request, user=member)
    # `initialize_request` (not a hand-built `Request(...)`) so authentication
    # is wired up exactly as `dispatch()` wires it in production.
    request = view.initialize_request(django_request, pk=str(experiment.pk))
    view.request = request
    view.kwargs = {"pk": str(experiment.pk)}

    view.initial(request, pk=str(experiment.pk))

    assert hasattr(request, "version")
    assert hasattr(request, "versioning_scheme")
    assert request.version is None
    assert request.versioning_scheme is None


@pytest.mark.django_db
def test_the_export_endpoint_returns_json_by_default(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{experiment.pk}/export/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "attachment" in response["Content-Disposition"]
    assert set(json.loads(response.content)) >= {"experiment_current", "calls"}


@pytest.mark.django_db
def test_the_export_endpoint_returns_a_zip_for_csv(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{experiment.pk}/export/?format=csv")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "calls.csv" in archive.namelist()


@pytest.mark.django_db
def test_export_requires_authentication(api_client, experiment):
    assert api_client.get(f"/api/experiments/{experiment.pk}/export/").status_code == 403


@pytest.mark.django_db
def test_an_unknown_export_format_is_a_400(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{experiment.pk}/export/?format=xml")
    assert response.status_code == 400


@pytest.mark.django_db
def test_export_format_is_accepted_case_insensitively(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{experiment.pk}/export/?format=CSV")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"


@pytest.mark.django_db
def test_export_survives_a_path_format_suffix(api_client, member, experiment):
    """`DefaultRouter` generates a format-SUFFIX route for every action
    (e.g. `GET .../export.csv`), which passes `format` as a URL kwarg — the
    action used to have no such parameter, so this raised `TypeError: export()
    got an unexpected keyword argument 'format'` (an uncaught 500). `progress`
    had the identical bug.
    """
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{experiment.pk}/export.csv")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"

    response = api_client.get(f"/api/experiments/{experiment.pk}/progress.json")
    assert response.status_code == 200


# --- Step 4: per-run export (`GET /api/pipeline-runs/{id}/export/`) -------
#
# Same `?format=json|csv` contract as the experiment-level export above, and
# the same guarantees, tested independently at this route too rather than
# assumed from the experiment-level tests: no API key, byte-identical
# output, and the isolation evidence present.


@pytest.mark.django_db
def test_a_run_export_returns_json_by_default(api_client, member, experiment):
    run = experiment.runs.get()
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/pipeline-runs/{run.pk}/export/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "attachment" in response["Content-Disposition"]
    body = json.loads(response.content)
    assert set(body) >= {"experiment_current", "inputs", "runs", "calls"}
    assert len(body["runs"]) == 2  # this run's own two phase runs (variant A only)
    assert {row["pipeline_run"] for row in body["runs"]} == {run.pk}


@pytest.mark.django_db
def test_a_run_export_returns_a_zip_for_csv(api_client, member, experiment):
    run = experiment.runs.get()
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/pipeline-runs/{run.pk}/export/?format=csv")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "calls.csv" in archive.namelist()
    assert "inputs.csv" in archive.namelist()


@pytest.mark.django_db
def test_a_run_export_requires_authentication(api_client, experiment):
    run = experiment.runs.get()
    assert api_client.get(f"/api/pipeline-runs/{run.pk}/export/").status_code == 403


@pytest.mark.django_db
def test_a_run_export_carries_only_that_runs_own_material(api_client, member, experiment):
    """Scoped to ONE repetition, not the whole experiment: a second
    repetition's calls/systems/scenarios must not leak into this run's own
    export.
    """
    first_run = experiment.runs.get()
    _run_another_repetition(experiment)
    second_run = PipelineRun.objects.exclude(pk=first_run.pk).get(experiment=experiment)

    api_client.force_authenticate(user=member)
    body = json.loads(api_client.get(f"/api/pipeline-runs/{first_run.pk}/export/").content)

    assert {row["pipeline_run"] for row in body["runs"]} == {first_run.pk}
    assert len(body["calls"]) == 2  # this run's own phase-2 + phase-3 call, not both runs'
    phase_run_ids = {row["phase_run"] for row in body["runs"]}
    assert all(call["phase_run"] in phase_run_ids for call in body["calls"])
    # Sanity: the OTHER run's own export is likewise scoped, not just this
    # one — proves the scoping isn't an accident of which run happened first.
    other_body = json.loads(api_client.get(f"/api/pipeline-runs/{second_run.pk}/export/").content)
    assert {row["pipeline_run"] for row in other_body["runs"]} == {second_run.pk}


@pytest.mark.django_db
def test_a_run_export_carries_the_isolation_evidence(api_client, member, experiment):
    """A Run A call must carry proof its leak scan ran — same guarantee as
    `test_the_bundle_records_the_isolation_evidence`, tested independently
    at this route too."""
    run = experiment.runs.get()
    api_client.force_authenticate(user=member)
    body = json.loads(api_client.get(f"/api/pipeline-runs/{run.pk}/export/").content)

    generation = [c for c in body["calls"] if c["scenario"] is None]
    assert generation[0]["leak_scan_ran"] is True
    assert generation[0]["guarded_by"] is not None


@pytest.mark.django_db
def test_a_run_export_contains_no_api_key(api_client, member, experiment):
    run = experiment.runs.get()
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/pipeline-runs/{run.pk}/export/")
    assert SENTINEL not in response.content.decode("utf-8")
    assert "sk-or-" not in response.content.decode("utf-8")


@pytest.mark.django_db
def test_a_run_export_contains_no_api_key_even_when_one_was_actually_written(
    api_client, member, experiment
):
    """Same reasoning as `test_the_bundle_contains_no_api_key_even_when_one_was_actually_written`:
    proves the redaction survives being read back THROUGH a real `LLMCall`
    row, not merely that the ordinary fixture flow never writes a key.
    """
    run = experiment.runs.get()
    phase_run = run.phase_runs.get(phase=2)
    call = LLMCall(
        phase_run=phase_run,
        sequence=99,
        rendered_system_prompt=f"system prompt with a key: {SENTINEL}",
        raw_response=SENTINEL,
    )
    call.save()  # LLMCall.save() redacts every one of these fields in place.

    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/pipeline-runs/{run.pk}/export/")
    assert SENTINEL not in response.content.decode("utf-8")
    assert "sk-or-" not in response.content.decode("utf-8")


@pytest.mark.django_db
def test_two_run_exports_of_an_unchanged_run_are_byte_identical(api_client, member, experiment):
    run = experiment.runs.get()
    api_client.force_authenticate(user=member)
    first = api_client.get(f"/api/pipeline-runs/{run.pk}/export/").content
    second = api_client.get(f"/api/pipeline-runs/{run.pk}/export/").content
    assert first == second


@pytest.mark.django_db
def test_an_unknown_run_export_format_is_a_400(api_client, member, experiment):
    run = experiment.runs.get()
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/pipeline-runs/{run.pk}/export/?format=xml")
    assert response.status_code == 400


@pytest.mark.django_db
def test_a_run_export_survives_a_path_format_suffix(api_client, member, experiment):
    """The actual bug `ExportContentNegotiationMixin` (shared with
    `ExperimentViewSet.export`, see `test_export_survives_a_path_format_suffix`)
    guards against, now proven at THIS route too: `DefaultRouter` generates a
    format-SUFFIX route for every action (e.g. `GET .../export.csv`), which
    passes `format` as a URL kwarg and used to raise `TypeError` (a 500)
    wherever an action had no such parameter.
    """
    run = experiment.runs.get()
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/pipeline-runs/{run.pk}/export.csv")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"


@pytest.mark.django_db
def test_build_run_bundle_has_every_section_with_no_key_missing(experiment):
    """Finding 4, task 5 review round: no test pinned `build_run_bundle`'s
    OWN top-level key set — `test_a_run_export_returns_json_by_default`
    checks only a subset via `>=`, so `taxonomy`/`systems`/`scenarios`/
    `labels`/`prompts` were each independently deletable from it with the
    suite still green. Mirrors `test_the_bundle_has_every_section_the_spec_names`
    (the equivalent test for `build_bundle`) for the per-run shape instead.
    """
    run = experiment.runs.get()
    bundle = build_run_bundle(run)
    assert set(bundle) == {
        "experiment_current",
        "taxonomy",
        "prompts",
        "inputs",
        "runs",
        "systems",
        "scenarios",
        "labels",
        "calls",
    }


@pytest.mark.django_db
def test_a_run_export_carries_its_own_pinned_inputs_with_every_nested_field_populated():
    """`build_run_bundle`'s `inputs` is `run.config` verbatim — the run's own
    pinned snapshot, in its native (nested) shape, distinct from
    `build_bundle`'s `run_configs` (see
    `test_the_bundles_run_configs_pins_exact_key_sets_and_values_including_nested_fields`,
    which checks the SAME snapshot data through that other bundle).

    A review round found this test's predecessor vacuous at exactly the
    level that mattered: with the shared `experiment` fixture's bare seed
    system (no scenario, no few-shot, no `target_system`), `abox`/
    `intended_hcvs` could be dropped from the scenario snapshot, a
    few-shot's `output` could be silently omitted, and `target_system`
    could be hard-coded to `None`, all with this test still green — none of
    those fields were ever populated with real data to check. This builds
    an experiment with all three actually set, and pins VALUES, not just
    key sets, at every nesting level.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-DEEP", version="1.0", is_active=True)
    hcv1 = HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="a", order=1)
    hcv2 = HCV.objects.create(taxonomy=tax, hcv_id="HCV-02", name="b", order=2)
    generation = PromptTemplate.objects.create_version(phase=2, name="dg", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(phase=3, name="dl", user_prompt="l")
    seed = SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)
    seed_scenario = Scenario.objects.create(
        system_description=seed,
        scenario_id="seed_01",
        actor="A",
        trigger="T",
        standing_state="S",
        asset="As",
        threat="Th",
        outcome="O",
        narrative="N",
        origin=Origin.SEED,
        abox={"nodes": ["x"]},
    )
    seed_scenario.intended_hcvs.set([hcv1, hcv2])
    target = SystemDescription.objects.create(title="Target", body="t", origin=Origin.MANUAL)
    FewShotExample.objects.create(
        phase=2, name="ex", input_text="the input", output_text="the output"
    )
    fewshot = FewShotExample.objects.get(name="ex")

    made = Experiment.objects.create(
        name="deep",
        taxonomy=tax,
        phase2_model="m2",
        # variants is restricted to ["A"] below, so only phase2_prompt_a is
        # needed for this fixture.
        phase2_prompt_a=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
        n_systems=1,
        n_scenarios_per_system=1,
        target_system=target,
    )
    made.variants = ["A"]
    made.save()
    made.seed_systems.add(seed)
    made.fewshots.add(fewshot)

    run = launch(made, n_repetitions=1)[0]

    bundle = build_run_bundle(run)
    assert bundle["inputs"] == run.config
    config = bundle["inputs"]
    assert set(config) == {
        "seed_systems",
        "fewshots",
        "target_system",
        "generate_systems",
        "generate_scenarios",
        "n_systems",
        "n_scenarios_per_system",
    }

    snapshot_system = config["seed_systems"][0]
    assert set(snapshot_system) == {"id", "title", "slug", "body", "scenarios"}
    assert snapshot_system["title"] == "Oldsmar"

    snapshot_scenario = snapshot_system["scenarios"][0]
    assert set(snapshot_scenario) == {
        "scenario_id",
        "actor",
        "trigger",
        "standing_state",
        "asset",
        "threat",
        "outcome",
        "narrative",
        "intended_hcvs",
        "abox",
    }
    assert snapshot_scenario["intended_hcvs"] == ["HCV-01", "HCV-02"]
    assert snapshot_scenario["abox"] == {"nodes": ["x"]}

    snapshot_fewshot = config["fewshots"][0]
    assert set(snapshot_fewshot) == {
        "id",
        "name",
        "phase",
        "is_active",
        "input",
        "output",
        "input_file_id",
        "output_file_id",
        "input_resource_kind",
        "output_resource_kind",
    }
    assert snapshot_fewshot["input"] == "the input"
    assert snapshot_fewshot["output"] == "the output"
    assert snapshot_fewshot["is_active"] is True
    assert snapshot_fewshot["input_resource_kind"] is None
    assert snapshot_fewshot["output_resource_kind"] is None

    assert config["target_system"] is not None
    assert config["target_system"]["id"] == target.pk
    assert config["target_system"]["title"] == "Target"

    # And the same data, reachable the OTHER way — build_bundle's run_configs.
    experiment_bundle = build_bundle(made)
    assert experiment_bundle["run_configs"] == {run.pk: run.config}


@pytest.mark.parametrize("bad_name", ["evil\r\nSet-Cookie: x=y", 'quote"name', "slash/name"])
@pytest.mark.django_db
def test_export_never_raises_on_an_unsafe_experiment_name(api_client, member, bad_name):
    """`Content-Disposition` used to be built from `experiment.name` — a
    free, staff-writable 200-char field with no charset restriction — with
    no sanitisation. A `\\r\\n` in it reached `HttpResponse.__setitem__`
    verbatim and raised `django.core.exceptions.BadHeaderError` (an
    uncaught 500); a bare `"` silently broke the filename's own quoting.
    """
    tax = Taxonomy.objects.create(vocabulary_id="X", version="1")
    HCV.objects.create(taxonomy=tax, hcv_id="X-1", name="x", order=1)
    generation = PromptTemplate.objects.create_version(phase=2, name="g2", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(phase=3, name="l2", user_prompt="l")
    made = Experiment.objects.create(
        name=bad_name,
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=generation,
        phase2_prompt_b=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
    )

    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{made.pk}/export/")

    assert response.status_code == 200
    assert "\r" not in response["Content-Disposition"]
    assert "\n" not in response["Content-Disposition"]
    disposition = response["Content-Disposition"]
    # The quoted filename must be exactly one quoted token: a stray `"` in
    # the stem would otherwise close the quoting early.
    assert disposition.count('"') == 2


@pytest.mark.django_db
def test_exporting_a_never_run_experiment_ships_empty_pinned_sections_not_an_error(
    api_client, member
):
    """No `PhaseRun` exists yet to pin anything to, so `prompts`/`taxonomy`
    (and `runs`/`systems`/`scenarios`/`labels`/`calls`) are correctly empty —
    this is "nothing has been pinned yet", not data loss, and the export
    docstring says so explicitly so a reader isn't left guessing. The export
    itself must still succeed (200, a real JSON body, a real zip) rather
    than erroring out on an experiment that simply hasn't run.
    """
    tax = Taxonomy.objects.create(vocabulary_id="NEVER-RUN", version="1")
    HCV.objects.create(taxonomy=tax, hcv_id="X-1", name="x", order=1)
    generation = PromptTemplate.objects.create_version(phase=2, name="never-g", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(phase=3, name="never-l", user_prompt="l")
    made = Experiment.objects.create(
        name="never run",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=generation,
        phase2_prompt_b=generation,
        phase3_model="m3",
        phase3_prompt=labelling,
    )

    bundle = build_bundle(made)
    assert bundle["prompts"] == []
    assert bundle["taxonomy"] == []
    assert bundle["runs"] == []
    assert bundle["systems"] == []
    assert bundle["scenarios"] == []
    assert bundle["labels"] == []
    assert bundle["calls"] == []

    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{made.pk}/export/?format=csv")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert "prompts.csv" in archive.namelist()
    assert "taxonomy.csv" in archive.namelist()


@pytest.mark.django_db
def test_a_run_with_no_snapshot_reports_its_config_absence_explicitly(
    api_client, member, experiment
):
    """Finding 5, task 5 review round: a `PipelineRun` created before the
    migration that added `config` (or by any path that bypasses `launch()`)
    has `config == {}` — the field's bare default. Left as-is, that reads
    identically to "this run's config was genuinely empty" (no seeds, no
    few-shots — a real, legitimate shape `config_snapshot` itself never
    actually produces, since it always writes all seven keys). `inputs`
    must instead read `None`, explicitly, so a reader cannot mistake "never
    snapshotted" for "snapshotted, nothing in it" — and the CSV export
    (`inputs.csv`, `run_configs.csv`) must not crash on it either.
    """
    run = experiment.runs.get()
    run.config = {}
    run.save(update_fields=["config"])

    api_client.force_authenticate(user=member)

    run_json = api_client.get(f"/api/pipeline-runs/{run.pk}/export/").json()
    assert run_json["inputs"] is None

    run_csv = api_client.get(f"/api/pipeline-runs/{run.pk}/export/?format=csv")
    assert run_csv.status_code == 200
    run_archive = zipfile.ZipFile(io.BytesIO(run_csv.content))
    assert "inputs.csv" in run_archive.namelist()
    assert run_archive.read("inputs.csv") == b""  # no rows, no header — see _write_csv

    experiment_json = api_client.get(f"/api/experiments/{experiment.pk}/export/").json()
    assert experiment_json["run_configs"][str(run.pk)] is None

    experiment_csv = api_client.get(f"/api/experiments/{experiment.pk}/export/?format=csv")
    assert experiment_csv.status_code == 200
    experiment_archive = zipfile.ZipFile(io.BytesIO(experiment_csv.content))
    run_configs_rows = list(
        csv.DictReader(io.StringIO(experiment_archive.read("run_configs.csv").decode("utf-8")))
    )
    assert run_configs_rows == [{"pipeline_run": str(run.pk), "config": ""}]


def _run_another_repetition(experiment):
    """One more repetition, identical generated material and label.

    Every repetition of a fixed experiment asks the model the exact same
    thing, so a real run's generated system titles and scenario ids repeat
    verbatim across repetitions — this is not a contrived edge case.
    """
    fake = FakeLLMClient()
    fake.queue(GENERATED)
    fake.queue(LABELLED)
    set_llm_client(fake)
    execute_pipeline_run(launch(experiment, n_repetitions=1)[0])


@pytest.mark.django_db
def test_build_bundle_query_count_does_not_grow_with_repetitions(experiment):
    """The bundle walks every call, label, scenario and system belonging to
    however many repetitions the experiment has run. A query issued per row
    in any of those tables would make the export cost grow with the
    experiment instead of staying flat — caught here by comparing the exact
    query count for 1 repetition against the count for 4."""
    with CaptureQueriesContext(connection) as one_repetition:
        build_bundle(experiment)

    for _ in range(3):
        _run_another_repetition(experiment)

    with CaptureQueriesContext(connection) as four_repetitions:
        build_bundle(experiment)

    assert len(four_repetitions) == len(one_repetition)


@pytest.mark.django_db
def test_two_exports_of_an_unchanged_experiment_are_byte_identical(experiment):
    """Every section must come out in a stable, reproducible order.

    Run several repetitions of the SAME configuration first: they generate a
    system titled identically ("Riverbend") and a scenario identically
    numbered ("sc_01", order 1) every time, which is exactly the kind of tie
    that leaves iteration order database-implementation-defined unless it is
    pinned explicitly (see `Label.Meta.ordering`'s own `id` tiebreaker, added
    for the same reason). This is a real behavioural guarantee — two exports
    of the same data must read the same — but it is NOT, on its own, proof
    that the tie is pinned: see the SQL-level test right below for why.
    """
    for _ in range(3):
        _run_another_repetition(experiment)

    first = json.dumps(build_bundle(experiment), sort_keys=False)
    second = json.dumps(build_bundle(experiment), sort_keys=False)
    assert first == second


@pytest.mark.django_db
def test_the_tied_sections_order_by_an_explicit_id_tiebreaker(experiment):
    """`SystemDescription.Meta.ordering = ["title"]` and `Scenario`'s default
    ordering are not unique — the fixture above already ties on both: every
    repetition of one experiment generates a system titled "Riverbend" and a
    scenario "sc_01"/order 1. That makes the PREVIOUS test
    (`test_two_exports_of_an_unchanged_experiment_are_byte_identical`)
    unable, on its own, to prove the tie is pinned: SQLite's own storage
    engine happens to break ties by rowid (insertion order) even with NO
    `ORDER BY id` at all, so that test passes whether or not `_systems`/
    `_scenarios` request one explicitly — confirmed by temporarily reverting
    their `.order_by(...)` and re-running it (see task report). Postgres,
    this project's real target, gives no such guarantee. So this asserts the
    SQL `build_bundle` actually issues, rather than the row order SQLite
    happens to return, which is the only way to catch the tiebreaker being
    dropped on this backend.
    """
    for _ in range(3):
        _run_another_repetition(experiment)

    with CaptureQueriesContext(connection) as queries:
        build_bundle(experiment)

    # `"generation_run_id" IN` (a WHERE clause) singles out `_systems`'s own
    # query — `build_bundle` no longer runs any other query against
    # `corpus_systemdescription` (the old, live-reading `_inputs`/
    # `_seed_scenarios` are gone; a run's inputs come from its pinned
    # `PipelineRun.config` snapshot now), but this stays explicit rather
    # than relying on that being the only such query by coincidence.
    systems_sql = next(
        q["sql"]
        for q in queries.captured_queries
        if 'FROM "corpus_systemdescription"' in q["sql"] and '"generation_run_id" IN' in q["sql"]
    )
    # Same reasoning, one level deeper: `"corpus_scenario"."generation_run_id"
    # IN` (an INNER JOIN's IN clause) is unique to `_scenarios`'s query.
    scenarios_sql = next(
        q["sql"]
        for q in queries.captured_queries
        if 'FROM "corpus_scenario"' in q["sql"]
        and '"corpus_scenario"."generation_run_id" IN' in q["sql"]
    )
    assert '"corpus_systemdescription"."id" ASC' in systems_sql.split("ORDER BY")[-1]
    assert '"corpus_scenario"."id" ASC' in scenarios_sql.split("ORDER BY")[-1]


@pytest.mark.django_db
def test_adhoc_runs_are_excluded_from_the_experiment_export(experiment):
    """USER DECISION (iterate-mode task): ad-hoc runs are scratch work and
    must NEVER appear in any export bundle — the export's whole point is to
    be "a real experiment's record", and an ad-hoc `PhaseRun` (no
    `pipeline_run`) was never launched against this, or any, `Experiment`.

    `_phase_run_ids(experiment)` filters `PhaseRun.objects.filter(
    pipeline_run__experiment=experiment)` — a `pipeline_run__` lookup, which
    SQL renders as a JOIN condition an ad-hoc run's NULL `pipeline_run_id`
    can never satisfy. This proves that holds end to end, not just at the
    query level: a real ad-hoc phase-2 run (using the SAME model/prompt-phase/
    taxonomy shape as the real experiment) that actually generated a system,
    a scenario, an archived call, AND — via an ad-hoc phase-3 run labelling
    it — a label, must be completely invisible in `build_bundle(experiment)`,
    even though every one of those rows would show up if this scoping were
    ever loosened.
    """
    from pipeline.generation import run_phase2
    from pipeline.models import Label, PhaseRun
    from pipeline.orchestration import run_labelling

    adhoc_generation_template = PromptTemplate.objects.create_version(
        phase=2, name="adhoc-gen", user_prompt="g"
    )
    adhoc_labelling_template = PromptTemplate.objects.create_version(
        phase=3, name="adhoc-lab", user_prompt="{{ scenario.scenario_id }}"
    )
    adhoc_generation = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=adhoc_generation_template,
        taxonomy=experiment.taxonomy,
    )
    get_llm_client().queue(GENERATED)
    run_phase2(adhoc_generation)
    assert adhoc_generation.status == PhaseRun.Status.COMPLETED  # sanity: real work happened

    adhoc_labelling = PhaseRun.objects.create(
        phase=3,
        model="m3",
        prompt_template=adhoc_labelling_template,
        taxonomy=experiment.taxonomy,
        source_phase_run=adhoc_generation,
    )
    get_llm_client().queue(LABELLED)
    adhoc_labelling = run_labelling(adhoc_labelling)
    assert adhoc_labelling.status == PhaseRun.Status.COMPLETED  # sanity: real work happened

    # Sanity: this ad-hoc work really did produce rows that WOULD leak into
    # the export if `_phase_run_ids`' scoping were ever loosened — proving
    # the assertions below are about scoping, not about there being nothing
    # to scope out in the first place.
    assert SystemDescription.objects.filter(generation_run=adhoc_generation).exists()
    assert Scenario.objects.filter(generation_run=adhoc_generation).exists()
    assert LLMCall.objects.filter(phase_run=adhoc_generation).exists()
    assert LLMCall.objects.filter(phase_run=adhoc_labelling).exists()
    assert Label.objects.filter(phase_run=adhoc_labelling).exists()

    bundle = build_bundle(experiment)

    scoped_phase_run_ids = {row["phase_run"] for row in bundle["runs"]}
    assert adhoc_generation.pk not in scoped_phase_run_ids
    assert adhoc_labelling.pk not in scoped_phase_run_ids
    # The real experiment's own single repetition still produced exactly one
    # system/scenario/label and its two calls — unaffected by the ad-hoc
    # run's existence alongside it, not merely "no ad-hoc row visible by
    # name" but a precise count that could not hold if scoping leaked.
    assert len(bundle["systems"]) == 1
    assert len(bundle["scenarios"]) == 1
    assert len(bundle["labels"]) == 1
    assert len(bundle["calls"]) == 2
