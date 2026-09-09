import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from corpus.models import FewShotExample, ResourceFile

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def resource(db):
    return ResourceFile.objects.create(name="scenarios-md", text="card content")


@pytest.mark.django_db
def test_inline_text_resolves(db):
    example = FewShotExample(phase=2, name="oldsmar", input_text="system", output_text="scenarios")
    example.full_clean()
    example.save()
    assert example.resolved_input == "system"
    assert example.resolved_output == "scenarios"


@pytest.mark.django_db
def test_file_reference_resolves_at_read_time(resource):
    example = FewShotExample(phase=2, name="from-file", input_text="system", output_file=resource)
    example.full_clean()
    example.save()
    assert example.resolved_output == "card content"

    resource.text = "edited content"
    resource.save()
    assert FewShotExample.objects.get(pk=example.pk).resolved_output == "edited content"


@pytest.mark.django_db
def test_both_sources_on_one_side_is_rejected(resource):
    example = FewShotExample(
        phase=2,
        name="ambiguous",
        input_text="system",
        output_text="inline",
        output_file=resource,
    )
    with pytest.raises(ValidationError, match="exactly one"):
        example.full_clean()


@pytest.mark.django_db
def test_neither_source_on_one_side_is_rejected():
    example = FewShotExample(phase=2, name="empty", input_text="system")
    with pytest.raises(ValidationError, match="exactly one"):
        example.full_clean()


@pytest.mark.django_db
def test_api_filters_by_phase(api_client, member):
    FewShotExample.objects.create(phase=2, name="gen", input_text="a", output_text="b")
    FewShotExample.objects.create(phase=3, name="label", input_text="c", output_text="d")
    api_client.force_authenticate(user=member)

    rows = api_client.get("/api/fewshots/?phase=3").json()["results"]
    assert [row["name"] for row in rows] == ["label"]


@pytest.mark.django_db
def test_api_rejects_a_non_integer_phase(api_client, member):
    """Regression: the raw value used to reach the ORM and raise a 500."""
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/fewshots/?phase=abc")

    assert response.status_code == 400
    assert "phase" in response.json()


@pytest.mark.django_db
def test_api_rejects_a_half_empty_pair(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/fewshots/", {"phase": 2, "name": "bad", "input_text": "a"}, format="json"
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_api_rejects_patch_that_clears_the_only_output_source(api_client, member, resource):
    """A PATCH that would leave a side with neither text nor file must be rejected.

    Regression test: FewShotExampleSerializer._existing() must merge the PATCH
    payload against the instance's *current* state so this violates the
    exactly-one-of rule and is caught, rather than silently saving a row whose
    output side renders half empty.
    """
    example = FewShotExample.objects.create(
        phase=2, name="file-backed", input_text="system", output_file=resource
    )
    api_client.force_authenticate(user=member)

    response = api_client.patch(
        f"/api/fewshots/{example.pk}/", {"output_file": None}, format="json"
    )

    assert response.status_code == 400
    example.refresh_from_db()
    assert example.output_file_id == resource.pk
    assert example.output_text == ""


@pytest.mark.django_db
def test_api_rejects_patch_that_adds_a_second_output_source(api_client, member, resource):
    """A PATCH that would leave a side with both text and file must be rejected."""
    example = FewShotExample.objects.create(
        phase=2, name="text-backed", input_text="system", output_text="scenarios"
    )
    api_client.force_authenticate(user=member)

    response = api_client.patch(
        f"/api/fewshots/{example.pk}/", {"output_file": resource.pk}, format="json"
    )

    assert response.status_code == 400
    example.refresh_from_db()
    assert example.output_file_id is None
    assert example.output_text == "scenarios"


@pytest.mark.django_db
def test_name_is_unique_within_a_phase():
    FewShotExample.objects.create(phase=2, name="dup", input_text="a", output_text="b")
    with pytest.raises(IntegrityError):
        FewShotExample.objects.create(phase=2, name="dup", input_text="c", output_text="d")
