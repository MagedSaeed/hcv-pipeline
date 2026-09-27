import pytest

from llm.schemas import (
    Phase2Response,
    Phase3Response,
    SchemaError,
    json_schema,
    parse_phase2,
    parse_phase3,
    phase2_json_schema,
    split_known_labels,
)

CARD = {
    "scenario_id": "sc_01",
    "actor": "EP1 (ExternalParty)",
    "trigger": "After 22:00.",
    "standing_state": "One shared password.",
    "asset": "The SCADA Service",
    "threat": "Spoofing, then Tampering.",
    "outcome": "The feed ran at 11,100 ppm for 8 hours.",
    "narrative_steps": ["EP1 opens the session.", "EP1 sends a setpoint."],
}


def test_parses_systems_with_nested_scenarios():
    parsed = parse_phase2(
        {"system_descriptions": [{"title": "Oldsmar", "body": "A plant.", "scenarios": [CARD]}]}
    )
    assert parsed.system_descriptions[0].title == "Oldsmar"
    assert parsed.system_descriptions[0].scenarios[0].scenario_id == "sc_01"


def test_parses_a_bare_scenario_list_for_the_target_system_mode():
    parsed = parse_phase2({"scenarios": [CARD]})
    assert parsed.system_descriptions == []
    assert parsed.scenarios[0].threat == "Spoofing, then Tampering."


def test_a_system_with_no_scenarios_is_valid():
    parsed = parse_phase2({"system_descriptions": [{"title": "T", "body": "B"}]})
    assert parsed.system_descriptions[0].scenarios == []


def test_narrative_steps_default_to_empty():
    card = {key: value for key, value in CARD.items() if key != "narrative_steps"}
    assert parse_phase2({"scenarios": [card]}).scenarios[0].narrative_steps == []


def test_a_missing_scenario_id_is_a_schema_error():
    card = {key: value for key, value in CARD.items() if key != "scenario_id"}
    with pytest.raises(SchemaError, match="scenario_id"):
        parse_phase2({"scenarios": [card]})


def test_a_non_dict_payload_is_a_schema_error():
    with pytest.raises(SchemaError):
        parse_phase2(["not", "a", "dict"])


def test_parses_labels():
    parsed = parse_phase3({"labels": [{"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"}]})
    assert parsed.labels[0].hcv_id == "HCV-07"


def test_an_empty_label_list_is_valid_and_meaningful():
    """A scenario carrying no vulnerability is a real result, not an error."""
    assert parse_phase3({"labels": []}).labels == []


def test_missing_labels_key_defaults_to_empty():
    assert parse_phase3({}).labels == []


def test_the_label_schema_requires_evidence_and_rationale():
    """Optional, qwen3-vl-8b-thinking and DeepSeek models omitted evidence."""
    label = json_schema(Phase3Response)["$defs"]["LabelOut"]
    assert sorted(label["required"]) == ["evidence", "hcv_id", "rationale"]
    assert label["properties"]["evidence"]["minLength"] == 1


def test_a_label_without_evidence_still_parses():
    """Required is asked of the model only; parsing keeps such labels."""
    parsed = parse_phase3({"labels": [{"hcv_id": "HCV-03", "rationale": "r"}]})
    assert parsed.labels[0].evidence == ""


def test_split_known_labels_separates_out_of_vocabulary_ids():
    parsed = parse_phase3(
        {
            "labels": [
                {"hcv_id": "HCV-07", "evidence": "e", "rationale": "r"},
                {"hcv_id": "HCV-99", "evidence": "e", "rationale": "r"},
            ]
        }
    )
    known, unknown = split_known_labels(parsed, {"HCV-07", "HCV-08"})

    assert [row.hcv_id for row in known] == ["HCV-07"]
    assert unknown == ["HCV-99"]


def test_json_schema_is_shaped_for_response_format():
    schema = json_schema(Phase2Response)
    assert schema["type"] == "object"
    assert "system_descriptions" in schema["properties"]

    schema3 = json_schema(Phase3Response)
    assert "labels" in schema3["properties"]


def test_phase2_json_schema_for_generate_systems_omits_bare_scenarios():
    """This is the defect-1 root-cause fix: the schema pinned into the
    request for a `generate_systems` run must not even offer the model a
    top-level `scenarios` field to (redundantly) fill in — see
    `pipeline.generation._call_and_persist` and the live run that lost a
    complete, paid-for generation to exactly this ambiguity."""
    schema = phase2_json_schema(generate_systems=True)
    assert "system_descriptions" in schema["properties"]
    assert "scenarios" not in schema["properties"]


def test_phase2_json_schema_for_target_mode_omits_system_descriptions():
    schema = phase2_json_schema(generate_systems=False)
    assert "scenarios" in schema["properties"]
    assert "system_descriptions" not in schema["properties"]


def test_loads_response_accepts_a_markdown_fence():
    from llm.schemas import loads_response

    assert loads_response('```json\n{"labels": []}\n```') == {"labels": []}
    assert loads_response('```\n{"a": 1}```') == {"a": 1}
    assert loads_response('  {"a": 1}  ') == {"a": 1}


def test_loads_response_still_refuses_non_json():
    import pytest

    from llm.schemas import loads_response

    with pytest.raises(ValueError):
        loads_response('```json\n{"system_descriptions": [\n')


def test_parse_failure_message_explains_a_truncated_reply():
    from llm.schemas import parse_failure_message

    assert "output limit (4096" in parse_failure_message(ValueError("x"), "length", 4096)
    assert parse_failure_message(ValueError("x"), "stop", 10) == "x"


def test_loads_response_accepts_raw_line_breaks_inside_strings():
    from llm.schemas import loads_response

    raw = '```json\n{"system_descriptions": [{"title": "P", "body": "Line one\n\nLine two"}]}\n```'
    assert loads_response(raw)["system_descriptions"][0]["body"] == "Line one\n\nLine two"
