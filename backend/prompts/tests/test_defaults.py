import pytest
from django.core.management import call_command

from corpus.models import FewShotExample, Scenario, SystemDescription
from prompts.models import PromptTemplate
from prompts.rendering import build_phase2_context, build_phase3_context, render
from taxonomy.models import Taxonomy


@pytest.fixture
def seeded(db):
    call_command("import_taxonomy", "--activate")
    call_command("seed_corpus")
    call_command("seed_prompts")
    return Taxonomy.active(), SystemDescription.objects.get(slug="oldsmar")


def _template(name):
    return PromptTemplate.objects.get(name=name, version=1)


@pytest.mark.django_db
@pytest.mark.parametrize("generate_systems", [True, False])
def test_default_generation_templates_render_for_their_variant(seeded, generate_systems):
    taxonomy, system = seeded
    common = {
        "seed_systems": [system],
        "fewshots": FewShotExample.objects.filter(phase=2),
        "generate_systems": generate_systems,
        "generate_scenarios": True,
        "n_systems": 1,
        "n_scenarios_per_system": 2,
        "target_system": None if generate_systems else system,
    }
    a = render(
        _template("default-generation-variant-a"),
        build_phase2_context(variant="A", guard_taxonomy=taxonomy, **common),
    )
    b = render(
        _template("default-generation-variant-b"),
        build_phase2_context(variant="B", taxonomy=taxonomy, **common),
    )
    assert "HCV-" not in a.user and "HCV-01" in b.user
    assert "Write 2 scenarios" in a.user or "write 2 scenarios" in a.user


@pytest.mark.django_db
def test_default_labelling_template_renders_with_the_seeded_example(seeded):
    taxonomy, system = seeded
    scenario = Scenario.objects.get(system_description=system, scenario_id="sc_02")
    rendered = render(
        _template("default-labelling"),
        build_phase3_context(
            scenario=scenario,
            system_description=system,
            taxonomy=taxonomy,
            fewshots=FewShotExample.objects.filter(phase=3),
        ),
    )
    assert "HCV-21" in rendered.user and "Scenario sc_02" in rendered.user
    assert "oldsmar-sc_01" in rendered.user


@pytest.mark.django_db
def test_seed_prompts_is_idempotent_and_activates_v1(seeded):
    call_command("seed_prompts")
    assert PromptTemplate.objects.count() == 3
    assert PromptTemplate.objects.filter(is_active=True).count() == 3
