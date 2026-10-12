"""Real subprocess transport with invented external replies; no SDK/model evidence."""

import asyncio
import sys
import pytest
import tryfolk.process as transport


@pytest.mark.parametrize("configured", [None, "relative-evidence"])
async def test_native_failure_binds_private_bytes_with_unset_or_relative_evidence_root(
    tmp_path, monkeypatch, configured,
):
    """Real subprocess, invented native error; no SDK/provider/model operation."""
    from types import SimpleNamespace
    from tryfolk.runtime import Runtime
    from tryfolk.pre_submission import runtime_gate

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(transport, "ROOT", tmp_path / "owned")
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    if configured is None:
        monkeypatch.delenv("TRYFOLK_EVIDENCE_ROOT", raising=False)
    else:
        monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", configured)
    runtime = Runtime(SimpleNamespace())
    original_spawn = asyncio.create_subprocess_exec
    script = '''import os,json,sys
from pathlib import Path
sys.stdin.readline()
root=Path(os.environ.get('TRYFOLK_EVIDENCE_ROOT','runtime/evidence'))
root.mkdir(parents=True,exist_ok=True)
raw={'stage':'manual_address','reason':'manual_document_unstable',
 'financial_submission_started':False,'financial_poll_started':False,
 'owned_origin':'http://127.0.0.1:3301',
 'checkout_origin':'https://test.checkout.dodopayments.com'}
path=root/'stagehand-error-fixture.json'
path.write_text(json.dumps({'error_type':'ManualAddressGate','pre_submission_evidence':raw}))
path.chmod(0o600)
print(json.dumps({'ok':False,'error':'invented native gate',
 'error_evidence_path':str(path),'pre_submission_evidence':raw}),flush=True)
sys.stdin.read()
'''

    async def external_fixture(*args, **kwargs):
        return await original_spawn(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", external_fixture)
    run = {"id": "fixture-run", "provider": "dodo", "revision": "fixture-rev",
           "source_commit": "a" * 40, "source_digest": "b" * 64, "target_mode": "broken",
           "preview_url": "http://127.0.0.1:3301"}
    manifest = {"target_revision": run["revision"], "source_commit": run["source_commit"],
                "source_sha256": run["source_digest"], "mode": "broken",
                "provider_environment": "test_mode", "app_origin": run["preview_url"],
                "source_clean": True}
    sdk = transport.SDKProcess("stagehand")
    try:
        with pytest.raises(RuntimeError) as stopped:
            await sdk.request({"op": "invented"}, timeout=2)
        gate = runtime_gate(stopped.value, run, {"id": "fixture-persona"},
                            {"_retry_target_manifest": manifest}, runtime.evidence)
        assert gate is not None
        assert runtime.evidence.is_absolute()
        assert __import__("pathlib").Path(stopped.value.evidence_path).parent == runtime.evidence
    finally:
        await sdk.close()


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


async def test_persistent_persona_channels_serialize_and_keep_exact_identity(tmp_path, monkeypatch):
    from tryfolk.process import PersonaChannel
    monkeypatch.setattr(transport, 'ROOT', tmp_path)
    spawn = asyncio.create_subprocess_exec
    children = []
    script = """import json,sys
for line in sys.stdin:
 value=json.loads(line)
 print(json.dumps({'ok':True,'data':{'run_id':value['run_id'],'persona_id':value['persona_id'],'op':value['op']}}),flush=True)
"""
    async def external_fixture(*args, **kwargs):
        child = await spawn(sys.executable, '-c', script, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(transport.asyncio, 'create_subprocess_exec', external_fixture)
    service = transport.SDKProcess('tinytroupe')
    a, b = PersonaChannel(service,'run-unit-fixture','a'), PersonaChannel(service,'run-unit-fixture','b')
    try:
        values = await asyncio.gather(a.request({'op':'init'}), b.request({'op':'init'}))
        assert values == [
            {'run_id':'run-unit-fixture','persona_id':'a','op':'bulk_init'},
            {'run_id':'run-unit-fixture','persona_id':'b','op':'bulk_init'},
        ]
        assert len(children) == 1
        await asyncio.gather(a.close(), b.close())
    finally:
        await service.close()
    assert children[0].returncode is not None
