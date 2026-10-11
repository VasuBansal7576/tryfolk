"""Verify manual entry's visible effect under the caller's original deadline.

The retained public bundle proves an idempotent opener for *Enter manually*.
The translated outer *Enter address manually* control toggles manual state;
retained bytes do not attest real3's current renderer or closed precondition.
Only a current, exact source/control match permits one fresh resolution after
a successful click with no effect. Purchase/login/Pay are never accepted here.
"""

import asyncio
import math
from .browser_policy import DODO_CHECKOUT, exact_origin

MANUAL_FIELDS = frozenset({"city", "state", "postal"})
REVIEWED_OPENERS = {
    "594824753cd5c68673c5719ec09a77477991381b992f82f1b2542fc44fda0895": "enter_manually",
}


class ManualAddressGate(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__("Manual address effect is unestablished: " + reason)


async def resolve_manual_address(
    read_current, act, policy, *, source_sha256=None, deadline, on_event=None, effect_timeout=2,
    executed=None,
):
    """read_current supplies stable SDK discovery + current document; act uses SDK.

    The source digest must describe the current document's independently
    captured public bytes, not a historical artifact. Neither selectors nor
    source/body/control values leave the closed event schema. The global
    clicked set stays unchanged until the caller receives verified effects.
    """
    if not 0 < effect_timeout <= 2:
        raise ValueError("Manual effect observation permits at most two seconds per action")
    loop = asyncio.get_running_loop()
    emit = on_event or (lambda event: None)
    original = (executed[1]["url"], executed[1].get("generation")) if executed else None
    attempts = 1 if executed else 0

    async def bounded(callback):
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise ManualAddressGate("original_deadline_exhausted")
        try:
            return await asyncio.wait_for(callback(), remaining)
        except TimeoutError:
            raise ManualAddressGate("original_deadline_exhausted") from None

    async def current():
        nonlocal original
        discovery, document = await bounded(read_current)
        policy.check_url(document["url"])
        if policy.provider != "dodo" or exact_origin(document["url"]) != DODO_CHECKOUT:
            raise ManualAddressGate("manual_origin_unapproved")
        if (
            document.get("document_stable") is not True
            or document.get("ready_state") not in ("interactive", "complete")
            or document.get("body_present") is not True
            or document.get("busy") is not False
        ):
            raise ManualAddressGate("manual_document_unstable")
        binding = document["url"], document.get("generation")
        if (not isinstance(binding[1], (int, float)) or isinstance(binding[1], bool)
                or not math.isfinite(binding[1]) or binding[1] <= 0):
            raise ManualAddressGate("manual_document_unstable")
        if original is None:
            original = binding
        elif binding != original:
            raise ManualAddressGate("manual_document_changed")
        fields = MANUAL_FIELDS & discovery.observed_fields
        emit({"decision": "manual_current_effect_read", "actions": attempts,
              "generation_current": True,
              "country_currently_verified": "country" in discovery.observed_completion,
              "fields_visible": {field: field in fields for field in sorted(MANUAL_FIELDS)},
              "outer_manual_control_visible": "enter_address_manually" in discovery.observed_manual_variants,
              "inner_manual_control_visible": "enter_manually" in discovery.observed_manual_variants})
        if "country" not in discovery.observed_completion:
            raise ManualAddressGate("manual_country_currently_unverified")
        if fields and fields != MANUAL_FIELDS:
            raise ManualAddressGate("manual_effect_partial")
        if fields == MANUAL_FIELDS and discovery.observed_manual_variants:
            raise ManualAddressGate("manual_entry_open_unverified")
        return discovery, document

    def validate_manual(action, document):
        metadata = action.get("_tryfolk", {})
        policy.validate(action, document["url"])
        if (
            action.get("method") != "click" or action.get("arguments") != []
            or metadata.get("kind") != "click" or metadata.get("frame_url")
            or metadata.get("diagnostic_manual") is not True
            or "/iframe[" in str(action.get("selector") or "")
            or metadata.get("manual_variant") not in ("enter_manually", "enter_address_manually")
        ):
            raise ManualAddressGate("manual_control_unresolved")
        return action

    def manual_action(state):
        discovery, document = state
        actions = [action for _, action in discovery.candidates
                   if action.get("_tryfolk", {}).get("diagnostic_manual") is True]
        if len(actions) != 1:
            raise ManualAddressGate("manual_control_unresolved")
        return validate_manual(actions[0], document)

    def verified(state):
        return MANUAL_FIELDS <= state[0].observed_fields

    if executed:
        validate_manual(*executed)
    state = await current()
    if verified(state):
        emit({"decision": "manual_effect_verified", "actions": attempts})
        return state
    initial_action = executed[0] if executed else manual_action(state)
    for attempt in (1, 2):
        action = initial_action if attempt == 1 else manual_action(state)
        if attempt == 2:
            reviewed = REVIEWED_OPENERS.get(source_sha256)
            if not reviewed or any(
                a["_tryfolk"].get("manual_variant") != reviewed for a in (initial_action, action)
            ):
                emit({"decision": "deny_manual_repeat", "actions": attempts,
                      "exact_reviewed_source_and_control": False})
                raise ManualAddressGate("manual_entry_open_unverified")
            emit({"decision": "manual_fresh_idempotent_resolution", "actions": attempts,
                  "exact_reviewed_source_and_control": True})
        if not (attempt == 1 and executed):
            attempts = attempt  # Count before execution; ambiguous SDK outcomes cannot be repeated.
            if await bounded(lambda: act(action)) is not True:
                raise ManualAddressGate("manual_SDK_not_successful")
        emit({"decision": "manual_act_succeeded_effect_pending", "actions": attempts})
        observation_end = min(deadline, loop.time() + effect_timeout)
        while True:
            state = await current()
            if verified(state):
                emit({"decision": "manual_effect_verified", "actions": attempts})
                return state
            remaining = observation_end - loop.time()
            if remaining <= 0:
                break
            await bounded(lambda: asyncio.sleep(min(0.05, remaining)))
            if loop.time() >= observation_end:
                break
        if attempt == 1:
            state = await current()  # A second action always needs a new SDK control resolution.
            if verified(state):
                emit({"decision": "manual_effect_verified", "actions": attempts})
                return state
    raise ManualAddressGate("bounded_manual_effect_unobserved")


async def wait_for_billing_initialization(read_document, policy, *, deadline, on_event=None):
    """Passive readiness only; Test Mode badge never verifies payment/entitlement.

    Retained native source renders its Test badge after async country and mode
    initialization. DOMContentLoaded or a visible default country alone cannot
    establish that point. A fresh field read/approved selection is still needed.
    """
    loop = asyncio.get_running_loop()
    emit = on_event or (lambda event: None)
    original = None
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise ManualAddressGate("billing_initialization_unobserved")
        try:
            document = await asyncio.wait_for(read_document(), remaining)
        except TimeoutError:
            raise ManualAddressGate("billing_initialization_unobserved") from None
        policy.check_url(document["url"])
        if policy.provider != "dodo" or exact_origin(document["url"]) != DODO_CHECKOUT:
            raise ManualAddressGate("manual_origin_unapproved")
        binding = document["url"], document.get("generation")
        if original is None:
            original = binding
        elif binding != original:
            raise ManualAddressGate("manual_document_changed")
        ready = (
            document.get("hosted_test_mode_visible") is True
            and document.get("ready_state") in ("interactive", "complete")
            and document.get("body_present") is True and document.get("busy") is False
        )
        emit({"decision": "billing_initialization_read", "initialization_observed": ready,
              "generation_current": True})
        if ready:
            return document
        await asyncio.sleep(min(0.05, max(0, deadline - loop.time())))
