"""The preset must send the research's prompts, byte for byte."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.core.management import call_command

from analyses import preset
from corpus.models import ResourceFile, Scenario
from prompts import rendering
from prompts.rendering import PromptRenderError, TaxonomyLeak, render
from taxonomy.models import Taxonomy

FIXTURE = json.loads((Path(__file__).parent / "fixtures/parity.json").read_text(encoding="utf-8"))


def digest(rendered):
    return {
        "system": hashlib.sha256(rendered.system.encode()).hexdigest(),
        "user": hashlib.sha256(rendered.user.encode()).hexdigest(),
    }


def template_of(step):
    return SimpleNamespace(
        pk=None,
        variant="",
        system_prompt=step.system_prompt,
        user_prompt=step.user_prompt,
        output_schema=step.output_schema,
    )


def examples_of(step):
    return [
        SimpleNamespace(
            name=example["name"],
            resolved_input=example["input"],
            resolved_output=example["output"],
            input_file=None,
            output_file=None,
        )
        for example in step.examples
    ]


@pytest.fixture
def taxonomy(db):
    call_command("import_taxonomy", "--activate")
    return Taxonomy.active()


def generation_context(taxonomy, variant="B"):
    return rendering.build_phase2_context(
        variant=variant,
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=FIXTURE["generation"]["n_systems"],
        n_scenarios_per_system=preset.load().generation.count,
        taxonomy=taxonomy if variant == "B" else None,
        guard_taxonomy=taxonomy if variant == "A" else None,
    )


def test_preset_is_the_recommended_setup():
    loaded = preset.load()
    generation, labelling = loaded.generation, loaded.labelling

    assert loaded.version == "1"
    assert (generation.model, generation.temperature) == ("anthropic/claude-sonnet-5", 0.8)
    assert (generation.prompt_name, generation.prompt_version) == ("Zero-shot Variant B", 1)
    assert (generation.examples, generation.count) == ((), 8)
    assert (labelling.model, labelling.temperature) == ("openai/gpt-5.6-luna-pro", 0.0)
    assert (labelling.prompt_name, labelling.prompt_version) == ("default-labelling", 1)
    assert labelling.count is None


def test_preset_has_five_examples_in_export_order():
    assert [example["name"] for example in preset.load().labelling.examples] == [
        "NoiseApp sc_02",
        "NoiseApp sc_03",
        "NoiseApp sc_04",
        "NoiseApp scenarios",
        "oldsmar-sc_01",
    ]


@pytest.mark.django_db
def test_generation_prompt_matches_the_research(taxonomy):
    step = preset.load().generation

    rendered = render(
        template_of(step),
        generation_context(taxonomy),
        resources=preset.resolver(step.resources),
    )

    assert digest(rendered) == FIXTURE["generation"]["expected"]


def labelling_prompt(case, taxonomy):
    step = preset.load().labelling
    card = case["scenario"]
    scenario = SimpleNamespace(
        scenario_id=card["scenario_id"],
        narrative=card["narrative"],
        card={field: card[field] for field in Scenario.CARD_FIELDS},
    )
    context = rendering.build_phase3_context(
        scenario=scenario,
        system_description=SimpleNamespace(slug="", **case["system"]),
        taxonomy=taxonomy,
        fewshots=examples_of(step),
    )
    return render(template_of(step), context, resources=preset.resolver(step.resources))


@pytest.mark.django_db
@pytest.mark.parametrize("case", FIXTURE["labelling"], ids=lambda case: case["export"])
def test_labelling_prompts_match_the_research(taxonomy, case):
    assert digest(labelling_prompt(case, taxonomy)) == case["expected"]


@pytest.mark.django_db
def test_labelling_prompt_is_unchanged_by_v12(taxonomy):
    path = settings.RESOURCES_DIR / "taxonomy/hcv21_v1.2.json"
    call_command("import_taxonomy", str(path))
    case = FIXTURE["labelling"][0]

    rendered = labelling_prompt(case, Taxonomy.objects.get(version="1.2"))

    assert digest(rendered) == case["expected"]


@pytest.mark.django_db
def test_resources_resolver_never_reads_the_database(taxonomy):
    ResourceFile.objects.create(name="hcv-vocabulary-basics", text="EDITED ON THE RESEARCH PAGE")
    step = preset.load().generation

    rendered = render(
        template_of(step),
        generation_context(taxonomy),
        resources=preset.resolver(step.resources),
    )

    assert "EDITED ON THE RESEARCH PAGE" not in rendered.user


def test_resolver_names_a_missing_resource():
    with pytest.raises(PromptRenderError, match="nope"):
        preset.resolver({})("nope")


@pytest.mark.django_db
def test_resolver_is_refused_under_variant_a(taxonomy):
    template = SimpleNamespace(pk=None, system_prompt="s", user_prompt="u", output_schema={})

    with pytest.raises(TaxonomyLeak):
        render(template, generation_context(taxonomy, "A"), resources=preset.resolver({}))
