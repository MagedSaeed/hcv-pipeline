from django.contrib import admin

from corpus.admin import ResourceFileAdmin, SystemDescriptionAdmin
from corpus.models import ResourceFile


def test_system_description_admin_origin_is_read_only():
    """Coordinator review round, finding 1 (CRITICAL): a bare `ModelAdmin`
    left `origin` freely editable, so a seed system validly targeted by an
    experiment could be flipped to `generated` afterwards, with nothing
    re-validating it — `pipeline.models.generated_seed_systems_error`/
    `generated_target_system_error` trust `origin` to refuse a GENERATED
    system as an experiment's seed or target, and the leak backstop cannot
    catch that contamination on its own (generated prose is taxonomy-DERIVED,
    not a literal quote). Read-only closes the write path instead of
    trusting nobody uses it — the same reasoning
    `pipeline.admin.PipelineRunAdmin.config`/`LLMCallAdmin.leak_scan_ran`
    are already read-only for (see `pipeline/tests/test_admin.py`).
    """
    assert "origin" in SystemDescriptionAdmin.readonly_fields


def test_resource_file_admin_kind_is_read_only_on_change_but_not_on_add():
    """Coordinator review round 3, finding 3: `kind` backs the variant-A
    taxonomy-resource refusal (`prompts.rendering._make_resource`) — the
    same class of hole `SystemDescriptionAdmin.origin` (above) closes.
    Unlike `origin`, though, choosing a `kind` at UPLOAD time is a
    legitimate, necessary step — `get_readonly_fields` must keep it
    editable on ADD (`obj=None`) and lock it only once a real row exists
    (`obj is not None`), not use the simpler, blanket `readonly_fields`
    tuple `SystemDescriptionAdmin.origin` uses.
    """
    admin_instance = ResourceFileAdmin(ResourceFile, admin.site)

    assert "kind" not in admin_instance.get_readonly_fields(request=None, obj=None)
    assert "kind" in admin_instance.get_readonly_fields(request=None, obj=object())
