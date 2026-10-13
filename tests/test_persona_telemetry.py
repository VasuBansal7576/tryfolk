"""Offline SDK/HTTP checks, never evidence of live Neatlogs delivery."""

import asyncio
import json

import httpx
import pytest

from test_telemetry import POISON, RUN, attrs, otlp, spans  # noqa: F401
from tryfolk.telemetry import Telemetry, read_trace


@pytest.mark.parametrize("pass_name", ["before", "after"])
async def test_four_concurrent_personas_have_distinct_correlated_private_traces(otlp, tmp_path, pass_name):  # noqa: F811
    telemetry = Telemetry()
    personas = [str(index) * 32 for index in range(3, 7)]
    ready = asyncio.Event()
    started = []

    async def journey(persona_id):
        with telemetry.persona(persona_id):
            started.append(persona_id)
            if len(started) == 4:
                ready.set()
            await ready.wait()
            telemetry.event("observation", {"text": POISON, "actions": []}, persona_id)
            await asyncio.sleep(0)
            telemetry.event("verification", {
                "outcome": "failed", "classification": "paid_without_access", "provider": "dodo",
                "payment_verified": True, "entitlement_verified": False,
            }, persona_id)

    with telemetry.run({**RUN, "pass": pass_name, "persona_count": 4}):
        await asyncio.gather(*(journey(persona_id) for persona_id in personas))
    exported = spans(otlp)
    roots = [span for span in exported if span.name == "tryfolk.persona_journey"]
    assert len(roots) == 4
    assert len({bytes(span.trace_id) for span in roots}) == 4
    for root in roots:
        identity = json.loads(attrs(root)["input.value"].string_value)
        assert identity["pass"] == pass_name
        assert identity["persona_id"] in personas
        assert not root.parent_span_id
        events = [span for span in exported if span.trace_id == root.trace_id
                  and span.name != "neatlogs.trace.complete" and span is not root]
        assert len(events) == 2
        assert all(event.parent_span_id == root.span_id for event in events)
        assert all(json.loads(attrs(event)["input.value"].string_value)["persona_id"]
                   == identity["persona_id"] for event in events)
        trace_id = bytes(root.trace_id).hex()
        assert telemetry.persona_url(identity["persona_id"]) == "https://app.neatlogs.com/traces/" + trace_id
        receipt = json.loads((tmp_path / f"{RUN['id']}-{trace_id}-trace.json").read_text())
        assert receipt["delivery_flushed"] is True
        assert receipt["finalized_readback"] == "not_verified"
        assert len(receipt["expected_spans"]) == 3
    assert POISON.encode() not in b"".join(data for data, _ in otlp)
    assert len([span for span in exported if span.name == "neatlogs.trace.complete"]) == 5


def hosted_mock(monkeypatch, payload, status=200):
    original_client = httpx.AsyncClient

    def handle(request):
        assert request.method == "GET"
        assert request.headers["x-api-key"] == "offline-test-key"
        assert "offline-test-key" not in str(request.url)
        assert "x-neatlogs-doctor" not in request.headers
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))


def make_receipt(tmp_path, monkeypatch):
    trace_id = "8" * 32
    monkeypatch.setenv("NEATLOGS_API_KEY", "offline-test-key")
    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(tmp_path))
    receipt = {
        "trace_id": trace_id, "run_id": RUN["id"], "persona_id": "3" * 32,
        "pass": "before", "delivery_flushed": True, "finalized_readback": "not_verified",
        "expected_spans": {"a" * 16: "tryfolk.persona_journey", "b" * 16: "tryfolk.verification"},
    }
    path = tmp_path / f"{RUN['id']}-{trace_id}-trace.json"
    path.write_text(json.dumps(receipt))
    payload = {
        "_id": trace_id, "status": "success", "spans": [
            {"span_id": span_id, "node_name": name, "data": {"output_value": {"measured": True}}}
            for span_id, name in receipt["expected_spans"].items()
        ],
    }
    return trace_id, path, payload


async def test_readback_verifies_actual_identity_and_all_exported_spans(tmp_path, monkeypatch):
    trace_id, path, payload = make_receipt(tmp_path, monkeypatch)
    hosted_mock(monkeypatch, payload)
    result = await read_trace(RUN["id"], trace_id)
    assert result["finalized_readback"] == "verified"
    assert len(result["spans"]) == 2
    receipt = json.loads(path.read_text())
    assert receipt["finalized_readback"] == "verified"
    assert receipt["readback_attempts"][0]["source"] == "official_neatlogs_trace_api"
    assert json.loads((tmp_path / f"{RUN['id']}-{trace_id}-readback.json").read_text()) == payload


async def test_finalized_failed_journey_is_readable_without_claiming_success(tmp_path, monkeypatch):
    trace_id, path, payload = make_receipt(tmp_path, monkeypatch)
    payload["status"] = "error"
    payload["spans"][1]["data"]["output_value"] = {"outcome": "failed"}
    hosted_mock(monkeypatch, payload)
    result = await read_trace(RUN["id"], trace_id)
    assert result["finalized_readback"] == "verified"
    assert result["hosted_status"] == "error"
    assert result["spans"][1]["data"]["output_value"]["outcome"] == "failed"
    assert json.loads(path.read_text())["hosted_status"] == "error"


@pytest.mark.parametrize("fault", ["wrong_id", "processing", "missing_span", "duplicate_span", "delivery_failed"])
async def test_readback_never_confuses_acceptance_or_incomplete_delivery_with_verified_trace(
    tmp_path, monkeypatch, fault,
):
    trace_id, path, payload = make_receipt(tmp_path, monkeypatch)
    if fault == "wrong_id":
        payload["_id"] = "9" * 32
    elif fault == "processing":
        payload["status"] = "processing"
    elif fault == "missing_span":
        payload["spans"].pop()
    elif fault == "duplicate_span":
        payload["spans"].append(payload["spans"][0])
    else:
        receipt = json.loads(path.read_text())
        receipt["delivery_flushed"] = False
        path.write_text(json.dumps(receipt))
    hosted_mock(monkeypatch, payload)
    result = await read_trace(RUN["id"], trace_id)
    assert result["finalized_readback"] == "not_verified"
    assert result["spans"] == []
    assert json.loads(path.read_text())["finalized_readback"] == "not_verified"


async def test_missing_key_does_not_attempt_readback(tmp_path, monkeypatch):
    trace_id, _, _ = make_receipt(tmp_path, monkeypatch)
    monkeypatch.delenv("NEATLOGS_API_KEY")
    result = await read_trace(RUN["id"], trace_id)
    assert result["reason"] == "credential_missing"
    assert result["finalized_readback"] == "not_verified"


def test_trace_endpoints_authenticate_and_reject_another_runs_trace(tmp_path, monkeypatch):
    from test_api import auth, client_at, quote, start

    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(tmp_path))
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    client = client_at(tmp_path / "runs.sqlite")
    first = start(client, quote(client), "trace-first").json()
    second = start(client, quote(client), "trace-second").json()
    trace_id = "8" * 32
    with client.app.state.service.store.transaction() as db:
        db.execute(
            "INSERT INTO events(run_id,persona_id,kind,created_at,data,trace_url) VALUES (?,?,?,?,?,?)",
            (first["id"], "3" * 32, "verification", "now", "{}",
             "https://app.neatlogs.com/traces/" + trace_id),
        )
    (tmp_path / f"{first['id']}-{trace_id}-trace.json").write_text(json.dumps({
        "trace_id": trace_id, "run_id": first["id"], "persona_id": "3" * 32,
        "delivery_flushed": True, "finalized_readback": "not_verified",
    }))
    assert client.get(f"/runs/{first['id']}/traces").status_code == 401
    assert client.get(f"/runs/{first['id']}/traces/{trace_id}").status_code == 401
    assert client.get(f"/runs/{first['id']}/traces", headers=auth(second)).status_code == 401
    listing = client.get(f"/runs/{first['id']}/traces", headers=auth(first))
    assert listing.status_code == 200
    assert listing.json()["traces"][0]["delivery_flushed"] is True
    assert listing.json()["traces"][0]["finalized_readback"] == "not_verified"
    inspection = client.get(f"/runs/{first['id']}/traces/{trace_id}", headers=auth(first))
    assert inspection.status_code == 200
    assert inspection.json()["reason"] == "credential_missing"
    assert "no-store" in inspection.headers["cache-control"]
    assert client.get(
        f"/runs/{second['id']}/traces/{trace_id}", headers={"Authorization": "Bearer test-owner"},
    ).status_code == 404


async def test_worker_keeps_verification_under_each_real_sdk_persona_root(otlp, tmp_path):  # noqa: F811
    from fastapi.testclient import TestClient
    from test_api import auth, quote, start
    from test_worker import FixtureIO
    from tryfolk.api import create_app
    from tryfolk.config import Settings, Target
    from tryfolk.worker import Worker

    class ConcurrentOfflineIO(FixtureIO):
        """Invented external payment/browser I/O, real installed telemetry SDK."""
        journey_concurrency = 4

        def __init__(self):
            super().__init__()
            self.telemetry = Telemetry()
            self.ready = asyncio.Event()
            self.active = 0

        async def generate(self, brief, count):
            population = await super().generate(brief, count)
            for index, persona in enumerate(population):
                persona["id"] = str(index + 3) * 32
            return population

        async def journey(self, run, persona, cancelled, emit):
            self.active += 1
            if self.active == 4:
                self.ready.set()
            await self.ready.wait()
            return await super().journey(run, persona, cancelled, emit)

    client = TestClient(create_app(Settings(
        db_path=tmp_path / "runs.sqlite", owner_token="test-owner", gates=(),
        targets=(Target(url="http://127.0.0.1:3100", revision=RUN["revision"], fault="paid-no-access"),),
    )))
    run = start(client, quote(client, 4)).json()
    io = ConcurrentOfflineIO()
    await Worker(client.app.state.service, io).tick()
    status = client.get(f"/runs/{run['id']}", headers=auth(run)).json()
    assert status["phase"] == "completed"
    assert status["counts"]["executed"] == 4
    listing = client.get(f"/runs/{run['id']}/traces", headers=auth(run)).json()["traces"]
    assert len([item for item in listing if item["persona_id"] is not None]) == 4
    for root in [span for span in spans(otlp) if span.name == "tryfolk.persona_journey"]:
        verification = [span for span in spans(otlp)
                        if span.trace_id == root.trace_id and span.name == "tryfolk.verification"]
        assert len(verification) == 1
        assert verification[0].parent_span_id == root.span_id
        assert verification[0].end_time_unix_nano <= root.end_time_unix_nano
