"""The OpenRouter client. Every call produces a record, successful or not.

The client never raises on a provider failure: a rate limit, a timeout or a 502
comes back as an LLMResult with `ok=False`, so a run stores a complete row rather
than a gap. The only exception it does raise is SecretLeak, because storing an
API key in a payload that later ships in an export is not a recoverable state.
"""

import inspect
import json
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from openai import OpenAI
from openai.resources.chat.completions import Completions

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 120

# The OpenAI SDK's own signature, introspected once at import time — not a
# hand-maintained key list. `build_request` puts OpenRouter extensions (like
# `usage: {"include": True}`, needed for per-call cost) straight into the
# same dict as SDK-native fields (`model`, `messages`, `response_format`...),
# because that dict is also the archival record of what was logically sent.
# Splatting it at the call site (`self._create(**request)`) fails immediately
# with `TypeError: Completions.create() got an unexpected keyword argument
# 'usage'` — a provider extension is never a typed parameter on the SDK's
# `create()`, it only exists in the JSON body, so `extra_body` is where it
# has to go. Deriving the split from the signature itself, rather than a
# `{"usage"}` literal, is what makes the NEXT extension route correctly
# without anyone remembering to update a second, separate list — this
# codebase has already been bitten by exactly that kind of drift more than
# once (see core.keys.KEY_PATTERN's history, and the fake/protocol timeout
# mismatch in llm/tests/test_client.py).
_CREATE_PARAMS = frozenset(inspect.signature(Completions.create).parameters) - {"self"}


def split_request_for_the_sdk(request):
    """Split a logical request into (typed kwargs, extra_body) for `create()`.

    Anything `Completions.create` actually declares as a parameter is passed
    typed; everything else — a provider extension `build_request` added — is
    routed through `extra_body`, which the SDK merges into the JSON body
    verbatim. `request_json` (the archived record) is never split: it keeps
    the full logical body, extensions included, because that IS what ends up
    on the wire once `extra_body` is merged in.
    """
    body = {key: value for key, value in request.items() if key in _CREATE_PARAMS}
    extra = {key: value for key, value in request.items() if key not in _CREATE_PARAMS}
    return body, extra


class SecretLeak(RuntimeError):
    """A payload about to be stored contained something shaped like an API key."""


@dataclass(frozen=True)
class LLMResult:
    request_json: dict
    response_json: dict | None = None
    raw_response: str = ""
    model: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None
    latency_ms: int = 0
    http_status: int | None = None
    transport_error: str = ""

    @property
    def ok(self):
        return self.response_json is not None and not self.transport_error


@runtime_checkable
class LLMClient(Protocol):
    """What a run may call. runtime_checkable so a stand-in can be asserted
    against it in a test rather than drifting from it unnoticed."""

    def complete(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        json_schema: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> LLMResult: ...


def build_messages(system_prompt, user_prompt):
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return messages


def build_request(model, system_prompt, user_prompt, temperature=0.0, json_schema=None):
    """The request body, built in ONE place for the real and the fake client.

    Both clients record this dict as `request_json`, and a run's stored request
    is the evidence of what was actually asked — which schema was pinned above
    all. Building it separately in the fake meant a test could assert "the
    schema was pinned into the request" and pass vacuously, because the fake's
    request never carried a response_format at all.

    `usage: {include: true}` is required for OpenRouter to return per-call cost;
    without it `usage.cost` is simply absent and every recorded cost is null.
    """
    request = {
        "model": model,
        "messages": build_messages(system_prompt, user_prompt),
        "temperature": temperature,
        "usage": {"include": True},
    }
    # Truthiness, not `is not None`: PromptTemplate.output_schema defaults to
    # `{}`, and passing that through would send a response_format demanding an
    # empty schema on every unschemad template — which some providers reject
    # outright. An empty schema is "no schema", not "a schema with no rules".
    if json_schema:
        request["response_format"] = {
            "type": "json_schema",
            # `strict` is deliberately omitted, not pinned. Audited:
            # model_json_schema() violates strict structured-output rules
            # (6 places on Phase2Response, 4 on Phase3Response —
            # `additionalProperties` is never set to false, and `required`
            # omits every defaulted field, including `labels` itself). On
            # a strict-enforcing provider, pinning strict=True would 400
            # every schema-pinned call, recorded as a failure — a whole
            # run producing nothing, discovered only on the first live
            # call. Turning it on later means post-processing the schema
            # (force additionalProperties: false recursively, and move
            # every defaulted field into `required`), which changes what
            # the model is told it must emit — a deliberate follow-up,
            # not a flag flipped here. Omitting it keeps the schema as
            # guidance only, with no failure mode.
            "json_schema": {"name": "response", "schema": json_schema},
        }
    return request


def _as_dict(value):
    """Coerce a field that should be a dict but might not be, without raising.

    A malformed provider reply is data, not a crash: the full body still gets
    archived verbatim on `response_json`, and only these derived columns fall
    back to empty defaults when a field isn't shaped the way we expect.
    """
    return value if isinstance(value, dict) else {}


class OpenRouterClient:
    """Talks to OpenRouter through the OpenAI-compatible SDK."""

    def __init__(self, api_key=None, base_url=OPENROUTER_BASE_URL):
        if api_key is None:
            # Imported here, not at module level: this is its only use, and it
            # touches the database (core.keys reads an AppSettings row before
            # falling back to the environment). Keeping it out of the module
            # header means llm.fake and the plain LLMResult dataclass can be
            # imported with no Django app registry booted at all.
            from core.keys import resolve_openrouter_key

            api_key = resolve_openrouter_key()
        self._api_key = api_key
        self._client = OpenAI(api_key=self._api_key, base_url=base_url)
        # One seam, so tests can stand in for the network without patching the SDK.
        self._create = self._client.chat.completions.with_raw_response.create

    def complete(
        self,
        *,
        model,
        system_prompt,
        user_prompt,
        temperature=0.0,
        json_schema=None,
        timeout=DEFAULT_TIMEOUT,
    ):
        request = build_request(model, system_prompt, user_prompt, temperature, json_schema)
        # The key lives on the client, never in the recorded body.
        self._guard(request)

        # `request` (and `request_json` below) stays the full logical body,
        # unsplit — it's the archival record of what was actually sent, and
        # `extra_body` really does get merged back into that same JSON body
        # on the wire. Only the arguments handed to the SDK call get split.
        body, extra = split_request_for_the_sdk(request)

        started = time.monotonic()
        try:
            raw = self._create(timeout=timeout, extra_body=extra, **body)
        except Exception as error:  # noqa: BLE001 — a provider failure is data
            return LLMResult(
                request_json=request,
                latency_ms=self._elapsed(started),
                # Every non-2xx raises before a raw response is ever returned
                # (verified against openai 3.8.0: APIStatusError.__init__ sets
                # status_code = response.status_code) — this is the only place
                # a 429/500/502/401 status is observable, so it must be read
                # off the exception rather than left empty.
                http_status=getattr(error, "status_code", None),
                transport_error=f"{type(error).__name__}: {error}",
            )

        latency = self._elapsed(started)
        status = getattr(raw, "status_code", None)
        try:
            body = json.loads(raw.text)
        except (ValueError, AttributeError):
            return LLMResult(
                request_json=request,
                latency_ms=latency,
                http_status=status,
                transport_error=f"Response body was not JSON: {raw.text[:500]}",
            )
        if not isinstance(body, dict):
            # Valid JSON, but not the object shape everything below assumes
            # (a bare list, string, number or null) — recorded as a failure
            # rather than crashing _result() on a non-dict .get() call.
            return LLMResult(
                request_json=request,
                latency_ms=latency,
                http_status=status,
                transport_error=(
                    f"Response body was not a JSON object (got {type(body).__name__}): "
                    f"{raw.text[:500]}"
                ),
            )
        return self._result(request, body, latency, status)

    def _result(self, request, body, latency, status):
        choices = body.get("choices")
        choice = _as_dict(choices[0]) if isinstance(choices, list) and choices else {}
        usage = _as_dict(body.get("usage"))
        message = _as_dict(choice.get("message"))
        return LLMResult(
            request_json=request,
            response_json=body,
            raw_response=message.get("content") or "",
            model=body.get("model", request["model"]),
            finish_reason=choice.get("finish_reason") or "",
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
            # OpenRouter returns per-call cost on `usage.cost` (and only when
            # the request asked for it, which build_request does). `total_cost`
            # is the separate generation endpoint's field name — accepted here
            # as a fallback so either shape is recorded rather than dropped.
            cost=usage.get("cost", usage.get("total_cost")),
            latency_ms=latency,
            http_status=status,
        )

    def _guard(self, payload):
        """Refuse to hand back a payload carrying anything key-shaped.

        Two checks: `KEY_PATTERN` (imported from `core.keys`, the single
        source of truth for what an OpenRouter key looks like — see its own
        comment for why this and `pipeline.redaction.scrub` share one
        pattern rather than each keeping a hand-written copy) catches a
        foreign OpenRouter key pasted into a prompt, bounded so it does not
        fire on ordinary text that merely contains "sk-or-" as a substring;
        comparing against `self._api_key` catches our own key in ANY shape
        (a rotated key, a non-`sk-or-` credential) with no boundary at all —
        this is an exact-value match against a secret the client knows for
        a fact, not a shape heuristic, so there is nothing to over-match.

        The import is local, not at module level: `core.keys` imports
        `core.models` (a Django model), and keeping that off this module's
        header is what lets `llm.fake`/`LLMResult`/`build_request` still be
        imported with no Django app registry booted at all — the same
        reason `OpenRouterClient.__init__` imports `resolve_openrouter_key`
        locally rather than at the top of this file.
        """
        from core.keys import KEY_PATTERN

        blob = json.dumps(payload)
        if KEY_PATTERN.search(blob) or (self._api_key and self._api_key in blob):
            raise SecretLeak(
                "Refusing to record a payload containing an OpenRouter key. "
                "The key belongs on the client, never in a stored request body."
            )

    @staticmethod
    def _elapsed(started):
        return int((time.monotonic() - started) * 1000)
