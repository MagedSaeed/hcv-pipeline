from django.contrib import admin

from prompts.models import PromptTemplate


@admin.register(PromptTemplate)
class PromptTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "phase", "version", "is_active", "created_at")
    list_filter = ("phase", "is_active")
    search_fields = ("name", "system_prompt", "user_prompt")
    readonly_fields = ("version", "created_at")
