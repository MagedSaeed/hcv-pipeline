from taxonomy.admin import HCVAdmin, HCVInline


def test_hcv_admin_scan_fields_are_read_only():
    """Coordinator review round 3, finding 3: `hcv_id`/`name`/`definition`
    are exactly the three fields `prompts.rendering.assert_no_taxonomy_leak`
    scans a variant-A request against — blanking or rewording an `hcv_id`
    here would silently NARROW that scan while the row still reports
    success. A bare `ModelAdmin` left them freely editable. HCV rows come
    from `import_taxonomy`, never hand-entry, so this is read-only
    unconditionally — unlike `corpus.admin.ResourceFileAdmin.kind`'s
    add-time exception, there is no legitimate admin-UI workflow this
    would ever need to leave open.
    """
    for field in ("hcv_id", "name", "definition"):
        assert field in HCVAdmin.readonly_fields


def test_hcv_inline_scan_fields_are_read_only_too():
    """The SAME three fields, reachable a second way: `HCVInline`, nested
    under `TaxonomyAdmin`. `ModelAdmin.readonly_fields` does not
    automatically apply to an inline formset — it needs its OWN
    `readonly_fields` — so `HCVAdmin`'s fix alone would have left this
    route open."""
    for field in ("hcv_id", "name", "definition"):
        assert field in HCVInline.readonly_fields


def test_hcv_admin_offers_no_add_form_at_all():
    """Round 4, leak 1 — the hole the read-only fix above CREATED.

    Marking a field read-only does not merely disable its widget: Django
    excludes it from the ModelForm entirely, and its model-level validation
    (`CharField(blank=False)`) goes with it. So the moment `hcv_id`/`name`/
    `definition` became read-only, `POST /admin/taxonomy/hcv/add/` stopped
    having any required field at all and started returning 302 with a row
    whose every scannable field was `""` — a taxonomy of such rows scans
    for nothing, finds nothing, and still archives `leak_scan_ran=True`.

    The fix is to REMOVE the capability rather than bolt validation back
    onto a form nobody should be using: HCV rows come from
    `import_taxonomy` (which itself refuses a row missing `hcv_id` or
    `name`), so no legitimate workflow needs an add form here. Without
    this, any future field added to `readonly_fields` re-opens the same
    hole silently.
    """
    assert HCVAdmin.has_add_permission(HCVAdmin, request=None) is False


def test_hcv_inline_offers_no_add_form_either():
    """The same route a second time, through `TaxonomyAdmin`'s nested
    inline. `ModelAdmin.has_add_permission` no more propagates to an inline
    than `readonly_fields` does (see the test above it), so `HCVAdmin`'s
    override alone would have left the inline's own "Add another HCV" row
    creating content-free rows exactly as before."""
    assert HCVInline.has_add_permission(HCVInline, request=None, obj=None) is False
