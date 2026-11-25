import io
import re
import unicodedata

import pytest
from pypdf import PdfReader

from analyses import state

pytestmark = pytest.mark.django_db


@pytest.fixture
def labelled(analysis):
    run = analysis.runs.create(
        kind="label",
        status="completed",
        pinned={
            "model": "openai/gpt-5.6-luna-pro",
            "temperature": 0.0,
            "examples": [],
            "prompt_label": "p",
        },
    )

    def labelled(scenario_id, hcv_id, **fields):
        scenario = analysis.scenarios.create(scenario_id=scenario_id, **fields)
        call = run.calls.create(scenario=scenario, parsed_ok=True)
        if hcv_id:
            scenario.labels.create(
                hcv=analysis.taxonomy.hcvs.get(hcv_id=hcv_id),
                evidence="enters behind",
                rationale="Follows staff in.",
                call=call,
            )
        scenario.label_key = state.label_key(analysis, scenario)
        scenario.labelled_call = call
        scenario.save()
        return scenario

    return labelled


def pdf_text(response):
    reader = PdfReader(io.BytesIO(response.content))
    text = "\n".join(page.extract_text() for page in reader.pages)
    return unicodedata.normalize("NFKC", " ".join(text.split()))


def test_report_pdf_opens_in_the_browser(client_for, staff, analysis, labelled):
    labelled("sc_01", "HCV-13", trigger="A resident installs the app.", narrative="It asks.")

    response = client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == 'inline; filename="Smart Parking report.pdf"'
    assert response.content.startswith(b"%PDF")


def test_report_pdf_holds_the_whole_report(client_for, staff, analysis, labelled):
    labelled(
        "sc_01",
        "HCV-13",
        trigger="A resident installs the app.",
        narrative="It asks.",
        outcome="22,000 locations kept.",
    )
    labelled(
        "sc_101",
        "HCV-10",
        trigger="",
        narrative="A cleaner enters behind an officer.",
        is_manual=True,
    )
    labelled("sc_02", None, trigger="A badge is kept.", narrative="x", set_aside=True)

    text = pdf_text(client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/"))

    for expected in [
        "Smart Parking",
        "2 potential vulnerabilities",
        "Scenarios analysed",
        "1 Summary",
        "2 Vulnerabilities",
        "3 Scenarios",
        "4 Research details",
        "Check them against the real system",
        "Inadequate Privacy Settings",
        "Information disclosure (if through mobile app permissions)",
        "STRIDE",
        "LINDDUN",
        "Data disclosure",
        "Service delivered by the MobileApp",
        "Tailgating",
        "Only in manually added scenarios",
        "Added manually",
        "A resident installs the app.",
        "Outcome: 22,000 locations kept.",
        "Follows staff in.",
        "From the scenario: “enters behind”",
        "Set aside, not in this report",
        "A badge is kept.",
        "Research details",
        "HCV-21 v1.2",
        "UnscopedDevicePermissionCondition (rule E13c)",
        "openai/gpt-5.6-luna-pro",
        "Identification",
    ]:
        assert expected in text, expected
    assert "Labelling" not in text


def test_report_pdf_numbers_its_pages(client_for, staff, analysis, labelled):
    for number in range(1, 13):
        labelled(f"sc_{number:02d}", "HCV-17", trigger=f"Occasion {number}.", narrative="x " * 200)

    reader = PdfReader(
        io.BytesIO(client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/").content)
    )

    total = len(reader.pages)
    assert total > 1
    assert f"Page {total} of {total}" in reader.pages[-1].extract_text()


def test_report_pdf_survives_a_scenario_longer_than_a_page(client_for, staff, analysis, labelled):
    labelled("sc_01", "HCV-17", trigger="Long.", narrative="A very long account. " * 900)

    response = client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/")

    assert response.status_code == 200
    assert len(PdfReader(io.BytesIO(response.content)).pages) > 2


def test_report_pdf_writes_arabic(client_for, staff, analysis, labelled):
    analysis.name = "مواقف ذكية"
    analysis.save()
    labelled("sc_01", "HCV-10", trigger="يدخل عامل النظافة خلف موظف.", narrative="x")

    text = pdf_text(client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/"))

    # Shaped Arabic glyphs normalise back to Arabic letters; boxes would not.
    assert sum(1 for char in text if "؀" <= char <= "ۿ") > 15


def test_report_pdf_filename_is_safe(client_for, staff, analysis):
    analysis.name = 'A "quoted"\nname/with:odd\\chars'
    analysis.save()

    response = client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/")

    assert (
        response["Content-Disposition"]
        == 'inline; filename="A quoted name with odd chars report.pdf"'
    )


def test_report_pdf_reads_like_the_report(client_for, member, analysis, api_client):
    assert client_for(member).get(f"/api/analyses/{analysis.pk}/report/pdf/").status_code == 200
    api_client.force_authenticate(user=None)
    assert api_client.get(f"/api/analyses/{analysis.pk}/report/pdf/").status_code == 403


def test_report_pdf_uses_the_bold_face(client_for, staff, analysis, labelled):
    labelled("sc_01", "HCV-17", trigger="Occasion.", narrative="x")

    reader = PdfReader(
        io.BytesIO(client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/").content)
    )

    fonts = {
        str(font.get_object().get("/BaseFont"))
        for page in reader.pages
        for font in (page["/Resources"].get("/Font") or {}).values()
    }
    assert any("Bold" in name for name in fonts)
    assert any("Bold" not in name for name in fonts)


def test_report_pdf_keeps_colour_to_one_accent(client_for, staff, analysis, labelled):
    labelled("sc_01", "HCV-13", trigger="Occasion.", narrative="x")

    content = client_for(staff).get(f"/api/analyses/{analysis.pk}/report/pdf/").content
    reader = PdfReader(io.BytesIO(content))
    streams = b"".join(page.get_contents().get_data() for page in reader.pages)

    # Colours used: neutral greys plus a single accent.
    found = set(re.findall(rb"([\d.]+ [\d.]+ [\d.]+) (?:rg|RG)", streams))

    def saturated(colour):
        values = [float(v) for v in colour.split()]
        return max(values) - min(values) > 0.12

    assert len({c for c in found if saturated(c)}) <= 1
