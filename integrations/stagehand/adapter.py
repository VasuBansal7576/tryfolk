"""Actual Stagehand local browser with Codex callback; JSONL process protocol."""

import asyncio
import json
import sys
import os
import hashlib
import tempfile
import traceback
import re
from uuid import uuid4
from pathlib import Path
from contextlib import AsyncExitStack
from urllib.parse import urlsplit
from stagehand import (
    LLMStructuredGenerateParams,
    LLMStructuredGenerateResult,
    LLMRole,
    LLMTextContent,
)
from local_session import open_routed_browser
from tryfolk.codex_bridge import agenerate
from tryfolk.browser_policy import FIELD_METHODS, DODO_CHECKOUT, DODO_SECURE_FRAME, exact_origin
from tryfolk.browser_network import NetworkPolicy
from tryfolk.browser_settlement import DOCUMENT_STATE, settle
from tryfolk.browser_observation import observe_fresh, same_document
from tryfolk.observed_controls import frame_control
from tryfolk.secure_frame import redact_frame_config
from tryfolk.checkout_timing import CheckoutTiming
from tryfolk.browser_events import EventCapture
from tryfolk.payment_privacy import redact_payment
from tryfolk.native_read_observation import NativeControlReads
from tryfolk.checkout_discovery import discover_checkout_candidates, include_snapshot_manual_choices
from tryfolk.manual_address import ManualAddressGate, read_current_manual_controls, resolve_manual_address, wait_for_billing_initialization
from tryfolk.pre_submission import adapter_safety


class PreActDocumentDrift(RuntimeError):
    """The SDK act was never entered; only closed comparison facts leave here."""

    def __init__(self, comparison, *, retryable=False):
        super().__init__(
            "Document changed after observation; no browser action was executed or retried."
        )
        self.code = "nonfinancial_control_stale" if retryable else None
        self.data = comparison


def private_evidence(label, data):
    root = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"stagehand-{label}-{uuid4().hex}.json"
    with open(path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        json.dump(redact_payment(data), handle, indent=2, default=str)
    return str(path)


CONTROL_STATE = """(selector) => {
 const el = selector.startsWith('xpath=') ? document.evaluate(selector.slice(6), document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue : document.querySelector(selector);
 if (!el) return {present:false,reason:'missing_control'};
 const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
 const centerHit=r.width>0&&r.height>0&&!!hit&&(hit===el||el.contains(hit));
 return {present:true,center_hit_self:centerHit,popup_ancestor:!!el.closest('[data-radix-popper-content-wrapper],[role=dialog],[role=menu],[role=listbox]'),visible:!!el.getClientRects().length && getComputedStyle(el).visibility!=='hidden',disabled:!!el.disabled || el.getAttribute('aria-disabled')==='true',tag:el.tagName,type:el.type,autocomplete:el.autocomplete,role:el.getAttribute('role'),aria_autocomplete:el.getAttribute('aria-autocomplete'),label:[...(el.labels || [])].map(x=>x.innerText).join(' ') || el.innerText || el.getAttribute('aria-label'),in_main:!!el.closest('main'),placeholder:el.placeholder,name:el.name,id:el.id,value:el.value,required:el.required,options:el.options ? [...el.options].map(x=>x.value):[],download:el.download,destination:el.href || el.formAction || el.form?.action || location.href,frame_url:location.href,frame_generation:performance.timeOrigin};
}"""


async def control_state(page, selector, network_page=None, guard=None, *, diagnostic=None):
    trace = NativeControlReads(diagnostic)
    try:
        return await _control_state_read(page, selector, network_page, guard, trace)
    except TimeoutError as error:
        evidence = trace.timeout_evidence()
        if diagnostic:
            diagnostic(evidence)
        error.control_observation = evidence
        raise


async def read_manual_current(
    page, network_page, guard, *, completed_fields=frozenset(),
    original_document=None, deadline=None, on_event=None,
):
    """No inference/action; freshly resolve native controls and current generation."""
    async def read_controls(before):
        snapshot = (await page.snapshot(include_iframes=True, timeout=10000)).model_dump(mode="json")
        return await discover_checkout_candidates(
            snapshot, lambda selector: control_state(page, selector, network_page, guard),
            guard.policy, before["url"], filled=completed_fields, completed_cards={}, clicked=set(),
            executed_fields=completed_fields, phase="primary_billing",
        )

    if original_document is not None:
        if deadline is None:
            raise ValueError("Manual observation requires the original bounded deadline")
        return await read_current_manual_controls(
            lambda: page.evaluate(DOCUMENT_STATE), read_controls, guard.policy,
            original_document=original_document, deadline=deadline, on_event=on_event,
        )
    before = await page.evaluate(DOCUMENT_STATE)
    discovery = await read_controls(before)
    after = await page.evaluate(DOCUMENT_STATE)
    return discovery, after | {"document_stable": same_document(before, after)}


async def _control_state_read(page, selector, network_page, guard, trace):
    if "/iframe[" in selector:
        async with asyncio.timeout(10):
            snapshot = (await trace.read("stagehand_snapshot", lambda: page.snapshot(include_iframes=True, timeout=10000))).model_dump(mode="json")
            try:
                observed = frame_control(selector, snapshot)
            except ValueError as error:
                return {"present": False, "reason": "missing_frame_control", "detail": str(error)}
            if (
                not network_page
                or not guard
                or exact_origin(await trace.read("stagehand_page_url", page.url)) != DODO_CHECKOUT
                or selector.count("/iframe[") != 1
            ):
                raise ValueError("Control is outside the approved exact secure child frame.")
            path = selector.removeprefix("xpath=")
            match = re.search(r"/iframe\[\d+\]", path)
            frame_path, control_path = path[: match.end()], path[match.end() :]
            element = await trace.read("parent_frame_element", lambda: network_page.locator("xpath=" + frame_path).element_handle(timeout=2000))
            frame = await trace.read("child_frame_lookup", element.content_frame) if element else None
            if (
                not frame
                or frame.parent_frame != network_page.main_frame
                or frame.url != observed["frame_url"]
            ):
                return {"present": False, "reason": "secure_frame_changed"}
            context = guard.frame_context(frame.url, network_page.main_frame.url)
            if not context or context.get("approved") is not True:
                raise ValueError("Secure card frame lacks independent fresh Test Mode payment correlation.")
            # Read-only DOM validation supplies disabled/type/generation absent
            # from the SDK's formatted AX tree. Stagehand alone performs input.
            state = await trace.read("child_control_state", lambda: frame.evaluate(CONTROL_STATE, "xpath=" + control_path))
            if not state.get("present"):
                return state
            if state["frame_url"] != frame.url or state["tag"] != observed["tag"]:
                return {"present": False, "reason": "secure_frame_changed"}
            state["label"] = state.get("label") or observed["label"]
            state["metadata_source"] = "stagehand_snapshot_and_read_only_current_frame_dom"
            state["visible"] = state["visible"] and await trace.read("stagehand_visibility", lambda: page.locator(selector).is_visible(timeout=2000))
            if state["tag"] in ("INPUT", "TEXTAREA", "SELECT"):
                value = await trace.read("stagehand_input_value", lambda: page.locator(selector).input_value(timeout=2000))
                after = await trace.read("child_generation", lambda: frame.evaluate("({url:location.href,generation:performance.timeOrigin})"))
                if (
                    after != {"url": state["frame_url"], "generation": state["frame_generation"]}
                    or frame.url != observed["frame_url"]
                ):
                    return {"present": False, "reason": "secure_frame_changed"}
                state["value"] = value
            state.update(
                frame_url=DODO_SECURE_FRAME,
                destination=DODO_SECURE_FRAME,
                frame_config_id=context["config_id"],
                frame_context_verified=True,
            )
            return state
    state = await page.evaluate("(" + CONTROL_STATE + ")(" + json.dumps(selector) + ")")
    state.pop("frame_url", None)
    state.pop("frame_generation", None)
    return state


async def model(request):
    if not isinstance(request, LLMStructuredGenerateParams):
        raise RuntimeError("Only bounded structured Stagehand inference is supported.")
    result = await agenerate(
        redact_payment(
            {
                "system": request.system_prompt,
                "messages": [m.model_dump(mode="json") for m in request.messages],
            }
        ),
        request.response_format.schema_.model_dump(),
        label="stagehand",
    )
    return LLMStructuredGenerateResult(
        role=LLMRole.assistant,
        content=LLMTextContent(type="text", text=json.dumps(result)),
        output_format="json_schema",
        structured_content=result,
    )


async def install_session(context, network_page, page, guard, downloads, capture, verify_frame):
    """All callbacks close over this exact fresh context and guard, never a later persona."""
    guard.frame_verify = verify_frame
    timing = CheckoutTiming(network_page)
    context.on("request", timing.started)
    context.on("response", timing.response)
    context.on("response", guard.bootstrap_response)
    context.on("requestfinished", timing.finished)

    def failed_request(request):
        guard.record(request, "request_failed", failure=request.failure)
        timing.failed(request)

    context.on("requestfailed", failed_request)

    async def download(item):
        if item.suggested_filename.endswith(".svg"):
            if guard.card_attempt and guard.card_attempt.confirmed:
                try:
                    await guard.admit_owned_download(item)
                except ValueError:
                    return
            await item.save_as(downloads / (uuid4().hex + ".svg"))

    network_page.on("download", download)
    return timing, await page.on("console", capture.console)


async def main():
    async with AsyncExitStack() as stack:
        await run(stack)


async def run(stack):
    capture = EventCapture()
    blocked_domains, console_events = capture.blocked_domains, capture.console_events
    commands, pending = asyncio.Queue(), {}

    async def read_input():
        while line := await asyncio.to_thread(sys.stdin.readline):
            message = json.loads(line)
            if "callback_reply" in message:
                reply = pending.get(message["callback_reply"])
                if reply and not reply.done():
                    reply.set_result(message.get("data") or {})
            else:
                await commands.put(message)
        await commands.put(None)

    async def verify_frame(context):
        call_id = uuid4().hex
        pending[call_id] = asyncio.get_running_loop().create_future()
        print(json.dumps({"callback": "verify_dodo_frame", "call_id": call_id, "data": context}), flush=True)
        try:
            return await asyncio.wait_for(pending[call_id], timeout=15)
        finally:
            pending.pop(call_id, None)

    reader = asyncio.create_task(read_input())
    stack.callback(reader.cancel)

    downloads = Path(
        tempfile.mkdtemp(prefix="stagehand-download-", dir=os.environ.get("TRYFOLK_EVIDENCE_ROOT"))
    )
    session_stack = AsyncExitStack()
    pooled = os.environ.get("TRYFOLK_STAGEHAND_REUSE") == "true"
    opened = await open_routed_browser(
        stack, model=model,
        logging={"level": "debug", "on_log": capture.network_log}, downloads=downloads,
        session_stack=session_stack, pooled=pooled,
    )
    if pooled:
        browser, stagehand, page, network_context, network_page, guard, session_factory = opened
    else:
        browser, stagehand, page, network_context, network_page, guard = opened
        session_factory = None
    checkout_timing, console_subscription = await install_session(
        network_context, network_page, page, guard, downloads, capture, verify_frame,
    )
    session_open = True
    expiries = []
    policy = None
    fresh_actions = []
    fresh_document = None
    completed_fields, completed_cards = set(), {}
    billing_initialized_binding = None
    financial_started = False
    clicked = set()
    pay_actions = 0
    try:
        while command := await commands.get():
            boundary = {"op": command.get("op"), "call": "dispatch"}
            try:
                op = command["op"]
                if op == "end_session":
                    if not pooled or not session_open:
                        raise RuntimeError("No owned pooled context can be closed.")
                    for expiry in expiries:
                        expiry.cancel()
                    await asyncio.gather(*expiries, return_exceptions=True)
                    expiries = []
                    await console_subscription.unsubscribe()
                    console_subscription = None
                    await session_stack.aclose()
                    session_open = False
                    print(json.dumps({"ok": True, "data": {"fresh_context_closed": True}}), flush=True)
                    continue
                if op == "begin_session":
                    if not pooled:
                        raise RuntimeError("Browser reuse was not explicitly enabled by the controller.")
                    if not session_open:
                        downloads = Path(tempfile.mkdtemp(prefix="stagehand-download-", dir=os.environ.get("TRYFOLK_EVIDENCE_ROOT")))
                        session_stack = AsyncExitStack()
                        page, network_context, network_page, guard = await session_factory(session_stack, downloads)
                        checkout_timing, console_subscription = await install_session(
                            network_context, network_page, page, guard, downloads, capture, verify_frame,
                        )
                        session_open = True
                    completed_fields, completed_cards, clicked = set(), {}, set()
                    fresh_actions, fresh_document = [], None
                    billing_initialized_binding, financial_started, pay_actions = None, False, 0
                    blocked_domains.clear()
                    console_events.clear()
                    op = "goto"
                if op == "goto":
                    parsed = urlsplit(command["url"])
                    policy = NetworkPolicy(
                        command.get("origin") or f"{parsed.scheme}://{parsed.netloc}",
                        command.get("provider", "local-fixture"),
                    )
                    policy.check_url(command["url"])
                    guard.policy = policy
                    guard.card_enabled = policy.provider == "dodo" and command.get("card_capability") is True
                    await browser.context.set_domain_policy({"allowed_domains": policy.sdk_domains})
                    await page.goto(command["url"])
                    ready = await settle(page, policy, {"method": "goto"})
                    data = {"url": ready["url"], "settlement": ready}
                    # This read observes the fresh owned browser, never persona text.
                    observed_context = await network_page.evaluate("""({locale:navigator.language,
                        user_agent:navigator.userAgent, has_touch:navigator.maxTouchPoints>0,
                        viewport:{width:innerWidth,height:innerHeight}})""")
                    data["condition_observation"] = {**observed_context,
                        "source":"stagehand_owned_context", "fresh_context":True}
                elif op in ("observe", "observe_native"):
                    fresh_actions, fresh_document = [], None
                    observation_deadline = asyncio.get_running_loop().time() + 170
                    current_document = await page.evaluate(DOCUMENT_STATE)
                    # Initial billing hydration must not follow the acknowledged
                    # Continue into the independently guarded payment document.
                    if (
                        policy.provider == "dodo"
                        and exact_origin(current_document["url"]) == DODO_CHECKOUT
                        and billing_initialized_binding is None
                        and guard.checkout_navigation is not None
                        and current_document["url"] == guard.checkout_navigation.entry_url
                        and guard.checkout_navigation.continue_generation is None
                    ):
                        initialized = await wait_for_billing_initialization(
                            lambda: page.evaluate(DOCUMENT_STATE), policy, deadline=observation_deadline,
                        )
                        billing_initialized_binding = initialized["url"], initialized.get("generation")

                    async def infer():
                        if op == "observe_native":
                            # Stagehand snapshots and read-only current DOM identify controls.
                            # There is no inference, selector caching or action in this pass.
                            boundary["call"] = "stagehand.snapshot.native_candidates"
                            snapshot = (await page.snapshot(include_iframes=True, timeout=10000)).model_dump(mode="json")
                            discovery = await discover_checkout_candidates(
                                snapshot, metadata, policy, current_document["url"],
                                filled=completed_fields, completed_cards=completed_cards,
                                clicked=clicked, pay_actions=pay_actions,
                                executed_fields=completed_fields, phase="primary_billing",
                                include_owned_post_payment=True,
                            )
                            boundary["native_discovery"] = discovery.evidence(document_stable=False)
                            return [action for _, action in sorted(discovery.candidates, key=lambda item: item[0])]
                        boundary["call"] = "stagehand.observe"
                        observed = await stagehand.observe(
                            "List visible enabled checkout, continue, confirm-purchase, art-pack and download controls. Include the Country dropdown even when preselected, the manual address option, and every unfilled billing/address/card field on the hosted test checkout. One action per control: click, fill or selectOptionFromDropdown for native dropdowns. Do not select disabled or Processing controls, navigate, or supply arbitrary test values."
                            if policy.provider == "dodo"
                            else "List the visible enabled clickable checkout and purchased-capability controls. Return one action per control; only click methods.",
                            page=page,
                        )
                        boundary["observation_path"] = private_evidence(
                            "observation",
                            {
                                "document": await page.evaluate(DOCUMENT_STATE),
                                "actual_sdk_response": observed.model_dump(mode="json"),
                            },
                        )
                        proposed = [item.model_dump(mode="json") for item in observed.data]
                        if policy.provider == "dodo" and exact_origin(await page.url()) == DODO_CHECKOUT:
                            snapshot = (await page.snapshot(include_iframes=True, timeout=10000)).model_dump(mode="json")
                            proposed = await include_snapshot_manual_choices(
                                proposed, snapshot, lambda selector: control_state(page, selector, network_page, guard),
                                policy, await page.url(),
                            )
                        return proposed

                    async def metadata(selector):
                        boundary.update(call="control_state", selector=selector)
                        return await control_state(page, selector, network_page, guard)

                    data = await observe_fresh(
                        page, policy, infer, metadata,
                        timeout=max(0, observation_deadline - asyncio.get_running_loop().time()),
                    )
                    fresh_actions, fresh_document = data["actions"], data["document"]
                    if op == "observe_native":
                        data["execution_policy"] = "conditional_persona_plan_native_stagehand_controls_v1"
                        data["model_inference"] = False
                        for field in data["field_evidence"]:
                            if field["approved_value_present"]:
                                completed_fields.add(field["field"])
                    data["freshness"]["evidence_path"] = private_evidence("freshness", data["freshness"])
                elif op == "act":
                    current_url = await page.url()
                    policy.check_url(current_url)
                    action = command["action"]
                    if action.get("_tryfolk", {}).get("kind") in ("pay", "confirm"):
                        financial_started = True
                    boundary.update(call="control_state.pre_act", selector=action["selector"])
                    control = await control_state(page, action["selector"], network_page, guard)
                    if policy.provider == "dodo":
                        current_document = await page.evaluate(DOCUMENT_STATE)
                        policy.check_url(current_document["url"])
                        if not fresh_document or not same_document(fresh_document, current_document):
                            comparison = {
                                "same_url": bool(fresh_document) and fresh_document.get("url") == current_document.get("url"),
                                "same_generation": bool(fresh_document) and fresh_document.get("generation") == current_document.get("generation"),
                                "same_control_signature": bool(fresh_document) and fresh_document.get("control_signature") == current_document.get("control_signature"),
                                "nonfinancial": False,
                                "executed": False,
                            }
                            metadata = action.get("_tryfolk") or {}
                            retryable = (
                                comparison["same_url"] and comparison["same_generation"]
                                and not comparison["same_control_signature"]
                                and action in fresh_actions
                                and action.get("method") == "fill"
                                and metadata.get("field") in ("address", "city", "state", "postal")
                                and isinstance(current_document.get("generation"), (int, float))
                                and not isinstance(current_document.get("generation"), bool)
                                and 0 < current_document["generation"] < float("inf")
                                and not financial_started
                                and not (guard.card_attempt and (guard.card_attempt.armed_at is not None or guard.card_attempt.confirmed))
                                and exact_origin(current_document["url"]) == DODO_CHECKOUT
                                and current_document.get("body_present") is True
                                and current_document.get("ready_state") in ("interactive", "complete")
                                and current_document.get("busy") is False
                                and not metadata.get("frame_url")
                                and "/iframe[" not in str(action.get("selector") or "")
                            )
                            if retryable:
                                try:
                                    policy.validate(action, current_document["url"])
                                    retryable = policy.bind(action, control, current_document["url"]) == action
                                except ValueError:
                                    retryable = False
                            if retryable:
                                comparison.update(nonfinancial=True, field=metadata["field"])
                                raise PreActDocumentDrift(comparison, retryable=True)
                            error = RuntimeError(
                                "Document changed after observation; no browser action was executed or retried."
                            )
                            error.data = comparison
                            raise error
                        if action not in fresh_actions:
                            raise RuntimeError("Action does not match a fresh SDK-observed control.")
                        policy.validate(action, current_url)
                        if policy.bind(action, control, current_url) != action:
                            raise RuntimeError(
                                "Observed control changed after inference; no browser action was executed."
                            )
                    else:
                        # Preserve legacy fixture diagnostic replay; revalidate its
                        # current DOM destination and strict single-click boundary.
                        policy.bind(action, control, current_url)
                    if not action.get("_tryfolk", {}).get("frame_url"):
                        policy.check_url(control.get("destination") or current_url)
                    fresh_actions = []
                    if action.get("_tryfolk", {}).get("kind") == "billing_continue":
                        guard.expect_billing_continue(fresh_document)
                    if guard.card_attempt and guard.card_attempt.confirmed and action.get("_tryfolk", {}).get("kind") in ("art_pack", "download"):
                        boundary["call"] = "owned_navigation.prepare"
                        await guard.prepare_owned_navigation(control["destination"])
                    if action.get("_tryfolk", {}).get("kind") == "pay":
                        await guard.arm_native_pay(completed_fields, completed_cards)

                        async def expire_card():
                            await asyncio.sleep(300)
                            guard.card_attempt.closed = True
                            await network_context.close()

                        expiry = asyncio.create_task(expire_card())
                        stack.callback(expiry.cancel)
                        expiries.append(expiry)
                    existing_files = set(downloads.iterdir())
                    sdk_action = {k: v for k, v in action.items() if k != "_tryfolk"}
                    boundary["call"] = "stagehand.act"
                    data = (await stagehand.act(sdk_action, page=page, timeout=10000)).model_dump(mode="json")
                    if not data.get("data", {}).get("success"):
                        raise RuntimeError("Stagehand did not execute the observed action successfully.")
                    kind = action.get("_tryfolk", {}).get("kind")
                    if kind in ("login", "purchase", "confirm", "art_pack", "download", "billing_continue"):
                        clicked.add(kind)
                    if kind == "pay":
                        pay_actions += 1
                    if action.get("_tryfolk", {}).get("diagnostic_manual"):
                        clicked.add("manual")
                        # Current public source bytes are not registered in the runtime.
                        # Verify the first click's effect; no automatic second click.
                        boundary["call"] = "manual_address_effect_verification"
                        events = []

                        async def forbidden_repeat(_):
                            raise RuntimeError("Unregistered native manual source cannot repeat an action.")

                        manual_deadline = asyncio.get_running_loop().time() + 10
                        try:
                            _, verified_document = await resolve_manual_address(
                                lambda: read_manual_current(
                                    page, network_page, guard, completed_fields=completed_fields,
                                    original_document=fresh_document, deadline=manual_deadline, on_event=events.append,
                                ), forbidden_repeat, policy, source_sha256=None,
                                deadline=manual_deadline,
                                executed=(action, fresh_document), on_event=events.append,
                            )
                        except ManualAddressGate as error:
                            error.observation_evidence = (error.observation_evidence or {}) | {
                                "manual_effect": {"verified": False, "events": events},
                            }
                            raise
                        data["manual_effect"] = {"verified": True, "events": events,
                                                "generation_current": verified_document.get("generation")
                                                == fresh_document.get("generation")}
                    # Hosted autocomplete/telemetry need not become idle after a fill.
                    # Wait for known app/checkout navigation, with an exact-origin check.
                    boundary["call"] = "current_document_settlement"
                    data["settlement"] = await settle(
                        page,
                        policy,
                        action,
                        previous_document=fresh_document,
                        timeout=30 if action.get("_tryfolk", {}).get("kind") == "billing_continue" else 10,
                    )
                    if data["settlement"].get("transition_state") == "error":
                        boundary["settlement"] = data["settlement"]
                        raise RuntimeError(
                            "Acknowledged checkout transition ended with a stable visible error; no action was retried."
                        )
                    data["settled_url"] = data["settlement"]["url"]
                    if action.get("method") in FIELD_METHODS:
                        boundary["call"] = "control_state.field_completion"
                        policy.validate_completion(
                            action,
                            await control_state(page, action["selector"], network_page, guard),
                            data["settled_url"],
                        )
                        data["field_completed"] = action["_tryfolk"]["field"]
                        completed_fields.add(action["_tryfolk"]["field"])
                        if action["_tryfolk"]["field"].startswith("card_"):
                            completed_cards[action["_tryfolk"]["field"]] = (
                                action["_tryfolk"].get("frame_config_id"), action["_tryfolk"].get("frame_generation")
                            )
                    data["observed_text"] = await page.evaluate("document.body.innerText")
                    if policy.provider == "dodo":
                        from tryfolk.scenario_evidence import checkout_observation

                        condition_observation = checkout_observation(guard, completed_fields)
                        if condition_observation:
                            data["condition_observation"] = condition_observation
                    if action.get("_tryfolk", {}).get("capability"):
                        deadline = asyncio.get_running_loop().time() + 5
                        while asyncio.get_running_loop().time() < deadline:
                            files = [
                                p
                                for p in downloads.iterdir()
                                if p not in existing_files and p.suffix == ".svg"
                            ]
                            if len(files) == 1 and files[0].stat().st_size:
                                if files[0].stat().st_size > 1_000_000:
                                    raise RuntimeError(
                                        "Capability download exceeds the owned sample file limit."
                                    )
                                raw = files[0].read_bytes()
                                download_admission = next((e for e in reversed(guard.events)
                                    if e.get("decision") == "owned_download_event"), None)
                                data["capability"] = {
                                    "available": b"<svg" in raw[:1024],
                                    "download_path": str(files[0]),
                                    "download_sha256": hashlib.sha256(raw).hexdigest(),
                                    "bytes": len(raw),
                                    "source": "stagehand_browser_download",
                                    "download_admission": download_admission,
                                    "download_binding": (download_admission or {}).get("download_binding"),
                                }
                                break
                            await asyncio.sleep(0.1)
                        else:
                            data["capability"] = {"available": None, "source": "download_not_observed"}
                elif op == "screenshot":
                    masks = [frame.locator("input, textarea, select") for frame in network_page.frames]
                    await network_page.screenshot(path=command["path"], mask=masks)
                    data = {"path": command["path"], "private_fields_masked": True,
                            "source": "owned_browser_native_masked_capture"}
                elif op == "inspect":
                    policy.check_url(await page.url())
                    data = {
                        "document": await page.evaluate(DOCUMENT_STATE),
                        "snapshot": (await page.snapshot(include_iframes=True, timeout=10000)).model_dump(
                            mode="json"
                        ),
                        "render": await page.evaluate("""(() => {
                          const safe=url=>{try{const u=new URL(url,location.href);return u.origin+u.pathname}catch{return null}};
                          return {iframes:[...document.querySelectorAll('iframe')].map(x=>({src:safe(x.src),visible:!!x.getClientRects().length})),
                           scripts:[...document.querySelectorAll('script[src]')].map(x=>safe(x.src)),
                           resources:performance.getEntriesByType('resource').slice(-100).map(x=>({url:safe(x.name),type:x.initiatorType,duration:x.duration,transfer_size:x.transferSize})),
                           body_text:document.body.innerText.slice(0,10000)};
                        })()"""),
                        "blocked_requests": blocked_domains,
                        "console_errors": console_events,
                        "resource_guard": guard.events,
                        "checkout_request_timing": checkout_timing.snapshot(),
                    }
                elif op == "close":
                    break
                else:
                    raise RuntimeError("Unsupported bounded browser operation.")
                print(json.dumps(redact_frame_config({"ok": True, "data": data})), flush=True)
            except Exception as error:
                safety = adapter_safety(error, boundary, policy, fresh_document,
                    financial_started=financial_started, card_attempt=guard.card_attempt)
                fresh_actions = []
                fresh_document = None
                error_path = private_evidence(
                    "error",
                    {
                        "boundary": boundary,
                        "error_type": type(error).__name__,
                        "pre_submission_evidence": safety,
                        "message": str(error),
                        "code": getattr(error, "code", None),
                        "data": getattr(error, "data", None),
                        "traceback": traceback.format_exc(),
                        "observation_evidence": getattr(error, "observation_evidence", None),
                        "settlement_diagnostics": getattr(error, "settlement_diagnostics", None),
                        "checkout_request_timing": checkout_timing.snapshot(),
                        "resource_guard": guard.events,
                    },
                )
                print(
                    json.dumps(
                        redact_frame_config(
                            {"ok": False, "error": str(error)[:500], "error_evidence_path": error_path, "pre_submission_evidence": safety,
                             "code": getattr(error, "code", None), "data": getattr(error, "data", None)}
                        )
                    ),
                    flush=True,
                )
    finally:
        for expiry in expiries:
            expiry.cancel()
        await asyncio.gather(*expiries, return_exceptions=True)
        try:
            if console_subscription:
                await console_subscription.unsubscribe()
        finally:
            await session_stack.aclose()


if __name__ == "__main__":
    asyncio.run(main())
