"""Real subprocess transport with invented external replies; no SDK/model evidence."""

import asyncio
import sys
import pytest
import tryfolk.process as transport


@pytest.mark.parametrize("size", [100_000, 2_100_000])
async def test_sdk_transport_accepts_actual_snapshot_sized_lines_and_closes_oversized_replies(
    tmp_path, monkeypatch, size
):
    monkeypatch.setattr(transport, "ROOT", tmp_path)
    spawn = asyncio.create_subprocess_exec
    script = f"import json,sys;sys.stdin.readline();print(json.dumps({{'ok':True,'data':{{'snapshot':'x'*{size}}}}}),flush=True);sys.stdin.read()"

    async def external_fixture(*args, **kwargs):
        return await spawn(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", external_fixture)
    sdk = transport.SDKProcess("external-unit-fixture")
    try:
        if size < 2_000_000:
            reply = await sdk.request({"op": "inspect"}, timeout=2)
            assert len(reply["snapshot"]) == size
        else:
            with pytest.raises(RuntimeError, match="response.*limit"):
                await sdk.request({"op": "inspect"}, timeout=2)
            assert sdk.process.returncode is not None
    finally:
        await sdk.close()


async def test_cancellation_stops_a_pending_independent_frame_read(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "ROOT", tmp_path)
    spawn = asyncio.create_subprocess_exec
    script = """import json,sys
sys.stdin.readline()
print(json.dumps({'callback':'verify_dodo_frame','call_id':'unit-frame','data':{}}),flush=True)
sys.stdin.read()
"""

    async def external_fixture(*args, **kwargs):
        return await spawn(sys.executable, "-c", script, **kwargs)

    reading = asyncio.Event()
    stopped = asyncio.Event()
    cancelled = False

    async def verify(data):
        reading.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", external_fixture)
    sdk = transport.SDKProcess("stagehand", lambda: cancelled, frame_verify=verify)
    task = asyncio.create_task(sdk.request({"op": "act"}, timeout=2))
    await asyncio.wait_for(reading.wait(), 1)
    cancelled = True
    try:
        with pytest.raises(transport.Cancelled):
            await task
        assert stopped.is_set() and sdk.process.returncode is not None
    finally:
        await sdk.close()


async def test_adapter_gets_only_a_correlated_frame_decision_from_the_controller(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "ROOT", tmp_path)
    spawn = asyncio.create_subprocess_exec
    script = """import json,sys
sys.stdin.readline()
print(json.dumps({'callback':'verify_dodo_frame','call_id':'unit-frame','data':{'payment_id':'pay_fixture'}}),flush=True)
reply=json.loads(sys.stdin.readline())
print(json.dumps({'ok':True,'data':reply}),flush=True)
sys.stdin.read()
"""

    async def external_fixture(*args, **kwargs):
        return await spawn(sys.executable, "-c", script, **kwargs)

    calls = []

    async def verify(data):
        calls.append(data)
        return {"approved": True, "payment_id": "pay_fixture"}

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", external_fixture)
    sdk = transport.SDKProcess("stagehand", frame_verify=verify)
    try:
        result = await sdk.request({"op": "act"}, timeout=2)
        assert calls == [{"payment_id": "pay_fixture"}]
        assert result == {
            "callback_reply": "unit-frame",
            "data": {"approved": True, "payment_id": "pay_fixture"},
        }
    finally:
        await sdk.close()
