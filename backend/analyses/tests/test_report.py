import pytest

from analyses import report, state

pytestmark = pytest.mark.django_db


@pytest.fixture
def run(analysis):
    pinned = {
        "model": "openai/gpt-5.6-luna-pro",
        "temperature": 0.0,
        "prompt_label": "default-labelling v1",
        "examples": [{"name": "one"}],
        "count": None,
        "is_custom": False,
        "preset_version": "1",
    }
    return analysis.runs.create(kind="label", status="completed", pinned=pinned)


@pytest.fixture
def labelled(analysis, run):
    def labelled(scenario_id, *hcv_ids, **fields):
        scenario = analysis.scenarios.create(
            scenario_id=scenario_id, narrative=f"{scenario_id} happens.", **fields
        )
        call = run.calls.create(scenario=scenario, parsed_ok=True, cost=0.01)
        for hcv_id in hcv_ids:
            scenario.labels.create(
                hcv=analysis.taxonomy.hcvs.get(hcv_id=hcv_id),
                evidence="quote",
                rationale="why",
                call=call,
            )
        scenario.label_key = state.label_key(analysis, scenario)
        scenario.labelled_call = call
        scenario.save()
        return scenario

    return labelled


def build(analysis):
    analysis.refresh_from_db()
    return report.build(analysis)


def findings(analysis):
    return {finding["hcv_id"]: finding for finding in build(analysis)["findings"]}


def test_all_19_rows_reach_the_report(analysis, labelled):
    labelled("sc_01", *analysis.taxonomy.hcvs.values_list("hcv_id", flat=True))

    found = findings(analysis)

    assert len(found) == 21
    assert sum(len(finding["mappings"]) for finding in found.values()) == 19
    for row in analysis.taxonomy.hcvs.all():
        shown = {threat["name"] for threat in found[row.hcv_id]["threats"]}
        mapped = row.threat_mappings
        assert shown == {name for m in mapped for name in m["stride"] + m["linddun"]}
        assert found[row.hcv_id]["elements"] == [m["element"] for m in mapped]


def test_hcv13_qualifies_only_information_disclosure(analysis, labelled):
    labelled("sc_01", "HCV-13")

    threats = findings(analysis)["HCV-13"]["threats"]

    assert threats == [
        {"name": "Data disclosure", "framework": "LINDDUN", "qualifier": ""},
        {"name": "Unawareness", "framework": "LINDDUN", "qualifier": ""},
        {"name": "Non-compliance", "framework": "LINDDUN", "qualifier": ""},
        {
            "name": "Information disclosure",
            "framework": "STRIDE",
            "qualifier": "if through mobile app permissions",
        },
    ]


def test_hcv15_has_no_qualifier(analysis, labelled):
    labelled("sc_01", "HCV-15")

    assert not any(t["qualifier"] for t in findings(analysis)["HCV-15"]["threats"])


def test_a_partial_threat_without_qualifier_names_its_condition():
    mappings = [
        {"condition_class": "A", "stride": ["Spoofing"], "linddun": [], "qualifier": ""},
        {"condition_class": "B", "stride": [], "linddun": [], "qualifier": ""},
    ]

    assert report.threats(mappings)[0]["qualifier"] == "A"


def test_unmapped_four_have_no_threats(analysis, labelled):
    four = ["HCV-09", "HCV-14", "HCV-19", "HCV-20"]
    labelled("sc_01", *four)

    found = findings(analysis)

    assert set(found) == set(four)
    assert all(finding["threats"] == [] for finding in found.values())


def test_only_current_labels_of_included_scenarios_count(analysis, run, labelled):
    labelled("sc_01", "HCV-17")
    labelled("sc_02", "HCV-03", set_aside=True)
    stale = labelled("sc_03", "HCV-10")
    stale.narrative = "Edited."
    stale.save()
    analysis.scenarios.create(scenario_id="sc_04", narrative="waiting")
    failed = analysis.scenarios.create(scenario_id="sc_05", narrative="failed")
    run.calls.create(scenario=failed, error="No credit.")

    built = build(analysis)

    assert [finding["hcv_id"] for finding in built["findings"]] == ["HCV-17"]
    assert built["incomplete"] == {"unlabelled": 1, "failed": 1, "stale": 1}
    assert [row["scenario_id"] for row in built["set_aside"]] == ["sc_02"]
    shown = {row["scenario_id"]: row["labels"] for row in built["scenarios"]}
    assert shown["sc_03"] == []
    assert len(shown["sc_01"]) == 1


def test_manual_counts_and_only_manual(analysis, labelled):
    labelled("sc_01", "HCV-17")
    labelled("sc_101", "HCV-17", "HCV-10", is_manual=True)

    found = findings(analysis)

    assert (found["HCV-17"]["scenario_count"], found["HCV-17"]["manual_count"]) == (2, 1)
    assert found["HCV-17"]["only_manual"] is False
    assert found["HCV-10"]["only_manual"] is True
    assert [o["is_manual"] for o in found["HCV-17"]["occurrences"]] == [False, True]


def test_occurrences_carry_the_outcome(analysis, labelled):
    labelled("sc_01", "HCV-17", outcome="8 hours of wrong readings.")

    assert findings(analysis)["HCV-17"]["occurrences"][0]["outcome"] == "8 hours of wrong readings."


def test_order_is_count_then_id(analysis, labelled):
    labelled("sc_01", "HCV-21", "HCV-03", "HCV-18")
    labelled("sc_02", "HCV-18")

    assert list(findings(analysis)) == ["HCV-18", "HCV-03", "HCV-21"]


def test_generator_threat_never_becomes_a_report_threat(analysis, labelled):
    labelled("sc_01", "HCV-04", threat="Spoofing")

    built = build(analysis)

    assert [t["name"] for t in built["findings"][0]["threats"]] == ["Denial of service"]
    assert built["scenarios"][0]["threat"] == "Spoofing"


def test_details_list_the_setups_behind_the_report(analysis, labelled):
    generated = analysis.runs.create(
        kind="generate",
        status="completed",
        pinned={
            "model": "anthropic/claude-sonnet-5",
            "temperature": 0.8,
            "prompt_label": "Zero-shot Variant B v1",
            "examples": [],
            "count": 8,
            "is_custom": False,
            "preset_version": "1",
        },
    )
    labelled("sc_01", "HCV-17", generated_by=generated)
    labelled("sc_02", "HCV-17", generated_by=generated)

    details = build(analysis)["details"]

    assert details["taxonomy"]["version"] == "1.2"
    assert details["generation"] == {
        "model": "anthropic/claude-sonnet-5",
        "temperature": 0.8,
        "prompt": "Zero-shot Variant B v1",
        "examples": [],
        "count": 8,
        "is_custom": False,
        "preset_version": "1",
    }
    assert len(details["labelling"]) == 1
    assert details["labelling"][0]["examples"] == ["one"]
    assert details["calls_cost"] == pytest.approx(0.02)
