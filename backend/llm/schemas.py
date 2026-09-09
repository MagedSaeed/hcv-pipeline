"""Response shapes the models are asked to produce, per spec section 8.

Malformed output is data, not a crash: parsing raises SchemaError, which the
caller records on the call record rather than letting it kill a run.
"""

from pydantic import BaseModel, Field, ValidationError


class SchemaError(ValueError):
    """A model response did not match the requested schema."""


class ScenarioCard(BaseModel):
    scenario_id: str
    actor: str = ""
    trigger: str = ""
    standing_state: str = ""
    asset: str = ""
    threat: str = ""
    outcome: str = ""
    narrative_steps: list[str] = Field(default_factory=list)


class GeneratedSystem(BaseModel):
    title: str
    body: str = ""
    scenarios: list[ScenarioCard] = Field(default_factory=list)


class Phase2Response(BaseModel):
    system_descriptions: list[GeneratedSystem] = Field(default_factory=list)
    # Populated instead of system_descriptions when generating against a target.
    scenarios: list[ScenarioCard] = Field(default_factory=list)


class LabelOut(BaseModel):
    hcv_id: str
    evidence: str = ""
    rationale: str = ""


class Phase3Response(BaseModel):
    # An empty list is a valid, meaningful answer: this scenario carries none.
    labels: list[LabelOut] = Field(default_factory=list)


def json_schema(model):
    """The JSON Schema to pin into an OpenRouter response_format request."""
    return model.model_json_schema()


def _parse(model, payload):
    if not isinstance(payload, dict):
        raise SchemaError(f"Expected a JSON object, got {type(payload).__name__}.")
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise SchemaError(str(error)) from error


def parse_phase2(payload):
    return _parse(Phase2Response, payload)


def parse_phase3(payload):
    return _parse(Phase3Response, payload)


def split_known_labels(response, allowed_ids):
    """Separate in-vocabulary labels from invented ones.

    How often a labeller invents an id outside the closed list is itself a
    finding, so the unknowns are returned rather than dropped.
    """
    known, unknown = [], []
    for label in response.labels:
        (known if label.hcv_id in allowed_ids else unknown).append(label)
    return known, [label.hcv_id for label in unknown]
