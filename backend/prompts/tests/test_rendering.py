import pytest

from corpus.models import FewShotExample, Origin, ResourceFile, Scenario, SystemDescription
from prompts.models import PromptTemplate
from prompts.rendering import (
    PromptRenderError,
    RenderedPrompt,
    TaxonomyLeak,
    assert_no_taxonomy_leak,
    assert_request_not_leaking_taxonomy,
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
@pytest.mark.parametrize(
    ("user_prompt", "error_type"),
    [
        ("{{ 1/0 }}", "ZeroDivisionError"),
        ('{{ "abc".index("z") }}', "ValueError"),
        ("{{ [].pop() }}", "IndexError"),
        ("{{ range(1000000)|list|length }}", "OverflowError"),
    ],
)
@pytest.mark.parametrize("variant", ["A", "B"])
def test_anything_a_template_raises_while_rendering_is_a_prompt_render_error(
    system, taxonomy, user_prompt, error_type, variant
):
    """Task 10.6 follow-up. Every caller — launch's dry run, `run_phase2`,
    `label_scenario`, preview — catches `(TaxonomyLeak, PromptRenderError)`,
    and these four used to escape `render()` as themselves, past all of
    them. Each is now a `PromptRenderError` naming its type, under either
    variant. A taxonomy-kind `resource()` still raises `TaxonomyLeak` as
    itself — `test_isolation.py` pins that distinction."""
    context = build_phase2_context(
        variant=variant,
        seed_systems=[system],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=3,
        n_scenarios_per_system=5,
        taxonomy=taxonomy if variant == "B" else None,
        guard_taxonomy=taxonomy if variant == "A" else None,
    )

    with pytest.raises(PromptRenderError, match=error_type):
        render(make(user_prompt), context)


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
    """The fail-closed behaviour this module's docstring documents:
    `taxonomy` is absent from the Run A context (by design — that IS the
    isolation guarantee), not merely `None`, so referencing it at all under
    StrictUndefined raises. `{% if taxonomy %}` looks safe but fails Run A
    while the identical template renders Run B fine — the same shape as a
    stray `{{ taxonomy }}` a variant-A template author might leave behind
    by copying variant B's template and forgetting to strip it, now that
    each variant is authored independently (`phase2_prompt_a`/
    `phase2_prompt_b`). This is deliberate: a mistake like that must fail
    loudly, not render a prompt that quietly looks unbiased."""
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
def test_if_taxonomy_is_defined_still_checks_presence_without_touching_the_value(system, taxonomy):
    """Phase 2 no longer documents `{% if taxonomy is defined %}` as ITS
    idiom — each variant gets its own, fully independent template now (see
    this module's own docstring), so a real phase-2 template never needs to
    branch on whether `taxonomy` was supplied: variant A's template simply
    never references it, and variant B's always can. This test is no longer
    pinning "the recommended pattern" — it pins the underlying JINJA
    mechanism `render()` itself still relies on and still needs to be
    correct: `is defined` checks presence without touching the (absent,
    under Run A) value, so a template that legitimately wants to detect
    whether a variable was supplied — for ANY variable, not just
    `taxonomy` — still gets the right answer under `StrictUndefined`
    rather than an `UndefinedError`. The SAME template rendered against
    both an A and a B context here is a deliberate stress test of that
    mechanism, not a claim that one template ought to serve both variants
    in practice any more."""
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


# --- assert_request_not_leaking_taxonomy: the FINAL choke point's own shared
# predicate. It is a thin wrapper around assert_no_taxonomy_leak (above), not a
# second, divergent notion of "taxonomy content" — see pipeline/tests
# /test_choke_point.py for the integration-level proof this exists to provide:
# a leak render()'s own scan cannot see (because it is added to the request
# AFTER rendering) is still caught here, because this scans the actual request
# dict, not the two prompt strings. ----------------------------------------


@pytest.mark.django_db
def test_request_scan_rejects_an_unrecognised_variant(taxonomy):
    """Fail closed: if the caller cannot even say A or B, refuse rather than
    guess which side of the isolation guarantee applies to this request."""
    with pytest.raises(TaxonomyLeak, match="variant A or B"):
        assert_request_not_leaking_taxonomy(
            variant="C", guard_taxonomy=taxonomy, request={"model": "m"}
        )


@pytest.mark.django_db
def test_request_scan_rejects_variant_a_with_no_taxonomy_to_check_against():
    """Fail closed: `guard_taxonomy=None` means the scan cannot run at all,
    and this is the LAST checkpoint before a real provider call — refused
    outright, not merely warned about the way render()'s own backstop would."""
    with pytest.raises(TaxonomyLeak, match="no taxonomy to check"):
        assert_request_not_leaking_taxonomy(
            variant="A", guard_taxonomy=None, request={"model": "m"}
        )


@pytest.mark.django_db
def test_request_scan_rejects_variant_a_against_an_empty_taxonomy(db):
    """Fail closed, the second way assert_no_taxonomy_leak cannot run: a
    taxonomy with zero HCV rows. Contrast with test_isolation.py's R2 case
    (test_an_empty_guard_taxonomy_warns_and_still_renders) — render()'s own
    backstop only warns and lets the render through there; this, being the
    LAST checkpoint before the provider is contacted, refuses outright."""
    empty = Taxonomy.objects.create(vocabulary_id="HCV-EMPTY-REQ", version="1.0")
    with pytest.raises(TaxonomyLeak, match="has no rows"):
        assert_request_not_leaking_taxonomy(
            variant="A", guard_taxonomy=empty, request={"model": "m"}
        )


@pytest.mark.django_db
def test_request_scan_names_the_any_blank_row_rule_when_refusing(db):
    """Task 10.6: the rule refuses on ANY content-free row, not only on a
    taxonomy blank throughout — `assert_no_taxonomy_leak` will not certify a
    scan that skips even one row — and the refusal must say so. This
    taxonomy has one perfectly good row beside one blank one. The old
    message told the operator it had no usable rows at all, which is false
    for exactly this taxonomy and sends them looking for the wrong fault."""
    partly_blank = Taxonomy.objects.create(vocabulary_id="HCV-PARTLY-BLANK", version="1.0")
    HCV.objects.create(taxonomy=partly_blank, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    HCV.objects.create(taxonomy=partly_blank, hcv_id="", name="", order=8)

    with pytest.raises(TaxonomyLeak, match="a row with no id, name or definition"):
        assert_request_not_leaking_taxonomy(
            variant="A", guard_taxonomy=partly_blank, request={"model": "m"}
        )


@pytest.mark.django_db
def test_request_scan_skips_variant_b_entirely(taxonomy):
    """Variant B legitimately carries the taxonomy by design — scanning it
    would just raise on material that belongs there — so it is not scanned
    at all, and the function reports that plainly (False, not an exception)."""
    request = {
        "model": "m",
        "messages": [{"role": "user", "content": format_hcv_table(taxonomy)}],
    }
    assert (
        assert_request_not_leaking_taxonomy(variant="B", guard_taxonomy=taxonomy, request=request)
        is False
    )


@pytest.mark.django_db
def test_request_scan_passes_a_clean_variant_a_request(taxonomy):
    request = {"model": "m", "messages": [{"role": "user", "content": "Generate scenarios."}]}
    assert (
        assert_request_not_leaking_taxonomy(variant="A", guard_taxonomy=taxonomy, request=request)
        is True
    )


@pytest.mark.django_db
def test_request_scan_catches_a_leak_in_a_field_that_is_not_a_prompt(taxonomy):
    """The whole reason this function exists: it scans the ENTIRE request
    dict, not just message content, so a taxonomy identifier hiding in
    response_format — or anything else a future field might carry — is
    caught exactly the same way as one hiding in a prompt string."""
    request = {
        "model": "m",
        "messages": [{"role": "user", "content": "Generate scenarios."}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": {"enum": ["HCV-07"]}},
        },
    }
    with pytest.raises(TaxonomyLeak, match="HCV-07"):
        assert_request_not_leaking_taxonomy(variant="A", guard_taxonomy=taxonomy, request=request)


@pytest.mark.django_db
def test_request_scan_refuses_a_request_it_cannot_walk(taxonomy):
    """A request dict cannot be scanned at all if it cannot even be WALKED —
    concretely, a circular reference (a dict that contains itself), which
    `_iter_request_text` refuses with `ValueError` rather than recursing
    until the stack overflows. Refused for the same fail-closed reason as an
    unresolvable taxonomy: if it cannot be verified taxonomy-free, it cannot
    be sent. This is deliberately narrower than "cannot be JSON-serialised" —
    an ordinary exotic value (not a str/dict/list/tuple/number/bool/None) is
    coerced with `str()` and scanned, not refused; see
    test_request_scan_scans_the_text_of_an_exotic_value below for that case."""
    request = {"model": "m"}
    request["self"] = request
    with pytest.raises(TaxonomyLeak, match="could not be checked"):
        assert_request_not_leaking_taxonomy(variant="A", guard_taxonomy=taxonomy, request=request)


@pytest.mark.django_db
def test_request_scan_scans_the_text_of_an_exotic_value_instead_of_refusing(taxonomy):
    """The other half of the docstring's precise claim: a value that is not
    JSON-native (not a str/dict/list/tuple/number/bool/None) is coerced with
    `str()` and scanned like anything else — not silently skipped, and not
    refused outright the way an unwalkable structure is."""

    class Exotic:
        def __str__(self):
            return "leaked HCV-07 right here"

    request = {"model": "m", "odd_field": Exotic()}
    with pytest.raises(TaxonomyLeak, match="HCV-07"):
        assert_request_not_leaking_taxonomy(variant="A", guard_taxonomy=taxonomy, request=request)


@pytest.fixture
def taxonomy_with_escapable_names(db):
    """Four HCVs whose NAMES each embed a character JSON escaping treats
    specially: a double quote, a backslash, a real newline, a real tab. This
    is exactly the shape that let the request scan disagree with the
    render-time scan when it used to serialise the whole request with
    `json.dumps` before scanning: `json.dumps` turns a literal `"` into the
    two characters `\\"`, a literal newline into the two characters `\\n`,
    and so on, so a plain substring match against the RAW name fails against
    the escaped blob even though the raw text is right there in the request.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-ESCAPE", version="1.0", is_active=True)
    names = [
        'Say "Hello" Attack',
        "Path\\Traversal Concept",
        "Multi\nLine Concept",
        "Tab\tSeparated Concept",
    ]
    for index, name in enumerate(names, start=1):
        HCV.objects.create(taxonomy=tax, hcv_id=f"ESC-{index}", name=name, order=index)
    return tax


@pytest.mark.django_db
def test_request_scan_catches_names_containing_json_escapable_characters(
    taxonomy_with_escapable_names,
):
    """The disagreement this predicate must never have again: a taxonomy
    name containing a quote, backslash, newline or tab must be found by the
    request scan exactly as reliably as by the render-time scan. Both scans
    are proven here, side by side, against the SAME raw text — if either one
    regresses to comparing against a `json.dumps`'d blob, it alone stops
    matching and this test tells them apart."""
    names = [row.name for row in taxonomy_with_escapable_names.hcvs.all()]
    leaked_text = " and also: ".join(names)

    # The render-time scan: raw system/user strings, no request dict at all.
    rendered = RenderedPrompt(system="", user=f"Generate scenarios. {leaked_text}")
    with pytest.raises(TaxonomyLeak, match="names or definitions"):
        assert_no_taxonomy_leak(taxonomy_with_escapable_names, rendered)

    # The request scan: the identical text, but reached the way it actually
    # would in production — inside a nested request dict, not passed as a
    # bare string.
    request = {
        "model": "m",
        "messages": [{"role": "user", "content": f"Generate scenarios. {leaked_text}"}],
    }
    with pytest.raises(TaxonomyLeak, match="names or definitions"):
        assert_request_not_leaking_taxonomy(
            variant="A", guard_taxonomy=taxonomy_with_escapable_names, request=request
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
