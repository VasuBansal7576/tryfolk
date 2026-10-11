"""Unit policy boundaries; these invented DOM controls are not live SDK evidence."""

import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.decisions import validated_action


def test_fresh_hosted_continue_control_is_bound_to_bounded_lifecycle_readiness():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = "https://test.checkout.dodopayments.com/session/fixture"
    control = {
        "tag": "BUTTON",
        "id": "step1",
        "label": "Continue to Payment",
        "destination": url,
        "visible": True,
        "disabled": False,
    }
    bound = policy.bind({"method": "click", "selector": "#step1"}, control, url)
    assert bound["_tryfolk"]["kind"] == "billing_continue"
    with pytest.raises(ValueError):
        policy.bind(
            {"method": "click", "selector": "#step1"},
            control | {"disabled": True, "label": "Processing..."},
            url,
        )


def test_only_observed_synthetic_card_fields_inside_exact_direct_secure_frame_are_allowed():
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    url = "https://test.checkout.dodopayments.com/payment"
    frame = "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html"
    selector = "xpath=/html[1]/body[1]/iframe[1]/html[1]/body[1]/input[1]"
    action = {"method": "fill", "selector": selector, "arguments": ["model-card"]}
    control = {
        "tag": "INPUT",
        "type": "text",
        "label": "Card number",
        "frame_url": frame,
        "destination": frame,
        "frame_generation": 12345,
        "frame_context_verified": True,
        "frame_config_id": "a" * 64,
    }
    bound = policy.bind(action, control, url)
    assert bound["arguments"] == ["4242424242424242"]
    assert bound["_tryfolk"]["frame_url"] == frame
    assert bound["_tryfolk"]["frame_generation"] == 12345
    assert bound["_tryfolk"]["frame_config_id"] == "a" * 64
    policy.validate_completion(bound, control | {"value": "4242 4242 4242 4242"}, url)
    for changes in (
        {"frame_url": frame + "?fixture=1"},
        {"frame_url": "https://js.stripe.com/v3/card.html"},
        {"frame_url": url},
        {"label": "Address Line"},
        {"type": "password"},
        {"frame_context_verified": False},
        {"frame_config_id": None},
    ):
        with pytest.raises(ValueError):
            policy.bind(action, control | changes, url)
    for changes in ({"method": "click"}, {"selector": selector.replace("/input[1]", "/iframe[1]/input[1]")}):
        with pytest.raises(ValueError):
            policy.bind(action | changes, control, url)
    with pytest.raises(ValueError):
        policy.bind(action, control, policy.origin)
    with pytest.raises(ValueError):
        policy.validate(bound | {"_tryfolk": bound["_tryfolk"] | {"frame_url": frame + "/extra"}}, url)
    with pytest.raises(ValueError):
        policy.validate_completion(
            bound, control | {"frame_generation": 54321, "value": "4242424242424242"}, url
        )
    with pytest.raises(ValueError):
        policy.validate_completion(
            bound, control | {"frame_config_id": "b" * 64, "value": "4242424242424242"}, url
        )


def test_exact_registered_origin_and_only_authorized_hosted_test_checkout():
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    for url in (
        "http://127.0.0.1:3301/target/login#token=opaque",
        "https://test.checkout.dodopayments.com/session/test",
    ):
        policy.check_url(url)
    for url in (
        "http://127.0.0.1:3300/",
        "https://checkout.dodopayments.com/session/test",
        "https://test.dodopayments.com/",
        "https://test.checkout.dodopayments.com.evil/session/test",
        "https://u:p@test.checkout.dodopayments.com/session/test",
        "https://test.checkout.dodopayments.com:8443/session/test",
        "javascript:alert(1)",
    ):
        with pytest.raises(ValueError):
            policy.check_url(url)
    fixture = BrowserPolicy("http://127.0.0.1:3101", "local-fixture")
    with pytest.raises(ValueError):
        fixture.check_url("https://test.checkout.dodopayments.com/session/test")


def test_observed_field_uses_official_synthetic_value_and_cannot_accept_model_arguments():
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    url = "https://test.checkout.dodopayments.com/session/test"
    action = {
        "method": "fill",
        "selector": "#card",
        "arguments": ["model-chosen-card"],
        "description": "Card number",
    }
    bound = policy.bind(
        action, {"tag": "INPUT", "type": "text", "autocomplete": "cc-number", "destination": url}, url
    )
    assert bound["arguments"] == ["4242424242424242"]
    assert action["arguments"] == ["model-chosen-card"]
    observed = [{"id": "fresh:0", "action": bound}]
    assert validated_action({"type": "BROWSER", "target": "fresh:0"}, observed, policy=policy) == bound
    with pytest.raises(ValueError):
        validated_action({"type": "BROWSER", "target": "old:0"}, observed, policy=policy)
    forged = {**bound, "arguments": ["4111111111111111"]}
    with pytest.raises(ValueError):
        policy.validate(forged, url)
    for control in ({"tag": "INPUT", "type": "password"}, {"tag": "TEXTAREA", "label": "Paste API secret"}):
        with pytest.raises(ValueError):
            policy.bind(action, {**control, "destination": url}, url)
    with pytest.raises(ValueError):
        policy.bind(
            action, {"tag": "INPUT", "autocomplete": "cc-number", "destination": policy.origin}, policy.origin
        )


def test_single_fresh_click_and_bounded_dropdown_are_distinct_from_download_evidence():
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    url = "https://test.checkout.dodopayments.com/session/test"
    selected = policy.bind(
        {"method": "selectOption", "selector": "#country", "arguments": ["IN"]},
        {"tag": "SELECT", "autocomplete": "country", "options": ["IN", "US"], "destination": url},
        url,
    )
    assert selected["arguments"] == ["US"]
    with pytest.raises(ValueError):
        policy.bind(
            {"method": "selectOption", "selector": "#country"},
            {"tag": "SELECT", "autocomplete": "country", "options": ["IN"], "destination": url},
            url,
        )
    action = policy.bind(
        {"method": "click", "selector": "#download", "arguments": []},
        {"destination": policy.origin + "/api/target/download?artwork=0", "download": "art.svg"},
        policy.origin,
    )
    assert action["_tryfolk"]["capability"] is True
    assert "capability_available" not in action
    with pytest.raises(ValueError):
        policy.bind(
            {"method": "click", "selector": "#buy", "arguments": []},
            {"destination": "https://live.dodopayments.com/pay"},
            policy.origin,
        )


def test_primary_observed_address_line_binds_to_documented_synthetic_address():
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    url = "https://test.checkout.dodopayments.com/test"
    bound = policy.bind(
        {"method": "fill", "selector": "#address"},
        {"tag": "INPUT", "label": "Address Line", "type": "text"},
        url,
    )
    assert bound["arguments"] == ["123 Test Street"]


def test_completed_field_requires_the_actual_approved_value_allowing_card_display_spacing():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = "https://test.checkout.dodopayments.com/session/test"
    action = policy.bind(
        {"method": "fill", "selector": "#card"},
        {"tag": "INPUT", "autocomplete": "cc-number"},
        url,
    )
    policy.validate_completion(action, {"value": "4242 4242 4242 4242"}, url)
    with pytest.raises(ValueError, match="completed"):
        policy.validate_completion(action, {"value": "4242"}, url)
    selected = policy.bind(
        {"method": "selectOption", "selector": "#country"},
        {"tag": "SELECT", "autocomplete": "country", "options": ["IN", "US"]},
        url,
    )
    with pytest.raises(ValueError, match="completed"):
        policy.validate_completion(selected, {"value": "IN"}, url)
    policy.validate_completion(selected, {"value": "US"}, url)


def test_actual_stagehand_dropdown_method_binds_only_the_approved_present_option():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = "https://test.checkout.dodopayments.com/session/test"
    action = {"method": "selectOptionFromDropdown", "selector": "#country", "arguments": ["IN"]}
    control = {"tag": "SELECT", "label": "Country", "options": ["IN", "US"]}
    bound = policy.bind(action, control, url)
    assert bound["method"] == "selectOptionFromDropdown"
    assert bound["arguments"] == ["US"]
    policy.validate_completion(bound, {"value": "US"}, url)
    with pytest.raises(ValueError, match="completed"):
        policy.validate_completion(bound, {"value": "IN"}, url)
    with pytest.raises(ValueError, match="absent"):
        policy.bind(action, control | {"options": ["IN"]}, url)
    with pytest.raises(ValueError, match="unapproved"):
        policy.validate(bound | {"arguments": ["IN"]}, url)
