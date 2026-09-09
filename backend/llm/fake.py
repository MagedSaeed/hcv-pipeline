"""An LLMClient that never touches the network, for tests and dry runs."""

from llm.client import DEFAULT_TIMEOUT, LLMResult, build_request


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
