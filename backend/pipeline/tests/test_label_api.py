import pytest
from django.contrib.auth import get_user_model

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Label, PhaseRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def staff(db):
    return User.objects.create_user(email="admin@example.com", password="pw-123456", is_staff=True)


@pytest.fixture
def labelled(db):
    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    insider = HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    passwords = HCV.objects.create(
        taxonomy=tax, hcv_id="HCV-17", name="Weak Passwords Practices", order=17
    )
    template = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    run = PhaseRun.objects.create(phase=3, model="m", prompt_template=template, taxonomy=tax)
    system = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    first = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    second = Scenario.objects.create(
        system_description=system, scenario_id="sc_02", origin=Origin.SEED, order=2
    )
    Label.objects.create(phase_run=run, scenario=first, hcv=insider, evidence="e1")
    Label.objects.create(phase_run=run, scenario=first, hcv=passwords, evidence="e2")
    Label.objects.create(phase_run=run, scenario=second, hcv=insider, evidence="e3")

    # Noise, so every filter below has something to EXCLUDE. Without a second
    # phase run and a second source, `?phase_run=<run>` and `?source=llm` each
    # return the whole table and would pass with the filter clause deleted.
    other = PhaseRun.objects.create(phase=3, model="m", prompt_template=template, taxonomy=tax)
    Label.objects.create(phase_run=other, scenario=second, hcv=passwords, evidence="e4")
    Label.objects.create(
        phase_run=run,
        scenario=second,
        hcv=passwords,
        evidence="e5",
        source=Label.Source.HUMAN,
    )
    return run, first, insider, other


@pytest.mark.django_db
def test_labels_require_authentication(api_client, labelled):
    assert api_client.get("/api/labels/").status_code == 403


@pytest.mark.django_db
def test_labels_list_with_their_identifiers(api_client, member, labelled):
    api_client.force_authenticate(user=member)
    body = api_client.get("/api/labels/").json()

    assert body["count"] == 5
    row = body["results"][0]
    assert row["hcv_id"] == "HCV-07"
    assert row["hcv_name"] == "Malicious Insiders"
    assert row["scenario_id"] == "sc_01"
    assert row["source"] == "llm"


@pytest.mark.django_db
def test_labels_filter_by_scenario(api_client, member, labelled):
    _, scenario, _, _ = labelled
    api_client.force_authenticate(user=member)
    assert api_client.get(f"/api/labels/?scenario={scenario.pk}").json()["count"] == 2
    assert api_client.get("/api/labels/?scenario=999999").json()["count"] == 0


@pytest.mark.django_db
def test_labels_filter_by_hcv(api_client, member, labelled):
    _, _, hcv, _ = labelled
    api_client.force_authenticate(user=member)
    assert api_client.get(f"/api/labels/?hcv={hcv.pk}").json()["count"] == 2


@pytest.mark.django_db
def test_labels_filter_by_hcv_accepts_the_taxonomy_id_too(api_client, member, labelled):
    """`hcv_id` in the response body is `"HCV-07"`, not the primary key — a
    researcher who reads that off a row and pastes it into `?hcv=` should
    get the same result as `?hcv=<pk>`, not a 400."""
    _, _, hcv, _ = labelled
    api_client.force_authenticate(user=member)
    by_pk = api_client.get(f"/api/labels/?hcv={hcv.pk}").json()
    by_taxonomy_id = api_client.get(f"/api/labels/?hcv={hcv.hcv_id}").json()
    assert by_taxonomy_id["count"] == by_pk["count"] == 2
    assert api_client.get("/api/labels/?hcv=HCV-99").json()["count"] == 0


@pytest.mark.django_db
def test_labels_filter_by_phase_run_and_source(api_client, member, labelled):
    """Each filter must EXCLUDE something, or it is not being tested at all."""
    run, _, _, other = labelled
    api_client.force_authenticate(user=member)
    assert api_client.get(f"/api/labels/?phase_run={run.pk}").json()["count"] == 4
    assert api_client.get(f"/api/labels/?phase_run={other.pk}").json()["count"] == 1
    assert api_client.get("/api/labels/?source=llm").json()["count"] == 4
    assert api_client.get("/api/labels/?source=human").json()["count"] == 1


@pytest.mark.django_db
def test_a_bad_label_filter_is_a_400(api_client, member, labelled):
    api_client.force_authenticate(user=member)
    assert api_client.get("/api/labels/?scenario=abc").status_code == 400
    assert api_client.get("/api/labels/?scenario=0").json()["count"] == 0


@pytest.mark.django_db
def test_the_label_list_does_not_n_plus_one(api_client, member, labelled):
    """`hcv_id`/`hcv_name`/`scenario_id` each walk an FK; select_related pays once."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    api_client.force_authenticate(user=member)
    with CaptureQueriesContext(connection) as first:
        api_client.get("/api/labels/")

    run, _, _, _ = labelled
    scenario = Scenario.objects.get(scenario_id="sc_02")
    hcv = HCV.objects.get(hcv_id="HCV-07")
    # `(run, scenario, hcv)` is already taken by fixture label "e3" —
    # `unique_label_per_run` forbids repeating it. A fresh PhaseRun per
    # label keeps the tuple unique while still reusing the same scenario
    # and hcv row on every iteration, which is what actually exercises
    # `select_related("hcv", "scenario")` on the list.
    for index in range(10):
        extra_run = PhaseRun.objects.create(
            phase=3, model="m", prompt_template=run.prompt_template, taxonomy=run.taxonomy
        )
        Label.objects.create(
            phase_run=extra_run, scenario=scenario, hcv=hcv, evidence=f"bulk-{index}"
        )

    with CaptureQueriesContext(connection) as second:
        api_client.get("/api/labels/")

    assert len(second) == len(first)


@pytest.mark.django_db
def test_labels_are_read_only(api_client, staff, member, labelled):
    """POST is unsupported for anyone — matches the other three viewsets now
    that `LabelViewSet` carries the same explicit `permission_classes`
    (task 5, step 5: "all four viewsets read the same way").

    DRF checks permissions before it resolves a handler, so a non-staff POST
    is refused with 403 first — 405 only surfaces once permission has
    already been granted, which is why this asserts POST as staff (see
    `test_run_api.test_runs_are_read_only` for the identical reasoning).
    """
    api_client.force_authenticate(user=staff)
    assert api_client.post("/api/labels/", {}, format="json").status_code == 405

    api_client.force_authenticate(user=member)
    assert api_client.post("/api/labels/", {}, format="json").status_code == 403


@pytest.mark.django_db
def test_label_pagination_is_stable_past_a_page_with_ties(api_client, member, labelled):
    """Two labels can share `(scenario, hcv)` from different phase runs — an
    exact tie under `Label.Meta.ordering = ["scenario", "hcv"]` alone (the
    fixture already has one: `e4`/`e5` both sit at `(second, passwords)`).
    Without an `id` tiebreaker, the database is free to order tied rows
    however it likes, and once there is more than one page a caller can see
    a row twice or miss one outright across the page boundary — for an
    endpoint serving the experiment's actual output, a silently dropped
    label is the worst failure mode there is. This piles two LARGE tie
    groups on top of the fixture's five rows, past `PAGE_SIZE` (50), and
    walks every page to prove no id is seen twice and none is missing.
    """
    run, first, insider, _ = labelled
    api_client.force_authenticate(user=member)
    second = Scenario.objects.get(scenario_id="sc_02")
    passwords = HCV.objects.get(hcv_id="HCV-17")

    for index in range(30):
        extra_run = PhaseRun.objects.create(
            phase=3, model="m", prompt_template=run.prompt_template, taxonomy=run.taxonomy
        )
        Label.objects.create(
            phase_run=extra_run, scenario=first, hcv=insider, evidence=f"tie-a-{index}"
        )
    for index in range(30):
        extra_run = PhaseRun.objects.create(
            phase=3, model="m", prompt_template=run.prompt_template, taxonomy=run.taxonomy
        )
        Label.objects.create(
            phase_run=extra_run, scenario=second, hcv=passwords, evidence=f"tie-b-{index}"
        )

    total = Label.objects.count()
    assert total > 50, "this test is pointless if everything fits on one page"

    seen_ids = []
    url = "/api/labels/"
    while url:
        body = api_client.get(url).json()
        seen_ids.extend(row["id"] for row in body["results"])
        url = body["next"]
        if url:
            # DRF returns an absolute URL; the test client wants a path.
            url = url.replace("http://testserver", "")

    assert len(seen_ids) == total
    assert len(set(seen_ids)) == total
