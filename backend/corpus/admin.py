from django.contrib import admin

from corpus.models import FewShotExample, ResourceFile, Scenario, SystemDescription


@admin.register(ResourceFile)
class ResourceFileAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "uploaded_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("name", "description")

    def get_readonly_fields(self, request, obj=None):
        """`kind` backs the variant-A taxonomy-resource refusal
        (`prompts.rendering._make_resource`: a TAXONOMY-kind file is
        refused for Run A, anything else sails straight through) — the
        SAME class of hole `SystemDescriptionAdmin.origin` closes (see that
        admin's own comment): hand-flipping a resource's `kind` AFTER
        upload, with nothing re-validating it, could turn a refused
        TAXONOMY-kind file into an `OTHER`-kind one that reaches a variant-A
        prompt untouched, or the reverse (cosmetic, but still a lie about
        what was actually scanned).

        Read-only ONLY on CHANGE (`obj is not None`), not on ADD
        (`obj is None`): choosing a kind at UPLOAD time is legitimate and
        necessary — the file has to be tagged with something — only editing
        it afterward is the hole.
        """
        if obj is not None:
            return (*self.readonly_fields, "kind")
        return self.readonly_fields


class ScenarioInline(admin.TabularInline):
    model = Scenario
    extra = 0


@admin.register(SystemDescription)
class SystemDescriptionAdmin(admin.ModelAdmin):
    list_display = ("title", "origin", "created_at")
    list_filter = ("origin",)
    inlines = [ScenarioInline]
    # `origin` is what `pipeline.models.generated_seed_systems_error`/
    # `generated_target_system_error` trust to refuse a GENERATED system as
    # an experiment's seed or target — the leak backstop cannot catch that
    # contamination on its own (generated prose is taxonomy-DERIVED, not a
    # literal quote; see `generated_target_system_error`'s own docstring).
    # A bare `ModelAdmin` left `origin` freely editable: a seed system
    # validly targeted by an experiment could be flipped to `generated`
    # afterwards with nothing re-validating it, reaching a variant-A prompt
    # with the run reporting COMPLETED — a coordinator review round caught
    # this live. Read-only removes the write path instead of trusting
    # nobody uses it, the same reasoning `PipelineRunAdmin.config` and
    # `LLMCallAdmin.leak_scan_ran` are already read-only for. Harmless to
    # the ADD form too: `OriginMixin.origin` defaults to `MANUAL`, which is
    # exactly what an admin-created row should be — `SEED` rows come from
    # `seed_corpus`, `GENERATED` ones only ever from a real phase-2 run.
    readonly_fields = ("origin",)


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("scenario_id", "system_description", "origin", "threat")
    list_filter = ("origin",)
    search_fields = ("scenario_id", "actor", "narrative")


@admin.register(FewShotExample)
class FewShotExampleAdmin(admin.ModelAdmin):
    list_display = ("name", "phase", "order", "is_active")
    list_filter = ("phase", "is_active")
