from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from prompts.rendering import VARIANT_GUIDED, VARIANT_UNBIASED

VARIANTS = (VARIANT_UNBIASED, VARIANT_GUIDED)


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


class Experiment(models.Model):
    """A pinned configuration. Everything a run needs is frozen here.

    Models, prompt versions and the taxonomy version are all recorded so a
    result can be traced back to the exact wording and label list that produced
    it — editing a template later creates a new version and cannot rewrite this.
    """

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True, default="")
    taxonomy = models.ForeignKey("taxonomy.Taxonomy", on_delete=models.PROTECT)

    phase2_model = models.CharField(max_length=200)
    phase2_temperature = models.FloatField(default=1.0)
    phase2_prompt = models.ForeignKey(
        "prompts.PromptTemplate",
        related_name="phase2_experiments",
        on_delete=models.PROTECT,
    )
    phase3_model = models.CharField(max_length=200)
    phase3_temperature = models.FloatField(default=0.0)
    phase3_prompt = models.ForeignKey(
        "prompts.PromptTemplate",
        related_name="phase3_experiments",
        on_delete=models.PROTECT,
    )

    seed_systems = models.ManyToManyField("corpus.SystemDescription", blank=True)
    fewshots = models.ManyToManyField("corpus.FewShotExample", blank=True)

    generate_systems = models.BooleanField(default=True)
    generate_scenarios = models.BooleanField(default=True)
    target_system = models.ForeignKey(
        "corpus.SystemDescription",
        null=True,
        blank=True,
        related_name="targeted_by",
        on_delete=models.PROTECT,
    )
    n_systems = models.PositiveIntegerField(default=3)
    n_scenarios_per_system = models.PositiveIntegerField(default=5)
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

    def clean(self):
        errors = {}
        if not self.generate_systems and not self.generate_scenarios:
            errors["generate_systems"] = (
                "Enable at least one of generate_systems or generate_scenarios."
            )
        if not self.generate_systems and self.target_system_id is None:
            errors["target_system"] = (
                "Set target_system when generate_systems is off — scenarios need "
                "a system to be written against."
            )

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
        if variant_errors:
            errors["variants"] = variant_errors

        # A generated system is a previous run's OUTPUT. Seeding with it is
        # already refused; targeting with it is the same contamination by
        # another route, and a worse one — target_system.body is exposed to the
        # prompt of BOTH variants, so a system written by a model that had the
        # taxonomy in front of it reaches a variant A prompt. The leak backstop
        # cannot catch that: paraphrased, taxonomy-derived prose trips no
        # identifier match and stays under the distinct-name threshold, so the
        # contamination would be invisible in the results.
        if self.target_system_id and self.target_system.origin == "generated":
            errors["target_system"] = (
                f"{self.target_system.title!r} was generated by a previous run. "
                "Targeting an experiment at its own output contaminates the "
                "comparison — its body is written into the variant A prompt."
            )

        # A phase-3 labelling template pinned as the generation prompt (or the
        # reverse) validated fine and only surfaced later as a baffling render
        # or a wasted run.
        for field, expected in (("phase2_prompt", 2), ("phase3_prompt", 3)):
            if getattr(self, f"{field}_id") is None:
                continue
            template = getattr(self, field)
            if template.phase != expected:
                errors[field] = (
                    f"{template} is a phase {template.phase} template; {field} must "
                    f"be a phase {expected} template."
                )

        if self.taxonomy_id and not self.taxonomy.hcvs.exists():
            # Variant A's leak backstop scans the rendered request against this
            # list. With no rows it cannot run at all — the run completes with
            # leak_scan_ran=False and a warning nobody reads, which is the
            # isolation guarantee silently switched off. Cheaper to catch here.
            errors["taxonomy"] = (
                f"{self.taxonomy} has no HCV rows, so variant A's taxonomy-leak "
                "backstop cannot run. Import the taxonomy before running against it."
            )

        if self.pk:
            generated = [s.title for s in self.seed_systems.all() if s.origin == "generated"]
            if generated:
                errors["seed_systems"] = (
                    f"{generated} were generated by a previous run. Seeding an "
                    "experiment with its own output contaminates the comparison."
                )
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
    """One repetition of an experiment — every variant, both phases."""

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
