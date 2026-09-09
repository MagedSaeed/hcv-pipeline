"""One place the pipeline gets its LLM client, so tests can swap in the fake."""

from llm.client import OpenRouterClient

_client = None


def get_llm_client():
    """The installed client, or a FRESH default built for this call.

    The default is deliberately not cached. `OpenRouterClient` resolves the
    OpenRouter key once, at construction, and holds it for its lifetime — so a
    process-lifetime cache pins whatever key was configured when the first call
    happened. A long-lived qcluster worker would then keep presenting a revoked
    key indefinitely, which breaks spec §6's promise that a key can be rotated
    in the running app without a redeploy or a container restart. The cost of
    rebuilding is one AppSettings query per LLM call, which is negligible
    against the network round trip that immediately follows it.

    An explicitly installed client (see `set_llm_client`) is always returned as
    it is — that seam is how tests and dry runs stand in for the network, and
    rebuilding over it would defeat the point.
    """
    if _client is not None:
        return _client
    return OpenRouterClient()


def set_llm_client(client):
    """Install a client for the process. Tests use this with FakeLLMClient."""
    global _client
    _client = client


def reset_llm_client():
    global _client
    _client = None
