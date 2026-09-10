"""`?run=` and `?variant=` on `/api/labels/` — see
`pipeline.views.LabelViewSet.get_queryset`.

Mirrors `corpus/tests/test_corpus_filters.py`, which added the identical pair
of filters to `/api/system-descriptions/` and `/api/scenarios/` for the same
reason: every filter test here is built so it FAILS if the filter clause it
exercises is deleted from the view — each fixture row is excluded by at least
one other row a no-op filter would wrongly include. This was verified by hand
for all six `/api/labels/` filters (`scenario`, `phase_run`, `hcv`, `source`,
`run`, `variant`): each clause was temporarily removed, the matching test
re-run to confirm it failed, then the clause restored (see the task report).
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext

from corpus.models import Origin, Scenario, SystemDescription
from pipeline.models import Experiment, Label, PhaseRun, PipelineRun
from prompts.models import PromptTemplate
from taxonomy.models import HCV, Taxonomy

User = get_user_model()


@pytest.fixture
def member(db):
    return User.objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def label_runs(db):
    """Two variants, two pipeline runs (repetitions), and one ad-hoc phase
    run (`pipeline_run=None`, like an operator tuning a labelling prompt) —
    the shape `?run=` can never reach, which is exactly why `?phase_run=`
    exists.

    `run_a` (variant A) sits under `pipeline_run_1`; `run_b` (variant B)
    sits under `pipeline_run_2`; `run_adhoc` (variant A) has no
    `pipeline_run` at all. Every label sits at a distinct
    (phase_run, scenario, hcv) so `unique_label_per_run` is never tripped.
    """
    tax = Taxonomy.objects.create(vocabulary_id="HCV-LABEL-FILTER", version="1.0")
    insider = HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    passwords = HCV.objects.create(
        taxonomy=tax, hcv_id="HCV-17", name="Weak Passwords Practices", order=17
    )
    template2 = PromptTemplate.objects.create_version(
        phase=2, name="label-filter-gen", user_prompt="g"
    )
    template3 = PromptTemplate.objects.create_version(
        phase=3, name="label-filter-lab", user_prompt="l"
    )

    experiment = Experiment.objects.create(
        name="label-filter-experiment",
        taxonomy=tax,
        phase2_model="m2",
        phase2_prompt_a=template2,
        phase2_prompt_b=template2,
        phase3_model="m3",
        phase3_prompt=template3,
    )
    pipeline_run_1 = PipelineRun.objects.create(experiment=experiment, repetition_index=1)
    pipeline_run_2 = PipelineRun.objects.create(experiment=experiment, repetition_index=2)

    run_a = PhaseRun.objects.create(
        pipeline_run=pipeline_run_1,
        phase=3,
        variant="A",
        model="m",
        prompt_template=template3,
        taxonomy=tax,
    )
    run_b = PhaseRun.objects.create(
        pipeline_run=pipeline_run_2,
        phase=3,
        variant="B",
        model="m",
        prompt_template=template3,
        taxonomy=tax,
    )
    run_adhoc = PhaseRun.objects.create(
        pipeline_run=None,
        phase=3,
        variant="A",
        model="m",
        prompt_template=template3,
        taxonomy=tax,
    )

    system = SystemDescription.objects.create(title="Oldsmar", body="x", origin=Origin.SEED)
    scenario_1 = Scenario.objects.create(
        system_description=system, scenario_id="sc_01", origin=Origin.SEED, order=1
    )
    scenario_2 = Scenario.objects.create(
        system_description=system, scenario_id="sc_02", origin=Origin.SEED, order=2
    )

    label_a = Label.objects.create(phase_run=run_a, scenario=scenario_1, hcv=insider, evidence="a")
    label_b = Label.objects.create(phase_run=run_b, scenario=scenario_1, hcv=insider, evidence="b")
    label_adhoc = Label.objects.create(
        phase_run=run_adhoc, scenario=scenario_2, hcv=passwords, evidence="adhoc"
    )
    label_human = Label.objects.create(
        phase_run=run_a,
        scenario=scenario_2,
        hcv=passwords,
        evidence="human",
        source=Label.Source.HUMAN,
    )

    return {
        "pipeline_run_1": pipeline_run_1,
        "pipeline_run_2": pipeline_run_2,
        "run_a": run_a,
        "run_b": run_b,
        "run_adhoc": run_adhoc,
        "scenario_1": scenario_1,
        "scenario_2": scenario_2,
        "insider": insider,
        "passwords": passwords,
        "label_a": label_a,
        "label_b": label_b,
        "label_adhoc": label_adhoc,
        "label_human": label_human,
    }


@pytest.mark.django_db
def test_labels_filter_by_run(api_client, member, label_runs):
    """`?run=` is the repetition (`PipelineRun`), reached via
    `phase_run__pipeline_run_id`. Both `run_a` and `label_human` sit under
    `pipeline_run_1` (same `phase_run`); `run_b`'s label sits under
    `pipeline_run_2`; the ad-hoc label has no `pipeline_run` to match."""
    api_client.force_authenticate(user=member)
    run1 = label_runs["pipeline_run_1"].pk
    run2 = label_runs["pipeline_run_2"].pk

    first = api_client.get(f"/api/labels/?run={run1}").json()
    second = api_client.get(f"/api/labels/?run={run2}").json()

    assert first["count"] == 2
    assert {row["id"] for row in first["results"]} == {
        label_runs["label_a"].id,
        label_runs["label_human"].id,
    }
    assert second["count"] == 1
    assert second["results"][0]["id"] == label_runs["label_b"].id


@pytest.mark.django_db
def test_ad_hoc_labels_are_unreachable_via_run(api_client, member, label_runs):
    """The whole point of `?phase_run=` (already supported): iterate-mode
    labels have no `pipeline_run`, so no value of `?run=` can ever surface
    them — `?phase_run=` is the only route in.
    """
    api_client.force_authenticate(user=member)
    run1 = label_runs["pipeline_run_1"].pk
    run2 = label_runs["pipeline_run_2"].pk

    for run_id in (run1, run2):
        ids = {row["id"] for row in api_client.get(f"/api/labels/?run={run_id}").json()["results"]}
        assert label_runs["label_adhoc"].id not in ids

    reached_via_phase_run = api_client.get(
        f"/api/labels/?phase_run={label_runs['run_adhoc'].pk}"
    ).json()
    assert reached_via_phase_run["count"] == 1
    assert reached_via_phase_run["results"][0]["id"] == label_runs["label_adhoc"].id


@pytest.mark.django_db
def test_labels_filter_by_variant(api_client, member, label_runs):
    """`?variant=` reaches via `phase_run__variant`. Variant A covers both
    `run_a` (batch) and `run_adhoc` (iterate mode) — a variant is not the
    same axis as a repetition, so this must include the ad-hoc label even
    though `?run=` never can."""
    api_client.force_authenticate(user=member)

    a = api_client.get("/api/labels/?variant=A").json()
    b = api_client.get("/api/labels/?variant=B").json()

    assert a["count"] == 3
    assert {row["id"] for row in a["results"]} == {
        label_runs["label_a"].id,
        label_runs["label_human"].id,
        label_runs["label_adhoc"].id,
    }
    assert b["count"] == 1
    assert b["results"][0]["id"] == label_runs["label_b"].id


@pytest.mark.django_db
def test_label_filters_combine_run_and_variant(api_client, member, label_runs):
    """`?run=` and `?variant=` must narrow together, not override one
    another — `run_a` matches both `run=pipeline_run_1` and `variant=A`, but
    NOT `variant=B`, even though `pipeline_run_1` exists."""
    api_client.force_authenticate(user=member)
    run1 = label_runs["pipeline_run_1"].pk

    matching = api_client.get(f"/api/labels/?run={run1}&variant=A").json()
    mismatched = api_client.get(f"/api/labels/?run={run1}&variant=B").json()

    assert matching["count"] == 2
    assert mismatched["count"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("bad_run", ["0", "-1", "999999"])
def test_a_nonexistent_run_id_is_an_empty_result_not_a_500(api_client, member, label_runs, bad_run):
    """`run=0` must filter to nothing — not fall through to unfiltered the
    way a bare `if run:` truthiness check would for the falsy integer 0."""
    api_client.force_authenticate(user=member)

    response = api_client.get(f"/api/labels/?run={bad_run}")

    assert response.status_code == 200
    assert response.json()["count"] == 0


@pytest.mark.django_db
def test_a_non_integer_run_filter_is_a_bad_request(api_client, member, label_runs):
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/labels/?run=abc")

    assert response.status_code == 400
    assert "run" in response.json()


@pytest.mark.django_db
def test_an_empty_run_filter_is_treated_as_absent_not_a_500(api_client, member, label_runs):
    """`?run=` with no value reaches `integer_param` as `""`, which is
    treated the same as the parameter being absent altogether — never a
    500 — matching every other integer filter in this API (see
    `corpus/tests/test_corpus_filters.py`'s identical case)."""
    api_client.force_authenticate(user=member)

    empty = api_client.get("/api/labels/?run=")
    unfiltered = api_client.get("/api/labels/")

    assert empty.status_code == 200
    assert empty.json()["count"] == unfiltered.json()["count"]


@pytest.mark.django_db
def test_an_unknown_variant_is_an_empty_result_not_a_500(api_client, member, label_runs):
    api_client.force_authenticate(user=member)

    response = api_client.get("/api/labels/?variant=ZZZ")

    assert response.status_code == 200
    assert response.json()["count"] == 0


@pytest.mark.django_db
def test_label_list_does_not_n_plus_one_with_the_new_filters(api_client, member, label_runs):
    """Neither `LabelSerializer` nor these new filters walk a per-row
    relation beyond what `select_related("hcv", "scenario")` already pays
    for once — filtering on `phase_run__variant` must not turn the list
    into a per-row query. Measured, not assumed.
    """
    api_client.force_authenticate(user=member)

    with CaptureQueriesContext(connection) as few:
        api_client.get("/api/labels/?variant=A")

    run_a = label_runs["run_a"]
    scenario_1 = label_runs["scenario_1"]
    insider = label_runs["insider"]
    # `(phase_run, scenario, hcv)` must stay unique — a fresh PhaseRun per
    # label keeps the tuple unique while reusing the same scenario/hcv row,
    # which is what actually exercises `select_related` on the list (same
    # technique as `pipeline/tests/test_label_api.py`'s own N+1 test).
    for index in range(10):
        extra_run = PhaseRun.objects.create(
            phase=3,
            variant="A",
            model="m",
            prompt_template=run_a.prompt_template,
            taxonomy=run_a.taxonomy,
        )
        Label.objects.create(
            phase_run=extra_run, scenario=scenario_1, hcv=insider, evidence=f"bulk-{index}"
        )

    with CaptureQueriesContext(connection) as many:
        api_client.get("/api/labels/?variant=A")

    assert len(many) == len(few)
