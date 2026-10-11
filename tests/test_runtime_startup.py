"""Runtime lifecycle with real JSONL subprocesses and invented HTTP/SDK replies."""

import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest

import tryfolk.process as transport
from tryfolk.runtime import Runtime
from tryfolk.store import Store


PERSONA = {
    "id": "fixture-persona",
    "name": "Fixture customer",
    "profile": {"preferences": ["keep the complete profile"], "personality": "careful"},
    "conditions": {"history": "new", "device": "desktop", "locale": "en-US"},
    "initial_snapshot": {"persona": {"name": "Fixture customer"}},
    "initial_semantic_memory": [{"type": "information", "content": "Keep initial memory."}],
    "memory": [],
}


def runtime_fixture(tmp_path, monkeypatch, settings):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(tmp_path / "evidence"))
    monkeypatch.setenv("TARGET_FIXTURE_SECRET", "invented-fixture-secret")
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    monkeypatch.setattr(transport, "ROOT", tmp_path)
    original_spawn = asyncio.create_subprocess_exec
    children = []
    fixture = Path(__file__).parent / "fixtures/startup_sdk.py"

    async def spawn(_python, adapter, **kwargs):
        role = Path(adapter).parent.name
        child = await original_spawn(sys.executable, "-u", str(fixture), role, str(tmp_path), **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
    fixture_account = {
        "account_id": "fixture-account",
        "checkout_id": "fixture-checkout",
        "product_id": "fixture-product",
        "amount": 100,
        "currency": "USD",
        "session_url": "/fixture-session",
    }

    def http_fixture(request):
        path = request.url.path
        if path == "/health":
            body = {
                "revision": "fixture-revision",
                "fault": "fixture",
                "provider": "local-fixture",
                "source_digest": "fixture-revision",
            }
        elif path == "/fixtures/accounts":
            body = fixture_account
        elif path == "/payments/fixture-checkout":
            body = {
                **fixture_account,
                "payment_id": "fixture-payment",
                "provider": "local-fixture",
                "status": "succeeded",
            }
        elif path == "/entitlements/fixture-account":
            body = {
                "account_id": "fixture-account",
                "product_id": "fixture-product",
                "payment_id": "fixture-payment",
                "granted": True,
                "units": 1,
            }
        else:
            raise AssertionError(f"Unplanned fixture HTTP path: {path}")
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(http_fixture))
    )
    store = Store(tmp_path / "fixture.sqlite")
    run = {
        "id": "fixture-run",
        "provider": "local-fixture",
        "pass": "before",
        "preview_url": "http://127.0.0.1:3100",
        "revision": "fixture-revision",
        "fault": "fixture",
        "phase": "running",
    }
    with store.transaction() as db:
        store.save(db, "runs", run)
    return Runtime(SimpleNamespace(store=store)), run, children


async def test_independent_startups_overlap_without_observing_before_saved_memory_is_restored(
    tmp_path, monkeypatch
):
    runtime, run, children = runtime_fixture(tmp_path, monkeypatch, {"require_overlap": True})
    events = []

    async def emit(kind, data, persona_id):
        events.append((kind, data, persona_id))

    result = await runtime.journey(run, PERSONA, lambda: False, emit)
    assert result["outcome"] == "completed" and result["measured"] is False
    assert result["payment_verified"] and result["entitlement_verified"]
    assert result["capability_checked"] is False  # A fixture FINISH does not invent a download.
    assert [kind for kind, _, _ in events] == [
        "persona_initialized",
        "observation",
        "decision",
        "persona_memory",
    ]
    assert events[0][1]["restored_persona"] == PERSONA
    assert events[-1][1]["snapshot"] == "retained fixture memory"
    assert len(children) == 2 and all(child.returncode is not None for child in children)
    for role in ("tinytroupe", "stagehand"):
        assert (tmp_path / f"{role}.closed").exists()


@pytest.mark.parametrize("failed_role", ["tinytroupe", "stagehand"])
async def test_startup_failure_stops_both_peers_without_publishing_uninitialized_memory(
    tmp_path, monkeypatch, failed_role
):
    runtime, run, children = runtime_fixture(
        tmp_path,
        monkeypatch,
        {
            "require_overlap": True,
            "failed_role": failed_role,
            "tinytroupe_delay": 0.15,
            "stagehand_delay": 0.15,
        },
    )
    events = []

    async def emit(kind, data, persona_id):
        events.append(kind)

    with pytest.raises(RuntimeError, match=f"{failed_role}: fixture startup failed"):
        await runtime.journey(run, PERSONA, lambda: False, emit)
    assert events == []
    assert len(children) == 2 and all(child.returncode is not None for child in children)
    commands = [
        json.loads(line) for line in (tmp_path / "tinytroupe.commands.jsonl").read_text().splitlines()
    ]
    assert [item["op"] for item in commands] == ["init"]


async def test_cancellation_drains_startup_requests_and_closes_both_peers(tmp_path, monkeypatch):
    runtime, run, children = runtime_fixture(
        tmp_path,
        monkeypatch,
        {
            "require_overlap": True,
            "tinytroupe_delay": 2,
            "stagehand_delay": 2,
        },
    )
    events = []

    async def emit(kind, data, persona_id):
        events.append(kind)

    journey = asyncio.create_task(runtime.journey(run, PERSONA, lambda: False, emit))
    try:
        async with asyncio.timeout(1):
            while not all((tmp_path / f"{role}.started").exists() for role in ("tinytroupe", "stagehand")):
                await asyncio.sleep(0.005)
    finally:
        journey.cancel()
        with pytest.raises(asyncio.CancelledError):
            await journey
    assert events == []
    assert len(children) == 2 and all(child.returncode is not None for child in children)
    commands = [
        json.loads(line) for line in (tmp_path / "tinytroupe.commands.jsonl").read_text().splitlines()
    ]
    assert [item["op"] for item in commands] == ["init"]


async def test_memory_snapshot_failure_still_closes_both_peers(tmp_path, monkeypatch):
    runtime, run, children = runtime_fixture(
        tmp_path,
        monkeypatch,
        {
            "require_overlap": True,
            "failed_snapshot": True,
        },
    )
    events = []

    async def emit(kind, data, persona_id):
        events.append(kind)

    try:
        with pytest.raises(RuntimeError, match="tinytroupe: fixture snapshot failed"):
            await runtime.journey(run, PERSONA, lambda: False, emit)
        assert events == ["persona_initialized", "observation", "decision"]
        assert len(children) == 2 and all(child.returncode is not None for child in children)
    finally:
        # Reap external fixture peers even when this regression fails.
        for child in children:
            if child.returncode is None:
                child.terminate()
                await child.wait()


async def test_browser_startup_failure_preserves_already_restored_memory(tmp_path, monkeypatch):
    runtime, run, children = runtime_fixture(
        tmp_path,
        monkeypatch,
        {
            "require_overlap": True,
            "failed_role": "stagehand",
            "wait_for_peer_ready_before_failure": True,
        },
    )
    events = []

    async def emit(kind, data, persona_id):
        events.append((kind, data))

    with pytest.raises(RuntimeError, match="stagehand: fixture startup failed"):
        await runtime.journey(run, PERSONA, lambda: False, emit)
    assert events == [("persona_memory", {"fixture": True, "snapshot": "retained fixture memory"})]
    assert len(children) == 2 and all(child.returncode is not None for child in children)
