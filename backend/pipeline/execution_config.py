"""Build a `PipelineRun.config` snapshot at launch, and read it back at
execution time — the pinned configuration, never the experiment's live,
editable fields.

The writer (`config_snapshot`) and reader
(`resolve_generation_config`/`resolve_labelling_fewshots`) live in the SAME
module deliberately: a review round found them split across
`orchestration.py` (writer) and here (reader), which meant any future
change to the snapshot's shape had to touch two files that must agree —
exactly the kind of split that produced this module's own worst defect (see
below). `orchestration.py` imports both `pipeline.generation` and
`pipeline.labelling`, so neither of those may import back from
`orchestration` — this module has no dependents of its own, so both are
free to import it, and it is free to live wherever makes the writer/reader
pairing clearest.

This is what makes `PipelineRun.config` (see its own docstring) actually
AUTHORITATIVE rather than decorative. Before this module existed, `launch()`
pinned a snapshot onto every run, but `generation._build_context` and
`labelling._fewshots` kept reading `seed_systems`/`fewshots`/`n_systems`/
`target_system` straight off the LIVE `Experiment` at execution time — so
editing an experiment while a run sat QUEUED made the archive contradict
itself: the run's own `inputs` would say one thing while its archived
`rendered_user_prompt` said another.

**Fail CLOSED, not open — the one property this module cannot get wrong.**
A review round proved a permissive reader is exactly as dangerous as no
reader at all: the first version of this module read every guard-relevant
snapshot field with `.get(key, default)`. A few-shot dict lacking
`input_resource_kind` — the exact shape a smaller bug in `config_snapshot`
itself had produced one round earlier — silently defaulted to `None`
("no file backing"), which switched OFF `prompts.rendering._fewshot_rows`'
variant-A taxonomy-resource guard. A TAXONOMY-kind resource's text rendered
into a Run A prompt and the run completed successfully: a taxonomy leak
into the unbiased variant, invisible in the results, the precise failure
the isolation guarantee's three layers of guards exist to prevent.
`prompts/rendering.py` refuses an unmarked or unrecognised context rather
than guessing (see `render()`'s own docstring); `validate_snapshot` below
holds every reader here to that same standard: every field a guard depends
on is REQUIRED, and a snapshot missing one — including a bare `{}`, which
used to fall through to the ad-hoc defaults as if this were a legitimate
iterate-mode run instead of a corrupted orchestrated one — is a malformed
snapshot and raises `MalformedSnapshot` rather than substituting anything.

An AD-HOC phase run (no `pipeline_run` — the iterate-mode path
`run_phase2_task`/`run_phase3_task` also serve) is the ONE case that
legitimately has no snapshot at all, checked first and explicitly, before
`validate_snapshot` ever runs: that is a distinct branch, not an incidental
side effect of a permissive default. Every orchestrated phase run (one WITH
a `pipeline_run`) is validated — there is no third, in-between case.

**Shape is not enough either.** A later round re-ran the same exploit with
`input_resource_kind` PRESENT but LYING — `""`, `"prose"`, wrong-case
`"TAXONOMY"`, `0`, `False` — and every one of them still rendered a
TAXONOMY-kind resource's text into a Run A prompt, because a shape-only
check (key exists) cannot tell "present and honest" from "present and
invented". `validate_snapshot` now checks values too: `input_resource_kind`/
`output_resource_kind` against the real `ResourceKind` choices, and every
other guard-relevant field against the type `config_snapshot` actually
produces. One value in that same family — a bare `None` — cannot be
rejected by a value check ALONE: it is indistinguishable from the
legitimate "no file backing this side" case that `config_snapshot` itself
produces routinely, so rejecting every `None` would break every ordinary
run with a text-backed few-shot.

**"Trust the writer" is not a guarantee — this plan has been bitten by it
twice.** The first version of this fix left that one `None` value closed
only by removing the interactive hand-edit route (`PipelineRunAdmin.config`
is read-only — see `pipeline/admin.py`) and otherwise trusting
`config_snapshot` to keep a file id and its recorded kind in lock-step. But
a writer bug that drops or nulls exactly `input_resource_kind` while the
file it came from is still attached is *precisely* the shape of the
original leak this docstring opens with — trusting the writer not to
repeat that bug is not the same as checking it did not. `config_snapshot`
now also snapshots `input_file_id`/`output_file_id`, and
`_require_file_kind_pairing` makes `validate_snapshot` check the two
fields against EACH OTHER: a non-null file id must carry a real
`ResourceKind`, and a null file id must carry `None`. A writer that lets
those two fields disagree — the exact defect class this module has now
been bitten by twice — fails closed here instead of being taken on faith.
"""

import copy
from types import SimpleNamespace

from corpus.models import ResourceKind, SystemDescription
from pipeline.models import (
    ALLOWED_PHASES,
    PHASE_LABELLING,
    Experiment,
    generated_target_system_error,
    generation_config_errors,
    unbound_guard_taxonomy_error,
)


class MalformedSnapshot(RuntimeError):
    """A `PipelineRun.config` that does not match the exact shape
    `config_snapshot` produces. Raised rather than guessed around: a field
    a guard depends on (chiefly `input_resource_kind`/`output_resource_kind`,
    which `prompts.rendering._fewshot_rows`' variant-A taxonomy-resource
    check reads) must be genuinely present, not defaulted — see this
    module's own docstring for the proven exploit a permissive reader
    allowed.
    """


class ReferencedRowMissing(RuntimeError):
    """A `PipelineRun.config` snapshot is well-shaped, but a row it
    references by id (its `target_system`) no longer exists — deleted
    after this run was launched. Distinct from `MalformedSnapshot`: the
    snapshot itself is fine, the world it points at has changed.
    """


class BorrowedExperimentContaminated(RuntimeError):
    """An ad-hoc run's borrowed `Experiment` (`PhaseRun.experiment` — see
    that field's own comment) currently has a GENERATED-origin system in its
    LIVE `seed_systems` — refused for the identical reason
    `Experiment.clean()`/`ExperimentSerializer._refuse_generated_seeds`
    refuse it for an orchestrated experiment: seeding a run with a prior
    run's own output contaminates the comparison.

    `Experiment.clean()` only runs when the EXPERIMENT ITSELF is validated
    (`full_clean()`, or the API's create/PATCH path) — an ad-hoc run reading
    that experiment's LIVE `seed_systems` at EXECUTION time never goes
    through either of those, and the experiment's own contents can change
    between when the ad-hoc run was created and when a worker actually
    executes it (that lag is the whole point of "live", not snapshotted —
    see `_adhoc_generation_config`'s own docstring). So this is checked
    again here, at the one place that actually reads the live list, rather
    than trusted from whatever `PhaseRunCreateSerializer.validate()` saw at
    creation time.
    """


class BorrowedExperimentMisconfigured(RuntimeError):
    """An ad-hoc run's borrowed `Experiment` currently fails one of
    `Experiment.clean()`'s two generation-toggle rules
    (`generation_toggles_off_error`/`missing_target_for_no_generation_error`,
    `pipeline.models`) — distinct from `BorrowedExperimentContaminated`
    above: this is a misconfiguration (nothing enabled to generate, or a
    targeting run with no target), not a GENERATED-origin leak route.
    `launch()` refuses both with `experiment.full_clean()`, before a single
    row exists; coordinator review round, finding 2 found the ad-hoc borrow
    path checked neither, so a probe against either PAID for one real
    provider call before failing on response validation — see those two
    functions' own docstrings.
    """


class UnboundGuardTaxonomy(RuntimeError):
    """The pinned `PhaseRun.taxonomy` is no longer the ONE this run is
    structurally entitled to. See `pipeline.models.unbound_guard_taxonomy_error`'s
    own docstring for the full reasoning (the decoy-taxonomy probe this
    closes) — this is its execution-time enforcement, re-checking the LIVE
    state rather than trusting whatever `PhaseRunCreateSerializer.validate()`
    derived/verified at creation time, for the identical reason
    `BorrowedExperimentContaminated` re-checks seeding/targeting live: both
    halves of the invariant can drift after creation (`Experiment.taxonomy`
    stays freely editable; which `Taxonomy` is "active" can change
    independently of any one run).
    """


def _check_adhoc_taxonomy_binding(phase_run, experiment):
    """Raise `UnboundGuardTaxonomy` if `phase_run`'s PINNED taxonomy has
    drifted from the one it is structurally entitled to. Called by BOTH
    `_adhoc_generation_config` (phase 2) and `_adhoc_labelling_fewshots`
    (phase 3) — taxonomy is not a phase-2-only concern the way
    `seed_systems`/`target_system`/the generation toggles are (coordinator
    review round, finding 5): phase 3 always renders it too
    (`build_phase3_context`), so it must be structurally tied to the
    borrowed experiment on that side exactly the same way.

    `experiment` is the caller's ALREADY freshly-fetched `Experiment` (or
    `None`) — not re-fetched here, to avoid a second query for what
    `_adhoc_generation_config`/`_adhoc_labelling_fewshots` already hold.

    `phase_run.taxonomy_id`, checked before touching `.taxonomy`: `taxonomy`
    is a required FK in real usage (every row `PhaseRunCreateSerializer`
    creates has one, derived or validated), but Django's FK descriptor
    raises `RelatedObjectDoesNotExist` — not a graceful `None` — for an
    UNSAVED instance with no id set at all, which only a synthetic test
    double builds (`PhaseRun()` with no `taxonomy=` at all). Reading the
    `_id` attname first avoids that unrelated crash and still reaches
    `unbound_guard_taxonomy_error`'s own "taxonomy is None" handling, which
    already fails closed on it correctly.
    """
    taxonomy = phase_run.taxonomy if phase_run.taxonomy_id else None
    error = unbound_guard_taxonomy_error(taxonomy, experiment)
    if error:
        raise UnboundGuardTaxonomy(error)


def _raise_config_errors(config):
    """Run `pipeline.models.generation_config_errors` over a resolved ad-hoc
    generation config and raise the matching exception for whatever it
    finds — nothing, and the config is clean.

    THE point of round 4's shared-checker seam, on the execution-time side:
    this function knows nothing about WHICH rules exist. Adding a rule to
    `generation_config_errors` (the one place `Experiment.clean()` reads
    them from too) reaches the ad-hoc path here automatically, instead of
    waiting for somebody to remember to hand-port it — which is what failed
    in each of the three preceding review rounds. `pipeline/tests/
    test_adhoc_rule_parity.py` fails the build if a future `*_error` helper
    reaches neither this seam nor the serializer's.

    The two exception TYPES stay distinct, because they mean genuinely
    different things to an operator and both are already asserted on by
    name: `BorrowedExperimentContaminated` for a GENERATED-origin seed or
    target (a leak route), `BorrowedExperimentMisconfigured` for a config
    that could never have produced anything (nothing enabled to generate, a
    targeting run with no target). Keyed off `generation_config_errors`'
    own stable field names.
    """
    for key, message in generation_config_errors(config).items():
        contaminated = key == "seed_systems" or (
            key == "target_system" and generated_target_system_error(config["target_system"])
        )
        if contaminated:
            raise BorrowedExperimentContaminated(message)
        raise BorrowedExperimentMisconfigured(message)


# Matches build_phase2_context's own defaults for an ad-hoc run that borrows
# NO Experiment at all (`PhaseRun.experiment_id` is null — see generation.py,
# pre-existing behaviour, unchanged). An ad-hoc run that DOES borrow one gets
# `_adhoc_generation_config`'s live-read branch below instead.
# `copy.deepcopy`d wherever this is handed out (never the module global
# itself) — `seed_systems`/`fewshots` are mutable lists, and every ad-hoc
# run sharing the SAME list objects is the identical footgun `launch()`
# already deep-copies `config_snapshot`'s own result to avoid.
_ADHOC_GENERATION_CONFIG = {
    "seed_systems": [],
    "fewshots": [],
    "generate_systems": True,
    "generate_scenarios": True,
    "n_systems": 3,
    "n_scenarios_per_system": 5,
    "target_system": None,
    "target_system_id": None,
}


def _adhoc_generation_config(phase_run):
    """The ad-hoc (iterate-mode) generation config for `phase_run`: the bare
    built-in defaults above if it borrows no `Experiment`, or that
    experiment's CURRENT, LIVE fields — plus this run's own
    `n_systems`/`n_scenarios_per_system` overrides, if set — if it does.

    Deliberately NOT pinned or snapshotted, the opposite of the orchestrated
    path's whole rule (`config_snapshot`/`validate_snapshot`: "never
    re-derive from the live Experiment, only from what was pinned at
    launch"). That rule exists to keep an ARCHIVED run's record truthful
    against an experiment that stays freely editable after it has run — but
    an ad-hoc run is never archived at all (`pipeline.export._phase_run_ids`
    scopes on `pipeline_run__experiment`, which a null `pipeline_run_id`
    never matches), so there is no record for a live read to contradict. The
    researcher's own framing is the point: "if I am in phase 2, I can rerun
    manually until I am satisfied" — rerunning against the experiment AS IT
    CURRENTLY STANDS, including an edit made five minutes ago, is what
    iterating actually means here.

    `Experiment.objects.get(...)`, not `phase_run.experiment` — the latter
    would cache onto `phase_run` the first time it is accessed and silently
    stay stale across a re-entrant call within the same process; a fresh
    query is what "live" actually requires. Safe to assume the row still
    exists: `PhaseRun.experiment` is `on_delete=PROTECT`, so a non-null
    `experiment_id` can never dangle (see that field's own comment).

    Every isolation layer downstream of this still applies unconditionally:
    `seed_systems`/`fewshots` are real `SystemDescription`/`FewShotExample`
    QuerySets, not snapshot-adapted stand-ins, so `prompts.rendering
    ._fewshot_rows`' variant-A taxonomy-resource guard reads their real
    `input_file`/`output_file` `.kind` exactly as it would for any other
    call — no adapter needed, unlike `system_from_snapshot`/
    `fewshot_from_snapshot` on the orchestrated path. The two guards the
    orchestrated path gets for free from `Experiment.clean()` (never
    reachable here, since nothing calls `full_clean()` on a borrowed,
    already-saved experiment, and `corpus.admin.SystemDescriptionAdmin` lets
    `origin` be hand-flipped afterwards) and this function cannot skip are
    contamination from a GENERATED `seed_system` OR `target_system` —
    checked explicitly below via the SAME `generated_seed_systems_error`/
    `generated_target_system_error` `Experiment.clean()` itself uses.
    `target_system` is the more dangerous of the two to miss: its `.body` is
    written into the variant-A prompt unconditionally (`build_phase2_context`
    doesn't gate it on `generate_systems`), and the render-time/final-request
    leak backstops CANNOT catch it — a generated system's prose is
    taxonomy-DERIVED, not a literal quote (see `generated_target_system_error`'s
    own docstring for the full reasoning; this was CVE-shaped enough that a
    coordinator review round caught a real version of this function missing
    it entirely, checking only seeds).

    Coordinator review round 3 found two more gaps in the same family:
    `phase_run.taxonomy` re-verified against the LIVE structural invariant
    (`_check_adhoc_taxonomy_binding` — the guard taxonomy must not be a
    free, driftable choice, the critical finding of that round), and
    `Experiment.clean()`'s two generation-toggle rules
    (`generation_toggles_off_error`/`missing_target_for_no_generation_error`)
    ported here for the identical "launch() refuses this with zero calls"
    reason `generated_seed_systems_error`/`generated_target_system_error`
    already were.
    """
    if phase_run.experiment_id is None:
        _check_adhoc_taxonomy_binding(phase_run, None)
        config = copy.deepcopy(_ADHOC_GENERATION_CONFIG)
        # The bare defaults are checked too, not waved through — see
        # `_raise_config_errors`. Cheap (they contain no rows), and it means
        # the ONE configuration in this codebase nobody validates because
        # "it is obviously fine" is validated like every other.
        _raise_config_errors(config)
        return config

    experiment = Experiment.objects.get(pk=phase_run.experiment_id)
    _check_adhoc_taxonomy_binding(phase_run, experiment)

    config = {
        "seed_systems": list(experiment.seed_systems.all()),
        "fewshots": list(
            experiment.fewshots.filter(phase=2, is_active=True).order_by("phase", "order", "name")
        ),
        "generate_systems": experiment.generate_systems,
        "generate_scenarios": experiment.generate_scenarios,
        "n_systems": phase_run.n_systems or experiment.n_systems,
        "n_scenarios_per_system": (
            phase_run.n_scenarios_per_system or experiment.n_scenarios_per_system
        ),
        "target_system": experiment.target_system,
        "target_system_id": experiment.target_system_id,
    }
    # Checked as a CONFIG, not as an experiment: the dict above is what this
    # run will actually render against (this run's own count overrides
    # included), so validating it is validating the real thing rather than
    # something adjacent to it.
    _raise_config_errors(config)
    return config


def _adhoc_labelling_fewshots(phase_run):
    """The ad-hoc (iterate-mode) phase-3 few-shots for `phase_run`: empty if
    it borrows no `Experiment`, or that experiment's CURRENT, active phase-3
    few-shots if it does — the phase-3 half of `_adhoc_generation_config`'s
    own "live, not snapshotted" reasoning; see that function's docstring for
    why re-reading live is correct here specifically.

    NO seed/target-system contamination check, and NO generation-toggle
    check, on this side — coordinator review round, finding 5 (deliberate
    OVER-enforcement fix): labelling never reads `seed_systems`/
    `target_system`/the generation toggles at all
    (`build_phase3_context` takes only `scenario`, `system_description`,
    `taxonomy`, `fewshots`), so checking them here would refuse a phase-3
    run for a reason its own render path can never actually reach — the
    same asymmetry `PhaseRunCreateSerializer.validate()` now also respects
    (those checks are scoped to `phase == 2` there too).

    `_check_adhoc_taxonomy_binding` IS still called, on both branches —
    unlike seed/target/toggles, taxonomy is not a phase-2-only concern:
    phase 3 always renders it too, so it must be structurally tied to the
    borrowed experiment here exactly the same way (see that function's own
    docstring).
    """
    if phase_run.experiment_id is None:
        _check_adhoc_taxonomy_binding(phase_run, None)
        return []
    experiment = Experiment.objects.get(pk=phase_run.experiment_id)
    _check_adhoc_taxonomy_binding(phase_run, experiment)
    return list(
        experiment.fewshots.filter(phase=3, is_active=True).order_by("phase", "order", "name")
    )


# The exact keys `config_snapshot` (below) produces at every nesting level,
# in the same order `config_snapshot`'s own dict/comprehension literals
# construct them (compare each tuple below against the matching return
# statement in `config_snapshot`/`_snapshot_system`/`_snapshot_scenario`).
# `validate_snapshot` needs only the SET of each (an EXACT match, not "at
# least these" — a field silently dropped from `config_snapshot`, a stale
# snapshot from a since-changed shape, or a hand-edited row all fail closed
# the same way), which is why these used to be plain `frozenset` literals.
# They are ORDERED tuples instead — with the frozenset each validation check
# actually needs derived from the tuple, not hand-duplicated alongside it —
# because `pipeline.export` also needs a canonical key order for this exact
# shape: `PipelineRun.config` is a `JSONField`, which Django maps to `jsonb`
# on PostgreSQL, and `jsonb` does not preserve object key insertion order on
# write (SQLite's JSON1 does). Reading `run.config` back and handing the
# dict straight to `json.dumps`/`csv.DictWriter` therefore produces a
# different key order — and different bytes — depending on which database
# produced the row, even for byte-for-byte the same snapshot. `pipeline
# .export._canonical_run_config` rebuilds the snapshot with every nesting
# level's keys in the order declared here instead of trusting whatever order
# the database handed back, so the archive's key order is a property of this
# declared shape, never of the backend. One set of tuples, used both ways,
# so the two can never silently drift apart the way two independently
# maintained lists could.
_TOP_LEVEL_KEY_ORDER = (
    "phases",
    "seed_systems",
    "fewshots",
    "target_system",
    "generate_systems",
    "generate_scenarios",
    "n_systems",
    "n_scenarios_per_system",
)
_SYSTEM_KEY_ORDER = ("id", "title", "slug", "body", "scenarios")
_SCENARIO_KEY_ORDER = (
    "scenario_id",
    "actor",
    "trigger",
    "standing_state",
    "asset",
    "threat",
    "outcome",
    "narrative",
    "intended_hcvs",
    "abox",
)
_FEWSHOT_KEY_ORDER = (
    "id",
    "name",
    "phase",
    "is_active",
    "input",
    "output",
    "input_file_id",
    "output_file_id",
    "input_resource_kind",
    "output_resource_kind",
)
_TOP_LEVEL_KEYS = frozenset(_TOP_LEVEL_KEY_ORDER)
_SYSTEM_KEYS = frozenset(_SYSTEM_KEY_ORDER)
_SCENARIO_KEYS = frozenset(_SCENARIO_KEY_ORDER)
_FEWSHOT_KEYS = frozenset(_FEWSHOT_KEY_ORDER)


def _snapshot_scenario(scenario):
    """One Phase-1 expert scenario, resolved — reused for a seed system and
    for `target_system` alike. Pinning ids alone is not enough: a seed
    `SystemDescription`/`Scenario` is editable after the fact (only a
    GENERATED row is read-only), so an id can still point at text that has
    since changed — this captures the text itself, at launch.
    """
    return {
        "scenario_id": scenario.scenario_id,
        **scenario.card,
        "narrative": scenario.narrative,
        "intended_hcvs": sorted(hcv.hcv_id for hcv in scenario.intended_hcvs.all()),
        "abox": scenario.abox,
    }


def _snapshot_system(system):
    """One `SystemDescription`, resolved, with its own Phase-1 expert pack.

    Scoped to `generation_run__isnull=True` — the expert seed pack, not
    anything a run of THIS (or any) experiment later generated against it —
    matching `export.py`'s old `_seed_scenarios` scoping.
    """
    return {
        "id": system.pk,
        "title": system.title,
        "slug": system.slug,
        "body": system.body,
        "scenarios": [
            _snapshot_scenario(scenario)
            for scenario in system.scenarios.filter(generation_run__isnull=True)
            .prefetch_related("intended_hcvs")
            .order_by("order", "scenario_id", "id")
        ],
    }


def config_snapshot(experiment):
    """Everything `launch` pins onto a `PipelineRun.config` at creation time.

    Seven fields live only on `Experiment` and used to be read LIVE at both
    export time and execution time: `seed_systems`, `fewshots`,
    `target_system`, `generate_systems`, `generate_scenarios`, `n_systems`,
    `n_scenarios_per_system`. `model`/`temperature`/`prompt_template`/
    `taxonomy` are already pinned on `PhaseRun`, and `variants` is
    recoverable from the phase runs' own `variant` values, so none of those
    need to be repeated here.

    Every few-shot is captured, active or not — `is_active` is recorded on
    each row rather than filtered out here, so `_snapshot_fewshots` below
    can reproduce the SAME `is_active=True` filter the live renderer used to
    apply, against the pinned data instead of a live query, rather than
    silently including a few-shot the model never actually saw.
    `input_resource_kind`/`output_resource_kind` record whether each side
    was backed by a TAXONOMY-kind `ResourceFile` — not just its resolved
    text — because `prompts.rendering._fewshot_rows`' variant-A guard
    refuses a few-shot that references the taxonomy transitively; losing
    that distinction here would silently widen Run A's taxonomy exposure
    the moment a pinned run (rather than the live experiment) rendered a
    prompt using one. `input_file_id`/`output_file_id` are captured
    alongside their respective kind — not read by any renderer, ONLY by
    `validate_snapshot`'s pairing check — precisely so that check does not
    have to trust this function to keep "a file is attached" and "its kind
    is recorded" in lock-step: a writer that ever drops or nulls a kind
    while the file id it came from survives (the exact shape of the
    original Run A leak, one review round earlier) now fails a check
    instead of being taken on faith. Every key this produces, at every
    nesting level, is checked exactly by `validate_snapshot` before a
    reader trusts it.

    Public (no leading underscore): `launch()` is its only production
    caller, but tests that construct a `PipelineRun` directly rather than
    through `launch()` (see `pipeline/tests/test_generation.py::make_run`)
    need the exact same snapshot a real launch would have produced, or a
    phase run "wired to its experiment" would silently execute against an
    empty `{}` config instead — which now REFUSES to execute at all rather
    than silently substituting ad-hoc defaults (see `validate_snapshot`).

    Computed ONCE per `launch()` call (not once per repetition it creates) —
    `launch` deep-copies this dict onto each `PipelineRun` it makes, so a
    batch of N repetitions costs the same fixed number of queries as one,
    not N times as many.
    """
    return {
        # Which phases `launch` creates rows for — the run's own record of what it did.
        "phases": sorted(experiment.phases),
        "seed_systems": [
            _snapshot_system(system) for system in experiment.seed_systems.order_by("title", "id")
        ],
        "fewshots": [
            {
                "id": fewshot.pk,
                "name": fewshot.name,
                "phase": fewshot.phase,
                "is_active": fewshot.is_active,
                "input": fewshot.resolved_input,
                "output": fewshot.resolved_output,
                "input_file_id": fewshot.input_file_id,
                "output_file_id": fewshot.output_file_id,
                "input_resource_kind": (fewshot.input_file.kind if fewshot.input_file_id else None),
                "output_resource_kind": (
                    fewshot.output_file.kind if fewshot.output_file_id else None
                ),
            }
            # select_related: without it, `.input_file.kind`/`.output_file
            # .kind` above cost two extra queries PER few-shot instead of
            # being folded into this one.
            for fewshot in experiment.fewshots.select_related("input_file", "output_file").order_by(
                "phase", "order", "name"
            )
        ],
        "target_system": (
            _snapshot_system(experiment.target_system) if experiment.target_system_id else None
        ),
        "generate_systems": experiment.generate_systems,
        "generate_scenarios": experiment.generate_scenarios,
        "n_systems": experiment.n_systems,
        "n_scenarios_per_system": experiment.n_scenarios_per_system,
    }


def _require_exact_keys(value, expected, what):
    """Fail CLOSED the instant `value` is not a dict with EXACTLY `expected`
    keys — no more (an unrecognised, possibly newer or foreign shape) and no
    fewer (a truncated or malformed one). This is the one primitive every
    check in `validate_snapshot` is built from; nothing downstream of a
    successful call reads this shape with `.get(key, default)` again — the
    presence of every key from here on is guaranteed, not hoped for.
    """
    if not isinstance(value, dict) or set(value) != expected:
        got = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise MalformedSnapshot(
            f"{what} does not match the shape config_snapshot produces — "
            f"expected exactly {sorted(expected)}, got {got}. Refusing to "
            "guess a default for a field a guard may depend on."
        )


def _require_str(value, what):
    """Every text field a rendered prompt actually interpolates
    (`title`/`slug`/`body`, the six card fields, `narrative`, a few-shot's
    `input`/`output`) is a Django `TextField`/`CharField` with
    `blank=True, default=""` — `config_snapshot` can therefore never
    legitimately produce `None` or a list for one of these. A round found
    `input=None` and `input=["a"]` both silently accepted by the old
    shape-only check (they satisfy "the key is present"); this closes that
    the same way `_require_exact_keys` closes a missing key."""
    if not isinstance(value, str):
        raise MalformedSnapshot(
            f"{what} must be a string, got {value!r} ({type(value).__name__}). "
            "Refusing to guess what a non-string value would even mean once "
            "interpolated into a rendered prompt."
        )


def _require_bool(value, what):
    if not isinstance(value, bool):
        raise MalformedSnapshot(
            f"{what} must be true or false, got {value!r} ({type(value).__name__})."
        )


def _require_int(value, what):
    # isinstance(True, int) is True in Python — bool is a subclass of int —
    # so this is checked first, or a stray `True`/`False` would silently
    # pass an int check meant for `n_systems`/`id`/etc.
    if isinstance(value, bool) or not isinstance(value, int):
        raise MalformedSnapshot(
            f"{what} must be an integer, got {value!r} ({type(value).__name__})."
        )


def _require_list(value, what):
    """Guards every `for x in config[...]` below from a bare `TypeError` on
    `None` — a round found `seed_systems`/`fewshots`/a system's `scenarios`
    set to `None` blew up with an unhelpful `TypeError` instead of the
    `MalformedSnapshot` every other malformed shape here raises. It still
    failed CLOSED (`run_phase2`'s broad except catches it), but the message
    read like an unrelated crash rather than a malformed snapshot."""
    if not isinstance(value, list):
        raise MalformedSnapshot(f"{what} must be a list, got {value!r} ({type(value).__name__}).")


def _require_resource_kind(value, what):
    """`None` — "no file backing this side of the few-shot" — is legitimate
    and common; see `config_snapshot`'s own
    `fewshot.input_file.kind if fewshot.input_file_id else None`. Anything
    ELSE must be one of the real `ResourceKind` choices: a round found
    `None`, `""`, `"prose"`, wrong-case `"TAXONOMY"`, `0` and `False` all
    silently accepted by shape-only validation, and every one of them reads
    as "no file" to `prompts.rendering._fewshot_rows`' variant-A
    taxonomy-resource guard — a TAXONOMY-kind resource's text rendered into
    a Run A prompt regardless of which lying value was stored, and the run
    completed. This closes five of those six values outright: `""`,
    `"prose"`, `"TAXONOMY"`, `0`, and `False` are none of them a real
    `ResourceKind` value AND none of them `None`, so all five now fail
    closed here.

    A lying bare `None` is NOT distinguishable from the legitimate case by
    this function ALONE — both mean "no file recorded" and `config_snapshot`
    itself can produce either for entirely legitimate reasons; a value check
    in isolation cannot tell them apart. See `_require_file_kind_pairing`
    below for how that gap is closed: not by trusting `config_snapshot` to
    always keep the two fields in lock-step (an earlier round proved that
    trust misplaced once already — a writer bug that dropped exactly this
    field caused the original leak), but by checking the recorded kind
    against the recorded file id it supposedly came from, INSIDE the same
    snapshot, so a writer bug that nulls or drops the kind while the file id
    survives fails closed instead of being silently accepted as "no file".
    `PipelineRunAdmin.config` being read-only (see `pipeline/admin.py`)
    still matters — it closes the interactive hand-edit route — but the
    pairing check is what makes the invariant verified rather than assumed.
    """
    if value is not None and value not in ResourceKind.values:
        raise MalformedSnapshot(
            f"{what} must be null or one of {sorted(ResourceKind.values)}, got {value!r}."
        )


def _require_file_kind_pairing(file_id, kind, what):
    """The invariant `_require_resource_kind` cannot check on its own: a
    file id and its recorded kind must AGREE. `config_snapshot` writes both
    `{side}_file_id` and `{side}_resource_kind` from the same
    `fewshot.{side}_file_id` in the same expression (see its own
    docstring) — so in an honest snapshot they can never disagree. Checking
    that here turns "the writer is correct" from an assumption into
    something this function actually verifies on every read, which matters
    because a writer that silently drops or nulls a kind while the file id
    it came from survives is EXACTLY the shape of the original Run A
    taxonomy leak, one review round earlier — a bug in `config_snapshot`
    itself, not a hand-edit.

    `file_id` is checked for `int`-or-`None` here too, not assumed: nothing
    upstream of this call has type-checked it yet.
    """
    if file_id is not None:
        _require_int(file_id, f"{what}_file_id")
        if kind is None:
            raise MalformedSnapshot(
                f"{what}_file_id={file_id!r} is set but {what}_resource_kind "
                "is null — a file is attached on this side of the few-shot "
                "but its kind was never recorded (or was dropped). Refusing "
                "to guess whether it is safe to treat as unguarded."
            )
    elif kind is not None:
        raise MalformedSnapshot(
            f"{what}_resource_kind={kind!r} is set but {what}_file_id is "
            "null — a kind was recorded with no file attached on this side "
            "of the few-shot. Refusing to guess which of the two is wrong."
        )


def _require_phases(value):
    """Exactly one of the shapes launch can pin — a list, in that order, of
    real ints (so `[3]`, `[3, 2]`, `(2, 3)` or `[True]` all fail closed)."""
    valid = isinstance(value, list) and all(type(p) is int for p in value)
    if not valid or value not in ALLOWED_PHASES:
        raise MalformedSnapshot(f"phases must be one of {list(ALLOWED_PHASES)}, got {value!r}.")


def _validate_scenario(scenario, what):
    _require_str(scenario["scenario_id"], f"{what}.scenario_id")
    for field in ("actor", "trigger", "standing_state", "asset", "threat", "outcome", "narrative"):
        _require_str(scenario[field], f"{what}.{field}")
    _require_list(scenario["intended_hcvs"], f"{what}.intended_hcvs")
    # `abox` is a nullable, shapeless JSONField ("reserved for a future
    # ontology-encoding phase" — see corpus.models.Scenario) — nothing reads
    # its type today, so nothing here constrains it.


def _validate_system(system, what):
    _require_exact_keys(system, _SYSTEM_KEYS, what)
    _require_int(system["id"], f"{what}.id")
    _require_str(system["title"], f"{what}.title")
    _require_str(system["slug"], f"{what}.slug")
    _require_str(system["body"], f"{what}.body")
    _require_list(system["scenarios"], f"{what}.scenarios")
    for index, scenario in enumerate(system["scenarios"]):
        _require_exact_keys(scenario, _SCENARIO_KEYS, f"{what}, scenario[{index}]")
        _validate_scenario(scenario, f"{what}, scenario[{index}]")


def _validate_fewshot(fewshot, what):
    _require_int(fewshot["id"], f"{what}.id")
    _require_str(fewshot["name"], f"{what}.name")
    if fewshot["phase"] not in (2, 3):
        raise MalformedSnapshot(f"{what}.phase must be 2 or 3, got {fewshot['phase']!r}.")
    _require_bool(fewshot["is_active"], f"{what}.is_active")
    _require_str(fewshot["input"], f"{what}.input")
    _require_str(fewshot["output"], f"{what}.output")
    _require_resource_kind(fewshot["input_resource_kind"], f"{what}.input_resource_kind")
    _require_resource_kind(fewshot["output_resource_kind"], f"{what}.output_resource_kind")
    _require_file_kind_pairing(
        fewshot["input_file_id"], fewshot["input_resource_kind"], f"{what}.input"
    )
    _require_file_kind_pairing(
        fewshot["output_file_id"], fewshot["output_resource_kind"], f"{what}.output"
    )


def validate_snapshot(config):
    """Fail CLOSED on anything that does not match `config_snapshot`'s exact
    shape AND values — mirroring `prompts.rendering`'s own
    refuse-rather-than-guess posture (`render()` refuses an
    unmarked/unrecognised context; this refuses an unmarked/unrecognised or
    dishonest snapshot the same way). See this module's own docstring for
    the proven exploit this closes: a permissive `.get(key, default)` reader
    let a malformed few-shot dict silently switch off the Run A
    taxonomy-resource guard instead of refusing to run.

    Checking KEY SHAPE alone (`_require_exact_keys`) is not enough on its
    own — a later round proved a snapshot field can be PRESENT but LYING
    (`input_resource_kind` set to a value that is not a real `ResourceKind`
    choice) and still switch the same guard off, because a permissive
    reader treats "present but not recognised" the same as "absent". Every
    value below is now checked against the type (or, for a resource kind,
    the exact enum) the field it guards actually needs.

    A bare `{}` (a `PipelineRun.config` that was never actually snapshotted
    — the field's own default, e.g. a run that predates this module, or any
    path that bypasses `launch()`) is checked FIRST and explicitly, with a
    message that says exactly that rather than reading like generic
    corruption — it is by far the most likely malformed shape to actually
    occur in practice, from an ordinary pre-migration row. This must never
    be read as "an ad-hoc run with no config to snapshot", which is a
    different, legitimate case checked separately and earlier, before this
    function is ever called, by
    `resolve_generation_config`/`resolve_labelling_fewshots`.
    """
    if config == {}:
        raise MalformedSnapshot(
            "PipelineRun.config is empty. This run predates the "
            "config-snapshot feature (or was created by a path that never "
            "called config_snapshot) — there is nothing pinned to execute "
            "against. This is not corruption: re-launch the experiment to "
            "get a run with a real snapshot."
        )
    _require_exact_keys(config, _TOP_LEVEL_KEYS, "PipelineRun.config")
    _require_phases(config["phases"])
    _require_list(config["seed_systems"], "seed_systems")
    for system in config["seed_systems"]:
        _validate_system(system, "a seed system")
    if config["target_system"] is not None:
        _validate_system(config["target_system"], "target_system")
    _require_list(config["fewshots"], "fewshots")
    for index, fewshot in enumerate(config["fewshots"]):
        _require_exact_keys(fewshot, _FEWSHOT_KEYS, f"fewshots[{index}]")
        _validate_fewshot(fewshot, f"fewshots[{index}]")
    _require_bool(config["generate_systems"], "generate_systems")
    _require_bool(config["generate_scenarios"], "generate_scenarios")
    _require_int(config["n_systems"], "n_systems")
    _require_int(config["n_scenarios_per_system"], "n_scenarios_per_system")


def system_from_snapshot(system_dict):
    """Adapt one (already-validated) snapshot system dict into the
    attribute shape `prompts.rendering._system_rows` expects (`.title`/
    `.body`/`.slug`) — that module is isolation-critical and deliberately
    left untouched by this; only the shape of what is handed TO it changes.
    """
    return SimpleNamespace(
        title=system_dict["title"], body=system_dict["body"], slug=system_dict["slug"]
    )


def fewshot_from_snapshot(fewshot_dict):
    """Adapt one (already-validated) snapshot few-shot dict into the
    attribute shape `prompts.rendering._fewshot_rows` expects, including
    `input_file`/`output_file` for its variant-A taxonomy-resource guard.
    Plain `[...]` indexing throughout, not `.get(key, default)`: by the time
    this runs, `validate_snapshot` has already confirmed every one of these
    keys is present, so a permissive default here would only hide a defect
    in the validator itself rather than guard against a real gap.
    """

    def _file_stub(kind):
        return SimpleNamespace(kind=kind) if kind is not None else None

    return SimpleNamespace(
        name=fewshot_dict["name"],
        resolved_input=fewshot_dict["input"],
        resolved_output=fewshot_dict["output"],
        input_file=_file_stub(fewshot_dict["input_resource_kind"]),
        output_file=_file_stub(fewshot_dict["output_resource_kind"]),
    )


def _snapshot_fewshots(config, phase):
    """This phase's ACTIVE few-shots, in the snapshot's own stored order.
    Assumes `config` has already passed `validate_snapshot`.

    Mirrors `experiment.fewshots.filter(phase=phase, is_active=True)`
    exactly — `is_active` is recorded on every snapshotted row (even an
    inactive one; see `config_snapshot`), specifically so this can
    reproduce that live filter against the PINNED data instead of a live
    query. The snapshot's own list order already matches
    `FewShotExample.Meta.ordering = ["phase", "order", "name"]` (that is
    the order `config_snapshot` queried in), so filtering in place — not
    re-sorting — preserves it.
    """
    return [
        fewshot_from_snapshot(f)
        for f in config["fewshots"]
        if f["phase"] == phase and f["is_active"]
    ]


def resolve_generation_config(phase_run):
    """The configuration Phase 2 renders ITS PROMPT against, and validates
    and persists the model's RESPONSE against, for `phase_run` — the run's
    pinned snapshot when one exists, `_adhoc_generation_config`'s bare
    defaults-or-borrowed-experiment resolution when it does not (see that
    function's own docstring for what "ad-hoc" now covers). Deliberately the
    ONE place both concerns read from (`generation
    ._build_context` for the prompt, `generation._persist_error`/`_persist`
    for the response), so a prompt and the validation of its own response
    can never disagree with each other — nor with what a re-read of this
    same run's export would show.

    Raises `MalformedSnapshot` for an orchestrated run (one WITH a
    `pipeline_run`) whose config does not validate — including a bare `{}`
    — rather than silently falling back to the ad-hoc defaults, which used
    to read as "an ordinary iterate-mode run" instead of "a corrupted
    orchestrated one". `run_phase2` catches this (and
    `ReferencedRowMissing` below) and fails the phase run cleanly — see its
    own docstring for why that call is made INSIDE its try/except now.

    `target_system_id` is carried alongside the full `target_system` display
    dict: rendering needs the resolved title/body/slug (already in the
    snapshot), but PERSISTING a bare generated scenario needs to attach it
    to a REAL `SystemDescription` row via FK. Checked for existence HERE,
    before any provider call is made, rather than only later inside
    `_persist` when the response comes back — a `target_system` deleted
    between launch and execution is worth failing on before spending a call
    on it, with a message that names what is actually missing rather than
    a bare `SystemDescription.DoesNotExist`. `_persist` still re-resolves
    the row itself when it actually needs to write to it — this check does
    not hand the row across the provider call, just confirms it exists now.
    """
    if not phase_run.pipeline_run_id:
        return _adhoc_generation_config(phase_run)
    return generation_config_from_snapshot(phase_run.pipeline_run.config)


def generation_config_from_snapshot(snapshot):
    """The generation config a `PipelineRun.config` snapshot pins — validated
    fail-closed, its target system confirmed to still exist — in the shape
    `pipeline.generation` renders against. `resolve_generation_config`'s
    orchestrated branch, as its own function.

    Split out (task 10.4) so `pipeline.orchestration.launch` can dry-render
    each variant's phase-2 template against the EXACT snapshot it is about
    to pin, before any `PipelineRun` exists to read one off. One reader for
    both callers, so the launch-time dry run and the execution-time render
    cannot disagree about what a snapshot means.
    """
    validate_snapshot(snapshot)
    target = snapshot["target_system"]
    target_system_id = target["id"] if target else None
    if (
        target_system_id is not None
        and not SystemDescription.objects.filter(pk=target_system_id).exists()
    ):
        raise ReferencedRowMissing(
            f"target_system (id={target_system_id}, pinned as {target['title']!r} "
            "at launch) no longer exists — it may have been deleted after this "
            "run was launched."
        )
    return {
        "seed_systems": [system_from_snapshot(s) for s in snapshot["seed_systems"]],
        "fewshots": _snapshot_fewshots(snapshot, phase=2),
        "generate_systems": snapshot["generate_systems"],
        "generate_scenarios": snapshot["generate_scenarios"],
        "n_systems": snapshot["n_systems"],
        "n_scenarios_per_system": snapshot["n_scenarios_per_system"],
        "target_system": system_from_snapshot(target) if target else None,
        "target_system_id": target_system_id,
    }


def resolve_labelling_fewshots(phase_run):
    """Phase 3's active few-shots for `phase_run`, from its pinned
    snapshot — or `_adhoc_labelling_fewshots`' bare-or-borrowed-experiment
    resolution for an ad-hoc run (no `pipeline_run`; see that function's own
    docstring). Raises `MalformedSnapshot` the same way
    `resolve_generation_config` does for an orchestrated run whose config
    does not validate.
    """
    if not phase_run.pipeline_run_id:
        return _adhoc_labelling_fewshots(phase_run)
    snapshot = phase_run.pipeline_run.config
    validate_snapshot(snapshot)
    if PHASE_LABELLING not in snapshot["phases"]:
        raise MalformedSnapshot(
            f"This run pinned phases {snapshot['phases']!r}, so it has no labelling; "
            "refusing to label under a record that says phase 3 never ran."
        )
    return _snapshot_fewshots(snapshot, phase=3)
