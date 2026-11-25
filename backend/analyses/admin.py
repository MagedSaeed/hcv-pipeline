from django.contrib import admin

from analyses.models import Analysis


@admin.register(Analysis)
class AnalysisAdmin(admin.ModelAdmin):
    """Read and delete only: edits go through the API, which enforces the lock."""

    list_display = ("name", "owner", "taxonomy", "updated_at")
    search_fields = ("name", "owner__email")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
