from django.contrib import admin

from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun


@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("name", "phase2_model", "phase3_model", "created_at")
    filter_horizontal = ("seed_systems", "fewshots")

    def has_delete_permission(self, request, obj=None):
        """Refuse deletion of an experiment with any runs recorded against it.

        The same guard as `ExperimentViewSet.perform_destroy`'s 409, closed
        here too: `Experiment.delete()` cascades away every PipelineRun,
        PhaseRun, generated SystemDescription/Scenario and LLMCall for it,
        and the admin is a surface an operator actually uses, not just the
        API. `obj is None` is Django's own "is deletion possible at all"
        probe — used to decide whether to show the 'Delete selected' bulk
        action in the dropdown — and must defer to the default check; only
        once there is a real object to inspect does the run count matter.
        Both the single-object delete view and the 'Delete selected' bulk
        action route through this same method with the real object (Django's
        NestedObjects collector checks has_delete_permission on every object
        it would cascade through, including the ones directly selected), so
        one override covers both.
        """
        if obj is not None and obj.runs.exists():
            return False
        return super().has_delete_permission(request, obj)


@admin.register(PipelineRun)
class PipelineRunAdmin(admin.ModelAdmin):
    list_display = ("__str__", "status", "started_at", "finished_at")
    list_filter = ("status",)


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
    )
    list_filter = ("phase", "variant", "status")


@admin.register(LLMCall)
class LLMCallAdmin(admin.ModelAdmin):
    list_display = ("__str__", "model", "parsed_ok", "http_status", "cost")
    list_filter = ("parsed_ok",)


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ("__str__", "source", "created_at")
    list_filter = ("source", "hcv")
