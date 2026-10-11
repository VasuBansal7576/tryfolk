"""Private native candidate binding to independently read provider/customer data."""

import json
import subprocess
from pathlib import Path
import pytest

SCRIPT = """const fs=require('fs'); const p=JSON.parse(fs.readFileSync(0,'utf8'));
import(process.cwd()+'/integrations/dodo/contact_binding.mjs').then(m=>
process.stdout.write(JSON.stringify(m.contactBindingFacts(p.payment,p.expected))));"""


def facts(mutation=None):
    expected = {
        "payment_id": "pay_fixture",
        "checkout_session_id": "cks_fixture",
        "customer_id": "cus_fixture",
        "source_account_ownership_verified": True,
        "email": "synthetic_current@tryfolk.invalid",
        "amount_minor": 100,
        "currency": "USD",
        "metadata": {
            "run_id": "run_fixture",
            "account_id": "account_fixture",
            "persona_id": "persona_fixture",
            "fixture_id": "fixture_current",
            "target_revision": "revision_fixture",
            "order_id": "order_fixture",
            "product_id": "product_fixture",
        },
        "product_id": "product_fixture",
    }
    payment = {
        "payment_id": "pay_fixture",
        "checkout_session_id": "cks_fixture",
        "customer": {"customer_id": "cus_fixture", "email": "synthetic_current@tryfolk.invalid"},
        "metadata": dict(expected["metadata"]),
        "total_amount": 100,
        "currency": "USD",
        "status": "requires_payment_method",
        "payment_provider": "dodo",
        "product_cart": [{"product_id": "product_fixture", "quantity": 1}],
    }
    if mutation == "email":
        expected["email"] = "synthetic_other@tryfolk.invalid"
    if mutation == "personal":
        payment["customer"]["email"] = expected["email"] = "personal@example.invalid"
    if mutation == "customer":
        payment["customer"]["customer_id"] = "cus_other"
    if mutation == "payment":
        payment["payment_id"] = "pay_other"
    if mutation == "checkout":
        payment["checkout_session_id"] = "cks_other"
    if mutation == "metadata":
        payment["metadata"]["account_id"] = "account_other"
    if mutation == "status":
        payment["status"] = "succeeded"
    if mutation == "source":
        expected["source_account_ownership_verified"] = False
    if mutation == "stale":
        payment["metadata"]["target_revision"] = "stale_revision"
    if mutation == "missing":
        expected["email"] = None
    result = json.loads(
        subprocess.check_output(
            ["node", "-e", SCRIPT],
            input=json.dumps({"payment": payment, "expected": expected}).encode(),
            cwd=Path(__file__).resolve().parents[1],
        )
    )
    assert all(isinstance(value, bool) for value in result.values())
    assert "@" not in json.dumps(result)
    return result


def test_exact_native_email_and_customer_match_current_independent_payment():
    result = facts()
    assert result["current_payment_customer_order_bound"]
    assert result["native_email_synthetic_format"] and result["provider_email_synthetic_format"]
    assert result["native_email_matches_current_provider"] and result["contact_binding_passed"]


@pytest.mark.parametrize(
    "mutation",
    ["email", "personal", "customer", "payment", "checkout", "metadata", "status", "source", "stale"],
)
def test_wrong_or_missing_native_candidate_never_establishes_contact_binding(mutation):
    assert not facts(mutation)["contact_binding_passed"]


def test_absent_native_email_stays_unobserved_while_current_provider_expectation_is_verified():
    result = facts("missing")
    assert result["contact_binding_passed"]
    assert not result["native_email_observed"] and not result["native_email_synthetic_format"]
    assert result["provider_email_synthetic_format"]
    assert result["native_candidate_matches_provider_if_present"]


@pytest.mark.parametrize("approved", [True, False])
def test_private_expectation_returns_email_only_to_bound_server_memory(approved):
    script = """const fs=require('fs');const approved=JSON.parse(fs.readFileSync(0,'utf8'));
import(process.cwd()+'/integrations/dodo/contact_binding.mjs').then(m=>{
const expected={source_account_ownership_verified:approved,payment_id:'pay_fixture',checkout_session_id:'cks_fixture',customer_id:'cus_fixture',email:null,
amount_minor:100,currency:'USD',product_id:'product_fixture',metadata:{run_id:'r',account_id:'a',persona_id:'p',fixture_id:'f',target_revision:'rev',order_id:'o',product_id:'product_fixture'}};
const payment={payment_provider:'dodo',status:'requires_payment_method',payment_id:'pay_fixture',checkout_session_id:'cks_fixture',customer:{customer_id:'cus_fixture',email:'synthetic_fixture@tryfolk.invalid'},total_amount:100,currency:'USD',product_cart:[{product_id:'product_fixture',quantity:1}],metadata:expected.metadata};
const result=m.privateContactExpectation(payment,expected);
process.stdout.write(JSON.stringify({facts:result.facts,private_expected_correct:result.expected_email===(approved?'synthetic_fixture@tryfolk.invalid':null)}));});"""
    result = json.loads(
        subprocess.check_output(
            ["node", "-e", script],
            input=json.dumps(approved).encode(),
            cwd=Path(__file__).resolve().parents[1],
        )
    )
    assert result["private_expected_correct"]
    assert result["facts"]["contact_binding_passed"] is approved
    assert "@" not in json.dumps(result)
