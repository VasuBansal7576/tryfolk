"""Invented SDK/control fixtures; no browser or provider success is measured."""

from tryfolk.browser_policy import BrowserPolicy
from tryfolk.checkout_discovery import discover_checkout_candidates


URL = "https://test.checkout.dodopayments.com/session/fixture"


async def test_prefilled_country_does_not_wait_for_native_bootstrap_to_offer_city():
    controls = {
        "xpath=/html[1]/body[1]/select[1]": {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "US", "options": ["US", "CA"], "destination": URL,
        },
        "xpath=/html[1]/body[1]/input[1]": {
            "present": True, "visible": True, "tag": "INPUT", "label": "City",
            "value": "", "destination": URL,
        },
    }

    async def read(selector):
        return controls[selector]

    discovery = await discover_checkout_candidates(
        {"xpath_map": {str(i): selector.removeprefix("xpath=") for i, selector in enumerate(controls)}},
        read, BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
        filled={"address"}, completed_cards={}, clicked={"login", "purchase", "manual"},
        phase="primary_billing",
    )
    assert [(priority, action["_tryfolk"].get("field")) for priority, action in discovery.candidates] == [(5, "city")]
    facts = discovery.evidence(document_stable=True)
    assert facts["phase"] == "primary_billing"
    assert facts["observed_completion"]["country"] is True
    assert facts["committed_completion"]["country"] is True
    assert facts["rejection_counts"]["completed_field"] == 1
    assert facts["eligible_counts"]["city"] == 1


async def test_missing_approved_country_option_has_a_closed_reason_without_private_state():
    import json

    async def read(selector):
        return {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "private-value", "options": ["private-option"], "destination": URL,
        }

    discovery = await discover_checkout_candidates(
        {"xpath_map": {"private-identifier": "/html[1]/body[1]/select[1]"}},
        read, BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
        filled={"address"}, completed_cards={}, clicked={"manual"}, phase="primary_billing",
    )
    facts = discovery.evidence(document_stable=True)
    assert facts["rejection_counts"]["policy_synthetic_option_absent"] == 1
    assert facts["candidate_count"] == 0
    retained = json.dumps(facts)
    for private in ("private-", "xpath=", "Country", URL):
        assert private not in retained


async def test_discarded_primary_pass_does_not_commit_observed_country_or_card_bindings():
    original_filled, original_cards = {"address", "city", "state", "postal"}, {}

    async def read(selector):
        return {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "US", "options": ["US"], "destination": URL,
        }

    discovery = await discover_checkout_candidates(
        {"xpath_map": {"0": "/html[1]/body[1]/select[1]"}}, read,
        BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
        filled=original_filled, completed_cards=original_cards, clicked={"manual"},
        phase="primary_billing",
    )
    discarded = discovery.evidence(document_stable=False)
    assert discarded["observed_completion"]["country"] is True
    assert discarded["completion_after_pass"]["country"] is True
    assert discarded["committed_completion"]["country"] is False
    assert original_filled == {"address", "city", "state", "postal"}
    assert original_cards == {}


async def test_next_primary_pass_can_reobserve_country_and_offer_billing_continue():
    controls = {
        "xpath=/html[1]/body[1]/select[1]": {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "US", "options": ["US"], "destination": URL,
        },
        "xpath=/html[1]/body[1]/button[1]": {
            "present": True, "visible": True, "tag": "BUTTON", "id": "step1",
            "label": "Continue to Payment", "destination": URL,
        },
    }

    async def read(selector):
        return controls[selector]

    discovery = await discover_checkout_candidates(
        {"xpath_map": {str(i): selector.removeprefix("xpath=") for i, selector in enumerate(controls)}},
        read, BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
        filled={"address", "city", "state", "postal"}, completed_cards={}, clicked={"manual"},
        phase="primary_billing",
    )
    assert [(priority, action["_tryfolk"].get("kind")) for priority, action in discovery.candidates] == [(8, "billing_continue")]
    assert discovery.evidence(document_stable=True)["eligible_counts"]["billing_continue"] == 1


async def test_unready_billing_and_unsupported_contact_are_retained_without_broadening_policy():
    controls = {
        "xpath=/html[1]/body[1]/input[1]": {
            "present": True, "visible": True, "tag": "INPUT", "type": "email",
            "label": "Email", "value": "private-contact", "destination": URL,
        },
        "xpath=/html[1]/body[1]/button[1]": {
            "present": True, "visible": True, "tag": "BUTTON", "id": "step1",
            "label": "Continue to Payment", "destination": URL,
        },
    }

    async def read(selector):
        return controls[selector]

    discovery = await discover_checkout_candidates(
        {"xpath_map": {str(i): selector.removeprefix("xpath=") for i, selector in enumerate(controls)}},
        read, BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
        filled={"address"}, completed_cards={}, clicked={"manual"}, phase="primary_billing",
    )
    assert discovery.candidates == []
    facts = discovery.evidence(document_stable=True)
    assert facts["rejection_counts"]["policy_unsupported_field"] == 1
    assert facts["rejection_counts"]["billing_completion_missing"] == 1
    assert facts["committed_completion"]["country"] is False
    assert facts["committed_completion"]["address"] is True


async def test_default_india_offers_approved_us_selection_before_manual_address():
    controls = {
        "xpath=/html[1]/body[1]/input[1]": {
            "present": True, "visible": True, "disabled": True, "tag": "INPUT",
            "name": "fullName", "label": "Full Name", "value": "Tryfolk Synthetic Customer",
            "destination": URL,
        },
        "xpath=/html[1]/body[1]/input[2]": {
            "present": True, "visible": True, "disabled": True, "tag": "INPUT",
            "name": "email", "label": "Email", "value": "private-fixture-contact",
            "destination": URL,
        },
        "xpath=/html[1]/body[1]/select[1]": {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "IN", "options": ["IN", "US"], "destination": URL,
        },
        "xpath=/html[1]/body[1]/button[1]": {
            "present": True, "visible": True, "tag": "BUTTON",
            "label": "Enter address manually", "destination": URL,
        },
    }

    async def read(selector):
        return controls[selector]

    discovery = await discover_checkout_candidates(
        {"xpath_map": {str(i): selector.removeprefix("xpath=") for i, selector in enumerate(controls)}},
        read, BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
        filled=set(), completed_cards={}, clicked={"login", "purchase"}, phase="primary_billing",
    )
    priority, selected = min(discovery.candidates, key=lambda item: item[0])
    assert priority == 2
    assert selected["method"] == "selectOptionFromDropdown"
    assert selected["arguments"] == ["US"]
    assert selected["_tryfolk"]["field"] == "country"
    facts = discovery.evidence(document_stable=True)
    assert facts["observed_completion"]["country"] is False
    assert facts["committed_completion"]["country"] is False
    assert facts["eligible_counts"]["country"] == 1
    assert facts["rejection_counts"]["disabled_control"] == 2


async def test_stale_observed_country_completion_offers_current_approved_selection():
    async def read(selector):
        return {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "IN", "options": ["IN", "US"], "destination": URL,
        }

    discovery = await discover_checkout_candidates(
        {"xpath_map": {"0": "/html[1]/body[1]/select[1]"}}, read,
        BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
        filled={"country", "address"}, completed_cards={}, clicked={"manual"}, phase="primary_billing",
    )
    facts = discovery.evidence(document_stable=True)
    assert facts["observed_completion"]["country"] is False
    assert facts["rejection_counts"]["prior_completion_currently_unverified"] == 1
    assert facts["observed_fields"]["country"] is True
    assert facts["current_completion_conflicts"]["country"] is True
    assert facts["committed_completion"]["country"] is False
    assert [(priority, action["_tryfolk"]["field"]) for priority, action in discovery.candidates] == [(2, "country")]
    assert discovery.candidates[0][1]["arguments"] == ["US"]


async def test_changed_executed_country_stops_without_repeating_the_action():
    import pytest
    from tryfolk.checkout_discovery import ExecutedFieldChanged

    async def read(selector):
        return {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "IN", "options": ["IN", "US"], "destination": URL,
        }

    with pytest.raises(ExecutedFieldChanged) as stopped:
        await discover_checkout_candidates(
            {"xpath_map": {"0": "/html[1]/body[1]/select[1]"}}, read,
            BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
            filled={"country", "address"}, executed_fields={"country"},
            completed_cards={}, clicked={"manual"}, phase="primary_billing",
        )
    assert stopped.value.field == "country"
    assert stopped.value.discovery.evidence(document_stable=False)["current_completion_conflicts"]["country"] is True
    assert stopped.value.discovery.candidates == []
