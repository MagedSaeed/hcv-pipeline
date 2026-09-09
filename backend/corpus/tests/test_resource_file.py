import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from corpus.models import FewShotExample, ResourceFile

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


def upload(name, body):
    return SimpleUploadedFile(name, body.encode("utf-8"), content_type="text/plain")


@pytest.mark.django_db
def test_upload_extracts_text(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/resources/",
        {
            "name": "oldsmar-scenarios",
            "kind": "scenarios",
            "file": upload("scenarios.md", "# sc_01\n\nbody text"),
        },
        format="multipart",
    )
    assert response.status_code == 201
    assert response.json()["text"] == "# sc_01\n\nbody text"
    assert ResourceFile.objects.get().uploaded_by == member


@pytest.mark.django_db
def test_text_may_be_supplied_without_a_file(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/resources/",
        {"name": "inline", "kind": "other", "text": "pasted content"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["text"] == "pasted content"


@pytest.mark.django_db
def test_binary_upload_is_rejected(api_client, member):
    api_client.force_authenticate(user=member)
    binary = SimpleUploadedFile("image.png", b"\x89PNG\r\n\x1a\n\x00\xff", content_type="image/png")
    response = api_client.post(
        "/api/resources/",
        {"name": "bad", "kind": "other", "file": binary},
        format="multipart",
    )
    assert response.status_code == 400
    assert "UTF-8 text" in str(response.json())


@pytest.mark.django_db
def test_name_is_unique(api_client, member):
    api_client.force_authenticate(user=member)
    payload = {"name": "dup", "kind": "other", "text": "a"}
    assert api_client.post("/api/resources/", payload, format="json").status_code == 201
    assert api_client.post("/api/resources/", payload, format="json").status_code == 400


@pytest.mark.django_db
def test_resources_require_authentication(api_client):
    assert api_client.get("/api/resources/").status_code == 403


@pytest.mark.django_db
def test_oversized_upload_is_rejected(api_client, member):
    api_client.force_authenticate(user=member)
    huge = SimpleUploadedFile("big.md", b"x" * (2 * 1024 * 1024 + 1), content_type="text/plain")
    response = api_client.post(
        "/api/resources/",
        {"name": "too-big", "kind": "other", "file": huge},
        format="multipart",
    )
    assert response.status_code == 400
    assert "2048 KB" in str(response.json())


@pytest.mark.django_db
def test_uploaded_by_cannot_be_set_by_the_client(api_client, member):
    other = User.objects.create_user(email="other@example.com", password="pw-123456")
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/resources/",
        {"name": "spoofed", "kind": "other", "text": "a", "uploaded_by": other.id},
        format="json",
    )
    assert response.status_code == 201
    assert ResourceFile.objects.get().uploaded_by == member


@pytest.mark.django_db
def test_deleting_a_resource_a_fewshot_uses_is_refused_cleanly(api_client, member):
    """The reference is PROTECTed at the database level and DRF has no handler
    for ProtectedError, so this used to be a 500 — and `seed_corpus` creates
    exactly this shape, so it is the first delete an admin meets."""
    resource = ResourceFile.objects.create(name="oldsmar-scenarios", text="cards")
    FewShotExample.objects.create(
        phase=2, name="oldsmar", input_text="system", output_file=resource
    )
    api_client.force_authenticate(user=member)

    response = api_client.delete(f"/api/resources/{resource.id}/")

    assert response.status_code == 400
    assert "oldsmar" in str(response.json())
    assert ResourceFile.objects.filter(pk=resource.pk).exists()


@pytest.mark.django_db
def test_an_unreferenced_resource_can_still_be_deleted(api_client, member):
    resource = ResourceFile.objects.create(name="stray", text="unused")
    api_client.force_authenticate(user=member)

    assert api_client.delete(f"/api/resources/{resource.id}/").status_code == 204
    assert not ResourceFile.objects.filter(pk=resource.pk).exists()
