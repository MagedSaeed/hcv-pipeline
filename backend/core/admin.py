from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from core.keys import mask_key
from core.models import AppSettings, User


@admin.register(User)
class AppUserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "name", "is_staff", "must_change_password")
    search_fields = ("email", "name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("name",)}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "must_change_password")},
        ),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)


@admin.register(AppSettings)
class AppSettingsAdmin(admin.ModelAdmin):
    """Audit surface only. The key is set through the API, never shown here.

    A staff account granted view-only access for auditing must be able to see
    who changed the key and when, without being handed the secret itself.
    """

    list_display = ("__str__", "updated_by", "updated_at")
    fields = ("masked_key", "updated_by", "updated_at")
    readonly_fields = ("masked_key", "updated_by", "updated_at")

    @admin.display(description="OpenRouter key")
    def masked_key(self, obj):
        return mask_key(obj.openrouter_key)

    def has_add_permission(self, request):
        # Singleton, and every field here is read-only: an add form could only
        # ever save an empty row. AppSettings.load() creates the only one.
        return False

    def has_delete_permission(self, request, obj=None):
        return False
