import pytest

from corpus.models import Origin, Scenario, SystemDescription
from prompts.checks import provided_names, syntax_error, unknown_variables
from prompts.rendering import _environment, build_phase2_context, build_phase3_context
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def system(db):
    return SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)


def test_valid_templates_have_no_syntax_error():
    assert syntax_error("", "{% if n_systems %}{{ n_systems }}{% endif %}") is None


def test_a_syntax_error_names_the_prompt_and_line():
    message = syntax_error("", "Line one.\n{% if n_systems %}\nNo end.")
    assert message.startswith("User prompt, line 2:")


def test_a_system_prompt_syntax_error_is_reported_too():
    assert syntax_error("{{ unclosed", "fine").startswith("System prompt, line 1:")


def test_a_misspelled_top_level_name_is_unknown():
    names = unknown_variables(3, "", "", "{{ scenrio.actor }} {{ scenario.actor }}")
    assert names == ["scenrio"]


def test_names_the_template_defines_or_jinja_provides_are_not_unknown():
    user = (
        "{% set label = 'x' %}{{ label }}"
        "{% for row in taxonomy %}{{ loop.index }} {{ row }}{% endfor %}"
        "{{ range(2) | list }} {{ hcv_table() }} {{ resource('vocab') }}"
    )
    assert unknown_variables(3, "", "", user) == []


def test_taxonomy_is_unknown_to_variant_a_but_not_variant_b():
    user = "{{ taxonomy }} {{ hcv_table() }}"
    assert unknown_variables(2, "A", "", user) == ["hcv_table", "taxonomy"]
    assert unknown_variables(2, "B", "", user) == []


def test_unknown_variables_is_empty_when_the_template_does_not_parse():
    assert unknown_variables(3, "", "", "{{ nope") == []


def _names(context):
    public = {key for key in context if not key.startswith("_")}
    return public | set(_environment(context).globals)


@pytest.mark.django_db
@pytest.mark.parametrize("variant", ["A", "B"])
def test_phase_2_names_match_what_rendering_provides(variant, taxonomy):
    context = build_phase2_context(
        variant=variant,
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        taxonomy=taxonomy if variant == "B" else None,
        guard_taxonomy=taxonomy,
    )
    assert provided_names(2, variant) == _names(context)


@pytest.mark.django_db
def test_phase_3_names_match_what_rendering_provides(system, taxonomy):
    scenario = Scenario.objects.create(system_description=system, scenario_id="S1")
    context = build_phase3_context(
        scenario=scenario, system_description=system, taxonomy=taxonomy, fewshots=[]
    )
    assert provided_names(3, "") == _names(context)
