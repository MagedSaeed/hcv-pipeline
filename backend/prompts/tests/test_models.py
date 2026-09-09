import pytest
from django.db import IntegrityError

from prompts.models import PromptTemplate


@pytest.fixture
def first(db):
    return PromptTemplate.objects.create_version(
        phase=2, name="generate", user_prompt="Write {{ n }} scenarios."
    )


@pytest.mark.django_db
def test_first_version_is_one(first):
    assert first.version == 1


@pytest.mark.django_db
def test_each_create_version_increments(first):
    second = PromptTemplate.objects.create_version(phase=2, name="generate", user_prompt="Changed.")
    assert second.version == 2
    assert PromptTemplate.objects.filter(phase=2, name="generate").count() == 2


@pytest.mark.django_db
def test_versions_are_per_name_and_phase(first):
    other_name = PromptTemplate.objects.create_version(phase=2, name="other", user_prompt="x")
    other_phase = PromptTemplate.objects.create_version(phase=3, name="generate", user_prompt="x")
    assert other_name.version == 1
    assert other_phase.version == 1


@pytest.mark.django_db
def test_new_version_copies_forward_and_applies_changes(first):
    second = first.new_version(user_prompt="Rewritten.")

    assert second.version == 2
    assert second.user_prompt == "Rewritten."
    assert second.name == first.name
    assert second.phase == first.phase
    first.refresh_from_db()
    assert first.user_prompt == "Write {{ n }} scenarios."


@pytest.mark.django_db
def test_latest_returns_the_highest_version(first):
    first.new_version(user_prompt="v2")
    third = first.new_version(user_prompt="v3")
    assert PromptTemplate.latest(2, "generate").pk == third.pk


@pytest.mark.django_db
def test_latest_is_none_when_nothing_exists(db):
    assert PromptTemplate.latest(2, "absent") is None


@pytest.mark.django_db
def test_a_duplicate_version_is_refused(first):
    with pytest.raises(IntegrityError):
        PromptTemplate.objects.create(phase=2, name="generate", version=1, user_prompt="clash")


@pytest.mark.django_db
def test_str_names_the_version(first):
    assert str(first) == "[phase 2] generate v1"


@pytest.mark.django_db
def test_new_version_deep_copies_output_schema(first):
    first.output_schema = {"type": "object", "properties": {"a": 1}}
    first.save()

    second = first.new_version()

    assert second.output_schema is not first.output_schema
    second.output_schema["properties"]["a"] = 2
    first.refresh_from_db()
    assert first.output_schema == {"type": "object", "properties": {"a": 1}}


@pytest.mark.django_db
def test_new_version_always_starts_inactive(first):
    first.is_active = True
    first.save()

    second = first.new_version()

    assert second.is_active is False
    first.refresh_from_db()
    assert first.is_active is True


@pytest.mark.django_db
def test_new_version_can_fork_name_or_phase(first):
    forked_name = first.new_version(name="other")
    forked_phase = first.new_version(phase=3)

    assert forked_name.version == 1
    assert forked_name.phase == first.phase
    assert forked_phase.version == 1
    assert forked_phase.name == first.name
    first.refresh_from_db()
    assert first.name == "generate"
    assert first.phase == 2
