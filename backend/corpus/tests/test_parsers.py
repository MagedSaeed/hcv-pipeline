from corpus.parsers import parse_scenarios

SAMPLE = """Scenario Cards and Scenarios

## The template

|  |  |
| --- | --- |
| Actor | Everyone involved, by their id. |
| Outcome | What is now true. |

# sc_01

|  |  |
| --- | --- |
| Actor | EP1 (ExternalParty), who opens the session. |
| Trigger | After 22:00. 1. EP1 opens the session. |
| Standing state | PlantConsoleAuth protects the SCADA Service. |
| Asset | The SCADA Service (Service). |
| Threat | Spoofing, then Tampering. |
| Outcome | The feed ran at 11,100 parts per million for 8 hours. |

## The scenario

After 22:00. The last shift has ended.

1. EP1 opens the TeamViewer Session to W2.

Outcome: the feed ran at 11,100 parts per million for 8 hours.

# sc_02

|  |  |
| --- | --- |
| Actor | R4471 (SeniorCitizen), aged 78. |
| Threat | Spoofing. |

## The scenario

A Thursday at the reception desk.
"""


def test_skips_the_template_preamble():
    scenarios = parse_scenarios(SAMPLE)
    assert [row["scenario_id"] for row in scenarios] == ["sc_01", "sc_02"]


def test_reads_every_card_field():
    first = parse_scenarios(SAMPLE)[0]
    assert first["actor"] == "EP1 (ExternalParty), who opens the session."
    assert first["trigger"] == "After 22:00. 1. EP1 opens the session."
    assert first["standing_state"] == "PlantConsoleAuth protects the SCADA Service."
    assert first["asset"] == "The SCADA Service (Service)."
    assert first["threat"] == "Spoofing, then Tampering."
    assert first["outcome"] == "The feed ran at 11,100 parts per million for 8 hours."


def test_narrative_is_everything_after_the_scenario_heading():
    first = parse_scenarios(SAMPLE)[0]
    assert first["narrative"].startswith("After 22:00. The last shift has ended.")
    assert "1. EP1 opens the TeamViewer Session to W2." in first["narrative"]
    assert "| Actor |" not in first["narrative"]


def test_absent_card_fields_become_empty_strings():
    second = parse_scenarios(SAMPLE)[1]
    assert second["threat"] == "Spoofing."
    assert second["trigger"] == ""
    assert second["outcome"] == ""


def test_empty_input_yields_no_scenarios():
    assert parse_scenarios("") == []


def test_escaped_pipe_in_a_card_cell_is_unescaped():
    markdown = """# sc_01

|  |  |
| --- | --- |
| Threat | Spoofing \\| Tampering. |

## The scenario

Narrative text.
"""
    scenario = parse_scenarios(markdown)[0]
    assert scenario["threat"] == "Spoofing | Tampering."


def test_parses_the_real_seed_file(settings):
    text = (settings.RESOURCES_DIR / "seeds/oldsmar/scenarios.md").read_text()
    scenarios = parse_scenarios(text)
    assert [row["scenario_id"] for row in scenarios] == ["sc_01", "sc_02", "sc_03"]
    assert scenarios[0]["threat"] == "Spoofing, then Tampering."
    assert scenarios[2]["threat"] == "Unawareness."
    assert all(row["narrative"] for row in scenarios)
