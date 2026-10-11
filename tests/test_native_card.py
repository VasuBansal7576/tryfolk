"""Closed request capability with invented external browser/provider observations."""

import json
import pytest
from types import SimpleNamespace
from urllib.parse import urlencode
from tryfolk.native_card import NativeCardAttempt

API = "https://api.custom.hs.dodopayments.com"
FRAME = "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html"
KEY, SECRET = "pk_snd_fixture", "pay_fixture_secret_ABC123"
RAW = FRAME + "?" + urlencode({"publishableKey": KEY, "clientSecret": SECRET, "apiUrl": API}) + "#" + urlencode({"parentOrigin": "https://test.checkout.dodopayments.com"})
RETURN = "http://127.0.0.1:3300/checkout/return"
INFO = {"user_agent": "fixture UA", "accept_header": "fixture Accept", "language": "en-US", "color_depth": 24,
        "screen_height": 800, "screen_width": 1280, "time_zone": 0, "java_enabled": True,
        "java_script_enabled": True, "device_model": "fixture device", "os_type": "fixture OS", "os_version": "1"}


def attempt(clock=lambda: 10):
    return NativeCardAttempt(RAW, 1234, "http://127.0.0.1:3300", INFO, {}, clock=clock)


def confirmation():
    body = {"payment_type": "normal", "payment_method": "card", "client_secret": SECRET,
            "return_url": RETURN, "browser_info": INFO,
            "payment_method_data": {"card": {"card_number": "4242424242424242", "card_exp_month": "06",
                                            "card_exp_year": "2032", "card_cvc": "123", "card_issuer": "",
                                            "card_network": "Visa"}}}
    request = SimpleNamespace(url=API + "/payments/pay_fixture/confirm", method="POST", resource_type="fetch",
                              post_data=json.dumps(body), redirected_from=None, is_navigation_request=lambda: False)
    headers = {"api-key": KEY, "origin": "https://sdk-v2.custom.hs.dodopayments.com",
               "content-type": "application/json", "x-client-version": "0.1.0", "x-payment-confirm-source": "sdk",
               "x-client-platform": "web", "x-merchant-domain": "test.checkout.dodopayments.com",
               "x-browser-name": "Others", "x-browser-version": "0",
               "x-client-source": "Payment"}
    return request, headers


def test_one_normal_card_confirm_requires_one_current_native_pay_and_exact_observed_return():
    capability = attempt()
    request, headers = confirmation()
    assert not capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    facts = capability.check(request, headers, RAW, 1234, RETURN)
    assert facts["config_bound"] and facts["operation"] == "confirm"
    assert capability.consume("confirm")
    assert not capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]


@pytest.mark.parametrize("mutation", ["card", "secret", "return", "fingerprint", "wallet", "saved", "retry", "mandate", "key", "cookie", "parent_config", "generation"])
def test_normal_card_request_rejects_every_other_payment_shape_or_changed_private_context(mutation):
    capability = attempt()
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    request, headers = confirmation()
    body, raw, generation = json.loads(request.post_data), RAW, 1234
    if mutation == "card":
        body["payment_method_data"]["card"]["card_number"] = "4000000000000002"
    elif mutation == "secret":
        body["client_secret"] = "pay_other_secret_ABC"
    elif mutation == "return":
        body["return_url"] = "https://processor.example/challenge"
    elif mutation == "fingerprint":
        body["browser_info"]["user_agent"] = "other UA"
    elif mutation == "wallet":
        body["payment_method"] = "wallet"
    elif mutation in ("saved", "retry", "mandate"):
        body[{"saved": "payment_token", "retry": "retry_action", "mandate": "new_mandate"}[mutation]] = "fixture"
    elif mutation == "key":
        headers["api-key"] = "pk_prd_fixture"
    elif mutation == "cookie":
        headers["cookie"] = "fixture"
    elif mutation == "parent_config":
        raw += "&changed=fixture"
    else:
        generation += 1
    request.post_data = json.dumps(body)
    assert not capability.check(request, headers, raw, generation, RETURN)["config_bound"]


def test_poll_read_count_and_deadline_include_fallback_and_never_permit_new_confirmation():
    now = [10]
    capability = attempt(lambda: now[0])
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    request, headers = confirmation()
    assert capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
    assert capability.consume("confirm")
    headers.pop("x-client-source")
    request.url = API + "/payments/pay_fixture?" + urlencode({"force_sync": "true", "client_secret": SECRET})
    request.method, request.post_data = "GET", None
    for _ in range(25):
        assert capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
        assert capability.consume("poll")
    assert not capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
    second = attempt(lambda: now[0])
    second.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    assert second.consume("confirm")
    now[0] = 310
    assert not second.check(request, headers, RAW, 1234, RETURN)["config_bound"]
    assert not second.consume("poll")


def test_native_pay_authority_expires_while_independent_confirmation_verification_is_in_progress():
    now = [10]
    capability = attempt(lambda: now[0])
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    now[0] = 71
    assert not capability.consume("confirm")


def test_confirm_transport_failure_allows_only_one_native_fallback_read_and_ir16_allows_none():
    capability = attempt()
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    assert capability.consume("confirm")
    capability.observe_response(None, 500, operation="confirm")
    request, headers = confirmation()
    headers.pop("x-client-source")
    request.url = API + "/payments/pay_fixture?" + urlencode({"force_sync": "true", "client_secret": SECRET})
    request.method, request.post_data = "GET", None
    assert capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
    assert capability.consume("poll")
    assert not capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
    capability.observe_response("processing", 200)
    # The fallback response enters ordinary polling, retaining the aggregate
    # ceiling rather than granting a second error fallback or confirmation.
    for _ in range(24):
        assert capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
        assert capability.consume("poll")
        capability.observe_response("processing", 200)
    assert capability.polls == 25
    assert not capability.check(request, headers, RAW, 1234, RETURN)["config_bound"]
    assert not capability.consume("poll")
    assert not capability.consume("confirm")
    blocked = attempt()
    blocked.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    assert blocked.consume("confirm")
    blocked.observe_response(None, 400, operation="confirm", error_code="IR_16")
    assert not blocked.check(request, headers, RAW, 1234, RETURN)["config_bound"]


@pytest.mark.parametrize("status,http", [("succeeded", 200), ("failed", 200), (None, 500)])
def test_confirm_error_fallback_terminal_or_error_response_closes_the_attempt(status, http):
    capability = attempt()
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    assert capability.consume("confirm")
    capability.observe_response(None, 500, operation="confirm")
    assert capability.consume("poll")
    capability.observe_response(status, http)
    assert capability.closed
    assert not capability.consume("poll")


def test_payment_id_in_an_unknown_provider_route_does_not_grant_a_challenge_or_return_path():
    capability = attempt()
    assert not capability.return_allowed("https://test.checkout.dodopayments.com/challenge/pay_fixture/3ds")
    assert not capability.return_allowed("https://test.checkout.dodopayments.com/pay_other")
    assert not capability.return_allowed(RETURN + "?payment_id=pay_other")


@pytest.mark.parametrize("placeholder", ["{PAYMENT_ID}", "%7BPAYMENT_ID%7D"])
def test_native_pay_return_template_expands_only_the_same_current_payment(placeholder):
    capability = attempt()
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    request, headers = confirmation()
    body = json.loads(request.post_data)
    body["return_url"] = "https://test.checkout.dodopayments.com/pay_fixture"
    request.post_data = json.dumps(body)
    observed_template = "https://test.checkout.dodopayments.com/" + placeholder
    facts = capability.check(request, headers, RAW, 1234, observed_template)
    assert facts["config_bound"] and facts["native_return_template_expanded"]
    body["return_url"] = "https://test.checkout.dodopayments.com/pay_other"
    request.post_data = json.dumps(body)
    assert not capability.check(request, headers, RAW, 1234, observed_template)["config_bound"]
    body["return_url"] = "https://test.checkout.dodopayments.com/challenge/pay_fixture"
    request.post_data = json.dumps(body)
    assert not capability.check(request, headers, RAW, 1234, "https://test.checkout.dodopayments.com/challenge/" + placeholder)["config_bound"]


@pytest.mark.parametrize("mutation,predicate", [
    ("fingerprint", "browser_info_bound"), ("secret", "client_secret_bound"),
    ("card", "test_card_number_bound"), ("return", "native_return_bound"),
    ("extra", "body_fields_closed"), ("header", "header_x_client_source_bound"),
    ("generation", "current_child_configuration"),
])
def test_denial_reports_first_named_binding_predicate_without_private_values(mutation, predicate):
    capability = attempt()
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    request, headers = confirmation()
    body = json.loads(request.post_data)
    generation = 1234
    if mutation == "fingerprint":
        body["browser_info"]["user_agent"] = "unknown private fingerprint"
    if mutation == "secret":
        body["client_secret"] = "unknown private secret"
    if mutation == "card":
        body["payment_method_data"]["card"]["card_number"] = "unknown private card"
    if mutation == "return":
        body["return_url"] = "https://unknown.invalid/private-return"
    if mutation == "extra":
        body["retry_action"] = "manual_retry"
    if mutation == "header":
        headers["x-client-source"] = "unknown private header"
    if mutation == "generation":
        generation += 1
    request.post_data = json.dumps(body)
    facts = capability.check(request, headers, RAW, generation, RETURN)
    assert not facts["config_bound"]
    assert facts["first_failed_predicate"] == predicate
    assert facts["binding_checks"][predicate] is False
    encoded = json.dumps(facts)
    assert "unknown private" not in encoded and "private-return" not in encoded
    assert KEY not in encoded and SECRET not in encoded and "4242424242424242" not in encoded


@pytest.mark.parametrize("mutation,predicate", [
    ("card", "test_card_number_bound"), ("card_shape", "card_fields_closed"),
    ("billing", "billing_email_bound"), ("billing_shape", "billing_fields_closed"),
    ("acceptance", "customer_acceptance_online_bound"),
    ("acceptance_time", "customer_acceptance_time_bound"),
    ("acceptance_shape", "customer_acceptance_fields_closed"),
])
def test_independent_body_diagnostics_keep_return_first_and_all_malformed_fields_denied(mutation, predicate):
    from datetime import datetime, timezone

    capability = attempt()
    capability.billing = {"email": "synthetic_fixture@tryfolk.invalid"}
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    request, headers = confirmation()
    body = json.loads(request.post_data)
    observed = "https://test.checkout.dodopayments.com/challenge/pay_fixture"
    body["return_url"] = observed
    body["payment_method_data"]["billing"] = {"email": capability.billing["email"]}
    body["customer_acceptance"] = {"acceptance_type": "online", "accepted_at": datetime.now(timezone.utc).isoformat(), "online": {"user_agent": INFO["user_agent"]}}
    if mutation == "card":
        body["payment_method_data"]["card"]["card_number"] = "private changed card"
    elif mutation == "card_shape":
        body["payment_method_data"]["card"] = None
    elif mutation == "billing":
        body["payment_method_data"]["billing"]["email"] = "private changed email"
    elif mutation == "billing_shape":
        body["payment_method_data"]["billing"] = ["private malformed billing"]
    elif mutation == "acceptance":
        body["customer_acceptance"]["online"] = {"user_agent": "private changed agent"}
    elif mutation == "acceptance_time":
        body["customer_acceptance"]["accepted_at"] = None
    else:
        body["customer_acceptance"] = "private malformed acceptance"
    request.post_data = json.dumps(body)
    facts = capability.check(request, headers, RAW, 1234, observed)
    assert not facts["config_bound"] and facts["first_failed_predicate"] == "return_destination_approved"
    assert facts["binding_checks"][predicate] is False
    assert capability.return_url is None and not capability.confirmed
    assert "private " not in json.dumps(facts)
    # With an approved return, that independent failing field still denies.
    body["return_url"] = RETURN
    request.post_data = json.dumps(body)
    facts = capability.check(request, headers, RAW, 1234, RETURN)
    assert not facts["config_bound"] and facts["first_failed_predicate"] == predicate


def test_return_denial_retains_independent_card_billing_acceptance_booleans_without_admission():
    from datetime import datetime, timezone

    capability = attempt()
    capability.billing = {"line1": "123 Test Street", "email": "synthetic_fixture@tryfolk.invalid"}
    capability.arm_native_pay(RAW, 1234, normal_bootstrap=True)
    request, headers = confirmation()
    body = json.loads(request.post_data)
    observed = "https://test.checkout.dodopayments.com/challenge/pay_fixture"
    body["return_url"] = observed
    body["payment_method_data"]["billing"] = {"address": {"line1": "123 Test Street"}, "email": capability.billing["email"]}
    body["customer_acceptance"] = {"acceptance_type": "online", "accepted_at": datetime.now(timezone.utc).isoformat(), "online": {"user_agent": INFO["user_agent"]}}
    request.post_data = json.dumps(body)
    facts = capability.check(request, headers, RAW, 1234, observed)
    assert not facts["config_bound"] and facts["first_failed_predicate"] == "return_destination_approved"
    checks = facts["binding_checks"]
    assert checks["test_card_number_bound"] and checks["test_card_expiry_bound"] and checks["test_card_cvc_bound"]
    assert checks["billing_email_bound"] and checks["billing_line1_bound"]
    assert checks["customer_acceptance_online_bound"] and checks["customer_acceptance_time_bound"]
    assert not capability.confirmed and capability.return_url is None
    encoded = json.dumps(facts)
    assert capability.billing["email"] not in encoded and "123 Test Street" not in encoded
    assert KEY not in encoded and SECRET not in encoded and "4242424242424242" not in encoded
    body["return_url"] = RETURN
    request.post_data = json.dumps(body)
    approved = capability.check(request, headers, RAW, 1234, RETURN)
    assert approved["config_bound"] and approved["first_failed_predicate"] is None
    assert all(approved["binding_checks"].values())
    assert not capability.confirmed  # Diagnostic validation does not consume.
