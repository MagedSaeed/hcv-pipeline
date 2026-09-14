"""The meta-test: no validation rule may exist that the ad-hoc path skips.

Round 4 was the FOURTH review round to find a rule living in
`Experiment.clean()` that had never been hand-ported to iterate mode
(`POST /api/phase-runs/`). Iterate mode has no `PipelineRun.config`
snapshot and bypasses `launch()`'s `experiment.full_clean()`, so every rule
`clean()` gains has to reach that path some other way — and three rounds
running, the "other way" was somebody remembering, and somebody did not.
Each miss was invisible in the results: a variant-A run completed, archived
`leak_scan_ran=True` and a `guarded_by` taxonomy, and nothing downstream
distinguished it from a run whose isolation actually held.

Round 4's structural answer has two halves. The first is
`pipeline.models.generation_config_errors` — ONE shared checker for the
config-shaped rules, called by `Experiment.clean()`, by
`PhaseRunCreateSerializer.validate()`, and by
`execution_config._adhoc_generation_config` on BOTH its branches, so adding
a config rule reaches all three sites at once with nothing to port. The
second half is this file, because the first half alone is still a
convention: nothing stops a future rule being written as a fresh
`*_error` helper wired only into `clean()`, exactly as the last three
were. This test enumerates the helpers and fails the build when one of
them cannot be reached from the ad-hoc path at all.

What it does NOT cover, stated plainly so nobody mistakes its scope:

- It checks REACHABILITY, not correctness. A helper called with the wrong
  argument, or under an `if` that never fires, still counts as referenced.
  The behavioural tests in `test_iterate_mode.py`/`test_execution_config.py`
  are what prove each rule actually bites.
- It only covers rules expressed as `*_error` helpers in `pipeline.models`.
  A rule written inline inside `Experiment.clean()`, never factored into a
  helper, is invisible to it — which is itself a reason to keep writing
  them as helpers.
- Rules that are NOT config-shaped are outside the shared checker by
  design and are covered here only because they happen to be called from a
  scanned function. Leak 2's rule class in particular — "a run's own field
  must stay bound to what it borrows" — is not expressible as a config
  rule at all: it is about the RELATIONSHIP between a `PhaseRun` row and
  the thing it borrows from, not about the contents of a configuration.
  That is why it needed its own binding invariant
  (`unbound_guard_taxonomy_error` + `Taxonomy`'s
  `only_one_active_taxonomy` database constraint) rather than another
  entry in `generation_config_errors`.
"""

import ast
import inspect
import pathlib

import pipeline.models

# Every function whose job is "return a validation message or None". The
# naming convention IS the enumeration: a new rule called `..._error` in
# `pipeline.models` is picked up here automatically, with no list to update.
_HELPER_SUFFIX = "_error"

# The functions that make up the ad-hoc (iterate-mode) validation path, as
# `(module path, dotted qualname)`. A helper counts as reachable if it is
# CALLED from inside any of them.
#
# `pipeline.models.generation_config_errors` is on the list because it IS
# the shared seam — a config rule wired into it reaches all three call
# sites (including both `_adhoc_generation_config` branches) by
# construction, so calling it there is genuinely calling it on the ad-hoc
# path.
_ADHOC_PATH = (
    ("pipeline/models.py", "generation_config_errors"),
    ("pipeline/serializers.py", "PhaseRunCreateSerializer.validate"),
    ("pipeline/execution_config.py", "_raise_config_errors"),
    ("pipeline/execution_config.py", "_adhoc_generation_config"),
    # A prompt test's own chosen inputs, resolved at create AND execution time.
    ("pipeline/execution_config.py", "chosen_generation_config"),
    ("pipeline/execution_config.py", "_adhoc_labelling_fewshots"),
    ("pipeline/execution_config.py", "_check_adhoc_taxonomy_binding"),
)

# Rules that genuinely have no ad-hoc equivalent, each with the reason. Empty
# today, and it should stay that way: before adding an entry, check that the
# rule really cannot bite on this path rather than merely being awkward to
# wire in. An unjustified entry here re-opens exactly the hole this file
# exists to close, so the reason is required reading for whoever reviews it.
_NOT_APPLICABLE_TO_ADHOC = {}

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _called_names(source_path, qualname):
    """Every function name CALLED anywhere inside `qualname`.

    Parsed from the AST rather than grepped from the source: this file's
    whole value is that it cannot be satisfied by a mention. Every module
    on the ad-hoc path names these helpers repeatedly in prose — their
    docstrings are where the reasoning lives — and a substring search would
    have counted each of those as proof, making the test vacuous in exactly
    the way six earlier tests in this codebase already were.
    """
    tree = ast.parse((_BACKEND_ROOT / source_path).read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        parents = [
            outer.name
            for outer in ast.walk(tree)
            if isinstance(outer, ast.ClassDef) and node in ast.walk(outer)
        ]
        if ".".join([*parents, node.name]) == qualname:
            target = node
            break
    assert target is not None, (
        f"{source_path} has no {qualname}. This file's `_ADHOC_PATH` is stale: "
        "if that function was renamed or moved, update the entry; if the ad-hoc "
        "validation path genuinely no longer has it, work out what replaced it "
        "and list that instead — never just delete the entry, or the rules it "
        "used to vouch for silently stop being checked."
    )
    called = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    return called


def _rule_helpers():
    return sorted(
        name
        for name, value in inspect.getmembers(pipeline.models, inspect.isfunction)
        if name.endswith(_HELPER_SUFFIX)
        and not name.startswith("_")
        and value.__module__ == pipeline.models.__name__
    )


def test_the_enumeration_actually_finds_the_rule_helpers():
    """A guard on the guard. If `_rule_helpers()` ever returned nothing —
    a renamed convention, a moved module, a typo in the suffix — the parity
    test below would pass over an empty list and certify nothing at all,
    which is precisely the vacuous-test failure mode this codebase has
    already been caught by six times. Seven helpers exist as of round 4;
    this asserts the enumeration is finding a plausible set of them, not
    the exact seven (a new rule must not have to edit this test)."""
    helpers = _rule_helpers()
    assert len(helpers) >= 7, helpers
    # A spot-check that the members really are the rule helpers and not, say,
    # every function in the module.
    assert "taxonomy_missing_rows_error" in helpers
    assert "unbound_guard_taxonomy_error" in helpers
    assert "generation_config_errors" not in helpers  # the checker, not a rule


def test_every_validation_rule_reaches_the_adhoc_path():
    """Fails when a `*_error` rule in `pipeline.models` is never called from
    the ad-hoc (iterate-mode) validation path.

    This is the test the three previous review rounds needed and did not
    have. Each of those rounds added a rule to `Experiment.clean()` that
    `POST /api/phase-runs/` never ran, and each time a variant-A run
    completed with false isolation evidence as a result.
    """
    reachable = set()
    for source_path, qualname in _ADHOC_PATH:
        reachable |= _called_names(source_path, qualname)

    missing = [
        helper
        for helper in _rule_helpers()
        if helper not in reachable and helper not in _NOT_APPLICABLE_TO_ADHOC
    ]
    assert not missing, (
        f"{missing} are validation rules in `pipeline.models` that the ad-hoc "
        "iterate-mode path (`POST /api/phase-runs/`) never runs.\n"
        "\n"
        "That path has no config snapshot and never calls "
        "`experiment.full_clean()`, so a rule reaches it only if something on "
        "it actually calls the rule. A rule that does not is not a style "
        "problem: it means a variant-A run can be created and executed in a "
        "state an orchestrated run would have been refused for, and complete "
        "with `leak_scan_ran=True` and a `guarded_by` taxonomy archived as "
        "proof. Three review rounds in a row shipped exactly that.\n"
        "\n"
        "Do ONE of these:\n"
        "  1. If the rule is CONFIG-shaped — it reads seed systems, few-shots, "
        "the target system, the generation toggles or the counts — call it "
        "from `pipeline.models.generation_config_errors` instead of from "
        "`Experiment.clean()` directly. That single move wires it into "
        "`Experiment.clean()`, `PhaseRunCreateSerializer.validate()` and both "
        "branches of `execution_config._adhoc_generation_config` at once, and "
        "is what this whole seam exists for.\n"
        "  2. If it is NOT config-shaped (it constrains a `PhaseRun`'s own "
        "field, or a relationship between a run and what it borrows — "
        "`unbound_guard_taxonomy_error` is the worked example), wire it "
        "explicitly into `PhaseRunCreateSerializer.validate()` for the "
        "create-time 400 AND into `execution_config` for the execution-time "
        "re-check. Both: the create-time check alone is not enough, because "
        "everything it inspected stays editable between creation and "
        "execution.\n"
        "  3. If the rule genuinely cannot apply to an ad-hoc run, add it to "
        "`_NOT_APPLICABLE_TO_ADHOC` in this file WITH the reason. Do that "
        "only after checking the rule really cannot bite there — 'it is "
        "awkward to wire in' is how the last three leaks happened.\n"
        "\n"
        "Then add a behavioural test that proves the rule bites on the ad-hoc "
        "path with zero provider calls: this test only proves the rule is "
        "reached, never that it is right."
    )
