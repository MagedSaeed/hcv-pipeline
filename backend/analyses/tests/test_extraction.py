import json

import pytest

from analyses import extraction
from analyses.extraction import ExtractError, parse_reply, structured_body


def reply(elements=(), facts=(), controls=(), gaps=None):
    return json.dumps(
        {
            "elements": list(elements),
            "facts": list(facts),
            "controls": list(controls),
            "gaps": gaps or {"not_assessable": [], "no_term": []},
        }
    )


def element(id_, field, value, fact_type="Asserted", **extra):
    return {
        "id": id_,
        "section": "",
        "field": field,
        "value": value,
        "fact_type": fact_type,
        "reason": "",
        "other_reading": "",
        **extra,
    }


def fact(subject, field, value, fact_type="Asserted"):
    return {
        "section": "",
        "field": field,
        "subject_id": subject,
        "value": value,
        "fact_type": fact_type,
        "reason": "",
        "other_reading": "",
    }


def control(element_id, name, state="absent", fact_type="Asserted"):
    return {
        "section": "8. Controls",
        "element_id": element_id,
        "control": name,
        "state": state,
        "fact_type": fact_type,
        "reason": "",
        "other_reading": "",
    }


def test_every_field_of_the_prompt_is_known():
    kinds = [spec.kind for spec in extraction.FIELDS.values()]

    assert kinds.count("element") == 14
    assert kinds.count("fact") == 29
    assert len(extraction.CONTROLS) == 43
    assert extraction.CONTROL_FAMILIES["PasswordControl"] == (
        "MultiFactorAuthentication",
        "PasswordStrengthEnforcement",
    )
    assert extraction.FIELDS["organization"].allowed[0] == "GovernmentOrganization"
    assert extraction.FIELDS["decision_impact"].allowed == (
        "Impact_Consequential",
        "Impact_Routine",
    )
    assert extraction.FIELDS["supplier"].value_kind == "element"
    assert extraction.FIELDS["retention_days"].value_kind == "number"
    assert extraction.FIELDS["removable_media"].value_kind == "boolean"
    assert extraction.FIELDS["access_scope"].fact_type == "possible"
    assert extraction.FIELDS["trust_boundary"].fact_type == "asserted_if_named"


def test_items_carry_their_section_and_kind():
    items, gaps = parse_reply(
        reply(
            elements=[element("Residents", "person_or_role", "Citizen")],
            facts=[fact("Residents", "vulnerability_factor", "Factor_Disability", "Possible")],
            controls=[control("Residents", "PasswordControl")],
            gaps={
                "not_assessable": [{"area": "Physical entry", "reason": "None described."}],
                "no_term": [],
            },
        )
    )

    assert [(i["kind"], i["section"], i["ref"], i["problem"]) for i in items] == [
        ("element", "1. People and organizations", "Residents", ""),
        ("fact", "1. People and organizations", "Residents", ""),
        ("control", "8. Controls", "Residents", ""),
    ]
    assert gaps["not_assessable"] == [{"area": "Physical entry", "reason": "None described."}]


def test_always_possible_fields_become_possible():
    items, _ = parse_reply(
        reply(
            elements=[element("Residents", "person_or_role", "Citizen")],
            facts=[fact("Residents", "vulnerability_factor", "Factor_Disability", "Asserted")],
        )
    )

    assert items[1]["fact_type"] == "Possible"


def test_out_of_list_value_unknown_field_and_dangling_id_are_marked():
    items, _ = parse_reply(
        reply(
            elements=[
                element("Residents", "person_or_role", "Tourist"),
                element("X", "mood", "Happy"),
            ],
            facts=[fact("Ghost", "uses", "Residents")],
            controls=[control("Residents", "Encryption")],
        )
    )

    assert "not an allowed value" in items[0]["problem"].lower()
    assert "unknown field" in items[1]["problem"].lower()
    assert "Ghost" in items[2]["problem"]
    assert "not an allowed control" in items[3]["problem"].lower()


def test_case_and_spacing_are_normalised():
    items, _ = parse_reply(
        reply(elements=[element("Residents", "person_or_role", " citizen ", "asserted ")])
    )

    assert (items[0]["value"], items[0]["fact_type"], items[0]["problem"]) == (
        "Citizen",
        "Asserted",
        "",
    )


def test_ids_resolve_after_all_elements():
    # The fact names an element that comes later in the reply.
    text = json.dumps(
        {
            "facts": [fact("Residents", "uses", "TheApp")],
            "elements": [
                element("Residents", "person_or_role", "Citizen"),
                element("TheApp", "interface", "MobileApp"),
            ],
            "controls": [],
            "gaps": {"not_assessable": [], "no_term": []},
        }
    )

    items, _ = parse_reply(text)

    assert all(not item["problem"] for item in items)


def test_duplicate_and_missing_ids_are_marked():
    items, _ = parse_reply(
        reply(
            elements=[
                element("Residents", "person_or_role", "Citizen"),
                element("Residents", "person_or_role", "SeniorCitizen"),
                element("", "person_or_role", "Child"),
            ]
        )
    )

    assert [bool(i["problem"]) for i in items] == [False, True, True]
    assert "duplicate" in items[1]["problem"]
    assert "no id" in items[2]["problem"]


def test_wrong_kinds_are_marked():
    items, _ = parse_reply(
        reply(
            elements=[
                element("Store", "data_store", "DataStore"),
                element("Desk", "device", "OperatorWorkstation"),
            ],
            facts=[
                fact("Store", "retention_days", "30 days"),
                fact("Desk", "removable_media", "yes"),
                fact("Store", "retention_days", 1095),
                fact("Desk", "removable_media", True),
            ],
        )
    )

    assert "whole number" in items[2]["problem"]
    assert "true or false" in items[3]["problem"]
    assert (items[4]["value"], items[4]["problem"]) == ("1095", "")
    assert (items[5]["value"], items[5]["problem"]) == ("true", "")


@pytest.mark.parametrize(
    "text",
    [
        "I can't help",
        "[]",
        '{"elements": {}, "facts": [], "controls": [], "gaps": {}}',
        '{"elements": [], "facts": [], "controls": [], "gaps": []}',
        '{"elements": [], "facts": []}',
    ],
)
def test_reply_shape_failures_raise(text):
    with pytest.raises(ExtractError):
        parse_reply(text)


def test_a_fenced_reply_is_read():
    items, _ = parse_reply(
        "```json\n" + reply(elements=[element("A", "service", "Service")]) + "\n```"
    )

    assert items[0]["ref"] == "A"


def approved(items, **decisions):
    for item in items:
        item["decision"] = decisions.get(item["ref"] + ":" + item["field"], "approved")
    return items


def test_body_lists_sections_in_order_and_only_approved():
    items, gaps = parse_reply(
        reply(
            elements=[
                element("Residents", "person_or_role", "Citizen"),
                element("SmartStopApp", "interface", "MobileApp"),
                element("Staff", "person_or_role", "CityOperator"),
            ],
            facts=[
                fact("Residents", "uses", "SmartStopApp"),
                fact("Staff", "uses", "SmartStopApp"),
            ],
            controls=[control("SmartStopApp", "MultiFactorAuthentication")],
            gaps={
                "not_assessable": [{"area": "Physical entry", "reason": ""}],
                "no_term": [
                    {"statement": "anonymised trip data shared for advertising", "reason": ""}
                ],
            },
        )
    )
    approved(items, **{"Staff:person_or_role": "rejected"})

    body = structured_body(items, gaps, "SmartStop helps residents.")

    assert body.index("### 1. People and organizations") < body.index("### 2. System components")
    assert body.index("### 2. System components") < body.index("### 8. Controls")
    assert "- Residents (Citizen)" in body
    assert "- SmartStopApp (MobileApp)" in body
    assert body.index("### 2. System components") < body.index("### Facts")
    assert body.index("### Facts") < body.index("### 8. Controls")
    assert "- Residents uses SmartStopApp" in body
    assert "- SmartStopApp: MultiFactorAuthentication absent" in body
    assert "Staff" not in body
    assert "- Physical entry" in body
    assert "anonymised trip data shared for advertising" in body
    assert body.rstrip().endswith("## Original description\n\nSmartStop helps residents.")


def test_body_writes_each_data_flow_on_one_line():
    items, gaps = parse_reply(
        reply(
            elements=[
                element("StopCameras", "device", "SensingDevice"),
                element("MoveAICloud", "infrastructure", "ComputePlatform"),
                element("DF_CameraToCloud", "data_flow", "DataFlow"),
                element("DF_Partial", "data_flow", "DataFlow"),
            ],
            facts=[
                fact("DF_CameraToCloud", "source", "StopCameras"),
                fact("DF_CameraToCloud", "destination", "MoveAICloud"),
                fact("DF_CameraToCloud", "data_category", "BiometricData"),
                fact("DF_CameraToCloud", "trust_boundary", "ServiceToCloud"),
                fact("DF_Partial", "data_category", "LocationData"),
            ],
        )
    )
    approved(items)

    body = structured_body(items, gaps, "Text.")

    assert (
        "- DF_CameraToCloud (DataFlow): StopCameras → MoveAICloud; "
        "data: BiometricData; boundary: ServiceToCloud"
    ) in body
    assert "- DF_Partial (DataFlow): data: LocationData" in body
    assert "### Facts" not in body
    assert body.startswith("## Structured description\n")


def test_body_leaves_out_pending_and_problem_values():
    items, gaps = parse_reply(
        reply(elements=[element("A", "service", "Service"), element("B", "service", "Robot")])
    )
    items[0]["decision"] = "pending"
    items[1]["decision"] = "approved"

    body = structured_body(items, gaps, "Text.")

    assert "- A (" not in body
    assert "Robot" not in body
