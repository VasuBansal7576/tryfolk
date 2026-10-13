"""Actual Runtime coordination with invented SDK I/O, never payment evidence."""
import asyncio
import time
from types import SimpleNamespace

import pytest
import tryfolk.runtime as module
from tryfolk.runtime import Runtime, FreshActionRetry
from tryfolk.trace_policy import TracePolicy, KINDS


def harness(tmp_path, monkeypatch, *, count=4, failing_lane=None, hold_pay=None, include_billing=False, include_purchase=False):
    monkeypatch.setattr(module, "NATIVE_PAY_START_SPACING_SECONDS", .025)
    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(tmp_path))
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    events, reads, pay_calls = [], [], []
    prepared = set()
    all_prepared = asyncio.Event()
    startup_roles = set()
    all_started = asyncio.Event()
    active, maximum = 0, 0
    hosted_active, hosted_maximum = 0, 0
    fields = ["card_number", "card_expiry", "card_cvc"]
    origin = "http://127.0.0.1:3301"

    class SDK:
        def __init__(self, name, *args, **kwargs):
            self.name, self.lane, self.step = name, None, 0
            self.process = SimpleNamespace(returncode=None)

        async def request(self, command, timeout=180):
            nonlocal active, maximum, hosted_active, hosted_maximum
            op = command["op"]
            if self.name == "tinytroupe":
                if op == "init":
                    startup_roles.add((self.name, command["persona"]["id"]))
                    if len(startup_roles) == count * 2:
                        all_started.set()
                    await asyncio.wait_for(all_started.wait(), 1)
                if op == "decide":
                    choices = command["observation"]["actions"]
                    return {"intent": {"type": "BROWSER" if choices else "FINISH",
                                       "target": choices[0]["id"] if choices else ""}}
                return {}
            if op == "goto":
                self.lane = command["url"].rsplit("/", 1)[-1]
                startup_roles.add((self.name, self.lane))
                if len(startup_roles) == count * 2:
                    all_started.set()
                await asyncio.wait_for(all_started.wait(), 1)
                return {}
            if op == "observe":
                assert len(startup_roles) == count * 2
                phases = [d["phase"] for k, d, p in events if k == "native_pay_admission" and p == self.lane]
                admitted = bool(phases and phases[-1] == "enter")
                reads.append((self.lane, self.step, admitted))
                offset = int(include_billing) + int(include_purchase)
                field_step = self.step - offset
                metadata = ({"kind": "purchase"} if include_purchase and self.step == 0
                            else {"kind": "billing_continue"} if include_billing and self.step == int(include_purchase)
                            else {"field": fields[field_step]} if 0 <= field_step < 3 else {"kind": "pay"})
                choices = [{"selector": "#invented", "method": "fill" if "field" in metadata else "click",
                            "description": "Invented SDK boundary choice", "_tryfolk": metadata}] if self.step < 4 + offset else []
                return {"url": origin, "text": "Invented SDK state", "actions": choices}
            if op == "act":
                offset = int(include_billing) + int(include_purchase)
                if include_purchase and self.step == 0:
                    assert runtime.hosted_checkout_permits[run["id"]].locked()
                    lane_reads = [entry for entry in reads if entry[0] == self.lane and entry[1] == 0]
                    assert len(lane_reads) == 2  # Discovery is discarded; selection uses a fresh read.
                    hosted_active += 1
                    hosted_maximum = max(hosted_maximum, hosted_active)
                    runtime.test_hosted_maximum = hosted_maximum
                if include_billing and self.step == int(include_purchase):
                    runtime.test_boundary_dispatches.append(("billing_continue", runtime.native_pay_started[run["id"]]))
                if self.step == 2 + offset:
                    prepared.add(self.lane)
                    if len(prepared) == count:
                        all_prepared.set()
                    if not include_purchase:
                        await asyncio.wait_for(all_prepared.wait(), 1)
                if self.step == 3 + offset:
                    if not include_purchase:
                        assert len(prepared) == count  # Partial SDK harness starts at card work.
                    else:
                        assert hosted_active == 1 and runtime.hosted_checkout_permits[run["id"]].locked()
                    pay_calls.append(self.lane)
                    runtime.test_dispatch_times.append(runtime.native_pay_started[run["id"]])
                    runtime.test_boundary_dispatches.append(("pay", runtime.native_pay_started[run["id"]]))
                    active += 1
                    maximum = max(maximum, active)
                    try:
                        if hold_pay is not None:
                            await hold_pay.wait()
                        await asyncio.sleep(.01)
                        if self.lane == failing_lane:
                            raise RuntimeError("Invented ambiguous Pay failure")
                    finally:
                        active -= 1
                        if include_purchase:
                            hosted_active -= 1
                    self.step += 1
                    return {"data": {"success": True}, "settlement": {"url": origin + "/checkout/return"}}
                self.step += 1
                return {"data": {"success": True}}
            return {}

        async def close(self):
            self.process.returncode = 0

    monkeypatch.setattr(module, "SDKProcess", SDK)
    monkeypatch.setattr(module, "validated_action", lambda intent, choices, **kwargs: choices[0]["action"])
    monkeypatch.setattr(module.BrowserPolicy, "validate", lambda *args: None)
    runtime = Runtime(SimpleNamespace())
    runtime.test_dispatch_times = []
    runtime.test_boundary_dispatches = []
    runtime.test_hosted_maximum = 0

    async def provision(run, persona):
        return {"login_url": origin + "/login/" + persona["id"], "account_id": persona["id"]}

    async def revision(*args):
        return {}

    async def verification(*args):
        return {"outcome": "pending", "measured": False}

    async def emit(kind, data, persona):
        events.append((kind, data, persona))

    runtime.provision, runtime.check_revision, runtime.verification = provision, revision, verification
    run = {"id": "a" * 32, "provider": "dodo", "preview_url": origin, "revision": "fixture"}
    people = [{"id": str(i), "conditions": {}, "initial_snapshot": {}, "profile": {}} for i in range(count)]
    return runtime, run, people, emit, events, reads, pay_calls, lambda: maximum


async def test_four_card_preparations_overlap_but_pay_return_windows_do_not(tmp_path, monkeypatch):
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch)
    results = await asyncio.gather(*(runtime.journey(run, p, lambda: False, emit) for p in people))
    assert len(pays) == 4 and maximum() == 1
    assert all(not r["measured"] for r in results)
    assert all(admitted for _, step, admitted in reads if step == 3)
    assert all(not admitted for _, step, admitted in reads if step < 3)
    phases = [d["phase"] for k, d, _ in events if k == "native_pay_admission"]
    assert phases.count("wait") == phases.count("enter") == phases.count("exit") == 4
    assert all(d["result"] == "owned_return_settled" for k, d, _ in events
               if k == "native_pay_admission" and d["phase"] == "exit")
    assert not runtime.native_pay_permits[run["id"]].locked()


async def test_four_sdk_startups_overlap_but_complete_hosted_windows_are_admitted_one_at_a_time(tmp_path, monkeypatch):
    runtime, run, people, emit, events, reads, pays, maximum = harness(
        tmp_path, monkeypatch, include_purchase=True, include_billing=True)
    results = await asyncio.gather(*(runtime.journey(run, p, lambda: False, emit) for p in people))
    assert len(results) == len(pays) == 4
    assert runtime.test_hosted_maximum == maximum() == 1
    admissions = [data for kind, data, _ in events if kind == "hosted_checkout_admission"]
    assert [d["phase"] for d in admissions].count("wait") == 4
    assert [d["phase"] for d in admissions].count("enter") == 4
    assert [d["phase"] for d in admissions].count("exit") == 4
    assert all(d["result"] == "owned_return_settled" for d in admissions if d["phase"] == "exit")
    assert all(d["capacity"] == 1 and d["payment_outcome_inferred"] is False for d in admissions)
    assert not runtime.hosted_checkout_permits[run["id"]].locked()


@pytest.mark.parametrize("cancel", [False, True])
async def test_hosted_resource_wait_is_bounded_without_selection_or_other_lease_release(tmp_path, monkeypatch, cancel):
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch, count=1, include_purchase=True)
    permit = asyncio.Lock()
    await permit.acquire()
    runtime.hosted_checkout_permits[run["id"]] = permit
    monkeypatch.setattr(module, "HOSTED_CHECKOUT_QUEUE_TIMEOUT_SECONDS", .03)
    task = asyncio.create_task(runtime.journey(run, people[0], lambda: False, emit))
    if cancel:
        while not any(k == "hosted_checkout_admission" for k, _, _ in events):
            await asyncio.sleep(0)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert pays == [] and reads == [("0", 0, False)]
    assert not any(k == "decision" for k, _, _ in events)
    assert permit.locked()
    permit.release()


@pytest.mark.parametrize("cancel", [False, True])
async def test_hosted_window_failure_or_cancellation_releases_without_resubmitting(tmp_path, monkeypatch, cancel):
    hold = asyncio.Event() if cancel else None
    runtime, run, people, emit, events, reads, pays, maximum = harness(
        tmp_path, monkeypatch, count=1, include_purchase=True, include_billing=True,
        failing_lane=None if cancel else "0", hold_pay=hold)
    task = asyncio.create_task(runtime.journey(run, people[0], lambda: False, emit))
    if cancel:
        while not pays:
            await asyncio.sleep(0)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await task
    assert pays == ["0"]
    assert not runtime.hosted_checkout_permits[run["id"]].locked()
    assert [d["result"] for k, d, _ in events if k == "hosted_checkout_admission" and d["phase"] == "exit"] == [
        "cancelled" if cancel else "failed"]


async def test_resource_queue_elapsed_198_seconds_starts_new_unchanged_action_deadline(tmp_path, monkeypatch):
    runtime, run, people, emit, events, reads, pays, maximum = harness(
        tmp_path, monkeypatch, count=1, include_purchase=True, include_billing=True)
    clock, created = [0], []

    class ControlledRetry(FreshActionRetry):
        def __init__(self, *, timeout=180):
            super().__init__(timeout=timeout)
            self.deadline = clock[0] + timeout
            created.append((clock[0], timeout))

        def remaining(self):
            remaining = self.deadline - clock[0]
            if remaining <= 0:
                raise RuntimeError("Original fresh action deadline exhausted; no action dispatched.")
            return remaining

    acquire = runtime.acquire_hosted_checkout

    async def admitted_after_resource_queue(*args):
        lease = await acquire(*args)
        clock[0] = 198  # Controlled resource wait; no wall-clock delay or SDK outcome claim.
        return lease

    runtime.acquire_hosted_checkout = admitted_after_resource_queue
    monkeypatch.setattr(module, "FreshActionRetry", ControlledRetry)
    await runtime.journey(run, people[0], lambda: False, emit)
    assert pays == ["0"] and created[:2] == [(0, 180), (198, 180)]
    assert all(timeout == 180 for _, timeout in created)
    assert module.HOSTED_CHECKOUT_QUEUE_TIMEOUT_SECONDS == 300


async def test_ambiguous_pay_failure_releases_next_lane_without_retry(tmp_path, monkeypatch):
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch, count=2, failing_lane="0")
    results = await asyncio.gather(*(runtime.journey(run, p, lambda: False, emit) for p in people), return_exceptions=True)
    assert sum(isinstance(r, RuntimeError) for r in results) == 1
    assert sorted(pays) == ["0", "1"] and maximum() == 1
    assert any(d.get("result") == "failed" for k, d, _ in events if k == "native_pay_admission")
    assert not runtime.native_pay_permits[run["id"]].locked()


async def test_cancellation_inside_pay_releases_owned_permit_without_resubmission(tmp_path, monkeypatch):
    hold = asyncio.Event()
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch, count=1, hold_pay=hold)
    task = asyncio.create_task(runtime.journey(run, people[0], lambda: False, emit))
    while not pays:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pays == ["0"] and not runtime.native_pay_permits[run["id"]].locked()
    assert any(d.get("result") == "cancelled" for k, d, _ in events if k == "native_pay_admission")


@pytest.mark.parametrize("cancel", [False, True])
async def test_permit_wait_obeys_original_deadline_and_never_keeps_a_pay_selection(tmp_path, monkeypatch, cancel):
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch, count=1)
    permit = asyncio.Lock()
    await permit.acquire()
    runtime.native_pay_permits[run["id"]] = permit
    monkeypatch.setattr(module, "FreshActionRetry", lambda: FreshActionRetry(timeout=.03))
    task = asyncio.create_task(runtime.journey(run, people[0], lambda: False, emit))
    if cancel:
        while not any(k == "native_pay_admission" for k, _, _ in events):
            await asyncio.sleep(0)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert pays == [] and not any(step == 3 for _, step, _ in reads)
    assert permit.locked()  # A canceled waiter cannot release another lane's permit.
    permit.release()


def test_admission_trace_projection_is_closed_and_recognized():
    assert "native_pay_admission" in KINDS
    policy = TracePolicy({"id": "a" * 32, "provider": "dodo"})
    result = policy.summary("native_pay_admission", {"phase": "exit", "result": "failed", "capacity": 1,
        "duration_ms": 30, "wait_ms": 10, "payment_outcome_inferred": False,
        "cooldown_ms": 20, "configured_minimum_spacing_ms": 33000,
        "spacing_basis": "conservative_experiment",
        "private_selector": "secret", "cookie": "secret"})
    assert result == {"phase": "exit", "result": "failed", "capacity": 1,
                      "duration_ms": 30, "wait_ms": 10, "payment_outcome_inferred": False,
                      "cooldown_ms": 20, "configured_minimum_spacing_ms": 33000,
                      "spacing_basis": "conservative_experiment"}


async def test_four_tasks_record_actual_dispatch_spacing_with_controlled_clock(tmp_path, monkeypatch):
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "NATIVE_PAY_START_SPACING_SECONDS", 33)
    # Advance only the admission clock after each genuine invented SDK boundary
    # completes. The asyncio/freshness clocks stay real and independent.
    clock = [0.0]
    runtime.native_pay_clock = lambda: clock[0]

    async def advance_clock(kind, data, persona):
        await emit(kind, data, persona)
        if kind == "native_pay_admission" and data["phase"] == "exit":
            clock[0] += 33

    await asyncio.gather(*(runtime.journey(run, p, lambda: False, advance_clock) for p in people))
    assert runtime.test_dispatch_times == [0, 33, 66, 99]
    assert len(pays) == 4 and maximum() == 1
    assert all(admitted for _, step, admitted in reads if step == 3)
    assert all(d["configured_minimum_spacing_ms"] == 33000
               for k, d, _ in events if k == "native_pay_admission" and d["phase"] == "enter")


@pytest.mark.parametrize("mode", ["complete", "cancel", "deadline"])
async def test_cooldown_before_fresh_observation_is_bounded_and_cancellation_releases(tmp_path, monkeypatch, mode):
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch, count=1)
    monkeypatch.setattr(module, "NATIVE_PAY_START_SPACING_SECONDS", 33)
    started = time.monotonic()
    runtime.native_pay_clock = lambda: 32.9 + time.monotonic() - started
    runtime.native_pay_started[run["id"]] = 0.0
    if mode == "deadline":
        monkeypatch.setattr(module, "FreshActionRetry", lambda: FreshActionRetry(timeout=.02))
    task = asyncio.create_task(runtime.journey(run, people[0], lambda: False, emit))
    if mode == "cancel":
        while run["id"] not in runtime.native_pay_permits or not runtime.native_pay_permits[run["id"]].locked():
            await asyncio.sleep(0)
        task.cancel()
    if mode == "complete":
        await task
        assert len(pays) == 1 and runtime.test_dispatch_times[0] >= 33
        entry = next(d for k, d, _ in events if k == "native_pay_admission" and d["phase"] == "enter")
        assert entry["cooldown_ms"] > 0 and all(admitted for _, step, admitted in reads if step == 3)
    else:
        with pytest.raises(asyncio.CancelledError if mode == "cancel" else TimeoutError):
            await task
        assert not pays and not any(step == 3 for _, step, _ in reads)
    assert not runtime.native_pay_permits[run["id"]].locked()


async def test_four_actual_tasks_share_one_continue_and_pay_dispatch_clock(tmp_path, monkeypatch):
    runtime, run, people, emit, events, reads, pays, maximum = harness(tmp_path, monkeypatch, include_billing=True)
    monkeypatch.setattr(module, "NATIVE_PAY_START_SPACING_SECONDS", 33)
    clock = [0.0]
    runtime.native_pay_clock = lambda: clock[0]

    async def advance(kind, data, persona):
        await emit(kind, data, persona)
        if kind == "native_pay_admission" and data["phase"] == "exit":
            clock[0] += 33

    await asyncio.gather(*(runtime.journey(run, p, lambda: False, advance) for p in people))
    assert [t for _, t in runtime.test_boundary_dispatches] == list(range(0, 8 * 33, 33))
    assert [k for k, _ in runtime.test_boundary_dispatches].count("billing_continue") == 4
    assert [k for k, _ in runtime.test_boundary_dispatches].count("pay") == 4
    assert len(pays) == 4 and maximum() == 1
    for lane in range(4):
        billing_reads = [admitted for p, step, admitted in reads if p == str(lane) and step == 0]
        assert billing_reads == [False, True]  # Discovery is discarded before fresh selection.
        assert all(admitted for p, step, admitted in reads if p == str(lane) and step == 4)
    exits = [d for k, d, _ in events if k == "native_pay_admission" and d["phase"] == "exit"]
    assert sum(d["boundary_kind"] == "billing_continue" and d["result"] == "billing_controls_settled" for d in exits) == 4
    assert sum(d["boundary_kind"] == "pay" and d["result"] == "owned_return_settled" for d in exits) == 4


async def test_known_replay_and_next_cohort_boundary_share_same_run_clock(tmp_path, monkeypatch):
    from tryfolk.dodo_replay import execute_replay_action
    runtime, run, people, emit, events, *_ = harness(tmp_path, monkeypatch, count=1)
    # Seed a previous normal/diagnostic actual dispatch and admit this replay
    # against the same global clock. A short remaining cooldown keeps this real
    # asyncio test fast without changing the production33s setting.
    monkeypatch.setattr(module, "NATIVE_PAY_START_SPACING_SECONDS", .025)
    runtime.native_pay_started[run["id"]] = runtime.native_pay_clock()
    action = {"method":"click", "selector":"#invented", "description":"Observed Continue", "_tryfolk":{"kind":"billing_continue"}}
    calls = []

    class ReplaySDK:
        async def request(self, command):
            calls.append(command["op"])
            if command["op"] == "observe":
                assert events[-1][1]["phase"] == "enter"
                return {"url":run["preview_url"], "actions":[action]}
            assert command["action"] == action
            return {"success":True}

    await execute_replay_action(ReplaySDK(), action, module.BrowserPolicy(run["preview_url"], "dodo"), "observe", runtime=runtime, run=run, emit=emit, persona_id=people[0]["id"])
    assert calls == ["observe", "act"]
    assert runtime.native_pay_started[run["id"]] > 0
    assert events[-1][1]["boundary_kind"] == "billing_continue"
    assert events[-1][1]["result"] == "billing_controls_settled"
    assert next(d for k, d, _ in events if d.get("phase") == "enter")["cooldown_ms"] > 0
    assert not runtime.native_pay_permits[run["id"]].locked()
