import pytest

from corpus.models import FewShotExample, Origin, ResourceFile, Scenario, SystemDescription
from prompts.models import PromptTemplate
from prompts.rendering import (
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    build_phase3_context,
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
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-09", name="Propaganda", definition="d", order=9)
    return tax


@pytest.fixture
def system(db):
    return SystemDescription.objects.create(
        title="Oldsmar", body="A water plant.", origin=Origin.SEED
    )


def make(user_prompt, system_prompt=""):
    return PromptTemplate(
        phase=2, name="t", version=1, user_prompt=user_prompt, system_prompt=system_prompt
    )


@pytest.mark.django_db
def test_renders_both_prompts(system):
    context = build_phase2_context(
        variant="A",
        seed_systems=[system],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=3,
        n_scenarios_per_system=5,
    )
    rendered = render(make("Make {{ n_systems }} systems.", "You are careful."), context)

    assert rendered.system == "You are careful."
    assert rendered.user == "Make 3 systems."


@pytest.mark.django_db
def test_seed_systems_expose_title_and_body(system):
    context = build_phase2_context(
        variant="A",
        seed_systems=[system],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    template = make("{% for s in seed_systems %}{{ s.title }}::{{ s.body }}{% endfor %}")
    assert render(template, context).user == "Oldsmar::A water plant."


@pytest.mark.django_db
def test_fewshots_expose_resolved_input_and_output(system):
    example = FewShotExample.objects.create(phase=2, name="one", input_text="IN", output_text="OUT")
    context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[example],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    template = make("{% for f in fewshots %}{{ f.input }}|{{ f.output }}{% endfor %}")
    assert render(template, context).user == "IN|OUT"


@pytest.mark.django_db
def test_resource_global_reads_a_resource_file_by_name(system):
    ResourceFile.objects.create(name="vocab", text="the vocabulary")
    context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    assert render(make('{{ resource("vocab") }}'), context).user == "the vocabulary"


@pytest.mark.django_db
def test_resource_global_names_the_missing_resource(system):
    context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    with pytest.raises(PromptRenderError, match="absent"):
        render(make('{{ resource("absent") }}'), context)


@pytest.mark.django_db
def test_an_undefined_variable_fails_loudly(system):
    """A typo must not silently render an empty prompt."""
    context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    with pytest.raises(PromptRenderError, match="n_systmes"):
        render(make("{{ n_systmes }}"), context)


@pytest.mark.django_db
def test_the_naive_taxonomy_guard_fails_variant_a_but_not_b(system, taxonomy):
    """The trap this module's docstring documents: `taxonomy` is absent from
    the Run A context (by design — that IS the isolation guarantee), not
    merely `None`, so referencing it at all under StrictUndefined raises.
    `{% if taxonomy %}` looks safe but is the wrong idiom — it fails Run A
    while the identical template renders Run B fine, which is exactly the
    confusing "one variant works, its sibling doesn't" symptom this trips
    every prompt author into eventually."""
    template = make("{% if taxonomy %}guided{% else %}unbiased{% endif %}")

    b_context = build_phase2_context(
        variant="B",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        taxonomy=taxonomy,
    )
    assert render(template, b_context).user == "guided"

    a_context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        guard_taxonomy=taxonomy,
    )
    with pytest.raises(PromptRenderError, match="taxonomy"):
        render(template, a_context)


@pytest.mark.django_db
def test_if_taxonomy_is_defined_renders_correctly_for_both_variants(system, taxonomy):
    """The documented fix for the trap above: `is defined` checks presence
    without touching the (absent, under Run A) value, so it is the one
    spelling of this guard that renders under both variants — empty for A,
    populated for B — making the idiom a checked guarantee rather than
    advice a template author has to remember."""
    template = make(
        "{% if taxonomy is defined %}{{ taxonomy|length }} rows{% else %}withheld{% endif %}"
    )

    a_context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        guard_taxonomy=taxonomy,
    )
    assert render(template, a_context).user == "withheld"

    b_context = build_phase2_context(
        variant="B",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        taxonomy=taxonomy,
    )
    assert render(template, b_context).user == "2 rows"


@pytest.mark.django_db
def test_the_sandbox_blocks_attribute_escapes(system):
    context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
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
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=False,
        generate_scenarios=True,
        n_systems=0,
        n_scenarios_per_system=4,
        target_system=system,
    )
    assert render(make("{{ target_system.title }}"), context).user == "Oldsmar"


@pytest.mark.django_db
def test_an_include_without_a_loader_fails_as_a_prompt_render_error(system):
    """This environment has no template loader; `{% include %}` raises a raw
    TypeError from Jinja rather than a TemplateError. It must still surface as
    PromptRenderError, not escape render() as an unhandled TypeError."""
    context = build_phase2_context(
        variant="A",
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
    )
    with pytest.raises(PromptRenderError):
        render(make('{% include "nope.txt" %}'), context)


@pytest.mark.django_db
def test_build_phase3_context_requires_a_taxonomy(system):
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="S1", narrative="A narrative."
    )
    with pytest.raises(TaxonomyLeak, match="Phase 3"):
        build_phase3_context(
            scenario=scenario, system_description=system, taxonomy=None, fewshots=[]
        )


@pytest.mark.django_db
def test_build_phase3_context_spreads_the_card_and_renders(system, taxonomy):
    scenario = Scenario.objects.create(
        system_description=system,
        scenario_id="S1",
        actor="Operator",
        trigger="A phishing email.",
        standing_state="Normal ops.",
        asset="SCADA HMI",
        threat="Remote command injection.",
        outcome="Chemical dosing altered.",
        narrative="A narrative.",
    )
    context = build_phase3_context(
        scenario=scenario, system_description=system, taxonomy=taxonomy, fewshots=[]
    )
    template = make(
        "{{ scenario.scenario_id }}/{{ scenario.actor }}/{{ scenario.threat }}/"
        "{{ scenario.narrative }}/{{ system_description.title }}"
    )
    assert (
        render(template, context).user
        == "S1/Operator/Remote command injection./A narrative./Oldsmar"
    )


@pytest.mark.django_db
def test_a_phase3_context_renders_without_a_variant_marker(system, taxonomy):
    """I5: the unmarked-context refusal is scoped to phase 2. Phase 3 labelling
    has no variant by design — it always sees the taxonomy — so it must keep
    rendering, and must report no Run A scan because there is none to run."""
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="S1", narrative="A narrative."
    )
    context = build_phase3_context(
        scenario=scenario, system_description=system, taxonomy=taxonomy, fewshots=[]
    )
    rendered = render(make("{{ scenario.scenario_id }}"), context)

    assert rendered.user == "S1"
    assert rendered.leak_scan_ran is False
    assert rendered.guarded_by is None
