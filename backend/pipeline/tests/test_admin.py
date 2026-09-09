import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from pipeline.admin import ExperimentAdmin, LLMCallAdmin, PhaseRunAdmin, PipelineRunAdmin
from pipeline.models import Experiment
from pipeline.orchestration import launch
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()


def test_phase_run_admin_lists_the_progress_counters():
    """M10: the admin listed failed_children alone, from before
    total_children/completed_children (and the progress property) existed —
    drift that left the two most useful counters, and the summary dict built
    from all three, invisible in the one place an operator is most likely to
    go looking for them."""
    assert "total_children" in PhaseRunAdmin.list_display
    assert "completed_children" in PhaseRunAdmin.list_display
    assert "failed_children" in PhaseRunAdmin.list_display
    assert "progress" in PhaseRunAdmin.list_display


def test_phase_run_admin_has_an_experiment_column_and_filter():
    """Coordinator review round, finding 3: with no column or filter here,
    the admin had NO navigable path at all to the ad-hoc (iterate-mode)
    phase runs borrowing an experiment's live inputs (`PhaseRun.experiment`
    — see that field's own comment) — exactly the runs an operator needs to
    find and delete before `ExperimentViewSet.perform_destroy`'s
    `experiment_has_adhoc_runs` 409 will let them remove the experiment."""
    assert "experiment" in PhaseRunAdmin.list_display
    filter_fields = {f if isinstance(f, str) else f[0] for f in PhaseRunAdmin.list_filter}
    assert "experiment" in filter_fields


def test_phase_run_admin_pins_the_fields_launch_pins():
    """Round 4, leak 3: the ORCHESTRATED path was left weaker than the
    ad-hoc one. `launch()` pins `phase`/`variant`/`model`/`prompt_template`/
    `taxonomy` onto every `PhaseRun` precisely so they are the experiment's
    own — `taxonomy` above all, since it IS the list variant A's leak
    backstop scans a prompt against. The ad-hoc path gained a live binding
    re-check a round earlier (`_check_adhoc_taxonomy_binding`); the
    orchestrated path never had one, and `PhaseRun.taxonomy` is not in the
    `PipelineRun.config` snapshot either, so `validate_snapshot` never saw
    it. A bare `ModelAdmin` therefore let a PENDING variant-A run's guard
    taxonomy be repointed at a 1-row decoy through the change form, with
    the run then completing and archiving `leak_scan_ran=True,
    guarded_by=<decoy>` over a leaked prompt.

    The HTTP-level proof (the form ignores the submitted value, and the
    un-repointed run then fails closed with zero calls) is in
    `pipeline/tests/test_guard_evidence.py`."""
    for field in ("phase", "variant", "model", "prompt_template", "taxonomy"):
        assert field in PhaseRunAdmin.readonly_fields


def test_phase_run_admin_has_no_add_form():
    """Leak 1's lesson, applied to leak 3's fix. Read-only fields are
    EXCLUDED from a ModelForm, not merely disabled in it — which is exactly
    how the previous round's HCV admin fix turned a validating add form
    into one that created blank rows. With five required fields read-only,
    an add form here would create a `PhaseRun` with no phase, model,
    template or taxonomy at all. A `PhaseRun` comes from `launch()` or
    `POST /api/phase-runs/`; there is no admin workflow that needs to make
    one by hand."""
    assert PhaseRunAdmin.has_add_permission(PhaseRunAdmin, request=None) is False


def test_pipeline_run_admin_config_is_read_only():
    """Round 6 review, finding 1: `PipelineRunAdmin` was a bare `ModelAdmin`,
    so its JSONField `config` — the pinned snapshot both execution and
    `validate_snapshot` trust as authoritative — rendered as an editable
    textarea in the admin. That was a live hand-edit route to a dishonest
    snapshot (e.g. a lying `input_resource_kind`) that value validation
    alone cannot distinguish from an honest one, since a hand edit produces
    the identical shape. Read-only closes the write path instead."""
    assert "config" in PipelineRunAdmin.readonly_fields


def test_llm_call_admin_leak_scan_evidence_is_read_only():
    """The same hole as `PipelineRunAdmin.config`, closed the same way, and
    it matters more here: `LLMCall.leak_scan_ran`/`guarded_by` are the
    archived PROOF the taxonomy-isolation checks ran on this call — since
    `pipeline.generation._call_and_persist` wires in the final-request
    choke point unconditionally, a persisted `leak_scan_ran=True` on a
    phase-2 row certifies BOTH the render-time backstop and the
    final-request scan ran clean. A bare `ModelAdmin` would render both as
    editable widgets, a live hand-edit route to falsely claiming a call was
    scanned when it never was — read-only closes the write path instead of
    trusting nobody uses it."""
    assert "leak_scan_ran" in LLMCallAdmin.readonly_fields
    assert "guarded_by" in LLMCallAdmin.readonly_fields


@pytest.fixture
def request_from_a_superuser():
    request = RequestFactory().get("/admin/pipeline/experiment/")
    request.user = User(email="admin@example.com", is_staff=True, is_superuser=True)
    return request


@pytest.fixture
def experiment_admin():
    return ExperimentAdmin(Experiment, admin.site)


@pytest.fixture
def experiment(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    gen = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    return Experiment.objects.create(
        name="baseline",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=gen,
        phase2_prompt_b=gen,
        phase3_model="m3",
        phase3_prompt=lab,
    )


@pytest.mark.django_db
def test_a_never_run_experiment_can_be_deleted_from_the_admin(
    experiment_admin, request_from_a_superuser, experiment
):
    assert experiment_admin.has_delete_permission(request_from_a_superuser, experiment) is True


@pytest.mark.django_db
def test_an_experiment_with_runs_cannot_be_deleted_from_the_admin(
    experiment_admin, request_from_a_superuser, experiment, settings
):
    """The same data-destruction hole finding 4's API 409 closed, through the
    admin: Experiment.delete() cascades away every PipelineRun, PhaseRun,
    generated system/scenario and LLMCall for it."""
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-key"
    launch(experiment, n_repetitions=1)

    assert experiment_admin.has_delete_permission(request_from_a_superuser, experiment) is False


@pytest.mark.django_db
def test_the_bulk_delete_action_is_not_globally_disabled(
    experiment_admin, request_from_a_superuser
):
    """`obj=None` is Django's own probe for whether deletion is possible at
    all — used to decide whether to even show 'Delete selected' in the
    dropdown — and must not be conflated with the per-object run check, or
    the action would vanish for every experiment, including ones with no
    runs at all."""
    assert experiment_admin.has_delete_permission(request_from_a_superuser, None) is True
