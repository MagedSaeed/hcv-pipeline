from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, Sum

from prompts.rendering import (
    VARIANT_GUIDED,
    VARIANT_UNBIASED,
    TaxonomyLeak,
    _fewshot_rows,
    unscannable_hcv_rows,
)
from taxonomy.models import Taxonomy

VARIANTS = (VARIANT_UNBIASED, VARIANT_GUIDED)
PHASE_GENERATION = 2
PHASE_LABELLING = 3
PHASE_LABELLING_ONLY = [PHASE_LABELLING]
# Generate only, generate then label that output, or label chosen corpus scenarios.
ALLOWED_PHASES = ([PHASE_GENERATION], [PHASE_GENERATION, PHASE_LABELLING], PHASE_LABELLING_ONLY)


def default_phases():
    return [PHASE_GENERATION, PHASE_LABELLING]


def _progress_dict(done, total, failed):
    """The one shape every level of `progress` returns — frontend and CLI agree.

    `percent` is 0 when `total` is 0 rather than raising: a run that has not
    been sized yet (no children counted) is not a divide-by-zero, it is 0%.
    """
    percent = int(done / total * 100) if total else 0
    return {"done": done, "total": total, "failed": failed, "percent": percent}


def _sum_progress(progresses):
    """Combine several `progress` dicts into one, for the levels that roll up."""
    done = total = failed = 0
    for entry in progresses:
        done += entry["done"]
        total += entry["total"]
        failed += entry["failed"]
    return _progress_dict(done, total, failed)


def template_phase_mismatch_error(template, expected_phase, field_label):
    """A `PromptTemplate` pinned to the wrong phase — or `None`, when there is
    nothing to check.

    Shared by `Experiment.clean()` (checking `phase2_prompt_a`/
    `phase2_prompt_b`/`phase3_prompt` against 2/2/3) and
    `pipeline.serializers.PhaseRunCreateSerializer` (an
    ad-hoc iterate-mode run's own `prompt_template` against its `phase`) —
    the same mistake, "a phase-3 labelling template pinned as the generation
    prompt (or the reverse)", validated fine on both paths and only surfaced
    later as a baffling render or a wasted run. One message-building rule so
    the two paths cannot drift into checking (or wording) it differently.
    """
    if template.phase == expected_phase:
        return None
    return (
        f"{template} is a phase {template.phase} template; {field_label} must "
        f"be a phase {expected_phase} template."
    )


def taxonomy_missing_rows_error(taxonomy):
    """A taxonomy variant A's leak backstop cannot actually scan against —
    or `None`, when every one of its rows carries scannable content.

    Shared by `Experiment.clean()` and `PhaseRunCreateSerializer` for the
    same reason as `template_phase_mismatch_error` above: variant A's leak
    backstop (`prompts.rendering.assert_no_taxonomy_leak`) scans the
    rendered request against this taxonomy's own rows. With none, it cannot
    run at all — the run would complete with `leak_scan_ran=False` and only
    a swallowed warning, which is the isolation guarantee silently switched
    off. Cheaper, and louder, to refuse here before a run is ever created.

    **The precondition is SCANNABLE rows, not row objects** — round 4, leak
    1, and the deeper of that leak's two fixes. `.exists()` asked only
    whether the taxonomy had rows AT ALL, which is not the property the
    backstop needs: `assert_no_taxonomy_leak` skips every falsy
    `hcv_id`/`name`/`definition` it walks past, so a row blank in all three
    contributes NOTHING to the scan while still satisfying `.exists()`. A
    taxonomy made of such rows therefore scanned for nothing, found
    nothing, and returned True — archiving `leak_scan_ran=True,
    guarded_by=<that taxonomy>` as proof over a variant-A prompt with real
    HCV text sitting in it. That is strictly worse than a zero-row
    taxonomy, which at least records `leak_scan_ran=False`, and it must be
    refused the same way.

    EVERY row must be scannable, not merely one of them: a 21-row taxonomy
    with 20 rows blanked would still pass a "has at least one scannable
    row" test while its guard silently covered 1/21 of the list — the same
    quiet narrowing, just partial. No legitimate route produces a
    content-free row (`import_taxonomy` refuses any JSON row missing
    `hcv_id` or `name`; `taxonomy.admin` no longer offers an add form at
    all), so this refuses only genuinely broken data.

    Whitespace is stripped before the emptiness test: a row whose `hcv_id`
    is `"   "` is content-free in every sense that matters here, and would
    otherwise be scanned for as a literal substring that matches nearly any
    prompt — a guard that fails on everything rather than one that guards.
    """
    rows = list(taxonomy.hcvs.all())
    if not rows:
        return (
            f"{taxonomy} has no HCV rows, so variant A's taxonomy-leak "
            "backstop cannot run. Import the taxonomy before running against it."
        )
    unscannable = unscannable_hcv_rows(rows)
    if unscannable:
        return (
            f"{taxonomy} has {len(unscannable)} of {len(rows)} HCV rows with no "
            "hcv_id, name or definition, so variant A's taxonomy-leak backstop "
            "would scan for nothing and still report success. A content-free row "
            "cannot guard anything — re-import the taxonomy before running "
            "against it."
        )
    return None


def generated_seed_systems_error(seed_systems):
    """Which of these `SystemDescription`s were GENERATED by a previous run
    — or `None`, when none were.

    Shared by `Experiment.clean()`, `pipeline.serializers.ExperimentSerializer
    ._refuse_generated_seeds` (the create-time hole `clean()` alone cannot
    close for an unsaved instance) and `PhaseRunCreateSerializer` (an ad-hoc
    run borrowing an experiment's LIVE `seed_systems` — see
    `pipeline.execution_config._adhoc_generation_config`, which re-checks
    this again at execution time for the same reason): a generated system is
    a previous run's OUTPUT, and seeding a run with it — however that
    seeding actually happens — contaminates the comparison the same way
    regardless of which of the three call sites caught it.
    """
    generated = [s.title for s in seed_systems if s.origin == "generated"]
    if not generated:
        return None
    return (
        f"{generated} were generated by a previous run. Seeding an "
        "experiment with its own output contaminates the comparison."
    )


def chosen_fewshots_error(fewshots, variant):
    """Why a prompt test's own chosen few-shots cannot be used, or `None`.

    An experiment only ever renders its active phase-2 few-shots, and variant A
    refuses one backed by a taxonomy-kind file (`_fewshot_rows`, reused as-is).
    A direct choice is refused instead of silently filtered, so a test never
    quietly runs with fewer examples than were chosen.
    """
    unusable = [f.name for f in fewshots if f.phase != PHASE_GENERATION or not f.is_active]
    if unusable:
        return (
            f"{unusable} are not active phase 2 few-shot examples. Choose from the "
            "active phase 2 examples only."
        )
    if variant == VARIANT_UNBIASED:
        try:
            _fewshot_rows(fewshots, variant=variant)
        except TaxonomyLeak as error:
            return str(error)
    return None


def generated_target_system_error(target_system):
    """Is `target_system` GENERATED-origin — or `None`, when it isn't (or
    there isn't one at all).

    The SECOND of two contamination routes `Experiment.clean()` refuses —
    seeding (`generated_seed_systems_error`, above) is the first. Coordinator
    review round, finding 1 (CRITICAL): the first version of the ad-hoc
    "borrow an experiment" feature ported the seed check but missed this
    one, so a generated-origin `target_system` reached a variant-A prompt
    with the run reporting COMPLETED and `leak_scan_ran=True` — a
    contaminated run that looks perfectly healthy. Targeting is WORSE than
    seeding, not merely another instance of it: `target_system.body` is
    written into the variant-A prompt directly (`build_phase2_context`'s
    `target_system` key is unconditional, independent of
    `generate_systems`), and the render-time/final-request leak backstops
    CANNOT catch it — a generated system's prose is taxonomy-DERIVED, not a
    literal quote, so it trips no `hcv_id` match and stays under the
    distinct-name/definition threshold. The origin check here is the ONLY
    thing that closes this route; scanning text can't.

    Shared by `Experiment.clean()` and, for the ad-hoc borrow path,
    `PhaseRunCreateSerializer` (create-time) and
    `pipeline.execution_config._adhoc_generation_config` (execution-time,
    re-checked live for the same reason `generated_seed_systems_error` is:
    `Experiment.clean()` never runs on an already-saved experiment being
    borrowed, and `corpus.admin.SystemDescriptionAdmin` lets `origin` be
    hand-flipped on a system AFTER it was validly set as a target).
    """
    if target_system is None or target_system.origin != "generated":
        return None
    return (
        f"{target_system.title!r} was generated by a previous run. "
        "Targeting an experiment at its own output contaminates the "
        "comparison — its body is written into the variant A prompt."
    )


def generation_toggles_off_error(generate_systems, generate_scenarios):
    """Both phase-2 generation toggles off — nothing left to generate — or
    `None`, when at least one is on.

    Shared by `Experiment.clean()` and, for the ad-hoc borrow path,
    `PhaseRunCreateSerializer` (create-time) and
    `pipeline.execution_config._adhoc_generation_config` (execution-time):
    coordinator review round, finding 2 — `launch()` refuses this with
    ZERO calls made (`experiment.full_clean()` runs before a single
    `PipelineRun`/`PhaseRun` exists); the ad-hoc borrow path checked
    neither `taxonomy_missing_rows_error` nor this rule, so a probe against
    it returned 202 and SPENT one real provider call before failing on
    response validation (`generate_systems is False ... but the response
    included system_descriptions`) — a wasted call for a configuration
    that could never have validated, exactly the "no HTTP call is made"
    guarantee this project's other guards all hold to.
    """
    if not generate_systems and not generate_scenarios:
        return "Enable at least one of generate_systems or generate_scenarios."
    return None


def missing_target_for_no_generation_error(generate_systems, target_system_id):
    """`generate_systems` is off but no `target_system` is set — scenarios
    need a system to be written against — or `None`, when one is set (or
    `generate_systems` is on and none is needed).

    Shared the same way `generation_toggles_off_error` (immediately above)
    is, and for the identical reason: a real, wasted provider call on the
    ad-hoc borrow path before this was ported.
    """
    if not generate_systems and target_system_id is None:
        return (
            "Set target_system when generate_systems is off — scenarios need "
            "a system to be written against."
        )
    return None


# The exact attribute/key names a "generation config" carries, whatever
# object is actually carrying it. `Experiment.generation_config` (below)
# produces one; so does `pipeline.execution_config._ADHOC_GENERATION_CONFIG`
# and the live dict `_adhoc_generation_config` builds from a borrowed
# experiment. Kept as a named tuple so the meta-test
# (`pipeline/tests/test_adhoc_rule_parity.py`) and a human reader can both
# see, in one place, what a config is expected to expose.
GENERATION_CONFIG_FIELDS = (
    "seed_systems",
    "fewshots",
    "target_system",
    "target_system_id",
    "generate_systems",
    "generate_scenarios",
    "n_systems",
    "n_scenarios_per_system",
)


def _config_value(cfg, name):
    """Read one config field off a MAPPING or an OBJECT, indifferently.

    "Duck-typed" is the point: the three call sites carry the same
    configuration in three different containers (an `Experiment`-derived
    dict, a module-level defaults dict, a live dict built from a borrowed
    experiment), and a checker that insisted on one of them would just
    become another thing each new call site has to adapt to by hand —
    which is precisely how the rules drifted apart three times.
    """
    if isinstance(cfg, Mapping):
        return cfg.get(name)
    return getattr(cfg, name, None)


def _config_rows(cfg, name):
    """`seed_systems`/`fewshots` as a real list, whether the config holds a
    list already or a related manager."""
    value = _config_value(cfg, name)
    if value is None:
        return []
    if hasattr(value, "all"):
        value = value.all()
    return list(value)


def generation_config_errors(cfg):
    """Every config-shaped rule a phase-2 generation configuration must
    satisfy, as an ordered `{field: message}` dict — empty when it is clean.

    **This is the seam that stops the class of bug this function was born
    from.** Round 4 was the FOURTH round in which a rule living in
    `Experiment.clean()` turned out never to have been hand-ported to the
    ad-hoc (`POST /api/phase-runs/`) path — iterate mode has no config
    snapshot and bypasses `launch()`'s `full_clean()`, so every rule had to
    be copied across by somebody remembering to, and three times running
    somebody did not. Wholesale `experiment.full_clean()` from the ad-hoc
    path was assessed and rejected as the wrong seam: it would have caught
    only one of the two original leaks, covers nothing at all for the
    no-experiment half of the surface (there IS no experiment to clean),
    and wrongly refuses four legitimate cases — the `variants` rules and
    both prompt-phase rules, none of which an ad-hoc run's own
    `phase`/`prompt_template`/`variant` fields have any equivalent of.

    So the CONFIG-shaped rules move here instead, and all three call sites
    call this one function:

    - `Experiment.clean()` — via `Experiment.generation_config`;
    - `pipeline.serializers.PhaseRunCreateSerializer.validate()` — over the
      BORROWED experiment's live config, create-time, for a fast 400;
    - `pipeline.execution_config._adhoc_generation_config` — over BOTH its
      branches, execution-time: the live borrowed config, and
      `_ADHOC_GENERATION_CONFIG`, the bare built-in defaults an
      experiment-less ad-hoc run gets. Checking the defaults themselves is
      not ceremony: they are a configuration like any other, and a future
      edit turning `generate_systems` off there would otherwise sail
      through every guard in the codebase.

    Rules genuinely specific to an `Experiment` stay in `Experiment.clean()`
    — the `variants` list (an ad-hoc run has a single `variant` field, not a
    list, and its own serializer rule for it), the
    `phase2_prompt_a`/`phase2_prompt_b`/`phase3_prompt` phase pairing (an
    ad-hoc run has ONE `prompt_template`, checked against its own `phase` by
    `template_phase_mismatch_error` directly), and "a template is set for
    every variant this experiment actually runs" (an ad-hoc run's
    `prompt_template` is never derived from `phase2_prompt_a`/`_b` at all,
    so there is nothing on that path for this rule to reach). Nothing is
    duplicated here that those already cover.

    Keys are `Experiment`'s own field names so `clean()` can raise them
    against the right form fields unchanged; callers with no such fields
    (the serializer, the execution-time resolvers) flatten
    `.values()` instead. Ordering is stable — seeds, then target, then
    toggles — so a caller rendering them as a list gets the same order
    every time.
    """
    errors = {}
    generate_systems = bool(_config_value(cfg, "generate_systems"))
    generate_scenarios = bool(_config_value(cfg, "generate_scenarios"))

    seed_error = generated_seed_systems_error(_config_rows(cfg, "seed_systems"))
    if seed_error:
        errors["seed_systems"] = seed_error

    target_error = generated_target_system_error(_config_value(cfg, "target_system"))
    if target_error:
        errors["target_system"] = target_error

    toggle_error = generation_toggles_off_error(generate_systems, generate_scenarios)
    if toggle_error:
        errors["generate_systems"] = toggle_error

    # LAST, and via `setdefault`, so the insertion order below reproduces
    # exactly the precedence the three call sites had before they shared
    # this function: seeds, then a GENERATED target, then the toggles, then
    # a MISSING target. Callers that surface only the first error
    # (`_raise_config_errors`) or render them as an ordered list (the
    # serializer's `experiment` key) therefore say the same thing they said
    # before the extraction, which is what keeps this a refactor rather than
    # a behaviour change. `setdefault` because both target rules key on
    # `target_system` and only one of them can ever fire — a GENERATED
    # target requires `target_system_id` set, a MISSING one requires it
    # null — so this never overwrites a real error, it only preserves order.
    target_needed_error = missing_target_for_no_generation_error(
        generate_systems, _config_value(cfg, "target_system_id")
    )
    if target_needed_error:
        errors.setdefault("target_system", target_needed_error)
    return errors


def unbound_guard_taxonomy_error(taxonomy, experiment):
    """Is `taxonomy` the ONE this run is structurally entitled to — the
    borrowed experiment's own, or (with no experiment) whichever taxonomy
    is currently active — or `None`, when it is.

    Coordinator review round, finding 1 (CRITICAL): `launch()` pins
    `taxonomy=experiment.taxonomy` onto EVERY `PhaseRun` it creates — the
    guard taxonomy variant A's leak backstop scans against is structurally
    the experiment's own, never a free choice. The ad-hoc borrow path let
    the requester choose ANY taxonomy with at least one HCV row
    (`taxonomy_missing_rows_error` alone), including a 1-row DECOY sharing
    no content with the experiment's real one — the render-time/final-
    request scans then genuinely ran (`leak_scan_ran=True`) and genuinely
    found nothing, because they compared the request against the WRONG
    list. The archived evidence was not merely absent, it was FALSE:
    `LLMCallAdmin`'s own comment calls `leak_scan_ran` "the archived PROOF
    that the taxonomy-isolation checks ran" — this is the one guard-related
    field where a request-scoped free choice is not survivable, because
    every downstream consumer (the export, the admin, an operator) trusts
    it as proof, not merely as a recorded intent.

    `PhaseRunCreateSerializer.validate()` DERIVES `taxonomy` outright from
    `experiment.taxonomy` when borrowing (a field that cannot be wrong
    beats one that is checked) rather than calling this function at all in
    that branch — this function is for the two cases where there is
    something to actually VALIDATE rather than simply assign: create-time
    with NO experiment (the submitted `taxonomy` against
    `Taxonomy.active()`), and EXECUTION-time re-verification in both
    branches (`pipeline.execution_config._adhoc_generation_config`/
    `_adhoc_labelling_fewshots`) — the pinned `PhaseRun.taxonomy` re-checked
    against the LIVE state, because both halves of the invariant can drift
    after creation: `Experiment.taxonomy` stays freely editable (nothing
    about being borrowed freezes it), and which `Taxonomy` is "active" can
    change independently of any one run.
    """
    if experiment is not None:
        if taxonomy is None or taxonomy.pk != experiment.taxonomy_id:
            return (
                f"taxonomy must be {experiment.taxonomy!r} — the borrowed "
                f"experiment {experiment!r}'s own taxonomy — not a free "
                "choice. The guard taxonomy variant A's leak backstop scans "
                "against must be structurally tied to what the experiment "
                "actually uses, never chosen independently."
            )
        return None
    active = Taxonomy.active()
    if active is None:
        return (
            "A prompt test without a borrowed experiment uses the active taxonomy, "
            "but no taxonomy is active. Activate one, or borrow an experiment."
        )
    if taxonomy is None or taxonomy.pk != active.pk:
        return (
            f"A prompt test without a borrowed experiment uses the active taxonomy "
            f"({active}). Activate a different one first if you meant another, or "
            "borrow an experiment."
        )
    return None


def _phases_error(phases):
    """Inline `Experiment.clean()` rule (not a `*_error` helper): an ad-hoc run
    has a single `phase` field and nothing equivalent to check."""
    if not isinstance(phases, list) or not phases:
        return "Choose the phases to run: [2], [2, 3] or [3]."
    if any(isinstance(p, bool) or p not in (PHASE_GENERATION, PHASE_LABELLING) for p in phases):
        return f"Unknown phase(s) in {phases!r}; expected 2 and/or 3."
    if len(set(phases)) != len(phases):
        return f"Duplicate phase(s) in {phases!r}; each phase may appear at most once."
    return None


def label_scenarios_error(scenarios):
    """The chosen scenarios of a phase-3-only experiment, or `None` when valid.

    Scratch rows are refused because exports leave prompt-test output out.
    """
    if not scenarios:
        return "Choose at least one scenario to label: this experiment runs phase 3 only."
    scratch = sorted(scenario.scenario_id for scenario in scenarios if scenario.is_scratch)
    if scratch:
        return (
            "Scenarios from prompt test runs can't be labelled in an experiment: "
            f"{', '.join(scratch)}."
        )
    return None


def labelled_elsewhere_error(scenarios, own_pipeline_run_ids=()):
    """Refuse a delete that would cascade away another repetition's labels, or
    `None`. A phase-3-only run labels scenarios it does not own, so deleting
    them (or whatever generated them) would silently shrink its results."""
    names = sorted(
        set(
            Label.objects.filter(scenario__in=scenarios, phase_run__pipeline_run__isnull=False)
            .exclude(phase_run__pipeline_run_id__in=list(own_pipeline_run_ids))
            .values_list("phase_run__pipeline_run__experiment__name", flat=True)
        )
    )
    if not names:
        return None
    return (
        f"Experiment(s) {', '.join(names)} labelled these scenarios; deleting them would "
        "remove those labels. Delete those experiments first."
    )


class Experiment(models.Model):
    """A pinned configuration. Everything a run needs is frozen here.

    Models, prompt versions and the taxonomy version are all recorded so a
    result can be traced back to the exact wording and label list that produced
    it — editing a template later creates a new version and cannot rewrite this.
    """

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True, default="")
    taxonomy = models.ForeignKey("taxonomy.Taxonomy", on_delete=models.PROTECT)

    # Blank for a phase-3-only experiment; `clean()` requires it when phase 2 runs.
    phase2_model = models.CharField(max_length=200, blank=True, default="")
    phase2_temperature = models.FloatField(default=1.0)
    # Two fully independent templates, one per variant — NOT one shared FK
    # branching on `{% if taxonomy is defined %}` (the old shape; see
    # `prompts/rendering.py`'s module docstring for why that idiom existed
    # and why it is no longer the documented pattern for phase 2). User
    # decision, 2026-09-08: the researcher was offered a drift-warning
    # variant and a shared-core variant and explicitly chose full
    # independence — no coupling, no diff, no "these two templates should
    # resemble each other" validation. Variant A's template simply never
    # references `taxonomy`; variant B's always does. `null=True` (like
    # `target_system` below): which of the two is actually REQUIRED depends
    # on `variants` — an experiment that only ever runs "B" has no need of
    # `phase2_prompt_a` — so DB-level nullability is correct and `clean()`
    # enforces "a template for every variant this experiment actually runs"
    # instead (see `clean()`'s own comment). Phase 3 is UNCHANGED — one
    # `phase3_prompt` still serves both variants, because phase 3 always
    # receives the taxonomy regardless of which variant it is labelling.
    phase2_prompt_a = models.ForeignKey(
        "prompts.PromptTemplate",
        null=True,
        blank=True,
        related_name="phase2_experiments_a",
        on_delete=models.PROTECT,
    )
    phase2_prompt_b = models.ForeignKey(
        "prompts.PromptTemplate",
        null=True,
        blank=True,
        related_name="phase2_experiments_b",
        on_delete=models.PROTECT,
    )
    # Blank/null allowed because a generation-only experiment (`phases=[2]`)
    # has no labelling; `clean()` requires both whenever phase 3 is enabled.
    phase3_model = models.CharField(max_length=200, blank=True, default="")
    phase3_temperature = models.FloatField(default=0.0)
    phase3_prompt = models.ForeignKey(
        "prompts.PromptTemplate",
        null=True,
        blank=True,
        related_name="phase3_experiments",
        on_delete=models.PROTECT,
    )
    # [2] (generate only), [2, 3] (generate, then label that output) or [3]
    # (label `label_scenarios`).
    phases = models.JSONField(default=default_phases)

    seed_systems = models.ManyToManyField("corpus.SystemDescription", blank=True)
    fewshots = models.ManyToManyField("corpus.FewShotExample", blank=True)
    # What a phase-3-only experiment labels; ignored for other phase lists.
    label_scenarios = models.ManyToManyField(
        "corpus.Scenario", blank=True, related_name="labelled_by_experiments"
    )

    generate_systems = models.BooleanField(default=True)
    generate_scenarios = models.BooleanField(default=True)
    target_system = models.ForeignKey(
        "corpus.SystemDescription",
        null=True,
        blank=True,
        related_name="targeted_by",
        on_delete=models.PROTECT,
    )
    n_systems = models.PositiveIntegerField(default=1)
    n_scenarios_per_system = models.PositiveIntegerField(default=3)
    variants = models.JSONField(default=list)

    created_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.variants:
            self.variants = list(VARIANTS)
        super().save(*args, **kwargs)

    @property
    def runs_labelling(self):
        return isinstance(self.phases, list) and PHASE_LABELLING in self.phases

    @property
    def labels_only(self):
        return self.phases == PHASE_LABELLING_ONLY

    def label_scenario_rows(self):
        """The scenarios `clean()` checks: the serializer's pending list when it
        set one, else the saved M2M (empty for an unsaved row)."""
        pending = getattr(self, "_pending_label_scenarios", None)
        if pending is not None:
            return list(pending)
        if not self.pk:
            return []
        return list(self.label_scenarios.select_related("generation_run"))

    def phase2_prompt_for(self, variant):
        """Which per-variant phase-2 template `launch()` pins onto `variant`'s
        generation `PhaseRun` — `phase2_prompt_a` for A, `phase2_prompt_b`
        for B. The one place that mapping is spelled out, so `launch()`
        (each variant's real run) and any future caller needing the same
        lookup (a preview endpoint, say) cannot drift into two different
        ideas of which field backs which variant.
        """
        if variant == VARIANT_UNBIASED:
            return self.phase2_prompt_a
        if variant == VARIANT_GUIDED:
            return self.phase2_prompt_b
        raise ValueError(f"Unknown variant {variant!r}; expected 'A' or 'B'.")

    @property
    def generation_config(self):
        """This experiment's phase-2 generation configuration, in the shape
        `generation_config_errors` checks (`GENERATION_CONFIG_FIELDS`).

        The adapter that lets `Experiment.clean()` and the two ad-hoc call
        sites run the SAME checker over the same conceptual configuration
        rather than three hand-ported copies of its rules — see
        `generation_config_errors`' own docstring for why that seam, and
        not `full_clean()`, is the one that closes this class of bug.

        `seed_systems`/`fewshots` are read only when `self.pk` exists: a
        many-to-many manager on an UNSAVED instance raises rather than
        returning an empty set, and an unsaved experiment provably has no
        related rows yet — `clean()` has always skipped the seed check for
        that reason, and `ExperimentSerializer._refuse_generated_seeds`
        exists precisely to cover the create-time hole it leaves.
        """
        return {
            "seed_systems": list(self.seed_systems.all()) if self.pk else [],
            "fewshots": list(self.fewshots.all()) if self.pk else [],
            "target_system": self.target_system if self.target_system_id else None,
            "target_system_id": self.target_system_id,
            "generate_systems": self.generate_systems,
            "generate_scenarios": self.generate_scenarios,
            "n_systems": self.n_systems,
            "n_scenarios_per_system": self.n_scenarios_per_system,
        }

    def clean(self):
        # Every CONFIG-shaped rule — generated seeds, a generated or missing
        # target system, the generation toggles — now lives in ONE shared
        # checker that the ad-hoc iterate-mode path calls too, at both its
        # create-time and execution-time seams. See
        # `generation_config_errors`' own docstring: hand-porting these
        # rules across to `PhaseRunCreateSerializer`/`_adhoc_generation_config`
        # failed three review rounds running, and `pipeline/tests/
        # test_adhoc_rule_parity.py` now fails the build if a NEW rule is
        # added here without reaching that path.
        errors = {} if self.labels_only else dict(generation_config_errors(self.generation_config))

        variants = list(self.variants or [])
        variant_errors = []
        unknown = [v for v in variants if v not in VARIANTS]
        if unknown:
            variant_errors.append(f"Unknown variant(s) {unknown}; expected A and/or B.")
        duplicated = sorted({v for v in variants if variants.count(v) > 1})
        if duplicated:
            # ["A", "A"] used to pass and produce a repetition with two A arms:
            # twice the cost, and a "comparison" of one variant against itself.
            variant_errors.append(
                f"Duplicate variant(s) {duplicated}; each variant may appear at most once."
            )
        if variant_errors and not self.labels_only:
            errors["variants"] = variant_errors

        # Which variants this experiment will ACTUALLY run once saved —
        # mirroring `save()`'s own "an empty variants list defaults to
        # both" behaviour, above. This matters because `clean()`/
        # `full_clean()` is called on an UNSAVED instance too
        # (`ExperimentSerializer.validate()`, at create time, before
        # `save()` has ever applied that default): reading bare `variants`
        # for the per-variant template check below would see `[]` for a
        # create request that never mentions `variants` at all, make the
        # check vacuously pass with NEITHER `phase2_prompt_a` nor
        # `phase2_prompt_b` set, and let the API store a configuration
        # `launch()` would go on to refuse the moment it re-validates the
        # NOW-defaulted, saved instance — precisely the "operator learns at
        # run time what they could have learned at save time" gap
        # `ExperimentSerializer.validate()`'s own docstring exists to
        # close. Deliberately only used below, not for the `unknown`/
        # `duplicated` checks above: a genuinely submitted
        # `variants=["C"]` must still be reported against the RAW list,
        # never silently swapped for the default.
        effective_variants = variants if variants else list(VARIANTS)

        phases_error = _phases_error(self.phases)
        if phases_error:
            errors["phases"] = phases_error

        # A phase-3 labelling template pinned as the generation prompt (or the
        # reverse) validated fine and only surfaced later as a baffling render
        # or a wasted run. See `template_phase_mismatch_error`'s own
        # docstring — shared with `PhaseRunCreateSerializer`'s identical check
        # for an ad-hoc run's own `prompt_template`. `phase2_prompt_a` and
        # `phase2_prompt_b` are both checked against phase 2 — the split into
        # two independent fields did not change which phase either belongs to.
        for field, expected in (
            ("phase2_prompt_a", 2),
            ("phase2_prompt_b", 2),
            ("phase3_prompt", 3),
        ):
            if getattr(self, f"{field}_id") is None:
                continue
            template_error = template_phase_mismatch_error(getattr(self, field), expected, field)
            if template_error:
                errors[field] = template_error

        # Each variant this experiment actually RUNS needs its own phase-2
        # template — `variants` may be ["A"], ["B"], or both (see the
        # `unknown`/`duplicated` checks above), so requiring BOTH
        # unconditionally would wrongly refuse a real "B only" experiment
        # that has no use for `phase2_prompt_a` at all. Deliberately inline
        # here, not a shared `*_error` helper: unlike the config-shaped rules
        # in `generation_config_errors`, this rule has no ad-hoc-path
        # equivalent to reach — `PhaseRunCreateSerializer` never derives an
        # ad-hoc run's `prompt_template` from `Experiment.phase2_prompt_a`/
        # `_b` at all (an ad-hoc run supplies its own `prompt_template`
        # directly, validated by `template_phase_mismatch_error` above), so
        # there is nothing on that path for this rule to be wired into — the
        # same reasoning that already keeps the `variants` list checks above
        # (unknown/duplicated) inline rather than factored out.
        for variant, field in (
            (VARIANT_UNBIASED, "phase2_prompt_a"),
            (VARIANT_GUIDED, "phase2_prompt_b"),
        ):
            if (
                not self.labels_only
                and variant in effective_variants
                and getattr(self, f"{field}_id") is None
            ):
                errors[field] = (
                    f"variant {variant!r} is enabled (in `variants`) but {field} "
                    f"is not set. Phase 2 needs a template for every variant "
                    "this experiment actually runs."
                )

        if not self.labels_only and not (self.phase2_model or "").strip():
            errors["phase2_model"] = "Set phase2_model: phase 2 is enabled."
        if self.labels_only:
            label_error = label_scenarios_error(self.label_scenario_rows())
            if label_error:
                errors["label_scenarios"] = label_error

        # Phase-3 settings are required only when this experiment labels.
        if self.runs_labelling:
            if not (self.phase3_model or "").strip():
                errors["phase3_model"] = "Set phase3_model: phase 3 is enabled."
            if self.phase3_prompt_id is None:
                errors["phase3_prompt"] = "Set phase3_prompt: phase 3 is enabled."

        if self.taxonomy_id:
            # See `taxonomy_missing_rows_error`'s own docstring — shared with
            # `PhaseRunCreateSerializer`'s identical check.
            taxonomy_error = taxonomy_missing_rows_error(self.taxonomy)
            if taxonomy_error:
                errors["taxonomy"] = taxonomy_error

        if errors:
            raise ValidationError(errors)

    @property
    def progress(self):
        """Summed progress across every repetition of this experiment.

        One aggregate query across every phase run of every repetition —
        not `run.progress for run in self.runs.all()`, which cost a query
        per repetition on top of the one for `self.runs.all()` itself (N+1;
        11 queries for 10 repetitions).
        """
        totals = PhaseRun.objects.filter(pipeline_run__experiment=self).aggregate(
            total=Sum("total_children"),
            completed=Sum("completed_children"),
            failed=Sum("failed_children"),
        )
        done = (totals["completed"] or 0) + (totals["failed"] or 0)
        return _progress_dict(done, totals["total"] or 0, totals["failed"] or 0)


class PipelineRun(models.Model):
    """One repetition of an experiment — every variant, each pinned phase."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        # Terminal, but distinct from FAILED: nothing failed, an operator (or
        # the system, on their behalf) called it dead so it could be deleted.
        # See PipelineRunViewSet.cancel — allowed from PENDING, QUEUED or
        # RUNNING, the one route out of a run stuck forever with no worker
        # coming for it.
        CANCELLED = "cancelled", "Cancelled"

    experiment = models.ForeignKey(Experiment, related_name="runs", on_delete=models.CASCADE)
    repetition_index = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # The configuration THIS repetition actually used, snapshotted once by
    # `launch()` at creation time — never re-derived from `Experiment`'s
    # live, editable fields afterward. `Experiment` may be (and is meant to
    # be) edited freely even after it has runs; nothing here freezes that.
    # This is what keeps an already-launched run's export truthful regardless
    # of any later edit — "browsing an older run should show its previous
    # config" (2026-09-09 user decision). See
    # `pipeline.execution_config.config_snapshot` for its exact shape and why
    # ids alone are not enough (a seed system's body is editable in place).
    config = models.JSONField(default=dict)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["experiment", "repetition_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "repetition_index"], name="unique_repetition"
            )
        ]

    def __str__(self):
        return f"{self.experiment.name} #{self.repetition_index}"

    @property
    def progress(self):
        """Summed progress across every phase run of this repetition."""
        return _sum_progress(phase_run.progress for phase_run in self.phase_runs.all())


class PhaseRun(models.Model):
    """One phase of one variant. `pipeline_run` is null for an ad-hoc run."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    pipeline_run = models.ForeignKey(
        PipelineRun,
        null=True,
        blank=True,
        related_name="phase_runs",
        on_delete=models.CASCADE,
    )
    phase = models.PositiveSmallIntegerField(
        choices=[(2, "Phase 2 — generation"), (3, "Phase 3 — labelling")]
    )
    variant = models.CharField(max_length=1, blank=True, default="")
    model = models.CharField(max_length=200)
    temperature = models.FloatField(default=0.0)
    prompt_template = models.ForeignKey("prompts.PromptTemplate", on_delete=models.PROTECT)
    # Pinned on both variants for the record. Injected only for B; for A it is
    # passed as guard_taxonomy so the leak backstop has something to scan.
    taxonomy = models.ForeignKey("taxonomy.Taxonomy", on_delete=models.PROTECT)
    source_phase_run = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        related_name="labelling_runs",
        on_delete=models.SET_NULL,
    )
    # Iterate mode's "borrow an experiment's live inputs" — see
    # `pipeline.execution_config._adhoc_generation_config`/
    # `_adhoc_labelling_fewshots`, the only readers. Deliberately separate
    # from `pipeline_run`: an ad-hoc run stays ad-hoc (null `pipeline_run`,
    # excluded from every export — see `pipeline.export._phase_run_ids`)
    # even when it borrows an experiment's configuration; only a real
    # `launch()` ever sets `pipeline_run`. `on_delete=PROTECT`, matching
    # `taxonomy`/`prompt_template` above: SET_NULL would make "this run
    # never borrowed an experiment" and "the experiment it borrowed was
    # since deleted" indistinguishable the instant the FK went null — the
    # exact silent-fallback failure this field exists to prevent (see
    # `ExperimentViewSet.perform_destroy`'s matching guard).
    experiment = models.ForeignKey(
        "pipeline.Experiment",
        null=True,
        blank=True,
        related_name="adhoc_phase_runs",
        on_delete=models.PROTECT,
    )
    # Direct per-run overrides for a borrowed experiment's own `n_systems`/
    # `n_scenarios_per_system` — the actual iterate-mode use case named by
    # the researcher: real seeds and few-shots, but ONE system and ONE
    # scenario, cheap and fast to tune against. `None` (not 0 — see
    # `pipeline.serializers.PhaseRunCreateSerializer`'s `min_value=1`) means
    # "no override, use the borrowed experiment's own value"; meaningless
    # (read by nothing) without `experiment` set, and for a phase-3 run.
    n_systems = models.PositiveIntegerField(null=True, blank=True)
    n_scenarios_per_system = models.PositiveIntegerField(null=True, blank=True)
    # Ad-hoc phase-3 runs only: the exact Scenario ids a researcher chose to
    # label (seed, manual or generated). None means "use source_phase_run".
    scenario_ids = models.JSONField(null=True, blank=True)
    # Ad-hoc phase-2 runs that borrow no experiment only: the test's own chosen
    # inputs (see `execution_config.chosen_generation_config`). Ids, not FKs, so a
    # deleted row fails the run instead of silently vanishing. None = built-in default.
    seed_system_ids = models.JSONField(null=True, blank=True)
    fewshot_ids = models.JSONField(null=True, blank=True)
    target_system_pk = models.PositiveIntegerField(null=True, blank=True)
    generate_systems = models.BooleanField(null=True, blank=True)
    generate_scenarios = models.BooleanField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    error = models.TextField(blank=True, default="")
    total_children = models.PositiveIntegerField(default=0)
    completed_children = models.PositiveIntegerField(default=0)
    failed_children = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(scenario_ids__isnull=True) | Q(phase=3),
                name="scenario_ids_only_on_labelling",
            ),
            models.CheckConstraint(
                condition=Q(
                    seed_system_ids__isnull=True,
                    fewshot_ids__isnull=True,
                    target_system_pk__isnull=True,
                    generate_systems__isnull=True,
                    generate_scenarios__isnull=True,
                )
                | Q(phase=2, pipeline_run__isnull=True, experiment__isnull=True),
                name="chosen_inputs_only_on_unborrowed_adhoc_generation",
            ),
        ]

    def __str__(self):
        label = f"phase {self.phase}"
        return f"{label} {self.variant}".strip()

    @property
    def is_unbiased(self):
        return self.variant == VARIANT_UNBIASED

    @property
    def is_terminal(self):
        """Has this run already reached an end state — successfully, by
        failing, or by being cancelled?

        Re-executing a finished run duplicates its provider calls, which
        double-counts its tokens and cost — the same is true of resurrecting
        a CANCELLED one, which is why CANCELLED joined COMPLETED/FAILED here
        (task 5 review round; an earlier version of this docstring said
        CANCELLED meant "never ran" and re-driving one was legitimate — that
        stopped being true the moment `cancel` could mark a run CANCELLED
        mid-flight, with real calls already archived, not only the
        "generation never completed" case this originally covered).
        `run_phase2`/`run_labelling` both hard-stop on `is_terminal` now
        including CANCELLED, so NOTHING re-drives a cancelled run through
        the ordinary execution path any more.

        `retry_failed` is the one caller that still needs its OWN, separate
        check rather than relying on this: it must refuse a CANCELLED run
        OUTRIGHT with an accurate reason ("cancelled, launch a new run
        instead" — see `pipeline.orchestration.RunCancelled` — not "wait for
        it to finish", which `RunInFlight`'s wording would otherwise imply
        for a run that will never finish on its own). And
        `finish_labelling_run` deliberately does NOT use this property at
        all: `retry_failed`'s legitimate re-drive of an already
        COMPLETED/FAILED run must still be able to re-derive a NEW terminal
        status from it, which a blanket `is_terminal` guard there would
        block — it checks CANCELLED specifically instead.
        """
        return self.status in (self.Status.COMPLETED, self.Status.FAILED, self.Status.CANCELLED)

    @property
    def progress(self):
        done = self.completed_children + self.failed_children
        return _progress_dict(done, self.total_children, self.failed_children)


class LLMCall(models.Model):
    """One archived request/response. Complete whether it succeeded or not."""

    phase_run = models.ForeignKey(PhaseRun, related_name="calls", on_delete=models.CASCADE)
    sequence = models.PositiveIntegerField(default=0)
    scenario = models.ForeignKey(
        "corpus.Scenario",
        null=True,
        blank=True,
        related_name="calls",
        on_delete=models.SET_NULL,
    )

    rendered_system_prompt = models.TextField(blank=True, default="")
    rendered_user_prompt = models.TextField(blank=True, default="")
    # Written from `RenderedPrompt.leak_scan_ran`/`.guarded_by` — the render-
    # time backstop's verdict (`prompts.rendering.assert_no_taxonomy_leak`,
    # via `render()`) — but for a phase-2 call the meaning is wider than that
    # alone: `pipeline.generation._call_and_persist` attaches a SECOND scan,
    # `assert_request_not_leaking_taxonomy`, as the client's `preflight`,
    # scanning the fully serialised request immediately before the HTTP call.
    # That one raises before an LLMCall is ever constructed, so a row simply
    # does not exist to carry a leaked call's `leak_scan_ran` value — a
    # persisted `True` on a phase-2 row therefore certifies BOTH scans ran
    # and found nothing, structurally, not by trusting this column alone.
    # Phase 3 (`pipeline.labelling`) attaches no second scan — it legitimately
    # renders with the taxonomy present — so this field there still means
    # only what it always has: nothing, since it stays False throughout.
    leak_scan_ran = models.BooleanField(default=False)
    guarded_by = models.ForeignKey(
        "taxonomy.Taxonomy",
        null=True,
        blank=True,
        related_name="+",
        on_delete=models.SET_NULL,
    )

    request_json = models.JSONField(default=dict)
    response_json = models.JSONField(null=True, blank=True)
    raw_response = models.TextField(blank=True, default="")

    parsed_ok = models.BooleanField(default=False)
    parse_error = models.TextField(blank=True, default="")
    unknown_hcv_ids = models.JSONField(default=list, blank=True)
    # A phase-2 response that parsed cleanly (parsed_ok stays True, parse_error
    # stays "") but ALSO carried a redundant top-level `scenarios` list
    # alongside populated `system_descriptions` — see
    # `pipeline.generation._redundant_top_level_scenarios`. The nested
    # scenarios are persisted as authoritative and this list is not; it is
    # recorded here, verbatim, purely as a visible research signal ("the model
    # filled in both places even though the schema no longer offers both") —
    # never folded into `parsed_ok`/`parse_error`, which measure something
    # else entirely (see this field's own naming next to `unknown_hcv_ids`,
    # the same kind of "anomaly, not a parse failure" signal on the phase-3
    # side).
    stray_scenarios = models.JSONField(default=list, blank=True)

    http_status = models.IntegerField(null=True, blank=True)
    transport_error = models.TextField(blank=True, default="")
    model = models.CharField(max_length=200, blank=True, default="")
    finish_reason = models.CharField(max_length=50, blank=True, default="")
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    cost = models.FloatField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["phase_run", "sequence", "id"]
        constraints = [
            # A sequence number identifies one attempt within a run, so writing
            # two rows for it is always a double-count of tokens and cost. Note
            # `scenario` is nullable and NULLs compare as distinct in every
            # database this project supports, so a phase-2 run's calls (scenario
            # NULL) are not restricted by this at all — it guards the phase-3
            # fan-out, where one scenario maps to one call.
            models.UniqueConstraint(
                fields=["phase_run", "scenario", "sequence"],
                name="unique_call_per_run_scenario_sequence",
            )
        ]

    def __str__(self):
        return f"call {self.sequence} of {self.phase_run}"

    def save(self, *args, **kwargs):
        # Redaction only runs here, in save(). A queryset .update() bypasses
        # both this method and Django's model-level save signal, so it would
        # write straight past redact_call() — never write an LLMCall through
        # .update(); use .save() (or re-create it) so a key still gets scrubbed.
        from pipeline.redaction import redact_call

        redact_call(self)
        super().save(*args, **kwargs)

    @property
    def ok(self):
        return self.response_json is not None and not self.transport_error


class Label(models.Model):
    """One vulnerability assigned to one scenario by one labelling run."""

    class Source(models.TextChoices):
        LLM = "llm", "LLM"
        HUMAN = "human", "Human"

    phase_run = models.ForeignKey(PhaseRun, related_name="labels", on_delete=models.CASCADE)
    scenario = models.ForeignKey("corpus.Scenario", related_name="labels", on_delete=models.CASCADE)
    hcv = models.ForeignKey("taxonomy.HCV", related_name="labels", on_delete=models.PROTECT)
    evidence = models.TextField(blank=True, default="")
    rationale = models.TextField(blank=True, default="")
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.LLM)
    # The call this label came out of. Spec §9 requires the export to name it,
    # and while it is derivable today by joining on (phase_run, scenario), that
    # join stops being unique the moment a call is duplicated — and it cannot be
    # backfilled afterwards. Nullable for a human label, which has no call.
    llm_call = models.ForeignKey(
        "pipeline.LLMCall",
        null=True,
        blank=True,
        related_name="labels",
        on_delete=models.SET_NULL,
    )
    created_by = models.ForeignKey("core.User", null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # `id` is a deliberate final tiebreaker, not decoration: two labels
        # can share `(scenario, hcv)` from different phase runs (a re-run,
        # a retry) and are then an exact tie under `scenario, hcv` alone —
        # their relative order becomes database-implementation-defined.
        # `/api/labels/` pages this ordering directly, and an undefined tie
        # order means a caller paging past PAGE_SIZE can see a row twice or
        # miss one outright. Fixed here, at the model, so the export, the
        # admin and every future consumer inherit the same stable order
        # rather than only whichever endpoint happens to add its own.
        ordering = ["scenario", "hcv", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["phase_run", "scenario", "hcv"], name="unique_label_per_run"
            )
        ]

    def __str__(self):
        return f"{self.scenario.scenario_id} -> {self.hcv.hcv_id}"
