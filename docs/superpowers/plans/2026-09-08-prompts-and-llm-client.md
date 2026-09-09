# Prompts and LLM Client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Versioned, hand-written Jinja2 prompt templates that render to the exact string sent to a model, with Run A's taxonomy isolation enforced structurally — plus the OpenRouter client that sends them.

**Architecture:** A new `prompts` Django app owns `PromptTemplate` (immutable versions; editing creates a new row) and `prompts/rendering.py`, the single renderer used by both the preview endpoint and, later, the pipeline — so what an admin previews is byte-identical to what gets sent. A new `backend/llm/` **plain Python package** (no models, no migrations) owns the provider client, its fake, and the Pydantic response schemas. Variant isolation lives in `build_context()`: a Run A context cannot bind the taxonomy at all, and a Run A template that reaches for it fails loudly instead of rendering an empty string.

**Tech Stack:** Django 5.2.17 LTS, DRF 3.18, Jinja2 3.1.6 (SandboxedEnvironment), pydantic 2.13.5, openai 3.8.0 pointed at OpenRouter, pytest-django, ruff.

**Spec:** `docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md`

**Builds on:** `docs/superpowers/plans/2026-09-07-backend-foundation.md` (complete: 127 tests).

## Global Constraints

- Python 3.13; Django 5.2.17; DRF 3.18.0. Managed by **uv** from `backend/pyproject.toml` — never `pip`, never edit `pyproject.toml`/`uv.lock`. Every command is `uv run …` from `backend/`.
- **ruff** is formatter and linter: finish each task with `uv run ruff format . && uv run ruff check --fix .`. Watch **DJ012** (model member order: fields → Meta → `__str__` → `save()` → custom methods), **B017** (no blind `pytest.raises(Exception)`), **F401/E402/F811** (imports at top, merged, none unused).
- Run **no** git state-changing commands (no `commit`, `add`, `restore`, `stash`, `checkout`). Read-only git is fine.
- DRF defaults are already `SessionAuthentication` + `IsAuthenticated`, with `PageNumberPagination` and `PAGE_SIZE = 50`. **List endpoints return `{count, next, previous, results}`** — assert on `["results"]`, never a bare array.
- `is_staff` gates prompt editing (spec §7).
- **Variant isolation is the property this whole plan exists to protect** (spec §1): a Run A render must never see the taxonomy, directly or transitively.
- **The OpenRouter key must never reach a stored payload** (spec §6): `request_json` carries no `Authorization` header, and the client asserts no stored payload contains `sk-or-`.
- Tests are pytest-django, written before implementation (TDD).

## Interfaces this plan consumes (verified present)

```
core.keys           resolve_openrouter_key() -> str   (raises MissingAPIKey)
                    key_source() -> "settings"|"environment"|"none"
                    mask_key(value) -> str
                    MissingAPIKey
taxonomy.models     Taxonomy(vocabulary_id, version, is_active), Taxonomy.active()
                    HCV(taxonomy, hcv_id, name, definition, condition_classes, order)
corpus.models       ResourceFile(name unique, text)
                    FewShotExample(phase, name, order, is_active,
                                   resolved_input, resolved_output)
                    SystemDescription(title, body, origin), Scenario(...)
config.settings     RESOURCES_DIR, OPENROUTER_API_KEY
```

---

## File Structure

```
backend/
  prompts/                       NEW Django app
    __init__.py  apps.py  admin.py
    models.py                    PromptTemplate + PromptTemplateManager
    rendering.py                 sandboxed env, globals, build_context, render
    serializers.py  views.py  urls.py
    migrations/
    tests/  test_models.py  test_rendering.py  test_isolation.py  test_api.py
  llm/                           NEW plain package — no models, no migrations
    __init__.py
    schemas.py                   Pydantic Phase 2 / Phase 3 response models
    client.py                    LLMResult, LLMClient protocol, OpenRouterClient
    fake.py                      FakeLLMClient
    models_api.py                cached OpenRouter model catalogue
    tests/  test_schemas.py  test_client.py  test_fake.py  test_models_api.py
  config/settings.py             + "prompts" in INSTALLED_APPS, + CACHES
  config/urls.py                 + prompts.urls, + llm model-catalogue route
```

**Why `llm/` is a package, not an app:** spec §4 sketches the client inside
`pipeline/`, but it has no models and no migrations, and it is consumed by this
plan's preview/catalogue work as well as by plan 3's tasks. Keeping it outside
the app that owns the pipeline's migrations lets plan 2 ship it with no
migration at all. Plan 3 imports `from llm.client import …` unchanged.

---

### Task 1: PromptTemplate with immutable versions

**Files:**
- Create: `backend/prompts/__init__.py`, `apps.py`, `models.py`, `admin.py`, `migrations/__init__.py`, `tests/__init__.py`, `tests/test_models.py`
- Modify: `backend/config/settings.py` (add `"prompts"` to `INSTALLED_APPS`, after `"corpus"`)

**Interfaces:**
- Consumes: `core.User`.
- Produces: `prompts.models.PromptTemplate` with fields `phase, name, version, system_prompt, user_prompt, output_schema, is_active, created_by, created_at`; manager method `PromptTemplate.objects.create_version(phase=…, name=…, **fields)`; classmethod `PromptTemplate.latest(phase, name)`; instance method `template.new_version(**changes)`.

- [ ] **Step 1: Write the failing test**

Create `backend/prompts/tests/test_models.py`:

```python
import pytest
from django.db import IntegrityError

from prompts.models import PromptTemplate


@pytest.fixture
def first(db):
    return PromptTemplate.objects.create_version(
        phase=2, name="generate", user_prompt="Write {{ n }} scenarios."
    )


@pytest.mark.django_db
def test_first_version_is_one(first):
    assert first.version == 1


@pytest.mark.django_db
def test_each_create_version_increments(first):
    second = PromptTemplate.objects.create_version(
        phase=2, name="generate", user_prompt="Changed."
    )
    assert second.version == 2
    assert PromptTemplate.objects.filter(phase=2, name="generate").count() == 2


@pytest.mark.django_db
def test_versions_are_per_name_and_phase(first):
    other_name = PromptTemplate.objects.create_version(
        phase=2, name="other", user_prompt="x"
    )
    other_phase = PromptTemplate.objects.create_version(
        phase=3, name="generate", user_prompt="x"
    )
    assert other_name.version == 1
    assert other_phase.version == 1


@pytest.mark.django_db
def test_new_version_copies_forward_and_applies_changes(first):
    second = first.new_version(user_prompt="Rewritten.")

    assert second.version == 2
    assert second.user_prompt == "Rewritten."
    assert second.name == first.name
    assert second.phase == first.phase
    first.refresh_from_db()
    assert first.user_prompt == "Write {{ n }} scenarios."


@pytest.mark.django_db
def test_latest_returns_the_highest_version(first):
    first.new_version(user_prompt="v2")
    third = first.new_version(user_prompt="v3")
    assert PromptTemplate.latest(2, "generate").pk == third.pk


@pytest.mark.django_db
def test_latest_is_none_when_nothing_exists(db):
    assert PromptTemplate.latest(2, "absent") is None


@pytest.mark.django_db
def test_a_duplicate_version_is_refused(first):
    with pytest.raises(IntegrityError):
        PromptTemplate.objects.create(
            phase=2, name="generate", version=1, user_prompt="clash"
        )


@pytest.mark.django_db
def test_str_names_the_version(first):
    assert str(first) == "[phase 2] generate v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest prompts/tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prompts'`.

- [ ] **Step 3: Write the implementation**

`backend/prompts/__init__.py`, `backend/prompts/migrations/__init__.py`,
`backend/prompts/tests/__init__.py`: all empty.

`backend/prompts/apps.py`:

```python
from django.apps import AppConfig


class PromptsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "prompts"
```

`backend/prompts/models.py`:

```python
import copy

from django.db import models


class PromptTemplateManager(models.Manager):
    def create_version(self, *, phase, name, **fields):
        """Create the next version of a named template.

        Versions are per (phase, name). Two concurrent calls can race to the
        same number, which the unique constraint turns into an IntegrityError
        rather than a silent overwrite — the loud failure is the point.
        """
        highest = self.filter(phase=phase, name=name).aggregate(
            models.Max("version")
        )["version__max"]
        return self.create(phase=phase, name=name, version=(highest or 0) + 1, **fields)


class PromptTemplate(models.Model):
    """A hand-written prompt, versioned so a run can pin exact wording.

    Editing never mutates a row: `new_version()` copies forward. A run stores
    the id it used, so the text behind any stored result stays recoverable.
    """

    PHASE_CHOICES = [(2, "Phase 2 — generation"), (3, "Phase 3 — labelling")]
    COPIED_FIELDS = ("phase", "name", "system_prompt", "user_prompt", "output_schema")

    phase = models.PositiveSmallIntegerField(choices=PHASE_CHOICES, db_index=True)
    name = models.CharField(max_length=200)
    version = models.PositiveIntegerField()
    system_prompt = models.TextField(blank=True, default="")
    user_prompt = models.TextField()
    output_schema = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        "core.User", null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = PromptTemplateManager()

    class Meta:
        ordering = ["phase", "name", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["phase", "name", "version"], name="unique_prompt_version"
            )
        ]

    def __str__(self):
        return f"[phase {self.phase}] {self.name} v{self.version}"

    @classmethod
    def latest(cls, phase, name):
        return cls.objects.filter(phase=phase, name=name).order_by("-version").first()

    def new_version(self, **changes):
        """Copy this template forward with `changes` applied. Never mutates self.

        Deep-copied because output_schema is a mutable dict: a shallow copy would
        leave both versions' in-memory attributes pointing at one object, so
        editing the new version's schema would silently edit the old one's.
        """
        fields = {
            field: copy.deepcopy(getattr(self, field)) for field in self.COPIED_FIELDS
        }
        fields.update(changes)
        phase = fields.pop("phase")
        name = fields.pop("name")
        return PromptTemplate.objects.create_version(phase=phase, name=name, **fields)
```

`backend/prompts/admin.py`:

```python
from django.contrib import admin

from prompts.models import PromptTemplate


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "phase", "version", "is_active", "created_at")
    list_filter = ("phase", "is_active")
    search_fields = ("name", "system_prompt", "user_prompt")
    readonly_fields = ("version", "created_at")
```

Add `"prompts"` to `INSTALLED_APPS` in `backend/config/settings.py`, after `"corpus"`.

- [ ] **Step 4: Create the migration and run the tests**

Run:

```bash
cd backend
uv run python manage.py makemigrations prompts
uv run pytest prompts/tests/test_models.py -v
```

Expected: `prompts/migrations/0001_initial.py` created; all eight tests PASS.

- [ ] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

Expected: clean. Do not run any git command.

---

### Task 2: Sandboxed renderer and variant isolation

**Files:**
- Create: `backend/prompts/rendering.py`, `backend/prompts/tests/test_rendering.py`, `backend/prompts/tests/test_isolation.py`

**Interfaces:**
- Consumes: `PromptTemplate` (Task 1); `corpus.models.ResourceFile`, `corpus.models.FewShotExample`; `taxonomy.models.Taxonomy`.
- Produces: `prompts.rendering.RenderedPrompt` (dataclass with `.system` and `.user`); `render(template, context) -> RenderedPrompt`; `build_phase2_context(...) -> dict`; `build_phase3_context(...) -> dict`; `format_hcv_table(taxonomy) -> str`; exceptions `TaxonomyLeak` and `PromptRenderError`.

- [ ] **Step 1: Write the failing rendering test**

Create `backend/prompts/tests/test_rendering.py`:

```python
import pytest

from corpus.models import FewShotExample, Origin, ResourceFile, SystemDescription
from prompts.models import PromptTemplate
from prompts.rendering import (
    PromptRenderError,
    build_phase2_context,
    format_hcv_table,
    render,
)
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(
        taxonomy=tax,
        hcv_id="HCV-07",
        name="Malicious Insiders",
        definition="Authorized insiders misuse access to compromise systems.",
        condition_classes=["UnconstrainedInsiderAccessCondition"],
        order=7,
    )
    HCV.objects.create(
        taxonomy=tax, hcv_id="HCV-09", name="Propaganda", definition="d", order=9
    )
    return tax


@pytest.fixture
def system(db):
    return SystemDescription.objects.create(
        title="Oldsmar", body="A water plant.", origin=Origin.SEED
    )


def make(user_prompt, system_prompt=""):
    return PromptTemplate(phase=2, name="t", version=1,
                          user_prompt=user_prompt, system_prompt=system_prompt)


@pytest.mark.django_db
def test_renders_both_prompts(system):
    context = build_phase2_context(
        variant="A", seed_systems=[system], fewshots=[],
        generate_systems=True, generate_scenarios=True,
        n_systems=3, n_scenarios_per_system=5,
    )
    rendered = render(make("Make {{ n_systems }} systems.", "You are careful."), context)

    assert rendered.system == "You are careful."
    assert rendered.user == "Make 3 systems."


@pytest.mark.django_db
def test_seed_systems_expose_title_and_body(system):
    context = build_phase2_context(
        variant="A", seed_systems=[system], fewshots=[],
        generate_systems=True, generate_scenarios=True,
        n_systems=1, n_scenarios_per_system=1,
    )
    template = make("{% for s in seed_systems %}{{ s.title }}::{{ s.body }}{% endfor %}")
    assert render(template, context).user == "Oldsmar::A water plant."


@pytest.mark.django_db
def test_fewshots_expose_resolved_input_and_output(system):
    example = FewShotExample.objects.create(
        phase=2, name="one", input_text="IN", output_text="OUT"
    )
    context = build_phase2_context(
        variant="A", seed_systems=[], fewshots=[example],
        generate_systems=True, generate_scenarios=True,
        n_systems=1, n_scenarios_per_system=1,
    )
    template = make("{% for f in fewshots %}{{ f.input }}|{{ f.output }}{% endfor %}")
    assert render(template, context).user == "IN|OUT"


@pytest.mark.django_db
def test_resource_global_reads_a_resource_file_by_name(system):
    ResourceFile.objects.create(name="vocab", text="the vocabulary")
    context = build_phase2_context(
        variant="A", seed_systems=[], fewshots=[],
        generate_systems=True, generate_scenarios=True,
        n_systems=1, n_scenarios_per_system=1,
    )
    assert render(make('{{ resource("vocab") }}'), context).user == "the vocabulary"


@pytest.mark.django_db
def test_resource_global_names_the_missing_resource(system):
    context = build_phase2_context(
        variant="A", seed_systems=[], fewshots=[],
        generate_systems=True, generate_scenarios=True,
        n_systems=1, n_scenarios_per_system=1,
    )
    with pytest.raises(PromptRenderError, match="absent"):
        render(make('{{ resource("absent") }}'), context)


@pytest.mark.django_db
def test_an_undefined_variable_fails_loudly(system):
    """A typo must not silently render an empty prompt."""
    context = build_phase2_context(
        variant="A", seed_systems=[], fewshots=[],
        generate_systems=True, generate_scenarios=True,
        n_systems=1, n_scenarios_per_system=1,
    )
    with pytest.raises(PromptRenderError, match="n_systmes"):
        render(make("{{ n_systmes }}"), context)


@pytest.mark.django_db
def test_the_sandbox_blocks_attribute_escapes(system):
    context = build_phase2_context(
        variant="A", seed_systems=[], fewshots=[],
        generate_systems=True, generate_scenarios=True,
        n_systems=1, n_scenarios_per_system=1,
    )
    with pytest.raises(PromptRenderError):
        render(make("{{ n_systems.__class__.__mro__ }}"), context)


@pytest.mark.django_db
def test_format_hcv_table_lists_every_row(taxonomy):
    table = format_hcv_table(taxonomy)
    assert "HCV-07" in table
    assert "Malicious Insiders" in table
    assert "Authorized insiders misuse access" in table
    assert "HCV-09" in table


@pytest.mark.django_db
def test_target_system_is_bound_when_not_generating_systems(system):
    context = build_phase2_context(
        variant="A", seed_systems=[], fewshots=[],
        generate_systems=False, generate_scenarios=True,
        n_systems=0, n_scenarios_per_system=4, target_system=system,
    )
    assert render(make("{{ target_system.title }}"), context).user == "Oldsmar"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest prompts/tests/test_rendering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prompts.rendering'`.

- [ ] **Step 3: Write the failing isolation test**

Create `backend/prompts/tests/test_isolation.py`:

```python
import pytest

from prompts.models import PromptTemplate
from prompts.rendering import (
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    render,
)
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    for index, (code, label) in enumerate(
        [("HCV-06", "Susceptibility to Deceptive Communication"),
         ("HCV-07", "Malicious Insiders")],
        start=6,
    ):
        HCV.objects.create(taxonomy=tax, hcv_id=code, name=label, order=index)
    return tax


def make(user_prompt):
    return PromptTemplate(phase=2, name="t", version=1, user_prompt=user_prompt)


def base(variant, taxonomy=None):
    return build_phase2_context(
        variant=variant, seed_systems=[], fewshots=[],
        generate_systems=True, generate_scenarios=True,
        n_systems=1, n_scenarios_per_system=1, taxonomy=taxonomy,
    )


@pytest.mark.django_db
def test_variant_a_refuses_a_taxonomy_outright(taxonomy):
    """The isolation guarantee: a Run A context cannot even hold the taxonomy."""
    with pytest.raises(TaxonomyLeak, match="Run A"):
        base("A", taxonomy=taxonomy)


@pytest.mark.django_db
def test_variant_b_requires_a_taxonomy():
    with pytest.raises(TaxonomyLeak, match="Run B"):
        base("B", taxonomy=None)


@pytest.mark.django_db
def test_variant_a_has_no_taxonomy_variable(taxonomy):
    with pytest.raises(PromptRenderError, match="taxonomy"):
        render(make("{{ taxonomy }}"), base("A"))


@pytest.mark.django_db
def test_variant_a_cannot_call_hcv_table(taxonomy):
    with pytest.raises(PromptRenderError, match="hcv_table"):
        render(make("{{ hcv_table() }}"), base("A"))


@pytest.mark.django_db
def test_variant_b_can_call_hcv_table(taxonomy):
    rendered = render(make("{{ hcv_table() }}"), base("B", taxonomy=taxonomy))
    assert "HCV-07" in rendered.user
    assert "Malicious Insiders" in rendered.user


@pytest.mark.django_db
def test_no_hcv_identifier_or_name_survives_into_a_variant_a_render(taxonomy):
    """Belt and braces: whatever the template says, Run A output is taxonomy-free."""
    template = make(
        "Systems: {{ n_systems }}. Scenarios: {{ n_scenarios_per_system }}."
    )
    rendered = render(template, base("A"))
    blob = f"{rendered.system}\n{rendered.user}"

    for row in HCV.objects.all():
        assert row.hcv_id not in blob
        assert row.name not in blob


@pytest.mark.django_db
def test_variant_b_context_carries_the_rows(taxonomy):
    context = base("B", taxonomy=taxonomy)
    assert [row["hcv_id"] for row in context["taxonomy"]] == ["HCV-06", "HCV-07"]
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend && uv run pytest prompts/tests/test_isolation.py -v`
Expected: FAIL — `ImportError: cannot import name 'TaxonomyLeak'`.

- [ ] **Step 5: Write the implementation**

`backend/prompts/rendering.py`:

```python
"""Rendering prompts, and the structural guarantee that Run A never sees the taxonomy.

The experiment compares what a model produces WITHOUT the taxonomy (Run A) against
what it produces WITH it (Run B). If the taxonomy leaked into a Run A prompt the
comparison would be worthless, so isolation is enforced here at the context layer
rather than left to whoever writes the template: a Run A context refuses to hold a
taxonomy at all, and `hcv_table()` is simply not defined for it.
"""

from dataclasses import dataclass

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from corpus.models import ResourceFile

VARIANT_UNBIASED = "A"
VARIANT_GUIDED = "B"


class TaxonomyLeak(RuntimeError):
    """Raised when a context pairs a variant with the wrong taxonomy state."""


class PromptRenderError(RuntimeError):
    """Raised when a template fails to render. Never rendered partially."""


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str


def format_hcv_table(taxonomy):
    """The taxonomy as a readable numbered list, for injection into Run B prompts."""
    return "\n".join(
        f"{row.hcv_id}: {row.name}" + (f" — {row.definition}" if row.definition else "")
        for row in taxonomy.hcvs.all()
    )


def _taxonomy_rows(taxonomy):
    return [
        {
            "hcv_id": row.hcv_id,
            "name": row.name,
            "definition": row.definition,
            "condition_classes": row.condition_classes,
        }
        for row in taxonomy.hcvs.all()
    ]


def _fewshot_rows(fewshots):
    return [
        {"name": f.name, "input": f.resolved_input, "output": f.resolved_output}
        for f in fewshots
    ]


def _system_rows(systems):
    return [{"title": s.title, "body": s.body, "slug": s.slug} for s in systems]


def _resource(name):
    try:
        return ResourceFile.objects.get(name=name).text
    except ResourceFile.DoesNotExist as error:
        raise PromptRenderError(
            f'resource("{name}") does not exist. Upload it first, or fix the name.'
        ) from error


def build_phase2_context(
    *,
    variant,
    seed_systems,
    fewshots,
    generate_systems,
    generate_scenarios,
    n_systems,
    n_scenarios_per_system,
    target_system=None,
    taxonomy=None,
):
    """Build the Phase 2 template context, enforcing variant isolation.

    Run A must be given no taxonomy; Run B must be given one. Both mistakes raise
    rather than quietly producing a prompt that measures the wrong thing.
    """
    if variant == VARIANT_UNBIASED and taxonomy is not None:
        raise TaxonomyLeak(
            "Run A is the unbiased variant and must not receive a taxonomy. "
            "Pass taxonomy=None, or run this as variant B."
        )
    if variant == VARIANT_GUIDED and taxonomy is None:
        raise TaxonomyLeak("Run B is the guided variant and requires a taxonomy.")
    if variant not in (VARIANT_UNBIASED, VARIANT_GUIDED):
        raise TaxonomyLeak(f"Unknown variant {variant!r}; expected 'A' or 'B'.")

    context = {
        "seed_systems": _system_rows(seed_systems),
        "fewshots": _fewshot_rows(fewshots),
        "generate_systems": generate_systems,
        "generate_scenarios": generate_scenarios,
        "n_systems": n_systems,
        "n_scenarios_per_system": n_scenarios_per_system,
        "target_system": _system_rows([target_system])[0] if target_system else None,
    }
    if taxonomy is not None:
        context["taxonomy"] = _taxonomy_rows(taxonomy)
        context["_taxonomy_object"] = taxonomy
    return context


def build_phase3_context(*, scenario, system_description, taxonomy, fewshots):
    """Phase 3 always sees the taxonomy — labelling chooses from a closed list."""
    if taxonomy is None:
        raise TaxonomyLeak("Phase 3 labelling requires a taxonomy.")
    return {
        "scenario": {
            "scenario_id": scenario.scenario_id,
            **scenario.card,
            "narrative": scenario.narrative,
        },
        "system_description": _system_rows([system_description])[0],
        "taxonomy": _taxonomy_rows(taxonomy),
        "fewshots": _fewshot_rows(fewshots),
        "_taxonomy_object": taxonomy,
    }


def _environment(context):
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    env.globals["resource"] = _resource
    taxonomy = context.get("_taxonomy_object")
    if taxonomy is not None:
        env.globals["hcv_table"] = lambda: format_hcv_table(taxonomy)
    return env


def render(template, context):
    """Render both halves of a template. This is the ONLY renderer.

    Preview and the pipeline both call it, so what an admin sees previewed is
    byte-identical to what is sent.
    """
    env = _environment(context)
    exposed = {key: value for key, value in context.items() if not key.startswith("_")}
    try:
        return RenderedPrompt(
            system=env.from_string(template.system_prompt).render(**exposed),
            user=env.from_string(template.user_prompt).render(**exposed),
        )
    except PromptRenderError:
        raise
    except TemplateError as error:
        raise PromptRenderError(str(error)) from error
```

- [ ] **Step 6: Run both test files to verify they pass**

Run: `cd backend && uv run pytest prompts/tests/ -v`
Expected: all tests PASS.

- [ ] **Step 7: Run the whole suite and lint**

```bash
cd backend
uv run pytest
uv run ruff format . && uv run ruff check --fix .
```

Expected: 127 prior tests plus the new ones, all green; ruff clean.

---

### Task 3: Prompt API with version-on-edit and preview

**Files:**
- Create: `backend/prompts/serializers.py`, `backend/prompts/views.py`, `backend/prompts/urls.py`, `backend/prompts/tests/test_api.py`
- Modify: `backend/config/urls.py` (add `path("api/", include("prompts.urls"))` as a fourth include)

**Interfaces:**
- Consumes: `PromptTemplate` (Task 1); `render`, `build_phase2_context`, `build_phase3_context`, `TaxonomyLeak`, `PromptRenderError` (Task 2).
- Produces: `GET/POST /api/prompts/` (list filterable by `?phase=`, paginated), `GET /api/prompts/{id}/`, `PATCH /api/prompts/{id}/` returning **201 with a NEW version**, `POST /api/prompts/{id}/preview/` returning `{"system_prompt": str, "user_prompt": str}`.

- [ ] **Step 1: Write the failing test**

Create `backend/prompts/tests/test_api.py`:

```python
import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, SystemDescription
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
def template(db):
    return PromptTemplate.objects.create_version(
        phase=2, name="generate", user_prompt="Make {{ n_systems }} systems."
    )


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.mark.django_db
def test_listing_requires_authentication(api_client, template):
    assert api_client.get("/api/prompts/").status_code == 403


@pytest.mark.django_db
def test_any_authenticated_user_may_read(api_client, member, template):
    api_client.force_authenticate(user=member)
    body = api_client.get("/api/prompts/").json()
    assert body["count"] == 1
    assert body["results"][0]["name"] == "generate"


@pytest.mark.django_db
def test_non_staff_cannot_create(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/prompts/", {"phase": 2, "name": "x", "user_prompt": "y"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_creates_version_one(api_client, staff):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/",
        {"phase": 2, "name": "fresh", "user_prompt": "y"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["version"] == 1
    assert PromptTemplate.objects.get(name="fresh").created_by == staff


@pytest.mark.django_db
def test_patch_creates_a_new_version_and_leaves_the_original(api_client, staff, template):
    api_client.force_authenticate(user=staff)
    response = api_client.patch(
        f"/api/prompts/{template.id}/", {"user_prompt": "Rewritten."}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    assert response.json()["user_prompt"] == "Rewritten."
    assert response.json()["id"] != template.id

    template.refresh_from_db()
    assert template.user_prompt == "Make {{ n_systems }} systems."


@pytest.mark.django_db
def test_filter_by_phase(api_client, member, template):
    PromptTemplate.objects.create_version(phase=3, name="label", user_prompt="z")
    api_client.force_authenticate(user=member)
    rows = api_client.get("/api/prompts/?phase=3").json()["results"]
    assert [row["name"] for row in rows] == ["label"]


@pytest.mark.django_db
def test_bad_phase_filter_is_a_400(api_client, member, template):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/prompts/?phase=abc").status_code == 400


@pytest.mark.django_db
def test_preview_returns_the_exact_rendered_strings(api_client, staff, template):
    system = SystemDescription.objects.create(
        title="Oldsmar", body="A plant.", origin=Origin.SEED
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "n_systems": 4, "n_scenarios_per_system": 5,
         "seed_systems": [system.id]},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["user_prompt"] == "Make 4 systems."
    assert response.json()["system_prompt"] == ""


@pytest.mark.django_db
def test_preview_of_variant_a_with_a_taxonomy_is_refused(api_client, staff, template, taxonomy):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "taxonomy": taxonomy.id, "n_systems": 1,
         "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 400
    assert "Run A" in str(response.json())


@pytest.mark.django_db
def test_preview_of_variant_b_injects_the_taxonomy(api_client, staff, taxonomy):
    guided = PromptTemplate.objects.create_version(
        phase=2, name="guided", user_prompt="{{ hcv_table() }}"
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{guided.id}/preview/",
        {"variant": "B", "taxonomy": taxonomy.id, "n_systems": 1,
         "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 200
    assert "HCV-07" in response.json()["user_prompt"]


@pytest.mark.django_db
def test_preview_reports_a_template_error_as_400(api_client, staff):
    broken = PromptTemplate.objects.create_version(
        phase=2, name="broken", user_prompt="{{ nope }}"
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{broken.id}/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 400
    assert "nope" in str(response.json())


@pytest.mark.django_db
def test_preview_of_a_phase_3_template_uses_a_scenario(api_client, staff, taxonomy):
    from corpus.models import Scenario

    system = SystemDescription.objects.create(
        title="Oldsmar", body="A plant.", origin=Origin.SEED
    )
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", actor="EP1",
        threat="Spoofing.", narrative="1. EP1 acts.", origin=Origin.SEED, order=1,
    )
    labeller = PromptTemplate.objects.create_version(
        phase=3, name="label", user_prompt="{{ scenario.scenario_id }}/{{ scenario.actor }}"
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{labeller.id}/preview/",
        {"taxonomy": taxonomy.id, "scenario": scenario.id},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["user_prompt"] == "sc_01/EP1"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest prompts/tests/test_api.py -v`
Expected: FAIL — 404 on `/api/prompts/`.

- [ ] **Step 3: Write the implementation**

`backend/prompts/serializers.py`:

```python
from rest_framework import serializers

from prompts.models import PromptTemplate


class PromptTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = PromptTemplate
        fields = [
            "id",
            "phase",
            "name",
            "version",
            "system_prompt",
            "user_prompt",
            "output_schema",
            "is_active",
            "created_by",
            "created_at",
        ]
        # A version is immutable; editing produces a new row, never a mutation.
        read_only_fields = ["version", "created_by", "created_at"]


class PreviewSerializer(serializers.Serializer):
    """The configuration a preview renders against.

    Mirrors what a run would supply, so the preview is not a separate code path.
    """

    variant = serializers.ChoiceField(choices=["A", "B"], required=False)
    taxonomy = serializers.IntegerField(required=False, allow_null=True)
    seed_systems = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    fewshots = serializers.ListField(
        child=serializers.IntegerField(), required=False, default=list
    )
    generate_systems = serializers.BooleanField(required=False, default=True)
    generate_scenarios = serializers.BooleanField(required=False, default=True)
    n_systems = serializers.IntegerField(required=False, default=3)
    n_scenarios_per_system = serializers.IntegerField(required=False, default=5)
    target_system = serializers.IntegerField(required=False, allow_null=True)
    scenario = serializers.IntegerField(required=False, allow_null=True)
```

`backend/prompts/views.py`:

```python
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response

from corpus.models import FewShotExample, Scenario, SystemDescription
from prompts.models import PromptTemplate
from prompts.rendering import (
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    build_phase3_context,
    render,
)
from prompts.serializers import PreviewSerializer, PromptTemplateSerializer
from taxonomy.models import Taxonomy


class StaffWrites(BasePermission):
    """Anyone signed in may read a prompt; only staff may author one."""

    message = "Only staff may create or edit prompt templates."

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or bool(request.user.is_staff)


class PromptTemplateViewSet(viewsets.ModelViewSet):
    serializer_class = PromptTemplateSerializer
    permission_classes = [IsAuthenticated, StaffWrites]

    def get_queryset(self):
        queryset = PromptTemplate.objects.all()
        phase = self.request.query_params.get("phase")
        if phase in (None, ""):
            return queryset
        try:
            return queryset.filter(phase=int(phase))
        except (TypeError, ValueError) as error:
            raise ValidationError({"phase": f"Must be an integer, got {phase!r}."}) from error

    def perform_create(self, serializer):
        data = dict(serializer.validated_data)
        phase = data.pop("phase")
        name = data.pop("name")
        serializer.instance = PromptTemplate.objects.create_version(
            phase=phase, name=name, created_by=self.request.user, **data
        )

    def update(self, request, *args, **kwargs):
        """Editing a template creates the next version rather than mutating this one.

        A run pins a template id, so mutating a row in place would rewrite the
        wording behind results that already exist.
        """
        template = self.get_object()
        serializer = self.get_serializer(template, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = {
            field: value
            for field, value in serializer.validated_data.items()
            if field in PromptTemplate.COPIED_FIELDS
        }
        created = template.new_version(created_by=request.user, **changes)
        return Response(
            self.get_serializer(created).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["post"])
    def preview(self, request, pk=None):
        """Render this template exactly as a run would, and return both strings."""
        template = self.get_object()
        options = PreviewSerializer(data=request.data)
        options.is_valid(raise_exception=True)
        config = options.validated_data

        taxonomy = (
            get_object_or_404(Taxonomy, pk=config["taxonomy"])
            if config.get("taxonomy")
            else None
        )
        try:
            context = self._context(template, config, taxonomy)
            rendered = render(template, context)
        except (TaxonomyLeak, PromptRenderError) as error:
            raise ValidationError({"detail": str(error)}) from error

        return Response(
            {"system_prompt": rendered.system, "user_prompt": rendered.user}
        )

    def _context(self, template, config, taxonomy):
        if template.phase == 3:
            if not config.get("scenario"):
                raise ValidationError(
                    {"scenario": "Previewing a phase 3 template requires a scenario id."}
                )
            scenario = get_object_or_404(Scenario, pk=config["scenario"])
            return build_phase3_context(
                scenario=scenario,
                system_description=scenario.system_description,
                taxonomy=taxonomy,
                fewshots=self._fewshots(config, phase=3),
            )
        target = (
            get_object_or_404(SystemDescription, pk=config["target_system"])
            if config.get("target_system")
            else None
        )
        return build_phase2_context(
            variant=config.get("variant", "A"),
            seed_systems=SystemDescription.objects.filter(
                pk__in=config.get("seed_systems", [])
            ),
            fewshots=self._fewshots(config, phase=2),
            generate_systems=config["generate_systems"],
            generate_scenarios=config["generate_scenarios"],
            n_systems=config["n_systems"],
            n_scenarios_per_system=config["n_scenarios_per_system"],
            target_system=target,
            taxonomy=taxonomy,
        )

    def _fewshots(self, config, *, phase):
        return FewShotExample.objects.filter(
            pk__in=config.get("fewshots", []), phase=phase
        )
```

`backend/prompts/urls.py`:

```python
from rest_framework.routers import DefaultRouter

from prompts.views import PromptTemplateViewSet

router = DefaultRouter()
router.register("prompts", PromptTemplateViewSet, basename="prompt")

urlpatterns = router.urls
```

Add to `backend/config/urls.py`'s `urlpatterns`:

```python
    path("api/", include("prompts.urls")),
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest prompts/tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite and lint**

```bash
cd backend
uv run pytest
uv run ruff format . && uv run ruff check --fix .
```

Expected: everything green, ruff clean.

---

### Task 4: Pydantic response schemas

**Files:**
- Create: `backend/llm/__init__.py`, `backend/llm/schemas.py`, `backend/llm/tests/__init__.py`, `backend/llm/tests/test_schemas.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `llm.schemas.ScenarioCard`, `GeneratedSystem`, `Phase2Response`, `LabelOut`, `Phase3Response`; `json_schema(model) -> dict`; `parse_phase2(payload) -> Phase2Response`; `parse_phase3(payload) -> Phase3Response`; `split_known_labels(response, allowed_ids) -> (known, unknown)`; exception `SchemaError`.

- [ ] **Step 1: Write the failing test**

Create `backend/llm/tests/test_schemas.py`:

```python
import pytest

from llm.schemas import (
    Phase2Response,
    Phase3Response,
    SchemaError,
    json_schema,
    parse_phase2,
    parse_phase3,
    split_known_labels,
)

CARD = {
    "scenario_id": "sc_01",
    "actor": "EP1 (ExternalParty)",
    "trigger": "After 22:00.",
    "standing_state": "One shared password.",
    "asset": "The SCADA Service",
    "threat": "Spoofing, then Tampering.",
    "outcome": "The feed ran at 11,100 ppm for 8 hours.",
    "narrative_steps": ["EP1 opens the session.", "EP1 sends a setpoint."],
}


def test_parses_systems_with_nested_scenarios():
    parsed = parse_phase2({"system_descriptions": [
        {"title": "Oldsmar", "body": "A plant.", "scenarios": [CARD]}
    ]})
    assert parsed.system_descriptions[0].title == "Oldsmar"
    assert parsed.system_descriptions[0].scenarios[0].scenario_id == "sc_01"


def test_parses_a_bare_scenario_list_for_the_target_system_mode():
    parsed = parse_phase2({"scenarios": [CARD]})
    assert parsed.system_descriptions == []
    assert parsed.scenarios[0].threat == "Spoofing, then Tampering."


def test_a_system_with_no_scenarios_is_valid():
    parsed = parse_phase2({"system_descriptions": [{"title": "T", "body": "B"}]})
    assert parsed.system_descriptions[0].scenarios == []


def test_narrative_steps_default_to_empty():
    card = {key: value for key, value in CARD.items() if key != "narrative_steps"}
    assert parse_phase2({"scenarios": [card]}).scenarios[0].narrative_steps == []


def test_a_missing_scenario_id_is_a_schema_error():
    card = {key: value for key, value in CARD.items() if key != "scenario_id"}
    with pytest.raises(SchemaError, match="scenario_id"):
        parse_phase2({"scenarios": [card]})


def test_a_non_dict_payload_is_a_schema_error():
    with pytest.raises(SchemaError):
        parse_phase2(["not", "a", "dict"])


def test_parses_labels():
    parsed = parse_phase3(
        {"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]}
    )
    assert parsed.labels[0].hcv_id == "HCV-07"


def test_an_empty_label_list_is_valid_and_meaningful():
    """A scenario carrying no vulnerability is a real result, not an error."""
    assert parse_phase3({"labels": []}).labels == []


def test_missing_labels_key_defaults_to_empty():
    assert parse_phase3({}).labels == []


def test_split_known_labels_separates_out_of_vocabulary_ids():
    parsed = parse_phase3({"labels": [
        {"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"},
        {"hcv_id": "HCV-99", "evidence": "e", "rationale": "r"},
    ]})
    known, unknown = split_known_labels(parsed, {"HCV-07", "HCV-08"})

    assert [row.hcv_id for row in known] == ["HCV-07"]
    assert unknown == ["HCV-99"]


def test_json_schema_is_shaped_for_response_format():
    schema = json_schema(Phase2Response)
    assert schema["type"] == "object"
    assert "system_descriptions" in schema["properties"]

    schema3 = json_schema(Phase3Response)
    assert "labels" in schema3["properties"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest llm/tests/test_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm'`.

- [ ] **Step 3: Write the implementation**

`backend/llm/__init__.py` and `backend/llm/tests/__init__.py`: empty.

`backend/llm/schemas.py`:

```python
"""Response shapes the models are asked to produce, per spec section 8.

Malformed output is data, not a crash: parsing raises SchemaError, which the
caller records on the call record rather than letting it kill a run.
"""

from pydantic import BaseModel, Field, ValidationError


class SchemaError(ValueError):
    """A model response did not match the requested schema."""


class ScenarioCard(BaseModel):
    scenario_id: str
    actor: str = ""
    trigger: str = ""
    standing_state: str = ""
    asset: str = ""
    threat: str = ""
    outcome: str = ""
    narrative_steps: list[str] = Field(default_factory=list)


class GeneratedSystem(BaseModel):
    title: str
    body: str = ""
    scenarios: list[ScenarioCard] = Field(default_factory=list)


class Phase2Response(BaseModel):
    system_descriptions: list[GeneratedSystem] = Field(default_factory=list)
    # Populated instead of system_descriptions when generating against a target.
    scenarios: list[ScenarioCard] = Field(default_factory=list)


class LabelOut(BaseModel):
    hcv_id: str
    evidence: str = ""
    rationale: str = ""


class Phase3Response(BaseModel):
    # An empty list is a valid, meaningful answer: this scenario carries none.
    labels: list[LabelOut] = Field(default_factory=list)


def json_schema(model):
    """The JSON Schema to pin into an OpenRouter response_format request."""
    return model.model_json_schema()


def _parse(model, payload):
    if not isinstance(payload, dict):
        raise SchemaError(f"Expected a JSON object, got {type(payload).__name__}.")
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise SchemaError(str(error)) from error


def parse_phase2(payload):
    return _parse(Phase2Response, payload)


def parse_phase3(payload):
    return _parse(Phase3Response, payload)


def split_known_labels(response, allowed_ids):
    """Separate in-vocabulary labels from invented ones.

    How often a labeller invents an id outside the closed list is itself a
    finding, so the unknowns are returned rather than dropped.
    """
    known, unknown = [], []
    for label in response.labels:
        (known if label.hcv_id in allowed_ids else unknown).append(label)
    return known, [label.hcv_id for label in unknown]
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest llm/tests/test_schemas.py -v`
Expected: all PASS.

- [ ] **Step 5: Format and lint**

```bash
cd backend && uv run ruff format . && uv run ruff check --fix .
```

---

### Task 5: OpenRouter client and its fake

**Files:**
- Create: `backend/llm/client.py`, `backend/llm/fake.py`, `backend/llm/tests/test_client.py`, `backend/llm/tests/test_fake.py`

**Interfaces:**
- Consumes: `core.keys.resolve_openrouter_key` only. The client takes `json_schema` as a plain dict parameter and imports nothing from `llm.schemas` — the caller (plan 3) is what pairs them.
- Produces: `llm.client.LLMResult` (frozen dataclass: `request_json, response_json, raw_response, model, finish_reason, prompt_tokens, completion_tokens, cost, latency_ms, http_status, transport_error, ok`); `llm.client.LLMClient` Protocol with `complete(*, model, system_prompt, user_prompt, temperature=0.0, json_schema=None, timeout=120) -> LLMResult`; `llm.client.OpenRouterClient`; `llm.client.SecretLeak`; `llm.fake.FakeLLMClient(responses=None)` with `.queue(text, **overrides)`, `.calls` and `.fail_next(...)`.

- [ ] **Step 1: Write the failing client test**

Create `backend/llm/tests/test_client.py`:

```python
import json

import pytest

from llm.client import LLMResult, OpenRouterClient, SecretLeak

SENTINEL_KEY = "sk-or-v1-SENTINELSENTINELSENTINEL"

BODY = {
    "id": "gen-abc123",
    "model": "anthropic/claude-sonnet-4",
    "provider": "Anthropic",
    "choices": [
        {"finish_reason": "stop", "message": {"role": "assistant", "content": '{"labels": []}'}}
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_cost": 0.00042},
}


class StubCompletions:
    """Stands in for openai's chat.completions.with_raw_response."""

    def __init__(self, body=None, error=None, status=200):
        self.body, self.error, self.status = body, error, status
        self.seen = None

    def create(self, **kwargs):
        self.seen = kwargs
        if self.error is not None:
            raise self.error
        return StubRaw(self.body, self.status)


class StubRaw:
    def __init__(self, body, status):
        self.text = json.dumps(body)
        self.status_code = status


def client_with(stub, key=SENTINEL_KEY):
    client = OpenRouterClient(api_key=key)
    client._create = stub.create  # the single seam onto the SDK
    return client


def test_a_successful_call_projects_every_column():
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        user_prompt="usr",
        temperature=0.2,
    )

    assert isinstance(result, LLMResult)
    assert result.ok is True
    assert result.raw_response == '{"labels": []}'
    assert result.response_json["id"] == "gen-abc123"
    assert result.model == "anthropic/claude-sonnet-4"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 8
    assert result.cost == pytest.approx(0.00042)
    assert result.latency_ms >= 0
    assert result.http_status == 200
    assert result.transport_error == ""


def test_the_whole_response_body_is_kept_verbatim():
    """Anything not modelled as a column stays recoverable from the dump."""
    result = client_with(StubCompletions(BODY)).complete(
        model="m", system_prompt="", user_prompt="u"
    )
    assert result.response_json["provider"] == "Anthropic"
    assert result.response_json == BODY


def test_the_request_body_is_recorded_without_the_key():
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(
        model="m", system_prompt="sys", user_prompt="usr", temperature=0.7
    )

    assert result.request_json["model"] == "m"
    assert result.request_json["temperature"] == 0.7
    assert result.request_json["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    blob = json.dumps(result.request_json)
    assert SENTINEL_KEY not in blob
    assert "sk-or-" not in blob
    assert "Authorization" not in blob


def test_an_empty_system_prompt_sends_no_system_message():
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")
    assert result.request_json["messages"] == [{"role": "user", "content": "u"}]


def test_a_json_schema_is_pinned_into_response_format():
    stub = StubCompletions(BODY)
    schema = {"type": "object", "properties": {"labels": {"type": "array"}}}
    client_with(stub).complete(
        model="m", system_prompt="", user_prompt="u", json_schema=schema
    )
    assert stub.seen["response_format"]["type"] == "json_schema"
    assert stub.seen["response_format"]["json_schema"]["schema"] == schema


def test_a_transport_failure_becomes_a_record_not_an_exception():
    stub = StubCompletions(error=RuntimeError("connection reset"))
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.response_json is None
    assert result.raw_response == ""
    assert "connection reset" in result.transport_error
    assert result.request_json["model"] == "m"


def test_a_failed_call_still_records_the_request_without_the_key():
    stub = StubCompletions(error=RuntimeError("boom"))
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")
    assert SENTINEL_KEY not in json.dumps(result.request_json)


def test_a_body_that_is_not_json_is_recorded_as_a_transport_error():
    class BadRaw:
        text = "<html>502 Bad Gateway</html>"
        status_code = 502

    class BadStub:
        seen = None

        def create(self, **kwargs):
            return BadRaw()

    result = client_with(BadStub()).complete(model="m", system_prompt="", user_prompt="u")
    assert result.ok is False
    assert result.http_status == 502
    assert "502 Bad Gateway" in result.transport_error


def test_a_key_appearing_in_a_payload_is_refused_outright():
    """Defence in depth: the storage guard fires even if a caller smuggles one in."""
    client = OpenRouterClient(api_key=SENTINEL_KEY)
    with pytest.raises(SecretLeak):
        client._guard({"messages": [{"role": "user", "content": SENTINEL_KEY}]})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest llm/tests/test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm.client'`.

- [ ] **Step 3: Write the failing fake test**

Create `backend/llm/tests/test_fake.py`:

```python
import pytest

from llm.fake import FakeLLMClient


def test_returns_queued_responses_in_order():
    client = FakeLLMClient()
    client.queue('{"labels": []}')
    client.queue('{"labels": [{"hcv_id": "HCV-07"}]}')

    first = client.complete(model="m", system_prompt="", user_prompt="a")
    second = client.complete(model="m", system_prompt="", user_prompt="b")

    assert first.raw_response == '{"labels": []}'
    assert "HCV-07" in second.raw_response


def test_records_every_call_for_assertions():
    client = FakeLLMClient()
    client.queue("ok")
    client.complete(model="anthropic/x", system_prompt="sys", user_prompt="usr",
                    temperature=0.3)

    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "anthropic/x"
    assert client.calls[0]["system_prompt"] == "sys"
    assert client.calls[0]["user_prompt"] == "usr"
    assert client.calls[0]["temperature"] == 0.3


def test_running_out_of_responses_is_a_loud_error():
    with pytest.raises(AssertionError, match="no queued response"):
        FakeLLMClient().complete(model="m", system_prompt="", user_prompt="u")


def test_fail_next_produces_a_transport_failure_record():
    client = FakeLLMClient()
    client.fail_next("rate limited", http_status=429)
    result = client.complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.response_json is None
    assert result.http_status == 429
    assert "rate limited" in result.transport_error


def test_queue_overrides_the_projected_columns():
    client = FakeLLMClient()
    client.queue("hi", finish_reason="length", prompt_tokens=11, completion_tokens=2)
    result = client.complete(model="m", system_prompt="", user_prompt="u")

    assert result.finish_reason == "length"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 2
    assert result.response_json["choices"][0]["message"]["content"] == "hi"
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend && uv run pytest llm/tests/test_fake.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm.fake'`.

- [ ] **Step 5: Write the implementation**

`backend/llm/client.py`:

```python
"""The OpenRouter client. Every call produces a record, successful or not.

The client never raises on a provider failure: a rate limit, a timeout or a 502
comes back as an LLMResult with `ok=False`, so a run stores a complete row rather
than a gap. The only exception it does raise is SecretLeak, because storing an
API key in a payload that later ships in an export is not a recoverable state.
"""

import json
import time
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from core.keys import resolve_openrouter_key

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
KEY_MARKER = "sk-or-"
DEFAULT_TIMEOUT = 120


class SecretLeak(RuntimeError):
    """A payload about to be stored contained something shaped like an API key."""


@dataclass(frozen=True)
class LLMResult:
    request_json: dict
    response_json: dict | None = None
    raw_response: str = ""
    model: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    latency_ms: int = 0
    http_status: int | None = None
    transport_error: str = ""

    @property
    def ok(self):
        return self.response_json is not None and not self.transport_error


class LLMClient(Protocol):
    def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        json_schema: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> LLMResult: ...


def build_messages(system_prompt, user_prompt):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages


class OpenRouterClient:
    """Talks to OpenRouter through the OpenAI-compatible SDK."""

    def __init__(self, api_key=None, base_url=OPENROUTER_BASE_URL):
        self._api_key = api_key or resolve_openrouter_key()
        self._client = OpenAI(api_key=self._api_key, base_url=base_url)
        # One seam, so tests can stand in for the network without patching the SDK.
        self._create = self._client.chat.completions.with_raw_response.create

    def complete(
        self,
        *,
        model,
        system_prompt,
        user_prompt,
        temperature=0.0,
        json_schema=None,
        timeout=DEFAULT_TIMEOUT,
    ):
        request = {
            "model": model,
            "messages": build_messages(system_prompt, user_prompt),
            "temperature": temperature,
        }
        if json_schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": json_schema},
            }
        # The key lives on the client, never in the recorded body.
        self._guard(request)

        started = time.monotonic()
        try:
            raw = self._create(timeout=timeout, **request)
        except Exception as error:  # noqa: BLE001 — a provider failure is data
            return LLMResult(
                request_json=request,
                latency_ms=self._elapsed(started),
                transport_error=f"{type(error).__name__}: {error}",
            )

        latency = self._elapsed(started)
        status = getattr(raw, "status_code", None)
        try:
            body = json.loads(raw.text)
        except (ValueError, AttributeError):
            return LLMResult(
                request_json=request,
                latency_ms=latency,
                http_status=status,
                transport_error=f"Response body was not JSON: {raw.text[:500]}",
            )
        return self._result(request, body, latency, status)

    def _result(self, request, body, latency, status):
        choice = (body.get("choices") or [{}])[0]
        usage = body.get("usage") or {}
        return LLMResult(
            request_json=request,
            response_json=body,
            raw_response=(choice.get("message") or {}).get("content") or "",
            model=body.get("model", request["model"]),
            finish_reason=choice.get("finish_reason") or "",
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
            cost=usage.get("total_cost"),
            latency_ms=latency,
            http_status=status,
        )

    def _guard(self, payload):
        """Refuse to hand back a payload carrying anything key-shaped."""
        if KEY_MARKER in json.dumps(payload):
            raise SecretLeak(
                "Refusing to record a payload containing an OpenRouter key. "
                "The key belongs on the client, never in a stored request body."
            )

    @staticmethod
    def _elapsed(started):
        return int((time.monotonic() - started) * 1000)
```

`backend/llm/fake.py`:

```python
"""An LLMClient that never touches the network, for tests and dry runs."""

from llm.client import LLMResult, build_messages


class FakeLLMClient:
    """Returns queued responses in order and records every call it received."""

    def __init__(self, responses=None):
        self._queue = list(responses or [])
        self.calls = []

    def queue(self, text, **overrides):
        """Queue one assistant message, optionally overriding projected columns."""
        self._queue.append({"text": text, **overrides})

    def fail_next(self, message, http_status=None):
        """Queue a transport failure — no response body, like a 429 or a timeout."""
        self._queue.append({"error": message, "http_status": http_status})

    def complete(
        self,
        *,
        model,
        system_prompt,
        user_prompt,
        temperature=0.0,
        json_schema=None,
        timeout=None,
    ):
        request = {
            "model": model,
            "messages": build_messages(system_prompt, user_prompt),
            "temperature": temperature,
        }
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "json_schema": json_schema,
            }
        )
        assert self._queue, (
            "FakeLLMClient has no queued response left. "
            "Queue one per expected call with .queue() or .fail_next()."
        )
        canned = self._queue.pop(0)

        if "error" in canned:
            return LLMResult(
                request_json=request,
                http_status=canned.get("http_status"),
                transport_error=canned["error"],
            )

        text = canned["text"]
        body = {
            "id": "fake-generation",
            "model": model,
            "choices": [
                {
                    "finish_reason": canned.get("finish_reason", "stop"),
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {
                "prompt_tokens": canned.get("prompt_tokens", 0),
                "completion_tokens": canned.get("completion_tokens", 0),
            },
        }
        return LLMResult(
            request_json=request,
            response_json=body,
            raw_response=text,
            model=model,
            finish_reason=canned.get("finish_reason", "stop"),
            prompt_tokens=canned.get("prompt_tokens", 0),
            completion_tokens=canned.get("completion_tokens", 0),
            cost=canned.get("cost"),
            latency_ms=0,
            http_status=200,
        )
```

- [ ] **Step 6: Run both test files**

Run: `cd backend && uv run pytest llm/tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Run the whole suite and lint**

```bash
cd backend
uv run pytest
uv run ruff format . && uv run ruff check --fix .
```

---

### Task 6: Cached OpenRouter model catalogue

**Files:**
- Create: `backend/llm/models_api.py`, `backend/llm/tests/test_models_api.py`
- Modify: `backend/config/settings.py` (add a `CACHES` setting), `backend/config/urls.py` (add the route)

**Interfaces:**
- Consumes: `core.keys.resolve_openrouter_key`, `core.keys.MissingAPIKey`.
- Produces: `llm.models_api.fetch_models(fetcher=None) -> list[dict]` (cached one hour) and `llm.models_api.AvailableModelsView`; route `GET /api/models/` returning `{"count": int, "results": [...]}`.

- [ ] **Step 1: Write the failing test**

Create `backend/llm/tests/test_models_api.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from llm.models_api import MODEL_CACHE_KEY, fetch_models

User = get_user_model()

CATALOGUE = {
    "data": [
        {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4",
         "pricing": {"prompt": "0.000003"}},
        {"id": "openai/gpt-5", "name": "GPT-5", "pricing": {"prompt": "0.00001"}},
    ]
}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def key(settings, db):
    """fetch_models resolves the key, which reads AppSettings — so these touch the DB."""
    settings.OPENROUTER_API_KEY = "sk-or-test"
    return settings.OPENROUTER_API_KEY


@pytest.mark.django_db
def test_fetch_returns_the_catalogue_rows(key):
    rows = fetch_models(fetcher=lambda key: CATALOGUE)
    assert [row["id"] for row in rows] == ["anthropic/claude-sonnet-4", "openai/gpt-5"]


@pytest.mark.django_db
def test_the_second_fetch_is_served_from_cache(key):
    calls = []

    def fetcher(api_key):
        calls.append(api_key)
        return CATALOGUE

    fetch_models(fetcher=fetcher)
    fetch_models(fetcher=fetcher)
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_fetch_failure_does_not_poison_the_cache(key):
    def broken(api_key):
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        fetch_models(fetcher=broken)
    assert cache.get(MODEL_CACHE_KEY) is None


@pytest.mark.django_db
def test_endpoint_requires_authentication(api_client):
    assert api_client.get("/api/models/").status_code == 403


@pytest.mark.django_db
def test_endpoint_returns_the_catalogue(api_client, member, settings, monkeypatch):
    settings.OPENROUTER_API_KEY = "sk-or-test"
    monkeypatch.setattr("llm.models_api._fetch_from_openrouter", lambda key: CATALOGUE)
    api_client.force_authenticate(user=member)

    body = api_client.get("/api/models/").json()
    assert body["count"] == 2
    assert body["results"][0]["id"] == "anthropic/claude-sonnet-4"


@pytest.mark.django_db
def test_endpoint_reports_a_missing_key_clearly(api_client, member, settings):
    settings.OPENROUTER_API_KEY = ""
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/models/")
    assert response.status_code == 503
    assert "No OpenRouter API key" in str(response.json())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest llm/tests/test_models_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llm.models_api'`.

- [ ] **Step 3: Write the implementation**

`backend/llm/models_api.py`:

```python
"""The OpenRouter model catalogue, cached so a dropdown does not hammer the API."""

import json

from django.core.cache import cache
from openai import OpenAI
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.keys import MissingAPIKey, resolve_openrouter_key
from llm.client import OPENROUTER_BASE_URL

MODEL_CACHE_KEY = "openrouter:models"
MODEL_CACHE_SECONDS = 3600


def _fetch_from_openrouter(api_key):
    """Read the catalogue through the SDK's raw-response accessor.

    Raw rather than parsed because OpenRouter adds fields the OpenAI model type
    does not declare — pricing and context_length among them — and those are
    exactly what a model picker needs to show.
    """
    client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL)
    return json.loads(client.models.with_raw_response.list().text)


def fetch_models(fetcher=None):
    """Return the catalogue, cached for an hour.

    A failed fetch is allowed to propagate and is never cached — a transient
    outage must not blank the model list for the next hour.
    """
    cached = cache.get(MODEL_CACHE_KEY)
    if cached is not None:
        return cached

    payload = (fetcher or _fetch_from_openrouter)(resolve_openrouter_key())
    rows = payload.get("data", [])
    cache.set(MODEL_CACHE_KEY, rows, MODEL_CACHE_SECONDS)
    return rows


class AvailableModelsView(APIView):
    def get(self, request):
        try:
            rows = fetch_models()
        except MissingAPIKey as error:
            return Response(
                {"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response({"count": len(rows), "results": rows})
```

This adds NO dependency: it reuses the `openai` SDK already pinned for the
client. Do not add anything to `pyproject.toml`.

Add to `backend/config/settings.py`, after `REST_FRAMEWORK`:

```python
# Local-memory cache is enough here: the only cached value is the OpenRouter
# model catalogue, and a per-process copy costs one extra fetch per worker.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "hcv-pipeline",
    }
}
```

Add to `backend/config/urls.py`:

```python
from llm.models_api import AvailableModelsView
```

and to `urlpatterns`:

```python
    path("api/models/", AvailableModelsView.as_view(), name="available-models"),
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest llm/tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite and lint**

```bash
cd backend
uv run pytest
uv run python manage.py makemigrations --check --dry-run
uv run ruff format . && uv run ruff check .
ls backend/media 2>&1
```

Expected: everything green; only the `prompts` migration exists and nothing is
missing; ruff clean; no `media` directory.

---

## Done when

- `cd backend && uv run pytest` is green, and `uv run ruff format --check . && uv run ruff check .` is clean.
- A staff user can create a prompt, `PATCH` it and receive **version 2 while version 1 is untouched**.
- `POST /api/prompts/{id}/preview/` returns the literal strings a run would send.
- **Variant isolation holds:** building a Run A context with a taxonomy raises `TaxonomyLeak`; a Run A render exposes neither `taxonomy` nor `hcv_table()`; and no HCV id or name appears in a Run A rendered prompt.
- `FakeLLMClient` drives the client interface end to end with no network and no spend.
- No stored `request_json` contains an OpenRouter key.

Plan 3 consumes: `prompts.rendering.render/build_phase2_context/build_phase3_context`,
`llm.client.LLMClient/LLMResult/OpenRouterClient`, `llm.fake.FakeLLMClient`, and
`llm.schemas.parse_phase2/parse_phase3/split_known_labels/json_schema`. It must
also widen `corpus.Scenario`'s unique constraint to
`(system_description, generation_run, scenario_id)` when it adds that FK, or
Phase 2's second task collides on `sc_01`.
