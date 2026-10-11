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

    def remaining():
        value = deadline - asyncio.get_running_loop().time()
        if value <= 0:
            raise RuntimeError(
                "Observed owned navigation or ready controls did not settle; ambiguous actions are not retried."
            )
        return value

    async def read(operation):
        budget = remaining()
        try:
            return await asyncio.wait_for(operation(), budget)
        except TimeoutError as error:
            raise RuntimeError(
                "Observed owned navigation or ready controls did not settle; ambiguous actions are not retried."
            ) from error

    while True:
        url = await read(page.url)
        policy.check_url(url)
        arrived = (
            exact_origin(url) == DODO_CHECKOUT
            if kind == "purchase" and policy.provider == "dodo"
            else url.split("?", 1)[0].split("#", 1)[0] == expected
            if expected
            else True
        )
        if arrived:
            document = await read(lambda: page.evaluate(DOCUMENT_STATE))
            policy.check_url(document["url"])
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
                snapshot = await read(lambda: page.snapshot(include_iframes=True, timeout=remaining() * 1000))
                tree = snapshot.formatted_tree
                current = await read(page.url)
                policy.check_url(current)
                after = await read(lambda: page.evaluate(DOCUMENT_STATE))
                policy.check_url(after["url"])
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
        await asyncio.sleep(min(0.05, remaining()))
