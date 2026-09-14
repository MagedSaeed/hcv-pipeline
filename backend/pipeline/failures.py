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
