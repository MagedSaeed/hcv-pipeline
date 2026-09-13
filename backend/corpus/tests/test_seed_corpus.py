import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from corpus.models import FewShotExample, Origin, ResourceFile, Scenario, SystemDescription


@pytest.mark.django_db
def test_seeds_the_oldsmar_pack():
    call_command("seed_corpus")

    system = SystemDescription.objects.get(origin=Origin.SEED)
    assert system.title == "Oldsmar Water Treatment"
    assert "Bruce T. Haddock Water Treatment Plant" in system.body

    scenarios = list(system.scenarios.all())
    assert [row.scenario_id for row in scenarios] == ["sc_01", "sc_02", "sc_03"]
    assert scenarios[0].origin == Origin.SEED
    assert scenarios[0].threat == "Spoofing, then Tampering."
    assert scenarios[0].order == 1


@pytest.mark.django_db
def test_registers_resources_and_a_phase_2_fewshot():
    call_command("seed_corpus")

    assert ResourceFile.objects.filter(name="oldsmar-system-description").exists()
    assert ResourceFile.objects.filter(name="oldsmar-scenarios").exists()
    assert ResourceFile.objects.filter(name="hcv-vocabulary").exists()

    example = FewShotExample.objects.get(phase=2, name="oldsmar")
    assert "Bruce T. Haddock" in example.resolved_input
    assert "sc_01" in example.resolved_output


@pytest.mark.django_db
def test_reseeding_is_idempotent():
    call_command("seed_corpus")
    call_command("seed_corpus")
    assert SystemDescription.objects.count() == 1
    assert Scenario.objects.count() == 3
    assert FewShotExample.objects.count() == 2


@pytest.mark.django_db
def test_unknown_pack_reports_clearly():
    with pytest.raises(CommandError, match="No seed pack"):
        call_command("seed_corpus", "--pack", "atlantis")
