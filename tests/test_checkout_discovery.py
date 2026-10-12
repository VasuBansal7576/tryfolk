"""Invented SDK/control fixtures; no browser or provider success is measured."""

import pytest
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


async def test_manual_effect_current_field_failure_retains_closed_diagnostics_without_repeating():
    """Invented changed-value I/O at the actual discovery/manual-effect seam.

    This locks down the diagnostic loss in retained234, not its unrecorded
    failed field or the provider's current DOM state.
    """
    import asyncio
    import json
    import pytest
    from tryfolk.checkout_discovery import ExecutedFieldChanged
    from tryfolk.manual_address import resolve_manual_address

    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    document = {"url": URL, "generation": 1, "body_present": True,
                "ready_state": "complete", "busy": False, "document_stable": True}
    manual = policy.bind(
        {"method": "click", "selector": "xpath=/html[1]/body[1]/button[1]", "arguments": []},
        {"present": True, "visible": True, "tag": "BUTTON", "label": "Enter address manually",
         "destination": URL}, URL,
    )
    repeats = []

    async def read_control(selector):
        return {"present": True, "visible": True, "tag": "SELECT", "label": "Country",
                "value": "private-changed-value", "options": ["US"], "destination": URL}

    async def read_current():
        discovery = await discover_checkout_candidates(
            {"xpath_map": {"0": "/html[1]/body[1]/select[1]"}}, read_control, policy, URL,
            filled={"country", "address"}, executed_fields={"country", "address"},
            completed_cards={}, clicked=set(), phase="primary_billing",
        )
        return discovery, document

    async def forbidden_repeat(action):
        repeats.append(action)
        raise AssertionError("A changed executed field must stop without another action")

    with pytest.raises(ExecutedFieldChanged) as stopped:
        await resolve_manual_address(
            read_current, forbidden_repeat, policy,
            deadline=asyncio.get_running_loop().time() + 1, executed=(manual, document),
        )
    facts = stopped.value.observation_evidence
    assert facts["failed_field"] == "country"
    assert facts["discovery_pass_complete"] is False
    assert facts["discovery"]["current_completion_conflicts"]["country"] is True
    assert facts["discovery"]["observed_completion"]["country"] is False
    assert repeats == []
    retained = json.dumps(facts)
    assert all(private not in retained for private in ("private-", "xpath=", URL, "Country"))


async def test_manual_mode_after_address_search_requires_the_real_billing_address():
    """Offline transition from the retained234 public renderer, not live DOM.

    Source594824... has H's unnamed role=combobox query/value:u with
    autocomplete=off, separate from form addressLine/address-line1. Opening
    manual mode hides H and reveals that initially empty form field.
    """
    import asyncio
    from tryfolk.manual_address import resolve_manual_address

    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    query = {"present": True, "visible": True, "tag": "INPUT", "type": "text", "label": "Address Line",
             "placeholder": "Address Line", "autocomplete": "off", "name": "",
             "role": "combobox", "aria_autocomplete": "list", "destination": URL}
    search = policy.bind({"method": "fill", "selector": "xpath=/html[1]/body[1]/input[1]",
                          "arguments": []}, query, URL)
    # The successful bounded search fill is not a committed billing address.
    policy.validate_completion(search, query | {"value": search["arguments"][0]}, URL)
    completed = {"country", search["_tryfolk"]["field"]}
    controls = {
        "xpath=/html[1]/body[1]/select[1]": {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "US", "options": ["US"], "destination": URL,
        },
    }
    for index, (name, autocomplete) in enumerate(
        [("addressLine", "address-line1"), ("city", "address-level2"),
         ("state", "address-level1"), ("zipCode", "postal-code")], start=1,
    ):
        controls[f"xpath=/html[1]/body[1]/input[{index}]"] = {
            "present": True, "visible": True, "tag": "INPUT", "name": name,
            "autocomplete": autocomplete, "value": "", "destination": URL,
        }
    document = {"url": URL, "generation": 1, "body_present": True,
                "ready_state": "complete", "busy": False, "document_stable": True}
    manual = policy.bind(
        {"method": "click", "selector": "xpath=/html[1]/body[1]/button[1]", "arguments": []},
        {"present": True, "visible": True, "tag": "BUTTON", "label": "Enter address manually",
         "destination": URL}, URL,
    )

    async def read_current():
        async def read_control(selector):
            return controls[selector]

        discovery = await discover_checkout_candidates(
            {"xpath_map": {str(i): s.removeprefix("xpath=") for i, s in enumerate(controls)}},
            read_control, policy, URL, filled=completed, executed_fields=completed,
            completed_cards={}, clicked=set(), phase="primary_billing",
        )
        return discovery, document

    async def forbidden_repeat(_):
        raise AssertionError("Opening manual fields must not repeat the click")

    discovery, _ = await resolve_manual_address(
        read_current, forbidden_repeat, policy, deadline=asyncio.get_running_loop().time() + 1,
        executed=(manual, document),
    )
    assert search["_tryfolk"]["field"] == "address_query"
    assert "address" not in discovery.filled
    assert discovery.observed_completion == {"country"}
    assert {a["_tryfolk"]["field"] for _, a in discovery.candidates} == {
        "address", "city", "state", "postal",
    }


@pytest.mark.parametrize("labels", [
    ["Enter address manually"], ["Enter manually"],
    ["Enter address manually", "Enter manually"],
])
async def test_current_open_manual_form_resolves_with_residual_affordances_without_acting(labels):
    """Invented I/O exercises real discovery and resolver; no live checkout claim."""
    import asyncio
    from tryfolk.manual_address import resolve_manual_address

    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    controls = {
        "xpath=/html[1]/body[1]/select[1]": {
            "present": True, "visible": True, "tag": "SELECT", "label": "Country",
            "value": "US", "options": ["US"], "destination": URL,
        },
    }
    for index, (name, autocomplete) in enumerate([
        ("addressLine", "address-line1"), ("city", "address-level2"),
        ("state", "address-level1"), ("zipCode", "postal-code"),
    ], start=1):
        controls[f"xpath=/html[1]/body[1]/input[{index}]"] = {
            "present": True, "visible": True, "disabled": False, "required": True,
            "tag": "INPUT", "name": name, "autocomplete": autocomplete,
            "value": "", "destination": URL,
        }
    for index, label in enumerate(labels, start=1):
        controls[f"xpath=/html[1]/body[1]/button[{index}]"] = {
            "present": True, "visible": True, "tag": "BUTTON", "label": label,
            "destination": URL,
        }

    async def read_control(selector):
        return controls[selector]

    discovery = await discover_checkout_candidates(
        {"xpath_map": {str(i): s.removeprefix("xpath=") for i, s in enumerate(controls)}},
        read_control, policy, URL, filled={"country"}, executed_fields={"country"},
        completed_cards={}, clicked=set(), phase="primary_billing",
    )
    assert {"city", "state", "postal"} <= discovery.observed_fields
    assert discovery.observed_manual_variants
    assert discovery.rejection_counts["manual_fields_already_visible"] == len(labels)
    assert not any(a["_tryfolk"].get("diagnostic_manual") for _, a in discovery.candidates)
    document = {"url": URL, "generation": 1, "document_stable": True,
                "ready_state": "complete", "body_present": True, "busy": False}

    async def read_current():
        return discovery, document

    async def no_action(_):
        raise AssertionError("An open form must never click a residual affordance")

    result, _ = await resolve_manual_address(
        read_current, no_action, policy, source_sha256=None,
        deadline=asyncio.get_running_loop().time() + 1,
    )
    assert result is discovery
    # Visible empty required fields establish the mode, not completed billing.
    assert result.observed_completion == {"country"}
    assert {a["_tryfolk"]["field"] for _, a in result.candidates} == {
        "address", "city", "state", "postal",
    }


async def test_real_executed_billing_address_change_still_stops_manual_effect_verification():
    import pytest
    from tryfolk.checkout_discovery import ExecutedFieldChanged

    async def read_control(_):
        return {"present": True, "visible": True, "tag": "INPUT", "name": "addressLine",
                "autocomplete": "address-line1", "value": "private-changed-value", "destination": URL}

    with pytest.raises(ExecutedFieldChanged) as stopped:
        await discover_checkout_candidates(
            {"xpath_map": {"0": "/html[1]/body[1]/input[1]"}}, read_control,
            BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
            filled={"country", "address"}, executed_fields={"address"},
            completed_cards={}, clicked=set(), phase="primary_billing",
        )
    assert stopped.value.field == "address"
    assert stopped.value.observation_evidence["discovery"]["current_completion_conflicts"]["address"]


@pytest.mark.parametrize("identity", [
    {"autocomplete": "address-line1", "name": "", "id": ""},
    {"autocomplete": "off", "name": "addressLine", "id": ""},
    {"autocomplete": "off", "name": "", "id": "billingAddress"},
])
async def test_changed_executed_real_address_combobox_stops_before_pay(identity):
    """An explicit billing field stays authoritative despite search-like ARIA."""
    from tryfolk.checkout_discovery import ExecutedFieldChanged

    reads = []
    controls = {
        "xpath=/html[1]/body[1]/input[1]": {
            "present": True, "visible": True, "tag": "INPUT", "type": "text",
            "label": "Address Line", "role": "combobox", "aria_autocomplete": "list",
            "value": "private-changed-value", "destination": URL, **identity,
        },
        "xpath=/html[1]/body[1]/button[1]": {
            "present": True, "visible": True, "tag": "BUTTON", "label": "Pay",
            "destination": URL,
        },
    }

    async def read_control(selector):
        reads.append(selector)
        return controls[selector]

    with pytest.raises(ExecutedFieldChanged) as stopped:
        await discover_checkout_candidates(
            {"xpath_map": {str(i): s.removeprefix("xpath=") for i, s in enumerate(controls)}},
            read_control, BrowserPolicy("http://127.0.0.1:3300", "dodo"), URL,
            filled={"country", "address", "city", "state", "postal",
                    "card_number", "card_expiry", "card_cvc"},
            executed_fields={"address"}, completed_cards={}, clicked=set(), phase="primary_billing",
        )
    assert stopped.value.field == "address"
    assert stopped.value.discovery.candidates == []
    assert stopped.value.discovery.current_completion_conflicts == {"address"}
    assert reads == ["xpath=/html[1]/body[1]/input[1]"]
