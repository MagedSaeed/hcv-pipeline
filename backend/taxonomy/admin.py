from django.contrib import admin

from taxonomy.models import HCV, Taxonomy

# `hcv_id`/`name`/`definition` are exactly the three fields
# `prompts.rendering.assert_no_taxonomy_leak` scans a variant-A request
# against — the whole isolation guarantee's backstop layer. Blanking or
# rewording an `hcv_id` here would silently narrow that scan while the row
# still reports success, the same class of hole `SystemDescriptionAdmin
# .origin`/`ResourceFileAdmin.kind` close elsewhere (see their own
# comments). HCV rows come from `import_taxonomy`, never hand-entry, so
# read-only here — on BOTH the inline (under `TaxonomyAdmin`) and the
# standalone admin, unlike `ResourceFileAdmin.kind`'s add-time exception —
# closes a write path with no legitimate workflow behind it at all, rather
# than merely one that needs to stay open a little longer.
_HCV_SCAN_FIELDS = ("hcv_id", "name", "definition")

# Round 4, leak 1 — the hole the round-3 fix directly CREATED. Marking a
# field read-only does not merely disable its widget: Django EXCLUDES it
# from the ModelForm entirely, so its model-level validation
# (`CharField(blank=False)`) never runs. On the CHANGE form that is exactly
# what was wanted; on the ADD form it turned a `hcv_id`-required create
# into a create with no required fields at all, and
# `POST /admin/taxonomy/hcv/add/` started returning 302 with a row whose
# `hcv_id`/`name`/`definition` were all empty strings. A content-free row
# is not cosmetic: `assert_no_taxonomy_leak` skips every falsy field, so a
# taxonomy made of them scans for NOTHING, finds nothing, and still returns
# True — `leak_scan_ran=True, guarded_by=<blank taxonomy>` archived as
# proof over a prompt with real HCV text in it.
#
# The fix is to remove the capability, not to bolt validation back onto it:
# HCV rows come from `import_taxonomy` (which itself refuses any row
# missing `hcv_id` or `name`), and hand-entering a 21-item research
# taxonomy through an admin form is not a workflow anyone has or wants. No
# add form means "read-only" cannot mean "creatable blank" here again.
# `pipeline.models.taxonomy_missing_rows_error` closes the same leak at the
# deeper level, refusing any taxonomy with no SCANNABLE rows however those
# rows got there — this only removes the one route that produced them.
_HCV_ROWS_COME_FROM_IMPORT = (
    "HCV rows are imported from the taxonomy JSON (`manage.py import_taxonomy`), "
    "never hand-entered — a row added here would have no scannable content for "
    "variant A's leak backstop."
)


class HCVInline(admin.TabularInline):
    model = HCV
    extra = 0
    readonly_fields = _HCV_SCAN_FIELDS

    def has_add_permission(self, request, obj=None):
        """No blank-row route through the nested inline either — see
        `_HCV_ROWS_COME_FROM_IMPORT`. `ModelAdmin`'s own
        `has_add_permission` does not propagate to an inline (the same
        reason `readonly_fields` had to be repeated here), so `HCVAdmin`'s
        would have left this second route wide open."""
        return False


@admin.register(Taxonomy)
class TaxonomyAdmin(admin.ModelAdmin):
    # `is_active` stays editable on purpose: activating a taxonomy is a real
    # operator workflow. What used to be missing is that NOTHING stopped a
    # second row being activated alongside the first, which silently
    # re-bound `Taxonomy.active()` — and every unborrowed variant-A run's
    # guard — to whichever was newer (round 4, leak 2). The
    # `only_one_active_taxonomy` database constraint
    # (`taxonomy.models.Taxonomy.Meta`) now refuses that write, and Django's
    # `validate_constraints()` surfaces it here as an ordinary form error
    # with the constraint's own `violation_error_message`, not a 500.
    list_display = ("vocabulary_id", "version", "is_active", "created_at")
    inlines = [HCVInline]


@admin.register(HCV)
class HCVAdmin(admin.ModelAdmin):
    list_display = ("hcv_id", "name", "taxonomy", "has_condition_class")
    list_filter = ("taxonomy",)
    search_fields = ("hcv_id", "name", "definition")
    readonly_fields = _HCV_SCAN_FIELDS

    def has_add_permission(self, request):
        """See `_HCV_ROWS_COME_FROM_IMPORT`: with every scannable field
        read-only, the add form had no required fields left at all, so it
        happily created content-free rows. Removing the add capability is
        the fix; patching validation back onto a form nobody should be
        using would only re-open it the next time a field is added to
        `readonly_fields`."""
        return False
