"""Replay policy tests use invented external observations, never measured checkout evidence."""

import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.dodo_replay import rebind_action, failed_persona


def test_replay_rebinds_recorded_intent_to_fresh_observed_identity_and_never_old_selector():
    policy = BrowserPolicy("http://127.0.0.1:3310", "dodo")
    url = "https://test.checkout.dodopayments.com/new-checkout"
    old = {
        "method": "fill",
        "selector": "#old-card",
        "arguments": ["old"],
        "_tryfolk": {"url": "https://test.checkout.dodopayments.com/old", "field": "card_number"},
    }
    fresh = policy.bind(
        {"method": "fill", "selector": "#fresh-card"}, {"tag": "INPUT", "autocomplete": "cc-number"}, url
    )
    assert rebind_action(old, [fresh], policy, url) == fresh
    with pytest.raises(ValueError):
        rebind_action(old, [], policy, url)
    with pytest.raises(ValueError):
        rebind_action(old, [fresh, {**fresh, "selector": "#other"}], policy, url)
    with pytest.raises(ValueError):
        rebind_action(old, [fresh], policy, "https://test.checkout.dodopayments.com/stale")


def test_replay_matches_semantic_clicks_with_fixed_description_fallback_and_actual_failed_persona():
    policy = BrowserPolicy("http://127.0.0.1:3310", "dodo")
    old = {
        "method": "click",
        "selector": "#old",
        "description": "Confirm purchase",
        "_tryfolk": {"url": "http://127.0.0.1:3301/checkout/return", "kind": "confirm"},
    }
    fresh = policy.bind(
        {"method": "click", "selector": "#new", "description": "Confirm purchase"},
        {"id": "confirm-art-pack"},
        policy.origin + "/checkout/return",
    )
    assert rebind_action(old, [fresh], policy, fresh["_tryfolk"]["url"]) == fresh
    population = [
        {"id": "first", "conditions": {"device": "desktop"}},
        {"id": "failed", "conditions": {"device": "desktop", "locale": "en-US"}},
    ]
    before = {
        "outcomes": {"failed": {"outcome": "failed", "classification": "paid_no_access", "actions": [old]}}
    }
    persona, result = failed_persona(before, population)
    assert persona == population[1] and result["actions"] == [old]
    with pytest.raises(ValueError):
        failed_persona(before, population[:1])
