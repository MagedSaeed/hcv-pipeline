import inspect
import json

import pytest
from openai.resources.chat.completions import Completions

from llm.client import (
    DEFAULT_TIMEOUT,
    LLMClient,
    LLMResult,
    OpenRouterClient,
    SecretLeak,
    build_request,
    split_request_for_the_sdk,
)
from llm.fake import FakeLLMClient
from llm.schemas import strict_schema

SENTINEL_KEY = "sk-or-v1-SENTINELSENTINELSENTINEL"

BODY = {
    "id": "gen-abc123",
    "model": "anthropic/claude-sonnet-4",
    "provider": "Anthropic",
    "choices": [
        {"finish_reason": "stop", "message": {"role": "assistant", "content": '{"labels": []}'}}
    ],
    # OpenRouter puts per-call cost on `usage.cost`. `total_cost` belongs to the
    # separate generation endpoint; a fixture using it here would have made the
    # implementation's wrong key look correct.
    "usage": {"prompt_tokens": 120, "completion_tokens": 8, "cost": 0.00042},
}


class StubCompletions:
    """Stands in for openai's chat.completions.with_raw_response."""

    def __init__(self, body=None, error=None, status=200):
        self.body, self.error, self.status = body, error, status
        self.seen = None

    def create(self, **kwargs):
        self.seen = kwargs
        if self.error is not None:
            raise self.error
        return StubRaw(self.body, self.status)


class StubRaw:
    def __init__(self, body, status):
        self.text = json.dumps(body)
        self.status_code = status


class FakeAPIStatusError(Exception):
    """Stands in for openai's APIStatusError, which every non-2xx raises
    (verified against the openai 3.8.0 source: APIStatusError.__init__ sets
    `self.status_code = response.status_code`) *before* a raw response is
    ever returned — so a 200 is the only status a raw-response stub can carry.
    """

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


def raw_response_stub(text, status=200):
    """A create() stub returning a raw response whose body is `text` verbatim."""

    class Raw:
        pass

    raw = Raw()
    raw.text = text
    raw.status_code = status

    class Stub:
        seen = None

        def create(self, **kwargs):
            return raw

    return Stub()


def client_with(stub, key=SENTINEL_KEY):
    client = OpenRouterClient(api_key=key)
    client._create = stub.create  # the single seam onto the SDK
    return client


def test_a_successful_call_projects_every_column():
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(
        model="anthropic/claude-sonnet-4",
        system_prompt="sys",
        user_prompt="usr",
        temperature=0.2,
    )

    assert isinstance(result, LLMResult)
    assert result.ok is True
    assert result.raw_response == '{"labels": []}'
    assert result.response_json["id"] == "gen-abc123"
    assert result.model == "anthropic/claude-sonnet-4"
    assert result.finish_reason == "stop"
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 8
    assert result.cost == pytest.approx(0.00042)
    assert result.latency_ms >= 0
    assert result.http_status == 200
    assert result.transport_error == ""


def test_the_whole_response_body_is_kept_verbatim():
    """Anything not modelled as a column stays recoverable from the dump."""
    result = client_with(StubCompletions(BODY)).complete(
        model="m", system_prompt="", user_prompt="u"
    )
    assert result.response_json["provider"] == "Anthropic"
    assert result.response_json == BODY


def test_the_request_body_is_recorded_without_the_key():
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(
        model="m", system_prompt="sys", user_prompt="usr", temperature=0.7
    )

    assert result.request_json["model"] == "m"
    assert result.request_json["temperature"] == 0.7
    assert result.request_json["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    blob = json.dumps(result.request_json)
    assert SENTINEL_KEY not in blob
    assert "sk-or-" not in blob
    assert "Authorization" not in blob


def test_an_empty_system_prompt_sends_no_system_message():
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")
    assert result.request_json["messages"] == [{"role": "user", "content": "u"}]


def test_a_json_schema_is_sent_in_strict_shape_without_the_strict_flag():
    """OpenAI's newer models 400 unless every object is closed and every field
    required; the `strict` flag itself isn't needed for that."""
    stub = StubCompletions(BODY)
    schema = {"type": "object", "properties": {"labels": {"type": "array"}}}
    client_with(stub).complete(model="m", system_prompt="", user_prompt="u", json_schema=schema)

    envelope = stub.seen["response_format"]["json_schema"]
    assert stub.seen["response_format"]["type"] == "json_schema"
    assert envelope["schema"] == {
        "type": "object",
        "properties": {"labels": {"type": "array"}},
        "additionalProperties": False,
        "required": ["labels"],
    }
    assert "strict" not in envelope


def test_a_transport_failure_becomes_a_record_not_an_exception():
    stub = StubCompletions(error=RuntimeError("connection reset"))
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.response_json is None
    assert result.raw_response == ""
    assert "connection reset" in result.transport_error
    assert result.request_json["model"] == "m"


def test_a_failed_call_still_records_the_request_without_the_key():
    stub = StubCompletions(error=RuntimeError("boom"))
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")
    assert SENTINEL_KEY not in json.dumps(result.request_json)


def test_a_provider_error_is_recorded_with_its_http_status():
    """Every non-2xx raises in the real SDK; the status must not be lost."""
    stub = StubCompletions(error=FakeAPIStatusError("rate limited", 429))
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.http_status == 429
    assert "rate limited" in result.transport_error


def test_a_502_from_the_provider_arrives_as_a_raised_error_with_its_status():
    """A 502 never reaches the raw-response path in the real SDK — it raises
    APIStatusError before this client ever sees a raw response object."""
    stub = StubCompletions(error=FakeAPIStatusError("502 Bad Gateway", 502))
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.http_status == 502
    assert "502 Bad Gateway" in result.transport_error


def test_a_200_response_whose_body_is_not_json_is_recorded_as_a_transport_error():
    stub = raw_response_stub("<html>not actually json</html>", status=200)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.http_status == 200
    assert "not actually json" in result.transport_error


def test_a_json_body_that_is_a_list_is_recorded_as_a_transport_error():
    stub = raw_response_stub(json.dumps(["not", "an", "object"]), status=200)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.response_json is None
    assert result.http_status == 200


def test_a_json_body_that_is_a_string_is_recorded_as_a_transport_error():
    stub = raw_response_stub(json.dumps("just a string"), status=200)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.response_json is None


def test_a_json_null_body_is_recorded_as_a_transport_error():
    stub = raw_response_stub("null", status=200)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.response_json is None


def test_choices_shaped_as_a_dict_degrades_to_empty_defaults():
    """The body is still archived verbatim; only the projected columns degrade."""
    body = {**BODY, "choices": {"unexpected": "shape"}}
    result = client_with(StubCompletions(body)).complete(
        model="m", system_prompt="", user_prompt="u"
    )

    assert result.ok is True
    assert result.response_json == body
    assert result.raw_response == ""
    assert result.finish_reason == ""


def test_choices_shaped_as_a_string_degrades_to_empty_defaults():
    body = {**BODY, "choices": "not-a-list"}
    result = client_with(StubCompletions(body)).complete(
        model="m", system_prompt="", user_prompt="u"
    )

    assert result.ok is True
    assert result.raw_response == ""
    assert result.finish_reason == ""


def test_usage_shaped_as_a_number_degrades_to_empty_defaults():
    body = {**BODY, "usage": 12345}
    result = client_with(StubCompletions(body)).complete(
        model="m", system_prompt="", user_prompt="u"
    )

    assert result.ok is True
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0
    assert result.cost is None


def test_a_key_appearing_in_a_payload_is_refused_outright():
    """Defence in depth: the storage guard fires even if a caller smuggles one in."""
    client = OpenRouterClient(api_key=SENTINEL_KEY)
    with pytest.raises(SecretLeak):
        client._guard({"messages": [{"role": "user", "content": SENTINEL_KEY}]})


def test_a_key_in_the_system_prompt_is_refused_through_complete_with_no_network_call():
    stub = StubCompletions(BODY)
    client = client_with(stub)
    with pytest.raises(SecretLeak):
        client.complete(model="m", system_prompt=SENTINEL_KEY, user_prompt="u")
    assert stub.seen is None


def test_a_key_in_the_user_prompt_is_refused_through_complete_with_no_network_call():
    stub = StubCompletions(BODY)
    client = client_with(stub)
    with pytest.raises(SecretLeak):
        client.complete(model="m", system_prompt="", user_prompt=SENTINEL_KEY)
    assert stub.seen is None


def test_a_key_in_the_model_name_is_refused_through_complete_with_no_network_call():
    stub = StubCompletions(BODY)
    client = client_with(stub)
    with pytest.raises(SecretLeak):
        client.complete(model=SENTINEL_KEY, system_prompt="", user_prompt="u")
    assert stub.seen is None


# --- `preflight`: the seam a caller plugs its own policy into, same shape as
# `_guard` above but for a check this client itself knows nothing about (see
# pipeline.generation._preflight / prompts.rendering
# .assert_request_not_leaking_taxonomy for the one real caller). These tests
# are deliberately taxonomy-agnostic — they prove the SEAM works (called on
# the final request, before the HTTP call, raise-and-all, with no network
# access if it raises), independent of what any particular preflight checks.


def test_a_preflight_that_raises_is_refused_with_no_network_call():
    stub = StubCompletions(BODY)
    client = client_with(stub)

    def boom(request):
        raise RuntimeError("policy says no")

    with pytest.raises(RuntimeError, match="policy says no"):
        client.complete(model="m", system_prompt="", user_prompt="u", preflight=boom)
    assert stub.seen is None


def test_a_preflight_receives_the_full_final_request_body():
    """The exact dict `request_json` later archives — model, messages, and
    anything `build_request` adds, like `response_format` when a schema is
    pinned — not a partial or pre-processed view of it."""
    stub = StubCompletions(BODY)
    client = client_with(stub)
    schema = {"type": "object", "properties": {"labels": {"type": "array"}}}
    seen = {}

    client.complete(
        model="m",
        system_prompt="sys",
        user_prompt="usr",
        json_schema=schema,
        preflight=seen.update,
    )

    assert seen["model"] == "m"
    assert seen["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
    assert seen["response_format"]["json_schema"]["schema"] == strict_schema(schema)


def test_a_preflight_that_does_not_raise_lets_the_call_through():
    stub = StubCompletions(BODY)
    client = client_with(stub)
    result = client.complete(
        model="m", system_prompt="", user_prompt="u", preflight=lambda request: None
    )
    assert result.ok is True
    assert stub.seen is not None


def test_no_preflight_behaves_exactly_as_before_this_parameter_existed():
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")
    assert result.ok is True


def test_a_key_of_any_shape_is_caught_by_the_identity_check_not_just_the_prefix():
    """The prefix check only catches sk-or-; the client also refuses to record
    its own key verbatim regardless of shape (a rotated key, sk-ant-, etc.),
    because it knows exactly what its own secret looks like."""
    custom_key = "custom-token-does-not-look-like-openrouter-at-all"
    stub = StubCompletions(BODY)
    client = client_with(stub, key=custom_key)
    with pytest.raises(SecretLeak):
        client.complete(model="m", system_prompt=custom_key, user_prompt="u")
    assert stub.seen is None


@pytest.mark.parametrize(
    "text",
    [
        "Assess the risk-or-benefit tradeoff.",
        "The ask-or-tell disclosure norm applies here.",
    ],
)
def test_ordinary_prose_containing_the_key_shape_is_not_refused(text):
    """`_guard` used to test a bare `"sk-or-" in blob`, matching INSIDE
    ordinary words — this project's prompts are about security and risk, so
    "risk-or-benefit"/"ask-or-tell" are plausible real phrases, not
    contrived ones, and a phase run whose prompt happened to contain either
    aborted outright on a spurious `SecretLeak` before ever reaching the
    provider. `pipeline.redaction.KEY_PATTERN` gained a left boundary for
    the identical reason first; `_guard` shares that same pattern now
    (imported from here, not a second hand-written copy) so the two cannot
    drift apart again the way they already did once.
    """
    stub = StubCompletions(BODY)
    client = client_with(stub)
    result = client.complete(model="m", system_prompt="", user_prompt=text)
    assert result.ok
    assert stub.seen is not None


def test_a_real_key_shaped_string_is_still_refused_alongside_ordinary_prose():
    """The boundary fix must not turn into a false negative: a genuine
    `sk-or-...` value sitting right next to ordinary "risk-or-"-shaped text
    in the SAME payload must still raise."""
    stub = StubCompletions(BODY)
    client = client_with(stub)
    with pytest.raises(SecretLeak):
        client.complete(
            model="m",
            system_prompt="Assess the risk-or-benefit tradeoff.",
            user_prompt=f"leaked key: {SENTINEL_KEY}",
        )
    assert stub.seen is None


def test_the_generation_endpoints_total_cost_key_is_still_honoured():
    """OpenRouter's generation endpoint names the same number `total_cost`. If a
    body arrives in that shape the cost is recorded, not dropped."""
    body = {**BODY, "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_cost": 0.5}}
    result = client_with(StubCompletions(body)).complete(
        model="m", system_prompt="", user_prompt="u"
    )

    assert result.cost == pytest.approx(0.5)


def test_a_usage_block_with_neither_cost_key_records_no_cost():
    body = {**BODY, "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    result = client_with(StubCompletions(body)).complete(
        model="m", system_prompt="", user_prompt="u"
    )

    assert result.cost is None


def test_the_request_asks_openrouter_to_return_usage_accounting():
    """Cost only comes back when the request asks for it, so the ask is part of
    every request — otherwise every recorded cost is null on live calls.

    `usage: {"include": True}` is an OpenRouter extension, not a parameter the
    OpenAI SDK's `Completions.create` declares — sending it as a plain keyword
    argument is exactly the production bug (`TypeError: Completions.create()
    got an unexpected keyword argument 'usage'`), so it must arrive on the wire
    through `extra_body`, not as a top-level kwarg to `create()`. The archived
    `request_json` still records the full logical body, `usage` included: it's
    the evidence of what was actually sent, and `extra_body` really does merge
    back into that same JSON body on the wire.
    """
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert "usage" not in stub.seen
    assert stub.seen["extra_body"] == {"usage": {"include": True}}
    assert result.request_json["usage"] == {"include": True}


def test_an_empty_json_schema_sends_no_response_format():
    """PromptTemplate.output_schema defaults to `{}`. Passing that through would
    pin an empty schema on every unschemad template — which some providers
    reject outright — so an empty schema means no schema."""
    stub = StubCompletions(BODY)
    result = client_with(stub).complete(
        model="m", system_prompt="", user_prompt="u", json_schema={}
    )

    assert "response_format" not in stub.seen
    assert "response_format" not in result.request_json


def test_no_json_schema_sends_no_response_format():
    stub = StubCompletions(BODY)
    client_with(stub).complete(model="m", system_prompt="", user_prompt="u")

    assert "response_format" not in stub.seen


def test_the_fake_builds_the_same_request_shape_as_the_real_client():
    """The fake's request_json is the thing a pipeline test asserts against —
    "the schema was pinned into the request". Built separately, that assertion
    could pass against the fake while the real client sent something else."""
    schema = {"type": "object", "properties": {"labels": {"type": "array"}}}
    arguments = {
        "model": "anthropic/claude-sonnet-4",
        "system_prompt": "sys",
        "user_prompt": "usr",
        "temperature": 0.3,
        "json_schema": schema,
    }

    fake = FakeLLMClient()
    fake.queue("ok")
    real = client_with(StubCompletions(BODY)).complete(**arguments)

    assert fake.complete(**arguments).request_json == real.request_json
    assert real.request_json["response_format"]["json_schema"]["schema"] == strict_schema(schema)


def test_the_fake_satisfies_the_llm_client_protocol():
    """They have already drifted once (timeout defaulted to None on the fake and
    to DEFAULT_TIMEOUT on the protocol); nothing caught it."""
    assert isinstance(FakeLLMClient(), LLMClient)
    assert isinstance(OpenRouterClient(api_key=SENTINEL_KEY), LLMClient)


def call_shape(method):
    """Parameter names, kinds and defaults — everything a caller can observe.
    Annotations are excluded deliberately: the protocol declares them and the
    two implementations do not, which is a style difference, not drift."""
    return [
        (name, parameter.kind, parameter.default)
        for name, parameter in inspect.signature(method).parameters.items()
    ]


def test_the_fake_and_the_protocol_agree_on_the_complete_signature():
    """isinstance() against a runtime_checkable Protocol only checks that the
    method exists — it says nothing about its parameters, which is exactly where
    the drift was (timeout defaulted to None on the fake, DEFAULT_TIMEOUT on the
    protocol). So compare what a caller can actually see."""
    expected = call_shape(LLMClient.complete)

    assert call_shape(FakeLLMClient.complete) == expected
    assert call_shape(OpenRouterClient.complete) == expected


def test_the_fake_defaults_its_timeout_to_the_shared_default():
    assert (
        inspect.signature(FakeLLMClient.complete).parameters["timeout"].default == DEFAULT_TIMEOUT
    )


# --- Contract: every key build_request() produces is deliverable to the SDK ---
#
# This is the test that would have caught the production bug. `build_request`
# put `usage: {"include": True}` — an OpenRouter extension the OpenAI SDK has
# never heard of — into the same dict that used to be splatted wholesale into
# `Completions.create(**request)`. That failed every real call with `TypeError:
# Completions.create() got an unexpected keyword argument 'usage'`, and none of
# the other 757 tests noticed, because none of them ever bound a request
# against the real SDK's signature — the stub's `create(**kwargs)` accepts
# anything, and the fake fabricates its own response by hand. These tests
# freshly introspect `Completions.create` themselves (not by trusting
# `split_request_for_the_sdk`'s own `_CREATE_PARAMS` blindly) and then bind
# against it for real, so a bug in the split itself — under-including a
# required SDK parameter, or over-including something the SDK doesn't
# recognise — fails here exactly the way a live call would fail, with no
# network and no API key.


def test_every_key_a_bare_request_produces_is_accounted_for_by_the_split():
    request = build_request("m", "sys", "usr", 0.0)
    body, extra = split_request_for_the_sdk(request)

    # Nothing from build_request() is silently dropped, and nothing lands in
    # both buckets.
    assert set(body) | set(extra) == set(request)
    assert set(body).isdisjoint(extra)

    # The typed half really does bind against the SDK's own signature — the
    # same check that raised TypeError on every real call before the fix.
    inspect.signature(Completions.create).bind(None, **body)

    # `usage` is the OpenRouter extension that broke production: it must be
    # routed through extra_body, not smuggled in as a typed kwarg, precisely
    # because the SDK does not declare it as a parameter at all.
    assert "usage" not in inspect.signature(Completions.create).parameters
    assert extra == {"usage": {"include": True}}


def test_every_key_a_schema_pinned_request_produces_is_accounted_for_by_the_split():
    """Covers the schema-pinned variant: response_format is SDK-native and
    must stay on the typed side, not get swept into extra_body next to usage."""
    schema = {"type": "object", "properties": {"labels": {"type": "array"}}}
    request = build_request("m", "sys", "usr", 0.0, json_schema=schema)
    body, extra = split_request_for_the_sdk(request)

    assert set(body) | set(extra) == set(request)
    assert set(body).isdisjoint(extra)

    inspect.signature(Completions.create).bind(None, **body)

    assert "response_format" in inspect.signature(Completions.create).parameters
    assert "response_format" in body
    assert extra == {"usage": {"include": True}}


def test_splatting_a_bare_request_unsplit_is_exactly_the_bug_that_broke_production():
    """Documents the failure this whole contract exists to prevent: the
    pre-fix call site (`self._create(timeout=timeout, **request)`) handed the
    *unsplit* request straight to the SDK. Reproducing that here, with no
    network involved, is the same TypeError a live call raised in production."""
    request = build_request("m", "sys", "usr", 0.0)

    with pytest.raises(TypeError, match="usage"):
        inspect.signature(Completions.create).bind(None, **request)
