"""Read-only retry lifecycle with external SDK/identity I/O fixtures."""

import asyncio
import json
import pytest
from tryfolk.native_read_observation import read_current_native_controls, NativeObservationGate


def state(generation=1, approved=True):
    return {
        "binding": ("private_config", "private_parent", generation),
        "checks": {
            "native_configuration_current": True,
            "native_generation_current": True,
            "independent_Test_identity_current": approved,
            "bootstrap_readiness_current": True,
        },
    }


async def test_transient_control_timeout_reobserves_with_original_deadline_and_no_action():
    reads, checks, events = [], [], []
    actions = []

    async def recheck():
        checks.append(True)
        return state()

    async def read():
        reads.append(True)
        if len(reads) == 1:
            error = TimeoutError("private URL and exception must not be retained")
            error.control_observation = {"operation": "stagehand_input_value", "elapsed_ms": 2000}
            raise error
        return ["fresh candidate"]

    deadline = asyncio.get_running_loop().time() + 1
    result = await read_current_native_controls(read, recheck, deadline=deadline, on_event=events.append)
    assert result == ["fresh candidate"] and len(reads) == 2 and len(checks) == 3
    assert actions == []
    assert (
        events[0]["operation"] == "stagehand_input_value" and events[0]["decision"] == "discard_read_timeout"
    )
    assert "private" not in json.dumps(events)


async def test_changed_generation_discards_candidates_before_returning_a_fresh_population():
    states = iter([state(1), state(2), state(2), state(2)])
    reads = []
    events = []

    async def recheck():
        return next(states)

    async def read():
        reads.append(True)
        return ["stale"] if len(reads) == 1 else ["fresh"]

    result = await read_current_native_controls(
        read, recheck, deadline=asyncio.get_running_loop().time() + 1, on_event=events.append
    )
    assert result == ["fresh"] and len(reads) == 2
    assert events == [{"decision": "discard_changed_native_state", "attempt": 1}]


async def test_wrong_identity_blocks_reads_and_cancellation_never_retries():
    reads = []

    async def wrong():
        return state(approved=False)

    async def cancelled():
        reads.append(True)
        raise asyncio.CancelledError()

    with pytest.raises(NativeObservationGate, match="identity_or_generation"):
        await read_current_native_controls(
            cancelled, wrong, deadline=asyncio.get_running_loop().time() + 1, on_event=lambda event: None
        )
    assert reads == []

    async def current():
        return state()

    with pytest.raises(asyncio.CancelledError):
        await read_current_native_controls(
            cancelled, current, deadline=asyncio.get_running_loop().time() + 1, on_event=lambda event: None
        )
    assert reads == [True]


async def test_read_timeout_never_extends_original_deadline_or_restarts_used_actions():
    reads = []
    events = []

    async def current():
        return state()

    async def slow():
        reads.append(True)
        await asyncio.sleep(1)

    started = asyncio.get_running_loop().time()
    with pytest.raises(NativeObservationGate, match="original_deadline"):
        await read_current_native_controls(slow, current, deadline=started + 0.02, on_event=events.append)
    assert len(reads) == 1 and asyncio.get_running_loop().time() - started < 0.15
    assert not events[0]["original_deadline_available"]


async def test_repeated_timeout_is_bounded_and_operation_timings_contain_no_private_values():
    from tryfolk.native_read_observation import NativeControlReads

    reads = []
    events = []

    async def current():
        return state()

    async def fail():
        reads.append(True)
        trace = NativeControlReads(events.append)

        async def external_timeout():
            raise TimeoutError("invented-private-selector-and-URL")

        try:
            await trace.read("stagehand_snapshot", external_timeout)
        except TimeoutError as error:
            error.control_observation = trace.timeout_evidence()
            raise

    with pytest.raises(NativeObservationGate, match="bounded_read_attempts"):
        await read_current_native_controls(
            fail, current, deadline=asyncio.get_running_loop().time() + 1, on_event=events.append
        )
    assert len(reads) == 2 and "invented" not in json.dumps(events)
    assert events[-1]["operation"] == "stagehand_snapshot"
    assert isinstance(events[-1]["elapsed_ms"], int)


async def test_missing_native_generation_check_is_a_gate_not_an_empty_success():
    async def partial():
        return {"binding": ("private", 1), "checks": {"independent_Test_identity_current": True}}

    async def forbidden():
        raise AssertionError("Incomplete native readiness must not expose a read")

    with pytest.raises(NativeObservationGate, match="identity_or_generation"):
        await read_current_native_controls(
            forbidden, partial, deadline=asyncio.get_running_loop().time() + 1, on_event=lambda event: None
        )


async def test_unrecognized_timeout_is_never_retried():
    reads = []

    async def current():
        return state()

    async def unknown():
        reads.append(True)
        raise TimeoutError("a noncandidate callback failure")

    with pytest.raises(NativeObservationGate, match="unrecognized_discovery_timeout"):
        await read_current_native_controls(
            unknown, current, deadline=asyncio.get_running_loop().time() + 1, on_event=lambda event: None
        )
    assert len(reads) == 1


async def test_generation_change_after_executed_card_stops_before_any_read_retry():
    states = iter([state(1), state(2)])
    reads = []

    async def current():
        return next(states)

    async def read():
        reads.append(True)
        return ["discarded candidate"]

    with pytest.raises(NativeObservationGate, match="native_changed_after_executed_card"):
        await read_current_native_controls(
            read,
            current,
            deadline=asyncio.get_running_loop().time() + 1,
            on_event=lambda event: None,
            stop_on_change=True,
        )
    assert len(reads) == 1


@pytest.mark.parametrize("missing", [False, True])
async def test_failed_read_cannot_hide_generation_change_after_executed_card(missing):
    from tryfolk.native_read_observation import NativeControlMissing

    states, reads, events = iter([state(1), state(2)]), [], []

    async def current():
        return next(states)

    async def failed_read():
        reads.append(True)
        error = NativeControlMissing("private missing candidate") if missing else TimeoutError("private timeout")
        error.control_observation = {"operation": "stagehand_visibility"}
        raise error

    with pytest.raises(NativeObservationGate, match="native_changed_after_executed_card"):
        await read_current_native_controls(failed_read, current,
            deadline=asyncio.get_running_loop().time()+1, on_event=events.append, stop_on_change=True)
    assert len(reads) == 1 and "private" not in json.dumps(events)
