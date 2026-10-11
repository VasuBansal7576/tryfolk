"""Pure classification of two independently read records. Never fulfills a purchase."""

from pydantic import BaseModel


class Payment(BaseModel):
    provider: str
    id: str
    status: str
    account_id: str
    checkout_id: str
    product_id: str
    amount: int
    currency: str
    evidence_url: str


class Entitlement(BaseModel):
    account_id: str
    product_id: str
    granted: bool
    payment_id: str | None
    units: int


def verify(
    payment: Payment | None,
    entitlement: Entitlement | None,
    *,
    account_id: str,
    checkout_id: str,
    product_id: str,
    amount: int,
    currency: str,
    settled: bool,
    capability: bool | None = None,
):
    result = {
        "outcome": "blocked",
        "classification": "verification_unavailable",
        "payment_verified": False,
        "entitlement_verified": False,
        "provider": payment.provider if payment else None,
        "measured": bool(payment and payment.provider == "dodo"),
        "capability_checked": capability is not None,
        "capability_available": capability,
        "payment": payment.model_dump() if payment else None,
        "entitlement": entitlement.model_dump() if entitlement else None,
    }
    if not payment or not entitlement:
        return result
    if (
        payment.account_id != account_id
        or payment.checkout_id != checkout_id
        or payment.product_id != product_id
        or payment.amount != amount
        or payment.currency != currency
        or entitlement.account_id != account_id
        or entitlement.product_id != product_id
    ):
        result["classification"] = "correlation_mismatch"
        return result
    result["payment_verified"] = True
    if payment.status in ("pending", "processing", "requires_payment_method"):
        result.update(outcome="pending", classification="payment_unsettled")
    elif payment.status == "succeeded":
        if entitlement.granted and entitlement.payment_id == payment.id and entitlement.units > 0:
            result.update(
                outcome="completed", classification="payment_and_entitlement", entitlement_verified=True
            )
        elif entitlement.granted:
            result.update(classification="entitlement_correlation_mismatch")
        else:
            result.update(
                outcome="failed" if settled else "pending",
                classification="paid_without_access" if settled else "awaiting_entitlement",
            )
    elif payment.status in ("failed", "cancelled"):
        result.update(
            outcome="completed" if not entitlement.granted else "failed",
            classification="decline_handled" if not entitlement.granted else "unpaid_access",
        )
    return result
