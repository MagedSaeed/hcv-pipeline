"""A phase 2 prompt test's own chosen inputs (`seed_systems`, `fewshots`,
`target_system`, the generation toggles and counts on `POST /api/phase-runs/`),
checked with the rules an experiment's inputs obey, at creation and again at
execution time."""

import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from corpus.models import FewShotExample, Origin, ResourceFile, ResourceKind, SystemDescription
from llm.fake import FakeLLMClient
from pipeline.clients import reset_llm_client, set_llm_client
from pipeline.execution_config import ChosenInputUnavailable, resolve_generation_config
from pipeline.models import Experiment, LLMCall, PhaseRun
from pipeline.tasks import run_phase2_task
from prompts.models import PromptTemplate
from prompts.rendering import format_hcv_table
from taxonomy.models import HCV, Taxonomy

User = get_user_model()
CARD = {
    "scenario_id": "sc_01",
    "actor": "EP1",
    "trigger": "t",
    "standing_state": "s",
    "asset": "a",
    "threat": "Spoofing.",
    "outcome": "o",
    "narrative_steps": ["EP1 acts."],
}
GENERATED = json.dumps(
    {"system_descriptions": [{"title": "Riverbend", "body": "b", "scenarios": [CARD]}]}
)


@pytest.fixture(autouse=True)
def _client():
    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.fixture
def template(db):
    return PromptTemplate.objects.create_version(
        phase=2,
        name="gen-inputs",
        user_prompt=(
            "Write {{ n_systems }}x{{ n_scenarios_per_system }}.\n"
            "Seeds: {% for s in seed_systems %}{{ s.title }};{% endfor %}\n"
            "Fewshots: {% for f in fewshots %}{{ f.name }};{% endfor %}"
        ),
    )


@pytest.fixture
def seed(db):
    return SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)


@pytest.fixture
def manual(db):
    return SystemDescription.objects.create(title="Hand-made", body="x", origin=Origin.MANUAL)


@pytest.fixture
def fewshot(db):
    return FewShotExample.objects.create(
        phase=2, name="oldsmar-example", input_text="in", output_text="out", is_active=True
    )


def _body(template, taxonomy, **overrides):
    body = {
        "phase": 2,
        "variant": "A",
        "model": "m2",
        "prompt_template": template.pk,
        "taxonomy": taxonomy.pk,
    }
    body.update(overrides)
    return body


def _create(api_client, staff, body):
    api_client.force_authenticate(user=staff)
    with patch("pipeline.orchestration.async_task"):
        return api_client.post("/api/phase-runs/", body, format="json")


def _assert_failed_with_zero_calls(phase_run_id, client, *fragments):
    phase_run = PhaseRun.objects.get(pk=phase_run_id)
    assert phase_run.status == PhaseRun.Status.FAILED
    for fragment in fragments:
        assert fragment in phase_run.error
    assert LLMCall.objects.filter(phase_run_id=phase_run_id).count() == 0
    assert client.calls == []


@pytest.mark.django_db
def test_chosen_inputs_are_stored_exposed_and_rendered(
    api_client, staff, taxonomy, template, seed, manual, fewshot, _client
):
    response = _create(
        api_client,
        staff,
        _body(
            template,
            taxonomy,
            seed_systems=[seed.pk, manual.pk],
            fewshots=[fewshot.pk],
            generate_systems=True,
            generate_scenarios=True,
            n_systems=2,
            n_scenarios_per_system=4,
        ),
    )
    assert response.status_code == 202, response.json()
    data = response.json()
    assert data["seed_systems"] == [seed.pk, manual.pk]
    assert data["fewshots"] == [fewshot.pk]
    assert data["target_system"] is None
    assert data["generate_systems"] is True

    _client.queue(GENERATED)
    assert run_phase2_task(data["id"]) == PhaseRun.Status.COMPLETED
    prompt = PhaseRun.objects.get(pk=data["id"]).calls.get().rendered_user_prompt
    assert "Write 2x4." in prompt
    assert "Hand-made;" in prompt and "Oldsmar;" in prompt
    assert "oldsmar-example;" in prompt


@pytest.mark.django_db
def test_empty_lists_mean_no_examples_and_still_run(
    api_client, staff, taxonomy, template, seed, fewshot, _client
):
    response = _create(api_client, staff, _body(template, taxonomy, seed_systems=[], fewshots=[]))
    assert response.status_code == 202, response.json()
    assert response.json()["seed_systems"] == []

    _client.queue(GENERATED)
    assert run_phase2_task(response.json()["id"]) == PhaseRun.Status.COMPLETED
    prompt = PhaseRun.objects.get(pk=response.json()["id"]).calls.get().rendered_user_prompt
    assert "Oldsmar" not in prompt
    assert "oldsmar-example" not in prompt


@pytest.mark.django_db
def test_a_generated_seed_system_is_refused(api_client, staff, taxonomy, template):
    generated = SystemDescription.objects.create(
        title="From a model", body="x", origin=Origin.GENERATED
    )
    response = _create(api_client, staff, _body(template, taxonomy, seed_systems=[generated.pk]))
    assert response.status_code == 400
    assert "From a model" in str(response.json()["seed_systems"])
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
@pytest.mark.parametrize(
    "fields",
    [{"phase": 3, "is_active": True}, {"phase": 2, "is_active": False}],
    ids=["phase-3", "inactive"],
)
def test_only_active_phase_2_fewshots_can_be_chosen(api_client, staff, taxonomy, template, fields):
    other = FewShotExample.objects.create(name="other", input_text="i", output_text="o", **fields)
    response = _create(api_client, staff, _body(template, taxonomy, fewshots=[other.pk]))
    assert response.status_code == 400
    assert "other" in str(response.json()["fewshots"])


@pytest.mark.django_db
def test_variant_a_refuses_a_taxonomy_resource_fewshot_but_variant_b_accepts_it(
    api_client, staff, taxonomy, template
):
    resource = ResourceFile.objects.create(
        name="hcv-table", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    leaky = FewShotExample.objects.create(
        phase=2, name="leaky", input_file=resource, output_text="ok", is_active=True
    )

    refused = _create(api_client, staff, _body(template, taxonomy, fewshots=[leaky.pk]))
    assert refused.status_code == 400
    assert "leaky" in str(refused.json()["fewshots"])
    assert "taxonomy resource" in str(refused.json()["fewshots"])

    accepted = _create(
        api_client, staff, _body(template, taxonomy, variant="B", fewshots=[leaky.pk])
    )
    assert accepted.status_code == 202, accepted.json()


@pytest.mark.django_db
def test_a_fewshot_switched_to_a_taxonomy_resource_after_creation_fails_with_zero_calls(
    api_client, staff, taxonomy, template, fewshot, _client
):
    response = _create(api_client, staff, _body(template, taxonomy, fewshots=[fewshot.pk]))
    assert response.status_code == 202
    fewshot.input_file = ResourceFile.objects.create(
        name="hcv-table", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    fewshot.input_text = ""
    fewshot.save()
    _client.queue(GENERATED)  # would be consumed if the guard did not fire

    assert run_phase2_task(response.json()["id"]) == PhaseRun.Status.FAILED
    _assert_failed_with_zero_calls(
        response.json()["id"], _client, "Missing input:", "oldsmar-example"
    )


@pytest.mark.django_db
def test_a_seed_system_deleted_after_creation_fails_the_run_not_drops_it(
    api_client, staff, taxonomy, template, seed, manual, _client
):
    response = _create(
        api_client, staff, _body(template, taxonomy, seed_systems=[seed.pk, manual.pk])
    )
    assert response.status_code == 202
    manual.delete()

    assert run_phase2_task(response.json()["id"]) == PhaseRun.Status.FAILED
    _assert_failed_with_zero_calls(response.json()["id"], _client, "no longer exist")


@pytest.mark.django_db
def test_a_target_system_deleted_after_creation_fails_the_run(
    api_client, staff, taxonomy, template, manual, _client
):
    response = _create(
        api_client,
        staff,
        _body(template, taxonomy, generate_systems=False, target_system=manual.pk),
    )
    assert response.status_code == 202, response.json()
    assert response.json()["target_system"] == manual.pk
    manual.delete()

    assert run_phase2_task(response.json()["id"]) == PhaseRun.Status.FAILED
    _assert_failed_with_zero_calls(response.json()["id"], _client, "target system")


@pytest.mark.django_db
def test_the_experiment_generation_rules_apply(api_client, staff, taxonomy, template):
    generated = SystemDescription.objects.create(title="Model-made", body="x", origin="generated")
    cases = [
        ({"generate_systems": False, "generate_scenarios": False}, "generate_systems"),
        ({"generate_systems": False}, "target_system"),
        ({"generate_systems": False, "target_system": generated.pk}, "target_system"),
        ({"target_system": 999999}, "target_system"),
        ({"n_systems": 51}, "n_systems"),
    ]
    for overrides, field in cases:
        response = _create(api_client, staff, _body(template, taxonomy, **overrides))
        assert response.status_code == 400, overrides
        assert field in response.json(), (overrides, response.json())
    assert PhaseRun.objects.count() == 0


@pytest.mark.django_db
def test_the_resolver_returns_the_chosen_rows_and_values(taxonomy, template, seed, manual, fewshot):
    phase_run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=template,
        taxonomy=taxonomy,
        seed_system_ids=[seed.pk],
        fewshot_ids=[fewshot.pk],
        target_system_pk=manual.pk,
        generate_systems=False,
        generate_scenarios=True,
        n_scenarios_per_system=2,
    )

    config = resolve_generation_config(phase_run)

    assert [s.pk for s in config["seed_systems"]] == [seed.pk]
    assert [f.pk for f in config["fewshots"]] == [fewshot.pk]
    assert config["target_system"].pk == manual.pk
    assert config["target_system_id"] == manual.pk
    assert config["generate_systems"] is False
    assert config["n_systems"] == 1  # unchosen: the built-in default
    assert config["n_scenarios_per_system"] == 2


@pytest.mark.django_db
def test_the_resolver_refuses_a_taxonomy_resource_fewshot_for_variant_a(taxonomy, template):
    resource = ResourceFile.objects.create(name="t", kind=ResourceKind.TAXONOMY, text="HCV-07")
    leaky = FewShotExample.objects.create(
        phase=2, name="leaky", input_text="i", output_file=resource, is_active=True
    )
    phase_run = PhaseRun.objects.create(
        phase=2,
        variant="A",
        model="m2",
        prompt_template=template,
        taxonomy=taxonomy,
        fewshot_ids=[leaky.pk],
    )

    with pytest.raises(ChosenInputUnavailable, match="taxonomy resource"):
        resolve_generation_config(phase_run)


@pytest.mark.django_db
def test_chosen_inputs_are_refused_with_a_borrowed_experiment_or_for_phase_3(
    api_client, staff, taxonomy, template, seed
):
    experiment = Experiment.objects.create(
        name="borrowable", taxonomy=taxonomy, phase2_model="m2", phase2_prompt_a=template
    )
    borrowed = _create(
        api_client,
        staff,
        _body(template, taxonomy, experiment=experiment.pk, seed_systems=[seed.pk]),
    )
    assert borrowed.status_code == 400
    assert "borrows an experiment" in str(borrowed.json()["seed_systems"])

    lab = PromptTemplate.objects.create_version(phase=3, name="lab", user_prompt="x")
    labelling = _create(
        api_client,
        staff,
        {
            "phase": 3,
            "model": "m3",
            "prompt_template": lab.pk,
            "taxonomy": taxonomy.pk,
            "scenarios": [1],
            "generate_systems": True,
        },
    )
    assert labelling.status_code == 400
    assert "generation test" in str(labelling.json()["generate_systems"])


@pytest.mark.django_db
def test_the_database_refuses_chosen_inputs_on_a_borrowing_run(taxonomy, template, seed):
    experiment = Experiment.objects.create(
        name="borrowable", taxonomy=taxonomy, phase2_model="m2", phase2_prompt_a=template
    )
    with pytest.raises(IntegrityError):
        PhaseRun.objects.create(
            phase=2,
            variant="A",
            model="m2",
            prompt_template=template,
            taxonomy=taxonomy,
            experiment=experiment,
            seed_system_ids=[seed.pk],
        )
