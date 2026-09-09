from django.contrib import admin

from corpus.models import FewShotExample, ResourceFile, Scenario, SystemDescription


@admin.register(ResourceFile)
class ResourceFileAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "uploaded_by", "created_at")
    list_filter = ("kind",)
    search_fields = ("name", "description")


class ScenarioInline(admin.TabularInline):
    model = Scenario
    extra = 0


@admin.register(SystemDescription)
class SystemDescriptionAdmin(admin.ModelAdmin):
    list_display = ("title", "origin", "created_at")
    list_filter = ("origin",)
    inlines = [ScenarioInline]


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("scenario_id", "system_description", "origin", "threat")
    list_filter = ("origin",)
    search_fields = ("scenario_id", "actor", "narrative")


@admin.register(FewShotExample)
class FewShotExampleAdmin(admin.ModelAdmin):
    list_display = ("name", "phase", "order", "is_active")
    list_filter = ("phase", "is_active")
