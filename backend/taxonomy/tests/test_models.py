import pytest
from django.db import IntegrityError

from taxonomy.models import HCV, Taxonomy


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
