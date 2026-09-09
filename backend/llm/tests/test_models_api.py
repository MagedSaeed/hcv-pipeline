import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from llm.models_api import MODEL_CACHE_KEY, fetch_models

User = get_user_model()

CATALOGUE = {
    "data": [
        {
            "id": "anthropic/claude-sonnet-4",
            "name": "Claude Sonnet 4",
            "pricing": {"prompt": "0.000003"},
        },
        {"id": "openai/gpt-5", "name": "GPT-5", "pricing": {"prompt": "0.00001"}},
    ]
}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def key(settings, db):
    """fetch_models resolves the key, which reads AppSettings — so these touch the DB."""
    settings.OPENROUTER_API_KEY = "sk-or-test"
    return settings.OPENROUTER_API_KEY


@pytest.mark.django_db
def test_fetch_returns_the_catalogue_rows(key):
    rows = fetch_models(fetcher=lambda key: CATALOGUE)
    assert [row["id"] for row in rows] == ["anthropic/claude-sonnet-4", "openai/gpt-5"]


@pytest.mark.django_db
def test_the_second_fetch_is_served_from_cache(key):
    calls = []

    def fetcher(api_key):
        calls.append(api_key)
        return CATALOGUE

    fetch_models(fetcher=fetcher)
    fetch_models(fetcher=fetcher)
    assert len(calls) == 1


@pytest.mark.django_db
def test_a_fetch_failure_does_not_poison_the_cache(key):
    def broken(api_key):
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        fetch_models(fetcher=broken)
    assert cache.get(MODEL_CACHE_KEY) is None


@pytest.mark.django_db
def test_endpoint_requires_authentication(api_client):
    assert api_client.get("/api/models/").status_code == 403


@pytest.mark.django_db
def test_endpoint_returns_the_catalogue(api_client, member, settings, monkeypatch):
    settings.OPENROUTER_API_KEY = "sk-or-test"
    monkeypatch.setattr("llm.models_api._fetch_from_openrouter", lambda key: CATALOGUE)
    api_client.force_authenticate(user=member)

    body = api_client.get("/api/models/").json()
    assert body["count"] == 2
    assert body["results"][0]["id"] == "anthropic/claude-sonnet-4"


@pytest.mark.django_db
def test_endpoint_reports_a_missing_key_clearly(api_client, member, settings):
    settings.OPENROUTER_API_KEY = ""
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/models/")
    assert response.status_code == 503
    assert "No OpenRouter API key" in str(response.json())


@pytest.mark.django_db
def test_an_empty_catalogue_is_not_cached(key):
    """A degraded reply with no models and no error must not blank the picker
    for the full hour — the same reason a failed fetch is not cached."""
    payloads = [{"data": []}, CATALOGUE]

    def fetcher(api_key):
        return payloads.pop(0)

    assert fetch_models(fetcher=fetcher) == []
    assert cache.get(MODEL_CACHE_KEY) is None

    rows = fetch_models(fetcher=fetcher)
    assert [row["id"] for row in rows] == ["anthropic/claude-sonnet-4", "openai/gpt-5"]


@pytest.mark.django_db
def test_a_non_object_payload_degrades_to_no_rows(key):
    """`payload.get(...)` on a bare list raises AttributeError, which reaches a
    view as an unexplained 500. A malformed body is data: no rows."""
    assert fetch_models(fetcher=lambda api_key: ["not", "an", "object"]) == []
    assert cache.get(MODEL_CACHE_KEY) is None


@pytest.mark.django_db
def test_a_payload_without_a_data_key_degrades_to_no_rows(key):
    assert fetch_models(fetcher=lambda api_key: {"error": "upstream is unhappy"}) == []


@pytest.mark.django_db
def test_endpoint_reports_an_upstream_failure_as_a_502(api_client, member, settings, monkeypatch):
    """A timeout or a DNS failure is the upstream being unreachable, not a bug
    in this service: 502 naming the cause, not a bare 500."""

    def broken(api_key):
        raise RuntimeError("upstream connection timed out")

    settings.OPENROUTER_API_KEY = "sk-or-test"
    monkeypatch.setattr("llm.models_api._fetch_from_openrouter", broken)
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/models/")
    assert response.status_code == 502
    assert "upstream connection timed out" in str(response.json())


@pytest.mark.django_db
def test_endpoint_survives_a_non_object_payload(api_client, member, settings, monkeypatch):
    settings.OPENROUTER_API_KEY = "sk-or-test"
    monkeypatch.setattr("llm.models_api._fetch_from_openrouter", lambda key: ["junk"])
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/models/")
    assert response.status_code == 200
    assert response.json() == {"count": 0, "results": []}
