"""Response shapes the models are asked to produce, per spec section 8.

Malformed output is data, not a crash: parsing raises SchemaError, which the
caller records on the call record rather than letting it kill a run.
"""

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError


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
    # Required in the schema sent to the model (some models skip optional
    # evidence); parsing keeps the defaults so a reply without it isn't lost.
    model_config = ConfigDict(json_schema_extra={"required": ["hcv_id", "evidence", "rationale"]})

    hcv_id: str
    evidence: str = Field(default="", json_schema_extra={"minLength": 1})
    rationale: str = ""


class Phase3Response(BaseModel):
    # An empty list is a valid, meaningful answer: this scenario carries none.
    labels: list[LabelOut] = Field(default_factory=list)


def json_schema(model):
    """The JSON Schema to pin into an OpenRouter response_format request."""
    return model.model_json_schema()


def phase2_json_schema(*, generate_systems):
    """`Phase2Response`'s schema, restricted to the ONE top-level field the
    run's actual mode uses.

    `Phase2Response` carries two top-level fields for two mutually exclusive
    modes: `system_descriptions` (generating fresh systems, `generate_systems`
    True) and `scenarios` (generating against a pre-existing `target_system`,
    `generate_systems` False — see the model's own comment). Pinning the FULL
    schema unconditionally, as `json_schema(Phase2Response)` does, shows the
    model both places to put its output regardless of which mode this run
    actually is — and a live run proved a model will do the obvious thing and
    fill in both, which `pipeline.generation._persist_error` then had no
    choice but to reject wholesale, discarding a perfectly good generation
    (see that function's docstring). The fix is upstream of parsing: don't
    hand the model a schema that offers a place its response must never use.

    `strict` is deliberately not pinned (see `llm.client.build_request`'s own
    long comment), so this alone cannot make a redundant field structurally
    impossible — `pipeline.generation` still carries a safety net for exactly
    that. This function's job is only to stop the well-behaved case from
    happening in the first place.
    """
    schema = json_schema(Phase2Response)
    field_to_drop = "scenarios" if generate_systems else "system_descriptions"
    schema["properties"].pop(field_to_drop, None)
    return schema


def loads_response(raw):
    """Decode a model's JSON reply, accepting one surrounding Markdown code fence.

    Models that ignore `response_format` often wrap otherwise valid JSON in
    ```json ... ``` and put raw line breaks inside string values; both are
    formatting, not a schema failure (`strict=False` allows control characters
    inside strings). Anything else that is not JSON still raises ValueError.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        body = text[first_newline + 1 :] if first_newline != -1 else ""
        text = body.rstrip()
        if text.endswith("```"):
            text = text[:-3]
    return json.loads(text, strict=False)


def parse_failure_message(error, finish_reason, completion_tokens):
    """A parse error, plus the likely cause when the model hit its output limit."""
    message = str(error)
    if finish_reason == "length":
        message += (
            f" The model stopped at its output limit ({completion_tokens or '?'} output "
            "tokens), so the reply is incomplete. Ask for fewer systems or scenarios, or "
            "pick a model with a larger output limit."
        )
    return message


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
