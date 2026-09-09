import pytest
from django.core.management import call_command
from django.db import IntegrityError

from corpus.models import Origin, Scenario, SystemDescription
from taxonomy.models import HCV


@pytest.fixture
def system(db):
    return SystemDescription.objects.create(
        title="Oldsmar Water Treatment", body="...", origin=Origin.SEED
    )


@pytest.mark.django_db
def test_slug_is_derived_from_the_title(system):
    assert system.slug == "oldsmar-water-treatment"


@pytest.mark.django_db
def test_slug_stays_unique(system):
    second = SystemDescription.objects.create(
        title="Oldsmar Water Treatment", body="...", origin=Origin.MANUAL
    )
    assert second.slug == "oldsmar-water-treatment-2"


@pytest.mark.django_db
def test_seed_and_manual_are_editable_but_generated_is_not(system):
    assert system.is_editable is True

    manual = SystemDescription.objects.create(
        title="Hand written", body="...", origin=Origin.MANUAL
    )
    assert manual.is_editable is True

    generated = SystemDescription.objects.create(
        title="From the model", body="...", origin=Origin.GENERATED
    )
    assert generated.is_editable is False


@pytest.mark.django_db
def test_scenario_card_collects_the_six_fields(system):
    scenario = Scenario.objects.create(
        system_description=system,
        scenario_id="sc_01",
        actor="EP1",
        trigger="After 22:00.",
        standing_state="PlantConsoleAuth protects the SCADA Service.",
        asset="The SCADA Service",
        threat="Spoofing, then Tampering.",
        outcome="The feed ran at 11,100 ppm for 8 hours.",
        narrative="1. EP1 opens the session.",
        origin=Origin.SEED,
        order=1,
    )
    assert scenario.card == {
        "actor": "EP1",
        "trigger": "After 22:00.",
        "standing_state": "PlantConsoleAuth protects the SCADA Service.",
        "asset": "The SCADA Service",
        "threat": "Spoofing, then Tampering.",
        "outcome": "The feed ran at 11,100 ppm for 8 hours.",
    }
    assert scenario.abox is None
    assert scenario.is_editable is True


@pytest.mark.django_db
def test_scenario_id_is_unique_among_rows_with_no_generation_run(system):
    """The run-scoped constraint alone would not catch this: both rows share
    generation_run=NULL, and NULLs compare as distinct there. The separate
    conditional constraint (generation_run IS NULL) is what still rejects a
    plain duplicate among seed/manual rows.
    """
    Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    with pytest.raises(IntegrityError):
        Scenario.objects.create(
            system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=2
        )


@pytest.mark.django_db
def test_scenario_id_is_not_unique_across_systems(system):
    other_system = SystemDescription.objects.create(
        title="Another System", body="...", origin=Origin.SEED
    )
    Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    Scenario.objects.create(
        system_description=other_system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    assert Scenario.objects.filter(scenario_id="sc_01").count() == 2


@pytest.mark.django_db
def test_deleting_a_system_removes_its_scenarios(system):
    Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    system.delete()
    assert Scenario.objects.count() == 0


@pytest.mark.django_db
def test_intended_hcvs_records_expert_intent(system):
    call_command("import_taxonomy", "--activate")
    scenario = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    scenario.intended_hcvs.add(HCV.objects.get(hcv_id="HCV-07"))
    assert [row.hcv_id for row in scenario.intended_hcvs.all()] == ["HCV-07"]
