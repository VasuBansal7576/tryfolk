"""Mock browser/model I/O at observation boundary, not live SDK evidence."""
import asyncio
import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.browser_settlement import DOCUMENT_STATE
from tryfolk.browser_observation import observe_fresh


class Page:
    def __init__(self):
        self.document = {"url":"https://test.checkout.dodopayments.com/session/unit", "generation":1, "ready_state":"complete", "body_present":True, "visible_controls":2}

    async def evaluate(self, expression):
        return dict(self.document) if expression == DOCUMENT_STATE else "Test-only checkout"


async def test_document_changes_during_inference_discard_every_old_action_and_reobserve_only():
    page = Page()
    observed = []
    looked_up = []

    async def infer():
        observed.append(page.document["url"])
        if len(observed) == 1:
            page.document["url"] = "https://test.checkout.dodopayments.com/unit-payment"
            return [{"selector":"#old-country", "method":"selectOptionFromDropdown"}]
        return [{"selector":"#current-pay", "method":"click", "arguments":[], "description":"Pay now"}]

    async def control(selector):
        looked_up.append(selector)
        return {"present":True, "visible":True, "disabled":False, "tag":"BUTTON", "label":"Pay now"}

    result = await observe_fresh(page, BrowserPolicy("http://127.0.0.1:3300", "dodo"), infer, control)
    assert observed == ["https://test.checkout.dodopayments.com/session/unit", "https://test.checkout.dodopayments.com/unit-payment"]
    assert looked_up == ["#current-pay"]
    assert [a["selector"] for a in result["actions"]] == ["#current-pay"]
    assert result["freshness"]["discarded"][0]["reason"] == "document_changed_during_inference"
    assert result["actions"][0]["_tryfolk"]["kind"] == "pay"


@pytest.mark.parametrize("change_at", ["generation", "metadata", "missing"])
async def test_document_swaps_and_missing_controls_never_expose_partial_old_actions(change_at):
    page = Page()
    calls = 0

    async def infer():
        nonlocal calls
        calls += 1
        if calls == 1:
            if change_at == "generation":
                page.document["generation"] += 1
            return [{"selector":"#old-pay", "method":"click", "arguments":[], "description":"Pay now"}]
        return [{"selector":"#new-pay", "method":"click", "arguments":[], "description":"Pay now"}]

    async def control(selector):
        if selector == "#old-pay":
            if change_at == "metadata":
                page.document["generation"] += 1
            if change_at == "missing":
                return {"present":False, "reason":"missing_control"}
        return {"present":True, "visible":True, "disabled":False, "tag":"BUTTON", "label":"Pay now"}

    result = await observe_fresh(page, BrowserPolicy("http://127.0.0.1:3300", "dodo"), infer, control)
    assert calls == 2
    assert [a["selector"] for a in result["actions"]] == ["#new-pay"]
    assert len(result["freshness"]["discarded"]) == 1


async def test_unstable_control_failure_is_explicit_bounded_and_never_repeats_an_action():
    page = Page()
    calls = 0

    async def infer():
        nonlocal calls
        calls += 1
        return [{"selector":"#gone", "method":"click", "arguments":[]}]

    async def missing(selector):
        return {"present":False, "reason":"missing_control"}

    with pytest.raises(RuntimeError, match="bounded read-only") as caught:
        await observe_fresh(page, BrowserPolicy("http://127.0.0.1:3300", "dodo"), infer, missing, max_attempts=2)
    assert calls == 2
    assert caught.value.observation_evidence["status"] == "stale"
    assert [x["reason"] for x in caught.value.observation_evidence["discarded"]] == ["missing_control", "missing_control"]


async def test_inference_and_every_read_share_the_original_total_deadline():
    async def slow():
        await asyncio.sleep(1)

    async def unused(selector):
        raise AssertionError("Expired inference must not read or select a control")

    started = asyncio.get_running_loop().time()
    with pytest.raises(RuntimeError, match="deadline"):
        await observe_fresh(Page(), BrowserPolicy("http://127.0.0.1:3300", "dodo"), slow, unused, timeout=0.05)
    assert asyncio.get_running_loop().time() - started < 0.2


async def test_disabled_and_processing_buttons_are_observed_but_never_selected():
    async def infer():
        return [{"selector":"#processing", "method":"click", "arguments":[], "description":"Continue"}]

    async def control(selector):
        return {"present":True, "visible":True, "disabled":True, "tag":"BUTTON", "label":"Processing..."}

    result = await observe_fresh(Page(), BrowserPolicy("http://127.0.0.1:3300", "dodo"), infer, control)
    assert result["actions"] == []
    assert "processing" in result["unsupported_controls"][0]["reason"]
