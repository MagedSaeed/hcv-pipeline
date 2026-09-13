import pytest
from django.contrib.auth import get_user_model

from corpus.models import FewShotExample, Origin, ResourceFile, ResourceKind, SystemDescription
from prompts.models import PromptTemplate
from prompts.rendering import format_hcv_table
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
def test_preview_returns_the_exact_rendered_strings(api_client, staff, template, taxonomy):
    system = SystemDescription.objects.create(title="Oldsmar", body="A plant.", origin=Origin.SEED)
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "n_systems": 4,
            "n_scenarios_per_system": 5,
            "seed_systems": [system.id],
        },
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
def test_preview_reports_a_runtime_error_in_the_template_as_400(api_client, staff):
    """Task 10.6 follow-up: a template that raises something other than a
    Jinja error while rendering — `{{ 1/0 }}` — used to escape `render()`
    and answer 500 here. Preview and a real run share `render()`, so both
    now refuse it the same way."""
    broken = PromptTemplate.objects.create_version(
        phase=2, name="divides-by-zero", user_prompt="{{ 1/0 }}"
    )
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{broken.id}/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 400
    assert "ZeroDivisionError" in str(response.json())


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
def test_an_unused_version_can_be_deleted(api_client, staff, template):
    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/prompts/{template.id}/")
    assert response.status_code == 204
    assert not PromptTemplate.objects.filter(pk=template.id).exists()


@pytest.mark.django_db
def test_a_version_a_run_uses_cannot_be_deleted(api_client, staff, template, taxonomy):
    from pipeline.models import PhaseRun

    PhaseRun.objects.create(
        phase=template.phase, model="m", prompt_template=template, taxonomy=taxonomy
    )
    api_client.force_authenticate(user=staff)
    response = api_client.delete(f"/api/prompts/{template.id}/")
    assert response.status_code == 400
    assert "still used by" in response.json()["detail"]


@pytest.mark.django_db
def test_members_cannot_delete_a_version(api_client, member, template):
    api_client.force_authenticate(user=member)
    assert api_client.delete(f"/api/prompts/{template.id}/").status_code == 403


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
def test_preview_refuses_when_there_is_no_guard_to_scan_against(api_client, staff, template):
    """With no active taxonomy and no guard supplied, the backstop cannot run
    at all. Coordinator review: this used to return a bare 200 with
    `leak_scan_ran: false`, which read as "checked, and clean" to anyone who
    didn't read that field closely — reachable in a single stateless preview
    request, the same defect shape `pipeline.models.unbound_guard_taxonomy_
    error` took four review rounds to close on the run path. A variant-A
    preview that cannot actually scan must fail CLOSED, mirroring
    `assert_request_not_leaking_taxonomy`, rather than be presentable as
    clean."""
    assert Taxonomy.objects.filter(is_active=True).count() == 0
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )

    assert response.status_code == 400
    assert "backstop could not run" in str(response.json())


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


# --- Draft preview: validate an in-progress edit before it is ever saved -------
#
# Spec §10: "A template is validated against its own variant before it can be
# saved." Before this, `preview` only ever rendered a PERSISTED template's
# stored body — the editor had to PATCH (creating a version) before it could
# find out a template was broken. These fields (`system_prompt`/`user_prompt`/
# `output_schema` in the preview body) let a draft be checked first, through
# the exact same `_context()`/`render()` call as a persisted preview and a
# real run — see `PromptTemplateViewSet._preview`.


@pytest.mark.django_db
def test_draft_preview_of_variant_a_referencing_taxonomy_is_refused_and_persists_nothing(
    api_client, staff, template
):
    """The gap this feature closes: a stray {{ taxonomy }} in a Run A draft
    must be caught before saving, not only after — and refusing it must
    leave no trace: no new PromptTemplate row, no version bump on this one."""
    api_client.force_authenticate(user=staff)
    before_count = PromptTemplate.objects.count()
    before_version = template.version

    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "user_prompt": "{{ taxonomy }}",
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "taxonomy" in str(response.json())
    assert PromptTemplate.objects.count() == before_count
    template.refresh_from_db()
    assert template.version == before_version
    assert template.user_prompt == "Make {{ n_systems }} systems."


@pytest.mark.django_db
def test_the_same_draft_body_previews_successfully_for_variant_b(
    api_client, staff, template, taxonomy
):
    """The mirror image of the test above: the identical draft body, under
    variant B with a taxonomy supplied, is exactly what the template is FOR —
    proving the refusal above is about variant isolation, not a broken draft."""
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "B",
            "taxonomy": taxonomy.id,
            "user_prompt": "{{ taxonomy }}",
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 200
    assert "HCV-07" in response.json()["user_prompt"]


@pytest.mark.django_db
def test_draft_preview_with_a_taxonomy_resource_is_refused_for_variant_a(
    api_client, staff, template, taxonomy
):
    """A draft can reach the taxonomy transitively too, via resource() — the
    same route a persisted template is refused through (test_isolation.py).
    Nothing is persisted by the refusal either."""
    ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    api_client.force_authenticate(user=staff)
    before_count = PromptTemplate.objects.count()

    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "user_prompt": '{{ resource("hcv_taxonomy") }}',
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "taxonomy resource" in str(response.json())
    assert PromptTemplate.objects.count() == before_count


@pytest.mark.django_db
def test_draft_preview_with_a_fewshot_referencing_a_taxonomy_resource_is_refused_for_variant_a(
    api_client, staff, template, taxonomy
):
    """A draft few-shot pointing at a taxonomy-kind resource must be refused
    at context-build time exactly as a persisted template's would be — the
    guard lives in `build_phase2_context`, shared by both."""
    resource = ResourceFile.objects.create(
        name="hcv_taxonomy", kind=ResourceKind.TAXONOMY, text=format_hcv_table(taxonomy)
    )
    example = FewShotExample.objects.create(
        phase=2, name="leaky", input_file=resource, output_text="ok"
    )
    api_client.force_authenticate(user=staff)
    before_count = PromptTemplate.objects.count()

    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "user_prompt": "{% for f in fewshots %}{{ f.input }}{% endfor %}",
            "fewshots": [example.id],
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "leaky" in str(response.json())
    assert PromptTemplate.objects.count() == before_count


@pytest.mark.django_db
def test_draft_preview_of_a_brand_new_template_with_no_id_works(api_client, staff, taxonomy):
    """The other half of the gap: a template that has never been saved at all
    has no id to preview against via the detail route."""
    api_client.force_authenticate(user=staff)
    before_count = PromptTemplate.objects.count()

    response = api_client.post(
        "/api/prompts/preview/",
        {
            "phase": 2,
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "user_prompt": "Make {{ n_systems }} systems.",
            "n_systems": 3,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["user_prompt"] == "Make 3 systems."
    assert PromptTemplate.objects.count() == before_count


@pytest.mark.django_db
def test_draft_preview_of_a_brand_new_template_is_equally_guarded(api_client, staff):
    """A brand-new template gets the exact same variant-A isolation guard as
    an existing one being edited — the id-less route is not a looser check."""
    api_client.force_authenticate(user=staff)
    before_count = PromptTemplate.objects.count()

    response = api_client.post(
        "/api/prompts/preview/",
        {
            "phase": 2,
            "variant": "A",
            "user_prompt": "{{ taxonomy }}",
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "taxonomy" in str(response.json())
    assert PromptTemplate.objects.count() == before_count


@pytest.mark.django_db
def test_draft_preview_of_a_brand_new_template_without_phase_is_a_400(api_client, staff):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/preview/",
        {"variant": "A", "user_prompt": "Generate.", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 400
    assert "phase" in response.json()


@pytest.mark.django_db
def test_draft_preview_of_a_brand_new_template_without_a_body_is_a_400(api_client, staff):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1},
        format="json",
    )
    assert response.status_code == 400
    assert "user_prompt" in response.json()


@pytest.mark.django_db
def test_a_partial_draft_falls_back_to_the_stored_user_prompt(
    api_client, staff, template, taxonomy
):
    """A draft that edits only system_prompt must still render this
    template's real, STORED user_prompt — not an empty one — and the draft
    system_prompt must actually reach the render, not be silently dropped."""
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "system_prompt": "You are helpful.",
            "n_systems": 4,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["system_prompt"] == "You are helpful."
    assert response.json()["user_prompt"] == "Make 4 systems."


@pytest.mark.django_db
def test_a_partial_draft_falls_back_to_the_stored_output_schema_and_is_refused_like_a_real_run(
    api_client, staff, taxonomy
):
    """The most important regression: a draft that edits only user_prompt
    must NOT silently drop the stored output_schema to `{}`. Coordinator
    review: previously it did, so a template whose real, SAVED schema
    enumerates HCV ids previewed as a clean 200 under variant A — a persisted
    preview of the identical row correctly returns 400, and a draft touching
    only the prompt text must be refused exactly the same way, because a
    real run against this row would send that same leaking schema."""
    leaky = PromptTemplate.objects.create_version(
        phase=2, name="leaky-schema-draft", user_prompt="Generate.", output_schema=schema_with_ids()
    )
    api_client.force_authenticate(user=staff)

    persisted = api_client.post(
        f"/api/prompts/{leaky.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )
    assert persisted.status_code == 400
    assert "HCV-07" in str(persisted.json())

    draft = api_client.post(
        f"/api/prompts/{leaky.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "user_prompt": "Generate something else entirely.",
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )
    assert draft.status_code == 400
    assert "HCV-07" in str(draft.json())


@pytest.mark.django_db
def test_a_partial_draft_falls_back_to_the_stored_system_prompt_and_is_refused_like_a_real_run(
    api_client, staff, taxonomy
):
    """The system_prompt half of the fallback above. A draft supplying only
    user_prompt must still render — and scan — the STORED system_prompt: a
    real run against this row sends it, so a preview that dropped it to ""
    would be the original false-clean 200 again, through a different field."""
    leaky = PromptTemplate.objects.create_version(
        phase=2,
        name="leaky-system-draft",
        system_prompt="Label with HCV-07 wherever it applies.",
        user_prompt="Generate.",
    )
    api_client.force_authenticate(user=staff)
    body = {
        "variant": "A",
        "guard_taxonomy": taxonomy.id,
        "n_systems": 1,
        "n_scenarios_per_system": 1,
    }

    persisted = api_client.post(f"/api/prompts/{leaky.id}/preview/", body, format="json")
    assert persisted.status_code == 400
    assert "HCV-07" in str(persisted.json())

    draft = api_client.post(
        f"/api/prompts/{leaky.id}/preview/",
        {**body, "user_prompt": "Generate something else entirely."},
        format="json",
    )
    assert draft.status_code == 400
    assert "HCV-07" in str(draft.json())


@pytest.mark.django_db
def test_draft_preview_of_a_brand_new_template_scans_its_draft_system_prompt(
    api_client, staff, taxonomy
):
    """The id-less route must render and scan the DRAFT system_prompt, not a
    blank one — the user_prompt here is innocent, so only the system_prompt
    can trip the scan."""
    api_client.force_authenticate(user=staff)
    before_count = PromptTemplate.objects.count()

    response = api_client.post(
        "/api/prompts/preview/",
        {
            "phase": 2,
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "system_prompt": "Label with HCV-07 wherever it applies.",
            "user_prompt": "Generate.",
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 400
    assert "HCV-07" in str(response.json())
    assert PromptTemplate.objects.count() == before_count


@pytest.mark.django_db
def test_draft_preview_of_a_brand_new_template_renders_its_draft_system_prompt(
    api_client, staff, taxonomy
):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/preview/",
        {
            "phase": 2,
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "system_prompt": "You write scenarios for {{ n_systems }} systems.",
            "user_prompt": "Generate.",
            "n_systems": 2,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["system_prompt"] == "You write scenarios for 2 systems."


# --- The fail-closed 400 must say what is actually wrong ------------------------


def _unscannable_preview(api_client, staff, template, **extra):
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {"variant": "A", "n_systems": 1, "n_scenarios_per_system": 1, **extra},
        format="json",
    )
    assert response.status_code == 400
    return str(response.json())


@pytest.mark.django_db
def test_preview_with_a_guard_taxonomy_of_no_rows_names_that_guard(api_client, staff, template):
    """A guard WAS passed — telling the operator to pass one would send them
    looking for the wrong problem."""
    empty = Taxonomy.objects.create(vocabulary_id="HCV-EMPTY-PREVIEW", version="1.0")

    detail = _unscannable_preview(api_client, staff, template, guard_taxonomy=empty.id)

    assert "backstop could not run" in detail
    assert f"The guard_taxonomy you passed ({empty})" in detail
    assert "has no HCV rows" in detail
    assert "No taxonomy to check" not in detail


@pytest.mark.django_db
def test_preview_with_a_guard_taxonomy_of_blank_rows_names_those_rows(api_client, staff, template):
    blanked = Taxonomy.objects.create(vocabulary_id="HCV-BLANK-PREVIEW", version="1.0")
    HCV.objects.create(taxonomy=blanked, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    HCV.objects.create(taxonomy=blanked, hcv_id="", name="", definition="", order=8)

    detail = _unscannable_preview(api_client, staff, template, guard_taxonomy=blanked.id)

    assert "backstop could not run" in detail
    assert f"The guard_taxonomy you passed ({blanked})" in detail
    assert "1 of 2 HCV rows with no hcv_id, name or definition" in detail
    assert "No taxonomy to check" not in detail


@pytest.mark.django_db
def test_preview_falling_back_to_an_empty_active_taxonomy_names_it(api_client, staff, template):
    """No guard passed, but one WAS found — the active taxonomy — and it is
    the thing that is broken."""
    active = Taxonomy.objects.create(
        vocabulary_id="HCV-EMPTY-ACTIVE", version="1.0", is_active=True
    )

    detail = _unscannable_preview(api_client, staff, template)

    assert "backstop could not run" in detail
    assert f"The active taxonomy ({active})" in detail
    assert "has no HCV rows" in detail
    assert "No taxonomy to check" not in detail


@pytest.mark.django_db
def test_preview_with_no_guard_at_all_still_asks_for_one(api_client, staff, template):
    assert Taxonomy.objects.filter(is_active=True).count() == 0

    detail = _unscannable_preview(api_client, staff, template)

    assert "No taxonomy to check" in detail
    assert "Pass guard_taxonomy=" in detail


@pytest.mark.django_db
def test_draft_preview_returns_the_draft_output_schema_not_the_stored_one(
    api_client, staff, template, taxonomy
):
    api_client.force_authenticate(user=staff)
    draft_schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "guard_taxonomy": taxonomy.id,
            "user_prompt": "Generate.",
            "output_schema": draft_schema,
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["output_schema"] == draft_schema
    template.refresh_from_db()
    assert template.output_schema == {}


@pytest.mark.django_db
def test_a_draft_output_schema_that_is_not_an_object_is_rejected(api_client, staff, template):
    """PreviewSerializer.validate_output_schema (shared with
    PromptTemplateSerializer's own check via _require_json_object) must
    actually run on the preview half too, not just the save half."""
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        f"/api/prompts/{template.id}/preview/",
        {
            "variant": "A",
            "user_prompt": "Generate.",
            "output_schema": ["not", "a", "schema"],
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "output_schema" in response.json()


@pytest.mark.django_db
def test_draft_preview_of_a_brand_new_template_rejects_an_invalid_phase(api_client, staff):
    """`phase` is a ChoiceField restricted to PromptTemplate.PHASE_CHOICES
    (2 or 3), not a bare integer — a preview naming a phase that doesn't
    exist must be refused here, not reach _context() and behave oddly."""
    api_client.force_authenticate(user=staff)
    response = api_client.post(
        "/api/prompts/preview/",
        {
            "phase": 99,
            "variant": "A",
            "user_prompt": "Generate.",
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )
    assert response.status_code == 400
    assert "phase" in response.json()


@pytest.mark.django_db
def test_non_staff_cannot_preview_a_brand_new_template(api_client, member):
    api_client.force_authenticate(user=member)
    response = api_client.post(
        "/api/prompts/preview/",
        {
            "phase": 2,
            "variant": "A",
            "user_prompt": "y",
            "n_systems": 1,
            "n_scenarios_per_system": 1,
        },
        format="json",
    )
    assert response.status_code == 403
