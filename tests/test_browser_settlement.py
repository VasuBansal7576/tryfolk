"""Unit boundary inputs with mocked browser I/O; not live SDK/payment evidence."""

from types import SimpleNamespace
import asyncio
import json
import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.browser_settlement import settle


class Page:
    def __init__(self, urls, *, ready="complete", controls=1, tree="[1] button: Test-only control"):
        self.urls = iter(urls)
        self.current = None
        self.waits = []
        self.ready, self.controls, self.tree = ready, controls, tree

    async def url(self):
        self.current = next(self.urls, self.current)
        return self.current

    async def wait_for_load_state(self, state, timeout):
        self.waits.append(state)
        raise TimeoutError("Test-only vendor main-world lifecycle timeout on an already-loaded document")

    async def evaluate(self, expression):
        return {
            "url": self.current,
            "ready_state": self.ready,
            "visible_controls": self.controls,
            "body_present": True,
            "generation": 1,
        }

    async def snapshot(self, include_iframes, timeout=None):
        assert include_iframes
        return SimpleNamespace(formatted_tree=self.tree)


async def test_field_write_finishes_with_current_document_even_when_hosted_network_never_idles():
    page = Page(["https://test.checkout.dodopayments.com/session/test-only"])
    result = await settle(
        page,
        BrowserPolicy("http://127.0.0.1:3300", "dodo"),
        {"method": "fill", "_tryfolk": {"field": "address"}},
        timeout=0.2,
    )
    assert result["url"] == "https://test.checkout.dodopayments.com/session/test-only"
    assert result["readiness_source"] == "stagehand_current_document_and_snapshot"
    assert page.waits == []


@pytest.mark.parametrize("condition", ["unchanged", "busy", "no_controls", "loading"])
async def test_continue_deadline_retains_closed_observed_state_without_claiming_click_failure(condition):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = "https://test.checkout.dodopayments.com/session/private-fixture"
    previous = {"url": url, "generation": 1, "control_signature": "private billing fixture",
                "error_signature": "[]"}
    state = previous | {"ready_state": "complete", "body_present": True,
                        "visible_controls": 3, "busy": False, "error_signature": "[]"}
    if condition != "unchanged":
        state["control_signature"] = "private changed fixture"
    if condition == "busy":
        state.update(busy=True, error_signature='["private validation fixture"]')
    elif condition == "no_controls":
        state["visible_controls"] = 0
    elif condition == "loading":
        state["ready_state"] = "loading"
    page = Page([url])

    async def evaluate(_):
        return dict(state)

    page.evaluate = evaluate
    with pytest.raises(RuntimeError) as caught:
        await settle(page, policy, {"method": "click", "_tryfolk": {"kind": "billing_continue"}},
                     timeout=0.01, previous_document=previous)
    diagnostic = getattr(caught.value, "settlement_diagnostics", None)
    assert diagnostic is not None, "The actual settlement deadline must retain its finite boundary"
    assert diagnostic["reason"] == "deadline_exhausted"
    assert diagnostic["stage"] == "current_document_settlement"
    assert diagnostic["first_failed_predicate"] == "settlement_completed_within_bound"
    assert diagnostic["checks"]["document_observed"] is True
    assert diagnostic["checks"]["same_previous_document"] is True
    assert diagnostic["checks"]["transition_changed"] is (condition != "unchanged")
    assert diagnostic["checks"]["document_busy"] is (condition == "busy")
    assert diagnostic["checks"]["visible_error_observed"] is (condition == "busy")
    assert diagnostic["checks"]["visible_controls_present"] is (condition != "no_controls")
    assert diagnostic["checks"]["document_ready"] is (condition != "loading")
    assert all(type(value) is bool for value in diagnostic["checks"].values())
    assert "private" not in json.dumps(diagnostic) and url not in str(caught.value)


@pytest.mark.parametrize("blocked_read", ["page_url", "page_snapshot", "final_page_url"])
async def test_settlement_read_timeout_retains_exact_operation_and_prior_observations(blocked_read):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    page = Page([policy.origin + "/special-downloads"])
    original_url, original_snapshot = page.url, page.snapshot
    calls = 0

    async def url():
        nonlocal calls
        calls += 1
        if (blocked_read == "page_url" and calls == 1) or (blocked_read == "final_page_url" and calls == 2):
            await asyncio.sleep(1)
        return await original_url()

    async def snapshot(include_iframes, timeout=None):
        if blocked_read == "page_snapshot":
            await asyncio.sleep(1)
        return await original_snapshot(include_iframes, timeout)

    page.url, page.snapshot = url, snapshot
    with pytest.raises(RuntimeError) as caught:
        await settle(page, policy, {"method": "click"}, timeout=0.01)
    diagnostic = getattr(caught.value, "settlement_diagnostics", None)
    assert diagnostic is not None
    assert diagnostic["reason"] == "observation_timed_out"
    assert diagnostic["operation"] == blocked_read
    assert diagnostic["checks"]["document_observed"] is (blocked_read != "page_url")
    assert diagnostic["checks"]["snapshot_observed"] is (blocked_read == "final_page_url")
    assert isinstance(caught.value.__cause__, TimeoutError)


async def test_settlement_preserves_original_non_timeout_sdk_exception_and_cancellation():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    page = Page([policy.origin + "/special-downloads"])
    original_error = RuntimeError("private external SDK fixture")

    async def failure():
        raise original_error

    page.url = failure
    with pytest.raises(RuntimeError) as caught:
        await settle(page, policy, {"method": "click"}, timeout=0.05)
    assert caught.value is original_error
    assert not hasattr(caught.value, "settlement_diagnostics")

    async def cancel():
        raise asyncio.CancelledError

    page.url = cancel
    with pytest.raises(asyncio.CancelledError):
        await settle(page, policy, {"method": "click"}, timeout=0.05)


async def test_settlement_success_keeps_stable_validation_state_and_original_read_sequence():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    page = Page([policy.origin + "/special-downloads"])
    calls = []
    original_url, original_evaluate, original_snapshot = page.url, page.evaluate, page.snapshot

    async def url():
        calls.append("url")
        return await original_url()

    async def evaluate(expression):
        calls.append("document")
        return await original_evaluate(expression) | {"error_signature": '["private validation fixture"]'}

    async def snapshot(include_iframes, timeout=None):
        calls.append("snapshot")
        return await original_snapshot(include_iframes, timeout)

    page.url, page.evaluate, page.snapshot = url, evaluate, snapshot
    result = await settle(page, policy, {"method": "click", "_tryfolk": {"kind": "confirm"}},
                          timeout=0.1, previous_document={"control_signature": "previous"})
    assert result["transition_state"] == "error"
    assert calls == ["url", "document", "snapshot", "url", "document"]


async def test_stable_visible_transition_error_is_reported_and_old_idle_form_is_not_ready():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = "https://test.checkout.dodopayments.com/session/test-only"
    page = Page([url])
    state = {
        "url": url,
        "generation": 1,
        "ready_state": "complete",
        "body_present": True,
        "visible_controls": 3,
        "busy": False,
        "control_signature": "billing form",
        "error_signature": "[]",
    }

    async def evaluate(expression):
        return dict(state)

    page.evaluate = evaluate
    previous = dict(state)
    action = {"method": "click", "_tryfolk": {"kind": "billing_continue", "url": url}}
    with pytest.raises(RuntimeError, match="ready"):
        await settle(page, policy, action, timeout=0.05, previous_document=previous)
    state["error_signature"] = '["Checkout transition fixture error"]'
    result = await settle(page, policy, action, timeout=0.2, previous_document=previous)
    assert result["transition_state"] == "error"


async def test_purchase_waits_for_its_actual_hosted_navigation_and_rejects_external_redirects():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    action = {"method": "click", "_tryfolk": {"kind": "purchase"}}
    page = Page(
        [policy.origin + "/special-downloads", "https://test.checkout.dodopayments.com/session/test-only"]
    )
    assert (await settle(page, policy, action, timeout=0.2))[
        "url"
    ] == "https://test.checkout.dodopayments.com/session/test-only"
    with pytest.raises(ValueError, match="approved"):
        await settle(
            Page(["https://checkout.dodopayments.com/session/test-only"]), policy, action, timeout=0.2
        )
    with pytest.raises(RuntimeError, match="navigation"):
        await settle(Page([policy.origin + "/special-downloads"]), policy, action, timeout=0.05)


@pytest.mark.parametrize("changes", [{"ready": "loading"}, {"controls": 0}, {"tree": ""}])
async def test_arrived_url_without_ready_actionable_current_document_does_not_pass(changes):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    with pytest.raises(RuntimeError, match="ready"):
        await settle(
            Page([policy.origin + "/special-downloads"], **changes), policy, {"method": "click"}, timeout=0.05
        )


async def test_pay_cannot_finish_against_the_old_hosted_document_or_unapproved_return():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    action = {"method": "click", "_tryfolk": {"kind": "pay"}}
    with pytest.raises(RuntimeError, match="navigation"):
        await settle(
            Page(["https://test.checkout.dodopayments.com/session/test-only"]), policy, action, timeout=0.05
        )
    page = Page(
        ["https://test.checkout.dodopayments.com/session/test-only", policy.origin + "/checkout/return"]
    )
    assert (await settle(page, policy, action, timeout=0.2))["url"] == policy.origin + "/checkout/return"


@pytest.mark.parametrize("blocked_read", ["initial_url", "final_url", "snapshot"])
async def test_every_read_and_sdk_snapshot_share_one_total_deadline(blocked_read):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    page = Page([policy.origin + "/special-downloads"])
    original_url, original_snapshot = page.url, page.snapshot
    calls = 0

    async def url():
        nonlocal calls
        calls += 1
        if (blocked_read == "initial_url" and calls == 1) or (blocked_read == "final_url" and calls == 2):
            await asyncio.sleep(1)
        return await original_url()

    async def snapshot(include_iframes, timeout=None):
        assert timeout is not None and 0 < timeout <= 50
        if blocked_read == "snapshot":
            await asyncio.sleep(1)
        return await original_snapshot(include_iframes, timeout)

    page.url, page.snapshot = url, snapshot
    started = asyncio.get_running_loop().time()
    with pytest.raises(RuntimeError, match="ready"):
        await settle(page, policy, {"method": "click"}, timeout=0.05)
    assert asyncio.get_running_loop().time() - started < 0.2


async def test_same_url_document_swap_during_snapshot_is_not_accepted():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    page = Page([policy.origin + "/special-downloads"])
    original = page.evaluate
    generation = 0

    async def evaluate(expression):
        nonlocal generation
        generation += 1
        return await original(expression) | {"generation": generation}

    page.evaluate = evaluate
    with pytest.raises(RuntimeError, match="ready"):
        await settle(page, policy, {"method": "click"}, timeout=0.05)


@pytest.mark.parametrize("delay", [0.08, None])
async def test_acknowledged_continue_waits_for_stable_next_form_and_preserves_busy_timeout(delay):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = "https://test.checkout.dodopayments.com/session/test-only"
    page = Page([url])
    started = asyncio.get_running_loop().time()

    async def evaluate(expression):
        elapsed = asyncio.get_running_loop().time() - started
        busy = delay is None or elapsed < delay
        return {
            "url": url,
            "generation": 1,
            "ready_state": "complete",
            "body_present": True,
            "visible_controls": 3,
            "busy": busy,
            "control_signature": "processing" if busy else "card form",
        }

    page.evaluate = evaluate
    action = {"method": "click", "_tryfolk": {"kind": "billing_continue", "url": url}}
    previous = {"url": url, "generation": 1, "control_signature": "billing form"}
    if delay is None:
        with pytest.raises(RuntimeError, match="ready"):
            await settle(page, policy, action, timeout=0.12, previous_document=previous)
        assert asyncio.get_running_loop().time() - started < 0.3
    else:
        result = await settle(page, policy, action, timeout=0.3, previous_document=previous)
        assert not result["busy"] and result["control_signature"] == "card form"
        assert asyncio.get_running_loop().time() - started >= delay
    assert page.waits == []
