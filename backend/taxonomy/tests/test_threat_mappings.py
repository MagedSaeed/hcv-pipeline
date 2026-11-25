import json

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.utils import timezone

from taxonomy.models import HCV, Taxonomy

V11 = settings.RESOURCES_DIR / "taxonomy/hcv21_v1.1.json"
V12 = settings.RESOURCES_DIR / "taxonomy/hcv21_v1.2.json"

# The owner's table, one line per row: HCV|condition|element|rule|STRIDE|LINDDUN
TABLE = """
01|LimitedAuthenticationInvolvement|Element protected by the AuthenticationMechanism|E1|S|
02|PoorParticipatoryInterface|Service behind the Interface|E2||U
03|UnmonitoredAutomation|The Service itself||T D|
04|MissingManualOverride|The Service itself||D|
05|ConsequentialEmotionDecision|The EmotionRecognitionService itself|||U DD NC
06|InboundExternalCommunication|The inbound DataFlow itself||S I|
07|PrivilegedInsiderAccess|Asset reached through the AccessGrant|E7|T I D E|
08|RemovableMediaEndpoint|The SharedEndpoint or OperatorWorkstation itself||T I E|
10|UnchallengedPhysicalEntry|RestrictedArea behind the PhysicalEntryPoint|E10|S T I E|
11|UnverifiedDomainAccess|Service behind the WebAccessChannel|E11|S I|
12|UnverifiedContactChannel|The outbound DataFlow itself||S I|
13|PermissiveDefaultPrivacy|The Service itself|||DD U NC
13|UnscopedDevicePermission|Service delivered by the MobileApp|E13c|I|DD U NC
15|UninformedCollection|The UserInputFlow itself|||DD U NC
15|UninformedTransfer|The DataFlow to the third party provider itself|||DD U NC
16|UnmanagedDisposal|The DataStore itself||I|DD NC
17|UnprotectedPasswordAuthentication|Element protected by the AuthenticationMechanism|E17|S|
18|UnreviewedAccessGrant|Asset reached through the AccessGrant|E18|E I|
21|ShippedDefaultConfiguration|The IoTDevice itself||S T I E|
"""
NAMES = {
    "S": "Spoofing",
    "T": "Tampering",
    "I": "Information disclosure",
    "D": "Denial of service",
    "E": "Elevation of privilege",
    "U": "Unawareness",
    "DD": "Data disclosure",
    "NC": "Non-compliance",
}


def expected_mappings():
    expected = {}
    for line in TABLE.strip().splitlines():
        number, condition, element, rule, stride, linddun = line.split("|")
        expected.setdefault(f"HCV-{number}", []).append(
            {
                "condition_class": f"{condition}Condition",
                "element": element,
                "rule": rule,
                "stride": [NAMES[code] for code in stride.split()],
                "linddun": [NAMES[code] for code in linddun.split()],
                "qualifier": (
                    "if through mobile app permissions"
                    if condition == "UnscopedDevicePermission"
                    else ""
                ),
            }
        )
    return expected


def rows_of(path):
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(
        email="staff@example.com", password="pw-123456", is_staff=True
    )


@pytest.fixture
def v12(db):
    call_command("import_taxonomy", "--activate")
    call_command("import_taxonomy", str(V12), "--if-missing", "--analysis-default")
    return Taxonomy.objects.get(version="1.2")


def test_v12_keeps_every_id_name_and_definition_of_v11():
    def wording(path):
        return [(r["hcv_id"], r["name"], r["definition"]) for r in rows_of(path)]

    assert wording(V12) == wording(V11)


def test_v12_has_the_19_rows_of_the_spec():
    expected = expected_mappings()
    actual = {row["hcv_id"]: row["threat_mappings"] for row in rows_of(V12)}

    assert sum(len(rows) for rows in actual.values()) == 19
    assert {key: rows for key, rows in actual.items() if rows} == expected
    assert {key for key, rows in actual.items() if not rows} == {
        "HCV-09",
        "HCV-14",
        "HCV-19",
        "HCV-20",
    }


def test_v12_condition_classes_equal_its_mappings():
    for row in rows_of(V12):
        assert row["condition_classes"] == [m["condition_class"] for m in row["threat_mappings"]]


@pytest.mark.django_db
def test_import_does_not_activate_v12(v12):
    assert Taxonomy.active().version == "1.1"
    assert Taxonomy.for_analyses() == v12
    assert v12.hcvs.get(hcv_id="HCV-17").threat_mappings == expected_mappings()["HCV-17"]


@pytest.mark.django_db
def test_import_never_moves_an_existing_default(v12, tmp_path):
    path = tmp_path / "next.json"
    payload = json.loads(V12.read_text(encoding="utf-8")) | {"version": "1.3"}
    path.write_text(json.dumps(payload), encoding="utf-8")

    call_command("import_taxonomy", str(path), "--analysis-default")

    assert Taxonomy.for_analyses() == v12


@pytest.mark.django_db
def test_importer_rejects_an_unknown_category(tmp_path):
    payload = json.loads(V12.read_text(encoding="utf-8"))
    payload["rows"][0]["threat_mappings"][0]["stride"] = ["Mischief"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CommandError, match="Mischief"):
        call_command("import_taxonomy", str(path))


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change",
    [{"stride": ["Mischief"]}, {"linddun": ["Spoofing"]}, {"condition_class": ""}, {"extra": 1}],
)
def test_api_rejects_an_invalid_mapping(api_client, staff, v12, change):
    row = v12.hcvs.get(hcv_id="HCV-17")
    api_client.force_authenticate(user=staff)

    response = api_client.patch(
        f"/api/hcvs/{row.pk}/",
        {"threat_mappings": [{**row.threat_mappings[0], **change}]},
        format="json",
    )

    assert response.status_code == 400
    assert "threat_mappings" in response.json()


@pytest.mark.django_db
def test_api_saves_a_valid_mapping(api_client, staff, v12):
    row = v12.hcvs.get(hcv_id="HCV-09")
    mapping = expected_mappings()["HCV-02"]
    api_client.force_authenticate(user=staff)

    response = api_client.patch(f"/api/hcvs/{row.pk}/", {"threat_mappings": mapping}, format="json")

    assert response.status_code == 200
    assert response.json()["threat_mappings"] == mapping


@pytest.mark.django_db
def test_api_always_returns_a_list(api_client, staff):
    taxonomy = Taxonomy.objects.create(vocabulary_id="OLD", version="1")
    HCV.objects.create(taxonomy=taxonomy, hcv_id="X-1", name="Old row")
    api_client.force_authenticate(user=staff)

    rows = api_client.get(f"/api/taxonomies/{taxonomy.pk}/hcvs/").json()["results"]

    assert rows[0]["threat_mappings"] == []


@pytest.mark.django_db
def test_new_version_copies_mappings_but_not_the_default(api_client, staff, v12):
    api_client.force_authenticate(user=staff)

    response = api_client.post(
        f"/api/taxonomies/{v12.pk}/new-version/", {"version": "1.3"}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["is_analysis_default"] is False
    copy = Taxonomy.objects.get(version="1.3")
    assert copy.hcvs.get(hcv_id="HCV-17").threat_mappings == expected_mappings()["HCV-17"]


@pytest.mark.django_db
def test_staff_move_the_analysis_default_without_activating(api_client, staff, v12):
    v11 = Taxonomy.active()
    api_client.force_authenticate(user=staff)

    response = api_client.post(f"/api/taxonomies/{v11.pk}/analysis-default/")

    assert response.status_code == 200
    assert response.json()["is_analysis_default"] is True
    assert Taxonomy.for_analyses() == v11
    assert Taxonomy.active() == v11


@pytest.mark.django_db
def test_members_cannot_move_the_analysis_default(api_client, v12):
    member = get_user_model().objects.create_user(email="m@example.com", password="pw-123456")
    api_client.force_authenticate(user=member)

    assert api_client.post(f"/api/taxonomies/{v12.pk}/analysis-default/").status_code == 403


@pytest.mark.django_db
def test_export_rows_carry_threat_mappings(v12):
    from pipeline import export
    from pipeline.models import PhaseRun
    from prompts.models import PromptTemplate

    template = PromptTemplate.objects.create(phase=3, name="label", version=1, user_prompt="x")
    run = PhaseRun.objects.create(phase=3, model="m", prompt_template=template, taxonomy=v12)

    rows = export._taxonomy([run.pk])[0]["rows"]

    by_id = {row["hcv_id"]: row["threat_mappings"] for row in rows}
    assert by_id["HCV-17"] == expected_mappings()["HCV-17"]
    assert by_id["HCV-09"] == []


@pytest.mark.django_db
def test_old_code_can_insert():
    """Code at 0620f6c names none of the new columns, so a revert must still write."""
    now = timezone.now()
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO taxonomy_taxonomy (vocabulary_id, version, notes, is_active, created_at)"
            " VALUES ('OLD', '1', '', %s, %s)",
            [False, now],
        )
        taxonomy_id = Taxonomy.objects.values_list("pk", flat=True).get(vocabulary_id="OLD")
        cursor.execute(
            "INSERT INTO taxonomy_hcv"
            ' (taxonomy_id, hcv_id, name, definition, condition_classes, "order")'
            " VALUES (%s, 'X-1', 'Old', '', '[]', 1)",
            [taxonomy_id],
        )

    row = HCV.objects.get(hcv_id="X-1")
    assert row.threat_mappings is None
    assert row.taxonomy.is_analysis_default is False
