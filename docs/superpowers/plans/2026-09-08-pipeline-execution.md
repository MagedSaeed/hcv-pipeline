# Pipeline Execution Implementation Plan (plan 3a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the experiment end to end — Phase 2 generates scenario material under both variants, Phase 3 labels every scenario against the pinned taxonomy, and every call is archived as a complete record.

**Architecture:** A new `pipeline` Django app owns `Experiment` (the pinned configuration), `PipelineRun` (one repetition), `PhaseRun` (one phase of one variant), `LLMCall` (one archived request/response) and `Label`. Django-Q2 with a Redis broker runs the work: one task per variant for Phase 2, and a fan-out of one task per scenario for Phase 3, so a single failed scenario fails only itself. All generated material is written with `origin="generated"`, which plan 1's API already refuses to mutate.

**Tech Stack:** Django 5.2.17 LTS, django-q2 1.11.1 + Redis 8, DRF 3.18, pytest-django, ruff. Consumes plan 2's `prompts.rendering`, `llm.client`, `llm.schemas`.

**Spec:** `docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md`

**Builds on:** plan 1 (`2026-09-07-backend-foundation.md`) and plan 2 (`2026-09-08-prompts-and-llm-client.md`), both complete — 282 tests.

**Scope note:** this is plan **3a**. The REST API for experiments and runs, and the export bundle, are plan 3b. 3a is driven by management commands and direct task calls, and is fully testable with `FakeLLMClient`.

## Global Constraints

- Python 3.13, Django 5.2.17, DRF 3.18.0. Managed by **uv** — never `pip`, never edit `pyproject.toml`/`uv.lock`. Every command is `uv run …` from `backend/`.
- **ruff** formats and lints: finish each task with `uv run ruff format . && uv run ruff check --fix .`. Watch **DJ012** (model member order: fields → Meta → `__str__` → `save()` → custom methods), **B017**, **F401/E402/F811**.
- Run **no** git state-changing commands (`commit`, `add`, `restore`, `stash`, `checkout`). Read-only git is fine.
- DRF is configured with `PageNumberPagination`, `PAGE_SIZE = 50` — list responses are `{count, next, previous, results}`.
- Tests are pytest-django, written before implementation (TDD). **No test may make a network call**; `FakeLLMClient` drives everything.
- **Contexts are ALWAYS built through `build_phase2_context` / `build_phase3_context`.** Never assemble a context dict by hand — plan 2's isolation guarantee is enforced in those builders, and `render()` refuses an unmarked phase-2 context precisely to catch a caller that bypasses them.
- **Variant A never receives a taxonomy.** `PhaseRun.taxonomy` is pinned for the record on both variants, but it is passed as `guard_taxonomy=` (scan only) for A and as `taxonomy=` (injected) for B.
- **No API key may reach a stored column.** `LLMCall` redacts on save.

## Interfaces this plan consumes (verified present)

```
prompts.rendering   build_phase2_context(*, variant, seed_systems, fewshots,
                        generate_systems, generate_scenarios, n_systems,
                        n_scenarios_per_system, target_system=None,
                        taxonomy=None, guard_taxonomy=None) -> dict
                    build_phase3_context(*, scenario, system_description,
                        taxonomy, fewshots) -> dict
                    render(template, context) -> RenderedPrompt(
                        system, user, guarded_by: int|None, leak_scan_ran: bool)
                    TaxonomyLeak, PromptRenderError
prompts.models      PromptTemplate(phase, name, version, system_prompt,
                        user_prompt, output_schema, is_active)
llm.client          LLMClient (runtime_checkable Protocol), OpenRouterClient,
                    LLMResult(request_json, response_json, raw_response, model,
                        finish_reason, prompt_tokens, completion_tokens, cost,
                        latency_ms, http_status, transport_error, .ok),
                    SecretLeak, DEFAULT_TIMEOUT
llm.fake            FakeLLMClient(responses=None) .queue() .fail_next() .calls
llm.schemas         Phase2Response, Phase3Response, ScenarioCard, GeneratedSystem,
                    parse_phase2, parse_phase3, split_known_labels, json_schema,
                    SchemaError
core.keys           resolve_openrouter_key(), MissingAPIKey
corpus.models       Origin(SEED/GENERATED/MANUAL), SystemDescription(title, slug,
                        body, origin), Scenario(system_description, scenario_id,
                        + 6 card fields, narrative, abox, origin, order, .card),
                    FewShotExample(phase, .resolved_input, .resolved_output)
taxonomy.models     Taxonomy(.active(), .hcvs), HCV(hcv_id, name, definition)
```

---

## File Structure

```
backend/pipeline/
  __init__.py  apps.py  admin.py
  models.py            Experiment, PipelineRun, PhaseRun, LLMCall, Label
  redaction.py         scrub secrets out of anything archived
  clients.py           get_llm_client() — the single seam tests swap
  generation.py        Phase 2: render -> call -> parse -> persist
  labelling.py         Phase 3: render -> call -> parse -> persist labels
  orchestration.py     launch a batch, roll up status, retry failures
  tasks.py             thin django-q entry points
  management/commands/run_experiment.py
  migrations/
  tests/  test_models.py  test_redaction.py  test_generation.py
          test_labelling.py  test_orchestration.py  test_run_experiment.py
backend/corpus/migrations/0004_generation_run.py   generated, adds the FKs
backend/config/settings.py    + "django_q", "pipeline"; + Q_CLUSTER
```

**Why `generation.py` and `labelling.py` are separate from `tasks.py`:** the phase logic must be callable directly in a test without a broker. `tasks.py` holds only the django-q entry points that resolve ids and delegate.

---

### Task 1: Pipeline models, and the corpus link back to a run

**Files:**
- Create: `backend/pipeline/{__init__,apps,models,admin}.py`, `backend/pipeline/migrations/__init__.py`, `backend/pipeline/tests/__init__.py`, `backend/pipeline/tests/test_models.py`
- Modify: `backend/corpus/models.py` (add `generation_run` to both models; widen the Scenario constraint), `backend/config/settings.py` (add `"pipeline"` after `"prompts"`)

**Interfaces:**
- Produces: `pipeline.models.Experiment`, `PipelineRun`, `PhaseRun`, `LLMCall`, `Label`; `PhaseRun.Status` / `PipelineRun.Status` text choices; `Experiment.clean()` validation; `PhaseRun.is_unbiased` property.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_models.py`:

```python
import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Experiment, LLMCall, Label, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def templates(db):
    return (
        PromptTemplate.objects.create_version(phase=2, name="gen", user_prompt="g"),
        PromptTemplate.objects.create_version(phase=3, name="lab", user_prompt="l"),
    )


@pytest.fixture
def seed(db):
    return SystemDescription.objects.create(
        title="Oldsmar", body="A plant.", origin=Origin.SEED
    )


@pytest.fixture
def experiment(db, taxonomy, templates, seed):
    gen, lab = templates
    exp = Experiment.objects.create(
        name="baseline",
        taxonomy=taxonomy,
        phase2_model="anthropic/claude-sonnet-4",
        phase2_prompt=gen,
        phase3_model="openai/gpt-5",
        phase3_prompt=lab,
        n_systems=2,
        n_scenarios_per_system=3,
    )
    exp.seed_systems.add(seed)
    return exp


@pytest.mark.django_db
def test_defaults_run_both_variants(experiment):
    assert experiment.variants == ["A", "B"]
    assert experiment.generate_systems is True
    assert experiment.generate_scenarios is True


@pytest.mark.django_db
def test_both_toggles_off_is_rejected(experiment):
    experiment.generate_systems = False
    experiment.generate_scenarios = False
    with pytest.raises(ValidationError, match="at least one"):
        experiment.full_clean()


@pytest.mark.django_db
def test_not_generating_systems_requires_a_target(experiment):
    experiment.generate_systems = False
    with pytest.raises(ValidationError, match="target_system"):
        experiment.full_clean()


@pytest.mark.django_db
def test_not_generating_systems_is_fine_with_a_target(experiment, seed):
    experiment.generate_systems = False
    experiment.target_system = seed
    experiment.full_clean()


@pytest.mark.django_db
def test_a_generated_system_may_not_seed_an_experiment(experiment):
    """Seeding a run with a prior run's output would contaminate the comparison."""
    generated = SystemDescription.objects.create(
        title="From a model", body="x", origin=Origin.GENERATED
    )
    experiment.seed_systems.add(generated)
    with pytest.raises(ValidationError, match="generated"):
        experiment.full_clean()


@pytest.mark.django_db
def test_an_unknown_variant_is_rejected(experiment):
    experiment.variants = ["A", "C"]
    with pytest.raises(ValidationError, match="C"):
        experiment.full_clean()


@pytest.mark.django_db
def test_phase_run_knows_whether_it_is_unbiased(experiment, taxonomy, templates):
    run = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    a = PhaseRun.objects.create(
        pipeline_run=run, phase=2, variant="A", model="m",
        prompt_template=templates[0], taxonomy=taxonomy,
    )
    b = PhaseRun.objects.create(
        pipeline_run=run, phase=2, variant="B", model="m",
        prompt_template=templates[0], taxonomy=taxonomy,
    )
    assert a.is_unbiased is True
    assert b.is_unbiased is False


@pytest.mark.django_db
def test_a_phase_run_may_be_ad_hoc_with_no_pipeline_run(experiment, taxonomy, templates):
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m",
        prompt_template=templates[0], taxonomy=taxonomy,
    )
    assert run.pipeline_run is None
    assert run.status == PhaseRun.Status.PENDING


@pytest.mark.django_db
def test_a_repetition_index_is_unique_per_experiment(experiment):
    PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    with pytest.raises(IntegrityError):
        PipelineRun.objects.create(experiment=experiment, repetition_index=1)


@pytest.mark.django_db
def test_generated_material_points_back_at_the_run(experiment, taxonomy, templates, seed):
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m",
        prompt_template=templates[0], taxonomy=taxonomy,
    )
    system = SystemDescription.objects.create(
        title="Generated", body="x", origin=Origin.GENERATED, generation_run=run
    )
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="sc_01",
        origin=Origin.GENERATED, generation_run=run, order=1,
    )
    assert system.generation_run == run
    assert scenario.generation_run == run
    assert run.systems.count() == 1
    assert run.scenarios.count() == 1


@pytest.mark.django_db
def test_two_runs_may_produce_the_same_scenario_id_for_one_system(
    experiment, taxonomy, templates, seed
):
    """Generating repeatedly against one target system must not collide on sc_01."""
    first = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    second = PhaseRun.objects.create(
        phase=2, variant="B", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    Scenario.objects.create(
        system_description=seed, scenario_id="sc_01",
        origin=Origin.GENERATED, generation_run=first, order=1,
    )
    Scenario.objects.create(
        system_description=seed, scenario_id="sc_01",
        origin=Origin.GENERATED, generation_run=second, order=1,
    )
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 2


@pytest.mark.django_db
def test_one_run_may_not_repeat_a_scenario_id_for_one_system(
    experiment, taxonomy, templates, seed
):
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    Scenario.objects.create(
        system_description=seed, scenario_id="sc_01",
        origin=Origin.GENERATED, generation_run=run, order=1,
    )
    with pytest.raises(IntegrityError):
        Scenario.objects.create(
            system_description=seed, scenario_id="sc_01",
            origin=Origin.GENERATED, generation_run=run, order=2,
        )


@pytest.mark.django_db
def test_a_label_is_unique_per_run_scenario_and_hcv(
    experiment, taxonomy, templates, seed
):
    run = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=templates[1], taxonomy=taxonomy
    )
    scenario = Scenario.objects.create(
        system_description=seed, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    hcv = HCV.objects.get(hcv_id="HCV-07")
    Label.objects.create(phase_run=run, scenario=scenario, hcv=hcv, evidence="e")
    with pytest.raises(IntegrityError):
        Label.objects.create(phase_run=run, scenario=scenario, hcv=hcv, evidence="e2")


@pytest.mark.django_db
def test_a_label_defaults_to_an_llm_source(experiment, taxonomy, templates, seed):
    run = PhaseRun.objects.create(
        phase=3, model="m", prompt_template=templates[1], taxonomy=taxonomy
    )
    scenario = Scenario.objects.create(
        system_description=seed, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    label = Label.objects.create(
        phase_run=run, scenario=scenario, hcv=HCV.objects.get(hcv_id="HCV-07")
    )
    assert label.source == Label.Source.LLM
    assert label.created_by is None


@pytest.mark.django_db
def test_an_llm_call_records_ok_from_its_payloads(experiment, taxonomy, templates):
    run = PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=templates[0], taxonomy=taxonomy
    )
    good = LLMCall.objects.create(
        phase_run=run, sequence=1, request_json={"model": "m"},
        response_json={"id": "x"}, raw_response="{}", parsed_ok=True,
    )
    failed = LLMCall.objects.create(
        phase_run=run, sequence=2, request_json={"model": "m"},
        transport_error="RateLimitError: 429",
    )
    assert good.ok is True
    assert failed.ok is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline'`.

- [x] **Step 3: Write the pipeline app**

`backend/pipeline/__init__.py`, `backend/pipeline/migrations/__init__.py`,
`backend/pipeline/tests/__init__.py`: empty.

`backend/pipeline/apps.py`:

```python
from django.apps import AppConfig


class PipelineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pipeline"
```

`backend/pipeline/models.py`:

```python
from django.core.exceptions import ValidationError
from django.db import models

from prompts.rendering import VARIANT_GUIDED, VARIANT_UNBIASED

VARIANTS = (VARIANT_UNBIASED, VARIANT_GUIDED)


class Experiment(models.Model):
    """A pinned configuration. Everything a run needs is frozen here.

    Models, prompt versions and the taxonomy version are all recorded so a
    result can be traced back to the exact wording and label list that produced
    it — editing a template later creates a new version and cannot rewrite this.
    """

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True, default="")
    taxonomy = models.ForeignKey("taxonomy.Taxonomy", on_delete=models.PROTECT)

    phase2_model = models.CharField(max_length=200)
    phase2_temperature = models.FloatField(default=1.0)
    phase2_prompt = models.ForeignKey(
        "prompts.PromptTemplate", related_name="phase2_experiments",
        on_delete=models.PROTECT,
    )
    phase3_model = models.CharField(max_length=200)
    phase3_temperature = models.FloatField(default=0.0)
    phase3_prompt = models.ForeignKey(
        "prompts.PromptTemplate", related_name="phase3_experiments",
        on_delete=models.PROTECT,
    )

    seed_systems = models.ManyToManyField("corpus.SystemDescription", blank=True)
    fewshots = models.ManyToManyField("corpus.FewShotExample", blank=True)

    generate_systems = models.BooleanField(default=True)
    generate_scenarios = models.BooleanField(default=True)
    target_system = models.ForeignKey(
        "corpus.SystemDescription", null=True, blank=True,
        related_name="targeted_by", on_delete=models.PROTECT,
    )
    n_systems = models.PositiveIntegerField(default=3)
    n_scenarios_per_system = models.PositiveIntegerField(default=5)
    variants = models.JSONField(default=list)

    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.variants:
            self.variants = list(VARIANTS)
        super().save(*args, **kwargs)

    def clean(self):
        errors = {}
        if not self.generate_systems and not self.generate_scenarios:
            errors["generate_systems"] = (
                "Enable at least one of generate_systems or generate_scenarios."
            )
        if not self.generate_systems and self.target_system_id is None:
            errors["target_system"] = (
                "Set target_system when generate_systems is off — scenarios need "
                "a system to be written against."
            )
        unknown = [v for v in (self.variants or []) if v not in VARIANTS]
        if unknown:
            errors["variants"] = f"Unknown variant(s) {unknown}; expected A and/or B."
        if self.pk:
            generated = [
                s.title for s in self.seed_systems.all() if s.origin == "generated"
            ]
            if generated:
                errors["seed_systems"] = (
                    f"{generated} were generated by a previous run. Seeding an "
                    "experiment with its own output contaminates the comparison."
                )
        if errors:
            raise ValidationError(errors)


class PipelineRun(models.Model):
    """One repetition of an experiment — every variant, both phases."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    experiment = models.ForeignKey(
        Experiment, related_name="runs", on_delete=models.CASCADE
    )
    repetition_index = models.PositiveIntegerField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["experiment", "repetition_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "repetition_index"], name="unique_repetition"
            )
        ]

    def __str__(self):
        return f"{self.experiment.name} #{self.repetition_index}"


class PhaseRun(models.Model):
    """One phase of one variant. `pipeline_run` is null for an ad-hoc run."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    pipeline_run = models.ForeignKey(
        PipelineRun, null=True, blank=True, related_name="phase_runs",
        on_delete=models.CASCADE,
    )
    phase = models.PositiveSmallIntegerField(
        choices=[(2, "Phase 2 — generation"), (3, "Phase 3 — labelling")]
    )
    variant = models.CharField(max_length=1, blank=True, default="")
    model = models.CharField(max_length=200)
    temperature = models.FloatField(default=0.0)
    prompt_template = models.ForeignKey(
        "prompts.PromptTemplate", on_delete=models.PROTECT
    )
    # Pinned on both variants for the record. Injected only for B; for A it is
    # passed as guard_taxonomy so the leak backstop has something to scan.
    taxonomy = models.ForeignKey("taxonomy.Taxonomy", on_delete=models.PROTECT)
    source_phase_run = models.ForeignKey(
        "self", null=True, blank=True, related_name="labelling_runs",
        on_delete=models.SET_NULL,
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    error = models.TextField(blank=True, default="")
    failed_children = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        label = f"phase {self.phase}"
        return f"{label} {self.variant}".strip()

    @property
    def is_unbiased(self):
        return self.variant == VARIANT_UNBIASED


class LLMCall(models.Model):
    """One archived request/response. Complete whether it succeeded or not."""

    phase_run = models.ForeignKey(
        PhaseRun, related_name="calls", on_delete=models.CASCADE
    )
    sequence = models.PositiveIntegerField(default=0)
    scenario = models.ForeignKey(
        "corpus.Scenario", null=True, blank=True, related_name="calls",
        on_delete=models.SET_NULL,
    )

    rendered_system_prompt = models.TextField(blank=True, default="")
    rendered_user_prompt = models.TextField(blank=True, default="")
    leak_scan_ran = models.BooleanField(default=False)
    guarded_by = models.ForeignKey(
        "taxonomy.Taxonomy", null=True, blank=True, related_name="+",
        on_delete=models.SET_NULL,
    )

    request_json = models.JSONField(default=dict)
    response_json = models.JSONField(null=True, blank=True)
    raw_response = models.TextField(blank=True, default="")

    parsed_ok = models.BooleanField(default=False)
    parse_error = models.TextField(blank=True, default="")
    unknown_hcv_ids = models.JSONField(default=list, blank=True)

    http_status = models.IntegerField(null=True, blank=True)
    transport_error = models.TextField(blank=True, default="")
    model = models.CharField(max_length=200, blank=True, default="")
    finish_reason = models.CharField(max_length=50, blank=True, default="")
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost = models.FloatField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["phase_run", "sequence", "id"]

    def __str__(self):
        return f"call {self.sequence} of {self.phase_run}"

    def save(self, *args, **kwargs):
        from pipeline.redaction import redact_call

        redact_call(self)
        super().save(*args, **kwargs)

    @property
    def ok(self):
        return self.response_json is not None and not self.transport_error


class Label(models.Model):
    """One vulnerability assigned to one scenario by one labelling run."""

    class Source(models.TextChoices):
        LLM = "llm", "LLM"
        HUMAN = "human", "Human"

    phase_run = models.ForeignKey(
        PhaseRun, related_name="labels", on_delete=models.CASCADE
    )
    scenario = models.ForeignKey(
        "corpus.Scenario", related_name="labels", on_delete=models.CASCADE
    )
    hcv = models.ForeignKey("taxonomy.HCV", related_name="labels", on_delete=models.PROTECT)
    evidence = models.TextField(blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    source = models.CharField(
        max_length=8, choices=Source.choices, default=Source.LLM
    )
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scenario", "hcv"]
        constraints = [
            models.UniqueConstraint(
                fields=["phase_run", "scenario", "hcv"], name="unique_label_per_run"
            )
        ]

    def __str__(self):
        return f"{self.scenario.scenario_id} -> {self.hcv.hcv_id}"
```

`backend/pipeline/admin.py`:

```python
from django.contrib import admin

from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun


@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("name", "phase2_model", "phase3_model", "created_at")
    filter_horizontal = ("seed_systems", "fewshots")


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "started_at", "finished_at")
    list_filter = ("status",)


@admin.register(PhaseRun)
class PhaseRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "phase", "variant", "status", "failed_children")
    list_filter = ("phase", "variant", "status")


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = ("__str__", "model", "parsed_ok", "http_status", "cost")
    list_filter = ("parsed_ok",)


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ("__str__", "source", "created_at")
    list_filter = ("source", "hcv")
```

Add `"pipeline"` to `INSTALLED_APPS` in `backend/config/settings.py`, after `"prompts"`.

- [x] **Step 4: Link the corpus back to a run**

In `backend/corpus/models.py`, add this field to BOTH `SystemDescription` and
`Scenario`, placed with the other fields (before `Meta`, per DJ012):

```python
    generation_run = models.ForeignKey(
        "pipeline.PhaseRun",
        null=True,
        blank=True,
        related_name="%(class)ss",
        on_delete=models.CASCADE,
    )
```

`CASCADE` is deliberate: deleting a run removes the material it produced, which
is how the spec says generated rows are removed — per run, never per row.

Then WIDEN the Scenario constraint. Replace:

```python
            models.UniqueConstraint(
                fields=["system_description", "scenario_id"],
                name="unique_scenario_id_per_system",
            )
```

with:

```python
            # Scoped to the run: Phase 2 generates sc_01, sc_02 … against the
            # same target system on every variant and every repetition, so the
            # id is only unique within the run that produced it. Seed and manual
            # rows share generation_run=NULL, and in every database this project
            # supports NULLs compare as distinct — so the constraint does not
            # restrict them. Their uniqueness is enforced by seed_corpus using
            # update_or_create on (system, scenario_id).
            models.UniqueConstraint(
                fields=["system_description", "generation_run", "scenario_id"],
                name="unique_scenario_id_per_run",
            )
```

- [x] **Step 5: Create the migrations and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations pipeline corpus
uv run pytest pipeline/tests/test_models.py -v
uv run pytest
```

Expected: `pipeline/migrations/0001_initial.py` and `corpus/migrations/0004_*.py`
created; the new tests pass; all 282 pre-existing tests still pass.

- [x] **Step 6: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 2: Redaction, and the client seam

**Files:**
- Create: `backend/pipeline/redaction.py`, `backend/pipeline/clients.py`, `backend/pipeline/tests/test_redaction.py`
- Modify: `backend/config/settings.py` (add `"django_q"` to `INSTALLED_APPS` and a `Q_CLUSTER` block)

**Interfaces:**
- Produces: `pipeline.redaction.REDACTED`, `secrets_to_scrub() -> list[str]`, `scrub(value, secrets) -> value`, `redact_call(call) -> None`; `pipeline.clients.get_llm_client() -> LLMClient`, `pipeline.clients.set_llm_client(client)` (test seam), `pipeline.clients.reset_llm_client()`.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_redaction.py`:

```python
import pytest

from core.models import AppSettings
from pipeline.models import Experiment, LLMCall, PhaseRun
from pipeline.redaction import REDACTED, scrub, secrets_to_scrub
from prompts.models import PromptTemplate
from taxonomy.models import Taxonomy

SENTINEL = "sk-or-v1-SENTINELSENTINEL"


@pytest.fixture
def key(db, settings):
    settings.OPENROUTER_API_KEY = SENTINEL
    return SENTINEL


@pytest.fixture
def phase_run(db, key):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    template = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    return PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=template, taxonomy=tax
    )


@pytest.mark.django_db
def test_the_configured_key_is_collected(key):
    assert SENTINEL in secrets_to_scrub()


@pytest.mark.django_db
def test_a_stored_key_is_collected_too(key):
    config = AppSettings.load()
    config.openrouter_key = "sk-or-stored-value"
    config.save()
    assert "sk-or-stored-value" in secrets_to_scrub()


@pytest.mark.django_db
def test_missing_keys_do_not_break_collection(settings):
    settings.OPENROUTER_API_KEY = ""
    assert secrets_to_scrub() == []


def test_scrub_replaces_in_a_nested_structure():
    payload = {"a": [{"b": f"Bearer {SENTINEL}"}], "c": SENTINEL}
    cleaned = scrub(payload, [SENTINEL])
    assert cleaned == {"a": [{"b": f"Bearer {REDACTED}"}], "c": REDACTED}


def test_scrub_leaves_clean_data_untouched():
    payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert scrub(payload, [SENTINEL]) == payload


def test_scrub_handles_non_string_leaves():
    payload = {"n": 1, "f": 1.5, "b": True, "none": None}
    assert scrub(payload, [SENTINEL]) == payload


def test_scrub_with_no_secrets_is_a_passthrough():
    payload = {"c": SENTINEL}
    assert scrub(payload, []) == payload


@pytest.mark.django_db
def test_a_key_in_any_archived_column_is_scrubbed_on_save(phase_run):
    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        request_json={"messages": [{"content": SENTINEL}]},
        response_json={"error": {"message": f"bad key {SENTINEL}"}},
        raw_response=f"leaked {SENTINEL}",
        transport_error=f"AuthError with {SENTINEL}",
    )
    call.refresh_from_db()

    blob = f"{call.request_json}{call.response_json}{call.raw_response}{call.transport_error}"
    assert SENTINEL not in blob
    assert REDACTED in call.raw_response
    assert REDACTED in call.transport_error


@pytest.mark.django_db
def test_redaction_does_not_mangle_a_clean_call(phase_run):
    body = {"id": "gen-1", "choices": [{"message": {"content": "hello"}}]}
    call = LLMCall.objects.create(
        phase_run=phase_run, sequence=1,
        request_json={"model": "m"}, response_json=body, raw_response="hello",
    )
    call.refresh_from_db()
    assert call.response_json == body
    assert call.raw_response == "hello"
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_redaction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.redaction'`.

- [x] **Step 3: Write the implementation**

`backend/pipeline/redaction.py`:

```python
"""Keep API keys out of anything archived.

The client already refuses to BUILD a request containing a key. This is the
second line: it scrubs on the way into the database, and it covers the columns
the client cannot vouch for — `response_json` and `transport_error` are built
from provider output, and a provider that echoes a key back in an error body
would otherwise put it in an export that gets shared with a collaborator.
"""

from django.conf import settings

REDACTED = "[REDACTED]"


def secrets_to_scrub():
    """Every key value currently in play, longest first so overlaps scrub fully."""
    from core.models import AppSettings

    candidates = [
        (AppSettings.load().openrouter_key or "").strip(),
        (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip(),
    ]
    return sorted({c for c in candidates if c}, key=len, reverse=True)


def scrub(value, secrets):
    """Recursively replace every secret occurrence. Structure is preserved."""
    if not secrets:
        return value
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {key: scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    return value


def redact_call(call):
    """Scrub every archived column of an LLMCall in place, before it is saved."""
    secrets = secrets_to_scrub()
    if not secrets:
        return
    call.request_json = scrub(call.request_json, secrets)
    call.response_json = scrub(call.response_json, secrets)
    call.raw_response = scrub(call.raw_response, secrets)
    call.transport_error = scrub(call.transport_error, secrets)
    call.rendered_system_prompt = scrub(call.rendered_system_prompt, secrets)
    call.rendered_user_prompt = scrub(call.rendered_user_prompt, secrets)
```

`backend/pipeline/clients.py`:

```python
"""One place the pipeline gets its LLM client, so tests can swap in the fake."""

from llm.client import OpenRouterClient

_client = None


def get_llm_client():
    global _client
    if _client is None:
        _client = OpenRouterClient()
    return _client


def set_llm_client(client):
    """Install a client for the process. Tests use this with FakeLLMClient."""
    global _client
    _client = client


def reset_llm_client():
    global _client
    _client = None
```

In `backend/config/settings.py`, add `"django_q"` to `INSTALLED_APPS` (before
the local apps) and add after the `CACHES` block:

```python
# Django-Q2. The pipeline fans Phase 3 out to one task per scenario, so a
# single bad scenario fails only itself.
Q_CLUSTER = {
    "name": "hcv-pipeline",
    "workers": config("Q_WORKERS", default=4, cast=int),
    "timeout": config("Q_TIMEOUT", default=600, cast=int),
    # Must exceed `timeout`, or a task is re-queued while it is still running.
    "retry": config("Q_RETRY", default=1200, cast=int),
    "max_attempts": 1,
    "save_limit": 0,
    "catch_up": False,
    "redis": config("REDIS_URL", default="redis://localhost:6379/0"),
}
```

- [x] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations
uv run pytest pipeline/tests/ -v
uv run pytest
```

Expected: django_q's own migrations are picked up; all tests pass.

- [x] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 3: Phase 2 generation

**Files:**
- Create: `backend/pipeline/generation.py`, `backend/pipeline/tests/test_generation.py`

**Interfaces:**
- Consumes: `build_phase2_context`, `render`, `get_llm_client`, `parse_phase2`, `json_schema`, `Phase2Response`.
- Produces: `pipeline.generation.run_phase2(phase_run) -> PhaseRun` — renders, calls, parses and persists, always leaving the run in a terminal status.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_generation.py`:

```python
import json

import pytest

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
        phase=2, name="gen",
        system_prompt="You write scenarios.",
        user_prompt="Write {{ n_systems }} systems with {{ n_scenarios_per_system }} each.",
    )


@pytest.fixture
def experiment(db, taxonomy, template):
    labeller = PromptTemplate.objects.create_version(phase=3, name="lab", user_prompt="l")
    return Experiment.objects.create(
        name="e", taxonomy=taxonomy,
        phase2_model="m2", phase2_prompt=template,
        phase3_model="m3", phase3_prompt=labeller,
        n_systems=1, n_scenarios_per_system=1,
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
    pipeline_run = PipelineRun.objects.create(
        experiment=experiment, repetition_index=highest + 1
    )
    return PhaseRun.objects.create(
        pipeline_run=pipeline_run,
        phase=2, variant=variant, model="m2", temperature=1.0,
        prompt_template=template, taxonomy=taxonomy,
    )


@pytest.mark.django_db
def test_a_successful_run_persists_the_generated_material(
    _client, experiment, taxonomy, template
):
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
def test_the_call_is_archived_with_its_rendered_prompts(
    _client, experiment, taxonomy, template
):
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
def test_variant_a_is_scanned_and_variant_b_is_not(
    _client, experiment, taxonomy, template
):
    _client.queue(PAYLOAD)
    a = run_phase2(make_run(experiment, taxonomy, template, variant="A"))
    _client.queue(PAYLOAD)
    b = run_phase2(make_run(experiment, taxonomy, template, variant="B"))

    assert a.calls.get().leak_scan_ran is True
    assert b.calls.get().leak_scan_ran is False


@pytest.mark.django_db
def test_variant_b_receives_the_taxonomy_and_a_does_not(
    _client, experiment, taxonomy, template
):
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
    target = SystemDescription.objects.create(
        title="Oldsmar", body="x", origin=Origin.SEED
    )
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
def test_malformed_json_is_recorded_as_a_parse_error(
    _client, experiment, taxonomy, template
):
    _client.queue("this is not json")
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    call = run.calls.get()
    assert call.parsed_ok is False
    assert call.parse_error
    assert call.raw_response == "this is not json"


@pytest.mark.django_db
def test_a_schema_violation_is_recorded_not_raised(
    _client, experiment, taxonomy, template
):
    _client.queue(json.dumps({"system_descriptions": [{"body": "no title"}]}))
    run = run_phase2(make_run(experiment, taxonomy, template))

    assert run.status == PhaseRun.Status.FAILED
    assert run.calls.get().parsed_ok is False
    assert "title" in run.calls.get().parse_error


@pytest.mark.django_db
def test_the_output_schema_is_pinned_into_the_request(
    _client, experiment, taxonomy, template
):
    _client.queue(PAYLOAD)
    run_phase2(make_run(experiment, taxonomy, template))
    assert _client.calls[0]["json_schema"]["properties"]["system_descriptions"]


@pytest.mark.django_db
def test_two_runs_against_one_target_do_not_collide(
    _client, experiment, taxonomy, template
):
    target = SystemDescription.objects.create(
        title="Oldsmar", body="x", origin=Origin.SEED
    )
    experiment.generate_systems = False
    experiment.target_system = target
    experiment.save()

    for _ in range(2):
        _client.queue(json.dumps({"scenarios": [CARD]}))
        assert run_phase2(
            make_run(experiment, taxonomy, template)
        ).status == PhaseRun.Status.COMPLETED

    assert Scenario.objects.filter(scenario_id="sc_01").count() == 2
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_generation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.generation'`.

- [x] **Step 3: Write the implementation**

`backend/pipeline/generation.py`:

```python
"""Phase 2 — generate scenario material under one variant.

The run reaches a terminal status whatever happens. A provider failure, a
malformed body and a schema violation are all recorded on the call and end the
run as FAILED — none of them raises, because a run that dies mid-flight leaves
no evidence of what went wrong.
"""

import json

from django.db import transaction
from django.utils import timezone

from corpus.models import Origin, Scenario, SystemDescription
from llm.schemas import Phase2Response, SchemaError, json_schema, parse_phase2
from pipeline.clients import get_llm_client
from pipeline.models import LLMCall, PhaseRun
from prompts.rendering import (
    VARIANT_UNBIASED,
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    render,
)


def _experiment_for(phase_run):
    """The configuration behind this run, or None for an ad-hoc run."""
    if phase_run.pipeline_run_id:
        return phase_run.pipeline_run.experiment
    return None


def _build_context(phase_run, experiment):
    """Always via the builder — never a hand-assembled dict (see plan 2)."""
    unbiased = phase_run.is_unbiased
    seeds = experiment.seed_systems.all() if experiment else []
    fewshots = experiment.fewshots.filter(phase=2, is_active=True) if experiment else []
    return build_phase2_context(
        variant=phase_run.variant or VARIANT_UNBIASED,
        seed_systems=list(seeds),
        fewshots=list(fewshots),
        generate_systems=experiment.generate_systems if experiment else True,
        generate_scenarios=experiment.generate_scenarios if experiment else True,
        n_systems=experiment.n_systems if experiment else 3,
        n_scenarios_per_system=experiment.n_scenarios_per_system if experiment else 5,
        target_system=experiment.target_system if experiment else None,
        # A never receives the taxonomy; it is passed only so the leak backstop
        # has a reference list to scan the finished prompt against.
        taxonomy=None if unbiased else phase_run.taxonomy,
        guard_taxonomy=phase_run.taxonomy if unbiased else None,
    )


def _fail(phase_run, message):
    phase_run.status = PhaseRun.Status.FAILED
    phase_run.error = message
    phase_run.finished_at = timezone.now()
    phase_run.save(update_fields=["status", "error", "finished_at"])
    return phase_run


def _narrative(card):
    return "\n".join(card.narrative_steps)


@transaction.atomic
def _persist(phase_run, parsed, experiment):
    """Write the generated material. Everything lands as origin=generated."""
    order = 0
    for system in parsed.system_descriptions:
        row = SystemDescription.objects.create(
            title=system.title, body=system.body,
            origin=Origin.GENERATED, generation_run=phase_run,
        )
        for card in system.scenarios:
            order += 1
            Scenario.objects.create(
                system_description=row, scenario_id=card.scenario_id,
                **{field: getattr(card, field) for field in Scenario.CARD_FIELDS},
                narrative=_narrative(card),
                origin=Origin.GENERATED, generation_run=phase_run, order=order,
            )

    target = experiment.target_system if experiment else None
    if parsed.scenarios and target is not None:
        for card in parsed.scenarios:
            order += 1
            Scenario.objects.create(
                system_description=target, scenario_id=card.scenario_id,
                **{field: getattr(card, field) for field in Scenario.CARD_FIELDS},
                narrative=_narrative(card),
                origin=Origin.GENERATED, generation_run=phase_run, order=order,
            )


def run_phase2(phase_run):
    """Render, call, parse and persist. Always ends in a terminal status."""
    experiment = _experiment_for(phase_run)
    phase_run.status = PhaseRun.Status.RUNNING
    phase_run.started_at = timezone.now()
    phase_run.save(update_fields=["status", "started_at"])

    try:
        context = _build_context(phase_run, experiment)
        rendered = render(phase_run.prompt_template, context)
    except (TaxonomyLeak, PromptRenderError) as error:
        return _fail(phase_run, f"{type(error).__name__}: {error}")

    result = get_llm_client().complete(
        model=phase_run.model,
        system_prompt=rendered.system,
        user_prompt=rendered.user,
        temperature=phase_run.temperature,
        json_schema=phase_run.prompt_template.output_schema
        or json_schema(Phase2Response),
    )

    call = LLMCall(
        phase_run=phase_run, sequence=1,
        rendered_system_prompt=rendered.system,
        rendered_user_prompt=rendered.user,
        leak_scan_ran=rendered.leak_scan_ran,
        guarded_by_id=rendered.guarded_by,
        request_json=result.request_json, response_json=result.response_json,
        raw_response=result.raw_response, http_status=result.http_status,
        transport_error=result.transport_error, model=result.model,
        finish_reason=result.finish_reason, prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens, cost=result.cost,
        latency_ms=result.latency_ms,
    )

    if not result.ok:
        call.save()
        return _fail(phase_run, result.transport_error or "The call returned no body.")

    try:
        parsed = parse_phase2(json.loads(result.raw_response))
    except (ValueError, SchemaError) as error:
        call.parse_error = str(error)
        call.save()
        return _fail(phase_run, f"Could not parse the response: {error}")

    call.parsed_ok = True
    call.save()
    _persist(phase_run, parsed, experiment)

    phase_run.status = PhaseRun.Status.COMPLETED
    phase_run.finished_at = timezone.now()
    phase_run.save(update_fields=["status", "finished_at"])
    return phase_run
```

- [x] **Step 4: Run the tests**

Run: `cd backend && uv run pytest pipeline/tests/test_generation.py -v && uv run pytest`
Expected: all pass.

- [x] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 4: Phase 3 labelling

**Files:**
- Create: `backend/pipeline/labelling.py`, `backend/pipeline/tests/test_labelling.py`

**Interfaces:**
- Consumes: `build_phase3_context`, `render`, `get_llm_client`, `parse_phase3`, `split_known_labels`, `json_schema`, `Phase3Response`.
- Produces: `pipeline.labelling.label_scenario(phase_run, scenario, sequence=1) -> LLMCall`; `pipeline.labelling.scenarios_for(phase_run) -> QuerySet`; `pipeline.labelling.finish_labelling_run(phase_run) -> PhaseRun`.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_labelling.py`:

```python
import json

import pytest

from corpus.models import Origin, Scenario, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.labelling import finish_labelling_run, label_scenario, scenarios_for
from pipeline.models import Label, PhaseRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    for code, name, order in [
        ("HCV-07", "Malicious Insiders", 7),
        ("HCV-17", "Weak Passwords Practices", 17),
    ]:
        HCV.objects.create(taxonomy=tax, hcv_id=code, name=name, order=order)
    return tax


@pytest.fixture
def template(db):
    return PromptTemplate.objects.create_version(
        phase=3, name="lab",
        user_prompt="Label {{ scenario.scenario_id }} from {{ taxonomy|length }} options.",
    )


@pytest.fixture
def scenario(db):
    system = SystemDescription.objects.create(
        title="Oldsmar", body="x", origin=Origin.SEED
    )
    return Scenario.objects.create(
        system_description=system, scenario_id="sc_01", actor="EP1",
        threat="Spoofing.", narrative="1. EP1 acts.", origin=Origin.SEED, order=1,
    )


@pytest.fixture
def run(db, taxonomy, template):
    return PhaseRun.objects.create(
        phase=3, model="m3", prompt_template=template, taxonomy=taxonomy
    )


def payload(*ids):
    return json.dumps(
        {"labels": [{"hcv_id": i, "evidence": "e", "rationale": "r"} for i in ids]}
    )


@pytest.mark.django_db
def test_labels_are_persisted(_client, run, scenario, taxonomy):
    _client.queue(payload("HCV-07"))
    label_scenario(run, scenario)

    label = Label.objects.get()
    assert label.hcv.hcv_id == "HCV-07"
    assert label.scenario == scenario
    assert label.phase_run == run
    assert label.source == Label.Source.LLM
    assert label.evidence == "e"


@pytest.mark.django_db
def test_an_empty_label_list_is_a_valid_result(_client, run, scenario):
    """A scenario carrying no vulnerability is a real finding, not a failure."""
    _client.queue(payload())
    call = label_scenario(run, scenario)

    assert call.parsed_ok is True
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_an_out_of_vocabulary_id_is_recorded_not_dropped(_client, run, scenario):
    _client.queue(payload("HCV-07", "HCV-99"))
    call = label_scenario(run, scenario)

    assert call.parsed_ok is True
    assert call.unknown_hcv_ids == ["HCV-99"]
    assert [label.hcv.hcv_id for label in Label.objects.all()] == ["HCV-07"]


@pytest.mark.django_db
def test_the_call_records_the_scenario_it_labelled(_client, run, scenario):
    _client.queue(payload("HCV-07"))
    call = label_scenario(run, scenario)
    assert call.scenario == scenario
    assert call.sequence == 1


@pytest.mark.django_db
def test_phase_three_always_sees_the_taxonomy(_client, run, scenario):
    _client.queue(payload("HCV-07"))
    call = label_scenario(run, scenario)
    assert "from 2 options" in call.rendered_user_prompt
    assert call.leak_scan_ran is False


@pytest.mark.django_db
def test_a_duplicate_label_from_one_run_is_ignored(_client, run, scenario):
    _client.queue(payload("HCV-07", "HCV-07"))
    label_scenario(run, scenario)
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_a_transport_failure_records_the_call_without_labels(_client, run, scenario):
    _client.fail_next("timeout", http_status=None)
    call = label_scenario(run, scenario)

    assert call.ok is False
    assert call.parsed_ok is False
    assert Label.objects.count() == 0


@pytest.mark.django_db
def test_scenarios_for_reads_the_source_run(_client, run, scenario, taxonomy, template):
    source = PhaseRun.objects.create(
        phase=2, variant="A", model="m2", prompt_template=template, taxonomy=taxonomy
    )
    scenario.generation_run = source
    scenario.origin = Origin.GENERATED
    scenario.save()
    run.source_phase_run = source
    run.save()

    assert list(scenarios_for(run)) == [scenario]


@pytest.mark.django_db
def test_scenarios_for_with_no_source_labels_everything(_client, run, scenario):
    assert list(scenarios_for(run)) == [scenario]


@pytest.mark.django_db
def test_finishing_completes_a_run_with_no_failures(_client, run, scenario):
    _client.queue(payload("HCV-07"))
    label_scenario(run, scenario)
    finished = finish_labelling_run(run)

    assert finished.status == PhaseRun.Status.COMPLETED
    assert finished.failed_children == 0
    assert finished.finished_at is not None


@pytest.mark.django_db
def test_finishing_fails_a_run_that_had_a_failed_child(_client, run, scenario):
    _client.fail_next("boom")
    label_scenario(run, scenario)
    finished = finish_labelling_run(run)

    assert finished.status == PhaseRun.Status.FAILED
    assert finished.failed_children == 1
    assert "1" in finished.error
```

- [x] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest pipeline/tests/test_labelling.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.labelling'`.

- [x] **Step 3: Write the implementation**

`backend/pipeline/labelling.py`:

```python
"""Phase 3 — label one scenario against the pinned taxonomy.

One scenario per call, so a single bad scenario fails only itself. A label
outside the pinned list is recorded on the call rather than dropped: how often
a labeller invents an identifier is itself a result.
"""

import json

from django.db import transaction
from django.utils import timezone

from corpus.models import Scenario
from llm.schemas import (
    Phase3Response,
    SchemaError,
    json_schema,
    parse_phase3,
    split_known_labels,
)
from pipeline.clients import get_llm_client
from pipeline.models import Label, LLMCall, PhaseRun
from prompts.rendering import (
    PromptRenderError,
    TaxonomyLeak,
    build_phase3_context,
    render,
)


def scenarios_for(phase_run):
    """The scenarios this run labels: one Phase 2 run's output, or everything."""
    if phase_run.source_phase_run_id:
        return Scenario.objects.filter(generation_run_id=phase_run.source_phase_run_id)
    return Scenario.objects.all()


def _fewshots(phase_run):
    if not phase_run.pipeline_run_id:
        return []
    experiment = phase_run.pipeline_run.experiment
    return list(experiment.fewshots.filter(phase=3, is_active=True))


@transaction.atomic
def _persist(phase_run, scenario, known):
    by_id = {row.hcv_id: row for row in phase_run.taxonomy.hcvs.all()}
    for entry in known:
        Label.objects.get_or_create(
            phase_run=phase_run,
            scenario=scenario,
            hcv=by_id[entry.hcv_id],
            defaults={"evidence": entry.evidence, "rationale": entry.rationale},
        )


def label_scenario(phase_run, scenario, sequence=1):
    """Label one scenario. Returns the archived call; never raises on provider error."""
    try:
        context = build_phase3_context(
            scenario=scenario,
            system_description=scenario.system_description,
            taxonomy=phase_run.taxonomy,
            fewshots=_fewshots(phase_run),
        )
        rendered = render(phase_run.prompt_template, context)
    except (TaxonomyLeak, PromptRenderError) as error:
        return LLMCall.objects.create(
            phase_run=phase_run, sequence=sequence, scenario=scenario,
            parse_error=f"{type(error).__name__}: {error}",
        )

    result = get_llm_client().complete(
        model=phase_run.model,
        system_prompt=rendered.system,
        user_prompt=rendered.user,
        temperature=phase_run.temperature,
        json_schema=phase_run.prompt_template.output_schema
        or json_schema(Phase3Response),
    )

    call = LLMCall(
        phase_run=phase_run, sequence=sequence, scenario=scenario,
        rendered_system_prompt=rendered.system,
        rendered_user_prompt=rendered.user,
        leak_scan_ran=rendered.leak_scan_ran,
        guarded_by_id=rendered.guarded_by,
        request_json=result.request_json, response_json=result.response_json,
        raw_response=result.raw_response, http_status=result.http_status,
        transport_error=result.transport_error, model=result.model,
        finish_reason=result.finish_reason, prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens, cost=result.cost,
        latency_ms=result.latency_ms,
    )
    if not result.ok:
        call.save()
        return call

    try:
        parsed = parse_phase3(json.loads(result.raw_response))
    except (ValueError, SchemaError) as error:
        call.parse_error = str(error)
        call.save()
        return call

    allowed = {row.hcv_id for row in phase_run.taxonomy.hcvs.all()}
    known, unknown = split_known_labels(parsed, allowed)
    call.parsed_ok = True
    call.unknown_hcv_ids = unknown
    call.save()

    _persist(phase_run, scenario, known)
    return call


def finish_labelling_run(phase_run):
    """Roll the fan-out up into one terminal status."""
    failed = phase_run.calls.filter(parsed_ok=False).count()
    phase_run.failed_children = failed
    phase_run.finished_at = timezone.now()
    if failed:
        phase_run.status = PhaseRun.Status.FAILED
        phase_run.error = f"{failed} scenario(s) failed to label."
    else:
        phase_run.status = PhaseRun.Status.COMPLETED
        phase_run.error = ""
    phase_run.save(
        update_fields=["status", "error", "failed_children", "finished_at"]
    )
    return phase_run
```

- [x] **Step 4: Run the tests**

Run: `cd backend && uv run pytest pipeline/tests/test_labelling.py -v && uv run pytest`
Expected: all pass.

- [x] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 5: Orchestration, tasks and the run command

**Files:**
- Create: `backend/pipeline/orchestration.py`, `backend/pipeline/tasks.py`, `backend/pipeline/management/__init__.py`, `backend/pipeline/management/commands/__init__.py`, `backend/pipeline/management/commands/run_experiment.py`, `backend/pipeline/tests/test_orchestration.py`, `backend/pipeline/tests/test_run_experiment.py`

**Interfaces:**
- Produces: `pipeline.orchestration.launch(experiment, n_repetitions=1, inline=False) -> list[PipelineRun]`, `execute_pipeline_run(pipeline_run)`, `retry_failed(phase_run)`, `roll_up(pipeline_run)`; `pipeline.tasks.run_phase2_task(phase_run_id)`, `run_phase3_task(phase_run_id)`; `manage.py run_experiment <name> [--repetitions N] [--inline]`.

- [x] **Step 1: Write the failing test**

Create `backend/pipeline/tests/test_orchestration.py`:

```python
import json

import pytest

from corpus.models import Origin, Scenario, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.models import Experiment, Label, PhaseRun, PipelineRun
from pipeline.orchestration import execute_pipeline_run, launch, retry_failed, roll_up
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

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
        name="e", taxonomy=tax,
        phase2_model="m2", phase2_prompt=gen,
        phase3_model="m3", phase3_prompt=lab,
        n_systems=1, n_scenarios_per_system=1,
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
    experiment.variants = ["A"]
    experiment.save()
    _client.queue(GENERATED)
    _client.fail_next("timeout")
    run = launch(experiment, n_repetitions=1)[0]
    execute_pipeline_run(run)

    labelling = run.phase_runs.get(phase=3, variant="A")
    assert labelling.status == PhaseRun.Status.FAILED
    assert Label.objects.count() == 0

    _client.queue(LABELLED)
    retried = retry_failed(labelling)

    assert retried.status == PhaseRun.Status.COMPLETED
    assert Label.objects.count() == 1


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
def test_roll_up_reports_completed_only_when_every_phase_did(experiment):
    run = launch(experiment, n_repetitions=1)[0]
    run.phase_runs.update(status=PhaseRun.Status.COMPLETED)
    assert roll_up(run).status == PipelineRun.Status.COMPLETED

    run.phase_runs.filter(phase=3, variant="B").update(status=PhaseRun.Status.FAILED)
    assert roll_up(run).status == PipelineRun.Status.FAILED
```

Create `backend/pipeline/tests/test_run_experiment.py`:

```python
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.models import Experiment, Label, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

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
    lab = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    exp = Experiment.objects.create(
        name="baseline", taxonomy=tax,
        phase2_model="m2", phase2_prompt=gen,
        phase3_model="m3", phase3_prompt=lab,
        n_systems=1, n_scenarios_per_system=1,
    )
    exp.variants = ["A"]
    exp.save()
    return exp


@pytest.mark.django_db
def test_the_command_runs_an_experiment_inline(_client, experiment):
    _client.queue(GENERATED)
    _client.queue(LABELLED)
    call_command("run_experiment", "baseline", "--inline")

    assert PipelineRun.objects.count() == 1
    assert PipelineRun.objects.get().status == PipelineRun.Status.COMPLETED
    assert Label.objects.count() == 1


@pytest.mark.django_db
def test_the_command_honours_repetitions(_client, experiment):
    for _ in range(2):
        _client.queue(GENERATED)
        _client.queue(LABELLED)
    call_command("run_experiment", "baseline", "--repetitions", "2", "--inline")
    assert PipelineRun.objects.count() == 2


@pytest.mark.django_db
def test_an_unknown_experiment_reports_clearly(_client):
    with pytest.raises(CommandError, match="No experiment"):
        call_command("run_experiment", "absent", "--inline")


@pytest.mark.django_db
def test_an_invalid_experiment_is_refused_before_any_call(_client, experiment):
    experiment.generate_systems = False
    experiment.save()
    with pytest.raises(CommandError, match="target_system"):
        call_command("run_experiment", "baseline", "--inline")
    assert PipelineRun.objects.count() == 0
    assert _client.calls == []
```

- [x] **Step 2: Run both to verify they fail**

Run: `cd backend && uv run pytest pipeline/tests/test_orchestration.py pipeline/tests/test_run_experiment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.orchestration'`.

- [x] **Step 3: Write the implementation**

`backend/pipeline/orchestration.py`:

```python
"""Turn an Experiment into runs, and roll their status back up.

Phase 3 depends on Phase 2's output, so within a variant the phases are ordered.
Variants are independent and never share context — that independence is what
makes the A/B comparison meaningful.
"""

from django.db import transaction
from django.utils import timezone

from pipeline.generation import run_phase2
from pipeline.labelling import finish_labelling_run, label_scenario, scenarios_for
from pipeline.models import PhaseRun, PipelineRun


@transaction.atomic
def launch(experiment, n_repetitions=1, inline=False):
    """Create the runs for N repetitions, pinning the configuration onto each."""
    experiment.full_clean()
    created = []
    highest = (
        PipelineRun.objects.filter(experiment=experiment)
        .order_by("-repetition_index")
        .values_list("repetition_index", flat=True)
        .first()
    ) or 0

    for offset in range(n_repetitions):
        run = PipelineRun.objects.create(
            experiment=experiment, repetition_index=highest + offset + 1
        )
        for variant in experiment.variants:
            generation = PhaseRun.objects.create(
                pipeline_run=run, phase=2, variant=variant,
                model=experiment.phase2_model,
                temperature=experiment.phase2_temperature,
                prompt_template=experiment.phase2_prompt,
                taxonomy=experiment.taxonomy,
            )
            PhaseRun.objects.create(
                pipeline_run=run, phase=3, variant=variant,
                model=experiment.phase3_model,
                temperature=experiment.phase3_temperature,
                prompt_template=experiment.phase3_prompt,
                taxonomy=experiment.taxonomy,
                source_phase_run=generation,
            )
        created.append(run)
    return created


def execute_pipeline_run(pipeline_run):
    """Run every variant's Phase 2 then Phase 3, in order, inline."""
    pipeline_run.status = PipelineRun.Status.RUNNING
    pipeline_run.started_at = timezone.now()
    pipeline_run.save(update_fields=["status", "started_at"])

    for variant in pipeline_run.experiment.variants:
        generation = pipeline_run.phase_runs.get(phase=2, variant=variant)
        labelling = pipeline_run.phase_runs.get(phase=3, variant=variant)

        run_phase2(generation)
        generation.refresh_from_db()
        if generation.status != PhaseRun.Status.COMPLETED:
            # Labelling nothing is not a success, and not a failure of its own.
            labelling.status = PhaseRun.Status.CANCELLED
            labelling.error = "Skipped: generation did not complete."
            labelling.finished_at = timezone.now()
            labelling.save(update_fields=["status", "error", "finished_at"])
            continue
        run_labelling(labelling)

    return roll_up(pipeline_run)


def run_labelling(phase_run):
    """Fan out over the scenarios this run is responsible for."""
    phase_run.status = PhaseRun.Status.RUNNING
    phase_run.started_at = timezone.now()
    phase_run.save(update_fields=["status", "started_at"])

    for sequence, scenario in enumerate(scenarios_for(phase_run), start=1):
        label_scenario(phase_run, scenario, sequence=sequence)
    return finish_labelling_run(phase_run)


def retry_failed(phase_run):
    """Re-label only the scenarios whose call did not parse."""
    failed = phase_run.calls.filter(parsed_ok=False)
    scenario_ids = [call.scenario_id for call in failed if call.scenario_id]
    failed.delete()

    highest = (
        phase_run.calls.order_by("-sequence").values_list("sequence", flat=True).first()
    ) or 0
    for offset, scenario in enumerate(
        scenarios_for(phase_run).filter(id__in=scenario_ids), start=1
    ):
        label_scenario(phase_run, scenario, sequence=highest + offset)
    return finish_labelling_run(phase_run)


def roll_up(pipeline_run):
    """A repetition is complete only when every phase run of it completed."""
    statuses = set(pipeline_run.phase_runs.values_list("status", flat=True))
    if statuses <= {PhaseRun.Status.COMPLETED}:
        pipeline_run.status = PipelineRun.Status.COMPLETED
    elif statuses & {PhaseRun.Status.PENDING, PhaseRun.Status.RUNNING}:
        pipeline_run.status = PipelineRun.Status.RUNNING
    else:
        pipeline_run.status = PipelineRun.Status.FAILED
    pipeline_run.finished_at = timezone.now()
    pipeline_run.save(update_fields=["status", "finished_at"])
    return pipeline_run
```

`backend/pipeline/tasks.py`:

```python
"""Django-Q entry points. Thin on purpose — the logic is importable and testable."""

from pipeline.generation import run_phase2
from pipeline.models import PhaseRun, PipelineRun
from pipeline.orchestration import execute_pipeline_run, run_labelling


def run_phase2_task(phase_run_id):
    return run_phase2(PhaseRun.objects.get(pk=phase_run_id)).status


def run_phase3_task(phase_run_id):
    return run_labelling(PhaseRun.objects.get(pk=phase_run_id)).status


def run_pipeline_task(pipeline_run_id):
    return execute_pipeline_run(PipelineRun.objects.get(pk=pipeline_run_id)).status
```

Create `backend/pipeline/management/__init__.py` and
`backend/pipeline/management/commands/__init__.py`, both empty.

`backend/pipeline/management/commands/run_experiment.py`:

```python
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from pipeline.models import Experiment
from pipeline.orchestration import execute_pipeline_run, launch


class Command(BaseCommand):
    help = "Run an experiment for N repetitions."

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("--repetitions", type=int, default=1)
        parser.add_argument(
            "--inline",
            action="store_true",
            help="Run in this process instead of queueing onto django-q.",
        )

    def handle(self, *args, **options):
        try:
            experiment = Experiment.objects.get(name=options["name"])
        except Experiment.DoesNotExist as error:
            raise CommandError(f"No experiment named {options['name']!r}.") from error

        try:
            runs = launch(experiment, n_repetitions=options["repetitions"])
        except ValidationError as error:
            raise CommandError(f"Experiment is not runnable: {error}") from error

        for run in runs:
            if options["inline"]:
                execute_pipeline_run(run)
            else:
                from django_q.tasks import async_task

                async_task("pipeline.tasks.run_pipeline_task", run.pk)

        self.stdout.write(
            self.style.SUCCESS(
                f"{'Ran' if options['inline'] else 'Queued'} {len(runs)} repetition(s) "
                f"of {experiment.name}"
            )
        )
```

- [x] **Step 4: Run the tests**

Run: `cd backend && uv run pytest pipeline/tests/ -v && uv run pytest`
Expected: all pass.

- [x] **Step 5: Prove it end to end against the real seed data**

Run:

```bash
cd backend
uv run python manage.py migrate
uv run python manage.py import_taxonomy --activate
uv run python manage.py seed_corpus
uv run python manage.py shell -c "
from corpus.models import Origin, SystemDescription
from pipeline.models import Experiment
from prompts.models import PromptTemplate
from taxonomy.models import Taxonomy

gen = PromptTemplate.objects.create_version(
    phase=2, name='smoke-gen',
    user_prompt='Write {{ n_systems }} system(s) with {{ n_scenarios_per_system }} scenario(s).')
lab = PromptTemplate.objects.create_version(
    phase=3, name='smoke-lab',
    user_prompt='Label {{ scenario.scenario_id }} using {{ taxonomy|length }} options.')
exp = Experiment.objects.create(
    name='smoke', taxonomy=Taxonomy.objects.get(is_active=True),
    phase2_model='m2', phase2_prompt=gen, phase3_model='m3', phase3_prompt=lab,
    n_systems=1, n_scenarios_per_system=2)
exp.seed_systems.set(SystemDescription.objects.filter(origin=Origin.SEED))
exp.full_clean()
print('experiment validates against the real seed pack:', exp.name, exp.variants)
Experiment.objects.filter(name='smoke').delete()
PromptTemplate.objects.filter(name__startswith='smoke-').delete()
"
```

Expected: prints that the experiment validates, with variants `['A', 'B']`.

- [x] **Step 6: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

## Done when

- `cd backend && uv run pytest` is green and `uv run ruff format --check . && uv run ruff check .` is clean.
- `manage.py run_experiment <name> --repetitions N --inline` runs end to end on `FakeLLMClient` with no network.
- Generated material carries `origin="generated"` and `generation_run`, and the same `scenario_id` may exist once per run against one target system.
- **Variant A is never given the taxonomy**, and every Run A call records `leak_scan_ran=True` with the taxonomy it was scanned against.
- Out-of-vocabulary `hcv_id`s are recorded on the call, never dropped; an empty label list is a success.
- No `LLMCall` column contains an API key, including `response_json` and `transport_error`.
- Retrying a partially failed Phase 3 re-runs only the failed scenarios and creates no duplicate labels.

Plan 3b adds the REST API (`/api/experiments/`, run launch, phase-run status,
retry, calls, labels) and the export bundle, consuming
`pipeline.orchestration.launch/execute_pipeline_run/retry_failed`.
