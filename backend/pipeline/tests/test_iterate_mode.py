"""Task 1 — iterate mode: `POST /api/phase-runs/` and `DELETE /api/phase-runs/{id}/`.

Runs ONE phase on its own, ad-hoc, with `pipeline_run` left null — spec §5.4
("null for ad-hoc iterate-mode runs") and §9 ("POST /api/phase-runs/ ad-hoc
single phase -> iterate mode"). Everything here is about the NEW `create`/
`destroy` behaviour on `PhaseRunViewSet`; the read-only surface (`list`,
`retrieve`, `retry`, `calls`) is already covered by `test_run_api.py` and is
untouched by this task.

**Isolation is the point of this whole file, not one test in it.** A bare
ad-hoc run (no `experiment` given) still resolves `seed_systems`/`fewshots`
to `[]` (see `_ADHOC_GENERATION_CONFIG`) — a scratch run with genuinely
nothing else to render against. But a coordinator review round found the
ORIGINAL version of this file's own framing wrong: an ad-hoc run can now
optionally BORROW an experiment's CURRENT, LIVE inputs (`experiment=` on the
create payload — see `pipeline.execution_config._adhoc_generation_config`),
which is the actual point of the feature ("if I am in phase 2, I can rerun
manually until I am satisfied" means tuning against REAL seeds and
few-shots, not bare defaults). That widens the isolation surface rather
than narrowing it: a borrowed experiment's few-shots/seed_systems are real
`FewShotExample`/`SystemDescription` rows, reachable by a variant-A ad-hoc
run exactly the way an orchestrated run's pinned snapshot already was. The
"Borrowing an experiment" section below proves BOTH known contamination
routes are still refused on this new path — a TAXONOMY-kind few-shot
(`prompts.rendering._fewshot_rows`' variant-A guard) and a GENERATED-origin
seed system (`generated_seed_systems_error`, re-checked live since
`Experiment.clean()` never runs on a borrowed, already-saved experiment) —
with zero provider calls and zero archived `LLMCall` rows either way.

A bare ad-hoc run with NO borrowed experiment is still covered too:
`test_an_adhoc_variant_a_run_with_a_taxonomy_kind_resource_fails_with_zero_calls`
proves `prompts.rendering._make_resource`'s `resource()` template global —
reachable from ANY phase-2 template regardless of `experiment`/config — still
refuses a TAXONOMY-kind file on that path, through the brand-new create ->
`queue_phase_run` -> `run_phase2_task` path end to end.
"""

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

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
from pipeline.models import Experiment, Label, LLMCall, PhaseRun
from pipeline.tasks import run_phase2_task, run_phase3_task
from prompts.models import PromptTemplate
from prompts.rendering import format_hcv_table
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


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def empty_taxonomy(db):
    """A real, saved Taxonomy with zero HCV rows — a broken import, not a
    missing one. `taxonomy_missing_rows_error` (`pipeline.models`) must
    refuse an ad-hoc run pinned to this the same way `Experiment.clean()`
    already refuses an experiment pinned to it.

    `is_active=True`: this test's whole point is isolating the "has no
    rows" rule from `unbound_guard_taxonomy_error`'s newer "must be the
    active taxonomy" rule (coordinator review round 3) — an INACTIVE
    empty taxonomy would be refused for the wrong reason first, which is
    not what this test is checking.
    """
    return Taxonomy.objects.create(vocabulary_id="HCV-EMPTY-ITER", version="1.0", is_active=True)


@pytest.fixture
def phase2_template(db):
    return PromptTemplate.objects.create_version(
        phase=2, name="gen", user_prompt="Write scenarios."
    )


@pytest.fixture
def phase3_template(db):
    return PromptTemplate.objects.create_version(
        phase=3, name="lab", user_prompt="{{ scenario.scenario_id }}"
    )


def _create_body(phase2_template, taxonomy, **overrides):
    body = {
        "phase": 2,
        "variant": "A",
        "model": "m2",
        "prompt_template": phase2_template.pk,
        "taxonomy": taxonomy.pk,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Permissions: staff-only create and destroy.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_creating_a_phase_run_requires_authentication(api_client, taxonomy, phase2_template):
    response = api_client.post(
        "/api/phase-runs/", _create_body(phase2_template, taxonomy), format="json"
    )
    assert response.status_code == 403
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_only_staff_may_create_an_ad_hoc_phase_run(api_client, member, taxonomy, phase2_template):
    """`StaffWrites` already gates every unsafe method; this pins that the
    NEW `create` action is actually reached by it — the risk the task
    itself names: adding `create` must not accidentally leave it open."""
    api_client.force_authenticate(user=member)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/", _create_body(phase2_template, taxonomy), format="json"
        )
    assert response.status_code == 403
    assert PhaseRun.objects.count() == 0
    assert enqueued.call_count == 0


@pytest.mark.django_db
def test_staff_may_create_an_ad_hoc_phase_run(api_client, staff, taxonomy, phase2_template):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task"):
        response = api_client.post(
            "/api/phase-runs/", _create_body(phase2_template, taxonomy), format="json"
        )
    assert response.status_code == 202
    assert PhaseRun.objects.count() == 1


@pytest.mark.django_db
def test_non_staff_cannot_delete_an_ad_hoc_run(api_client, member, taxonomy, phase2_template):
    generation = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=phase2_template, taxonomy=taxonomy
    )
    api_client.force_authenticate(user=member)
    response = api_client.delete(f"/api/phase-runs/{generation.pk}/")
    assert response.status_code == 403
    assert PhaseRun.objects.filter(pk=generation.pk).exists()


# ---------------------------------------------------------------------------
# The API key is resolved first — before any row exists.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_missing_api_key_fails_before_any_row_exists(
    api_client, staff, taxonomy, phase2_template, settings
):
    settings.OPENROUTER_API_KEY = ""
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/", _create_body(phase2_template, taxonomy), format="json"
        )
    assert response.status_code == 503
    assert response.json()["code"] == "missing_api_key"
    assert "No OpenRouter API key" in str(response.json())
    assert PhaseRun.objects.count() == 0
    assert enqueued.call_count == 0


# ---------------------------------------------------------------------------
# Validation — reusing Experiment.clean()'s own reasoning.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_phase_2_run_needs_a_real_variant(api_client, staff, taxonomy, phase2_template):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/phase-runs/",
        _create_body(phase2_template, taxonomy, variant=""),
        format="json",
    )
    assert response.status_code == 400
    assert "variant" in response.json()
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_an_unknown_variant_is_refused_for_a_phase_2_run(
    api_client, staff, taxonomy, phase2_template
):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/phase-runs/",
        _create_body(phase2_template, taxonomy, variant="C"),
        format="json",
    )
    assert response.status_code == 400
    assert "variant" in response.json()


@pytest.mark.django_db
def test_a_phase_3_run_needs_a_source_phase_run(api_client, staff, taxonomy, phase3_template):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/phase-runs/",
        {"phase": 3, "model": "m3", "prompt_template": phase3_template.pk, "taxonomy": taxonomy.pk},
        format="json",
    )
    assert response.status_code == 400
    assert "source_phase_run" in response.json()
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_a_phase_3_source_must_itself_be_a_phase_2_run(
    api_client, staff, taxonomy, phase3_template
):
    """`source_phase_run` is where the scenarios to label come from
    (`pipeline.labelling.scenarios_for`); a phase-3 run has no generated
    scenarios of its own to point another phase-3 run at."""
    other_labelling = PhaseRun.objects.create(
        phase=3, model="m3", prompt_template=phase3_template, taxonomy=taxonomy
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/phase-runs/",
        {
            "phase": 3,
            "model": "m3",
            "prompt_template": phase3_template.pk,
            "taxonomy": taxonomy.pk,
            "source_phase_run": other_labelling.pk,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "source_phase_run" in response.json()
    assert "phase 2" in str(response.json())


@pytest.mark.django_db
def test_a_phase_3_run_refuses_a_garbage_variant(
    api_client, staff, taxonomy, phase2_template, phase3_template
):
    """Coordinator review round, finding 5: phase 3 skipped the variant
    check entirely, so `variant="C"` (or any free string) reached 202 and
    was archived — `PhaseRun.variant` is a plain `CharField`, no DB-level
    choices constraint. Harmless to isolation (phase 3 never branches on
    it), but it surfaces in every export, so it must mean something
    recognisable there — blank still allowed, garbage is not.
    """
    source = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=phase2_template,
        taxonomy=taxonomy,
        status=PhaseRun.Status.COMPLETED,
    )
    api_client.force_authenticate(user=staff)

    response = api_client.post(
        "/api/phase-runs/",
        {
            "phase": 3,
            "variant": "C",
            "model": "m3",
            "prompt_template": phase3_template.pk,
            "taxonomy": taxonomy.pk,
            "source_phase_run": source.pk,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "variant" in response.json()
    assert PhaseRun.objects.filter(phase=3).count() == 0


@pytest.mark.django_db
def test_a_phase_3_run_still_permits_a_blank_variant(
    api_client, staff, taxonomy, phase2_template, phase3_template
):
    """The permissive half of the same fix: blank stays legitimate for
    phase 3 (an ad-hoc labelling run need not record which variant it
    labels) — only genuine garbage is refused."""
    source = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=phase2_template,
        taxonomy=taxonomy,
        status=PhaseRun.Status.COMPLETED,
    )
    api_client.force_authenticate(user=staff)

    with patch("pipeline.orchestration.async_task"):
        response = api_client.post(
            "/api/phase-runs/",
            {
                "phase": 3,
                "model": "m3",
                "prompt_template": phase3_template.pk,
                "taxonomy": taxonomy.pk,
                "source_phase_run": source.pk,
            },
            format="json",
        )

    assert response.status_code == 202
    assert response.json()["variant"] == ""


@pytest.mark.django_db
def test_the_prompt_template_must_be_pinned_to_the_requested_phases_shaped_work(
    api_client, staff, taxonomy, phase3_template
):
    """A phase-3 (labelling) template pinned to a phase-2 (generation)
    request validated fine and only surfaced later as a baffling render or a
    wasted run — the same rule `Experiment.clean()` enforces for
    `phase2_prompt`/`phase3_prompt`, reused here via
    `template_phase_mismatch_error`."""
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/phase-runs/",
        _create_body(phase3_template, taxonomy),  # phase=2 (default), template is phase 3
        format="json",
    )
    assert response.status_code == 400
    assert "phase 3" in str(response.json())
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_the_taxonomy_must_have_hcv_rows(api_client, staff, empty_taxonomy, phase2_template):
    """Variant A's leak backstop cannot run at all against a taxonomy with
    zero rows — refused before a row exists, the same as
    `Experiment.clean()` already refuses an experiment pinned to one."""
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/phase-runs/",
        _create_body(phase2_template, empty_taxonomy),
        format="json",
    )
    assert response.status_code == 400
    assert "HCV" in str(response.json())
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_a_taxonomy_of_content_free_rows_is_refused_too(api_client, staff, phase2_template):
    """Round 4, leak 1, at the ad-hoc create seam. A taxonomy with rows that
    carry no `hcv_id`/`name`/`definition` passed the old `.exists()`
    precondition while giving variant A's backstop nothing to scan for — it
    then ran, matched nothing, and archived `leak_scan_ran=True` over a
    prompt with real HCV text in it. Refused here with zero rows created,
    the same way a zero-row taxonomy already was.

    `is_active=True` so the newer "must be the active taxonomy" rule
    (`unbound_guard_taxonomy_error`) cannot refuse this for the wrong
    reason first — this test is about the SCANNABILITY precondition, in
    isolation.
    """
    blank = Taxonomy.objects.create(vocabulary_id="HCV-BLANK-ITER", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=blank, hcv_id="", name="", definition="", order=0)
    api_client.force_authenticate(user=staff)

    response = api_client.post(
        "/api/phase-runs/",
        _create_body(phase2_template, blank),
        format="json",
    )

    assert response.status_code == 400
    assert "content-free" in str(response.json())
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_no_experiment_and_an_inactive_taxonomy_is_refused(api_client, staff, phase2_template):
    """Coordinator review round 3, finding 1 (CRITICAL) — the "no
    experiment" half: `taxonomy_missing_rows_error` alone let ANY real,
    non-empty taxonomy through, active or not. With no experiment to
    derive from, the guard taxonomy must still not be a free choice — it
    must be whichever taxonomy is currently ACTIVE.
    """
    inactive = Taxonomy.objects.create(vocabulary_id="HCV-INACTIVE-ITER", version="1.0")
    HCV.objects.create(taxonomy=inactive, hcv_id="HCV-01", name="Has rows", order=1)
    api_client.force_authenticate(user=staff)

    response = api_client.post(
        "/api/phase-runs/",
        _create_body(phase2_template, inactive),
        format="json",
    )

    assert response.status_code == 400
    assert "taxonomy" in response.json()
    assert "active" in str(response.json()).lower()
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_borrowing_an_experiment_silently_overrides_a_mismatched_submitted_taxonomy(
    api_client, staff, borrowable_experiment
):
    """Deriving beats validating: a client submitting a taxonomy that
    DISAGREES with the borrowed experiment's own is not REJECTED — it is
    silently overridden, because the field is structurally not theirs to
    choose once `experiment` is given (see `unbound_guard_taxonomy_error`'s
    own docstring — "a field that cannot be wrong beats one that is
    checked")."""
    wrong = Taxonomy.objects.create(vocabulary_id="HCV-WRONG-ITER", version="1.0")
    HCV.objects.create(taxonomy=wrong, hcv_id="HCV-01", name="Not this one", order=1)
    assert wrong.pk != borrowable_experiment.taxonomy_id  # sanity: genuinely different
    api_client.force_authenticate(user=staff)

    with patch("pipeline.orchestration.async_task"):
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(
                borrowable_experiment.phase2_prompt,
                wrong,
                experiment=borrowable_experiment.pk,
            ),
            format="json",
        )

    assert response.status_code == 202
    assert response.json()["taxonomy"] == borrowable_experiment.taxonomy_id
    phase_run = PhaseRun.objects.get(pk=response.json()["id"])
    assert phase_run.taxonomy_id == borrowable_experiment.taxonomy_id


@pytest.mark.django_db
def test_both_generation_toggles_off_is_refused_at_create_time_with_zero_calls(
    api_client, staff, borrowable_experiment
):
    """Coordinator review round 3, finding 2: `launch()` refuses this with
    ZERO calls made — before this fix, the ad-hoc borrow path checked
    neither this rule nor `target_system`-required, so a probe against
    either returned 202 and SPENT one real provider call before failing on
    response validation.
    """
    borrowable_experiment.generate_systems = False
    borrowable_experiment.generate_scenarios = False
    borrowable_experiment.save(update_fields=["generate_systems", "generate_scenarios"])
    api_client.force_authenticate(user=staff)

    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(
                borrowable_experiment.phase2_prompt,
                borrowable_experiment.taxonomy,
                experiment=borrowable_experiment.pk,
            ),
            format="json",
        )

    assert response.status_code == 400
    assert "experiment" in response.json()
    assert "at least one" in str(response.json())
    assert PhaseRun.objects.count() == 0
    assert enqueued.call_count == 0


@pytest.mark.django_db
def test_missing_target_when_not_generating_is_refused_at_create_time_with_zero_calls(
    api_client, staff, borrowable_experiment
):
    """The second unported rule, same round — see the previous test's
    docstring for the "real, wasted provider call" this closes."""
    borrowable_experiment.generate_systems = False
    borrowable_experiment.target_system = None
    borrowable_experiment.save(update_fields=["generate_systems", "target_system"])
    api_client.force_authenticate(user=staff)

    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(
                borrowable_experiment.phase2_prompt,
                borrowable_experiment.taxonomy,
                experiment=borrowable_experiment.pk,
            ),
            format="json",
        )

    assert response.status_code == 400
    assert "experiment" in response.json()
    assert "target_system" in str(response.json())
    assert PhaseRun.objects.count() == 0
    assert enqueued.call_count == 0


@pytest.mark.django_db
def test_a_phase_3_borrow_is_not_refused_for_a_generated_target_system(
    api_client, staff, borrowable_experiment
):
    """Coordinator review round 3, finding 5 (deliberate OVER-enforcement
    fix): `_adhoc_labelling_fewshots` reads neither `seed_systems` nor
    `target_system` at all — a phase-3 ad-hoc run borrowing an experiment
    whose `target_system` happens to be GENERATED-origin must NOT be
    refused for it, unlike the phase-2 case
    (`test_borrowing_a_contaminated_target_system_is_refused_at_create_time`),
    which correctly still is. Same for the generation toggles: this
    experiment's `generate_systems`/`generate_scenarios` are left at their
    (mismatched-for-targeting) defaults on purpose, to prove phase 3 does
    not care about them either.
    """
    generated_target = SystemDescription.objects.create(
        title="Target From a Model", body="x", origin=Origin.GENERATED
    )
    borrowable_experiment.target_system = generated_target
    borrowable_experiment.save(update_fields=["target_system"])
    source = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=borrowable_experiment.phase2_prompt,
        taxonomy=borrowable_experiment.taxonomy,
        status=PhaseRun.Status.COMPLETED,
    )
    api_client.force_authenticate(user=staff)

    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            {
                "phase": 3,
                "model": "m3",
                "prompt_template": borrowable_experiment.phase3_prompt.pk,
                "experiment": borrowable_experiment.pk,
                "source_phase_run": source.pk,
            },
            format="json",
        )

    assert response.status_code == 202
    assert enqueued.call_count == 1
    phase_run = PhaseRun.objects.get(pk=response.json()["id"])
    assert phase_run.taxonomy_id == borrowable_experiment.taxonomy_id


# ---------------------------------------------------------------------------
# Successful creation: pipeline_run stays null, enqueued through queue_phase_run.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_created_phase_2_run_leaves_pipeline_run_null_and_is_queued(
    api_client, staff, taxonomy, phase2_template
):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(phase2_template, taxonomy, temperature=0.7),
            format="json",
        )
    assert response.status_code == 202
    body = response.json()
    assert body["pipeline_run"] is None
    assert body["status"] == "queued"
    assert body["phase"] == 2
    assert body["variant"] == "A"

    phase_run = PhaseRun.objects.get(pk=body["id"])
    assert phase_run.pipeline_run_id is None
    assert phase_run.status == PhaseRun.Status.QUEUED
    assert phase_run.temperature == 0.7
    # Enqueued through queue_phase_run's dispatch table (PHASE_RUN_TASKS),
    # not a hand-rolled call — the exact literal `pipeline.tasks` itself uses.
    assert enqueued.call_args.args == ("pipeline.tasks.run_phase2_task", phase_run.pk)


@pytest.mark.django_db
def test_a_created_phase_3_run_records_its_source_and_is_queued(
    api_client, staff, taxonomy, phase2_template, phase3_template
):
    source = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=phase2_template,
        taxonomy=taxonomy,
        status=PhaseRun.Status.COMPLETED,
    )
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            {
                "phase": 3,
                "model": "m3",
                "prompt_template": phase3_template.pk,
                "taxonomy": taxonomy.pk,
                "source_phase_run": source.pk,
            },
            format="json",
        )
    assert response.status_code == 202
    phase_run = PhaseRun.objects.get(pk=response.json()["id"])
    assert phase_run.pipeline_run_id is None
    assert phase_run.source_phase_run_id == source.pk
    assert enqueued.call_args.args == ("pipeline.tasks.run_phase3_task", phase_run.pk)


@pytest.mark.django_db
def test_a_broker_failure_reverts_the_created_run_and_is_a_clear_503(
    api_client, staff, taxonomy, phase2_template
):
    """Mirrors `test_experiment_api
    .test_a_broker_failure_reverts_that_run_and_is_a_clear_503`: the row is
    NOT deleted (unlike the missing-key case, which never creates one at
    all) — it is reverted to PENDING so an operator can inspect or retry it,
    the same "reverted, not deleted" contract `queue_runs` already gives an
    orchestrated repetition."""
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task", side_effect=RuntimeError("no broker")):
        response = api_client.post(
            "/api/phase-runs/", _create_body(phase2_template, taxonomy), format="json"
        )
    assert response.status_code == 503
    assert response.json()["code"] == "broker_unavailable"
    phase_run = PhaseRun.objects.get()
    assert phase_run.status == PhaseRun.Status.PENDING


# ---------------------------------------------------------------------------
# Deletion: staff-only, refused in flight, generated material cascades.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_staff_deletes_a_finished_ad_hoc_run_and_its_generated_material_cascades(
    api_client, staff, taxonomy, phase2_template, _client
):
    """Spec §9: "Generated material is only removed by deleting the run
    that produced it, via DELETE /api/phase-runs/{id}/, which cascades" —
    the ONLY route for an ad-hoc run, which has no PipelineRun to delete
    instead."""
    generation = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=phase2_template, taxonomy=taxonomy
    )
    _client.queue(GENERATED)
    run_phase2(generation)
    assert SystemDescription.objects.filter(generation_run=generation).exists()
    assert Scenario.objects.filter(generation_run=generation).exists()
    assert LLMCall.objects.filter(phase_run=generation).exists()

    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/phase-runs/{generation.pk}/")

    assert response.status_code == 204
    assert not PhaseRun.objects.filter(pk=generation.pk).exists()
    assert not SystemDescription.objects.filter(generation_run_id=generation.pk).exists()
    assert not Scenario.objects.filter(generation_run_id=generation.pk).exists()
    assert not LLMCall.objects.filter(phase_run_id=generation.pk).exists()


@pytest.mark.django_db
def test_an_in_flight_ad_hoc_run_cannot_be_deleted(api_client, staff, taxonomy, phase2_template):
    generation = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=phase2_template, taxonomy=taxonomy
    )
    api_client.force_authenticate(user=staff)
    for state in (PhaseRun.Status.QUEUED, PhaseRun.Status.RUNNING):
        generation.status = state
        generation.save(update_fields=["status"])
        response = api_client.delete(f"/api/phase-runs/{generation.pk}/")
        assert response.status_code == 409
        assert response.json()["code"] == "run_in_flight"
        assert PhaseRun.objects.filter(pk=generation.pk).exists()


# ---------------------------------------------------------------------------
# THE isolation guarantee, proven through the brand-new path end to end.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_an_adhoc_variant_a_run_with_a_taxonomy_kind_resource_fails_with_zero_calls(
    api_client, staff, taxonomy, _client
):
    """An ad-hoc run has no `pipeline_run`, therefore no config snapshot —
    `resolve_generation_config`'s ad-hoc branch always resolves
    `seed_systems`/`fewshots` to `[]` (`_ADHOC_GENERATION_CONFIG`), so there
    is no slot through which an ad-hoc run's own few-shot list could ever
    carry a TAXONOMY-kind file — unlike an orchestrated run's pinned
    snapshot (see `test_generation
    .test_a_fewshot_referencing_a_taxonomy_resource_still_leaks_via_the_snapshot_path`
    for that equivalent, experiment-backed proof). What still applies,
    unconditionally, regardless of `pipeline_run`/config, is
    `prompts.rendering._make_resource`'s `resource()` template global —
    reachable from ANY phase-2 template. This proves that guard fires
    through the BRAND NEW path end to end: the real view creates the row,
    the real `queue_phase_run` hands it to django-q (captured here instead
    of actually reaching a broker), and the real `run_phase2_task` a worker
    would execute is invoked directly — FAILED, zero provider calls, zero
    archived `LLMCall` rows, exactly the bar `test_choke_point.py` holds the
    experiment-backed path to.
    """
    ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    leaky_template = PromptTemplate.objects.create_version(
        phase=2, name="leaky-adhoc", user_prompt='{{ resource("hcv_taxonomy") }}'
    )
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(leaky_template, taxonomy),
            format="json",
        )
    assert response.status_code == 202
    phase_run_id = response.json()["id"]
    task_name, queued_pk = enqueued.call_args.args
    assert task_name == "pipeline.tasks.run_phase2_task"
    assert queued_pk == phase_run_id

    # Simulate the worker actually picking up the queued task.
    result_status = run_phase2_task(queued_pk)

    assert result_status == PhaseRun.Status.FAILED
    phase_run = PhaseRun.objects.get(pk=phase_run_id)
    assert phase_run.status == PhaseRun.Status.FAILED
    assert "TaxonomyLeak" in phase_run.error
    assert phase_run.calls.count() == 0
    assert LLMCall.objects.filter(phase_run_id=phase_run_id).count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_a_legitimate_adhoc_variant_a_run_succeeds_with_isolation_evidence_recorded(
    api_client, staff, taxonomy, phase2_template, _client
):
    """Contrast case for the test above: the resource guard is not a blanket
    refusal of variant A — a clean ad-hoc run still sails through, and the
    final choke point's verdict (`leak_scan_ran`/`guarded_by`) is still
    recorded on the archived call, the same as an orchestrated run's."""
    _client.queue(GENERATED)
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        api_client.post("/api/phase-runs/", _create_body(phase2_template, taxonomy), format="json")
    _, queued_pk = enqueued.call_args.args

    result_status = run_phase2_task(queued_pk)

    assert result_status == PhaseRun.Status.COMPLETED
    phase_run = PhaseRun.objects.get(pk=queued_pk)
    call = phase_run.calls.get()
    assert call.leak_scan_ran is True
    assert call.guarded_by_id == taxonomy.pk
    assert len(_client.calls) == 1
    assert SystemDescription.objects.filter(generation_run=phase_run, title="Riverbend").exists()


@pytest.mark.django_db
def test_an_adhoc_phase_3_run_labels_its_sources_scenarios_through_the_new_path(
    api_client, staff, taxonomy, phase2_template, phase3_template, _client
):
    """The ordinary, happy iterate-mode path for phase 3: label a phase-2
    run's own output, end to end through create -> queue -> the real task."""
    source = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=phase2_template, taxonomy=taxonomy
    )
    _client.queue(GENERATED)
    run_phase2(source)
    assert source.status == PhaseRun.Status.COMPLETED

    _client.queue(LABELLED)
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            {
                "phase": 3,
                "model": "m3",
                "prompt_template": phase3_template.pk,
                "taxonomy": taxonomy.pk,
                "source_phase_run": source.pk,
            },
            format="json",
        )
    assert response.status_code == 202
    task_name, queued_pk = enqueued.call_args.args
    assert task_name == "pipeline.tasks.run_phase3_task"

    result_status = run_phase3_task(queued_pk)

    assert result_status == PhaseRun.Status.COMPLETED
    labelling = PhaseRun.objects.get(pk=queued_pk)
    assert labelling.completed_children == 1
    assert Label.objects.filter(phase_run=labelling, hcv__hcv_id="HCV-07").exists()


# ---------------------------------------------------------------------------
# Borrowing an experiment's LIVE inputs (coordinator follow-up).
#
# A bare ad-hoc run's config resolves to hard-coded defaults — no seeds, no
# few-shots, someone else's counts — which defeats the whole point of
# iterate mode: a researcher tuning a phase-2 prompt would be tuning against
# a prompt that does not resemble the real one. `experiment=` on the create
# payload lets an ad-hoc run borrow a real experiment's CURRENT, LIVE inputs
# instead, with optional `n_systems`/`n_scenarios_per_system` overrides for
# a cheap, one-system tuning run. Isolation applies unchanged — this section
# proves it end to end through the real create -> queue -> task path.
# ---------------------------------------------------------------------------


@pytest.fixture
def borrowable_experiment(db, taxonomy, phase2_template, phase3_template):
    """A real, saveable `Experiment` with real seeds, real active few-shots
    (phase 2 and phase 3) and its own distinct `n_systems`/
    `n_scenarios_per_system` — everything an ad-hoc run could borrow."""
    seed = SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)
    phase2_fewshot = FewShotExample.objects.create(
        phase=2, name="p2-example", input_text="in", output_text="out", is_active=True
    )
    phase3_fewshot = FewShotExample.objects.create(
        phase=3, name="p3-example", input_text="in3", output_text="out3", is_active=True
    )
    made = Experiment.objects.create(
        name="borrowable",
        taxonomy=taxonomy,
        phase2_model="m2",
        phase2_prompt=phase2_template,
        phase3_model="m3",
        phase3_prompt=phase3_template,
        n_systems=7,
        n_scenarios_per_system=9,
    )
    made.seed_systems.add(seed)
    made.fewshots.add(phase2_fewshot, phase3_fewshot)
    return made


@pytest.mark.django_db
def test_borrowing_an_experiment_renders_with_its_real_seeds_and_counts(
    api_client, staff, taxonomy, borrowable_experiment, _client
):
    """The actual researcher use case: real seeds/few-shots come through,
    with an override making this one cheap tuning call instead of the
    borrowed experiment's own 7x9. Uses a template that actually
    interpolates `seed_systems`/`n_systems`/`fewshots` — `phase2_template`'s
    stock body ("Write scenarios.") never would, so it would not
    distinguish "borrowing genuinely renders these" from "nothing errored".
    """
    interpolating_template = PromptTemplate.objects.create_version(
        phase=2,
        name="interpolates-borrowed-inputs",
        user_prompt=(
            "Write {{ n_systems }} systems.\n"
            "Seeds: {% for s in seed_systems %}{{ s.title }} {% endfor %}\n"
            "Fewshots: {% for f in fewshots %}{{ f.name }} {% endfor %}"
        ),
    )
    _client.queue(GENERATED)
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(
                interpolating_template,
                taxonomy,
                experiment=borrowable_experiment.pk,
                n_systems=1,
                n_scenarios_per_system=1,
            ),
            format="json",
        )
    assert response.status_code == 202
    assert response.json()["experiment"] == borrowable_experiment.pk
    _, queued_pk = enqueued.call_args.args

    result_status = run_phase2_task(queued_pk)

    assert result_status == PhaseRun.Status.COMPLETED
    call = PhaseRun.objects.get(pk=queued_pk).calls.get()
    # The override (1), not the borrowed experiment's own n_systems (7), is
    # what actually reached the prompt — and its real seed system/few-shot
    # both came through too.
    assert "Write 1 systems" in call.rendered_user_prompt
    assert "Oldsmar" in call.rendered_user_prompt
    assert "p2-example" in call.rendered_user_prompt


@pytest.mark.django_db
def test_a_zero_override_is_refused_not_silently_treated_as_no_override(
    api_client, staff, taxonomy, borrowable_experiment
):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/phase-runs/",
        _create_body(
            borrowable_experiment.phase2_prompt,
            taxonomy,
            experiment=borrowable_experiment.pk,
            n_systems=0,
        ),
        format="json",
    )
    assert response.status_code == 400
    assert "n_systems" in response.json()
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize("wild_value", [2**31, 10**12])
def test_a_wildly_large_override_is_refused_by_the_deliberate_tuning_cap(
    api_client, staff, taxonomy, borrowable_experiment, wild_value
):
    """Coordinator review round, finding 4 — CORRECTED in round 3: the
    ORIGINAL version of this test (and its docstring) claimed
    `n_systems=2**31`/`10**12` would overflow PostgreSQL's
    `PositiveIntegerField` into a live `NumericValueOutOfRange` 500. That
    was WRONG — `integer_field_ranges["PositiveIntegerField"]` is
    `(0, 2147483647)` on every Django database backend, and DRF's
    `IntegerField` already maps that to `max_value` automatically, so both
    values were already ordinary 400s before `_ADHOC_OVERRIDE_MAX` ever
    existed; there was no live crash to prevent. `_ADHOC_OVERRIDE_MAX = 50`
    is kept anyway, correctly reasoned this time: a deliberate PRODUCT
    choice for what a cheap TUNING run could plausibly want (ten times the
    real-experiment default of 3x5), not a database safety net — see
    `_ADHOC_OVERRIDE_MAX`'s own comment in `pipeline/serializers.py`.
    """
    api_client.force_authenticate(user=staff)

    response = api_client.post(
        "/api/phase-runs/",
        _create_body(
            borrowable_experiment.phase2_prompt,
            taxonomy,
            experiment=borrowable_experiment.pk,
            n_systems=wild_value,
        ),
        format="json",
    )

    assert response.status_code == 400
    assert "n_systems" in response.json()
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_borrowing_a_contaminated_experiment_is_refused_at_create_time(
    api_client, staff, taxonomy, borrowable_experiment
):
    """The fast, friendly 400 half of the fix — `Experiment.clean()`'s own
    guard never runs on an already-saved experiment being borrowed, so this
    is checked explicitly at create time too (not only, later, at
    execution — see the next test for that half)."""
    generated = SystemDescription.objects.create(
        title="From a model", body="x", origin=Origin.GENERATED
    )
    borrowable_experiment.seed_systems.add(generated)
    api_client.force_authenticate(user=staff)

    response = api_client.post(
        "/api/phase-runs/",
        _create_body(
            borrowable_experiment.phase2_prompt, taxonomy, experiment=borrowable_experiment.pk
        ),
        format="json",
    )

    assert response.status_code == 400
    assert "experiment" in response.json()
    assert "From a model" in str(response.json())
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_a_generated_seed_system_added_after_creation_still_fails_closed_at_execution(
    api_client, staff, taxonomy, borrowable_experiment, _client
):
    """The execution-time half: the create-time check above cannot see a
    contamination introduced AFTER the ad-hoc run was already queued — the
    whole point of borrowing LIVE is that the experiment can change in that
    window. `_adhoc_generation_config` re-checks the same rule at the point
    it actually renders, so the race is still closed, not merely narrowed —
    FAILED, zero provider calls, zero archived `LLMCall` rows.
    """
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(
                borrowable_experiment.phase2_prompt,
                taxonomy,
                experiment=borrowable_experiment.pk,
            ),
            format="json",
        )
    assert response.status_code == 202
    _, queued_pk = enqueued.call_args.args

    # The contamination arrives AFTER creation — simulating an operator
    # editing the experiment in the window before a worker picks this up.
    generated = SystemDescription.objects.create(
        title="From a model", body="x", origin=Origin.GENERATED
    )
    borrowable_experiment.seed_systems.add(generated)

    result_status = run_phase2_task(queued_pk)

    assert result_status == PhaseRun.Status.FAILED
    phase_run = PhaseRun.objects.get(pk=queued_pk)
    assert "BorrowedExperimentContaminated" in phase_run.error
    assert "From a model" in phase_run.error
    assert phase_run.calls.count() == 0
    assert LLMCall.objects.filter(phase_run_id=queued_pk).count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_borrowing_a_contaminated_target_system_is_refused_at_create_time(
    api_client, staff, taxonomy, borrowable_experiment
):
    """Coordinator review round, finding 1 (CRITICAL) — the create-time
    half for `target_system`, the route the first version of this fix
    missed entirely (only `seed_systems` was checked). Mirrors
    `test_borrowing_a_contaminated_experiment_is_refused_at_create_time`
    exactly, one field over.
    """
    generated_target = SystemDescription.objects.create(
        title="Target From a Model", body="x", origin=Origin.GENERATED
    )
    borrowable_experiment.target_system = generated_target
    borrowable_experiment.save(update_fields=["target_system"])
    api_client.force_authenticate(user=staff)

    response = api_client.post(
        "/api/phase-runs/",
        _create_body(
            borrowable_experiment.phase2_prompt, taxonomy, experiment=borrowable_experiment.pk
        ),
        format="json",
    )

    assert response.status_code == 400
    assert "experiment" in response.json()
    assert "Target From a Model" in str(response.json())
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_the_reviewers_exact_probe_a_generated_target_system_no_longer_reaches_the_prompt(
    api_client, staff, taxonomy, borrowable_experiment, _client
):
    """THE critical finding, reproduced exactly as the reviewer described it,
    then proven fixed.

    Before this fix: a `SystemDescription` validly set as an experiment's
    `target_system` while `origin=SEED` gets flipped to `origin=GENERATED`
    AFTERWARDS — reachable with no ORM access at all, since
    `corpus.admin.SystemDescriptionAdmin` was a bare `ModelAdmin` with
    `origin` freely editable (now read-only — see `corpus/tests/test_admin
    .test_system_description_admin_origin_is_read_only`). An ad-hoc run
    borrowing that experiment then reported COMPLETED, made 1 real provider
    call, archived an `LLMCall` with `leak_scan_ran=True`, and its
    `rendered_user_prompt` carried the generated system's taxonomy-DERIVED
    prose — invisible in the results, because the render-time/final-request
    leak backstops scan for LITERAL taxonomy identifiers/names/definitions,
    and paraphrased, model-written prose trips neither.

    The flip happens AFTER the ad-hoc run is already created and queued —
    not before — the same race
    `test_a_generated_seed_system_added_after_creation_still_fails_closed_at_execution`
    proves for seeds: the create-time check (proven separately by
    `test_borrowing_a_contaminated_target_system_is_refused_at_create_time`)
    cannot see a contamination introduced after it already ran, so THIS is
    the scenario where only the execution-time re-check in
    `_adhoc_generation_config` stands between the admin-reachable flip and a
    real, contaminated provider call. Uses a template that actually
    interpolates `target_system.body` (so a real leak, if it reached the
    prompt, would be genuinely visible in `rendered_user_prompt`, not merely
    assumed). Asserts the FIXED behaviour: FAILED, zero provider calls, zero
    archived `LLMCall` rows.
    """
    seed_target = SystemDescription.objects.create(
        title="Riverbend Plant", body="A water treatment plant.", origin=Origin.SEED
    )
    borrowable_experiment.target_system = seed_target
    borrowable_experiment.save(update_fields=["target_system"])
    # `generate_systems` deliberately left at its default (`True`): the leak
    # does not depend on targeting mode at all —
    # `build_phase2_context`'s `target_system` key is unconditional, set
    # whenever a `target_system` is passed regardless of `generate_systems`
    # — so this proves the contamination reaches the prompt either way, not
    # only in the narrower "targeting, not generating" configuration.

    leaky_template = PromptTemplate.objects.create_version(
        phase=2,
        name="interpolates-target-system-body",
        user_prompt="Write scenarios for: {{ target_system.body }}",
    )
    _client.queue(GENERATED)  # would be consumed if the leak were not caught
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(
                leaky_template,
                taxonomy,
                experiment=borrowable_experiment.pk,
                n_systems=1,
                n_scenarios_per_system=1,
            ),
            format="json",
        )
    assert response.status_code == 202
    phase_run_id = response.json()["id"]
    task_name, queued_pk = enqueued.call_args.args
    assert task_name == "pipeline.tasks.run_phase2_task"

    # The admin-reachable flip, AFTER creation: origin changed with nothing
    # re-validating it (this is exactly what `readonly_fields` on
    # `SystemDescriptionAdmin` now prevents through the admin UI — this
    # `.save()` is the underlying write that guard exists to block).
    seed_target.origin = Origin.GENERATED
    seed_target.save(update_fields=["origin"])

    result_status = run_phase2_task(queued_pk)

    assert result_status == PhaseRun.Status.FAILED
    phase_run = PhaseRun.objects.get(pk=phase_run_id)
    assert "BorrowedExperimentContaminated" in phase_run.error
    assert "Riverbend Plant" in phase_run.error
    assert phase_run.calls.count() == 0
    assert LLMCall.objects.filter(phase_run_id=phase_run_id).count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_the_reviewers_exact_probe_a_decoy_taxonomy_no_longer_reaches_the_prompt(
    borrowable_experiment, _client
):
    """THE critical finding of the THIRD review round, reproduced exactly
    as the reviewer described it, then proven fixed.

    Before this fix: an ad-hoc `PhaseRun` could be pinned to ANY taxonomy
    with at least one row (`taxonomy_missing_rows_error` alone) — including
    a 1-row DECOY sharing no content with the borrowed experiment's real
    one. `resource()`'s variant-A guard only refuses TAXONOMY-kind files —
    an `OTHER`-kind one carrying real taxonomy content verbatim
    ("HCV-07 Malicious Insiders: An insider abuses trust.") sails straight
    through it, and the render-time backstop then scans that text against
    the DECOY's own rows — which do not include HCV-07 — and finds
    NOTHING WRONG. The run reports COMPLETED, makes a real provider call,
    and archives `leak_scan_ran=True, guarded_by=<decoy>`: false evidence.
    The byte-identical run pinned to the REAL taxonomy (which DOES define
    HCV-07) fails closed at render time instead, with zero calls — the
    contrast that proves this was never about `resource()` being wrong,
    only about which list the scan ran against.

    `PhaseRunCreateSerializer` no longer lets this state be REACHED via the
    API at all (`taxonomy` is derived from `experiment.taxonomy` outright
    when borrowing) — so this constructs the `PhaseRun` directly, the same
    way an admin hand-edit or a future regression could still produce it,
    and proves `_check_adhoc_taxonomy_binding`'s execution-time re-check is
    what actually stands between that state and a real, contaminated
    provider call — not merely the create-time path being narrower now.
    """
    decoy = Taxonomy.objects.create(vocabulary_id="HCV-DECOY-PROBE", version="1.0")
    HCV.objects.create(taxonomy=decoy, hcv_id="HCV-99", name="Unrelated Decoy Row", order=99)
    other_resource = ResourceFile.objects.create(
        name="not-tagged-as-taxonomy",
        kind=ResourceKind.OTHER,
        text="HCV-07 Malicious Insiders: An insider abuses trust.",
    )
    leaky_template = PromptTemplate.objects.create_version(
        phase=2,
        name="reads-an-other-kind-resource",
        user_prompt='Context: {{ resource("not-tagged-as-taxonomy") }}',
    )
    phase_run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=leaky_template,
        taxonomy=decoy,
        experiment=borrowable_experiment,
    )
    assert phase_run.taxonomy_id != borrowable_experiment.taxonomy_id  # sanity: genuine decoy

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.FAILED
    assert "UnboundGuardTaxonomy" in result.error
    assert result.calls.count() == 0
    assert LLMCall.objects.filter(phase_run_id=phase_run.pk).count() == 0
    assert _client.calls == []
    # Every ResourceFile row this test created is real and untouched — the
    # sanity check that `resource("not-tagged-as-taxonomy")` really would
    # have carried the leak, had nothing stopped it.
    assert "HCV-07" in other_resource.text


@pytest.mark.django_db
def test_borrowing_an_experiment_with_a_taxonomy_kind_fewshot_fails_with_zero_calls(
    api_client, staff, taxonomy, borrowable_experiment, _client
):
    """THE probe the coordinator asked for: unlike a bare ad-hoc run (which
    structurally cannot carry a few-shot at all), a run borrowing an
    experiment DOES render with that experiment's real, active few-shots —
    so a TAXONOMY-kind one must still be refused, through the real path,
    with zero provider calls and zero archived `LLMCall` rows, exactly the
    bar the equivalent experiment-backed proof
    (`test_generation.test_a_fewshot_referencing_a_taxonomy_resource_still_leaks_via_the_snapshot_path`)
    and the bare-ad-hoc `resource()` proof above are both held to.
    """
    resource = ResourceFile.objects.create(
        name="hcv_taxonomy_borrow", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    leaky = FewShotExample.objects.create(
        phase=2, name="leaky-borrowed", input_file=resource, output_text="ok", is_active=True
    )
    borrowable_experiment.fewshots.add(leaky)

    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        response = api_client.post(
            "/api/phase-runs/",
            _create_body(
                borrowable_experiment.phase2_prompt,
                taxonomy,
                experiment=borrowable_experiment.pk,
            ),
            format="json",
        )
    assert response.status_code == 202
    task_name, queued_pk = enqueued.call_args.args
    assert task_name == "pipeline.tasks.run_phase2_task"

    result_status = run_phase2_task(queued_pk)

    assert result_status == PhaseRun.Status.FAILED
    phase_run = PhaseRun.objects.get(pk=queued_pk)
    assert "leaky-borrowed" in phase_run.error
    assert "TaxonomyLeak" in phase_run.error
    assert phase_run.calls.count() == 0
    assert LLMCall.objects.filter(phase_run_id=queued_pk).count() == 0
    assert _client.calls == []


@pytest.mark.django_db
def test_an_adhoc_run_borrowing_an_experiment_is_still_excluded_from_its_export(
    api_client, staff, taxonomy, borrowable_experiment, _client
):
    """The user decision holds even when the ad-hoc run borrows the SAME
    experiment it renders alongside: `pipeline.export._phase_run_ids` scopes
    on `pipeline_run__experiment`, which this run's null `pipeline_run_id`
    still never matches, `experiment` FK notwithstanding — borrowing an
    experiment's inputs must not make the run archivable.
    """
    from pipeline.export import build_bundle

    _client.queue(GENERATED)
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task") as enqueued:
        api_client.post(
            "/api/phase-runs/",
            _create_body(
                borrowable_experiment.phase2_prompt,
                taxonomy,
                experiment=borrowable_experiment.pk,
                n_systems=1,
                n_scenarios_per_system=1,
            ),
            format="json",
        )
    _, queued_pk = enqueued.call_args.args
    run_phase2_task(queued_pk)
    adhoc_run = PhaseRun.objects.get(pk=queued_pk)
    assert adhoc_run.status == PhaseRun.Status.COMPLETED  # sanity: real material was produced
    assert adhoc_run.pipeline_run_id is None

    bundle = build_bundle(borrowable_experiment)

    assert queued_pk not in {row["phase_run"] for row in bundle["runs"]}
    assert bundle["systems"] == []
    assert bundle["scenarios"] == []


@pytest.mark.django_db
def test_an_experiment_cannot_be_deleted_while_an_adhoc_run_still_borrows_it(
    api_client, staff, taxonomy, borrowable_experiment
):
    """`ExperimentViewSet.perform_destroy`'s new guard — the API-level proof
    that `PhaseRun.experiment`'s `on_delete=PROTECT` (see
    `test_execution_config
    .test_an_experiment_borrowed_by_an_adhoc_run_cannot_be_deleted` for the
    model-level proof, and the same test file for a probe that this really
    is what PROTECT buys, not incidental) surfaces as a friendly 409, not a
    bare `ProtectedError` 500.

    Coordinator review round, finding 3: the FIRST version of this 409
    named the route (`DELETE /api/phase-runs/{id}/`) but not which
    `{id}`s — an operator had a 409 and no way to act on it, since
    `?experiment=` (see the filter test below) used to return 0 for the
    exact ad-hoc run causing the conflict. The message must name the
    blocking id(s) so it is actionable on its own.
    """
    blocker = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=borrowable_experiment.phase2_prompt,
        taxonomy=taxonomy,
        experiment=borrowable_experiment,
    )
    api_client.force_authenticate(user=staff)

    response = api_client.delete(f"/api/experiments/{borrowable_experiment.pk}/")

    assert response.status_code == 409
    assert response.json()["code"] == "experiment_has_adhoc_runs"
    assert str(blocker.pk) in str(response.json())
    assert Experiment.objects.filter(pk=borrowable_experiment.pk).exists()


@pytest.mark.django_db
def test_experiment_filter_finds_an_adhoc_run_borrowing_it(
    api_client, member, taxonomy, borrowable_experiment
):
    """Coordinator review round, finding 3: `?experiment=` used to filter
    only `pipeline_run__experiment_id`, which an ad-hoc run's null
    `pipeline_run` never matches — the 409 above told an operator to go
    find and delete the blocking run via this exact filter, and it
    returned 0 results for it. An orchestrated run (via `pipeline_run`) and
    an ad-hoc one borrowing the SAME experiment (via `PhaseRun.experiment`)
    must both be found by the identical query, since both really do belong
    to "every phase run of experiment X".
    """
    borrowed = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=borrowable_experiment.phase2_prompt,
        taxonomy=taxonomy,
        experiment=borrowable_experiment,
    )
    # Noise: a phase run belonging to nothing this filter should match.
    other_taxonomy = Taxonomy.objects.create(vocabulary_id="HCV-NOISE-FILTER", version="1.0")
    other_template = PromptTemplate.objects.create_version(
        phase=2, name="noise-gen", user_prompt="g"
    )
    PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=other_template, taxonomy=other_taxonomy
    )
    api_client.force_authenticate(user=member)

    response = api_client.get(f"/api/phase-runs/?experiment={borrowable_experiment.pk}")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["id"] == borrowed.pk
