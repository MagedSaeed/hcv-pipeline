import pytest

from llm.client import OpenRouterClient
from llm.fake import FakeLLMClient
from pipeline.clients import get_llm_client, reset_llm_client, set_llm_client

# OpenRouterClient() calls resolve_openrouter_key(), which reads the
# AppSettings row before falling back to settings.OPENROUTER_API_KEY — so any
# test that lets get_llm_client() build a real default needs the db fixture
# and a configured key. Constructing OpenRouterClient() does not touch the
# network (it only configures the OpenAI SDK client), so building a real one
# here is cheap and gives genuine coverage of the construction behaviour
# rather than only asserting on identity/None through the fake.


@pytest.mark.django_db
def test_the_default_client_is_rebuilt_on_every_call(settings):
    """I2: the default must NOT be cached. `OpenRouterClient` resolves the key
    once, at construction, and holds it — so a process-lifetime cache pinned
    whatever key was configured when the first call happened. A long-lived
    qcluster worker would then keep using a revoked key forever, which breaks
    spec §6's promise that a key can be rotated in the running app without a
    redeploy or a container restart. One AppSettings query per LLM call is
    negligible against the network round trip that follows it."""
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-key"

    first = get_llm_client()
    second = get_llm_client()

    assert isinstance(first, OpenRouterClient)
    assert isinstance(second, OpenRouterClient)
    assert first is not second


@pytest.mark.django_db
def test_the_default_client_picks_up_a_rotated_key(settings):
    """The point of rebuilding: the next call uses the key configured now."""
    settings.OPENROUTER_API_KEY = "sk-or-v1-original"
    before = get_llm_client()

    settings.OPENROUTER_API_KEY = "sk-or-v1-rotated"
    after = get_llm_client()

    assert before._api_key == "sk-or-v1-original"
    assert after._api_key == "sk-or-v1-rotated"


def test_set_llm_client_installs_the_given_client():
    """An explicitly installed client IS returned identically, every time —
    that is the seam tests and dry runs depend on."""
    fake = FakeLLMClient()
    set_llm_client(fake)

    assert get_llm_client() is fake
    assert get_llm_client() is fake


@pytest.mark.django_db
def test_reset_llm_client_clears_the_installed_client(settings):
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-key"
    set_llm_client(FakeLLMClient())

    reset_llm_client()

    rebuilt = get_llm_client()
    assert isinstance(rebuilt, OpenRouterClient)
