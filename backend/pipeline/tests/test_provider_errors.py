import json

import pytest

from pipeline.failures import describe_provider_error

CREDIT = (
    "APIStatusError: Error code: 402 - {'error': {'message': \"This request requires more "
    "credits, or fewer max_tokens. You requested up to 65536 tokens, but can only afford "
    "51471.\", 'code': 402}}"
)


def test_no_transport_error_means_no_summary():
    assert describe_provider_error(200, "") == ""
    assert describe_provider_error(None, None) == ""


def test_credit_errors_say_what_to_do_and_keep_the_providers_words():
    summary = describe_provider_error(402, CREDIT)
    assert summary.startswith("Not enough OpenRouter credit")
    assert "Raise the key's limit or add credit on OpenRouter." in summary
    assert "You requested up to 65536 tokens, but can only afford 51471." in summary
    assert "APIStatusError" not in summary


@pytest.mark.parametrize(
    "status, start",
    [
        (401, "OpenRouter rejected the API key."),
        (404, "OpenRouter doesn't recognise this model."),
        (429, "Rate limited by OpenRouter or the model provider."),
        (400, "OpenRouter refused the request as invalid."),
        (502, "OpenRouter or the model provider is unavailable."),
        (418, "The model call failed (HTTP 418)."),
    ],
)
def test_each_status_gets_a_plain_sentence(status, start):
    error = f"APIStatusError: Error code: {status} - {{'error': {{'message': 'Provider says no'}}}}"
    summary = describe_provider_error(status, error)
    assert summary.startswith(start)
    assert "Provider says no" in summary


@pytest.mark.parametrize(
    "error", ["APITimeoutError: Request timed out.", "APIConnectionError: Connection error."]
)
def test_timeouts_and_connection_errors_read_as_unavailable(error):
    assert describe_provider_error(None, error).startswith(
        "OpenRouter or the model provider is unavailable."
    )


def test_an_unparseable_body_still_yields_a_sentence():
    summary = describe_provider_error(500, "Response body was not JSON: <html>oops</html>")
    assert summary.startswith("OpenRouter or the model provider is unavailable.")


def test_a_long_provider_message_is_shortened():
    long_message = "x" * 1000
    error = f"APIStatusError: Error code: 400 - {json.dumps({'error': {'message': long_message}})}"
    assert len(describe_provider_error(400, error)) < 450


@pytest.fixture
def fake_llm():
    from llm.fake import FakeLLMClient
    from pipeline.clients import reset_llm_client, set_llm_client

    fake = FakeLLMClient()
    set_llm_client(fake)
    yield fake
    reset_llm_client()


@pytest.fixture
def taxonomy(db):
    from taxonomy.models import HCV, Taxonomy

    tax = Taxonomy.objects.create(vocabulary_id="HCV-21", version="1.1", is_active=True)
    HCV.objects.create(taxonomy=tax, hcv_id="HCV-07", name="Malicious Insiders", order=7)
    return tax


@pytest.mark.django_db
def test_a_labelling_run_that_failed_for_one_reason_says_why(api_client, taxonomy, fake_llm):
    from django.contrib.auth import get_user_model

    from corpus.models import Origin, Scenario, SystemDescription
    from pipeline.models import Experiment
    from pipeline.orchestration import execute_pipeline_run, launch
    from prompts.models import PromptTemplate

    system = SystemDescription.objects.create(title="Oldsmar", body="b", origin=Origin.SEED)
    chosen = [
        Scenario.objects.create(system_description=system, scenario_id=f"sc_0{i}", origin=Origin.SEED)
        for i in (1, 2)
    ]
    label_prompt = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    experiment = Experiment.objects.create(
        name="p3", taxonomy=taxonomy, phases=[3], phase3_model="m3", phase3_prompt=label_prompt
    )
    experiment.label_scenarios.set(chosen)
    run = launch(experiment, n_repetitions=1)[0]
    fake_llm.fail_next(CREDIT, http_status=402)
    fake_llm.fail_next(CREDIT, http_status=402)
    execute_pipeline_run(run)

    labelling = run.phase_runs.get()
    assert labelling.error.startswith("2 scenario(s) failed to label. Not enough OpenRouter credit")

    member = get_user_model().objects.create_user(email="m@example.com", password="pw-123456")
    api_client.force_authenticate(user=member)
    calls = api_client.get(f"/api/phase-runs/{labelling.id}/calls/").json()["results"]
    assert calls[0]["error_summary"].startswith("Not enough OpenRouter credit")


@pytest.mark.django_db
def test_mixed_failure_reasons_keep_the_plain_count(taxonomy, fake_llm):
    from corpus.models import Origin, Scenario, SystemDescription
    from pipeline.models import Experiment
    from pipeline.orchestration import execute_pipeline_run, launch
    from prompts.models import PromptTemplate

    system = SystemDescription.objects.create(title="Oldsmar", body="b", origin=Origin.SEED)
    chosen = [
        Scenario.objects.create(system_description=system, scenario_id=f"sc_0{i}", origin=Origin.SEED)
        for i in (1, 2)
    ]
    label_prompt = PromptTemplate.objects.create_version(phase=3, name="l", user_prompt="l")
    experiment = Experiment.objects.create(
        name="p3", taxonomy=taxonomy, phases=[3], phase3_model="m3", phase3_prompt=label_prompt
    )
    experiment.label_scenarios.set(chosen)
    run = launch(experiment, n_repetitions=1)[0]
    fake_llm.fail_next(CREDIT, http_status=402)
    fake_llm.queue("not json")
    execute_pipeline_run(run)
    assert run.phase_runs.get().error == "2 scenario(s) failed to label."


@pytest.mark.django_db
def test_a_generation_run_that_failed_says_why(taxonomy, fake_llm):
    from pipeline.models import Experiment
    from pipeline.orchestration import execute_pipeline_run, launch
    from prompts.models import PromptTemplate

    generation = PromptTemplate.objects.create_version(phase=2, name="g", user_prompt="g")
    experiment = Experiment.objects.create(
        name="gen", taxonomy=taxonomy, phases=[2], phase2_model="m2",
        phase2_prompt_a=generation, phase2_prompt_b=generation,
    )
    experiment.variants = ["A"]
    experiment.save()
    run = launch(experiment, n_repetitions=1)[0]
    fake_llm.fail_next(
        "AuthenticationError: Error code: 401 - {'error': {'message': 'No auth credentials found'}}",
        http_status=401,
    )
    execute_pipeline_run(run)
    generation_run = run.phase_runs.get()
    assert generation_run.error.startswith("OpenRouter rejected the API key.")
    assert "No auth credentials found" in generation_run.error


def test_without_a_provider_message_the_raw_details_are_kept():
    summary = describe_provider_error(None, "APITimeoutError: Request timed out.")
    assert summary.endswith("Details: Request timed out.")
