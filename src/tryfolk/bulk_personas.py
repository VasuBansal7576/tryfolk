"""Individually bound purchase intentions, never payment or fulfillment outcomes.

The executor must supply only freshly observed, policy-bound actions and retain
all native frame, checkout, customer, currency and financial-submit guards.
"""

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from typing import Literal

from .scenarios import digest, challenge_binding


class PurchaseIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase: StrictBool
    rationale: str = Field(min_length=1, max_length=1200)
    max_steps: StrictInt = Field(ge=1, le=48)
    unexpected_state: Literal["reconsider", "abandon"]


def validated_purchase_plan(persona):
    plan = persona.get("purchase_plan")
    challenge = challenge_binding(persona)
    challenge_keys = {"buyer_challenge_sha256"} if challenge is not None else set()
    if not isinstance(plan, dict) or set(plan) != challenge_keys | {
        "persona_id",
        "profile_sha256",
        "scenario_sha256",
        "purchase",
        "rationale",
        "max_steps",
        "unexpected_state",
        "source",
    }:
        raise ValueError("Complete individual purchase plan required.")
    if challenge is not None and plan["buyer_challenge_sha256"] != digest(challenge):
        raise ValueError("Individual purchase intention buyer challenge changed.")
    if plan["persona_id"] != persona.get("id") or plan["source"] != "genuine_codex_batch_intent":
        raise ValueError("Purchase plan identity or inference source changed.")
    if plan["profile_sha256"] != digest(persona.get("profile")):
        raise ValueError("Purchase plan profile binding changed.")
    scenario = persona.get("assigned_scenario", {}).get("scenario_snapshot")
    if scenario is None:
        scenario = persona.get("bulk_scenario_snapshot")
    if not scenario or plan["scenario_sha256"] != digest(scenario):
        raise ValueError("Purchase plan scenario binding changed.")
    PurchaseIntent.model_validate({k: plan[k] for k in PurchaseIntent.model_fields})
    return dict(plan)


def choose_planned_action(plan, observed_actions, executed_actions):
    """Choose an unchanged fresh observed action; no selectors/values are invented.

    None asks the executor to reconsider/abandon according to the saved intent.
    It is never a success/failure. A purchase intention does not arm native Pay.
    """
    intent = PurchaseIntent.model_validate({k: plan[k] for k in PurchaseIntent.model_fields})
    if not intent.purchase or len(executed_actions) >= intent.max_steps:
        return None
    fields = [
        "country",
        "address_query",
        "address",
        "city",
        "state",
        "postal",
        "card_number",
        "card_name",
        "card_expiry",
        "card_month",
        "card_year",
        "card_cvc",
    ]
    ranks = {
        "login": 0,
        "purchase": 1,
        "manual": 4,
        "billing_continue": 20,
        "pay": 40,
        "confirm": 41,
        "art_pack": 42,
        "download": 43,
    }
    field_rank = {field: index + 2 for index, field in enumerate(fields)}

    def key(action):
        metadata = action.get("_tryfolk", {})
        if metadata.get("diagnostic_manual"):
            return "manual"
        if metadata.get("field"):
            return "field:" + metadata["field"]
        return metadata.get("kind")

    used = {key(action) for action in executed_actions}
    candidates = []
    for index, action in enumerate(observed_actions):
        if not isinstance(action, dict) or not isinstance(action.get("_tryfolk"), dict):
            continue
        action_key = key(action)
        if action_key in used:
            continue
        if action_key and action_key.startswith("field:"):
            rank = field_rank.get(action_key[6:])
            if action.get("method") not in ("fill", "selectOptionFromDropdown"):
                continue
        else:
            rank = ranks.get(action_key)
            if action.get("method") != "click":
                continue
        if rank is not None:
            candidates.append((rank, index, action))
    return min(candidates, key=lambda candidate: candidate[:2])[2] if candidates else None
