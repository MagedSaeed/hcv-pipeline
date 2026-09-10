# Pipeline API and Export Implementation Plan (plan 3b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drive and observe the experiment over HTTP, and download a self-contained bundle carrying everything needed to reconstruct a run without database access.

**Architecture:** A DRF layer over the `pipeline` app built in plan 3a. Experiments are CRUD plus a `run` action that calls `orchestration.launch()` and enqueues; runs and phase runs are read-only with `retry` and `calls` actions; labels are read-only and filterable. The export assembles one bundle from already-stored rows — it computes nothing and derives nothing, because the spec makes it the archival record.

**Tech Stack:** Django 5.2.17 LTS, DRF 3.18, django-q2, pytest-django, ruff. Consumes plan 3a's `pipeline.orchestration` and `pipeline.models`.

**Spec:** `docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md` — §9 (API and Export contents) is the binding section.

**Builds on:** plans 1, 2 and 3a, all complete — 446 tests.

## Global Constraints

- Python 3.13, Django 5.2.17, DRF 3.18.0. Managed by **uv** — never `pip`, never edit `pyproject.toml`/`uv.lock`. Every command is `uv run …` from `backend/`.
- **ruff** formats and lints: finish each task with `uv run ruff format . && uv run ruff check --fix .`. Watch **DJ012**, **B017**, **F401/E402/F811**.
- Run **no** git state-changing commands. Read-only git is fine.
- DRF defaults: `SessionAuthentication`, `IsAuthenticated`, `PageNumberPagination`, `PAGE_SIZE = 50`. **List responses are `{count, next, previous, results}`** — assert on `["results"]`.
- **`is_staff` gates experiment creation and run launching** (spec §7). Any authenticated user may read.
- **Runs and calls are immutable over the API.** `PipelineRun`, `PhaseRun`, `LLMCall` and `Label` are read-only; the only writes are the `run` and `retry` actions. Generated material is already protected by plan 1's `ReadOnlyIfGenerated`.
- **No API key may appear in any response or in the export.** Redaction happens on write (plan 3a), but the export tests must assert it independently.
- Tests are pytest-django. **No test may require Redis or make a network call** — `FakeLLMClient` and a patched `async_task` cover both.

## Interfaces this plan consumes (verified present)

```
pipeline.orchestration  launch(experiment, n_repetitions=1, inline=False) -> list[PipelineRun]
                        execute_pipeline_run(pipeline_run, on_progress=None) -> PipelineRun
                        retry_failed(phase_run) -> PhaseRun
pipeline.models         Experiment(.progress), PipelineRun(.progress, .repetition_index,
                        .status), PhaseRun(.progress, .phase, .variant, .status,
                        .total_children, .completed_children, .failed_children,
                        .leak_scan_ran via calls), LLMCall(request_json, response_json,
                        raw_response, rendered_*_prompt, parsed_ok, parse_error,
                        unknown_hcv_ids, http_status, transport_error, model,
                        finish_reason, prompt_tokens, completion_tokens, cost,
                        latency_ms, leak_scan_ran, guarded_by), Label(hcv, evidence,
                        rationale, source, llm_call)
pipeline.tasks          run_pipeline_task(pipeline_run_id)
core.keys               MissingAPIKey
```

---

## File Structure

```
backend/pipeline/
  serializers.py      Experiment, PipelineRun, PhaseRun, LLMCall, Label serializers
  views.py            the four viewsets and their actions
  urls.py             router registrations
  export.py           bundle assembly — JSON and the CSV zip
  tests/  test_experiment_api.py  test_run_api.py  test_label_api.py  test_export.py
backend/config/urls.py   + pipeline.urls
```

**Why `export.py` is separate from `views.py`:** the bundle is the archival artifact and needs to be testable without HTTP. The view is a thin wrapper that sets the filename and content type.

---

### Task 1: Experiment API and the run action

**Files:**
- Create: `backend/pipeline/serializers.py`, `backend/pipeline/views.py`, `backend/pipeline/urls.py`, `backend/pipeline/tests/test_experiment_api.py`
- Modify: `backend/config/urls.py`

**Interfaces:**
- Produces: `ExperimentSerializer`; `ExperimentViewSet` at `/api/experiments/` with actions `run` (`POST`, staff, body `{"n_repetitions": int}`) and `progress` (`GET`).

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_experiment_api.py`:

```python
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, SystemDescription
from pipeline.models import Experiment, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        email="admin@example.com", password="pw-123456", is_staff=True
    )


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def prompts(db):
    return (
        PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g"),
        PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l"),
    )


@pytest.fixture
def seed(db):
    return SystemDescription.objects.create(
        title="Oldsmar", body="A plant.", origin=Origin.SEED
    )


@pytest.fixture
def experiment(db, taxonomy, prompts, seed):
    generation, labelling = prompts
    made = Experiment.objects.create(
        name="baseline", taxonomy=taxonomy,
        phase2_model="m2", phase2_prompt=generation,
        phase3_model="m3", phase3_prompt=labelling,
    )
    made.seed_systems.add(seed)
    return made


@pytest.mark.django_db
def test_listing_requires_authentication(api_client, experiment):
    assert api_client.get("/api/experiments/").status_code == 403


@pytest.mark.django_db
def test_any_authenticated_user_may_read(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    body = api_client.get("/api/experiments/").json()
    assert body["count"] == 1
    assert body["results"][0]["name"] == "baseline"


@pytest.mark.django_db
def test_the_list_carries_progress(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    row = api_client.get("/api/experiments/").json()["results"][0]
    assert row["progress"] == {"done": 0, "total": 0, "failed": 0, "percent": 0}


@pytest.mark.django_db
def test_non_staff_cannot_create(api_client, member, taxonomy, prompts):
    api_client.force_authenticate(user=member)
    generation, labelling = prompts
    response = api_client.post(
        "/api/experiments/",
        {"name": "x", "taxonomy": taxonomy.id, "phase2_model": "m",
         "phase2_prompt": generation.id, "phase3_model": "m",
         "phase3_prompt": labelling.id},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_creates_an_experiment(api_client, staff, taxonomy, prompts, seed):
    api_client.force_authenticate(user=staff)
    generation, labelling = prompts
    response = api_client.post(
        "/api/experiments/",
        {"name": "fresh", "taxonomy": taxonomy.id, "phase2_model": "m2",
         "phase2_prompt": generation.id, "phase3_model": "m3",
         "phase3_prompt": labelling.id, "seed_systems": [seed.id]},
        format="json",
    )
    assert response.status_code == 201
    assert Experiment.objects.get(name="fresh").created_by == staff


@pytest.mark.django_db
def test_an_invalid_experiment_is_refused_with_the_reason(
    api_client, staff, taxonomy, prompts
):
    """Validation lives on the model; the API must surface it, not bypass it."""
    api_client.force_authenticate(user=staff)
    generation, labelling = prompts
    response = api_client.post(
        "/api/experiments/",
        {"name": "bad", "taxonomy": taxonomy.id, "phase2_model": "m2",
         "phase2_prompt": generation.id, "phase3_model": "m3",
         "phase3_prompt": labelling.id, "generate_systems": False},
        format="json",
    )
    assert response.status_code == 400
    assert "target_system" in str(response.json())


@pytest.mark.django_db
def test_a_generated_system_cannot_be_seeded(api_client, staff, taxonomy, prompts):
    """The isolation guarantee, enforced at the point of entry.

    Experiment.clean() cannot check seed_systems before the row has a pk, so
    the API must refuse this itself or a run's own output could seed its
    successor.
    """
    api_client.force_authenticate(user=staff)
    generation, labelling = prompts
    output = SystemDescription.objects.create(
        title="Riverbend", body="b", origin=Origin.GENERATED
    )
    response = api_client.post(
        "/api/experiments/",
        {"name": "contaminated", "taxonomy": taxonomy.id, "phase2_model": "m2",
         "phase2_prompt": generation.id, "phase3_model": "m3",
         "phase3_prompt": labelling.id, "seed_systems": [output.id]},
        format="json",
    )

    assert response.status_code == 400
    assert "Riverbend" in str(response.json()["seed_systems"])
    assert not Experiment.objects.filter(name="contaminated").exists()


@pytest.mark.django_db
def test_non_staff_cannot_launch_a_run(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 1}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_launches_repetitions_and_they_are_queued(api_client, staff, experiment):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.views.async_task") as enqueued:
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 2},
            format="json",
        )

    assert response.status_code == 202
    assert len(response.json()["runs"]) == 2
    assert PipelineRun.objects.count() == 2
    assert enqueued.call_count == 2
    assert enqueued.call_args_list[0].args[0] == "pipeline.tasks.run_pipeline_task"

    for run in PipelineRun.objects.all():
        assert run.status == PipelineRun.Status.QUEUED
        assert {p.status for p in run.phase_runs.all()} == {PhaseRun.Status.QUEUED}


@pytest.mark.django_db
def test_launching_defaults_to_one_repetition(api_client, staff, experiment):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.views.async_task"):
        api_client.post(f"/api/experiments/{experiment.id}/run/", {}, format="json")
    assert PipelineRun.objects.count() == 1


@pytest.mark.django_db
def test_a_silly_repetition_count_is_refused(api_client, staff, experiment):
    api_client.force_authenticate(user=staff)
    for count in (0, -1, 101):
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/", {"n_repetitions": count},
            format="json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
def test_launching_without_an_api_key_is_a_clear_503(
    api_client, staff, experiment, settings
):
    """A missing key is an operator problem with a fix, not a server fault."""
    settings.OPENROUTER_API_KEY = ""
    api_client.force_authenticate(user=staff)
    with patch("pipeline.views.async_task") as enqueued:
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 1},
            format="json",
        )

    assert response.status_code == 503
    assert "No OpenRouter API key" in str(response.json())
    assert PipelineRun.objects.count() == 0
    assert enqueued.call_count == 0


@pytest.mark.django_db
def test_launching_an_invalid_experiment_is_a_400_and_creates_nothing(
    api_client, staff, experiment
):
    experiment.generate_systems = False
    experiment.save()
    api_client.force_authenticate(user=staff)
    with patch("pipeline.views.async_task") as enqueued:
        response = api_client.post(
            f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 1},
            format="json",
        )

    assert response.status_code == 400
    assert PipelineRun.objects.count() == 0
    assert enqueued.call_count == 0


@pytest.mark.django_db
def test_the_progress_action_reports_the_roll_up(api_client, member, staff, experiment):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.views.async_task"):
        api_client.post(
            f"/api/experiments/{experiment.id}/run/", {"n_repetitions": 1},
            format="json",
        )

    api_client.force_authenticate(user=member)
    body = api_client.get(f"/api/experiments/{experiment.id}/progress/").json()
    assert body["total"] == 2  # one generation call per variant, seeded at launch
    assert body["done"] == 0
    assert body["percent"] == 0
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_experiment_api.py -v`
Expected: FAIL — 404 on `/api/experiments/`.

- [x] **Step 3: Write the implementation**

`backend/pipeline/serializers.py`:

```python
import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from pipeline.models import Experiment


class ProgressField(serializers.Field):
    """Read-only passthrough of a model's `progress` dict."""

    def __init__(self, **kwargs):
        kwargs.setdefault("read_only", True)
        kwargs.setdefault("source", "*")
        super().__init__(**kwargs)

    def to_representation(self, value):
        return value.progress


class ExperimentSerializer(serializers.ModelSerializer):
    progress = ProgressField()
    run_count = serializers.IntegerField(source="runs.count", read_only=True)

    class Meta:
        model = Experiment
        fields = [
            "id",
            "name",
            "description",
            "taxonomy",
            "phase2_model",
            "phase2_temperature",
            "phase2_prompt",
            "phase3_model",
            "phase3_temperature",
            "phase3_prompt",
            "seed_systems",
            "fewshots",
            "generate_systems",
            "generate_scenarios",
            "target_system",
            "n_systems",
            "n_scenarios_per_system",
            "variants",
            "progress",
            "run_count",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["created_by", "created_at"]
        # Not settable on an unsaved instance, so validate() skips them.
        m2m_fields = frozenset({"seed_systems", "fewshots"})

    def validate(self, attrs):
        """Run the model's own validation. The API must not bypass it.

        Experiment.clean() carries the rules that keep the comparison honest —
        that a generated system may not target a run, that both toggles cannot
        be off, that each prompt matches its phase, that the taxonomy has rows
        for variant A's leak backstop to scan. Skipping it here would let the
        API store configurations `launch` then refuses, so the operator learns
        at run time what they could have learned at save time.

        Validation runs against a COPY carrying the pending changes, so a
        rejected PATCH leaves `self.instance` untouched for the error response.
        """
        instance = copy.copy(self.instance) if self.instance else Experiment()
        for field, value in attrs.items():
            if field not in self.Meta.m2m_fields:
                setattr(instance, field, value)

        try:
            instance.clean()
        except DjangoValidationError as error:
            raise serializers.ValidationError(error.message_dict) from error

        self._refuse_generated_seeds(attrs)
        return attrs

    def _refuse_generated_seeds(self, attrs):
        """Close the create-time hole in Experiment.clean().

        clean() can only check seed_systems when the row already has a pk —
        an unsaved instance has no M2M table to query. So on create, a
        generated system could be seeded through the API and would only be
        caught later, by `launch`'s full_clean. Checking the supplied list
        directly catches it at the point the operator can still fix it.
        """
        seeds = attrs.get("seed_systems")
        if not seeds:
            return
        generated = [s.title for s in seeds if s.origin == "generated"]
        if generated:
            raise serializers.ValidationError(
                {
                    "seed_systems": (
                        f"{generated} were generated by a previous run. Seeding an "
                        "experiment with its own output contaminates the comparison."
                    )
                }
            )


class RunRequestSerializer(serializers.Serializer):
    n_repetitions = serializers.IntegerField(required=False, default=1, min_value=1, max_value=100)
```

`backend/pipeline/views.py`:

```python
from django.core.exceptions import ValidationError as DjangoValidationError
from django_q.tasks import async_task
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response

from core.keys import MissingAPIKey
from pipeline.models import Experiment, PhaseRun, PipelineRun
from pipeline.orchestration import launch
from pipeline.serializers import ExperimentSerializer, RunRequestSerializer

RUN_TASK = "pipeline.tasks.run_pipeline_task"


class StaffWrites(BasePermission):
    """Anyone signed in may read; only staff may create or launch."""

    message = "Only staff may create experiments or launch runs."

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or bool(request.user.is_staff)


class ExperimentViewSet(viewsets.ModelViewSet):
    queryset = Experiment.objects.all()
    serializer_class = ExperimentSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        """Launch N repetitions and hand each to django-q.

        `launch` resolves the API key first, so a missing key creates nothing —
        the run fails here with an actionable message rather than producing a
        batch full of 401s.
        """
        experiment = self.get_object()
        options = RunRequestSerializer(data=request.data)
        options.is_valid(raise_exception=True)

        try:
            runs = launch(experiment, n_repetitions=options.validated_data["n_repetitions"])
        except MissingAPIKey as error:
            return Response(
                {"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        except DjangoValidationError as error:
            raise ValidationError(
                getattr(error, "message_dict", {"detail": str(error)})
            ) from error

        for run in runs:
            run.status = PipelineRun.Status.QUEUED
            run.save(update_fields=["status"])
            run.phase_runs.update(status=PhaseRun.Status.QUEUED)
            async_task(RUN_TASK, run.pk)

        return Response(
            {
                "runs": [
                    {"id": run.pk, "repetition_index": run.repetition_index}
                    for run in runs
                ]
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        return Response(self.get_object().progress)
```

`backend/pipeline/urls.py`:

```python
from rest_framework.routers import DefaultRouter

from pipeline.views import ExperimentViewSet

router = DefaultRouter()
router.register("experiments", ExperimentViewSet, basename="experiment")

urlpatterns = router.urls
```

Add to `backend/config/urls.py`'s `urlpatterns`:

```python
    path("api/", include("pipeline.urls")),
```

- [x] **Step 4: Run the tests**

Run: `cd backend && uv run pytest pipeline/tests/test_experiment_api.py -v && uv run pytest`
Expected: all pass.

- [x] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 2: Run and phase-run API, retry, and the call inspector

**Files:**
- Modify: `backend/pipeline/serializers.py`, `backend/pipeline/views.py`, `backend/pipeline/urls.py`
- Create: `backend/pipeline/tests/test_run_api.py`

**Interfaces:**
- Produces: `PipelineRunSerializer`, `PhaseRunSerializer`, `LLMCallSerializer`; `PipelineRunViewSet` (read + `destroy`, `?experiment=`) and `PhaseRunViewSet` (read-only, `?pipeline_run=&phase=&variant=&status=`) with actions `retry` (`POST`, staff) and `calls` (`GET`, paginated).

**Why a run may be deleted when generated material is otherwise immutable:** `corpus/permissions.py` already states the policy — "Removing generated material is done by deleting the run that produced it, which cascades. Deleting one row at a time would be selective reporting." Deleting a whole run is the sanctioned escape hatch precisely because it is all-or-nothing. Task 1's 409 on experiment deletion tells the operator to delete the runs first, so that route has to exist.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_run_api.py`:

```python
import json

import pytest
from django.contrib.auth import get_user_model

from llm.fake import FakeLLMClient
from pipeline.clients import set_llm_client
from pipeline.models import Experiment, PhaseRun, PipelineRun
from pipeline.orchestration import execute_pipeline_run, launch
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()

CARD = {
    "scenario_id": "sc_01", "actor": "EP1", "trigger": "t", "standing_state": "s",
    "asset": "a", "threat": "Spoofing.", "outcome": "o",
    "narrative_steps": ["EP1 acts."],
}
GENERATED = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": [CARD]}]}
)
LABELLED = json.dumps(
    {"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]}
)


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        email="admin@example.com", password="pw-123456", is_staff=True
    )


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def executed(db, settings):
    settings.OPENROUTER_API_KEY = "sk-or-test"
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    labelling = PromptTemplate.objects.create_version(
        phase=3, name="l", user_prompt="{{ scenario.scenario_id }}"
    )
    experiment = Experiment.objects.create(
        name="e", taxonomy=tax,
        phase2_model="m2", phase2_prompt=generation,
        phase3_model="m3", phase3_prompt=labelling,
        n_systems=1, n_scenarios_per_system=1,
    )
    experiment.variants = ["A"]
    experiment.save()

    fake = FakeLLMClient()
    fake.queue(GENERATED)
    fake.queue(LABELLED)
    set_llm_client(fake)

    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)
    run.refresh_from_db()
    return run


@pytest.mark.django_db
def test_runs_require_authentication(api_client, executed):
    assert api_client.get("/api/pipeline-runs/").status_code == 403


@pytest.mark.django_db
def test_a_run_reports_its_status_and_progress(api_client, member, executed):
    api_client.force_authenticate(user=member)
    row = api_client.get(f"/api/pipeline-runs/{executed.pk}/").json()

    assert row["status"] == "completed"
    assert row["repetition_index"] == 1
    assert row["progress"]["percent"] == 100
    assert row["progress"]["failed"] == 0


@pytest.mark.django_db
def test_runs_filter_by_experiment(api_client, member, executed):
    api_client.force_authenticate(user=member)
    body = api_client.get(
        f"/api/pipeline-runs/?experiment={executed.experiment_id}"
    ).json()
    assert body["count"] == 1


@pytest.mark.django_db
def test_runs_are_read_only(api_client, member, executed):
    api_client.force_authenticate(user=member)
    assert api_client.post("/api/pipeline-runs/", {}, format="json").status_code == 405
    assert api_client.delete(f"/api/pipeline-runs/{executed.pk}/").status_code == 405


@pytest.mark.django_db
def test_non_staff_cannot_delete_a_run(api_client, member, executed):
    api_client.force_authenticate(user=member)
    assert api_client.delete(f"/api/pipeline-runs/{executed.pk}/").status_code == 403
    assert PipelineRun.objects.filter(pk=executed.pk).exists()


@pytest.mark.django_db
def test_staff_deletes_a_finished_run_and_its_output_goes_with_it(
    api_client, staff, executed
):
    """All-or-nothing. Deleting individual generated rows would be cherry-picking."""
    from corpus.models import Scenario, SystemDescription

    from pipeline.models import Label, LLMCall

    assert SystemDescription.objects.filter(origin="generated").exists()

    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/pipeline-runs/{executed.pk}/")

    assert response.status_code == 204
    assert not PipelineRun.objects.filter(pk=executed.pk).exists()
    assert not PhaseRun.objects.filter(pipeline_run_id=executed.pk).exists()
    assert not SystemDescription.objects.filter(origin="generated").exists()
    assert not Scenario.objects.filter(origin="generated").exists()
    assert LLMCall.objects.count() == 0
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_a_run_in_flight_cannot_be_deleted(api_client, staff, executed):
    """A worker still holds it; deleting rows out from under it is a crash, not a delete."""
    api_client.force_authenticate(user=staff)
    for state in (PipelineRun.Status.QUEUED, PipelineRun.Status.RUNNING):
        executed.status = state
        executed.save(update_fields=["status"])
        response = api_client.delete(f"/api/pipeline-runs/{executed.pk}/")
        assert response.status_code == 409
        assert PipelineRun.objects.filter(pk=executed.pk).exists()


@pytest.mark.django_db
def test_deleting_the_last_run_frees_the_experiment_for_deletion(
    api_client, staff, executed
):
    """Task 1's 409 tells the operator to delete the runs first. Prove that works."""
    experiment_id = executed.experiment_id
    api_client.force_authenticate(user=staff)

    assert api_client.delete(f"/api/experiments/{experiment_id}/").status_code == 409
    assert api_client.delete(f"/api/pipeline-runs/{executed.pk}/").status_code == 204
    assert api_client.delete(f"/api/experiments/{experiment_id}/").status_code == 204


@pytest.mark.django_db
def test_phase_runs_carry_the_isolation_evidence(api_client, member, executed):
    """A Run A phase run must be able to prove its leak scan ran."""
    api_client.force_authenticate(user=member)
    rows = api_client.get(f"/api/phase-runs/?pipeline_run={executed.pk}&phase=2").json()
    generation = rows["results"][0]

    assert generation["variant"] == "A"
    assert generation["leak_scan_ran"] is True
    assert generation["guarded_by"] is not None


@pytest.mark.django_db
def test_phase_runs_filter_by_phase_and_variant(api_client, member, executed):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/phase-runs/?phase=3").json()["count"] == 1
    assert api_client.get("/api/phase-runs/?variant=A").json()["count"] == 2
    assert api_client.get("/api/phase-runs/?status=completed").json()["count"] == 2


@pytest.mark.django_db
def test_a_bad_filter_value_is_a_400_not_a_500(api_client, member, executed):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/phase-runs/?phase=abc").status_code == 400
    assert api_client.get("/api/pipeline-runs/?experiment=abc").status_code == 400


@pytest.mark.django_db
def test_the_call_inspector_returns_both_prompts_and_the_raw_response(
    api_client, member, executed
):
    api_client.force_authenticate(user=member)
    generation = executed.phase_runs.get(phase=2)
    body = api_client.get(f"/api/phase-runs/{generation.pk}/calls/").json()

    call = body["results"][0]
    assert call["rendered_user_prompt"]
    assert call["raw_response"]
    assert call["request_json"]["model"] == "m2"
    assert call["response_json"] is not None
    assert call["parsed_ok"] is True


@pytest.mark.django_db
def test_the_call_inspector_never_returns_a_key(api_client, member, executed, settings):
    api_client.force_authenticate(user=member)
    generation = executed.phase_runs.get(phase=2)
    blob = str(api_client.get(f"/api/phase-runs/{generation.pk}/calls/").json())
    assert "sk-or-" not in blob


@pytest.mark.django_db
def test_non_staff_cannot_retry(api_client, member, executed):
    api_client.force_authenticate(user=member)
    labelling = executed.phase_runs.get(phase=3)
    assert api_client.post(f"/api/phase-runs/{labelling.pk}/retry/").status_code == 403


@pytest.mark.django_db
def test_retrying_a_healthy_run_is_a_no_op(api_client, staff, executed):
    api_client.force_authenticate(user=staff)
    labelling = executed.phase_runs.get(phase=3)
    before = labelling.calls.count()

    response = api_client.post(f"/api/phase-runs/{labelling.pk}/retry/")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    labelling.refresh_from_db()
    assert labelling.calls.count() == before


@pytest.mark.django_db
def test_retrying_a_phase_two_run_is_refused(api_client, staff, executed):
    """Retry re-labels scenarios; it has no meaning for a generation run."""
    api_client.force_authenticate(user=staff)
    generation = executed.phase_runs.get(phase=2)
    response = api_client.post(f"/api/phase-runs/{generation.pk}/retry/")
    assert response.status_code == 400
    assert "phase 3" in str(response.json()).lower()
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_run_api.py -v`
Expected: FAIL — 404 on `/api/pipeline-runs/`.

- [x] **Step 3: Write the implementation**

Append to `backend/pipeline/serializers.py`:

```python
from pipeline.models import Label, LLMCall, PhaseRun, PipelineRun


class PipelineRunSerializer(serializers.ModelSerializer):
    progress = ProgressField()

    class Meta:
        model = PipelineRun
        fields = [
            "id", "experiment", "repetition_index", "status",
            "progress", "started_at", "finished_at",
        ]


class PhaseRunSerializer(serializers.ModelSerializer):
    progress = ProgressField()
    leak_scan_ran = serializers.SerializerMethodField()
    guarded_by = serializers.SerializerMethodField()

    class Meta:
        model = PhaseRun
        fields = [
            "id", "pipeline_run", "phase", "variant", "model", "temperature",
            "prompt_template", "taxonomy", "source_phase_run", "status", "error",
            "progress", "total_children", "completed_children", "failed_children",
            "leak_scan_ran", "guarded_by", "started_at", "finished_at",
        ]

    def get_leak_scan_ran(self, obj):
        """Whether the Run A isolation backstop actually ran, from the calls."""
        return any(call.leak_scan_ran for call in obj.calls.all())

    def get_guarded_by(self, obj):
        for call in obj.calls.all():
            if call.guarded_by_id:
                return call.guarded_by_id
        return None


class LLMCallSerializer(serializers.ModelSerializer):
    class Meta:
        model = LLMCall
        fields = [
            "id", "sequence", "scenario",
            "rendered_system_prompt", "rendered_user_prompt",
            "request_json", "response_json", "raw_response",
            "parsed_ok", "parse_error", "unknown_hcv_ids",
            "http_status", "transport_error",
            "model", "finish_reason", "prompt_tokens", "completion_tokens",
            "cost", "latency_ms", "leak_scan_ran", "guarded_by", "created_at",
        ]
```

Append to `backend/pipeline/views.py`:

```python
from pipeline.models import LLMCall
from pipeline.orchestration import retry_failed
from pipeline.serializers import (
    LLMCallSerializer,
    PhaseRunSerializer,
    PipelineRunSerializer,
)


def integer_param(params, name):
    """Read an integer query parameter, or 400. Never a 500."""
    raw = params.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError) as error:
        raise ValidationError({name: f"Must be an integer, got {raw!r}."}) from error


class PipelineRunViewSet(
    mixins.DestroyModelMixin, viewsets.ReadOnlyModelViewSet
):
    """Runs are a record of what happened; they are never EDITED over the API.

    Deletion is the one exception, and it is deliberate: `corpus.permissions`
    already fixes the policy that generated material is removed by deleting the
    run that produced it, whole, because deleting individual rows would be
    selective reporting. It is also what task 1's 409 on experiment deletion
    tells the operator to do, so the route has to exist.
    """

    serializer_class = PipelineRunSerializer
    # `StaffWrites` allows any authenticated user to read and reserves every
    # unsafe method — DELETE included — for staff.
    permission_classes = [IsAuthenticated, StaffWrites]

    def perform_destroy(self, instance):
        """Refuse while a worker may still hold the run.

        A QUEUED or RUNNING repetition is either waiting for a worker or being
        driven by one right now; deleting its rows mid-flight turns an orderly
        run into a crash inside the task, with half the output already written.
        """
        if instance.status in (
            PipelineRun.Status.QUEUED,
            PipelineRun.Status.RUNNING,
        ):
            raise Conflict(
                f"Repetition {instance.repetition_index} is {instance.status} and "
                "cannot be deleted while a worker may still hold it. Wait for it to "
                "finish, or cancel it first."
            )
        instance.delete()

    def get_queryset(self):
        queryset = PipelineRun.objects.select_related("experiment")
        experiment = integer_param(self.request.query_params, "experiment")
        return queryset.filter(experiment_id=experiment) if experiment else queryset


class PhaseRunViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PhaseRunSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def get_queryset(self):
        queryset = PhaseRun.objects.prefetch_related("calls")
        params = self.request.query_params
        if (pipeline_run := integer_param(params, "pipeline_run")) is not None:
            queryset = queryset.filter(pipeline_run_id=pipeline_run)
        if (phase := integer_param(params, "phase")) is not None:
            queryset = queryset.filter(phase=phase)
        if variant := params.get("variant"):
            queryset = queryset.filter(variant=variant)
        if state := params.get("status"):
            queryset = queryset.filter(status=state)
        return queryset

    @action(detail=True, methods=["get"])
    def calls(self, request, pk=None):
        """Every archived call, in full. Nothing is truncated."""
        queryset = self.get_object().calls.all()
        page = self.paginate_queryset(queryset)
        serializer = LLMCallSerializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Re-label only the scenarios whose call failed."""
        phase_run = self.get_object()
        if phase_run.phase != 3:
            raise ValidationError(
                {"detail": "Retry re-labels scenarios and applies only to a phase 3 run."}
            )
        return Response(PhaseRunSerializer(retry_failed(phase_run)).data)
```

Register both in `backend/pipeline/urls.py`:

```python
from pipeline.views import ExperimentViewSet, PhaseRunViewSet, PipelineRunViewSet

router.register("pipeline-runs", PipelineRunViewSet, basename="pipeline-run")
router.register("phase-runs", PhaseRunViewSet, basename="phase-run")
```

- [x] **Step 4: Run the tests**

Run: `cd backend && uv run pytest pipeline/tests/test_run_api.py -v && uv run pytest`

- [x] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 3: Labels API

**Files:**
- Modify: `backend/pipeline/serializers.py`, `backend/pipeline/views.py`, `backend/pipeline/urls.py`
- Create: `backend/pipeline/tests/test_label_api.py`

**Interfaces:**
- Produces: `LabelSerializer`; `LabelViewSet` (read-only) at `/api/labels/` filterable by `?scenario=&phase_run=&hcv=&source=`.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_label_api.py`:

```python
import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Label, PhaseRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def labelled(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    insider = HCV.objects.create(
        taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7
    )
    passwords = HCV.objects.create(
        taxonomy=tax, hcv_id="HCV-17", name="Weak Passwords Practices", order=17
    )
    template = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    run = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=template, taxonomy=tax
    )
    system = SystemDescription.objects.create(
        title="Oldsmar", body="x", origin=Origin.SEED
    )
    first = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    second = Scenario.objects.create(
        system_description=system, scenario_id="sc_02", origin=Origin.SEED, order=2
    )
    Label.objects.create(phase_run=run, scenario=first, hcv=insider, evidence="e1")
    Label.objects.create(phase_run=run, scenario=first, hcv=passwords, evidence="e2")
    Label.objects.create(phase_run=run, scenario=second, hcv=insider, evidence="e3")

    # Noise, so every filter below has something to EXCLUDE. Without a second
    # phase run and a second source, `?phase_run=<run>` and `?source=llm` each
    # return the whole table and would pass with the filter clause deleted.
    other = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=template, taxonomy=tax
    )
    Label.objects.create(phase_run=other, scenario=second, hcv=passwords, evidence="e4")
    Label.objects.create(
        phase_run=run, scenario=second, hcv=passwords, evidence="e5",
        source=Label.Source.HUMAN,
    )
    return run, first, insider, other


@pytest.mark.django_db
def test_labels_require_authentication(api_client, labelled):
    assert api_client.get("/api/labels/").status_code == 403


@pytest.mark.django_db
def test_labels_list_with_their_identifiers(api_client, member, labelled):
    api_client.force_authenticate(user=member)
    body = api_client.get("/api/labels/").json()

    assert body["count"] == 5
    row = body["results"][0]
    assert row["hcv_id"] == "HCV-07"
    assert row["hcv_name"] == "Malicious Insiders"
    assert row["scenario_id"] == "sc_01"
    assert row["source"] == "llm"


@pytest.mark.django_db
def test_labels_filter_by_scenario(api_client, member, labelled):
    _, scenario, _, _ = labelled
    api_client.force_authenticate(user=member)
    assert api_client.get(f"/api/labels/?scenario={scenario.pk}").json()["count"] == 2
    assert api_client.get("/api/labels/?scenario=999999").json()["count"] == 0


@pytest.mark.django_db
def test_labels_filter_by_hcv(api_client, member, labelled):
    _, _, hcv, _ = labelled
    api_client.force_authenticate(user=member)
    assert api_client.get(f"/api/labels/?hcv={hcv.pk}").json()["count"] == 2


@pytest.mark.django_db
def test_labels_filter_by_phase_run_and_source(api_client, member, labelled):
    """Each filter must EXCLUDE something, or it is not being tested at all."""
    run, _, _, other = labelled
    api_client.force_authenticate(user=member)
    assert api_client.get(f"/api/labels/?phase_run={run.pk}").json()["count"] == 4
    assert api_client.get(f"/api/labels/?phase_run={other.pk}").json()["count"] == 1
    assert api_client.get("/api/labels/?source=llm").json()["count"] == 4
    assert api_client.get("/api/labels/?source=human").json()["count"] == 1


@pytest.mark.django_db
def test_a_bad_label_filter_is_a_400(api_client, member, labelled):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/labels/?scenario=abc").status_code == 400
    assert api_client.get("/api/labels/?scenario=0").json()["count"] == 0


@pytest.mark.django_db
def test_the_label_list_does_not_n_plus_one(api_client, member, labelled):
    """`hcv_id`/`hcv_name`/`scenario_id` each walk an FK; select_related pays once."""
    from django.test.utils import CaptureQueriesContext
    from django.db import connection

    api_client.force_authenticate(user=member)
    with CaptureQueriesContext(connection) as first:
        api_client.get("/api/labels/")

    run, _, _, _ = labelled
    scenario = Scenario.objects.get(scenario_id="sc_02")
    hcv = HCV.objects.get(hcv_id="HCV-07")
    for index in range(10):
        Label.objects.create(
            phase_run=run, scenario=scenario, hcv=hcv, evidence=f"bulk-{index}"
        )

    with CaptureQueriesContext(connection) as second:
        api_client.get("/api/labels/")

    assert len(second) == len(first)


@pytest.mark.django_db
def test_labels_are_read_only(api_client, member, labelled):
    api_client.force_authenticate(user=member)
    assert api_client.post("/api/labels/", {}, format="json").status_code == 405
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_label_api.py -v`
Expected: FAIL — 404 on `/api/labels/`.

- [x] **Step 3: Write the implementation**

Append to `backend/pipeline/serializers.py`:

```python
class LabelSerializer(serializers.ModelSerializer):
    hcv_id = serializers.CharField(source="hcv.hcv_id", read_only=True)
    hcv_name = serializers.CharField(source="hcv.name", read_only=True)
    scenario_id = serializers.CharField(source="scenario.scenario_id", read_only=True)

    class Meta:
        model = Label
        fields = [
            "id", "phase_run", "scenario", "scenario_id", "hcv", "hcv_id", "hcv_name",
            "evidence", "rationale", "source", "llm_call", "created_by", "created_at",
        ]
```

Append to `backend/pipeline/views.py`:

```python
from pipeline.models import Label
from pipeline.serializers import LabelSerializer


class LabelViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = LabelSerializer

    def get_queryset(self):
        queryset = Label.objects.select_related("hcv", "scenario")
        params = self.request.query_params
        for name, field in (
            ("scenario", "scenario_id"),
            ("phase_run", "phase_run_id"),
            ("hcv", "hcv_id"),
        ):
            if (value := integer_param(params, name)) is not None:
                queryset = queryset.filter(**{field: value})
        if source := params.get("source"):
            queryset = queryset.filter(source=source)
        return queryset
```

Register in `backend/pipeline/urls.py`:

```python
from pipeline.views import LabelViewSet

router.register("labels", LabelViewSet, basename="label")
```

- [x] **Step 4: Run the tests**

Run: `cd backend && uv run pytest pipeline/tests/test_label_api.py -v && uv run pytest`

- [x] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 4: The export bundle

**Files:**
- Create: `backend/pipeline/export.py`, `backend/pipeline/tests/test_export.py`
- Modify: `backend/pipeline/views.py`

**Interfaces:**
- Produces: `pipeline.export.build_bundle(experiment) -> dict`; `pipeline.export.write_csv_zip(bundle) -> bytes`; `GET /api/experiments/{id}/export/?format=json|csv`.

Spec §9 fixes the bundle's sections: `experiment`, `taxonomy`, `prompts`, `inputs`, `runs`, `systems`, `scenarios`, `labels`, `calls`. It assembles stored rows and computes nothing.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_export.py`:

```python
import io
import json
import zipfile

import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import set_llm_client
from pipeline.export import build_bundle, write_csv_zip
from pipeline.models import Experiment
from pipeline.orchestration import execute_pipeline_run, launch
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()

CARD = {
    "scenario_id": "sc_01", "actor": "EP1", "trigger": "t", "standing_state": "s",
    "asset": "a", "threat": "Spoofing.", "outcome": "o",
    "narrative_steps": ["EP1 acts."],
}
GENERATED = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": [CARD]}]}
)
LABELLED = json.dumps(
    {"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]}
)
SENTINEL = "sk-or-v1-EXPORTSENTINEL"


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def experiment(db, settings):
    settings.OPENROUTER_API_KEY = SENTINEL
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(
        taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders",
        definition="Authorized insiders misuse access.", order=7,
    )
    generation = PromptTemplate.objects.create_version(
        phase=2, name="g", system_prompt="sys", user_prompt="Write {{ n_systems }}."
    )
    labelling = PromptTemplate.objects.create_version(
        phase=3, name="l", user_prompt="{{ scenario.scenario_id }}"
    )
    seed = SystemDescription.objects.create(
        title="Oldsmar", body="A plant.", origin=Origin.SEED
    )
    made = Experiment.objects.create(
        name="baseline", taxonomy=tax,
        phase2_model="m2", phase2_prompt=generation,
        phase3_model="m3", phase3_prompt=labelling,
        n_systems=1, n_scenarios_per_system=1,
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
    bundle = build_bundle(experiment)
    assert set(bundle) == {
        "experiment", "taxonomy", "prompts", "inputs",
        "runs", "systems", "scenarios", "labels", "calls",
    }


@pytest.mark.django_db
def test_the_bundle_pins_the_configuration(experiment):
    bundle = build_bundle(experiment)
    assert bundle["experiment"]["name"] == "baseline"
    assert bundle["experiment"]["phase2_model"] == "m2"
    assert bundle["experiment"]["variants"] == ["A"]


@pytest.mark.django_db
def test_the_bundle_carries_the_taxonomy_verbatim(experiment):
    rows = build_bundle(experiment)["taxonomy"]["rows"]
    assert rows[0]["hcv_id"] == "HCV-07"
    assert rows[0]["definition"] == "Authorized insiders misuse access."


@pytest.mark.django_db
def test_the_bundle_carries_the_prompts_at_the_version_used(experiment):
    prompts = build_bundle(experiment)["prompts"]
    assert {p["phase"] for p in prompts} == {2, 3}
    assert any(p["user_prompt"] == "Write {{ n_systems }}." for p in prompts)


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
def test_the_csv_zip_holds_one_file_per_section(experiment):
    archive = zipfile.ZipFile(io.BytesIO(write_csv_zip(build_bundle(experiment))))
    assert set(archive.namelist()) == {
        "experiment.csv", "taxonomy.csv", "prompts.csv", "inputs.csv",
        "runs.csv", "systems.csv", "scenarios.csv", "labels.csv", "calls.csv",
    }


@pytest.mark.django_db
def test_the_csv_calls_are_not_truncated(experiment):
    archive = zipfile.ZipFile(io.BytesIO(write_csv_zip(build_bundle(experiment))))
    text = archive.read("calls.csv").decode("utf-8")
    assert "Write 1." in text
    assert SENTINEL not in text


@pytest.mark.django_db
def test_the_export_endpoint_returns_json_by_default(api_client, member, experiment):
    api_client.force_authenticate(user=member)
    response = api_client.get(f"/api/experiments/{experiment.pk}/export/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert "attachment" in response["Content-Disposition"]
    assert set(json.loads(response.content)) >= {"experiment", "calls"}


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
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.export'`.

- [x] **Step 3: Write the implementation**

`backend/pipeline/export.py`:

```python
"""Assemble the archival bundle for one experiment.

This computes nothing. Spec §9 makes the bundle the record from which the
deferred metrics work and the offline ontology step both read, so it carries
the prompts and every call verbatim rather than only the labels. Truncating to
save size would defeat the point.
"""

import csv
import io
import json
import zipfile

from corpus.models import Scenario, SystemDescription
from pipeline.models import Label, LLMCall, PhaseRun, PipelineRun

SECTIONS = (
    "experiment", "taxonomy", "prompts", "inputs",
    "runs", "systems", "scenarios", "labels", "calls",
)


def _experiment(experiment):
    return {
        "id": experiment.pk,
        "name": experiment.name,
        "description": experiment.description,
        "phase2_model": experiment.phase2_model,
        "phase2_temperature": experiment.phase2_temperature,
        "phase3_model": experiment.phase3_model,
        "phase3_temperature": experiment.phase3_temperature,
        "generate_systems": experiment.generate_systems,
        "generate_scenarios": experiment.generate_scenarios,
        "n_systems": experiment.n_systems,
        "n_scenarios_per_system": experiment.n_scenarios_per_system,
        "variants": experiment.variants,
        "created_at": experiment.created_at.isoformat(),
    }


def _taxonomy(experiment):
    taxonomy = experiment.taxonomy
    return {
        "vocabulary_id": taxonomy.vocabulary_id,
        "version": taxonomy.version,
        "rows": [
            {
                "hcv_id": row.hcv_id,
                "name": row.name,
                "definition": row.definition,
                "condition_classes": row.condition_classes,
            }
            for row in taxonomy.hcvs.all()
        ],
    }


def _prompts(experiment):
    return [
        {
            "id": template.pk,
            "phase": template.phase,
            "name": template.name,
            "version": template.version,
            "system_prompt": template.system_prompt,
            "user_prompt": template.user_prompt,
            "output_schema": template.output_schema,
        }
        for template in (experiment.phase2_prompt, experiment.phase3_prompt)
    ]


def _inputs(experiment):
    return {
        "seed_systems": [
            {"id": s.pk, "title": s.title, "body": s.body, "origin": s.origin}
            for s in experiment.seed_systems.all()
        ],
        "fewshots": [
            {
                "id": f.pk, "phase": f.phase, "name": f.name,
                "input": f.resolved_input, "output": f.resolved_output,
            }
            for f in experiment.fewshots.all()
        ],
        "target_system": (
            {
                "id": experiment.target_system.pk,
                "title": experiment.target_system.title,
                "body": experiment.target_system.body,
            }
            if experiment.target_system_id
            else None
        ),
    }


def _runs(experiment):
    rows = []
    for run in PipelineRun.objects.filter(experiment=experiment):
        for phase_run in run.phase_runs.all():
            rows.append(
                {
                    "pipeline_run": run.pk,
                    "repetition_index": run.repetition_index,
                    "phase_run": phase_run.pk,
                    "phase": phase_run.phase,
                    "variant": phase_run.variant,
                    "model": phase_run.model,
                    "temperature": phase_run.temperature,
                    "prompt_template": phase_run.prompt_template_id,
                    "status": phase_run.status,
                    "error": phase_run.error,
                    "total_children": phase_run.total_children,
                    "completed_children": phase_run.completed_children,
                    "failed_children": phase_run.failed_children,
                    "started_at": phase_run.started_at.isoformat() if phase_run.started_at else None,
                    "finished_at": phase_run.finished_at.isoformat() if phase_run.finished_at else None,
                }
            )
    return rows


def _phase_run_ids(experiment):
    return list(
        PhaseRun.objects.filter(pipeline_run__experiment=experiment).values_list(
            "pk", flat=True
        )
    )


def _systems(phase_run_ids):
    return [
        {
            "id": s.pk, "title": s.title, "slug": s.slug, "body": s.body,
            "origin": s.origin, "generation_run": s.generation_run_id,
        }
        for s in SystemDescription.objects.filter(generation_run_id__in=phase_run_ids)
    ]


def _scenarios(phase_run_ids):
    return [
        {
            "id": s.pk,
            "system_description": s.system_description_id,
            "scenario_id": s.scenario_id,
            **s.card,
            "narrative": s.narrative,
            "origin": s.origin,
            "generation_run": s.generation_run_id,
            "order": s.order,
        }
        for s in Scenario.objects.filter(generation_run_id__in=phase_run_ids)
    ]


def _labels(phase_run_ids):
    return [
        {
            "id": label.pk,
            "phase_run": label.phase_run_id,
            "scenario": label.scenario_id,
            "scenario_id": label.scenario.scenario_id,
            "hcv_id": label.hcv.hcv_id,
            "hcv_name": label.hcv.name,
            "evidence": label.evidence,
            "rationale": label.rationale,
            "source": label.source,
            "llm_call": label.llm_call_id,
        }
        for label in Label.objects.filter(
            phase_run_id__in=phase_run_ids
        ).select_related("hcv", "scenario")
    ]


def _calls(phase_run_ids):
    return [
        {
            "id": call.pk,
            "phase_run": call.phase_run_id,
            "sequence": call.sequence,
            "scenario": call.scenario_id,
            "rendered_system_prompt": call.rendered_system_prompt,
            "rendered_user_prompt": call.rendered_user_prompt,
            "request_json": call.request_json,
            "response_json": call.response_json,
            "raw_response": call.raw_response,
            "parsed_ok": call.parsed_ok,
            "parse_error": call.parse_error,
            "unknown_hcv_ids": call.unknown_hcv_ids,
            "http_status": call.http_status,
            "transport_error": call.transport_error,
            "model": call.model,
            "finish_reason": call.finish_reason,
            "prompt_tokens": call.prompt_tokens,
            "completion_tokens": call.completion_tokens,
            "cost": call.cost,
            "latency_ms": call.latency_ms,
            "leak_scan_ran": call.leak_scan_ran,
            "guarded_by": call.guarded_by_id,
            "created_at": call.created_at.isoformat(),
        }
        for call in LLMCall.objects.filter(phase_run_id__in=phase_run_ids)
    ]


def build_bundle(experiment):
    """Everything needed to reconstruct this experiment without the database."""
    phase_run_ids = _phase_run_ids(experiment)
    return {
        "experiment": _experiment(experiment),
        "taxonomy": _taxonomy(experiment),
        "prompts": _prompts(experiment),
        "inputs": _inputs(experiment),
        "runs": _runs(experiment),
        "systems": _systems(phase_run_ids),
        "scenarios": _scenarios(phase_run_ids),
        "labels": _labels(phase_run_ids),
        "calls": _calls(phase_run_ids),
    }


def _as_rows(section):
    """Flatten a section to CSV rows. Nested values become compact JSON."""
    if isinstance(section, dict):
        section = [section]
    rows = []
    for entry in section:
        rows.append(
            {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else value
                for key, value in entry.items()
            }
        )
    return rows


def write_csv_zip(bundle):
    """One CSV per section, zipped. Long columns are written in full."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in SECTIONS:
            section = bundle[name]
            if name == "taxonomy":
                section = section["rows"]
            elif name == "inputs":
                section = section["seed_systems"] + section["fewshots"]
            rows = _as_rows(section)
            text = io.StringIO()
            if rows:
                writer = csv.DictWriter(text, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            archive.writestr(f"{name}.csv", text.getvalue())
    return buffer.getvalue()
```

Append the export action to `ExperimentViewSet` in `backend/pipeline/views.py`:

```python
import json

from django.http import HttpResponse

from pipeline.export import build_bundle, write_csv_zip
```

and inside `ExperimentViewSet`:

```python
    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        """Download the archival bundle. Any authenticated user may."""
        experiment = self.get_object()
        wanted = request.query_params.get("format", "json")
        if wanted not in ("json", "csv"):
            raise ValidationError({"format": "Use format=json or format=csv."})

        bundle = build_bundle(experiment)
        stem = f"{experiment.name}-export"
        if wanted == "csv":
            response = HttpResponse(write_csv_zip(bundle), content_type="application/zip")
            response["Content-Disposition"] = f'attachment; filename="{stem}.zip"'
            return response

        response = HttpResponse(
            json.dumps(bundle, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        response["Content-Disposition"] = f'attachment; filename="{stem}.json"'
        return response
```

Note: `export` must be readable by any authenticated user, but `StaffWrites` only
gates unsafe methods, and `export` is a GET — so no extra permission is needed.

- [x] **Step 4: Run the tests**

Run: `cd backend && uv run pytest pipeline/tests/test_export.py -v && uv run pytest`

- [x] **Step 5: Prove it end to end against the real seed data**

```bash
cd backend
uv run python manage.py shell -c "
import json
from django.core.management import call_command
from corpus.models import Origin, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import set_llm_client
from pipeline.export import build_bundle
from pipeline.models import Experiment
from pipeline.orchestration import execute_pipeline_run, launch
from prompts.models import PromptTemplate
from taxonomy.models import Taxonomy

call_command('import_taxonomy', '--activate')
call_command('seed_corpus')
card = {'scenario_id':'sc_01','actor':'EP1','trigger':'t','standing_state':'s',
        'asset':'a','threat':'Spoofing.','outcome':'o','narrative_steps':['EP1 acts.']}
gen = PromptTemplate.objects.create_version(phase=2, name='x-gen', user_prompt='Write {{ n_systems }}.')
lab = PromptTemplate.objects.create_version(phase=3, name='x-lab', user_prompt='{{ scenario.scenario_id }}')
exp = Experiment.objects.create(name='export-demo', taxonomy=Taxonomy.objects.get(is_active=True),
    phase2_model='m2', phase2_prompt=gen, phase3_model='m3', phase3_prompt=lab,
    n_systems=1, n_scenarios_per_system=1)
exp.variants = ['A']; exp.save()
exp.seed_systems.set(SystemDescription.objects.filter(origin=Origin.SEED))
fake = FakeLLMClient()
fake.queue(json.dumps({'system_descriptions':[{'title':'Riverbend','body':'b','scenarios':[card]}]}))
fake.queue(json.dumps({'labels':[{'hcv_id':'HCV-07','evidence':'e','rationale':'r'}]}))
set_llm_client(fake)
execute_pipeline_run(launch(exp, n_repetitions=1)[0])
b = build_bundle(exp)
print('sections   :', sorted(b))
print('taxonomy   :', len(b['taxonomy']['rows']), 'rows')
print('calls      :', len(b['calls']), '| labels:', len(b['labels']))
print('no key     :', 'sk-or-' not in json.dumps(b))
print('A scan ran :', [c['leak_scan_ran'] for c in b['calls'] if c['scenario'] is None])
Experiment.objects.filter(name='export-demo').delete()
PromptTemplate.objects.filter(name__startswith='x-').delete()
"
```

Expected: 9 sections, 21 taxonomy rows, 2 calls, 1 label, no key, and the Run A
generation call reporting `[True]`.

- [x] **Step 6: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

## Done when

- `cd backend && uv run pytest` is green and `uv run ruff format --check . && uv run ruff check .` is clean.
- A staff user can create an experiment, launch N repetitions over HTTP, and watch progress; a non-staff user can read everything and launch nothing.
- Launching with no API key returns **503** and creates nothing; launching an invalid experiment returns **400** and creates nothing.
- Runs, phase runs, calls and labels are readable and **immutable** over the API; `retry` is the only write, and only on a phase 3 run.
- The export bundle carries all nine spec §9 sections, every call in full, the isolation evidence, and **no API key** — asserted independently of the write-time redaction.

Plan 4 (the React frontend) consumes: `/api/experiments/` (+ `run`, `progress`, `export`),
`/api/pipeline-runs/`, `/api/phase-runs/` (+ `calls`, `retry`), `/api/labels/`,
plus plan 1's and plan 2's endpoints. It must ship an extension allowlist with any
ResourceFile download route (carried forward from plan 1's review).

---

### Task 5: Close the lifecycle seams (added after the whole-branch review)

Tasks 1-4 each guarded the state transitions they owned; nobody owned the seams between
them. The final whole-layer review found a permanent dead end, an archive that can still be
rewritten after the fact, and a destructive action with no in-flight guard.

**USER DECISION (2026-09-09), superseding this plan's first draft of step 2.** The draft froze
an experiment's configuration once it had runs. The user rejected that framing and specified a
better one: *"runs should keep their config data attached. That is, even if these config data
change, browsing an older run should show its previous config."* So nothing freezes — the
configuration is **snapshotted onto the run**, and the export reads the snapshot. The user can
edit an experiment freely; an old run stays truthful because it carries its own copy.

**Files:**
- Modify: `backend/pipeline/models.py` (+ migrations), `backend/pipeline/orchestration.py`,
  `backend/pipeline/views.py`, `backend/pipeline/serializers.py`, `backend/pipeline/export.py`
- Test: `backend/pipeline/tests/test_lifecycle.py` (new), plus additions to existing files

**Interfaces:**
- Produces: `PipelineRun.config` (JSON snapshot), `PipelineRun.Status.CANCELLED`,
  `POST /api/pipeline-runs/{id}/cancel/`, `GET /api/pipeline-runs/{id}/export/`,
  `?experiment=` on `/api/phase-runs/`.

**Deferred by ruling:** `POST /api/phase-runs/` (iterate mode, spec §5.4/§9) moves to its own
task. Its archival design is unresolved — an ad-hoc `PhaseRun` has no experiment, and
`_phase_run_ids` scopes on `pipeline_run__experiment`, so it can never reach a bundle as the
export is currently scoped. Removing the freeze also relieves the workflow pressure that made
it urgent: the user can now edit an experiment and relaunch instead.

---

- [x] **Step 1: `retry_failed` must refuse a run in flight — fix it at the source**

The whole-branch review proved: a phase-3 run RUNNING with `total=5 completed=0 failed=1`
(a worker mid-fan-out), `POST .../retry/` returns **200**, **deletes archived `LLMCall` pk 4**,
and flips the run RUNNING→`completed` with incoherent counters, while the worker keeps writing
to a row now marked terminal.

Task 2 parked "the deeper fix inside `retry_failed` is noted but not attempted — the guard is
at one layer only". That parked ruling has stopped being acceptable: this is the same missing
guard through a second door. Fix it **inside `retry_failed`**, so the management command and any
future caller are protected too:

```python
class RunInFlight(RuntimeError):
    """The run is not finished, so its archived calls are not safe to touch."""
```

`retry_failed` raises `RunInFlight` unless `phase_run.is_terminal`. The view maps it to **409**
via the existing `Conflict`. Keep the view's phase-2 and missing-`source_phase_run` guards; they
fire earlier and say something more specific.

Test: a RUNNING phase run's archived calls survive a refused retry byte-for-byte (pks, counters,
status, `finished_at`), and the management command is refused too.

- [x] **Step 2: Snapshot the configuration onto the run**

Seven fields live only on `Experiment` and are read **live** at export time: `seed_systems`,
`fewshots`, `target_system`, `generate_systems`, `generate_scenarios`, `n_systems`,
`n_scenarios_per_system`. (`model`, `temperature`, `prompt_template` and `taxonomy` are already
pinned on `PhaseRun`, and `variants` is recoverable from the phase runs' own `variant` values.)

Proved on a **completed** experiment: `PATCH {"seed_systems": [other]}` → 200, and the bundle's
`inputs.seed_systems` goes `['Oldsmar']` → `['Swapped']` with `seed_scenarios` 1 → 0. The expert
ground-truth anchor and the record of *what variant A actually saw* both leave the archive.

Add `PipelineRun.config = models.JSONField(default=dict)`, written by `launch()` inside its
existing atomic block, alongside the phase runs it already pins.

**Snapshot resolved text, not just ids.** Pinning ids alone is not enough: a seed
`SystemDescription` is editable (`ReadOnlyIfGenerated` only protects *generated* rows), so an id
can still point at text that has since changed. Capture, at launch:

- each seed system's `id`, `title`, `slug` and `body`
- each seed system's scenarios (the Phase-1 expert pack) with `scenario_id`, the card fields,
  `narrative`, `intended_hcvs` and `abox`
- each few-shot's `id`, `name`, `phase` and its **resolved** `input`/`output` text
- `target_system` the same way as a seed system
- the scalars: `generate_systems`, `generate_scenarios`, `n_systems`, `n_scenarios_per_system`

This makes the structured record agree with the text record already archived verbatim in each
call's `rendered_system_prompt`/`rendered_user_prompt`.

`export.py`'s `_inputs` must read **the run's snapshot**, not the experiment. Where a bundle
covers several runs, carry each run's own snapshot on its `runs[]` entry. Rename or relabel the
bundle's `experiment` section so it is unmistakably the experiment's **current** configuration
rather than what ran — a reader must never mistake one for the other.

Tests: a full launch → `PATCH` every one of the seven fields → export, asserting the bundle
still reports what the run actually used; a seed system's `body` edited after launch, asserting
the snapshot keeps the original text; two runs of one experiment with a config edit between
them, asserting each run reports its own config and the two differ.

- [x] **Step 3: A run that will never finish must have a way out**

**The dead end, confirmed and permanent.** A run stuck QUEUED — the broker accepted the task
then died, `max_attempts=1`, `catch_up=False` — has no route out across all 28 registered
routes: `DELETE run` 409, `DELETE experiment` 409 (task 1 requires the runs gone first),
`PATCH`/`PUT` 405, `DELETE phase-run` 405, and no `cancel` exists. Task 2's own 409 message says
*"Wait for it to finish, or cancel it first"* — advertising an affordance never built — and
`Experiment.name` is unique, so the operator cannot even recreate it.

`PipelineRun.Status` has **no** `CANCELLED` member (`PhaseRun.Status` does). Add it, with a
migration. Then add `POST /api/pipeline-runs/{id}/cancel/`, staff-only:

- **USER DECISION:** allowed from any non-terminal state — PENDING, QUEUED **and** RUNNING. A
  terminal run is a 409.
- Sets the run and every non-terminal phase run under it to CANCELLED.
- The message must be honest: cancelling marks the run dead so it can be deleted; it does
  **not** reach into a worker still executing, whose in-flight writes may still land. Say that
  plainly rather than implying interruption.

**USER DECISION — surface this at launch, not only at cancel.** The `run` action's 202 response
must carry the same caveat, so the operator learns when starting a batch that cancelling marks
a run dead rather than interrupting it. Assert it in the launch test.

Then make task 2's 409 message name this route.

Test the full escape as one journey: launch → leave the run QUEUED (patched `async_task`) →
`DELETE run` 409 → `cancel` 200 → `DELETE run` 204 → `DELETE experiment` 204. That journey is
what was impossible.

- [x] **Step 4: Per-run export**

**USER DECISION:** *"the export will be per run."* Add `GET /api/pipeline-runs/{id}/export/`
with the same `?format=json|csv` contract as the experiment export, scoped to one repetition and
carrying that run's own config snapshot. Keep the experiment-level bundle — it now contains
every run, each with its own snapshot — so both the whole experiment and a single repetition can
be archived.

Reuse the experiment export's assembly rather than writing a second one; if the shapes differ,
extract what they share. The same guarantees apply and must be tested at this route too: no API
key, deterministic byte-identical output, and the isolation evidence present.

- [x] **Step 5: Two cross-task inconsistencies**

- `?experiment=` exists on `pipeline-runs` and not on `phase-runs`, and the miss is **silent**:
  `/api/phase-runs/?experiment=1` returns 200 with every phase run of every experiment, so the
  natural isolation query — "all variant-A phase-2 runs of experiment X" — cannot be expressed.
  Add it (`pipeline_run__experiment_id`), through `integer_param`.
- The two 409s carry only English `detail` while the 503s carry a machine-readable `code`. Give
  `Conflict` a `default_code` and distinct codes per case, matching the 503 pattern.
- `LabelViewSet` alone omits explicit `permission_classes`; make it explicit so all four
  viewsets read the same way.

- [x] **Step 6: Verify, format, lint**

```bash
cd backend && uv run pytest && uv run ruff format . && uv run ruff check --fix .
uv run python manage.py makemigrations --check --dry-run
uv run pytest prompts/tests/test_isolation.py -q
```

Baseline is 650 passing. Every guard added here must be verified by breaking it and watching the
test fail — this codebase has caught six vacuous tests already.

## Done when (task 5)

- `retry_failed` itself refuses a run in flight; no archived call can be destroyed through any
  caller, view or command.
- A run carries its own configuration snapshot, so editing an experiment can never change what
  an existing run's export reports — proven by editing all seven fields and a seed body.
- A run stuck QUEUED or RUNNING can be cancelled, then deleted, then its experiment deleted —
  proven as one end-to-end journey — and the caveat is surfaced at launch.
- A single run can be exported on its own.
- All four viewsets agree on filters, permissions and error shapes.
