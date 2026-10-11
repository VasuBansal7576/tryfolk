"""Invented external routing I/O, never provider payment evidence."""
import json
import pytest

from tryfolk.checkout_navigation import CheckoutNavigation, BILLING_SOURCE_SHA256

ORIGIN = "https://test.checkout.dodopayments.com"
ENTRY = ORIGIN + "/session/cks_fixture?theme=light&trace=owned%20value"
LINK = ORIGIN + "/Private1"
PARENT = LINK + "?theme=light&trace=owned+value&from=session"


def document(url=ENTRY, generation=1000):
    return {"url": url, "generation": generation, "body_present": True, "ready_state": "complete"}


def test_owned_session_response_and_authenticated_payment_resolve_distinct_parent():
    navigation = CheckoutNavigation("cks_fixture", ENTRY, {"hide_order_details": False, "hide_on_demand_tag": False})
    navigation.observe_source(BILLING_SOURCE_SHA256, document())
    assert navigation.expect_continue(document())
    assert navigation.bind_submission(ORIGIN + "/api/checkout/sessions/cks_fixture/confirm", '{"mode":"test","data":{}}', document())
    assert navigation.observe_response(json.dumps({"payment_link": LINK}).encode(), document())
    checks = navigation.parent_checks(LINK, document(PARENT))
    assert all(checks.values())
    assert navigation.entry_url == ENTRY
    assert "owned value" not in json.dumps(checks)
    assert not all(navigation.parent_checks(ORIGIN + "/Other111", document(PARENT)).values())
    assert not all(navigation.parent_checks(LINK, document(PARENT + "&extra=yes")).values())
    assert not all(navigation.parent_checks(LINK, document(PARENT, 2000)).values())


@pytest.mark.parametrize("mutation", ["no_source", "unknown_source", "stale_source", "no_continue", "wrong_session", "changed_entry", "live_mode", "extra_body", "null_link", "foreign_link", "userinfo", "query_link", "long_alias", "unknown_customization", "stale_response"])
def test_transition_requires_current_owned_request_source_and_exact_safe_response(mutation):
    navigation = CheckoutNavigation("cks_fixture", ENTRY, {"hide_order_details": False, "hide_on_demand_tag": False})
    if mutation != "no_source":
        navigation.observe_source("0" * 64 if mutation == "unknown_source" else BILLING_SOURCE_SHA256,
                                  document(generation=2000 if mutation == "stale_source" else 1000))
    if mutation != "no_continue":
        navigation.expect_continue(document())
    url = ORIGIN + "/api/checkout/sessions/" + ("cks_other" if mutation == "wrong_session" else "cks_fixture") + "/confirm"
    body = {"mode": "live" if mutation == "live_mode" else "test", "data": {}}
    if mutation == "extra_body":
        body["other"] = True
    bound = navigation.bind_submission(url, json.dumps(body), document(ENTRY + "&other=1" if mutation == "changed_entry" else ENTRY))
    link = {"null_link": None, "foreign_link": "https://checkout.dodopayments.com/Private1",
            "userinfo": "https://user@test.checkout.dodopayments.com/Private1",
            "query_link": LINK + "?unknown=1", "long_alias": ORIGIN + "/prefixPrivate1"}.get(mutation, LINK)
    if mutation == "unknown_customization":
        navigation.customization = None
    captured = navigation.observe_response(json.dumps({"payment_link": link}).encode(), document(generation=2000 if mutation == "stale_response" else 1000))
    assert not captured
    assert not all(navigation.parent_checks(LINK, document(PARENT)).values())
    if mutation in {"wrong_session", "changed_entry", "live_mode", "extra_body", "no_continue"}:
        assert not bound


@pytest.mark.parametrize("link", [None, "", "https://checkout.dodopayments.com/Private1", LINK + "#private", "https://user@test.checkout.dodopayments.com/Private1", "https://test.checkout.dodopayments.com\\@foreign.invalid/Private1"])
def test_missing_or_foreign_authenticated_link_never_admits_parent(link):
    navigation = CheckoutNavigation("cks_fixture", ENTRY, None)
    assert not any(navigation.parent_checks(link, document(link)).values())


def test_full_authenticated_link_is_preferred_without_guessing_route_alias():
    navigation = CheckoutNavigation("cks_fixture", ENTRY, None)
    assert all(navigation.parent_checks(LINK, document(LINK, 2000)).values())
    assert not all(navigation.parent_checks(LINK, document(ORIGIN + "/Other111")).values())


def test_captured_customization_and_query_set_use_complete_source_order():
    entry = ORIGIN + "/session/cks_fixture?from=old&from=other&trace=%7E%2A&showOrderDetails=true"
    navigation = CheckoutNavigation("cks_fixture", entry, {"hide_order_details": True, "hide_on_demand_tag": True})
    navigation.observe_source(BILLING_SOURCE_SHA256, document(entry))
    assert navigation.expect_continue(document(entry))
    assert navigation.bind_submission(ORIGIN + "/api/checkout/sessions/cks_fixture/confirm", '{"mode":"test","data":{}}', document(entry))
    assert navigation.observe_response(json.dumps({"payment_link": LINK}).encode(), document(entry))
    expected = LINK + "?from=session&trace=%7E*&showOrderDetails=false&showOnDemandTag=false"
    assert all(navigation.parent_checks(LINK, document(expected)).values())
    assert not all(navigation.parent_checks(LINK, document(expected + "&from=session")).values())
