import pytest

from core.models import AppSettings
from pipeline.models import LLMCall, PhaseRun
from pipeline.redaction import REDACTED, scrub, secrets_to_scrub
from prompts.models import PromptTemplate
from taxonomy.models import Taxonomy

SENTINEL = "sk-or-v1-SENTINELSENTINEL"


@pytest.fixture
def key(db, settings):
    settings.OPENROUTER_API_KEY = SENTINEL
    return SENTINEL


@pytest.fixture
def phase_run(db, key):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    template = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    return PhaseRun.objects.create(
        phase=2, variant="A", model="m", prompt_template=template, taxonomy=tax
    )


@pytest.mark.django_db
def test_the_configured_key_is_collected(key):
    assert SENTINEL in secrets_to_scrub()


@pytest.mark.django_db
def test_a_stored_key_is_collected_too(key):
    config = AppSettings.load()
    config.openrouter_key = "sk-or-stored-value"
    config.save()
    assert "sk-or-stored-value" in secrets_to_scrub()


@pytest.mark.django_db
def test_missing_keys_do_not_break_collection(settings):
    settings.OPENROUTER_API_KEY = ""
    assert secrets_to_scrub() == []


def test_scrub_replaces_in_a_nested_structure():
    payload = {"a": [{"b": f"Bearer {SENTINEL}"}], "c": SENTINEL}
    cleaned = scrub(payload, [SENTINEL])
    assert cleaned == {"a": [{"b": f"Bearer {REDACTED}"}], "c": REDACTED}


def test_scrub_leaves_clean_data_untouched():
    payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert scrub(payload, [SENTINEL]) == payload


def test_scrub_handles_non_string_leaves():
    payload = {"n": 1, "f": 1.5, "b": True, "none": None}
    assert scrub(payload, [SENTINEL]) == payload


def test_scrub_with_no_secrets_still_scrubs_key_shaped_strings():
    """M5: this used to assert a pure passthrough. It is no longer one — the
    pattern scrub runs whether or not any key is currently configured, which is
    the whole point of it (a rotated or foreign key matches no configured
    value). Ordinary content is still returned untouched; see the test above."""
    payload = {"c": SENTINEL}
    assert scrub(payload, []) == {"c": REDACTED}


@pytest.mark.django_db
def test_a_key_in_any_archived_column_is_scrubbed_on_save(phase_run):
    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        request_json={"messages": [{"content": SENTINEL}]},
        response_json={"error": {"message": f"bad key {SENTINEL}"}},
        raw_response=f"leaked {SENTINEL}",
        transport_error=f"AuthError with {SENTINEL}",
    )
    call.refresh_from_db()

    blob = f"{call.request_json}{call.response_json}{call.raw_response}{call.transport_error}"
    assert SENTINEL not in blob
    assert REDACTED in call.raw_response
    assert REDACTED in call.transport_error


@pytest.mark.django_db
def test_parse_error_and_unknown_hcv_ids_are_scrubbed_too(phase_run):
    """A provider that returns something key-shaped where a string was
    expected puts that value into pydantic's validation message via
    `input_value=...` — landing in `parse_error`, not `raw_response`. These
    two columns were missed by an earlier version of `redact_call` even
    though `export.py` ships both verbatim.
    """
    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        parse_error=f"1 validation error: input_value='{SENTINEL}'",
        unknown_hcv_ids=[SENTINEL, "HCV-99"],
    )
    call.refresh_from_db()

    assert SENTINEL not in call.parse_error
    assert REDACTED in call.parse_error
    assert SENTINEL not in call.unknown_hcv_ids
    assert REDACTED in call.unknown_hcv_ids
    assert "HCV-99" in call.unknown_hcv_ids


@pytest.mark.django_db
def test_stray_scenarios_is_scrubbed_too(phase_run):
    """`stray_scenarios` (the redundant top-level `scenarios` list recorded
    when `pipeline.generation._redundant_top_level_scenarios` fires) is a
    verbatim slice of provider output, the same shape of risk as
    `unknown_hcv_ids` — a scenario card is free-text, so nothing stops a
    provider from putting a key-shaped string in one of its fields.
    """
    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        stray_scenarios=[{"scenario_id": "sc_01", "threat": f"leaked {SENTINEL}"}],
    )
    call.refresh_from_db()

    blob = str(call.stray_scenarios)
    assert SENTINEL not in blob
    assert REDACTED in blob
    assert call.stray_scenarios[0]["scenario_id"] == "sc_01"


@pytest.mark.django_db
def test_model_and_finish_reason_are_scrubbed_too(phase_run):
    """Both are read straight off provider response JSON before this
    function ever sees them — the same class of risk as `parse_error`.
    """
    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        model=f"echo: {SENTINEL}",
        finish_reason=f"stop ({SENTINEL})",
    )
    call.refresh_from_db()

    assert SENTINEL not in call.model
    assert REDACTED in call.model
    assert SENTINEL not in call.finish_reason
    assert REDACTED in call.finish_reason


@pytest.mark.django_db
def test_redaction_does_not_mangle_a_clean_call(phase_run):
    body = {"id": "gen-1", "choices": [{"message": {"content": "hello"}}]}
    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        request_json={"model": "m"},
        response_json=body,
        raw_response="hello",
    )
    call.refresh_from_db()
    assert call.response_json == body
    assert call.raw_response == "hello"


@pytest.mark.django_db
def test_a_foreign_key_is_scrubbed_even_though_it_is_not_the_configured_one(phase_run):
    """M5: `secrets_to_scrub` only knows the two CURRENTLY-configured values. A
    key that has since been rotated, or a collaborator's key echoed back inside
    a provider error body, matched neither and reached the export intact. Spec
    §6 has the storage layer assert that no stored payload matches `sk-or-`, so
    the shape is scrubbed as well as the exact values."""
    foreign = "sk-or-v1-SOMEONEELSESKEY"
    assert foreign not in secrets_to_scrub()

    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        request_json={"model": "m"},
        response_json={"error": {"message": f"invalid api key {foreign}"}},
        transport_error=f"AuthenticationError: {foreign} is not valid",
    )
    call.refresh_from_db()

    assert foreign not in f"{call.response_json}{call.transport_error}"
    assert REDACTED in call.transport_error
    assert REDACTED in call.response_json["error"]["message"]


def test_scrub_replaces_a_key_shaped_string_with_no_configured_secrets():
    """The pattern scrub does not depend on a key being configured at all."""
    payload = {"c": "sk-or-v1-ROTATEDAWAY", "note": "unrelated text"}
    assert scrub(payload, []) == {"c": REDACTED, "note": "unrelated text"}


def test_scrub_does_not_eat_ordinary_words_that_merely_contain_the_key_shape():
    """`KEY_PATTERN` used to have no LEFT boundary, so it matched INSIDE
    ordinary words: "a risk-or-benefit tradeoff" became
    "a ri[REDACTED] tradeoff" — and this project's prompts are about security
    and risk, so "risk-or-" is a phrase this corpus can plausibly contain,
    not a contrived string. A real key alongside must still be caught in the
    same pass.
    """
    payload = {
        "clean": "a risk-or-benefit tradeoff",
        "also_clean": "ask-or-tell is a real phrase",
        "dirty": f"the key is {SENTINEL}",
    }
    cleaned = scrub(payload, [SENTINEL])
    assert cleaned["clean"] == "a risk-or-benefit tradeoff"
    assert cleaned["also_clean"] == "ask-or-tell is a real phrase"
    assert cleaned["dirty"] == f"the key is {REDACTED}"


def test_scrub_still_catches_a_real_key_at_the_very_start_of_a_string():
    """The left boundary must not turn into a false negative: a key with
    nothing before it (string start) has no preceding character to check,
    which a naive lookbehind could mishandle."""
    assert scrub("sk-or-v1-ROTATEDAWAY", []) == REDACTED


@pytest.mark.django_db
def test_a_prompt_survives_a_round_trip_through_save_byte_identical_when_it_merely_looks_key_shaped(
    phase_run,
):
    """The whole point of archiving a prompt verbatim is that it STAYS
    verbatim — this exercises the exact columns `redact_call` scrubs
    (`rendered_system_prompt`, `rendered_user_prompt`, `raw_response`)
    through a real `.save()`, not just `scrub()` in isolation.
    """
    text = "Assess the risk-or-benefit tradeoff of this ask-or-tell disclosure."
    call = LLMCall.objects.create(
        phase_run=phase_run,
        sequence=1,
        rendered_system_prompt=text,
        rendered_user_prompt=text,
        raw_response=text,
    )
    call.refresh_from_db()
    assert call.rendered_system_prompt == text
    assert call.rendered_user_prompt == text
    assert call.raw_response == text


def test_scrub_also_scrubs_dict_keys_not_just_values():
    """`response_json` is `json.loads`'d straight from the provider's raw
    body — nothing guarantees a key-shaped string never lands in a JSON
    object KEY rather than a value, and `scrub`'s job is not to assume a
    shape is safe just because it usually is.
    """
    payload = {SENTINEL: "value", "clean_key": "clean_value"}
    cleaned = scrub(payload, [SENTINEL])
    assert SENTINEL not in cleaned
    assert REDACTED in cleaned
    assert cleaned[REDACTED] == "value"
    assert cleaned["clean_key"] == "clean_value"
