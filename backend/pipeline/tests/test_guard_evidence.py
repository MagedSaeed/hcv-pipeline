"""Round 4: the three leaks, each reproduced end to end and then pinned.

All three shared one signature, and it is the reason none of them could
have been caught by looking at results: a variant-A run **COMPLETED**, made
a real provider call, and archived `LLMCall.leak_scan_ran=True` with a
`guarded_by` taxonomy — while the rendered prompt carried real HCV text.
The scan genuinely ran. It genuinely found nothing. It was pointed at the
wrong list (leaks 2 and 3) or at a list with no content in it (leak 1).
`LLMCallAdmin`'s own comment calls that field "the archived PROOF that the
taxonomy-isolation checks ran", so a `True` there over a leaked prompt is
worse than no scan at all: an operator, the export and every downstream
consumer read it as evidence.

Each test below therefore asserts the FULL post-fix outcome, not merely
that something raised — status FAILED, `phase_run.calls.count() == 0`, zero
`LLMCall` rows for the run, and an untouched `FakeLLMClient` (`calls == []`,
so nothing was even attempted at the provider layer) — the same bar
`test_choke_point.py` and `test_iterate_mode.py`'s own probes are held to.
Each also asserts the leak really WOULD have been visible (`"HCV-07" in
resource.text`, and a template that genuinely interpolates it), so a test
cannot pass by never having had anything to leak.

The three routes into that one signature:

1. a taxonomy whose HCV rows carry no `hcv_id`/`name`/`definition` — rows
   exist, so the old `.exists()` precondition passed, but the scan skips
   every falsy field and so compares against nothing;
2. a SECOND taxonomy flagged active, which silently re-bound
   `Taxonomy.active()` — and every unborrowed ad-hoc run's guard — to a
   1-row decoy;
3. an orchestrated run's PINNED `taxonomy` repointed at a decoy through the
   admin, which nothing re-checked (the ad-hoc path had gained a live
   binding re-check a round earlier; the orchestrated path never had one).
"""

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from corpus.models import ResourceFile, ResourceKind
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.generation import run_phase2
from pipeline.models import Experiment, LLMCall, PhaseRun
from pipeline.orchestration import launch
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


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def superuser(db):
    return User.objects.create_user(
        email="root@example.com", password="pw-123456", is_staff=True, is_superuser=True
    )


@pytest.fixture
def admin_client(superuser):
    client = Client()
    client.force_login(superuser)
    return client


@pytest.fixture
def leaky_resource(db):
    """An `OTHER`-kind resource carrying real taxonomy text verbatim.

    `prompts.rendering._make_resource` refuses TAXONOMY-*kind* files for
    variant A; it says nothing about content, and correctly so — that is
    what the render-time and final-request SCANS are for. Which is the
    whole point of these three tests: each one disables, narrows or
    misdirects the scan rather than defeating the kind check.
    """
    return ResourceFile.objects.create(
        name="not-tagged-as-taxonomy",
        kind=ResourceKind.OTHER,
        text="HCV-07 Malicious Insiders: An insider abuses trust.",
    )


@pytest.fixture
def leaky_template(db, leaky_resource):
    return PromptTemplate.objects.create_version(
        phase=2,
        name="reads-an-other-kind-resource",
        user_prompt='Context: {{ resource("not-tagged-as-taxonomy") }}',
    )


def assert_refused_with_nothing_spent(result, fake_client):
    """The bar every isolation guard in this codebase is held to."""
    assert result.status == PhaseRun.Status.FAILED
    assert result.calls.count() == 0
    assert LLMCall.objects.filter(phase_run_id=result.pk).count() == 0
    assert fake_client.calls == []


# ---------------------------------------------------------------------------
# LEAK 1 — a taxonomy whose rows carry no scannable content.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_admin_can_no_longer_create_a_content_free_hcv_row(admin_client):
    """The reviewer's exact probe for leak 1, at the route that produced it.

    Round 3 made `HCVAdmin`'s `hcv_id`/`name`/`definition` read-only to stop
    hand-edits — and Django does not merely disable a read-only field's
    widget, it EXCLUDES the field from the ModelForm, taking
    `CharField(blank=False)`'s validation with it. `POST
    /admin/taxonomy/hcv/add/` therefore went from "refuses without an
    `hcv_id`" to "302, row created, every scannable field empty". A
    security fix that opened a hole is worth pinning at the HTTP level, not
    only as an attribute assertion: `HCVAdmin.readonly_fields` looked
    perfectly correct the whole time it was broken.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-BLANK", version="1.0", is_active=True)

    response = admin_client.post("/admin/taxonomy/hcv/add/", {"taxonomy": tax.pk, "order": 0})

    assert response.status_code == 403
    assert HCV.objects.count() == 0


@pytest.mark.django_db
def test_a_blank_row_taxonomy_cannot_guard_a_variant_a_run(_client, leaky_template, leaky_resource):
    """Leak 1 end to end: an ACTIVE taxonomy whose only row is content-free.

    Before the fix this run COMPLETED — `taxonomy_missing_rows_error` asked
    `.exists()` (rows, not row CONTENT) and passed it; `assert_no_taxonomy_leak`
    then scanned for every falsy `hcv_id`/`name`/`definition`, i.e. for
    nothing, found nothing, and returned True. Archived:
    `leak_scan_ran=True`, `guarded_by=<the blank taxonomy>`, with
    "HCV-07 Malicious Insiders" sitting in `rendered_user_prompt`.

    Constructed directly rather than through `POST /api/phase-runs/`,
    deliberately: the serializer's create-time check is only one of the two
    levels this was fixed at, and this test is for the other one — proof
    that the EXECUTION-time layers refuse it too, so the leak cannot come
    back through any route that reaches a `PhaseRun` some other way.
    """
    blank = Taxonomy.objects.create(vocabulary_id="HCV-BLANK", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=blank, hcv_id="", name="", definition="", order=0)
    phase_run = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=leaky_template, taxonomy=blank
    )
    _client.queue(GENERATED)

    result = run_phase2(phase_run)

    assert_refused_with_nothing_spent(result, _client)
    assert "TaxonomyLeak" in result.error
    # The leak was real and would have been visible: this is not a test that
    # passes because there was nothing to catch.
    assert "HCV-07" in leaky_resource.text


@pytest.mark.django_db
def test_one_blanked_row_among_good_ones_is_refused_too(_client, leaky_template):
    """The partial case, which "at least one scannable row" would have let
    through: 1 blanked row out of 3 narrows the guard silently rather than
    switching it off outright, and a narrowed guard still certifies itself.
    Nothing legitimate produces a content-free row (`import_taxonomy`
    refuses one; the admin no longer offers an add form), so refusing every
    one of them costs nothing real."""
    # Deliberately rows the leaky resource does NOT mention: if the good
    # rows matched its "HCV-07" text, the ordinary identifier scan would
    # catch the leak on its own and this test would prove nothing about the
    # blanked row. It has to fail for the RIGHT reason.
    tax = Taxonomy.objects.create(vocabulary_id="HCV-PARTIAL", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-01", name="Cognitive Overload", order=1)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-02", name="Alarm Fatigue", order=2)
    HCV.objects.create(taxonomy=tax, hcv_id="  ", name="", definition="  ", order=99)
    phase_run = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=leaky_template, taxonomy=tax
    )
    _client.queue(GENERATED)

    result = run_phase2(phase_run)

    assert_refused_with_nothing_spent(result, _client)


@pytest.mark.django_db
def test_a_fully_scannable_taxonomy_still_guards_normally(_client):
    """The contrast case, without which every test above would also pass
    against a blanket refusal. A clean taxonomy and a clean template still
    complete, still make their one call, and still archive
    `leak_scan_ran=True` — the evidence is only worth anything if it can
    still be earned."""
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    clean = PromptTemplate.objects.create_version(
        phase=2, name="clean", user_prompt="Write scenarios."
    )
    phase_run = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=clean, taxonomy=tax
    )
    _client.queue(GENERATED)

    result = run_phase2(phase_run)

    assert result.status == PhaseRun.Status.COMPLETED
    call = LLMCall.objects.get(phase_run_id=result.pk)
    assert call.leak_scan_ran is True
    assert call.guarded_by_id == tax.pk


# ---------------------------------------------------------------------------
# LEAK 2 — a second active taxonomy re-binding the guard.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_a_second_active_taxonomy_cannot_exist(_client):
    """Leak 2 at its root. With no experiment borrowed, an ad-hoc run's
    guard taxonomy is bound to `Taxonomy.active()` — and nothing enforced
    that only one row could be active, while `active()` itself simply took
    the newest. Creating a 1-row decoy and flagging it active therefore
    RE-BOUND every unborrowed variant-A run to it: the scan ran against the
    decoy's `HCV-99`, found no match for the real taxonomy's `HCV-07`, and
    archived `leak_scan_ran=True, guarded_by=<decoy>` over a leaked prompt.

    The invariant is enforced by the database (`only_one_active_taxonomy`,
    a partial unique index) rather than a `save()` override, so it holds
    against `QuerySet.update()`, `bulk_create`, `loaddata` and raw SQL
    alike — `import_taxonomy --activate` uses `.update()` for exactly the
    deactivation half of this, and a `save()` override would not have seen
    it.
    """
    from django.db import IntegrityError

    real = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=real, hcv_id="HCV-07", name="Malicious Insiders", order=7)

    with pytest.raises(IntegrityError):
        Taxonomy.objects.create(vocabulary_id="HCV-DECOY", version="1.0", is_active=True)


@pytest.mark.django_db
def test_activating_a_second_taxonomy_through_the_admin_is_refused(admin_client):
    """The route an operator would actually take. `TaxonomyAdmin` leaves
    `is_active` editable on purpose — activating a taxonomy is a real
    workflow — so the constraint has to surface as a FORM error rather than
    an IntegrityError 500. Django 4.1+'s `validate_constraints()` does that
    with the constraint's own `violation_error_message`, which is why the
    message is written for an operator and names the way out."""
    Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    other = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.2")

    response = admin_client.post(
        f"/admin/taxonomy/taxonomy/{other.pk}/change/",
        {
            "vocabulary_id": other.vocabulary_id,
            "version": other.version,
            "notes": "",
            "is_active": "on",
            "hcvs-TOTAL_FORMS": "0",
            "hcvs-INITIAL_FORMS": "0",
            "hcvs-MIN_NUM_FORMS": "0",
            "hcvs-MAX_NUM_FORMS": "0",
        },
    )

    assert response.status_code == 200  # redisplayed with errors, not a 302
    other.refresh_from_db()
    assert other.is_active is False
    assert "already active" in response.content.decode()


@pytest.mark.django_db(transaction=True)
def test_active_fails_closed_rather_than_picking_one(_client, leaky_template, leaky_resource):
    """If the invariant is violated ANYWAY — a dump restored from before the
    constraint existed, raw SQL, a `--skip-checks` data migration —
    `Taxonomy.active()` must not silently pick one. "Which taxonomy guarded
    this run" would then be a coin flip recorded as evidence.

    The constraint is dropped and restored around the probe, which is the
    honest way to reproduce "the constraint is somehow not there": faking
    it with a mock would only prove the mock works. `transaction=True`
    because SQLite's schema editor refuses to run inside an already-open
    atomic block (it has to disable foreign-key checks, which SQLite cannot
    do mid-transaction) — the DDL is real, not simulated, so it needs a
    real non-atomic connection.
    """
    from django.db import connection

    from taxonomy.models import MultipleActiveTaxonomies

    constraint = next(c for c in Taxonomy._meta.constraints if c.name == "only_one_active_taxonomy")
    real = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=real, hcv_id="HCV-07", name="Malicious Insiders", order=7)

    with connection.schema_editor(atomic=False) as editor:
        editor.remove_constraint(Taxonomy, constraint)
    try:
        decoy = Taxonomy.objects.create(vocabulary_id="HCV-DECOY", version="1.0", is_active=True)
        HCV.objects.create(taxonomy=decoy, hcv_id="HCV-99", name="Unrelated Decoy", order=99)
        assert Taxonomy.objects.filter(is_active=True).count() == 2

        with pytest.raises(MultipleActiveTaxonomies):
            Taxonomy.active()

        # And the run that would have been bound to the newer one fails
        # closed rather than being guarded by whichever `first()` returned.
        phase_run = PhaseRun.objects.create(
            phase=2, variant="A", model="m2", prompt_template=leaky_template, taxonomy=decoy
        )
        _client.queue(GENERATED)
        result = run_phase2(phase_run)
        assert_refused_with_nothing_spent(result, _client)
    finally:
        Taxonomy.objects.filter(vocabulary_id="HCV-DECOY").update(is_active=False)
        with connection.schema_editor(atomic=False) as editor:
            editor.add_constraint(Taxonomy, constraint)


# ---------------------------------------------------------------------------
# LEAK 3 — an orchestrated run's pinned taxonomy repointed through the admin.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_admin_cannot_repoint_a_pending_orchestrated_runs_taxonomy(
    admin_client, _client, leaky_template, leaky_resource, settings
):
    """Leak 3, reproduced through the admin and then pinned end to end.

    `launch()` pins `taxonomy`/`prompt_template`/`model`/`variant`/`phase`
    onto every `PhaseRun` precisely so they are the experiment's own. The
    ad-hoc path had gained a live binding re-check a round earlier
    (`_check_adhoc_taxonomy_binding`); the ORCHESTRATED path never had one,
    and `PhaseRun.taxonomy` is not part of the `PipelineRun.config`
    snapshot either, so `validate_snapshot`'s exact-shape check never saw
    it. With `PhaseRunAdmin` a bare `ModelAdmin`, a PENDING variant-A run's
    guard taxonomy could simply be repointed at a decoy through the change
    form, and the run then completed with the usual false evidence.

    The probe posts the change form and asserts the field is IGNORED, then
    runs the phase to prove what the un-repointed run does: fails closed on
    the real taxonomy's own `HCV-07`, with nothing spent. Both halves
    matter — a test that only checked `readonly_fields` would not have
    noticed if the form still accepted the value.
    """
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-key"
    real = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=real, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    decoy = Taxonomy.objects.create(vocabulary_id="HCV-DECOY", version="1.0")
    HCV.objects.create(taxonomy=decoy, hcv_id="HCV-99", name="Unrelated Decoy", order=99)
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    experiment = Experiment.objects.create(
        name="orchestrated",
        taxonomy=real,
        phase2_model="m2",
        phase2_prompt_a=leaky_template,
        phase2_prompt_b=leaky_template,
        phase3_model="m3",
        phase3_prompt=lab,
    )
    with patch("pipeline.orchestration.async_task"):
        launch(experiment, n_repetitions=1)
    phase_run = PhaseRun.objects.get(phase=2, variant="A", pipeline_run__experiment=experiment)
    assert phase_run.status == PhaseRun.Status.PENDING

    response = admin_client.post(
        f"/admin/pipeline/phaserun/{phase_run.pk}/change/",
        {
            "pipeline_run": phase_run.pipeline_run_id,
            "phase": phase_run.phase,
            "variant": phase_run.variant,
            "model": phase_run.model,
            "temperature": phase_run.temperature,
            "prompt_template": phase_run.prompt_template_id,
            "taxonomy": decoy.pk,
            "status": phase_run.status,
            "error": "",
            "total_children": 0,
            "completed_children": 0,
            "failed_children": 0,
        },
    )

    assert response.status_code == 302  # the form saves; the pinned field is ignored
    phase_run.refresh_from_db()
    assert phase_run.taxonomy_id == real.pk

    _client.queue(GENERATED)
    result = run_phase2(phase_run)

    assert_refused_with_nothing_spent(result, _client)
    assert "TaxonomyLeak" in result.error
    assert "HCV-07" in result.error


@pytest.mark.django_db
def test_a_phase_run_cannot_be_hand_created_in_the_admin(admin_client):
    """Leak 1's lesson applied to leak 3's fix, at the HTTP level.

    Making the five pinned fields read-only EXCLUDES them from the
    ModelForm — which is exactly how leak 1 was introduced one round
    earlier, turning a validating add form into one that creates blank
    rows. `PhaseRunAdmin.has_add_permission` is what stops that repeating
    here: a `PhaseRun` comes from `launch()` or `POST /api/phase-runs/`,
    both of which pin or validate those five fields, and there is no
    admin-side workflow that ever needed to create one by hand.
    """
    response = admin_client.post("/admin/pipeline/phaserun/add/", {})

    assert response.status_code == 403
    assert PhaseRun.objects.count() == 0
