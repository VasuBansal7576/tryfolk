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


def captured_navigation():
    navigation = CheckoutNavigation("cks_fixture", ENTRY, {"hide_order_details": False, "hide_on_demand_tag": False})
    assert navigation.observe_source(BILLING_SOURCE_SHA256, document())
    assert navigation.expect_continue(document())
    assert navigation.bind_submission(ORIGIN + "/api/checkout/sessions/cks_fixture/confirm", '{"mode":"test","data":{}}', document())
    assert navigation.observe_response(json.dumps({"payment_link": LINK}).encode(), document())
    return navigation


@pytest.mark.parametrize("mutation,expected", [
    (None, {}),
    ("query", {"derived_parent_equals_current_document": False, "derived_parent_query_equals_current": False,
               "derived_parent_query_pairs_equal_current": False, "derived_parent_query_order_equal_current": False}),
    ("encoding", {"derived_parent_equals_current_document": False, "derived_parent_query_equals_current": False}),
    ("order", {"derived_parent_equals_current_document": False, "derived_parent_query_equals_current": False,
               "derived_parent_query_order_equal_current": False}),
    ("path", {"derived_parent_equals_current_document": False, "derived_parent_path_equals_current": False}),
    ("generation", {"billing_generation_equals_current": False, "source_generation_equals_current": False}),
    ("independent_link", {"billing_response_link_equals_independent_link": False, "billing_response_path_equals_independent": False}),
    ("null_link", {"independent_payment_link_null": True, "independent_payment_link_valid": False, "billing_response_link_equals_independent_link": False}),
])
def test_parent_equality_facts_distinguish_structural_mismatches_without_admitting_them(mutation, expected):
    navigation = captured_navigation()
    current, independent = document(PARENT), LINK
    if mutation == "query":
        current["url"] += "&extra=yes"
    elif mutation == "encoding":
        current["url"] = PARENT.replace("owned+value", "owned%20value")
    elif mutation == "order":
        current["url"] = LINK + "?from=session&theme=light&trace=owned+value"
    elif mutation == "path":
        current["url"] = PARENT.replace("Private1", "Other111")
    elif mutation == "generation":
        current["generation"] = 2000
    elif mutation == "independent_link":
        independent = ORIGIN + "/Other111"
    elif mutation == "null_link":
        independent = None
    facts = navigation.parent_equality_facts(independent, current)
    baseline = {
        "independent_payment_link_null": False,
        "independent_payment_link_valid": True,
        "current_document_url_valid": True,
        "independent_link_equals_current_document": False,
        "owned_billing_transition_available": True,
        "billing_response_link_equals_independent_link": True,
        "billing_response_origin_equals_independent": True,
        "billing_response_path_equals_independent": True,
        "billing_response_query_equals_independent": True,
        "derived_parent_equals_current_document": True,
        "derived_parent_origin_equals_current": True,
        "derived_parent_scheme_equals_current": True,
        "derived_parent_netloc_equals_current": True,
        "derived_parent_path_equals_current": True,
        "derived_parent_query_equals_current": True,
        "derived_parent_query_pairs_equal_current": True,
        "derived_parent_query_order_equal_current": True,
        "billing_generation_equals_current": True,
        "source_generation_equals_current": True,
        "source_generation_equals_billing": True,
        "continue_generation_equals_billing": True,
        "submission_generation_equals_billing": True,
        "creation_customization_flags_available": True,
        "creation_customization_hides_order_details": False,
        "creation_customization_hides_on_demand_tag": False,
    }
    if mutation == "null_link":
        expected |= {"billing_response_origin_equals_independent": False, "billing_response_path_equals_independent": False,
                     "billing_response_query_equals_independent": False}
    assert facts == baseline | expected
    assert all(type(value) is bool for value in facts.values())
    assert not any(value in json.dumps(facts) for value in (ENTRY, LINK, "owned value", "Private1", "trace", "account"))
    # Component equality and timing are diagnostics, never route authority.
    assert all(navigation.parent_checks(independent, current).values()) is (mutation is None)


def test_absent_transition_has_no_vacuous_generation_or_component_equality():
    navigation = CheckoutNavigation("cks_fixture", ENTRY, None)
    facts = navigation.parent_equality_facts(None, {})
    assert facts["independent_payment_link_null"] is True
    assert all(value is False for key, value in facts.items() if key != "independent_payment_link_null")
    assert not any(navigation.parent_checks(None, {}).values())
