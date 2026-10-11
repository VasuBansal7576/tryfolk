from tryfolk.verification import verify, Payment, Entitlement


def test_payment_is_independent_and_must_match_same_account_purchase_and_product():
    payment = Payment(
        provider="dodo",
        id="p1",
        status="succeeded",
        account_id="a1",
        checkout_id="c1",
        product_id="credits",
        amount=500,
        currency="USD",
        evidence_url="https://provider/payment/p1",
    )
    access = Entitlement(account_id="a1", product_id="credits", granted=True, payment_id="p1", units=10)
    result = verify(
        payment,
        access,
        account_id="a1",
        checkout_id="c1",
        product_id="credits",
        amount=500,
        currency="USD",
        settled=True,
        capability=False,
    )
    assert result["outcome"] == "completed"
    assert result["payment_verified"] is True
    assert result["capability_checked"] is True
    assert result["capability_available"] is False  # entitlement is not capability execution
    for mismatched in [
        Payment(**{**payment.model_dump(), "account_id": "other"}),
        Payment(**{**payment.model_dump(), "checkout_id": "other"}),
        Payment(**{**payment.model_dump(), "amount": 1}),
    ]:
        assert (
            verify(
                mismatched,
                access,
                account_id="a1",
                checkout_id="c1",
                product_id="credits",
                amount=500,
                currency="USD",
                settled=True,
            )["outcome"]
            == "blocked"
        )
    assert (
        verify(
            payment,
            Entitlement(**{**access.model_dump(), "account_id": "other"}),
            account_id="a1",
            checkout_id="c1",
            product_id="credits",
            amount=500,
            currency="USD",
            settled=True,
        )["outcome"]
        == "blocked"
    )


def test_paid_without_access_is_pending_until_settlement_then_defect_and_fixture_is_disclosed():
    p = Payment(
        provider="local-fixture",
        id="p1",
        status="succeeded",
        account_id="a1",
        checkout_id="c1",
        product_id="credits",
        amount=500,
        currency="USD",
        evidence_url="local-provider:p1",
    )
    e = Entitlement(account_id="a1", product_id="credits", granted=False, payment_id=None, units=0)
    args = dict(account_id="a1", checkout_id="c1", product_id="credits", amount=500, currency="USD")
    assert verify(p, e, **args, settled=False)["outcome"] == "pending"
    result = verify(p, e, **args, settled=True)
    assert result["outcome"] == "failed"
    assert result["classification"] == "paid_without_access"
    assert result["measured"] is False
    assert result["provider"] == "local-fixture"
