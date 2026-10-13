"""Known-failure replay using the same profile and fresh observed browser controls."""

import asyncio
import re
from urllib.parse import urlsplit
import httpx
from .browser_policy import BrowserPolicy, exact_origin
from .dodo import DodoTarget
from .process import SDKProcess
from .worker import Cancelled
from .payment_privacy import redact_payment


def failed_persona(before, population):
    profiles = {p["id"]: p for p in population}
    for persona_id, result in before["outcomes"].items():
        if result.get("outcome") == "failed" and result.get("actions"):
            if persona_id not in profiles:
                raise ValueError(
                    "The known failure's original saved persona is absent; replay cannot substitute another."
                )
            return profiles[persona_id], result
    raise ValueError("No recorded failed browser sequence; adaptive retest cannot substitute for replay.")


def replay_observation_contract(persona, failure):
    """Retain the saved plan's real bound and guarded observation contract."""
    if persona.get("purchase_plan"):
        from .bulk_personas import validated_purchase_plan

        plan = validated_purchase_plan(persona)
        operation, limit = "observe_native", plan["max_steps"]
    else:
        operation, limit = "observe", 24
    if len(failure["actions"]) > limit:
        raise ValueError("Known failure exceeds its original saved checkout action bound.")
    return operation


class MissingReplayControl(ValueError):
    """No matching freshly observed control; an ambiguity is not this condition."""


def rebind_action(recorded, actions, policy, url):
    """Match intent, never a stale checkout ID, selector, opaque login or fill value."""
    policy.check_url(url)
    previous = recorded.get("_tryfolk") or {}

    def normalized(description):
        return re.sub(r"\s+", " ", str(description or "").strip().lower())

    def matches(action):
        current = action.get("_tryfolk") or {}
        if action.get("method") != recorded.get("method"):
            return False
        if previous.get("field"):
            return current.get("field") == previous["field"]
        if previous.get("kind") and previous["kind"] != "click":
            return current.get("kind") == previous["kind"]
        return bool(normalized(recorded.get("description"))) and normalized(
            action.get("description")
        ) == normalized(recorded["description"])

    candidates = [a for a in actions if matches(a)]
    if (len(candidates) > 1 and previous.get("kind") == "art_pack"
            and exact_origin(url) == policy.origin and urlsplit(url).path == "/checkout/return"):
        # BrowserPolicy.bind permits art_pack on the owned return only for a
        # visible anchor with the exact owned /special-downloads destination.
        # Choose an existing fresh action, never construct a historical selector.
        for candidate in candidates:
            policy.validate(candidate, url)
        candidates = [next((a for a in candidates if a["selector"] == recorded.get("selector")),
                           min(candidates, key=lambda a: a["selector"]))]
    if len(candidates) != 1:
        error = MissingReplayControl if not candidates else ValueError
        raise error(
            "Recorded intent has no unique freshly observed control; diagnostic stops without navigation substitution."
        )
    policy.validate(candidates[0], url)
    return candidates[0]


def diagnostic_persona_id(persona_id, run):
    attempt = run.get("execution_attempt", 1)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("Replay requires a valid execution attempt.")
    return persona_id + "-known-replay" + (f"-attempt-{attempt}" if attempt > 1 else "")


async def observed_replay_action(browser, recorded, policy, operation, *, after_confirm=False,
                                 check=None, timeout=170, return_document=False):
    """At most two passive missing-control rereads share the original SDK bound."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    for attempt in range(3):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise RuntimeError("Original replay observation deadline exhausted; no action dispatched.")
        observed = await asyncio.wait_for(browser.request({"op": operation}), remaining)
        try:
            action = rebind_action(recorded, observed["actions"], policy, observed["url"])
            return (action, observed.get("document")) if return_document else action
        except MissingReplayControl:
            if (not after_confirm or recorded.get("_tryfolk", {}).get("kind") != "art_pack"
                    or exact_origin(observed["url"]) != policy.origin
                    or urlsplit(observed["url"]).path != "/checkout/return" or attempt == 2):
                raise
            if check is not None:
                await check()
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise RuntimeError("Original replay observation deadline exhausted; no action dispatched.")
            await asyncio.wait_for(asyncio.sleep(0.05), remaining)


async def execute_replay_action(browser, recorded, policy, operation, *, after_confirm=False, check=None,
                                runtime=None, run=None, emit=None, persona_id=None):
    """Reuse the original freshness bound and shared run native admission."""
    from .runtime import FreshActionRetry

    retry = FreshActionRetry()
    kind = recorded.get("_tryfolk", {}).get("kind")
    lease, result, settled = None, None, False
    try:
        if runtime is not None and kind in ("billing_continue", "pay"):
            lease = await runtime.acquire_native_admission(run["id"], kind, retry, emit, persona_id)
        while True:
            if check is not None:
                await check()
            action, document = await observed_replay_action(
                browser, recorded, policy, operation, after_confirm=after_confirm, check=check,
                timeout=min(170, retry.remaining()), return_document=True,
            )
            if not retry.accepts_replacement(action, document):
                raise RuntimeError("Fresh replay replacement left its original control binding; no action dispatched.")
            if lease is not None and action.get("_tryfolk", {}).get("kind") != lease["boundary_kind"]:
                raise RuntimeError("Replay admission boundary changed; no action dispatched.")
            try:
                if lease is not None:
                    runtime.native_pay_started[(run["id"], persona_id)] = runtime.native_pay_clock()
                result = await asyncio.wait_for(browser.request({"op": "act", "action": action}), retry.remaining())
            except Exception as error:
                if not retry.allow(error, action, document):
                    raise
                continue
            settled = (result.get("settlement", {}).get("url", "").split("?", 1)[0] == policy.origin + "/checkout/return"
                       if kind == "pay" else result.get("success", result.get("data", {}).get("success", True)) is True)
            return action, result
    finally:
        if lease is not None:
            await runtime.release_native_admission(lease, "cancelled" if asyncio.current_task().cancelling() else
                ("owned_return_settled" if kind == "pay" else "billing_controls_settled") if settled else "failed", emit, persona_id)


async def unpaid_control(runtime, run):
    """A fresh unpaid account must remain denied; this creates no provider checkout."""
    target = DodoTarget(run["preview_url"])
    fixture = await runtime.provision(run, {"id": "heldout-unpaid", "conditions": {"history": "new"}})
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as client:
        login = await client.post(
            target.origin + "/api/target/login",
            headers={"Origin": target.origin},
            json={"login_token": fixture["login_token"]},
        )
        login.raise_for_status()
        download = await client.get(target.origin + "/api/target/download?artwork=0")
        account = await client.get(target.origin + "/api/target/me")
        account.raise_for_status()
    orders = (
        await target.request("GET", "/api/target/orders", params={"account_id": fixture["account_id"]})
    )["orders"]
    return {
        "case": "unpaid_access_denied",
        "passed": download.status_code == 403 and not account.json()["has_access"] and not orders,
        "download_http": download.status_code,
        "orders": len(orders),
        "account_id": fixture["account_id"],
        "not_in_population_denominator": True,
        "method": "owned HTTP control; no model, checkout or payment",
    }


async def duplicate_control(runtime, run, fixture, browser, capability):
    """Separate same-order HTTP audit after the real observed Confirm executed."""
    first = await runtime.verification(run, fixture, capability)
    if (first.get("outcome") != "completed" or first.get("provider") != "dodo"
            or first.get("payment_verified") is not True or first.get("entitlement_verified") is not True):
        raise ValueError("Duplicate audit requires independent already-paid and granted account proof.")
    payment = first["payment"]
    order_id = first["receipt"]["ownership"]["order_id"]
    audit = await browser.request({
        "op": "duplicate_reconcile", "ownership": {key: fixture[key] for key in ("run_id", "persona_id", "account_id", "fixture_id", "target_revision")},
        "order_id": order_id, "payment_id": payment["id"], "checkout_id": payment["checkout_id"],
    })
    second = await runtime.verification(run, fixture, capability)
    identity = ("id", "status", "account_id", "checkout_id", "product_id", "amount", "currency")
    grant, next_grant = first.get("app_entitlement", {}), second.get("app_entitlement", {})
    return {"case": "duplicate_reconciliation_grants_once",
            "passed": audit.get("state") == "already_granted" and audit.get("grant_applied") is False
            and audit.get("same_account_order") is True and second.get("outcome") == "completed"
            and second.get("payment_verified") is True and second.get("entitlement_verified") is True
            and all(second.get("payment", {}).get(key) == payment.get(key) for key in identity)
            and grant == next_grant and next_grant.get("grant_count") == 1,
            "account_id": fixture["account_id"], "order_id": order_id,
            "method": "owned HTTP diagnostic in actual browser cookie jar after real observed Confirm; independent before/after payment and grant reads",
            "not_in_population_denominator": True, "additional_checkout_or_pay": False,
            "diagnostic": audit}


async def replay(runtime, run, population, emit):
    original_emit = emit

    async def emit(kind, data, persona_id=None):
        await original_emit(kind, redact_payment(data), persona_id)

    with runtime.service.store.transaction() as db:
        before = runtime.service.store.load(db, "runs", run["parent_run_id"])
    persona, failure = failed_persona(before, population)
    observe_operation = replay_observation_contract(persona, failure)
    fixture = await runtime.provision(run, {**persona, "id": diagnostic_persona_id(persona["id"], run)})

    def cancelled():
        return runtime.cancelled(run["id"])

    async def frame_verify(context):
        return await DodoTarget(run["preview_url"]).frame_context(context, run, fixture)

    browser = SDKProcess("stagehand", cancelled, frame_verify=frame_verify)
    policy = BrowserPolicy(run["preview_url"], "dodo")
    executed, capability = [], None
    previous_kind = None
    duplicate = {"case": "duplicate_reconciliation_grants_once", "passed": False,
                 "not_in_population_denominator": True}
    try:
        await browser.request(
            {"op": "goto", "url": fixture["login_url"], "origin": policy.origin, "provider": "dodo", "card_capability": True}
        )
        for recorded in failure["actions"]:
            if cancelled():
                raise Cancelled()
            await runtime.check_revision(run)
            async def check_observation_retry():
                if cancelled():
                    raise Cancelled()
                await runtime.check_revision(run)

            action, result = await execute_replay_action(
                browser, recorded, policy, observe_operation,
                after_confirm=previous_kind == "confirm", check=check_observation_retry,
                runtime=runtime, run=run, emit=emit, persona_id=persona["id"],
            )
            previous_kind = action["_tryfolk"].get("kind")
            executed.append(redact_payment(action))
            await emit(
                "known_replay_action", {"action": action, "result": result, "diagnostic": True}, persona["id"]
            )
            if action["_tryfolk"].get("capability"):
                capability = result.get("capability", {}).get("available")
            if action["_tryfolk"].get("kind") == "confirm" and not duplicate["passed"]:
                duplicate = await duplicate_control(runtime, run, fixture, browser, capability)
                await emit("held_out_checks", {"cases": [duplicate],
                           "not_in_population_denominator": True,
                           "no_additional_provider_checkout": True}, persona["id"])
                if not duplicate["passed"]:
                    raise RuntimeError("Same-paid-order HTTP idempotency diagnostic failed; retest stopped.")
        verified = await runtime.verification(run, fixture, capability)
        if verified["outcome"] != "completed":
            await emit(
                "known_failure_replay",
                {
                    **verified,
                    "diagnostic": True,
                    "not_in_population_denominator": True,
                    "original_persona_id": persona["id"],
                    "conditions": persona["conditions"],
                    "actions": executed,
                },
            )
            raise RuntimeError(
                "Known failure's fresh provider/account verification did not pass; full retest stopped."
            )
        # Separate capability audit can need the now-enabled Art Pack link that
        # was unavailable on the broken source. It is not a substituted checkout.
        for _ in range(2):
            observation = await browser.request({"op": observe_operation})
            choices = [
                a
                for a in observation["actions"]
                if a.get("_tryfolk", {}).get("kind")
                == (
                    "download"
                    if any(x.get("_tryfolk", {}).get("kind") == "download" for x in observation["actions"])
                    else "art_pack"
                )
            ]
            if not choices:
                break
            # Multiple owned artwork links are equivalent samples: choose one
            # stable selector, never an arbitrary URL or model-supplied argument.
            action = sorted(choices, key=lambda a: a["selector"])[0]
            result = await browser.request({"op": "act", "action": action})
            await emit("known_replay_capability", {"action": action, "result": result, "diagnostic": True})
            if action["_tryfolk"].get("capability"):
                capability = result.get("capability", {}).get("available")
                break
        verified = await runtime.verification(run, fixture, capability)
        await emit(
            "known_failure_replay",
            {
                **verified,
                "diagnostic": True,
                "not_in_population_denominator": True,
                "original_persona_id": persona["id"],
                "account_id": fixture["account_id"],
                "conditions": persona["conditions"],
                "actions": executed,
                "revision": run["revision"],
                "capability_separate": True,
            },
        )
        unpaid = await unpaid_control(runtime, run)
        await emit(
            "held_out_checks",
            {
                "cases": [duplicate, unpaid],
                "not_in_population_denominator": True,
                "no_additional_provider_checkout": True,
            },
        )
        if (
            verified["outcome"] != "completed"
            or capability is not True
            or not duplicate["passed"]
            or not unpaid["passed"]
        ):
            raise RuntimeError(
                "Known replay capability/idempotency/unpaid control incomplete; full saved-population retest stopped."
            )
    finally:
        await browser.close()
