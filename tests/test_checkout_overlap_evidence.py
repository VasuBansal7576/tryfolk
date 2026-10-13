"""Unit fixtures exercise refusal rules; these fixtures are not Dodo evidence."""
from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("checkout_overlap", Path(__file__).parents[1] / "scripts/verify_checkout_overlap.py")
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def fixture(windows):
    run = {"outcomes": {pid: {"order": {"checkout_session_id": "cks-" + pid}} for pid in windows}}
    events = []
    for pid, (start, end) in windows.items():
        for sec in (start, end):
            url = collector.HOST + "/checkout/cks-" + pid
            events.append({"id": len(events) + 1, "persona_id": pid, "kind": "observation",
                           "created_at": f"2026-10-13T00:00:{sec:02d}+00:00",
                           "data": {"url": url, "freshness": {"status": "fresh"},
                                    "document": {"url": url, "hosted_test_mode_visible": True,
                                                 "body_present": True, "ready_state": "complete"}}})
    return run, events


def test_requires_four_actual_primary_checkout_documents_to_intersect():
    windows = {"a": (0, 30), "b": (4, 31), "c": (8, 33), "d": (12, 34)}
    run, events = fixture(windows)
    result = collector.hosted_intervals(run, events, windows)
    assert result["all_four_hosted_checkouts_overlap"] is True
    assert result["all_four_hosted_intersection_seconds"] == 18
    assert result["sdk_process_overlap_or_persona_context_duration_used"] is False


@pytest.mark.parametrize("windows", [
    {"a": (0, 5), "b": (6, 10), "c": (11, 15), "d": (16, 20)},
    {"a": (0, 20), "b": (1, 20), "c": (2, 20), "d": (20, 30)},
])
def test_serialized_or_three_only_overlap_is_not_four_checkout_acceptance(windows):
    run, events = fixture(windows)
    result = collector.hosted_intervals(run, events, windows)
    assert result["all_four_hosted_checkouts_overlap"] is False
    assert result["all_four_hosted_intersection_seconds"] == 0


@pytest.mark.parametrize("change", ["wrong_checkout", "stale", "no_test_mode", "no_body", "not_ready", "foreign_document"])
def test_checkout_identity_and_fresh_document_are_required(change):
    windows = {pid: (0, 20) for pid in "abcd"}
    run, events = fixture(windows)
    data = events[0]["data"]
    if change == "wrong_checkout":
        run["outcomes"]["a"]["order"]["checkout_session_id"] = "cks-other"
    elif change == "stale":
        data["freshness"]["status"] = "stale"
    elif change == "no_test_mode":
        data["document"]["hosted_test_mode_visible"] = False
    elif change == "no_body":
        data["document"]["body_present"] = False
    elif change == "not_ready":
        data["document"]["ready_state"] = "loading"
    else:
        data["document"]["url"] = "https://foreign.example/checkout"
    with pytest.raises(ValueError):
        collector.hosted_intervals(run, events, windows)


def test_intervening_off_host_document_cannot_be_counted_as_continuous_hosted_occupancy():
    windows = {pid: (0, 20) for pid in "abcd"}
    run, events = fixture(windows)
    middle = deepcopy(events[0])
    middle["id"] = 1.5
    middle["data"]["url"] = "http://127.0.0.1:3300/checkout/return"
    events.insert(1, middle)
    with pytest.raises(ValueError, match="discontinuous"):
        collector.hosted_intervals(run, events, windows)


def test_less_than_four_actual_windows_never_establishes_four_checkout_acceptance():
    windows = {pid: (0, 20) for pid in "abc"}
    run, events = fixture(windows)
    with pytest.raises(ValueError, match="Exactly four"):
        collector.hosted_intervals(run, events, windows)
