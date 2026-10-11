"""Bound owned navigation without requiring third-party requests to become idle."""

import asyncio
import hashlib
from .browser_policy import DODO_CHECKOUT, exact_origin

DOCUMENT_STATE = r"""(() => {const controls=[...document.querySelectorAll('button,input:not([type=hidden]),select,textarea,a[href]')]
 .filter(el=>el.getClientRects().length && getComputedStyle(el).visibility!=='hidden');return ({
 url:location.href,generation:performance.timeOrigin,ready_state:document.readyState,body_present:!!document.body,
 hosted_test_mode_visible:[...document.querySelectorAll('span,div')].some(el=>
  el.getClientRects().length && getComputedStyle(el).visibility!=='hidden' && el.innerText.trim()==='Test Mode'),
 visible_controls:controls.length,
 busy:controls.some(el=>el.getAttribute('aria-busy')==='true' ||
  (el.tagName==='BUTTON' && (el.disabled || el.getAttribute('aria-disabled')==='true') && /^(processing|loading|please wait)\b/i.test(el.innerText.trim()))),
 error_signature:JSON.stringify([...document.querySelectorAll('[role=alert],[aria-live=assertive]')]
  .filter(el=>el.getClientRects().length && getComputedStyle(el).visibility!=='hidden' && el.innerText.trim())
  .map(el=>el.innerText.trim().slice(0,1000))),
 control_signature:JSON.stringify(controls.map(el=>[el.tagName,el.type,el.id,el.name,el.autocomplete,
  el.getAttribute('aria-label'),el.placeholder,!!el.disabled,el.getAttribute('aria-disabled'),
  el.tagName==='BUTTON'?el.innerText:null]))
})})()"""


class SettlementDeadline(RuntimeError):
    """Closed facts from existing reads, without guessing whether the click failed."""

    def __init__(self, reason, operation, checks):
        self.checks = dict(checks)
        self.first_failed_predicate = "settlement_completed_within_bound"
        self.settlement_diagnostics = {
            "stage": "current_document_settlement",
            "reason": reason,
            "operation": operation,
            "first_failed_predicate": self.first_failed_predicate,
            "checks": self.checks,
        }
        super().__init__(
            "Observed owned navigation or ready controls did not settle; ambiguous actions are not retried."
        )


async def settle(page, policy, action, *, timeout=10, previous_document=None):
    metadata = action.get("_tryfolk", {})
    kind = metadata.get("kind") if action.get("method") == "click" else None
    transition = kind in ("login", "purchase", "pay", "art_pack", "confirm", "billing_continue")
    expected = {
        "login": policy.origin + "/special-downloads",
        "art_pack": policy.origin + "/special-downloads",
        "pay": policy.origin + "/checkout/return",
    }.get(kind)
    deadline = asyncio.get_running_loop().time() + timeout
    operation = "settlement_start"
    checks = dict.fromkeys((
        "destination_arrived", "document_observed", "document_URL_current", "document_ready",
        "body_present", "visible_controls_present", "document_busy", "visible_error_observed",
        "same_previous_document", "transition_changed", "snapshot_observed", "snapshot_nonempty",
        "post_snapshot_document_observed", "post_snapshot_document_stable",
        "post_snapshot_controls_stable", "settlement_completed_within_bound",
    ), False)
    checks["previous_document_available"] = bool(previous_document)

    def remaining():
        value = deadline - asyncio.get_running_loop().time()
        if value <= 0:
            raise SettlementDeadline("deadline_exhausted", operation, checks)
        return value

    async def read(name, reader):
        nonlocal operation
        operation = name
        budget = remaining()
        try:
            return await asyncio.wait_for(reader(), budget)
        except TimeoutError as error:
            raise SettlementDeadline("observation_timed_out", operation, checks) from error

    while True:
        url = await read("page_url", page.url)
        policy.check_url(url)
        arrived = (
            exact_origin(url) == DODO_CHECKOUT
            if kind == "purchase" and policy.provider == "dodo"
            else url.split("?", 1)[0].split("#", 1)[0] == expected
            if expected
            else True
        )
        checks["destination_arrived"] = arrived
        if arrived:
            document = await read("document_state", lambda: page.evaluate(DOCUMENT_STATE))
            policy.check_url(document["url"])
            checks.update(
                document_observed=True,
                document_URL_current=document["url"] == url,
                document_ready=document.get("ready_state") in ("interactive", "complete"),
                body_present=bool(document.get("body_present")),
                visible_controls_present=document.get("visible_controls", 0) > 0,
                document_busy=bool(document.get("busy")),
                visible_error_observed=document.get("error_signature") not in (None, "[]"),
                same_previous_document=bool(previous_document) and all(
                    document.get(key) == previous_document.get(key) for key in ("url", "generation")
                ),
                transition_changed=not previous_document or any(
                    document.get(key) != previous_document.get(key)
                    for key in ("url", "generation", "control_signature", "error_signature")
                ),
            )
            # Do not let an in-progress document swap satisfy the previous URL.
            if (
                document["url"] == url
                and document.get("body_present")
                and document.get("ready_state") in ("interactive", "complete")
                and document.get("visible_controls", 0) > 0
                and (not transition or not document.get("busy"))
                and (
                    not transition
                    or not previous_document
                    or any(
                        document.get(key) != previous_document.get(key)
                        for key in ("url", "generation", "control_signature", "error_signature")
                    )
                )
            ):
                snapshot = await read(
                    "page_snapshot", lambda: page.snapshot(include_iframes=True, timeout=remaining() * 1000)
                )
                tree = snapshot.formatted_tree
                checks.update(snapshot_observed=True, snapshot_nonempty=bool(tree.strip()))
                current = await read("final_page_url", page.url)
                policy.check_url(current)
                after = await read("final_document_state", lambda: page.evaluate(DOCUMENT_STATE))
                policy.check_url(after["url"])
                checks.update(
                    post_snapshot_document_observed=True,
                    post_snapshot_document_stable=current == url == after["url"]
                    and document.get("generation") == after.get("generation"),
                    post_snapshot_controls_stable=not after.get("busy")
                    and document.get("control_signature") == after.get("control_signature")
                    and document.get("error_signature") == after.get("error_signature"),
                )
                if (
                    tree.strip()
                    and current == url == after["url"]
                    and document.get("generation") == after.get("generation")
                    and after.get("ready_state") in ("interactive", "complete")
                    and after.get("body_present")
                    and after.get("visible_controls", 0) > 0
                    and (
                        not transition
                        or (
                            not after.get("busy")
                            and document.get("control_signature") == after.get("control_signature")
                            and document.get("error_signature") == after.get("error_signature")
                        )
                    )
                ):
                    return {
                        **document,
                        "readiness_source": "stagehand_current_document_and_snapshot",
                        "snapshot_sha256": hashlib.sha256(tree.encode()).hexdigest(),
                        "transition_state": "error"
                        if transition and document.get("error_signature") not in (None, "[]")
                        else "ready",
                    }
        operation = "settlement_pause"
        await asyncio.sleep(min(0.05, remaining()))
