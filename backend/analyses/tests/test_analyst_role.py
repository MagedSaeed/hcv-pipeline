import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APIClient

from analyses.models import Analysis

pytestmark = pytest.mark.django_db


@pytest.fixture
def analyst(db):
    return get_user_model().objects.create_user(
        email="sam@example.com", password="pw-123456", role="analyst"
    )


@pytest.fixture
def signed_in():
    """A client signed in through the session, as the browser is."""

    def signed_in(user):
        client = APIClient()
        client.force_login(user)
        return client

    return signed_in


@pytest.fixture
def mine(analyst, v12, signed_in):
    response = signed_in(analyst).post("/api/analyses/", {"name": "Mine"}, format="json")
    assert response.status_code == 201
    return Analysis.objects.get(pk=response.json()["id"])


def test_me_says_the_role(signed_in, analyst, staff):
    assert signed_in(analyst).get("/api/auth/me/").json()["role"] == "analyst"
    assert signed_in(staff).get("/api/auth/me/").json()["role"] == "researcher"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/pipeline-runs/"),
        ("get", "/api/experiments/"),
        ("get", "/api/prompts/"),
        ("get", "/api/system-descriptions/"),
        ("get", "/api/settings/"),
        ("post", "/api/taxonomies/"),
        ("get", "/api/no-such-endpoint/"),
    ],
)
def test_analysts_are_kept_out_of_the_experiment_lab(signed_in, analyst, method, path):
    response = getattr(signed_in(analyst), method)(path)

    assert response.status_code == 403
    assert response.json()["detail"] == "Analyst accounts can only use Analyses."


def test_analysts_reach_what_analyses_needs(signed_in, analyst, v12, settings):
    client = signed_in(analyst)

    for path in ["/api/auth/me/", "/api/analyses/", "/api/analyses/options/", "/api/taxonomies/"]:
        assert client.get(path).status_code == 200, path
    assert client.get("/api/auth/csrf/").status_code == 200


def test_analysts_create_edit_and_run_their_own(signed_in, analyst, mine, fake, queue):
    client = signed_in(analyst)

    renamed = client.patch(f"/api/analyses/{mine.pk}/", {"name": "Renamed"}, format="json")
    described = client.patch(
        f"/api/analyses/{mine.pk}/", {"description": "An app lets residents pay."}, format="json"
    )
    extract = client.post(f"/api/analyses/{mine.pk}/extract/")

    assert (renamed.status_code, described.status_code, extract.status_code) == (200, 200, 202)


def test_analysts_see_only_their_own(signed_in, analyst, mine, analysis):
    client = signed_in(analyst)

    listed = [row["id"] for row in client.get("/api/analyses/?scope=all").json()["results"]]
    assert listed == [mine.pk]
    for path in ["", "report/", "report/pdf/"]:
        assert client.get(f"/api/analyses/{analysis.pk}/{path}").status_code == 404, path
    assert (
        client.patch(f"/api/analyses/{analysis.pk}/", {"name": "x"}, format="json").status_code
        == 404
    )
    assert client.post(f"/api/analyses/{analysis.pk}/start-new/").status_code == 404
    assert client.delete(f"/api/analyses/{analysis.pk}/").status_code == 404


def test_researchers_still_see_analysts_work(signed_in, staff, mine):
    listed = [
        row["id"] for row in signed_in(staff).get("/api/analyses/?scope=all").json()["results"]
    ]

    assert mine.pk in listed


def test_create_user_sets_the_role():
    call_command("create_user", "ana@example.com", "--password", "pw-123456", "--role", "analyst")

    user = get_user_model().objects.get(email="ana@example.com")
    assert (user.role, user.is_staff) == ("analyst", False)
