"""Known-failure replay using the same profile and fresh observed browser controls."""

import re
import httpx
from .browser_policy import BrowserPolicy
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
    if len(candidates) != 1:
        raise ValueError(
            "Recorded intent has no unique freshly observed control; diagnostic stops without navigation substitution."
        )
    policy.validate(candidates[0], url)
    return candidates[0]


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


async def replay(runtime, run, population, emit):
    original_emit = emit

    async def emit(kind, data, persona_id=None):
        await original_emit(kind, redact_payment(data), persona_id)

    with runtime.service.store.transaction() as db:
        before = runtime.service.store.load(db, "runs", run["parent_run_id"])
    persona, failure = failed_persona(before, population)
    fixture = await runtime.provision(run, {**persona, "id": persona["id"] + "-known-replay"})

    def cancelled():
        return runtime.cancelled(run["id"])

    async def frame_verify(context):
        return await DodoTarget(run["preview_url"]).frame_context(context, run, fixture)

    browser = SDKProcess("stagehand", cancelled, frame_verify=frame_verify)
    policy = BrowserPolicy(run["preview_url"], "dodo")
    executed, capability = [], None
    try:
        await browser.request(
            {"op": "goto", "url": fixture["login_url"], "origin": policy.origin, "provider": "dodo", "card_capability": True}
        )
        if len(failure["actions"]) > 24:
            raise ValueError("Known failure exceeds the quoted bounded checkout sequence.")
        for recorded in failure["actions"]:
            if cancelled():
                raise Cancelled()
            await runtime.check_revision(run)
            observed = await browser.request({"op": "observe"})
            action = rebind_action(recorded, observed["actions"], policy, observed["url"])
            result = await browser.request({"op": "act", "action": action})
            executed.append(redact_payment(action))
            await emit(
                "known_replay_action", {"action": action, "result": result, "diagnostic": True}, persona["id"]
            )
            if action["_tryfolk"].get("capability"):
                capability = result.get("capability", {}).get("available")
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
        # Repeat the actual observed confirmation only if present, using the same
        # paid account. This never creates another checkout or changes identity.
        observation = await browser.request({"op": "observe"})
        confirmations = [a for a in observation["actions"] if a.get("_tryfolk", {}).get("kind") == "confirm"]
        duplicate = {
            "case": "duplicate_reconciliation_grants_once",
            "passed": False,
            "not_in_population_denominator": True,
        }
        if len(confirmations) == 1:
            await browser.request({"op": "act", "action": confirmations[0]})
            second = await runtime.verification(run, fixture, capability)
            first_grant, next_grant = verified.get("app_entitlement", {}), second.get("app_entitlement", {})
            duplicate.update(
                passed=second["outcome"] == "completed"
                and first_grant == next_grant
                and next_grant.get("grant_count") == 1,
                account_id=fixture["account_id"],
                method="fresh Stagehand confirmation on already-paid diagnostic account; independent read-only grant checks",
            )
        # Separate capability audit can need the now-enabled Art Pack link that
        # was unavailable on the broken source. It is not a substituted checkout.
        for _ in range(2):
            observation = await browser.request({"op": "observe"})
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
