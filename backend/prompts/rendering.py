"""Rendering prompts, and the structural guarantee that Run A never sees the taxonomy.

The experiment compares what a model produces WITHOUT the taxonomy (Run A) against
what it produces WITH it (Run B). If the taxonomy leaked into a Run A prompt the
comparison would be worthless, so isolation is enforced here in layers rather than
left to whoever writes the template:

- the context layer refuses to hold a taxonomy object for Run A at all, and
  `hcv_table()` is simply not defined for it (the direct route);
- `resource()` and few-shot files refuse taxonomy-kind ResourceFiles under Run A,
  closing the two known transitive routes — a taxonomy uploaded as a resource, or
  referenced by a few-shot example — with a clear error instead of a silent leak;
- `render()` scans the finished Run A string, case-insensitively, against the
  guard taxonomy's identifiers, names, and definitions as a backstop, in case
  some other route reaches it later that nobody has thought of yet — any single
  identifier match is a hard fail, and three or more distinct name/definition
  matches combined is treated as a dumped list rather than incidental prose.
  The scan covers the template's `output_schema` too, not just the two prompts:
  the schema ships to the model in the SAME request body (as
  `response_format.json_schema.schema`), and it is one of `COPIED_FIELDS`, so a
  `new_version(name=...)` or a renaming PATCH forks a Run B schema — enum of
  every hcv_id, definitions in its descriptions — into a Run A template with
  prompts that look entirely innocent;
- `assert_request_not_leaking_taxonomy()` walks the FINAL request dict and
  scans every raw string it contains — not the two prompt strings `render()`
  sees, but everything about to be sent to the provider, including whatever
  a future field carries — immediately before the HTTP call. Deliberately
  RAW text, not a `json.dumps`'d blob: JSON's own escaping of a quote,
  backslash, newline or tab would otherwise hide a taxonomy name or
  definition containing one of those characters from a plain substring
  match, disagreeing with the render-time scan (which compares against raw
  prompt strings) about what counts as "the same content". This is the
  layer that survives a mistake the other three cannot even see: a
  field added to the request AFTER rendering (an `output_schema` copied
  straight into the body instead of coming from the template, say), which
  `render()`'s own scan has no visibility into no matter how thorough it is.
  Wired into `llm.client.OpenRouterClient.complete()` as a `preflight`
  callable — see `pipeline.generation` for how phase 2 attaches it to every
  call, both variants, so nobody has to remember to do so only for Run A.

Whether that backstop actually ran is recorded on the `RenderedPrompt` itself
(`leak_scan_ran`, `guarded_by`) rather than left to a warning on someone's
stderr: for a tool whose central claim is "Run A never saw the taxonomy", the
evidence belongs in the archive next to the material it vouches for.

**A trap for template authors: `{% if taxonomy %}` is wrong. Use
`{% if taxonomy is defined %}`.** One `PromptTemplate` renders both variants
of a phase-2 repetition (`Experiment.phase2_prompt` is a single FK), and
rendering below runs under `StrictUndefined`. Under Run A, `taxonomy` is not
merely falsy or `None` — it is genuinely ABSENT from the context (that IS the
isolation guarantee above, doing its job), so referencing it at all raises
`jinja2.exceptions.UndefinedError`, caught here and re-raised as
`PromptRenderError: 'taxonomy' is undefined`. `is defined` is the one Jinja2
idiom that checks presence without touching the (absent) value, so it is the
only spelling of this guard that renders under both variants — every other
natural-looking form (`{% if taxonomy %}`, `{% if taxonomy != None %}`,
`{{ taxonomy or ... }}`) fails Run A outright. This is correct, deliberate,
fail-closed behaviour: a template must never be able to treat a missing
taxonomy as merely "empty" and carry on as if guided — but the symptom is the
most confusing one in this codebase, because Run B renders the exact same
template fine while Run A fails on a variable the author can see sitting
right there in an `{% if %}` block. See the spec
(`docs/superpowers/specs/2026-09-07-hcv-pipeline-design.md`, §5.3) for the
same note next to the template context table, and
`prompts/tests/test_rendering.py` for a test that pins this idiom as a
checked guarantee rather than advice someone has to remember.
"""

import json
import warnings
from dataclasses import dataclass, replace

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from corpus.models import ResourceFile, ResourceKind
from taxonomy.models import Taxonomy

VARIANT_UNBIASED = "A"
VARIANT_GUIDED = "B"

# Names like "Propaganda" and "Tailgating" are ordinary English a legitimate
# scenario may use, so a single match is not evidence of a leak. Three or more
# distinct names and/or definitions COMBINED is a dumped list, not prose.
LEAK_MATCH_THRESHOLD = 3

# What identifies a phase 3 labelling context: the only context kind that has no
# variant, because labelling always sees the taxonomy. Must stay in sync with
# build_phase3_context — but note which way a mistake falls. Renaming a key in
# one place and not the other refuses phase 3 renders outright, which is loud and
# fixed in a minute; it can never let an unmarked generation context through,
# because anything this set does not match is refused. That asymmetry is why the
# check enumerates phase 3 rather than phase 2.
PHASE3_CONTEXT_KEYS = frozenset({"scenario", "system_description"})


class TaxonomyLeak(RuntimeError):
    """Raised when a context, resource, or rendered prompt would expose the
    taxonomy to a variant that must not see it."""


class PromptRenderError(RuntimeError):
    """Raised when a template fails to render. Never rendered partially."""


@dataclass(frozen=True)
class RenderedPrompt:
    """The two strings a call will send, plus the provenance of the Run A scan.

    `leak_scan_ran` says whether the backstop actually compared this render
    against a taxonomy, and `guarded_by` says which one — so a stored Run A
    result can prove the isolation check ran rather than merely asserting it.
    Both stay False/None for Run B and for phase 3, where there is nothing to
    isolate.

    Scoped strictly to the RENDER-time scan: these two fields say nothing
    about `assert_request_not_leaking_taxonomy`, the separate final-request
    choke point a phase-2 call additionally passes through immediately
    before the HTTP call (see `pipeline.generation._preflight`) — this
    dataclass is built and returned by `render()`, before a request dict
    (or a client, or a call) exists to scan at all. `pipeline.models.LLMCall
    .leak_scan_ran`, which this feeds via straight assignment, ends up
    meaning something WIDER once both scans are wired in for phase 2 — see
    that field's own comment for why a persisted `True` there is stronger
    evidence than this dataclass field is on its own.
    """

    system: str
    user: str
    guarded_by: int | None = None
    leak_scan_ran: bool = False


def format_hcv_table(taxonomy):
    """The taxonomy as a readable numbered list, for injection into Run B prompts."""
    return "\n".join(
        f"{row.hcv_id}: {row.name}" + (f" — {row.definition}" if row.definition else "")
        for row in taxonomy.hcvs.all()
    )


def unscannable_hcv_rows(rows):
    """Which of `rows` carry NO content this module could ever scan for —
    `hcv_id`, `name` and `definition` all blank (whitespace included).

    The ONE definition of "scannable", deliberately shared rather than
    restated: `assert_no_taxonomy_leak` below refuses to certify a scan
    against such rows, and `pipeline.models.taxonomy_missing_rows_error`
    imports this same function to refuse a run pinned to them before it is
    ever created. Round 4, leak 1 was exactly the two levels DISAGREEING —
    the create-time guard asked `taxonomy.hcvs.exists()` (row objects) while
    the scan needs row CONTENT — so a taxonomy of blank rows passed the
    first and then scanned for nothing at the second, returning True.
    One predicate means they cannot drift apart into two answers again.

    Whitespace is stripped: an `hcv_id` of `"   "` is content-free in every
    sense that matters, and scanning for it as a literal substring would
    match almost any prompt — a guard that fails on everything, not one
    that guards.
    """
    return [
        row
        for row in rows
        if not (row.hcv_id.strip() or row.name.strip() or row.definition.strip())
    ]


def assert_no_taxonomy_leak(taxonomy, rendered, extra=""):
    """Backstop for Run A: no taxonomy content may arrive by ANY route.

    The context-layer guards close the direct route and the known transitive
    ones. This closes the rest, including routes added later, by checking the
    finished string rather than trusting how it was assembled.

    Matching is case-insensitive (casefolded on both sides) so a template
    filter like `|lower` or `|upper` cannot slip taxonomy content past the
    scan just by changing its case — the original casing is kept in the error
    message, only the comparison is folded. Any single identifier match is a
    hard fail on its own; names and definitions are individually harmless
    English a legitimate scenario might use, so they only count as a leak once
    three or more DISTINCT ones, combined, show up — that is a dumped list,
    not prose.

    `extra` is any further text that will travel to the model in the same
    request but is not one of the two prompts — the serialised `output_schema`,
    today — folded into the same blob so one scan covers the whole request body.

    A taxonomy with zero HCV rows can't be scanned against at all — that's a
    broken import, not a leak, so it warns (same as an entirely missing guard
    taxonomy) and returns rather than raising. A taxonomy whose rows carry no
    `hcv_id`/`name`/`definition` (see `unscannable_hcv_rows` above) is the
    SAME condition wearing a disguise, and gets the same answer: round 4,
    leak 1 turned on those rows passing `.exists()` while contributing
    nothing to any of the three comparisons below, so the scan ran, matched
    nothing, and returned True — `leak_scan_ran=True` archived as proof over
    a prompt with real HCV text in it. Returning False instead is what makes
    `_assert_request_has_no_taxonomy`'s final checkpoint refuse to send it at
    all, with zero provider calls.

    ANY content-free row is enough to refuse, not merely all of them: 20
    blanked rows out of 21 would otherwise leave a scan quietly covering
    1/21 of the taxonomy while still certifying itself, which is the same
    silent narrowing in smaller print. Nothing legitimate produces such a
    row — `import_taxonomy` refuses one, and `taxonomy.admin` offers no add
    form — so this refuses only genuinely broken data.

    Returns True if a scan actually happened, False if it could not run, so the
    caller can record that distinction instead of assuming a clean pass.
    """
    if taxonomy is None:
        return False
    rows = list(taxonomy.hcvs.all())
    if not rows:
        warnings.warn(
            f"Guard taxonomy {taxonomy} has no HCV rows: the Run A leak backstop "
            "cannot run. Check that the taxonomy was imported correctly.",
            stacklevel=2,
        )
        return False
    unscannable = unscannable_hcv_rows(rows)
    if unscannable:
        warnings.warn(
            f"Guard taxonomy {taxonomy} has {len(unscannable)} of {len(rows)} HCV "
            "rows with no hcv_id, name or definition: the Run A leak backstop "
            "would scan for nothing and still report success. Check that the "
            "taxonomy was imported correctly.",
            stacklevel=2,
        )
        return False
    blob = f"{rendered.system}\n{rendered.user}\n{extra}".casefold()

    found_ids = sorted(row.hcv_id for row in rows if row.hcv_id and row.hcv_id.casefold() in blob)
    if found_ids:
        raise TaxonomyLeak(
            "Run A request contains taxonomy identifiers "
            f"{found_ids}. Something reached the taxonomy transitively — check "
            "resource() calls, few-shot files referenced by this template, and "
            "its output_schema."
        )

    found_names = {row.name for row in rows if row.name and row.name.casefold() in blob}
    found_definitions = {
        row.definition for row in rows if row.definition and row.definition.casefold() in blob
    }
    combined_count = len(found_names) + len(found_definitions)
    if combined_count >= LEAK_MATCH_THRESHOLD:
        matches = sorted(found_names) + sorted(found_definitions)
        raise TaxonomyLeak(
            f"Run A request contains {combined_count} taxonomy names/definitions "
            f"{matches[:5]}. That is a taxonomy listing, not incidental prose."
        )
    return True


def _iter_request_text(value, _ancestors=frozenset()):
    """Walk a JSON-like value (nested dicts/lists/tuples) and yield every
    string leaf it contains, RAW — never round-tripped through `json.dumps`.

    That distinction is load-bearing, not cosmetic: `json.dumps` ESCAPES a
    `"`, a backslash, a real newline and a real tab inside a string value
    (`"` becomes the two characters `\\"`, a real newline becomes the two
    characters `\\n`, and so on). A taxonomy name or definition containing
    any of those characters, embedded verbatim in a request field, therefore
    stops being a literal substring of a JSON-serialised blob even though it
    IS a literal substring of the raw text — exactly the gap that let the
    render-time scan (which compares against `rendered.system`/`.user`,
    never JSON-encoded) catch content the request scan, when it used to
    serialise the whole request first, silently missed. Walking the
    structure and comparing raw string leaves is what keeps this predicate
    genuinely the SAME as `assert_no_taxonomy_leak`'s own comparison, not a
    second one that happens to agree only on ASCII, quote-free content.

    Anything that is not a string, a dict, a list/tuple, a number, a bool or
    `None` is coerced with `str()` rather than dropped — the same "capture
    unknown content instead of silently skipping it" principle a bare
    `default=str` served when this used to go through `json.dumps`.

    Cycle-safe: `_ancestors` carries the `id()` of every dict/list/tuple
    currently being walked (never mutated — each recursive call gets its own
    extended copy, so two branches that happen to share the same sub-object
    are not mistaken for a cycle). A value that contains one of its own
    ancestors raises `ValueError`, the same failure shape `json.dumps` itself
    raises on a circular structure, rather than recursing until the stack
    overflows.
    """
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict | list | tuple):
        marker = id(value)
        if marker in _ancestors:
            raise ValueError("Circular reference detected while scanning the request.")
        nested = _ancestors | {marker}
        items = value.values() if isinstance(value, dict) else value
        for item in items:
            yield from _iter_request_text(item, nested)
        return
    if value is None or isinstance(value, bool | int | float):
        return
    yield str(value)


def assert_request_not_leaking_taxonomy(*, variant, guard_taxonomy, request):
    """The final choke point: scan every string in the request about to go to
    the provider — walked, not serialised — immediately before the HTTP call,
    for variant A only.

    This is deliberately not the same check as `assert_no_taxonomy_leak`
    above, which scans the two rendered prompt strings (plus the template's
    own `output_schema`) at RENDER time, before a request dict even exists.
    That backstop can only ever see what it is handed; it has no way to see
    something a LATER step adds to the request — an `output_schema` copied
    straight into the body by a refactor instead of coming from the
    template, a field nobody has invented yet. This function closes exactly
    that gap by running LAST, on the actual dict about to be sent, so a
    field added to the request tomorrow is covered automatically — nobody
    has to remember to teach a second scanner about it.

    It is a thin caller around `assert_no_taxonomy_leak`, not a second,
    divergent notion of "taxonomy content": every string in the request is
    collected, RAW, by `_iter_request_text` (walking the structure — see its
    own docstring for why this is NOT the same as `json.dumps`-then-scan,
    which used to let a taxonomy name or definition containing a quote,
    backslash, newline or tab slip past this function while the render-time
    scan still caught it) and joined into one blob handed to
    `assert_no_taxonomy_leak` as `extra`, so the identical matching rules
    apply (any single `hcv_id` match, or three or more distinct names/
    definitions combined) against the identical KIND of text the render-time
    scan compares against. Two hand-maintained ideas of what counts as a
    leak is exactly the defect pattern this codebase keeps re-discovering;
    this keeps it to one — and now keeps them from disagreeing about what
    "the same text" even means.

    Fails CLOSED wherever `assert_no_taxonomy_leak` itself would only warn
    and let a render through — deliberately stricter, because this is the
    LAST checkpoint before a real provider call, not a backstop with another
    layer behind it:
    - `variant` not being "A" or "B" means this function cannot tell
      whether the request even needs scanning — refused rather than assumed
      safe;
    - under variant A, `guard_taxonomy=None`, or a guard taxonomy with zero
      HCV rows, both mean `assert_no_taxonomy_leak` cannot actually compare
      anything (it returns False rather than raising, for either — see its
      own docstring) — refused here instead of sent unverified;
    - a request whose structure cannot be walked at all — concretely, a
      circular reference, which raises `ValueError` — is refused for the
      same reason: if it cannot be scanned, it cannot be sent. This is
      narrower than it sounds: an ordinary "weird" value (some object that
      is not a string/dict/list/tuple/number/bool/`None`) is not refused —
      `_iter_request_text` coerces it with `str()` and scans that instead,
      so only a structure that cannot be WALKED at all trips this, not
      merely one that is exotic.

    Variant B is not scanned at all — it legitimately carries the taxonomy,
    by design, and scanning it would simply raise on material that belongs
    there. Phase 3 labelling has no variant of its own and must never reach
    this function at all; that scoping is the CALLER's job (only
    `pipeline.generation`'s phase-2 call site wires this in as a client
    `preflight` — see `llm.client.OpenRouterClient.complete` — while
    `pipeline.labelling`'s phase-3 call site does not), not something this
    function can determine from a bare `variant` string alone.

    Returns True if a scan actually ran (variant A, resolved against a real
    taxonomy), False if it was correctly skipped (variant B) — mirroring
    `assert_no_taxonomy_leak`'s own return contract, for the same reason: so
    a caller can record that distinction rather than assume one.
    """
    if variant not in (VARIANT_UNBIASED, VARIANT_GUIDED):
        raise TaxonomyLeak(
            f"Cannot determine whether this request is variant A or B (got "
            f"{variant!r}). Refusing to send a request whose isolation "
            "requirement is unknown."
        )
    if variant != VARIANT_UNBIASED:
        return False
    if guard_taxonomy is None:
        raise TaxonomyLeak(
            "No taxonomy to check this variant-A request against before "
            "sending it. Refusing to send a request that cannot be "
            "verified taxonomy-free."
        )
    try:
        blob = "\n".join(_iter_request_text(request))
    except (TypeError, ValueError) as error:
        raise TaxonomyLeak(
            "The final request body could not be walked for scanning "
            f"({error}). Refusing to send a request that cannot be "
            "verified taxonomy-free."
        ) from error
    scanned = assert_no_taxonomy_leak(
        guard_taxonomy, RenderedPrompt(system="", user=""), extra=blob
    )
    if not scanned:
        raise TaxonomyLeak(
            f"Guard taxonomy {guard_taxonomy} has no HCV rows the scan can use "
            "(none at all, or none carrying an hcv_id/name/definition — see "
            "`unscannable_hcv_rows`): the final request scan cannot run, and "
            "this is the last checkpoint before the provider call. Refusing to "
            "send an unscanned variant-A request."
        )
    return True


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


def _fewshot_rows(fewshots, variant=None):
    rows = []
    for f in fewshots:
        if variant == VARIANT_UNBIASED:
            for side in ("input_file", "output_file"):
                referenced = getattr(f, side)
                if referenced is not None and referenced.kind == ResourceKind.TAXONOMY:
                    raise TaxonomyLeak(
                        f'Few-shot example "{f.name}" references a taxonomy resource '
                        f"via {side} and cannot be used in a Run A (unbiased) context."
                    )
        rows.append({"name": f.name, "input": f.resolved_input, "output": f.resolved_output})
    return rows


def _system_rows(systems):
    return [{"title": s.title, "body": s.body, "slug": s.slug} for s in systems]


def _make_resource(variant):
    """Build the `resource()` template global.

    Bound fresh per render so it can refuse a taxonomy-kind ResourceFile under
    Run A — the resource route is deliberately closed here, not just at the
    context layer, since a template author could otherwise reach the taxonomy
    without ever touching the `taxonomy` context variable.
    """

    def resource(name):
        try:
            resource_file = ResourceFile.objects.get(name=name)
        except ResourceFile.DoesNotExist as error:
            raise PromptRenderError(
                f'resource("{name}") does not exist. Upload it first, or fix the name.'
            ) from error
        if variant == VARIANT_UNBIASED and resource_file.kind == ResourceKind.TAXONOMY:
            raise TaxonomyLeak(
                f'resource("{name}") is a taxonomy resource and cannot be used in a '
                "Run A (unbiased) context."
            )
        return resource_file.text

    return resource


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
    guard_taxonomy=None,
):
    """Build the Phase 2 template context, enforcing variant isolation.

    Run A must be given no taxonomy; Run B must be given one. Both mistakes raise
    rather than quietly producing a prompt that measures the wrong thing.

    `guard_taxonomy` is used ONLY under variant A, and ONLY to scan the rendered
    output for leaked taxonomy content (see `assert_no_taxonomy_leak`) — it is
    never injected into the template and never exposed as a context variable,
    unlike `taxonomy`. Pass the specific taxonomy a run has pinned so the scan
    checks against exactly that one; if omitted, the currently active `Taxonomy`
    is used instead. If neither is available, the backstop simply cannot run —
    that is allowed (so Run A still works before any taxonomy has been
    imported), but it is surfaced as a warning rather than failing silently.
    """
    if variant not in (VARIANT_UNBIASED, VARIANT_GUIDED):
        raise TaxonomyLeak(f"Unknown variant {variant!r}; expected 'A' or 'B'.")
    if taxonomy is not None and not isinstance(taxonomy, Taxonomy):
        raise TaxonomyLeak(
            f"taxonomy must be a Taxonomy instance or None, got {type(taxonomy).__name__!r}."
        )
    if guard_taxonomy is not None and not isinstance(guard_taxonomy, Taxonomy):
        raise TaxonomyLeak(
            "guard_taxonomy must be a Taxonomy instance or None, got "
            f"{type(guard_taxonomy).__name__!r}."
        )
    if variant == VARIANT_UNBIASED and taxonomy is not None:
        raise TaxonomyLeak(
            "Run A is the unbiased variant and must not receive a taxonomy. "
            "Pass taxonomy=None, or run this as variant B."
        )
    if variant == VARIANT_GUIDED and taxonomy is None:
        raise TaxonomyLeak("Run B is the guided variant and requires a taxonomy.")

    context = {
        "seed_systems": _system_rows(seed_systems),
        "fewshots": _fewshot_rows(fewshots, variant=variant),
        "generate_systems": generate_systems,
        "generate_scenarios": generate_scenarios,
        "n_systems": n_systems,
        "n_scenarios_per_system": n_scenarios_per_system,
        "target_system": _system_rows([target_system])[0] if target_system else None,
    }
    if taxonomy is not None:
        context["taxonomy"] = _taxonomy_rows(taxonomy)
        context["_taxonomy_object"] = taxonomy
    # Set for BOTH variants, deliberately. It is not injected into the template
    # (the underscore prefix keeps it out), and marking only Run A would mean an
    # unmarked phase-2 context is indistinguishable from a legitimate Run B one —
    # so a caller that assembled a context by hand would silently disable the
    # resource guard and the output scan. With both marked, render() can refuse
    # the unmarked case outright instead of failing open on the variant whose
    # isolation is the whole point.
    context["_variant"] = variant
    if variant == VARIANT_UNBIASED:
        resolved_guard = guard_taxonomy if guard_taxonomy is not None else Taxonomy.active()
        if resolved_guard is None:
            warnings.warn(
                "No taxonomy available to guard this Run A (unbiased) context: the "
                "Run A leak backstop cannot run. Pass guard_taxonomy= explicitly, or "
                "activate a Taxonomy, to restore it.",
                stacklevel=2,
            )
        context["_guard_taxonomy"] = resolved_guard
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


def _is_phase3_context(context):
    """Is this the one context kind that legitimately carries no variant?

    Recognition is deliberately inverted: rather than listing what a generation
    context looks like and demanding a marker only for those, render() demands a
    marker for EVERYTHING except a phase 3 labelling context. Enumerating phase 2
    fails open on any shape the list does not happen to cover — a partial dict, a
    key renamed, a context kind plan 3 invents — and each of those silently
    switches off the Run A guards. Enumerating phase 3 instead fails closed: an
    unrecognised shape is refused, loudly, and the fix is one keyword argument.
    """
    return context.keys() >= PHASE3_CONTEXT_KEYS


def _environment(context):
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    env.globals["resource"] = _make_resource(context.get("_variant"))
    taxonomy = context.get("_taxonomy_object")
    if taxonomy is not None:
        env.globals["hcv_table"] = lambda: format_hcv_table(taxonomy)
    return env


def render(template, context):
    """Render both halves of a template. This is the ONLY renderer.

    Preview and the pipeline both call it, so what an admin sees previewed is
    byte-identical to what is sent. For a Run A context, the rendered strings and
    the template's `output_schema` — everything that goes into the request body —
    are scanned for taxonomy content as a backstop before this returns; see
    assert_no_taxonomy_leak.

    Any context with no `_variant` marker is refused unless it is a phase 3
    labelling context, which has no variant by design. Failing open would
    silently switch off the resource guard and the output scan for a context
    that might well be a Run A one, which is the exact failure this module
    exists to make impossible — so an unmarked generation context, or a context
    of a shape nobody here recognises, is a TaxonomyLeak rather than a
    best-effort render.
    """
    variant = context.get("_variant")
    if variant is None and not _is_phase3_context(context):
        raise TaxonomyLeak(
            "This context carries no _variant marker and is not a phase 3 "
            "labelling context, so render() cannot tell an unguarded Run A from "
            "a Run B and the isolation layers would silently do nothing. Build "
            "it with build_phase2_context(variant=...), or with "
            "build_phase3_context() for labelling."
        )

    env = _environment(context)
    exposed = {key: value for key, value in context.items() if not key.startswith("_")}
    try:
        rendered = RenderedPrompt(
            system=env.from_string(template.system_prompt).render(**exposed),
            user=env.from_string(template.user_prompt).render(**exposed),
        )
    except (TemplateError, TypeError) as error:
        # TaxonomyLeak (raised by resource()) is neither of these, so it is never
        # caught here — it propagates as itself, not rewrapped as a render error.
        raise PromptRenderError(str(error)) from error

    if variant != VARIANT_UNBIASED:
        return rendered

    guard = context.get("_guard_taxonomy")
    scanned = assert_no_taxonomy_leak(
        guard,
        rendered,
        # The schema is part of the request body, so it is part of what is scanned.
        extra=json.dumps(template.output_schema, ensure_ascii=False),
    )
    return replace(
        rendered,
        guarded_by=guard.pk if guard is not None else None,
        leak_scan_ran=scanned,
    )
