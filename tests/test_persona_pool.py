"""Actual JSONL child processes with controlled replies, no SDK/model evidence."""

import asyncio
import sys

import pytest

import tryfolk.process as transport


@pytest.fixture
def decision_children(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "ROOT", tmp_path)
    spawn = asyncio.create_subprocess_exec
    children = []
    script = '''import json,os,sys,time
from pathlib import Path
people={}
for line in sys.stdin:
    command=json.loads(line)
    key=(command['run_id'],command['persona_id'])
    op=command['op']
    if op=='bulk_init':
        if people:
            raise RuntimeError('old customer memory was not released')
        people[key]=0
        data={'pid':os.getpid(),'persona_id':key[1]}
    elif op=='bulk_release':
        del people[key]
        data={'released':True}
    elif op=='bulk_fail':
        print(json.dumps({'ok':False,'error':'controlled failure'}),flush=True)
        continue
    else:
        people[key]+=1
        if command.get('barrier'):
            barrier=Path(command['barrier'])
            (barrier/key[1]).write_text(str(os.getpid()))
            while not (barrier/'release').exists():
                time.sleep(.01)
        data={'pid':os.getpid(),'persona_id':key[1],'steps':people[key]}
    print(json.dumps({'ok':True,'data':data}),flush=True)
'''

    async def controlled_child(*args, **kwargs):
        child = await spawn(sys.executable, "-c", script, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", controlled_child)
    return children


async def test_four_decisions_reach_barrier_together_and_preserve_persona_memory(tmp_path, decision_children):
    pool = transport.PersonaPool(4, "same-run")
    leases = await asyncio.gather(*(pool.acquire(f"customer-{index}") for index in range(4)))
    barrier = tmp_path / "decision-barrier"
    barrier.mkdir()
    decisions = []
    try:
        initial = await asyncio.gather(*(lease.request({"op": "init"}) for lease in leases))
        assert len({value["pid"] for value in initial}) == 4
        decisions = [asyncio.create_task(lease.request({"op": "step", "barrier": str(barrier)}))
                     for lease in leases]

        async def all_decisions_started():
            while len(list(barrier.glob("customer-*"))) != 4:
                await asyncio.sleep(0.01)

        # A shared request lock could only produce one marker before release.
        await asyncio.wait_for(all_decisions_started(), 4)
        assert all(not task.done() for task in decisions)
        (barrier / "release").touch()
        first = await asyncio.gather(*decisions)
        second = await asyncio.gather(*(lease.request({"op": "step"}) for lease in leases))
        for index, (before, after) in enumerate(zip(first, second)):
            assert before["pid"] == after["pid"] == initial[index]["pid"]
            assert before["persona_id"] == after["persona_id"] == f"customer-{index}"
            assert before["steps"] == 1 and after["steps"] == 2
        await asyncio.gather(*(lease.close() for lease in leases))
        replacements = await asyncio.gather(*(pool.acquire(f"next-{index}") for index in range(4)))
        await asyncio.gather(*(lease.request({"op": "init"}) for lease in replacements))
        reset = await asyncio.gather(*(lease.request({"op": "step"}) for lease in replacements))
        assert all(value["steps"] == 1 for value in reset)
        assert len(decision_children) == 4
        await asyncio.gather(*(lease.close() for lease in replacements))
    finally:
        for task in decisions:
            task.cancel()
        await asyncio.gather(*decisions, return_exceptions=True)
        await pool.close()
    assert all(child.returncode is not None for child in decision_children)


async def test_failed_decision_process_is_replaced_before_next_customer(decision_children):
    pool = transport.PersonaPool(1, "failed-run")
    try:
        first = await pool.acquire("first")
        initial = await first.request({"op": "init"})
        with pytest.raises(RuntimeError, match="controlled failure"):
            await first.request({"op": "fail"})
        await first.close()
        assert decision_children[0].returncode is not None
        second = await pool.acquire("second")
        clean = await second.request({"op": "init"})
        assert clean["pid"] != initial["pid"]
        assert (await second.request({"op": "step"}))["steps"] == 1
        await second.close()
    finally:
        await pool.close()


async def test_cancelled_request_kills_owned_child_and_waiting_acquire_exits(tmp_path, decision_children):
    cancelled = False
    pool = transport.PersonaPool(1, "cancel-run", lambda: cancelled)
    lease = await pool.acquire("first")
    await lease.request({"op": "init"})
    barrier = tmp_path / "cancel-barrier"
    barrier.mkdir()
    request = asyncio.create_task(lease.request({"op": "step", "barrier": str(barrier)}))
    waiting = asyncio.create_task(pool.acquire("second"))
    try:
        while not (barrier / "first").exists():
            await asyncio.sleep(0.01)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await lease.close()
        assert decision_children[0].returncode is not None
        # The blocked acquire may already have the clean replacement; its lease
        # has no process yet and pool cleanup still owns that slot.
        replacement = await waiting
        cancelled = True
        with pytest.raises(transport.Cancelled):
            await pool.acquire("third")
        await replacement.close()
    finally:
        request.cancel()
        waiting.cancel()
        await asyncio.gather(request, waiting, return_exceptions=True)
        await pool.close()


async def test_pool_close_releases_waiting_acquire(decision_children):
    pool = transport.PersonaPool(1, "closing-run")
    await pool.acquire("first")
    waiting = asyncio.create_task(pool.acquire("second"))
    await pool.close()
    with pytest.raises(RuntimeError, match="already closed"):
        await asyncio.wait_for(waiting, 1)
