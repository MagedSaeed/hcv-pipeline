"""How a run failure is worded for the researcher."""


def describe_failure(error):
    """A short label for a problem the pipeline anticipates, then its message; the
    exception type for anything unexpected, which is a bug worth reporting."""
    from core.keys import MissingAPIKey
    from llm.client import SecretLeak
    from pipeline.execution_config import (
        BorrowedExperimentContaminated,
        BorrowedExperimentMisconfigured,
        ChosenInputUnavailable,
        MalformedSnapshot,
        ReferencedRowMissing,
        UnboundGuardTaxonomy,
    )
    from prompts.rendering import PromptRenderError, TaxonomyLeak

    labels = (
        (TaxonomyLeak, "Taxonomy check"),
        (PromptRenderError, "Prompt error"),
        (MalformedSnapshot, "Saved run settings are invalid"),
        (ReferencedRowMissing, "Missing input"),
        (ChosenInputUnavailable, "Missing input"),
        (BorrowedExperimentContaminated, "Input not allowed"),
        (BorrowedExperimentMisconfigured, "Inputs incomplete"),
        (UnboundGuardTaxonomy, "Taxonomy mismatch"),
        (MissingAPIKey, "API key"),
        (SecretLeak, "Secret in request"),
    )
    for kind, label in labels:
        if isinstance(error, kind):
            return f"{label}: {error}"
    return f"Unexpected error ({type(error).__name__}): {error}"


_PROVIDER_ERROR_SENTENCES = {
    401: "OpenRouter rejected the API key. Check the key on the Settings page or in OPENROUTER_API_KEY.",
    402: (
        "Not enough OpenRouter credit: the key's spending limit or the account balance is too "
        "low for this request. Raise the key's limit or add credit on OpenRouter."
    ),
    404: "OpenRouter doesn't recognise this model. Check the model id.",
    429: "Rate limited by OpenRouter or the model provider. Wait a moment, then Retry.",
    400: "OpenRouter refused the request as invalid.",
}
_UNAVAILABLE = "OpenRouter or the model provider is unavailable. Try again later."
_MAX_PROVIDER_MESSAGE = 300


def _shorten(text):
    text = text.strip()
    return text if len(text) <= _MAX_PROVIDER_MESSAGE else text[: _MAX_PROVIDER_MESSAGE - 1] + "…"


def _provider_message(transport_error):
    """The provider's own error message out of the recorded `"Name: Error code: N - {body}"`."""
    import ast

    _, _, body = transport_error.partition(" - ")
    try:
        parsed = ast.literal_eval(body.strip())
    except (ValueError, SyntaxError):
        return ""
    error = parsed.get("error") if isinstance(parsed, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return ""
    return _shorten(message)


def describe_provider_error(http_status, transport_error):
    """One plain sentence for a failed model call, then OpenRouter's own words, or
    "" when the call did not fail at the transport level."""
    if not transport_error:
        return ""
    if http_status in _PROVIDER_ERROR_SENTENCES:
        sentence = _PROVIDER_ERROR_SENTENCES[http_status]
    elif http_status is None or http_status >= 500:
        sentence = _UNAVAILABLE
    else:
        sentence = f"The model call failed (HTTP {http_status})."
    message = _provider_message(transport_error)
    if message:
        return f"{sentence} OpenRouter said: “{message}”"
    # No structured body (a timeout, a proxy page): keep the raw details, minus the exception name.
    name, separator, details = transport_error.partition(": ")
    return f"{sentence} Details: {_shorten(details if separator else name)}"
