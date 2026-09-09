import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, SystemDescription
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def template(db):
    return PromptTemplate.objects.create_version(
        phase=2, name="generate", user_prompt="Make {{ n_systems }} systems."
    )


@pytest.fixture
def taxonomy(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.mark.django_db
def test_listing_requires_authentication(api_client, template):
    assert api_client.get("/api/prompts/").status_code == 403


@pytest.mark.django_db
def test_any_authenticated_user_may_read(api_client, member, template):
    api_client.force_authenticate(user=member)
    body = api_client.get("/api/prompts/").json()
    assert body["count"] == 1
    assert body["results"][0]["name"] == "generate"


@pytest.mark.django_db
def test_non_staff_cannot_create(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/prompts/", {"phase": 2, "name": "x", "user_prompt": "y"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_staff_creates_version_one(api_client, staff):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/",
        {"phase": 2, "name": "fresh", "user_prompt": "y"},
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["version"] == 1
    assert PromptTemplate.objects.get(name="fresh").created_by == staff


@pytest.mark.django_db
def test_patch_creates_a_new_version_and_leaves_the_original(api_client, staff, template):
    api_client.force_authenticate(user=staff)
    response = api_client.patch(
        f"/api/prompts/{template.id}/", {"user_prompt": "Rewritten."}, format="json"
    )

    assert response.status_code == 201
    assert response.json()["version"] == 2
    assert response.json()["user_prompt"] == "Rewritten."
    assert response.json()["id"] != template.id

    template.refresh_from_db()
    assert template.user_prompt == "Make {{ n_systems }} systems."


@pytest.mark.django_db
def test_filter_by_phase(api_client, member, template):
    PromptTemplate.objects.create_version(phase=3, name="label", user_prompt="z")
    api_client.force_authenticate(user=member)
    rows = api_client.get("/api/prompts/?phase=3").json()["results"]
    assert [row["name"] for row in rows] == ["label"]


@pytest.mark.django_db
def test_bad_phase_filter_is_a_400(api_client, member, template):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/prompts/?phase=abc").status_code == 400


@pytest.mark.django_db
def test_preview_returns_the_exact_rendered_strings(api_client, staff, template):
    system = SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "n_systems": 4, "n_scenarios_per_system": 5, "seed_systems": [system.id]},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["user_prompt"] == "Make 4 systems."
    assert response.json()["system_prompt"] == ""


@pytest.mark.django_db
def test_preview_of_variant_a_with_a_taxonomy_is_refused(api_client, staff, template, taxonomy):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "taxonomy": taxonomy.id, "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 400
    assert "Run A" in str(response.json())


@pytest.mark.django_db
def test_preview_of_variant_b_injects_the_taxonomy(api_client, staff, taxonomy):
    guided = PromptTemplate.objects.create_version(
        phase=2, name="guided", user_prompt="{{ hcv_table() }}"
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{guided.id}/preview/",
        {"variant": "B", "taxonomy": taxonomy.id, "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 200
    assert "HCV-07" in response.json()["user_prompt"]


@pytest.mark.django_db
def test_preview_reports_a_template_error_as_400(api_client, staff):
    broken = PromptTemplate.objects.create_version(phase=2, name="broken", user_prompt="{{ nope }}")
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{broken.id}/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 400
    assert "nope" in str(response.json())


@pytest.mark.django_db
def test_non_staff_cannot_patch(api_client, member, template):
    api_client.force_authenticate(user=member)
    response = api_client.patch(
        f"/api/prompts/{template.id}/", {"user_prompt": "hack"}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_non_staff_cannot_put(api_client, member, template):
    api_client.force_authenticate(user=member)
    response = api_client.put(
        f"/api/prompts/{template.id}/",
        {"phase": 2, "name": "generate", "user_prompt": "hack"},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_non_staff_cannot_preview(api_client, member, template):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_post_ignores_version_and_created_by_from_the_payload(api_client, staff, member):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/",
        {
            "phase": 2,
            "name": "spoofed",
            "user_prompt": "y",
            "version": 999,
            "created_by": member.id,
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.json()["version"] == 1
    created = PromptTemplate.objects.get(name="spoofed")
    assert created.version == 1
    assert created.created_by == staff


@pytest.mark.django_db
def test_is_active_is_read_only_on_both_create_and_patch(api_client, staff, template):
    api_client.force_authenticate(user=staff)

    created = api_client.post(
        "/api/prompts/",
        {"phase": 2, "name": "fresh", "user_prompt": "y", "is_active": True},
        format="json",
    )
    assert created.status_code == 201
    assert created.json()["is_active"] is False

    patched = api_client.patch(f"/api/prompts/{template.id}/", {"is_active": True}, format="json")
    assert patched.status_code == 201
    assert patched.json()["is_active"] is False


@pytest.mark.django_db
def test_activate_makes_this_version_the_active_one(api_client, staff, template):
    sibling = template.new_version(user_prompt="v2 text")
    other_phase = PromptTemplate.objects.create_version(phase=3, name="generate", user_prompt="z")
    other_name = PromptTemplate.objects.create_version(phase=2, name="other", user_prompt="z")
    PromptTemplate.objects.filter(pk__in=[template.id, other_phase.id, other_name.id]).update(
        is_active=True
    )

    api_client.force_authenticate(user=staff)
    response = api_client.post(f"/api/prompts/{sibling.id}/activate/")
    assert response.status_code == 200
    assert response.json()["is_active"] is True

    template.refresh_from_db()
    sibling.refresh_from_db()
    other_phase.refresh_from_db()
    other_name.refresh_from_db()
    assert template.is_active is False
    assert sibling.is_active is True
    assert other_phase.is_active is True
    assert other_name.is_active is True


@pytest.mark.django_db
def test_non_staff_cannot_activate(api_client, member, template):
    api_client.force_authenticate(user=member)
    response = api_client.post(f"/api/prompts/{template.id}/activate/")
    assert response.status_code == 403


@pytest.mark.django_db
def test_delete_is_not_a_supported_operation(api_client, staff, template):
    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/prompts/{template.id}/")
    assert response.status_code == 405


@pytest.mark.django_db
def test_preview_of_a_phase_3_template_uses_a_scenario(api_client, staff, taxonomy):
    from corpus.models import Scenario

    system = SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)
    scenario = Scenario.objects.create(
        system_description=system,
        scenario_id="sc_01",
        actor="EP1",
        threat="Spoofing.",
        narrative="1. EP1 acts.",
        origin=Origin.SEED,
        order=1,
    )
    labeller = PromptTemplate.objects.create_version(
        phase=3, name="label", user_prompt="{{ scenario.scenario_id }}/{{ scenario.actor }}"
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{labeller.id}/preview/",
        {"taxonomy": taxonomy.id, "scenario": scenario.id},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["user_prompt"] == "sc_01/EP1"


# --- The preview must show, and check, everything that will actually be sent ----


@pytest.fixture
def parked_taxonomy(db):
    """A taxonomy that is NOT active — the case an experiment pinning a specific
    version hits, and the case where falling back to Taxonomy.active() would
    scan the wrong list (or nothing at all)."""
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="2.0", is_active=False)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-06", name="Deceptive Communication", order=6)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


def schema_with_ids():
    return {
        "type": "object",
        "properties": {"hcv_id": {"type": "string", "enum": ["HCV-06", "HCV-07"]}},
    }


@pytest.mark.django_db
def test_preview_reports_that_nothing_was_scanned_when_there_is_no_guard(
    api_client, staff, template
):
    """With no active taxonomy and no guard supplied, the backstop cannot run.
    A bare 200 would read as proof of isolation, so the response says outright
    that nothing was checked."""
    assert Taxonomy.objects.filter(is_active=True).count() == 0
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["leak_scan_ran"] is False
    assert response.json()["guarded_by"] is None


@pytest.mark.django_db
def test_preview_scans_against_the_supplied_guard_taxonomy(
    api_client, staff, template, parked_taxonomy
):
    """A variant A preview cannot be given a taxonomy — so guard_taxonomy is the
    only way to say which list the backstop should check against, including a
    pinned, non-active version."""
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": parked_taxonomy.id,
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["leak_scan_ran"] is True
    assert response.json()["guarded_by"] == parked_taxonomy.id


@pytest.mark.django_db
def test_preview_of_a_variant_a_leak_caught_by_the_guard_taxonomy_is_a_400(
    api_client, staff, parked_taxonomy
):
    """The C1 route through the API: innocent prompts, a schema enumerating the
    taxonomy. The guard taxonomy catches it and the preview refuses."""
    leaky = PromptTemplate.objects.create_version(
        phase=2, name="leaky-schema", user_prompt="Generate.", output_schema=schema_with_ids()
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{leaky.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": parked_taxonomy.id,
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "HCV-06" in str(response.json())


@pytest.mark.django_db
def test_preview_returns_the_output_schema_that_will_be_sent(api_client, staff, taxonomy):
    """The schema goes to the model in the same request body as the prompts, so
    the preview shows it rather than leaving a whole third of the payload
    invisible to the admin reviewing it."""
    guided = PromptTemplate.objects.create_version(
        phase=2, name="guided-schema", user_prompt="Generate.", output_schema=schema_with_ids()
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{guided.id}/preview/",
        {
            "variant": "B",
            "taxonomy": taxonomy.id,
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["output_schema"] == schema_with_ids()


@pytest.mark.django_db
def test_guard_taxonomy_is_not_injected_into_the_context(api_client, staff, parked_taxonomy):
    """guard_taxonomy exists ONLY to be scanned against. If it were also bound
    as `taxonomy`, it would become the leak it is there to detect."""
    peeking = PromptTemplate.objects.create_version(
        phase=2, name="peeking", user_prompt="{{ taxonomy }}"
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{peeking.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": parked_taxonomy.id,
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "taxonomy" in str(response.json())


# --- output_schema must be a JSON object, checked here rather than by the provider


@pytest.mark.django_db
def test_a_list_output_schema_is_rejected(api_client, staff):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/",
        {"phase": 2, "name": "listy", "user_prompt": "y", "output_schema": ["not", "a", "schema"]},
        format="json",
    )

    assert response.status_code == 400
    assert "output_schema" in response.json()


@pytest.mark.django_db
def test_a_string_output_schema_is_rejected_on_patch(api_client, staff, template):
    api_client.force_authenticate(user=staff)
    response = api_client.patch(
        f"/api/prompts/{template.id}/", {"output_schema": "{}"}, format="json"
    )

    assert response.status_code == 400
    assert "output_schema" in response.json()


@pytest.mark.django_db
def test_an_object_output_schema_is_accepted(api_client, staff):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/",
        {"phase": 2, "name": "schemad", "user_prompt": "y", "output_schema": schema_with_ids()},
        format="json",
    )

    assert response.status_code == 201
    assert response.json()["output_schema"] == schema_with_ids()


@pytest.mark.django_db
def test_a_failed_activation_leaves_the_previous_active_version_alone(
    api_client, staff, template, monkeypatch
):
    """Deactivating the siblings and activating this row are one act: if the
    save fails in between, the rollback must not leave the (phase, name) with
    no active version at all."""
    sibling = template.new_version(user_prompt="v2 text")
    PromptTemplate.objects.filter(pk=template.id).update(is_active=True)

    def boom(self, *args, **kwargs):
        raise RuntimeError("connection lost mid-activation")

    monkeypatch.setattr(PromptTemplate, "save", boom)
    api_client.force_authenticate(user=staff)
    with pytest.raises(RuntimeError):
        api_client.post(f"/api/prompts/{sibling.id}/activate/")

    monkeypatch.undo()
    template.refresh_from_db()
    assert template.is_active is True
    assert PromptTemplate.objects.filter(phase=2, name="generate", is_active=True).count() == 1
