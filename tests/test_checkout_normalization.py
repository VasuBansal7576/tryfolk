"""Invented source/browser storage I/O, not actual8 storage or payment proof."""

import json

import pytest

from test_checkout_navigation import captured_navigation, document, ENTRY, LINK

SOURCE = "174be265126b29a5d7344bb545876bf50ba35f8bf816bc9858db998ef1ced24f"
KEY = "sess_Abc123xyZ9"
PARENT = LINK + "?session=" + KEY


def normalized_document():
    return document(PARENT) | {"_normalization": {
        "url": PARENT, "generation": 1000, "storage_key": KEY,
        "storage_value": json.dumps({"theme": "light", "trace": "owned value", "from": "session"}),
    }}


def test_separately_captured_source_and_exact_owned_storage_map_bind_normalized_parent():
    nav = captured_navigation()
    assert nav.observe_source(SOURCE, document())
    current = normalized_document()
    assert all(nav.normalization_checks(current).values())
    assert all(nav.parent_checks(LINK, current).values())
    assert nav.freeze_parent(LINK, current)
    assert nav.armed_normalization_current(current)
    assert not nav.parent_checks(LINK.replace("Private1", "Other111"), current)["owned_checkout_parent_exact"]
    serialized = json.dumps(nav.normalization_checks(current))
    assert all(type(value) is bool for value in nav.normalization_checks(current).values())
    assert not any(value in serialized for value in (KEY, ENTRY, LINK, "owned value", "trace"))


@pytest.mark.parametrize("mutation", [
    "missing_source", "unknown_source", "stale_source", "wrong_path", "foreign_origin", "extra_query",
    "duplicate_query", "wrong_key_name", "wrong_prefix", "long_key", "encoded_key", "fragment",
    "no_observation", "missing_storage", "nonobject_storage", "nonstring_value", "oversized_storage",
    "duplicate_storage_member", "missing_map_member", "extra_map_member", "wrong_map_value",
    "cross_attempt_map", "wrong_storage_key", "changed_observed_url", "changed_observed_generation",
    "changed_document_generation", "body_absent", "no_billing_transition",
])
def test_normalization_never_accepts_query_shape_without_exact_current_source_and_owned_map(mutation):
    nav = captured_navigation()
    if mutation != "missing_source":
        nav.observe_source("0" * 64 if mutation == "unknown_source" else SOURCE,
                           document(generation=2000 if mutation == "stale_source" else 1000))
    current = normalized_document()
    changes = {
        "wrong_path": PARENT.replace("Private1", "Other111"),
        "foreign_origin": PARENT.replace("test.checkout.dodopayments.com", "foreign.invalid"),
        "extra_query": PARENT + "&other=1", "duplicate_query": PARENT + "&session=" + KEY,
        "wrong_key_name": PARENT.replace("?session=", "?unknown="),
        "wrong_prefix": PARENT.replace("sess_", "other_"), "long_key": PARENT + "A",
        "encoded_key": PARENT.replace("?session=", "?%73ession="), "fragment": PARENT + "#other",
    }
    if mutation in changes:
        current["url"] = current["_normalization"]["url"] = changes[mutation]
    if mutation == "no_observation":
        del current["_normalization"]
    if mutation == "missing_storage":
        current["_normalization"]["storage_value"] = None
    if mutation == "nonobject_storage":
        current["_normalization"]["storage_value"] = "[]"
    if mutation == "nonstring_value":
        current["_normalization"]["storage_value"] = '{"theme":true,"trace":"owned value","from":"session"}'
    if mutation == "oversized_storage":
        current["_normalization"]["storage_value"] = " " * 20_001
    if mutation == "duplicate_storage_member":
        current["_normalization"]["storage_value"] = '{"theme":"light","theme":"light","trace":"owned value","from":"session"}'
    if mutation in {"missing_map_member", "extra_map_member", "wrong_map_value", "cross_attempt_map"}:
        mapping = json.loads(current["_normalization"]["storage_value"])
        if mutation == "missing_map_member":
            del mapping["from"]
        elif mutation == "extra_map_member":
            mapping["unretained"] = "extra"
        elif mutation == "wrong_map_value":
            mapping["from"] = "other"
        else:
            mapping["trace"] = "cross-attempt"
        current["_normalization"]["storage_value"] = json.dumps(mapping)
    if mutation == "wrong_storage_key":
        current["_normalization"]["storage_key"] = "sess_Other12345"
    if mutation == "changed_observed_url":
        current["_normalization"]["url"] += "&other=1"
    if mutation == "changed_observed_generation":
        current["_normalization"]["generation"] = 2000
    if mutation == "changed_document_generation":
        current["generation"] = 2000
    if mutation == "body_absent":
        current["body_present"] = False
    if mutation == "no_billing_transition":
        nav.transition = None
    assert not all(nav.normalization_checks(current).values())
    assert not nav.parent_checks(LINK, current)["owned_checkout_parent_exact"]


@pytest.mark.parametrize("mutation", ["key", "map_encoding", "map_value", "generation", "missing_observation"])
def test_confirmation_rejects_changed_frozen_normalization_even_when_shape_still_matches(mutation):
    nav = captured_navigation()
    nav.observe_source(SOURCE, document())
    current = normalized_document()
    assert nav.freeze_parent(LINK, current)
    if mutation == "key":
        other = "sess_Other12345"
        current["url"] = current["_normalization"]["url"] = LINK + "?session=" + other
        current["_normalization"]["storage_key"] = other
    elif mutation == "map_encoding":
        current["_normalization"]["storage_value"] = json.dumps(json.loads(current["_normalization"]["storage_value"]), indent=2)
    elif mutation == "map_value":
        current["_normalization"]["storage_value"] = '{}'
    elif mutation == "generation":
        current["generation"] = current["_normalization"]["generation"] = 2000
    else:
        del current["_normalization"]
    assert not nav.armed_normalization_current(current)


@pytest.mark.parametrize("pairs,expected,approved", [
    ("theme=first&theme=last&from=session", {"theme": "last", "from": "session"}, True),
    ("session=&theme=light&from=session", {"session": "", "theme": "light", "from": "session"}, True),
    ("session=existing&from=session", {"session": "existing", "from": "session"}, False),
    ("session=existing&session=&from=session", {"session": "", "from": "session"}, False),
    ("__proto__=value&from=session", {"__proto__": "value", "from": "session"}, False),
])
def test_normalizer_uses_first_session_presence_and_last_duplicate_object_member(pairs, expected, approved):
    nav = captured_navigation()
    nav.observe_source(SOURCE, document())
    nav.transition["parent_url"] = LINK + "?" + pairs
    current = normalized_document()
    current["_normalization"]["storage_value"] = json.dumps(expected)
    assert all(nav.normalization_checks(current).values()) is approved
    assert nav.parent_checks(LINK, current)["owned_checkout_parent_exact"] is approved


def test_preferred_authenticated_and_ordinary_parent_keep_existing_exact_authority():
    for current in (document(LINK), document(captured_navigation().transition["parent_url"])):
        nav = captured_navigation()
        assert nav.freeze_parent(LINK, current)
        assert nav._armed_normalization is None
        assert nav.armed_normalization_current(current)
