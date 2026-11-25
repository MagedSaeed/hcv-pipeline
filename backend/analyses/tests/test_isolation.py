import json

import pytest
from django.db import connection

from corpus.models import Scenario, SystemDescription
from pipeline.export import build_bundle
from pipeline.models import Experiment, Label, LLMCall, PhaseRun, PipelineRun
from prompts.models import PromptTemplate

pytestmark = pytest.mark.django_db

RESEARCH_MODELS = (Experiment, PipelineRun, PhaseRun, SystemDescription, Scenario, LLMCall, Label)
RESEARCH_LISTS = (
    "experiments",
    "pipeline-runs",
    "phase-runs",
    "labels",
    "system-descriptions",
    "scenarios",
)
SENTINEL = "ZEBRA-CROSSING-SENTINEL"


@pytest.fixture
def full_flow(client_for, staff, v12, queue, fake):
    def full_flow():
        client = client_for(staff)
        body = {"name": f"{SENTINEL} name", "description": f"{SENTINEL} description"}
        path = f"/api/analyses/{client.post('/api/analyses/', body, format='json').json()['id']}/"
        extracted = {
            "elements": [
                {
                    "id": "SentinelApp",
                    "section": "",
                    "field": "interface",
                    "value": "MobileApp",
                    "fact_type": "Asserted",
                    "reason": f"{SENTINEL} reason",
                    "other_reading": "",
                }
            ],
            "facts": [],
            "controls": [],
            "gaps": {"not_assessable": [{"area": f"{SENTINEL} area", "reason": ""}], "no_term": []},
        }
        fake.queue(json.dumps(extracted))
        assert client.post(f"{path}extract/").status_code == 202
        queue.drain()
        assert client.post(f"{path}items/approve-asserted/").status_code == 200
        card = {
            "scenario_id": "sc_01",
            "trigger": f"{SENTINEL} trigger",
            "narrative_steps": [f"{SENTINEL} step"],
        }
        fake.queue(json.dumps({"scenarios": [card]}))
        assert client.post(f"{path}generate/").status_code == 202
        queue.drain()
        manual = {"narrative": f"{SENTINEL} manual"}
        assert client.post(f"{path}scenarios/", manual, format="json").status_code == 201
        label = {"hcv_id": "HCV-17", "evidence": f"{SENTINEL} quote", "rationale": "why"}
        for _ in range(2):
            fake.queue(json.dumps({"labels": [label]}))
        assert client.post(f"{path}label/").status_code == 202
        queue.drain()
        report = client.get(f"{path}report/").json()
        assert report["findings"][0]["scenario_count"] == 2
        return client, path

    return full_flow


def test_full_flow_writes_no_research_rows(full_flow, client_for, staff, v12):
    prompt = PromptTemplate.objects.create(phase=3, name="label", version=1, user_prompt="x")
    experiment = Experiment.objects.create(
        name="Research",
        phases=[2],
        phase2_model="m",
        phase2_prompt_b=PromptTemplate.objects.create(
            phase=2, name="gen", version=1, variant="B", user_prompt="x"
        ),
        phase3_prompt=prompt,
        taxonomy=v12,
        variants=["B"],
    )
    client = client_for(staff)

    def research():
        lists = {name: client.get(f"/api/{name}/").json() for name in RESEARCH_LISTS}
        assert all("results" in body for body in lists.values())
        bundle = build_bundle(experiment)
        counts = [model.objects.count() for model in RESEARCH_MODELS]
        return lists, json.dumps(bundle, sort_keys=True, default=str), counts

    before = research()
    full_flow()

    assert research() == before


def tables_holding(text):
    holding = []
    with connection.cursor() as cursor:
        for table in connection.introspection.table_names(cursor):
            for column in connection.introspection.get_table_description(cursor, table):
                cursor.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{column.name}" AS TEXT) LIKE %s',
                    [f"%{text}%"],
                )
                if cursor.fetchone()[0]:
                    holding.append(f"{table}.{column.name}")
    return holding


def test_delete_leaves_no_text_in_any_table(full_flow):
    client, path = full_flow()
    assert tables_holding(SENTINEL)

    assert client.delete(path).status_code == 204

    assert tables_holding(SENTINEL) == []
