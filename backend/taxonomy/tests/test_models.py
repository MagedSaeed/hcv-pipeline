import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection

from taxonomy.models import HCV, MultipleActiveTaxonomies, Taxonomy


@pytest.fixture
def taxonomy(db):
    return Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1")


@pytest.mark.django_db
def test_hcv_id_is_unique_within_a_taxonomy(taxonomy):
    HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-01", name="A", order=1)
    with pytest.raises(IntegrityError):
        HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-01", name="B", order=2)


@pytest.mark.django_db
def test_the_same_hcv_id_may_exist_in_another_version(taxonomy):
    other = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.2")
    HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-01", name="A", order=1)
    HCV.objects.create(taxonomy=other, hcv_id="HCV-01", name="A", order=1)
    assert HCV.objects.filter(hcv_id="HCV-01").count() == 2


@pytest.mark.django_db
def test_condition_classes_defaults_to_an_empty_list(taxonomy):
    row = HCV.objects.create(taxonomy=taxonomy, hcv_id="HCV-09", name="Propaganda", order=9)
    assert row.condition_classes == []
    assert row.has_condition_class is False


@pytest.mark.django_db
def test_has_condition_class_is_true_when_populated(taxonomy):
    row = HCV.objects.create(
        taxonomy=taxonomy,
        hcv_id="HCV-07",
        name="Malicious Insiders",
        condition_classes=["UnconstrainedInsiderAccessCondition"],
        order=7,
    )
    assert row.has_condition_class is True


@pytest.mark.django_db
def test_hcvs_come_back_in_taxonomy_order(taxonomy):
    for index in (3, 1, 2):
        HCV.objects.create(taxonomy=taxonomy, hcv_id=f"HCV-0{index}", name=str(index), order=index)
    assert [row.order for row in taxonomy.hcvs.all()] == [1, 2, 3]


@pytest.mark.django_db
def test_active_returns_the_flagged_taxonomy(taxonomy):
    Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.2", is_active=True)
    assert Taxonomy.active().version == "1.2"


@pytest.mark.django_db
def test_only_one_taxonomy_may_be_active(taxonomy):
    """Round 4, leak 2. With no experiment borrowed, an ad-hoc variant-A
    run's guard taxonomy IS `Taxonomy.active()`
    (`pipeline.models.unbound_guard_taxonomy_error`), so "which taxonomy is
    active" decides which list the leak backstop compares a prompt against.
    Nothing used to enforce that only one row could hold the flag, and
    `active()` simply took the newest — so a fresh 1-row decoy silently
    re-bound every unborrowed run to itself, and the scan then ran against
    the wrong list and archived `leak_scan_ran=True` as proof.

    Enforced by the DATABASE (a partial unique index) rather than a
    `save()` override, so it survives `QuerySet.update()`, `bulk_create`,
    `loaddata` and raw SQL — `import_taxonomy --activate` uses `.update()`
    for the deactivation half, which a `save()` override would never see.
    """
    taxonomy.is_active = True
    taxonomy.save(update_fields=["is_active"])

    with pytest.raises(IntegrityError):
        Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.3", is_active=True)


@pytest.mark.django_db
def test_deactivating_first_then_activating_another_is_allowed(taxonomy):
    """The contrast case: switching which taxonomy is active is a real,
    supported workflow — the constraint refuses two AT ONCE, not the switch
    itself. Without this, the test above would also pass against a rule
    that simply forbade activation entirely."""
    taxonomy.is_active = True
    taxonomy.save(update_fields=["is_active"])

    taxonomy.is_active = False
    taxonomy.save(update_fields=["is_active"])
    newer = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.3", is_active=True)

    assert Taxonomy.active() == newer


@pytest.mark.django_db
def test_the_active_constraint_is_validated_by_full_clean(taxonomy):
    """Django 4.1+ runs `validate_constraints()` from `full_clean()`, which
    is what turns the database constraint into an ordinary FORM error in
    `TaxonomyAdmin` (where `is_active` stays deliberately editable) instead
    of an IntegrityError 500. The `violation_error_message` is written for
    an operator and names the way out, so it is worth pinning that it is
    what actually surfaces."""
    taxonomy.is_active = True
    taxonomy.save(update_fields=["is_active"])
    other = Taxonomy(vocabulary_id="HCV-21", version="1.3", is_active=True)

    with pytest.raises(ValidationError, match="already active"):
        other.full_clean()


@pytest.mark.django_db(transaction=True)
def test_active_refuses_to_guess_when_the_invariant_is_violated():
    """`active()` fails CLOSED. If two rows are somehow both active — a dump
    restored from before `only_one_active_taxonomy` existed, raw SQL, a
    `--skip-checks` data migration — returning "the newer one" would make
    "which taxonomy guarded this run" a coin flip recorded as evidence.

    The constraint is genuinely DROPPED and restored around the probe
    rather than mocked away: a mocked queryset would only prove the mock
    works, and the point is what `active()` does against a real database in
    a state the constraint normally makes unreachable. `transaction=True`
    because SQLite's schema editor cannot run inside an already-open atomic
    block. `pipeline/tests/test_guard_evidence.py` carries the end-to-end
    half — that a run in this state FAILS with zero provider calls rather
    than completing with a guessed guard.
    """
    constraint = next(c for c in Taxonomy._meta.constraints if c.name == "only_one_active_taxonomy")
    Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)

    with connection.schema_editor(atomic=False) as editor:
        editor.remove_constraint(Taxonomy, constraint)
    try:
        Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.2", is_active=True)
        assert Taxonomy.objects.filter(is_active=True).count() == 2

        with pytest.raises(MultipleActiveTaxonomies, match="2 taxonomies"):
            Taxonomy.active()
    finally:
        Taxonomy.objects.filter(version="1.2").update(is_active=False)
        with connection.schema_editor(atomic=False) as editor:
            editor.add_constraint(Taxonomy, constraint)
