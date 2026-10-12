"""Invented browser I/O tests the actual bounded manual observation interface."""

import asyncio
import json
import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.manual_address import ManualAddressGate, read_current_manual_controls

URL = "https://test.checkout.dodopayments.com/session/invented"
DOCUMENT = {"url": URL, "generation": 1, "ready_state": "complete",
            "body_present": True, "busy": False, "control_signature": "closed"}
POLICY = BrowserPolicy("http://127.0.0.1:3300", "dodo")


async def observe(documents):
    iterator = iter(documents)
    reads, candidates, events = [], [], []

    async def read_document():
        reads.append("document")
        return next(iterator)

    async def read_controls(document):
        candidate = object()
        candidates.append(candidate)
        reads.append("controls")
        return candidate

    try:
        result = await read_current_manual_controls(
            read_document, read_controls, POLICY, original_document=DOCUMENT,
            deadline=asyncio.get_running_loop().time() + 1, on_event=events.append,
        )
        return result, reads, candidates, events
    except ManualAddressGate as error:
        error.reads, error.candidates, error.events = reads, candidates, events
        raise


async def test_same_original_idle_document_rerender_discards_intermediate_controls():
    opened = DOCUMENT | {"control_signature": "open"}
    result, reads, candidates, events = await observe([DOCUMENT, opened, opened, opened])
    assert result[0] is candidates[1] and result[0] is not candidates[0]
    assert result[1]["document_stable"] is True
    assert reads == ["document", "controls", "document"] * 2
    assert events[0]["decision"] == "discard_manual_control_rerender"
    assert len(candidates) == 2
    assert all(secret not in json.dumps(events) for secret in (URL, "closed", "open"))


@pytest.mark.parametrize("change,reason", [
    ({"generation": 2}, "manual_document_changed"),
    ({"generation": float("nan")}, "manual_document_unstable"),
    ({"url": URL + "-changed"}, "manual_document_changed"),
    ({"ready_state": "loading"}, "manual_document_unstable"),
    ({"body_present": False}, "manual_document_unstable"),
    ({"busy": True}, "manual_document_unstable"),
])
@pytest.mark.parametrize("side", ["before", "after"])
async def test_unsafe_binding_never_receives_a_second_control_pass(change, reason, side):
    changed = DOCUMENT | change | {"control_signature": "rerendered"}
    documents = [changed] if side == "before" else [DOCUMENT, changed]
    with pytest.raises(ManualAddressGate) as stopped:
        await observe(documents)
    assert stopped.value.reason == reason
    assert len(stopped.value.candidates) == (0 if side == "before" else 1)
    assert not any(e["decision"] == "discard_manual_control_rerender" for e in stopped.value.events)


async def test_a_second_control_rerender_stops_without_a_third_pass():
    with pytest.raises(ManualAddressGate) as stopped:
        await observe([DOCUMENT, DOCUMENT | {"control_signature": "first"},
                       DOCUMENT | {"control_signature": "first"},
                       DOCUMENT | {"control_signature": "second"}])
    assert stopped.value.reason == "manual_document_unstable"
    assert len(stopped.value.candidates) == 2


async def test_slow_read_cannot_extend_the_original_deadline():
    cancelled = []

    async def read_document():
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    async def forbidden_controls(_):
        raise AssertionError("No snapshot after the deadline")

    with pytest.raises(ManualAddressGate, match="original_deadline_exhausted"):
        await read_current_manual_controls(
            read_document, forbidden_controls, POLICY, original_document=DOCUMENT,
            deadline=asyncio.get_running_loop().time() + .01,
        )
    assert cancelled == [True]


async def test_expired_original_deadline_performs_no_reads():
    async def forbidden(*_):
        raise AssertionError("Expired budget must not read")

    with pytest.raises(ManualAddressGate, match="original_deadline_exhausted"):
        await read_current_manual_controls(
            forbidden, forbidden, POLICY, original_document=DOCUMENT,
            deadline=asyncio.get_running_loop().time() - 1,
        )


@pytest.mark.parametrize("signature", [None, "", 42])
async def test_absent_control_signature_cannot_establish_a_stable_pass(signature):
    with pytest.raises(ManualAddressGate, match="manual_document_unstable") as stopped:
        await observe([DOCUMENT | {"control_signature": signature}])
    assert stopped.value.candidates == []
