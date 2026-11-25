import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command

from pipeline.clients import reset_llm_client
from taxonomy.models import Taxonomy


@pytest.fixture(autouse=True)
def _no_provider(settings):
    """A key is configured, and an installed fake never leaks between tests."""
    settings.OPENROUTER_API_KEY = "sk-or-v1-test-key"
    yield
    reset_llm_client()


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(
        email="analyst@example.com", password="pw-123456", is_staff=True
    )


@pytest.fixture
def member(db):
    return get_user_model().objects.create_user(email="member@example.com", password="pw-123456")


@pytest.fixture
def v12(db):
    call_command("import_taxonomy", "--activate")
    path = django_settings.RESOURCES_DIR / "taxonomy/hcv21_v1.2.json"
    call_command("import_taxonomy", str(path), "--analysis-default")
    return Taxonomy.objects.get(version="1.2")


@pytest.fixture
def fresh_analysis(staff, v12):
    """An analysis with a description and nothing extracted yet."""
    from analyses import state
    from analyses.models import Analysis

    return Analysis.objects.create(
        owner=staff,
        name="Smart Parking",
        description="An app lets residents pay for parking.",
        taxonomy=v12,
        extraction_settings=state.default_settings("extraction"),
        generation_settings=state.default_settings("generation"),
        labelling_settings=state.default_settings("labelling"),
        preset_version="1",
    )


@pytest.fixture
def analysis(fresh_analysis):
    """An analysis whose extraction is current and fully approved."""
    from analyses import state
    from analyses.models import AnalysisItem

    rows = [
        ("element", "1. People and organizations", "person_or_role", "Residents", "Citizen", ""),
        ("element", "2. System components", "interface", "ParkingApp", "MobileApp", ""),
        ("fact", "3. Use and responsibility", "uses", "Residents", "ParkingApp", ""),
        ("control", "8. Controls", "control", "ParkingApp", "PasswordControl", "absent"),
    ]
    for order, (kind, section, field, ref, value, item_state) in enumerate(rows):
        AnalysisItem.objects.create(
            analysis=fresh_analysis,
            kind=kind,
            section=section,
            field=field,
            ref=ref,
            value=value,
            original_value=value,
            state=item_state,
            original_state=item_state,
            fact_type="Asserted",
            decision="approved",
            order=order,
        )
    fresh_analysis.extraction_gaps = {"not_assessable": [], "no_term": []}
    fresh_analysis.extraction_digest = state.description_digest(fresh_analysis)
    fresh_analysis.save()
    return fresh_analysis


@pytest.fixture
def mark_extracted():
    """Keeps the extraction current after a test edits the description."""
    from analyses import state

    def mark(analysis):
        analysis.extraction_digest = state.description_digest(analysis)
        analysis.save()

    return mark


class CapturedQueue:
    """Stands in for django-q's broker: records each task, runs them on request."""

    def __init__(self, capture):
        self.tasks = []
        self.error = None
        self._capture = capture

    def __call__(self, name, *args):
        if self.error:
            raise self.error
        self.tasks.append((name, args))

    def step(self):
        """Run the next task as a worker would, commit hooks included."""
        from django.utils.module_loading import import_string

        name, args = self.tasks.pop(0)
        with self._capture(execute=True):
            return import_string(name)(*args)

    def drain(self):
        while self.tasks:
            self.step()


@pytest.fixture
def queue(monkeypatch, django_capture_on_commit_callbacks):
    captured = CapturedQueue(django_capture_on_commit_callbacks)
    monkeypatch.setattr("analyses.runs.async_task", captured)
    return captured


@pytest.fixture
def fake():
    from llm.fake import FakeLLMClient
    from pipeline.clients import set_llm_client

    client = FakeLLMClient()
    set_llm_client(client)
    return client


@pytest.fixture
def client_for(api_client):
    def client_for(user):
        api_client.force_authenticate(user=user)
        return api_client

    return client_for


@pytest.fixture
def extracted(analysis):
    return analysis
