"""Invented private config and provider I/O fixtures, never live payment evidence."""

from urllib.parse import urlencode
import json
import pytest
from tryfolk.secure_frame import frame_config, redact_frame_config, FrameConfigError


def fixture_url(**changes):
    query = {
        "publishableKey": "pk_snd_fixture",
        "clientSecret": "pay_fixture_secret_ABC123",
        "apiUrl": "https://api.custom.hs.dodopayments.com",
        "locale": "en",
    } | changes
    return (
        "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html?"
        + urlencode(query)
        + "#"
        + urlencode({"parentOrigin": "https://test.checkout.dodopayments.com", "merchantName": "Fixture"})
    )


def test_generated_frame_config_is_private_exact_sandbox_and_redacted_before_serialization():
    url = fixture_url()
    data = frame_config(url)
    assert data["payment_id"] == "pay_fixture"
    assert data["sandbox_key"] and data["parent_matches"] and data["api_base_verified"]
    assert data["query_fields"] == ["apiUrl", "clientSecret", "locale", "publishableKey"]
    serialized = json.dumps(data)
    assert "pk_snd_fixture" not in serialized and "ABC123" not in serialized
    redacted = redact_frame_config({"raw_sdk": {"url_map": {"frame": url}}, "error": "Failed " + url})
    assert "pk_snd_fixture" not in json.dumps(redacted) and "ABC123" not in json.dumps(redacted)
    for change in (
        {"publishableKey": "pk_prd_fixture"},
        {"apiUrl": "https://live.dodopayments.com"},
        {"unknown": "fixture"},
        {"locale": "fr"},
        {"clientSecret": "not-a-payment-secret"},
    ):
        with pytest.raises(ValueError):
            frame_config(fixture_url(**change))


def test_source_defined_secret_component_has_no_invented_fixed_id_grammar_and_errors_stay_private():
    data = frame_config(fixture_url(clientSecret="pay_fixture_extended_id_secret_ABC123"))
    assert data["payment_id"] == "pay_fixture_extended_id"
    with pytest.raises(FrameConfigError) as failure:
        frame_config(fixture_url(apiUrl="https://test.dodopayments.com:invalid"))
    assert failure.value.evidence["api_base_verified"] is False
    value = redact_frame_config(
        {"publishableKey": "unknown-private-prefix", "clientSecret": "unknown-private-value"}
    )
    assert "unknown-private" not in json.dumps(value)


def test_browser_shared_config_is_distinct_from_the_server_test_api_and_grants_no_actions():
    data = frame_config(fixture_url())
    assert data["api_url"] == "https://api.custom.hs.dodopayments.com"
    assert data["browser_api_role"] == "observed_approved_shared_service_config"
    assert data["service_sandbox_only_attested"] is False
    for base in (
        "https://test.dodopayments.com",
        "https://api.custom.hs.dodopayments.com/",
        "https://api.custom.hs.dodopayments.com:443",
        "https://api.custom.hs.dodopayments.com?x=1",
        "https://u:p@api.custom.hs.dodopayments.com",
    ):
        with pytest.raises(FrameConfigError):
            frame_config(fixture_url(apiUrl=base))
    from tryfolk.browser_network import NetworkPolicy
    policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    with pytest.raises(ValueError):
        policy.check_url(data["api_url"])
    assert policy.decide(data["api_url"] + "/account/payment_methods", "GET", "fetch", data["frame_url"], False) == "deny"
