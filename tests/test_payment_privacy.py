from tryfolk.payment_privacy import redact_payment


def test_private_payment_parent_authority_never_enters_proof_or_model_context():
    link = "https://test.checkout.dodopayments.com/Private1?private-token=secret"
    value = {"identity": {"payment_link": link}, "private_payment_link": link}
    assert link not in str(redact_payment(value))


def test_payment_payload_fingerprint_and_visible_test_values_are_removed_before_evidence_or_model_context():
    value = {
        "payment_method_data": {"card": {"card_number": "4242424242424242", "card_cvc": "123"},
                                "billing": {"email": "private@tryfolk.invalid"}},
        "browser_info": {"user_agent": "private-agent"},
        "text": "Card 4242 4242 4242 4242 expiry 06/32 CVC 123",
        "decision": "widget_confirm", "body_field_names": ["browser_info", "payment_method_data"],
    }
    result = str(redact_payment(value))
    for private in ["4242", "06/32", "123", "private@tryfolk.invalid", "private-agent"]:
        assert private not in result
    assert "widget_confirm" in result and "body_field_names" in result


def test_split_expiry_and_holder_actor_values_are_redacted_only_in_evidence():
    for field, value in [("card_month", "06"), ("card_year", "2032"), ("card_name", "Tryfolk Test Customer")]:
        action = {"_tryfolk": {"field": field}, "arguments": [value]}
        assert redact_payment(action)["arguments"] == ["[redacted_test_card]"]
        assert action["arguments"] == [value]
def test_native_return_route_facts_retain_literal_template_and_correlations_without_values():
    from tryfolk.native_card_observation import return_route_facts

    raw = "https://test.checkout.dodopayments.com/payment-status/{PAYMENT_ID}?redirect_url=http%3A%2F%2F127.0.0.1%3A3300%2Fcheckout%2Freturn&session_id=cks_private&token=private-query-value"
    facts = return_route_facts(raw, payment_id="pay_private", checkout_id="cks_private", owned_origin="http://127.0.0.1:3300", checkout_url="https://test.checkout.dodopayments.com/session/cks_private")
    assert facts["origin_role"] == "test_checkout"
    assert facts["template_path_skeleton"] == "/payment-status/{PAYMENT_ID}"
    assert facts["resolved_path_skeleton"] == "/payment-status/:payment_id"
    assert facts["query_field_names"] == ["redirect_url", "session_id", "token"]
    assert facts["current_payment_path_bound"] and facts["current_checkout_query_bound"]
    assert facts["query_owned_return_bound"] and facts["literal_payment_placeholder"]
    assert "private" not in __import__("json").dumps(facts)


def test_native_return_route_facts_hide_unknown_origins_path_tokens_and_query_keys():
    from tryfolk.native_card_observation import return_route_facts

    facts = return_route_facts("https://private-origin.invalid/secret-path-token?private-key=private-value", payment_id="pay_private", checkout_id="cks_private", owned_origin="http://127.0.0.1:3300", checkout_url="https://test.checkout.dodopayments.com/cks_private")
    assert facts["origin_role"] == "unapproved_origin" and facts["template_path_skeleton"] == "/:opaque_segment"
    assert facts["query_field_names"] == [":unrecognized_field"]
    assert not facts["current_payment_path_bound"] and not facts["current_checkout_query_bound"]
    assert "private" not in __import__("json").dumps(facts) and "secret-path" not in __import__("json").dumps(facts)


def test_native_return_route_diagnostics_report_duplicate_blank_and_invalid_shapes_without_values():
    from tryfolk.native_card_observation import return_route_facts

    arguments = {"payment_id": "pay_private", "checkout_id": "cks_private", "owned_origin": "http://127.0.0.1:3300", "checkout_url": "https://test.checkout.dodopayments.com/cks_private"}
    facts = return_route_facts("https://test.checkout.dodopayments.com/pay_other?status=&status=private-status#private-fragment", **arguments)
    assert facts["duplicate_query_names"] and facts["blank_query_values"] and facts["fragment_present"]
    assert not facts["current_payment_path_bound"]
    malformed = return_route_facts("https://[private-malformed", **arguments)
    assert malformed == {"origin_role": "invalid_url", "parsing_failed": True}
    assert "private" not in __import__("json").dumps([facts, malformed])


def test_native_return_diagnostics_bind_current_parent_alias_and_session_privately():
    from tryfolk.native_card_observation import return_route_facts

    arguments = {"payment_id": "pay_private", "checkout_id": "cks_private", "owned_origin": "http://127.0.0.1:3300", "checkout_url": "https://test.checkout.dodopayments.com/private-alias?session=private-session"}
    facts = return_route_facts("https://test.checkout.dodopayments.com/status/private-alias?session=private-session", **arguments)
    assert facts["resolved_path_skeleton"] == "/status/:current_checkout_route"
    assert facts["query_field_names"] == ["session"]
    assert facts["current_parent_query_bindings"] == {"session": True}
    wrong = return_route_facts("https://test.checkout.dodopayments.com/status/private-alias?session=other-private-session", **arguments)
    assert wrong["current_parent_query_bindings"] == {"session": False}
    assert "private" not in __import__("json").dumps([facts, wrong])
