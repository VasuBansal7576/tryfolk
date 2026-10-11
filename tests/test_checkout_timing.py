"""Invented browser-event I/O; no provider or SDK readiness claim."""

from types import SimpleNamespace
import json
from tryfolk.checkout_timing import CheckoutTiming


def test_first_party_timing_records_lifecycle_without_private_request_or_response_content():
    page = object()
    audit = CheckoutTiming(page)
    request = SimpleNamespace(
        url="https://test.checkout.dodopayments.com/api/checkout/session/fixture?clientSecret=private-fixture",
        method="POST",
        frame=SimpleNamespace(
            page=page, url="https://test.checkout.dodopayments.com/session/fixture?publishableKey=private-key"
        ),
    )

    # Real Playwright request objects are hashable; this external event fixture is too.
    class Request:
        def __getattr__(self, name):
            return getattr(request, name)

        @property
        def headers(self):
            raise AssertionError("Request headers are private")

        @property
        def post_data(self):
            raise AssertionError("Request body is private")

    observed = Request()
    audit.started(observed)
    audit.response(SimpleNamespace(request=observed, status=200))
    audit.finished(observed)
    events = audit.snapshot()
    assert len(events) == 1 and events[0]["method"] == "POST" and events[0]["status"] == 200
    assert events[0]["completion"] == "finished" and events[0]["elapsed_ms"] >= 0
    assert events[0]["path"] == "/api/checkout/session/fixture"
    assert events[0]["initiator"] == "https://test.checkout.dodopayments.com/session/fixture"
    assert "private-fixture" not in json.dumps(events) and "private-key" not in json.dumps(events)
    observed.url = "https://unknown.example/api"
    audit.started(observed)
    assert len(audit.snapshot()) == 1


def test_first_party_failure_and_incomplete_request_remain_distinct_and_private():
    page = object()
    audit = CheckoutTiming(page)

    class Request:
        method = "POST"
        url = "https://test.checkout.dodopayments.com/api/checkout/fixture?secret=private-fixture"
        frame = SimpleNamespace(page=page, url="https://test.checkout.dodopayments.com/session/fixture")
        failure = "net::ERR_BLOCKED_BY_CLIENT.Inspector"

    request = Request()
    audit.started(request)
    assert audit.snapshot()[0]["completion"] == "pending" and audit.snapshot()[0]["status"] is None
    audit.failed(request)
    failed = audit.snapshot()[0]
    assert failed["completion"] == "failed" and failed["failure"] == "net::ERR_BLOCKED_BY_CLIENT.Inspector"
    assert "private-fixture" not in json.dumps(failed)
