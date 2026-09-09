from django.contrib import admin

from taxonomy.models import HCV, Taxonomy


class HCVInline(admin.TabularInline):
    model = HCV
    extra = 0


@admin.register(Taxonomy)
class TaxonomyAdmin(admin.ModelAdmin):
    list_display = ("vocabulary_id", "version", "is_active", "created_at")
    inlines = [HCVInline]


@admin.register(HCV)
class HCVAdmin(admin.ModelAdmin):
    list_display = ("hcv_id", "name", "taxonomy", "has_condition_class")
    list_filter = ("taxonomy",)
    search_fields = ("hcv_id", "name", "definition")
