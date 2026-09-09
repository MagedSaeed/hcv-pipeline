import subprocess
import sys
from pathlib import Path

import pytest

from llm.fake import FakeLLMClient

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_returns_queued_responses_in_order():
    client = FakeLLMClient()
    client.queue('{"labels": []}')
    client.queue('{"labels": [{"hcv_id": "HCV-07"}]}')

    first = client.complete(model="m", system_prompt="", user_prompt="a")
    second = client.complete(model="m", system_prompt="", user_prompt="b")

    assert first.raw_response == '{"labels": []}'
    assert "HCV-07" in second.raw_response


def test_records_every_call_for_assertions():
    client = FakeLLMClient()
    client.queue("ok")
    client.complete(model="anthropic/x", system_prompt="sys", user_prompt="usr", temperature=0.3)

    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "anthropic/x"
    assert client.calls[0]["system_prompt"] == "sys"
    assert client.calls[0]["user_prompt"] == "usr"
    assert client.calls[0]["temperature"] == 0.3


def test_running_out_of_responses_is_a_loud_error():
    with pytest.raises(AssertionError, match="no queued response"):
        FakeLLMClient().complete(model="m", system_prompt="", user_prompt="u")


def test_the_exhaustion_error_survives_python_dash_o():
    """A bare `assert` is stripped under -O, turning the exhaustion check into
    an opaque IndexError from `list.pop`; it must raise AssertionError
    explicitly instead so the failure stays legible under -O too."""
    script = (
        "from llm.fake import FakeLLMClient\n"
        "FakeLLMClient().complete(model='m', system_prompt='', user_prompt='u')\n"
    )
    result = subprocess.run(
        [sys.executable, "-O", "-c", script],
        capture_output=True,
        text=True,
        cwd=BACKEND_DIR,
    )
    assert result.returncode != 0
    assert "AssertionError" in result.stderr
    assert "IndexError" not in result.stderr


def test_fail_next_produces_a_transport_failure_record():
    client = FakeLLMClient()
    client.fail_next("rate limited", http_status=429)
    result = client.complete(model="m", system_prompt="", user_prompt="u")

    assert result.ok is False
    assert result.response_json is None
    assert result.http_status == 429
    assert "rate limited" in result.transport_error


def test_queue_overrides_the_projected_columns():
    client = FakeLLMClient()
    client.queue("hi", finish_reason="length", prompt_tokens=11, completion_tokens=2)
    result = client.complete(model="m", system_prompt="", user_prompt="u")

    assert result.finish_reason == "length"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 2
    assert result.response_json["choices"][0]["message"]["content"] == "hi"


def test_constructor_accepts_plain_strings_as_documented():
    """FakeLLMClient(responses=[...]) — the documented constructor usage —
    takes bare strings, not the internal {"text": ...} dict shape."""
    client = FakeLLMClient(responses=['{"labels": []}', "second"])

    first = client.complete(model="m", system_prompt="", user_prompt="a")
    second = client.complete(model="m", system_prompt="", user_prompt="b")

    assert first.raw_response == '{"labels": []}'
    assert second.raw_response == "second"
