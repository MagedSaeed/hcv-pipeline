import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from corpus.models import Scenario, SystemDescription
from taxonomy.models import HCV, Taxonomy

# Rows the ontology deliberately carries no condition class for.
ROWS_WITHOUT_CONDITION_CLASSES = {"HCV-09", "HCV-14", "HCV-19", "HCV-20"}


def write_taxonomy(path, rows, version="1.1"):
    path.write_text(
        json.dumps({"vocabulary_id": "HCV-21", "version": version, "rows": rows}),
        encoding="utf-8",
    )
    return path


@pytest.mark.django_db
def test_imports_the_real_seed_file():
    call_command("import_taxonomy", "--activate")

    taxonomy = Taxonomy.objects.get(vocabulary_id="HCV-21", version="1.1")
    assert taxonomy.is_active is True
    assert taxonomy.hcvs.count() == 21

    first = taxonomy.hcvs.get(hcv_id="HCV-01")
    assert first.name == "Limited User Involvement in AI-based Authentication"
    assert first.condition_classes == ["LimitedAuthenticationInvolvementCondition"]
    assert first.order == 1

    empty = {row.hcv_id for row in taxonomy.hcvs.all() if not row.condition_classes}
    assert empty == ROWS_WITHOUT_CONDITION_CLASSES


@pytest.mark.django_db
def test_source_json_is_kept_verbatim():
    call_command("import_taxonomy")
    taxonomy = Taxonomy.objects.get(version="1.1")
    assert len(taxonomy.source_json["rows"]) == 21


@pytest.mark.django_db
def test_reimport_keeps_the_primary_keys_of_unchanged_rows():
    """A taxonomy is immutable once referenced, so its rows keep their identity.

    Re-importing used to delete every row and insert fresh ones, which silently
    severed anything pointing at an HCV (and would CASCADE labels away once
    Label.hcv exists).
    """
    call_command("import_taxonomy")
    before = {row.hcv_id: row.pk for row in HCV.objects.all()}

    call_command("import_taxonomy")

    assert Taxonomy.objects.count() == 1
    assert HCV.objects.count() == 21
    assert {row.hcv_id: row.pk for row in HCV.objects.all()} == before


@pytest.mark.django_db
def test_reimport_keeps_scenario_hcv_links_intact():
    call_command("import_taxonomy")
    system = SystemDescription.objects.create(title="Oldsmar Water Treatment")
    scenario = Scenario.objects.create(system_description=system, scenario_id="sc_01")
    scenario.intended_hcvs.add(HCV.objects.get(hcv_id="HCV-01"))

    call_command("import_taxonomy")

    assert [row.hcv_id for row in scenario.intended_hcvs.all()] == ["HCV-01"]


@pytest.mark.django_db
def test_reimport_updates_a_changed_row_in_place(tmp_path):
    path = write_taxonomy(tmp_path / "v.json", [{"hcv_id": "HCV-01", "name": "Original"}])
    call_command("import_taxonomy", str(path))
    original_pk = HCV.objects.get(hcv_id="HCV-01").pk

    write_taxonomy(path, [{"hcv_id": "HCV-01", "name": "Renamed", "definition": "new definition"}])
    call_command("import_taxonomy", str(path))

    row = HCV.objects.get(hcv_id="HCV-01")
    assert row.pk == original_pk
    assert row.name == "Renamed"
    assert row.definition == "new definition"


@pytest.mark.django_db
def test_reimport_drops_rows_that_left_the_file(tmp_path):
    """The database still mirrors the file exactly: a removed row is removed."""
    path = write_taxonomy(
        tmp_path / "v.json",
        [{"hcv_id": "HCV-01", "name": "Kept"}, {"hcv_id": "HCV-02", "name": "Dropped"}],
    )
    call_command("import_taxonomy", str(path))
    kept_pk = HCV.objects.get(hcv_id="HCV-01").pk

    write_taxonomy(path, [{"hcv_id": "HCV-01", "name": "Kept"}])
    call_command("import_taxonomy", str(path))

    assert [row.hcv_id for row in HCV.objects.all()] == ["HCV-01"]
    assert HCV.objects.get(hcv_id="HCV-01").pk == kept_pk


@pytest.mark.django_db
def test_activate_deactivates_other_versions(tmp_path):
    call_command("import_taxonomy", "--activate")
    other = tmp_path / "v2.json"
    other.write_text(
        json.dumps(
            {
                "vocabulary_id": "HCV-21",
                "version": "2.0",
                "rows": [
                    {"hcv_id": "HCV-01", "name": "A", "definition": "d", "condition_classes": []}
                ],
            }
        )
    )
    call_command("import_taxonomy", str(other), "--activate")

    assert Taxonomy.objects.get(version="1.1").is_active is False
    assert Taxonomy.objects.get(version="2.0").is_active is True


@pytest.mark.django_db
def test_missing_file_reports_clearly(tmp_path):
    with pytest.raises(CommandError, match="not found"):
        call_command("import_taxonomy", str(tmp_path / "absent.json"))


@pytest.mark.django_db
def test_rejects_a_file_with_no_rows(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"vocabulary_id": "X", "version": "1", "rows": []}))
    with pytest.raises(CommandError, match="no rows"):
        call_command("import_taxonomy", str(bad))


@pytest.mark.django_db
def test_a_row_missing_a_required_field_names_the_row(tmp_path):
    bad = write_taxonomy(
        tmp_path / "bad.json",
        [{"hcv_id": "HCV-01", "name": "Fine"}, {"hcv_id": "HCV-02"}],
    )
    with pytest.raises(CommandError, match="row 2"):
        call_command("import_taxonomy", str(bad))

    # The whole import is one transaction, so a bad row leaves nothing behind.
    assert Taxonomy.objects.count() == 0
    assert HCV.objects.count() == 0


@pytest.mark.django_db
def test_a_repeated_hcv_id_names_the_row(tmp_path):
    bad = write_taxonomy(
        tmp_path / "bad.json",
        [{"hcv_id": "HCV-01", "name": "First"}, {"hcv_id": "HCV-01", "name": "Again"}],
    )
    with pytest.raises(CommandError, match="row 2 repeats"):
        call_command("import_taxonomy", str(bad))

    assert HCV.objects.count() == 0
