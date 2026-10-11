"""Owned Dodo target control and independent read-only receipt/entitlement adapter.

The browser checkout path remains credential-gated until genuine sandbox validation.
"""

import os
import re
from urllib.parse import urlsplit
import httpx
from .verification import Payment, Entitlement, verify
from .secure_frame import BROWSER_API

OWNERSHIP = ("run_id", "persona_id", "account_id", "fixture_id", "target_revision")


def correlate(order, receipt, entitlement, capability=None):
    result = {
        "outcome": "blocked",
        "classification": "verification_unavailable",
        "measured": False,
        "provider": "dodo",
        "payment_verified": False,
        "entitlement_verified": False,
        "capability_checked": capability is not None,
        "capability_available": capability,
        "receipt": receipt,
        "entitlement": entitlement,
    }
    if receipt.get("source") != "dodo_test_api" or entitlement.get("source") != "app_local_entitlement":
        return result
    expected = {key: order[key] for key in (*OWNERSHIP, "order_id", "expected_product_id")}
    if any(value.get("ownership") != expected for value in (receipt, entitlement)):
        return {**result, "classification": "correlation_mismatch"}
    if receipt.get("state") == "pending":
        return {**result, "outcome": "pending", "classification": "payment_unsettled"}
    if receipt.get("state") not in ("succeeded", "failed"):
        return {
            **result,
            "classification": "correlation_mismatch"
            if receipt.get("state") == "mismatch"
            else "verification_unavailable",
        }
    payment = receipt.get("payment") or {}
    checkout = receipt.get("checkout") or {}
    metadata = payment.get("metadata") or {}
    if (
        any(metadata.get(key) != order[key] for key in (*OWNERSHIP, "order_id"))
        or metadata.get("product_id") != order["expected_product_id"]
        or receipt.get("checkout_session_id") != order["checkout_session_id"]
        or checkout.get("id") != order["checkout_session_id"]
        or checkout.get("payment_id") != payment.get("payment_id")
        or payment.get("checkout_session_id") != order["checkout_session_id"]
        or checkout.get("payment_status") != payment.get("status")
        or payment.get("product_cart") != [{"product_id": order["expected_product_id"], "quantity": 1}]
        or payment.get("amount_minor") != order["amount_minor"]
        or payment.get("currency") != order["currency"]
    ):
        return {**result, "classification": "correlation_mismatch"}
    paid = Payment(
        provider="dodo",
        id=payment["payment_id"],
        status=payment["status"],
        account_id=order["account_id"],
        checkout_id=order["checkout_session_id"],
        product_id=order["expected_product_id"],
        amount=order["amount_minor"],
        currency=order["currency"],
        evidence_url=receipt.get("evidence_url", ""),
    )
    grant = Entitlement(
        account_id=order["account_id"],
        product_id=order["expected_product_id"],
        granted=bool(
            entitlement.get("order_has_grant")
            and entitlement.get("has_access")
            and entitlement.get("active")
            and entitlement.get("grant_count") == 1
        ),
        payment_id=entitlement.get("payment_id"),
        units=entitlement.get("grant_count", 0),
    )
    outcome = verify(
        paid,
        grant,
        account_id=order["account_id"],
        checkout_id=order["checkout_session_id"],
        product_id=order["expected_product_id"],
        amount=order["amount_minor"],
        currency=order["currency"],
        settled=entitlement.get("payment_id") == paid.id,
        capability=capability,
    )
    return {**outcome, "receipt": receipt, "app_entitlement": entitlement}


class DodoTarget:
    def __init__(self, origin):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port not in range(3300, 3400)
            or parsed.path not in ("", "/")
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Dodo target must be an explicitly owned exact loopback preview.")
        self.origin = origin.rstrip("/")
        self.headers = {"Authorization": "Bearer " + os.environ.get("TARGET_CONTROL_TOKEN", "")}

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as client:
            response = await client.request(method, self.origin + path, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()

    async def registration(self):
        readiness = await self.request("GET", "/api/target/readiness")
        manifest = readiness["manifest"]
        if (
            manifest["app_origin"] != self.origin
            or manifest["safe_origins"] != [self.origin]
            or manifest["provider_environment"] != "test_mode"
            or manifest["upstream_commit"] != "ef7cbd980d8f1e57b036dc2af71ff05521b6175a"
            or manifest["source_clean"] is not True
            or manifest.get("checkout_origins") != ["https://test.checkout.dodopayments.com"]
            or not re.fullmatch(r"[a-f0-9]{40}", manifest.get("source_commit") or "")
            or not re.fullmatch(r"[a-f0-9]{64}", manifest.get("source_sha256") or "")
        ):
            raise ValueError("Owned Dodo manifest provenance/origin/build validation failed.")
        return readiness

    async def provision(self, run, persona):
        seed = await self.request(
            "POST",
            "/api/target/fixtures",
            json={"run_id": run["id"], "persona_id": persona["id"], "target_revision": run["revision"]},
        )
        expected = {"run_id": run["id"], "persona_id": persona["id"], "target_revision": run["revision"]}
        if (
            any(seed.get(key) != value for key, value in expected.items())
            or not seed.get("account_id")
            or not seed.get("fixture_id")
        ):
            raise ValueError("Dodo fixture ownership mismatch.")
        parsed = urlsplit(seed["login_url"])
        if (
            f"{parsed.scheme}://{parsed.netloc}" != self.origin
            or parsed.path != "/target/login"
            or parsed.query
            or parsed.fragment != "token=" + seed.get("login_token", "")
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", seed.get("login_token", ""))
        ):
            raise ValueError("Dodo login destination mismatch.")
        return seed

    async def verification(self, run, fixture, capability=None):
        orders = (
            await self.request("GET", "/api/target/orders", params={"account_id": fixture["account_id"]})
        )["orders"]
        if len(orders) != 1:
            return {
                "outcome": "blocked",
                "classification": "ambiguous_order" if orders else "verification_unavailable",
                "measured": False,
                "provider": "dodo",
                "capability_checked": capability is not None,
                "capability_available": capability,
            }
        order = orders[0]
        if any(order.get(key) != fixture[key] for key in OWNERSHIP):
            raise ValueError("Dodo order ownership differs from this fresh persona fixture.")
        receipt = await self.request("GET", "/api/target/receipts", params={"order_id": order["order_id"]})
        receipt["evidence_url"] = self.origin + "/api/target/evidence?evidence_id=" + receipt["evidence_id"]
        entitlement = await self.request(
            "GET",
            "/api/target/entitlements",
            params={"account_id": fixture["account_id"], "order_id": order["order_id"]},
        )
        return correlate(order, receipt, entitlement, capability)

    async def frame_context(self, observed, run, fixture):
        """Authorize current card input/requests by Test identity, never a secret."""
        denied = {
            "approved": False,
            "reason": "independent_frame_context_mismatch",
            "secret_authenticated": False,
        }
        manifest = (await self.registration())["manifest"]
        if (
            manifest.get("target_revision") != run["revision"]
            or fixture.get("run_id") != run["id"]
            or fixture.get("target_revision") != run["revision"]
            or observed.get("api_url") != BROWSER_API
            or observed.get("browser_api_role") != "observed_approved_shared_service_config"
            or observed.get("service_sandbox_only_attested") is not False
            or not all(
                observed.get(key) is True for key in ("api_base_verified", "sandbox_key", "parent_matches")
            )
        ):
            return denied
        orders = (
            await self.request("GET", "/api/target/orders", params={"account_id": fixture["account_id"]})
        )["orders"]
        if len(orders) != 1:
            return denied
        order = orders[0]
        if not order.get("active") or any(order.get(key) != fixture[key] for key in OWNERSHIP):
            return denied
        receipt = await self.request("GET", "/api/target/receipts", params={"order_id": order["order_id"]})
        payment, checkout = receipt.get("payment") or {}, receipt.get("checkout") or {}
        metadata = payment.get("metadata") or {}
        operation = observed.get("operation", "bootstrap")
        allowed_status = (
            {"requires_payment_method", "requires_confirmation", "processing", "succeeded", "failed", "cancelled", "requires_capture", "partially_captured"}
            if operation == "poll" else {"requires_payment_method"}
        )
        expected = {key: order[key] for key in (*OWNERSHIP, "order_id", "expected_product_id")}
        if (
            receipt.get("source") != "dodo_test_api"
            or operation not in ("bootstrap", "session_tokens", "confirm", "poll")
            or receipt.get("state") not in (("pending", "succeeded", "failed") if operation == "poll" else ("pending",))
            or receipt.get("ownership") != expected
            or receipt.get("mismatches")
            or not observed.get("payment_id")
            or payment.get("payment_id") != observed["payment_id"]
            or not order.get("customer_id")
            or payment.get("customer_id") != order["customer_id"]
            or receipt.get("checkout_session_id") != order["checkout_session_id"]
            or checkout.get("id") != order["checkout_session_id"]
            or checkout.get("payment_id") != payment.get("payment_id")
            or payment.get("checkout_session_id") != order["checkout_session_id"]
            or checkout.get("payment_status") != payment.get("status")
            or payment.get("status") not in allowed_status
            or any(metadata.get(key) != order[key] for key in (*OWNERSHIP, "order_id"))
            or metadata.get("product_id") != order["expected_product_id"]
            or payment.get("product_cart") != [{"product_id": order["expected_product_id"], "quantity": 1}]
            or payment.get("amount_minor") != order["amount_minor"]
            or payment.get("currency") != order["currency"]
        ):
            return denied
        return {
            "approved": True,
            "provider_environment": "test_mode",
            "payment_correlated": True,
            "customer_correlated": True,
            "payment_id": payment["payment_id"],
            "checkout_session_id": order["checkout_session_id"],
            "receipt_evidence_id": receipt.get("evidence_id"),
            "secret_authenticated": False,
            "payment_status": payment["status"],
        }
