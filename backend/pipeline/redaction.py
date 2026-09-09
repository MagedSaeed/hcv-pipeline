"""Keep API keys out of anything archived.

The client already refuses to BUILD a request containing a key. This is the
second line: it scrubs on the way into the database, and it covers every
column the client cannot vouch for — `response_json`, `transport_error`,
`parse_error`, `unknown_hcv_ids`, `model` and `finish_reason` are all built
from provider output (some derived from it well after the client returns), and
a provider that echoes a key back into any of them would otherwise put it in
an export that gets shared with a collaborator.
"""

from django.conf import settings

from core.keys import KEY_PATTERN

REDACTED = "[REDACTED]"

# Spec §6 has the storage layer assert that no stored payload matches `sk-or-`.
# The exact-value replacement below only knows the two keys configured RIGHT
# NOW, so a key that has since been rotated, or a collaborator's key echoed
# back inside a provider error body, matched nothing and reached the export
# intact. Matching the shape as well closes that, and costs nothing on a clean
# payload.
#
# `KEY_PATTERN` is IMPORTED from `core.keys` — the single source of truth
# for what an OpenRouter key looks like, per that module's own docstring —
# not re-declared here. This and `llm.client.OpenRouterClient._guard` used
# to each keep an independent hand-written form (this one a bounded regex,
# the client's a bare `"sk-or-" in blob`) and drifted apart: this side
# gained a left word-boundary first, and the client kept matching INSIDE
# ordinary words ("a risk-or-benefit tradeoff", "ask-or-tell disclosure" —
# both plausible in a corpus about security and risk) for a full review
# round after that, aborting real phase runs on nothing but that ordinary
# text. `core.keys` rather than `llm.client` also matters beyond naming:
# `llm.client` imports the `openai` SDK at module level, and this module
# sits on `LLMCall.save()` — importing the pattern from `llm.client` would
# have dragged the provider SDK into every model save for the sake of one
# regex; `core.keys` imports only `django.conf.settings` and `core.models`.


def secrets_to_scrub():
    """Every key value currently in play, longest first so overlaps scrub fully."""
    from core.models import AppSettings

    candidates = [
        (AppSettings.load().openrouter_key or "").strip(),
        (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip(),
    ]
    return sorted({c for c in candidates if c}, key=len, reverse=True)


def scrub(value, secrets):
    """Recursively replace every secret occurrence. Structure is preserved.

    Two passes over each string: the configured key values (longest first, so
    overlaps scrub fully), then anything merely key-SHAPED. The second pass runs
    whether or not `secrets` is empty — a rotated or foreign key is exactly the
    case where there is nothing configured to match it against.
    """
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return KEY_PATTERN.sub(REDACTED, value)
    if isinstance(value, dict):
        # Dict KEYS are scrubbed too, not just values. `request_json`'s keys
        # are all literals from `build_request` and never see outside input,
        # but `response_json` is `json.loads`'d straight from the provider's
        # raw body — nothing stops a malformed or adversarial response from
        # putting something key-shaped in a JSON object key instead of a
        # value, and this function's whole job is not assuming a shape is
        # safe just because it usually is.
        #
        # Known, accepted trade: if TWO distinct keys both redact to the same
        # `"[REDACTED]"` string (`{K1: "first", K2: "second"}`), the dict
        # comprehension keeps only the LAST one written — one sibling entry
        # silently disappears rather than colliding loudly. This only fires
        # on `response_json`, which is already a payload caught echoing a
        # credential back; losing a sibling entry from an already-poisoned
        # response is judged better than exporting a live key, so this is
        # deliberate, not an oversight.
        return {scrub(key, secrets): scrub(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, secrets) for item in value]
    return value


def redact_call(call):
    """Scrub every archived column of an LLMCall in place, before it is saved.

    Only `LLMCall.save()` calls this. A queryset `.update()` writes straight to
    the database without instantiating/saving a row, so it never reaches this
    function — an LLMCall must always be written via `.save()` (or `.create()`,
    which calls it), never `.update()`, or a key can reach the database unscrubbed.

    `parse_error` and `unknown_hcv_ids` used to be left out: a provider that
    returns something key-shaped where a string was expected (e.g.
    `{"system_descriptions": "sk-or-v1-..."}`) puts that value straight into
    pydantic's validation message via `input_value=...`, which lands in
    `parse_error` unredacted even though `raw_response` — the same payload,
    read a different way — was already scrubbed. `model` and `finish_reason`
    are also provider-controlled text, read off the response body BEFORE this
    function ever sees it, so they get the same treatment for the same
    reason: nothing this function does not scrub is safe to assume clean.
    """
    # No early return on an empty `secrets`: scrub() still has a key SHAPE to
    # match, which is the whole point of the pattern pass.
    secrets = secrets_to_scrub()
    call.request_json = scrub(call.request_json, secrets)
    call.response_json = scrub(call.response_json, secrets)
    call.raw_response = scrub(call.raw_response, secrets)
    call.transport_error = scrub(call.transport_error, secrets)
    call.rendered_system_prompt = scrub(call.rendered_system_prompt, secrets)
    call.rendered_user_prompt = scrub(call.rendered_user_prompt, secrets)
    call.parse_error = scrub(call.parse_error, secrets)
    call.unknown_hcv_ids = scrub(call.unknown_hcv_ids, secrets)
    call.model = scrub(call.model, secrets)
    call.finish_reason = scrub(call.finish_reason, secrets)
