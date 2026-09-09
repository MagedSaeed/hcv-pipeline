"""An LLMClient that never touches the network, for tests and dry runs."""

import inspect

from openai.resources.chat.completions import Completions

from llm.client import DEFAULT_TIMEOUT, LLMResult, build_request, split_request_for_the_sdk

# Bound, not just membership-checked against a set: this raises the identical
# `TypeError` shape a live call would raise (`Completions.create() got an
# unexpected keyword argument '...'`). Before this check existed, the fake
# took whatever `build_request()` produced and fabricated a response
# unconditionally — including the `usage: {"include": True}` OpenRouter
# extension the real SDK rejects outright as an unexpected keyword argument.
# That is precisely why 757 passing tests never caught the production bug:
# nearly every one of them ran a call through a fake that was strictly more
# permissive than the real API. Validating on every `complete()` call turns
# each of those existing tests into an incidental check of this same
# invariant too, not just the one dedicated contract test in
# llm/tests/test_client.py.
_CREATE_SIGNATURE = inspect.signature(Completions.create)


class FakeLLMClient:
    """Returns queued responses in order and records every call it received."""

    def __init__(self, responses=None):
        self._queue = [self._as_canned(response) for response in responses or []]
        self.calls = []

    @staticmethod
    def _as_canned(response):
        """Accept a bare string as shorthand for `.queue(text)`."""
        return {"text": response} if isinstance(response, str) else response

    def queue(self, text, **overrides):
        """Queue one assistant message, optionally overriding projected columns."""
        self._queue.append({"text": text, **overrides})

    def fail_next(self, message, http_status=None):
        """Queue a transport failure — no response body, like a 429 or a timeout."""
        self._queue.append({"error": message, "http_status": http_status})

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
        # Built by the same function the real client uses, so a test asserting
        # on request_json — that a schema was pinned, say — means the same thing
        # against the fake as it does against OpenRouter.
        request = build_request(model, system_prompt, user_prompt, temperature, json_schema)
        # Same split the real client applies at its call site, then proof
        # that the typed half is something `Completions.create` would
        # actually accept — reject here exactly as a live call would reject,
        # instead of accepting a shape reality never would.
        body, _extra = split_request_for_the_sdk(request)
        _CREATE_SIGNATURE.bind(None, **body)
        self.calls.append(
            {
                "model": model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "temperature": temperature,
                "json_schema": json_schema,
            }
        )
        if not self._queue:
            # Raised explicitly, not a bare `assert`: `python -O` strips bare
            # asserts, which would turn this into an opaque IndexError from
            # `list.pop` instead of a legible, matchable failure.
            raise AssertionError(
                "FakeLLMClient has no queued response left. "
                "Queue one per expected call with .queue() or .fail_next()."
            )
        canned = self._queue.pop(0)

        if "error" in canned:
            return LLMResult(
                request_json=request,
                http_status=canned.get("http_status"),
                transport_error=canned["error"],
            )

        text = canned["text"]
        body = {
            "id": "fake-generation",
            "model": model,
            "choices": [
                {
                    "finish_reason": canned.get("finish_reason", "stop"),
                    "message": {"role": "assistant", "content": text},
                }
            ],
            "usage": {
                "prompt_tokens": canned.get("prompt_tokens", 0),
                "completion_tokens": canned.get("completion_tokens", 0),
            },
        }
        return LLMResult(
            request_json=request,
            response_json=body,
            raw_response=text,
            model=model,
            finish_reason=canned.get("finish_reason", "stop"),
            prompt_tokens=canned.get("prompt_tokens", 0),
            completion_tokens=canned.get("completion_tokens", 0),
            cost=canned.get("cost"),
            latency_ms=0,
            http_status=200,
        )
