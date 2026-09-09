import pytest

from corpus.models import FewShotExample, ResourceFile, ResourceKind
from prompts.models import PromptTemplate
from prompts.rendering import (
    PromptRenderError,
    TaxonomyLeak,
    build_phase2_context,
    format_hcv_table,
    render,
)
from taxonomy.models import HCV, Taxonomy


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    for index, (code, label) in enumerate(
        [
            ("HCV-06", "Susceptibility to Deceptive Communication"),
            ("HCV-07", "Malicious Insiders"),
        ],
        start=6,
    ):
        HCV.objects.create(taxonomy=tax, hcv_id=code, name=label, order=index)
    return tax


def make(user_prompt, output_schema=None):
    return PromptTemplate(
        phase=2,
        name="t",
        version=1,
        user_prompt=user_prompt,
        output_schema=output_schema if output_schema is not None else {},
    )


def base(variant, taxonomy=None, guard_taxonomy=None):
    return build_phase2_context(
        variant=variant,
        seed_systems=[],
        fewshots=[],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        taxonomy=taxonomy,
        guard_taxonomy=guard_taxonomy,
    )


@pytest.fixture
def taxonomy_with_definitions(db):
    """Three HCVs with distinct, non-empty definitions and no reused wording —
    used to test the definitions-only leak route independent of ids and names.
    Active, so the two tests using it can rely on the plain Taxonomy.active()
    fallback rather than passing guard_taxonomy= — that keeps them isolated to
    the G2 (definitions-not-indexed) bug rather than also exercising G3's
    explicit-override behaviour, which has its own dedicated test below."""
    tax = Taxonomy.objects.create(vocabulary_id="HCV-DEF", version="1.0", is_active=True)
    definitions = [
        "Attackers exploit trust in seemingly legitimate communications to deceive targets.",
        "Authorized insiders misuse their access to compromise systems from within.",
        "Adversaries physically follow authorized personnel through secured entry points.",
    ]
    for index, text in enumerate(definitions, start=1):
        HCV.objects.create(
            taxonomy=tax, hcv_id=f"D-{index}", name=f"Concept {index}", definition=text, order=index
        )
    return tax


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
    """Belt and braces: a template that ACTIVELY TRIES to leak the taxonomy — via a
    taxonomy-kind ResourceFile pulled through resource() — must still be refused
    under variant A. This is the reviewer's C1 attack, turned into a regression test.
    """
    ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    with pytest.raises(TaxonomyLeak, match="taxonomy resource"):
        render(make('{{ resource("hcv_taxonomy") }}'), base("A"))


@pytest.mark.django_db
def test_no_hcv_survives_via_a_fewshot_file_pointing_at_a_taxonomy_resource(taxonomy):
    """The reviewer's C2 attack: a phase-2 few-shot example whose input file is a
    taxonomy-kind resource must not leak it into a variant A render either.
    """
    resource = ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    example = FewShotExample.objects.create(
        phase=2, name="leaky", input_file=resource, output_text="ok"
    )
    template = make("{% for f in fewshots %}{{ f.input }}{% endfor %}")

    # The refusal happens at context-build time, before a prompt exists at all —
    # so the build is what must be asserted on. (A `render()` call inside this
    # block would be unreachable, proving nothing.)
    with pytest.raises(TaxonomyLeak, match="leaky"):
        build_phase2_context(
            variant="A",
            seed_systems=[],
            fewshots=[example],
            generate_systems=True,
            generate_scenarios=True,
            n_systems=1,
            n_scenarios_per_system=1,
        )

    # And the same few-shot under variant B renders the taxonomy straight into
    # the prompt — which is what makes the refusal above load-bearing rather
    # than incidental: this content really would have reached the model.
    guided = build_phase2_context(
        variant="B",
        seed_systems=[],
        fewshots=[example],
        generate_systems=True,
        generate_scenarios=True,
        n_systems=1,
        n_scenarios_per_system=1,
        taxonomy=taxonomy,
    )
    assert "HCV-07" in render(template, guided).user


@pytest.mark.django_db
def test_backstop_catches_taxonomy_content_even_from_an_untagged_resource(taxonomy):
    """Defence in depth: even a resource NOT tagged kind=TAXONOMY (so the explicit
    resource-kind guard does not fire) is still caught, because render() scans the
    finished string against the currently active taxonomy before returning.
    """
    ResourceFile.objects.create(name="not_tagged_as_taxonomy", text=format_hcv_table(taxonomy))
    with pytest.raises(TaxonomyLeak):
        render(make('{{ resource("not_tagged_as_taxonomy") }}'), base("A"))


@pytest.mark.django_db
def test_variant_b_context_carries_the_rows(taxonomy):
    context = base("B", taxonomy=taxonomy)
    assert [row["hcv_id"] for row in context["taxonomy"]] == ["HCV-06", "HCV-07"]


@pytest.mark.django_db
def test_an_unknown_variant_is_rejected():
    with pytest.raises(TaxonomyLeak, match="Unknown variant"):
        base("C")


@pytest.mark.django_db
def test_a_non_taxonomy_value_is_rejected_instead_of_crashing():
    """taxonomy=0 is falsy but not None — must not slip past an `is None` check and
    crash later with AttributeError when something calls `.hcvs` on it."""
    with pytest.raises(TaxonomyLeak):
        base("B", taxonomy=0)


@pytest.mark.django_db
def test_taxonomy_object_is_not_exposed_as_a_template_variable(taxonomy):
    """The underscore-prefix convention: `_taxonomy_object` carries the live ORM
    object for building `hcv_table`, but must never be an addressable template
    variable, even under variant B where a taxonomy is legitimately present."""
    with pytest.raises(PromptRenderError, match="_taxonomy_object"):
        render(make("{{ _taxonomy_object }}"), base("B", taxonomy=taxonomy))


@pytest.mark.django_db
def test_a_prior_variant_b_render_does_not_leave_hcv_table_callable_for_variant_a(taxonomy):
    """Ordering guard: rendering B (which binds hcv_table) in the same process must
    not leave it callable for a variant A render that follows."""
    render(make("{{ hcv_table() }}"), base("B", taxonomy=taxonomy))

    with pytest.raises(PromptRenderError, match="hcv_table"):
        render(make("{{ hcv_table() }}"), base("A"))


# --- Round 2: the backstop's own blind spots (G1 case-sensitivity, G2 definitions,
# G3 silent no-op with no active taxonomy) ---------------------------------------


@pytest.mark.django_db
def test_backstop_catches_taxonomy_content_through_a_lower_filter(taxonomy):
    """G1: `{{ resource("notes")|lower }}` must not slip taxonomy content past the
    scan just because a template filter changed its case. "notes" is deliberately
    NOT kind=TAXONOMY, so only the case-insensitive output scan can catch this."""
    ResourceFile.objects.create(name="notes", text=format_hcv_table(taxonomy))
    with pytest.raises(TaxonomyLeak):
        render(make('{{ resource("notes")|lower }}'), base("A"))


@pytest.mark.django_db
def test_backstop_catches_taxonomy_content_through_an_upper_filter(taxonomy):
    """G1, other direction: proves the fix is case-FOLDING, not just lowercasing."""
    ResourceFile.objects.create(name="notes", text=format_hcv_table(taxonomy))
    with pytest.raises(TaxonomyLeak):
        render(make('{{ resource("notes")|upper }}'), base("A"))


@pytest.mark.django_db
def test_backstop_catches_definitions_only_content(taxonomy_with_definitions):
    """G2: a resource holding ONLY the definitions of every row — no ids, no
    names — must still be caught. Definitions are the most information-rich
    column in the taxonomy; leaking them is at least as bad as leaking ids."""
    all_definitions = "\n".join(row.definition for row in taxonomy_with_definitions.hcvs.all())
    ResourceFile.objects.create(name="defs", text=all_definitions)

    with pytest.raises(TaxonomyLeak):
        render(make('{{ resource("defs") }}'), base("A"))


@pytest.mark.django_db
def test_two_distinct_definitions_render_but_three_raise(taxonomy_with_definitions):
    """G2 boundary: the combined name/definition threshold is 3. Exactly two
    distinct definitions is still incidental; exactly three is a dumped list."""
    rows = list(taxonomy_with_definitions.hcvs.all())
    two_definitions = "\n".join(row.definition for row in rows[:2])
    three_definitions = "\n".join(row.definition for row in rows[:3])
    ResourceFile.objects.create(name="two", text=two_definitions)
    ResourceFile.objects.create(name="three", text=three_definitions)

    rendered = render(make('{{ resource("two") }}'), base("A"))
    assert two_definitions in rendered.user

    with pytest.raises(TaxonomyLeak):
        render(make('{{ resource("three") }}'), base("A"))


@pytest.mark.django_db
def test_explicit_guard_taxonomy_is_scanned_instead_of_the_active_one(taxonomy):
    """G3: an explicit guard_taxonomy is what gets scanned, even when a
    DIFFERENT taxonomy (here, the `taxonomy` fixture) is the active one."""
    other = Taxonomy.objects.create(vocabulary_id="HCV-OTHER", version="1.0", is_active=False)
    HCV.objects.create(taxonomy=other, hcv_id="X-01", name="Unrelated Vulnerability", order=1)
    ResourceFile.objects.create(name="other_table", text=format_hcv_table(other))

    context = base("A", guard_taxonomy=other)
    with pytest.raises(TaxonomyLeak, match="X-01"):
        render(make('{{ resource("other_table") }}'), context)


@pytest.mark.django_db
def test_no_active_and_no_explicit_guard_taxonomy_warns(db):
    """G3: with neither an explicit guard_taxonomy nor an active Taxonomy, the
    backstop cannot run at all. That must be visible, not silent."""
    assert Taxonomy.objects.filter(is_active=True).count() == 0
    with pytest.warns(UserWarning, match="backstop cannot run"):
        base("A")


@pytest.mark.django_db
def test_one_or_two_ordinary_hcv_names_do_not_trip_the_backstop():
    """False-positive floor, re-confirmed under casefolded matching: a scenario
    mentioning one or two HCV names in ordinary English is not a taxonomy dump."""
    tax = Taxonomy.objects.create(vocabulary_id="HCV-PROSE", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="P-01", name="Tailgating", order=1)
    HCV.objects.create(taxonomy=tax, hcv_id="P-02", name="Propaganda", order=2)

    one = render(make("A guard noticed Tailgating near the loading dock."), base("A"))
    assert "Tailgating" in one.user

    two = render(
        make(
            "A guard noticed Tailgating near the loading dock, and a wave of "
            "Propaganda about the incident spread online."
        ),
        base("A"),
    )
    assert "Propaganda" in two.user


@pytest.mark.django_db
def test_a_single_lowercase_incidental_match_does_not_trip_the_backstop():
    """False-positive floor matters more now that matching is casefolded: a
    single lowercase occurrence of an HCV name in ordinary prose, on its own,
    must not trip the scan."""
    tax = Taxonomy.objects.create(vocabulary_id="HCV-PROSE-2", version="1.0", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="P-03", name="Tailgating", order=1)

    rendered = render(make("A guard noticed tailgating near the loading dock."), base("A"))
    assert "tailgating" in rendered.user


# --- Round 3: two residual silent-no-op gaps, same class as G3 -------------------


@pytest.mark.django_db
def test_a_non_taxonomy_guard_value_is_rejected_instead_of_crashing():
    """R1: guard_taxonomy=0 (falsy but not None, or any non-Taxonomy) must be
    rejected at context-build time the same way `taxonomy` already is — not
    slip past and crash later at render time with a raw AttributeError."""
    with pytest.raises(TaxonomyLeak):
        base("A", guard_taxonomy=0)


@pytest.mark.django_db
def test_an_empty_guard_taxonomy_warns_and_still_renders():
    """R2: an empty Taxonomy (zero HCV rows) is a broken import, not a leak —
    the backstop cannot run against it, so it must warn (not raise), and the
    render must still succeed rather than fail on a data problem."""
    empty_taxonomy = Taxonomy.objects.create(
        vocabulary_id="HCV-EMPTY", version="1.0", is_active=False
    )
    assert empty_taxonomy.hcvs.count() == 0

    context = base("A", guard_taxonomy=empty_taxonomy)
    with pytest.warns(UserWarning, match="backstop cannot run"):
        rendered = render(make("Systems: {{ n_systems }}."), context)
    assert rendered.user == "Systems: 1."


# --- Round 4: the fourth leak route — output_schema, sent in the SAME request body
# as the two prompts (as response_format.json_schema.schema) and copied forward by
# new_version()/PATCH, so a Run B schema can be forked into a Run A template ------


def schema_listing(taxonomy):
    """A response schema shaped like a real labelling schema, whose enum pins
    every hcv_id — exactly what a Run B template carries, and exactly what a
    rename or a fork would copy into a Run A one."""
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "hcv_id": {
                            "type": "string",
                            "enum": [row.hcv_id for row in taxonomy.hcvs.all()],
                        }
                    },
                },
            }
        },
    }


@pytest.mark.django_db
def test_output_schema_carrying_hcv_ids_is_caught_under_variant_a(taxonomy):
    """C1: the schema travels in the same request body as the prompts, so it is
    scanned with them. Without this, a template whose prompts are the innocent
    string "Generate." ships all 21 ids to the model and raises nothing."""
    template = make("Generate.", output_schema=schema_listing(taxonomy))
    with pytest.raises(TaxonomyLeak, match="HCV-06"):
        render(template, base("A"))


@pytest.mark.django_db
def test_output_schema_carrying_definitions_is_caught_under_variant_a(taxonomy_with_definitions):
    """C1, the other column: a schema whose `description` documents the taxonomy
    in prose leaks just as much as one enumerating its ids."""
    definitions = " ".join(row.definition for row in taxonomy_with_definitions.hcvs.all())
    template = make(
        "Generate.",
        output_schema={
            "type": "object",
            "properties": {"labels": {"type": "array", "description": definitions}},
        },
    )
    with pytest.raises(TaxonomyLeak, match="names/definitions"):
        render(template, base("A"))


@pytest.mark.django_db
def test_variant_b_may_carry_the_taxonomy_in_its_output_schema(taxonomy):
    """The mirror image: Run B is the guided variant, so pinning the closed list
    into its schema is the intended design, not a leak."""
    template = make("Generate.", output_schema=schema_listing(taxonomy))
    rendered = render(template, base("B", taxonomy=taxonomy))
    assert rendered.user == "Generate."


@pytest.mark.django_db
def test_an_empty_output_schema_does_not_disturb_a_variant_a_render(taxonomy):
    """The default schema is `{}`; scanning it must stay a no-op."""
    assert render(make("Generate."), base("A")).user == "Generate."


# --- Round 4: the render record (I4) and the fail-closed variant marker (I5) ----


@pytest.mark.django_db
def test_a_guarded_variant_a_render_records_that_the_scan_ran(taxonomy):
    """I4: "Run A never saw the taxonomy" is the central claim of the tool, so
    the render itself carries the evidence — which taxonomy guarded it, and that
    the scan actually executed — rather than a warning on a worker's stderr."""
    rendered = render(make("Generate."), base("A", guard_taxonomy=taxonomy))

    assert rendered.leak_scan_ran is True
    assert rendered.guarded_by == taxonomy.pk


@pytest.mark.django_db
def test_an_unguarded_variant_a_render_records_that_the_scan_did_not_run(db):
    """I4: with no taxonomy to scan against, the record says so plainly instead
    of looking identical to a render that passed a real check."""
    assert Taxonomy.objects.filter(is_active=True).count() == 0
    with pytest.warns(UserWarning, match="backstop cannot run"):
        context = base("A")
    rendered = render(make("Generate."), context)

    assert rendered.leak_scan_ran is False
    assert rendered.guarded_by is None


@pytest.mark.django_db
def test_an_empty_guard_taxonomy_records_that_the_scan_did_not_run(db):
    """I4 boundary: a guard taxonomy with no rows cannot be scanned against, so
    the render must not claim it was."""
    empty = Taxonomy.objects.create(vocabulary_id="HCV-EMPTY-2", version="1.0")
    context = base("A", guard_taxonomy=empty)
    with pytest.warns(UserWarning, match="backstop cannot run"):
        rendered = render(make("Generate."), context)

    assert rendered.leak_scan_ran is False


@pytest.mark.django_db
def test_a_variant_b_render_reports_no_scan(taxonomy):
    """I4: Run B is allowed the taxonomy, so there is no scan to report."""
    rendered = render(make("Generate."), base("B", taxonomy=taxonomy))

    assert rendered.leak_scan_ran is False
    assert rendered.guarded_by is None


@pytest.mark.django_db
def test_a_hand_built_phase2_context_without_a_variant_is_refused(taxonomy):
    """I5: a context assembled by hand carries no _variant, which would switch
    off the resource guard and the output scan silently. render() refuses it
    rather than producing a prompt whose isolation nothing checked."""
    ResourceFile.objects.create(name="hcv_table", text=format_hcv_table(taxonomy))
    hand_built = {
        "seed_systems": [],
        "fewshots": [],
        "generate_systems": True,
        "generate_scenarios": True,
        "n_systems": 1,
        "n_scenarios_per_system": 1,
        "target_system": None,
    }
    with pytest.raises(TaxonomyLeak, match="_variant"):
        render(make('{{ resource("hcv_table") }}'), hand_built)


@pytest.mark.django_db
def test_contexts_from_the_builder_still_render_for_both_variants(taxonomy):
    """I5, the other side: marking BOTH variants must not make Run B unrenderable."""
    assert render(make("Generate."), base("A", guard_taxonomy=taxonomy)).user == "Generate."
    assert render(make("Generate."), base("B", taxonomy=taxonomy)).user == "Generate."


@pytest.mark.django_db
def test_the_variant_marker_is_still_not_a_template_variable(taxonomy):
    """I5: _variant is set for Run B now too, and must stay unaddressable there."""
    with pytest.raises(PromptRenderError, match="_variant"):
        render(make("{{ _variant }}"), base("B", taxonomy=taxonomy))


# --- Round 5: the marker check must not be evadable by a PARTIAL phase-2 dict ---

# Every key build_phase2_context puts in a context. Each one alone is enough to
# make a dict phase-2-shaped, and none of them may buy an exemption from the
# variant marker.
PHASE2_KEYS = [
    "seed_systems",
    "n_systems",
    "n_scenarios_per_system",
    "generate_systems",
    "generate_scenarios",
    "target_system",
]


@pytest.mark.django_db
@pytest.mark.parametrize("key", PHASE2_KEYS)
def test_any_single_phase2_key_without_a_variant_is_refused(key):
    """Recognising phase 2 by two of its seven keys let a context that omitted
    those two — but kept the rest — render with no variant at all."""
    with pytest.raises(TaxonomyLeak, match="_variant"):
        render(make("Generate."), {key: None})


@pytest.mark.django_db
def test_a_partial_phase2_context_cannot_render_a_taxonomy_resource(taxonomy):
    """The re-reviewer's evasion, exactly: a hand-built context keeping fewshots,
    generate_systems, generate_scenarios, n_scenarios_per_system and
    target_system while omitting seed_systems and n_systems. It used to render a
    kind=TAXONOMY ResourceFile straight through, verbatim, unguarded and
    unscanned, because render() did not recognise it as phase 2."""
    ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    evasion = {
        "fewshots": [],
        "generate_systems": True,
        "generate_scenarios": True,
        "n_scenarios_per_system": 1,
        "target_system": None,
    }
    with pytest.raises(TaxonomyLeak):
        render(make('{{ resource("hcv_taxonomy") }}'), evasion)


@pytest.mark.django_db
def test_an_unrecognised_context_shape_is_refused(taxonomy):
    """The fail-closed default: a context that is neither a marked generation
    context nor a phase 3 labelling one is refused, rather than assumed
    harmless. A shape nobody anticipated is exactly the case where guessing
    "probably fine" is how the guarantee is lost."""
    ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    with pytest.raises(TaxonomyLeak, match="_variant"):
        render(make('{{ resource("hcv_taxonomy") }}'), {"something_plan_3_invents": 1})


@pytest.mark.django_db
def test_an_empty_context_is_refused(taxonomy):
    with pytest.raises(TaxonomyLeak, match="_variant"):
        render(make("Generate."), {})
