from django.contrib import admin

from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun


@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("name", "phase2_model", "phase3_model", "created_at")
    filter_horizontal = ("seed_systems", "fewshots")

    def has_delete_permission(self, request, obj=None):
        """Mirror the API: an experiment is deletable unless one of its runs is
        pending, queued or running, or an iterate run borrows it (PROTECT; the API
        removes those first, the admin's collector cannot)."""
        if obj is not None and (
            obj.runs.filter(
                status__in=(
                    PipelineRun.Status.PENDING,
                    PipelineRun.Status.QUEUED,
                    PipelineRun.Status.RUNNING,
                )
            ).exists()
            or obj.adhoc_phase_runs.exists()
        ):
            return False
        return super().has_delete_permission(request, obj)


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "started_at", "finished_at")
    list_filter = ("status",)
    # `config` is the pinned snapshot execution and validation both trust as
    # authoritative (see `pipeline.execution_config`'s own docstring) — a
    # bare `ModelAdmin` renders a JSONField as an editable textarea, which
    # was a live hand-edit route to a dishonest snapshot (e.g. a lying
    # `input_resource_kind`) that `validate_snapshot`'s value checks alone
    # cannot close, since a hand-edited value and an honest one are the same
    # shape. Read-only here removes the write path instead.
    readonly_fields = ("config",)


@admin.register(PhaseRun)
class PhaseRunAdmin(admin.ModelAdmin):
    list_display = (
        "__str__",
        "phase",
        "variant",
        "status",
        "total_children",
        "completed_children",
        "failed_children",
        "progress",
        # The ad-hoc "borrow an experiment's live inputs" link (see
        # `PhaseRun.experiment`'s own comment) — null on every orchestrated
        # row, an experiment on a borrowing ad-hoc one. Coordinator review
        # round, finding 3: with no column or filter here, the ONLY way to
        # find the ad-hoc runs blocking `ExperimentViewSet.perform_destroy`'s
        # 409 (before that message named their ids directly) was to guess —
        # the admin had no navigable path to them at all.
        "experiment",
    )
    list_filter = (
        "phase",
        "variant",
        "status",
        # `RelatedOnlyFieldListFilter`: the plain form would dropdown EVERY
        # `Experiment` ever created, most of which no ad-hoc run has ever
        # borrowed — this narrows the choices to experiments actually
        # referenced by at least one `PhaseRun`.
        ("experiment", admin.RelatedOnlyFieldListFilter),
    )
    # Round 4, leak 3: the ORCHESTRATED path was left WEAKER than the ad-hoc
    # one. `launch()` pins these five fields onto every `PhaseRun` it creates
    # precisely so they are the experiment's own — `taxonomy` most of all,
    # since it IS the guard variant A's leak backstop scans against — and
    # unlike the ad-hoc path (whose `_check_adhoc_taxonomy_binding` re-checks
    # the binding live at execution time) nothing re-verifies an orchestrated
    # run's pinned fields at all: `PhaseRun.taxonomy` is not part of the
    # `PipelineRun.config` snapshot, so `validate_snapshot`'s exact-shape
    # check never sees it either. With this admin a bare `ModelAdmin`, a
    # PENDING variant-A run's `taxonomy` could simply be repointed at a
    # 1-row decoy through the change form, and the run would then complete
    # with `leak_scan_ran=True, guarded_by=<decoy>` over a prompt carrying
    # real HCV text — the identical false-evidence signature the ad-hoc
    # decoy probe produced a round earlier. Read-only removes the write
    # path, the same reasoning `PipelineRunAdmin.config` and
    # `LLMCallAdmin.leak_scan_ran` are already read-only for.
    #
    # `has_add_permission` below is not decoration: leak 1 of this same
    # round was CAUSED by read-only fields on an ADD form — Django excludes
    # a read-only field from the ModelForm entirely, taking its
    # `blank=False` validation with it, so `readonly_fields` alone would
    # have turned "add a PhaseRun" into "create a row with no phase, no
    # model, no template and no taxonomy". The two must be applied
    # together.
    readonly_fields = ("phase", "variant", "model", "prompt_template", "taxonomy")

    def has_add_permission(self, request):
        """A `PhaseRun` is created by `launch()` (orchestrated) or by
        `POST /api/phase-runs/` (ad-hoc) — both of which pin or validate the
        five fields above. Hand-creating one here was never a real workflow,
        and with those fields read-only it would produce a blank row rather
        than a usable one (see `readonly_fields`' comment: read-only must
        not mean creatable-blank, which is exactly how this round's leak 1
        was introduced)."""
        return False


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = ("__str__", "model", "parsed_ok", "http_status", "cost")
    list_filter = ("parsed_ok",)
    # `leak_scan_ran`/`guarded_by` are the archived PROOF that the taxonomy-
    # isolation checks ran on this call — since `pipeline.generation
    # ._call_and_persist` wires in the final-request choke point
    # unconditionally, a persisted `leak_scan_ran=True` on a phase-2 row now
    # certifies BOTH the render-time backstop and the final-request scan ran
    # clean (see `LLMCall.leak_scan_ran`'s own field comment). A bare
    # `ModelAdmin` renders a `BooleanField`/`ForeignKey` as an editable
    # widget, which is a live hand-edit route to falsely claiming a call was
    # scanned when it never was — the exact hole `PipelineRunAdmin.config`
    # was made read-only to close for the config snapshot (see its own
    # comment); it matters more here, because this field is what an operator
    # or the export ultimately trusts as evidence the isolation guarantee
    # held. Read-only removes the write path instead of relying on nobody
    # using it.
    readonly_fields = ("leak_scan_ran", "guarded_by")


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ("__str__", "source", "created_at")
    list_filter = ("source", "hcv")
