"""Invented routing inputs, never retained actual provider query values."""

import json

import pytest

from test_checkout_navigation import captured_navigation, document, PARENT, LINK, ENTRY


@pytest.mark.parametrize("change,first_fact", [
    ("from_value", "current_from_session_exact"),
    ("duplicate_from", "derived_current_from_values_equal"),
    ("missing_from", "current_from_present"),
    ("order_details", "derived_current_order_details_values_equal"),
    ("on_demand_tag", "derived_current_on_demand_tag_values_equal"),
    ("unknown_value", "current_original_unrelated_pairs_retained"),
    ("unknown_extra", "current_original_unrelated_pairs_retained"),
    ("unknown_order", "current_original_unrelated_pair_order_retained"),
    ("encoding", None),
    (None, None),
])
def test_query_diagnostics_identify_known_parameters_and_retention_without_values(change, first_fact):
    nav = captured_navigation()
    current = PARENT
    if change == "from_value":
        current = current.replace("from=session", "from=other")
    elif change == "duplicate_from":
        current += "&from=session"
    elif change == "missing_from":
        current = current.replace("&from=session", "")
    elif change == "order_details":
        current += "&showOrderDetails=false"
    elif change == "on_demand_tag":
        current += "&showOnDemandTag=false"
    elif change == "unknown_value":
        current = current.replace("owned+value", "unretained+value")
    elif change == "unknown_extra":
        current += "&unretained_parameter=unretained_value"
    elif change == "unknown_order":
        current = LINK + "?trace=owned+value&theme=light&from=session"
    elif change == "encoding":
        current = current.replace("owned+value", "owned%20value")
    facts = nav.parent_query_facts(document(current))
    checks, counts = facts["checks"], facts["counts"]
    assert all(type(value) is bool for value in checks.values())
    assert all(type(value) is int and value >= 0 for value in counts.values())
    assert checks["entry_query_available"] and checks["derived_query_available"] and checks["current_query_available"]
    assert not checks["entry_from_present"] and checks["derived_from_present"]
    assert checks["derived_from_session_exact"]
    assert checks["derived_original_unrelated_pairs_retained"]
    assert counts["entry_unrelated_pairs"] == counts["derived_unrelated_pairs"] == 2
    assert counts["current_unrelated_pairs"] == (3 if change == "unknown_extra" else 2)
    assert counts["current_unrelated_names"] == counts["current_unrelated_pairs"]
    if first_fact:
        assert checks[first_fact] is False
    if change == "order_details":
        assert checks["current_order_details_present"] and checks["current_order_details_explicit_false"]
    if change == "on_demand_tag":
        assert checks["current_on_demand_tag_present"] and checks["current_on_demand_tag_explicit_false"]
    if change in {None, "encoding"}:
        assert checks["current_original_unrelated_pair_order_retained"]
        assert checks["derived_current_from_values_equal"]
    serialized = json.dumps(facts)
    assert not any(value in serialized for value in (ENTRY, LINK, "trace", "theme", "owned value", "unretained"))
    # Even equivalent decoded values never admit a differently represented URL.
    assert all(nav.parent_checks(LINK, document(current)).values()) is (change is None)


def test_missing_or_invalid_query_documents_have_no_fabricated_presence_or_counts():
    nav = captured_navigation()
    facts = nav.parent_query_facts(document("https://foreign.invalid/Private1?from=session"))
    assert facts["checks"]["current_query_available"] is False
    assert facts["checks"]["current_from_present"] is False
    assert facts["checks"]["derived_current_from_values_equal"] is False
    assert facts["counts"]["current_unrelated_pairs"] == 0
    assert facts["counts"]["current_unrelated_names"] == 0
