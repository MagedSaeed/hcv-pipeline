from django.db import models
from django.db.models import Q


class MultipleActiveTaxonomies(RuntimeError):
    """More than one `Taxonomy` row is flagged `is_active` at once.

    Coordinator review round 4, leak 2. With no experiment borrowed, the
    guard taxonomy an ad-hoc variant-A run is bound to IS
    `Taxonomy.active()` (`pipeline.models.unbound_guard_taxonomy_error`),
    and variant A's leak backstop
    (`prompts.rendering.assert_no_taxonomy_leak`) scans the rendered
    request against whatever that returns. Nothing used to enforce that
    only one row could be active, and `active()` itself simply took the
    newest — so creating a fresh 1-row decoy and flagging it active
    silently RE-BOUND every unborrowed run to it: the scan then genuinely
    ran, genuinely found nothing (wrong list), and archived
    `leak_scan_ran=True, guarded_by=<decoy>` as false proof.

    `only_one_active_taxonomy` (below) makes the state unreachable through
    the ORM and the admin alike. This exception is the fail-CLOSED half:
    if the invariant is somehow violated anyway (raw SQL, a restored dump
    predating the constraint, a `--skip-checks` data migration), `active()`
    must NOT silently pick one of them — "which taxonomy guarded this run"
    would then be a coin flip recorded as evidence. Raising makes the run
    FAIL with zero provider calls instead, which is the only honest
    outcome.
    """


class Taxonomy(models.Model):
    """A versioned closed label list. Immutable once a run references it."""

    vocabulary_id = models.CharField(max_length=50)
    version = models.CharField(max_length=20)
    notes = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)
    source_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "taxonomies"
        constraints = [
            models.UniqueConstraint(
                fields=["vocabulary_id", "version"], name="unique_taxonomy_version"
            ),
            # STRUCTURAL, not a `save()` override: "at most one active
            # taxonomy" is the invariant `active()` — and therefore every
            # unborrowed ad-hoc variant-A run's guard binding — rests on.
            # A `save()` override is bypassed by `QuerySet.update()`,
            # `bulk_create`, `bulk_update`, loaddata and raw SQL alike (and
            # `import_taxonomy --activate` uses `.update()` for exactly the
            # deactivation half of this); a partial unique index is checked
            # by the DATABASE on every write, whichever route it came
            # through. Portable across both backends this project runs on:
            # a `UniqueConstraint` with a `condition` compiles to a partial
            # unique index, which PostgreSQL 17 (production) and SQLite
            # (tests, dev) both support natively — no `expressions`, no
            # `nulls_distinct`, none of the backend-specific features whose
            # SQLite-vs-PostgreSQL divergence has bitten this codebase
            # before. Django 4.1+ also validates it in `full_clean()`/
            # `validate_constraints()`, so `TaxonomyAdmin`'s editable
            # `is_active` checkbox surfaces `violation_error_message` as an
            # ordinary form error rather than an IntegrityError 500 —
            # activating a second taxonomy now means deactivating the first
            # one, deliberately, in its own step.
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="only_one_active_taxonomy",
                violation_error_message=(
                    "Another taxonomy is already active, and exactly one may be. "
                    "Variant A's leak backstop scans against whichever taxonomy is "
                    "active, so 'which one guards an unborrowed run' must never be "
                    "ambiguous. Deactivate the current one first, or use "
                    "`import_taxonomy --activate`, which does both in one step."
                ),
            ),
        ]
        ordering = ["vocabulary_id", "version"]

    def __str__(self):
        return f"{self.vocabulary_id} v{self.version}"

    @classmethod
    def active(cls):
        """The one active taxonomy, or `None` when none is active.

        Fails CLOSED rather than picking one when the
        `only_one_active_taxonomy` invariant above is somehow violated
        anyway — see `MultipleActiveTaxonomies`' own docstring. The old
        implementation was `...order_by("-created_at").first()`, which
        turned "two rows are active" into "the newer one wins", silently:
        that is precisely how a 1-row decoy could re-bind every unborrowed
        variant-A run's guard taxonomy and still archive
        `leak_scan_ran=True` as proof.

        Sliced to 2: enough to detect the violation, never a full table
        scan of every version ever imported.
        """
        rows = list(cls.objects.filter(is_active=True).order_by("-created_at")[:2])
        if len(rows) > 1:
            raise MultipleActiveTaxonomies(
                f"{cls.objects.filter(is_active=True).count()} taxonomies are flagged "
                "active at once, so there is no single guard taxonomy an unborrowed "
                "run could be bound to. Refusing to guess which one variant A's leak "
                "backstop should scan against — deactivate all but one "
                "(`only_one_active_taxonomy` normally makes this state unreachable)."
            )
        return rows[0] if rows else None


class HCV(models.Model):
    """One human-centric vulnerability row of a taxonomy."""

    taxonomy = models.ForeignKey(Taxonomy, related_name="hcvs", on_delete=models.CASCADE)
    hcv_id = models.CharField(max_length=20)
    name = models.CharField(max_length=300)
    definition = models.TextField(blank=True, default="")
    # Empty by design for rows the ontology carries no condition class for.
    condition_classes = models.JSONField(default=list, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "HCV"
        verbose_name_plural = "HCVs"
        constraints = [
            models.UniqueConstraint(fields=["taxonomy", "hcv_id"], name="unique_hcv_per_taxonomy")
        ]
        ordering = ["order", "hcv_id"]

    def __str__(self):
        return f"{self.hcv_id} {self.name}"

    @property
    def has_condition_class(self):
        return bool(self.condition_classes)
