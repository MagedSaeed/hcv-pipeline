import pytest

from pipeline.clients import reset_llm_client


@pytest.fixture(autouse=True)
def _reset_llm_client_seam():
    """Make the client seam safe by construction, not by every test remembering.

    `pipeline.clients` caches its installed client in module-level global
    state. Tasks 3-5 install `FakeLLMClient` through `set_llm_client()`; a
    test that does so and forgets to call `reset_llm_client()` would leak
    that fake into every later test in the same process, surfacing as a
    baffling failure in an unrelated test rather than at its actual cause.
    Resetting here after every test in this package closes that hole
    regardless of whether an individual test's own fixtures also reset —
    harmless belt-and-braces if they do.
    """
    yield
    reset_llm_client()


@pytest.fixture(autouse=True)
def _configured_openrouter_key(settings):
    """Give every pipeline test a key, because `launch` now demands one.

    `orchestration.launch()` resolves the OpenRouter key before it creates a
    single row, so that a run with no key configured fails before anything
    exists rather than stranding a half-built repetition (spec §12). That
    makes a configured key a precondition of almost every test in this
    package, so it is supplied here once rather than in each fixture. A test
    that is specifically about the missing-key path clears it explicitly in
    its own body, which runs after this.
    """
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-key"
