"""Owned Dodo target control and independent read-only receipt/entitlement adapter.

The browser checkout path remains credential-gated until genuine sandbox validation.
"""

import os
import asyncio
import json
from pathlib import Path
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

    async def request(self, method, path, *, _trace=None, **kwargs):
        item = None
        if _trace is not None:
            item = {"stage": {"/api/target/readiness": "readiness", "/api/target/orders": "orders",
                              "/api/target/receipts": "receipt"}.get(path, "other"),
                    "response_received": False}
            _trace.append(item)
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as client:
                response = await client.request(method, self.origin + path, headers=self.headers, **kwargs)
                if item is not None:
                    item.update(response_received=True, http_status=response.status_code)
                response.raise_for_status()
                return response.json()
        except Exception as error:
            if item is not None:
                item["error_type"] = type(error).__name__ if isinstance(
                    error, (httpx.HTTPStatusError, json.JSONDecodeError)) else "transport_error" \
                    if isinstance(error, httpx.RequestError) else "unrecognized"
            raise

    async def registration(self, *, _trace=None):
        readiness = await self.request("GET", "/api/target/readiness", _trace=_trace)
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
        if observed.get("operation") == "contact":
            contact = await self.private_contact(observed, run, fixture)
            return {"approved": bool(contact), "private_contact": contact}
        bounded = observed.get("operation") == "poll" and observed.get("identity_stage") == "native_response_capture"
        reads = []
        active_trace = []

        async def observe():
            nonlocal active_trace
            initial_binding = None
            for attempt in range(3 if bounded else 1):
                trace = active_trace = []
                try:
                    result = await self._frame_context_read(observed, run, fixture, trace)
                except Exception as error:
                    error_type = type(error).__name__ if type(error) in (
                        httpx.HTTPStatusError, json.JSONDecodeError, ValueError, KeyError, TypeError
                    ) else "transport_error" if isinstance(error, httpx.RequestError) else "unrecognized"
                    reads.append({"stage": trace[-1]["stage"] if trace else "identity_read",
                                  "outcome": "error", "error_type": error_type, "HTTP_reads": trace})
                    error.identity_diagnostics = {"reads": reads, "retry_bound_exhausted": False}
                    raise
                facts = result.pop("_identity_read")
                binding = result.pop("_identity_binding", None)
                if initial_binding is not None and binding is not None and binding != initial_binding:
                    result = {"approved": False, "reason": "independent_frame_context_mismatch",
                              "secret_authenticated": False}
                    facts.update(stage="identity_binding", outcome="denied",
                                 first_failed_predicate="same_identity_across_convergence_reads")
                    facts["checks"]["same_identity_across_convergence_reads"] = False
                elif binding is not None:
                    initial_binding = binding
                facts["HTTP_reads"] = trace
                reads.append(facts)
                active_trace = []
                retry = facts["outcome"] == "checkout_status_convergence_pending"
                result["identity_diagnostics"] = {"reads": list(reads),
                    "retry_bound_exhausted": bounded and retry and attempt == 2}
                if not (bounded and retry and attempt < 2):
                    return result
                await asyncio.sleep(0.1)

        if bounded:
            # A smaller cap inside the existing five-second response capture;
            # no new capture/aggregate deadline, browser action or confirmation.
            try:
                async with asyncio.timeout(2):
                    return await observe()
            except TimeoutError as error:
                error.identity_diagnostics = {"reads": reads + [{"stage": "identity_read_deadline",
                    "outcome": "error", "error_type": "TimeoutError", "HTTP_reads": active_trace}],
                    "retry_bound_exhausted": True}
                raise
        return await observe()

    async def _frame_context_read(self, observed, run, fixture, trace):
        denied = {
            "approved": False,
            "reason": "independent_frame_context_mismatch",
            "secret_authenticated": False,
        }

        def decision(checks, stage, outcome="denied"):
            return {**denied, "_identity_read": {"stage": stage, "outcome": outcome, "checks": checks,
                "first_failed_predicate": next((key for key, value in checks.items() if not value), None)}}

        manifest = (await self.registration(_trace=trace))["manifest"]
        checks = {
            "current_target_revision": manifest.get("target_revision") == run["revision"],
            "fixture_current_run": fixture.get("run_id") == run["id"],
            "fixture_current_revision": fixture.get("target_revision") == run["revision"],
            "approved_browser_API": observed.get("api_url") == BROWSER_API,
            "approved_browser_API_role": observed.get("browser_api_role") == "observed_approved_shared_service_config",
            "browser_service_sandbox_only_not_claimed": observed.get("service_sandbox_only_attested") is False,
            "browser_API_base_verified": observed.get("api_base_verified") is True,
            "browser_sandbox_key": observed.get("sandbox_key") is True,
            "browser_parent_matches": observed.get("parent_matches") is True,
        }
        if not all(checks.values()):
            return decision(checks, "configuration")
        orders = (
            await self.request("GET", "/api/target/orders", params={"account_id": fixture["account_id"]}, _trace=trace)
        )["orders"]
        if len(orders) != 1:
            return decision({"single_owned_order": False}, "orders")
        order = orders[0]
        checks = {"active_owned_order": bool(order.get("active")),
                  "exact_order_ownership": all(order.get(key) == fixture[key] for key in OWNERSHIP)}
        if not all(checks.values()):
            return decision(checks, "orders")
        receipt = await self.request("GET", "/api/target/receipts", params={"order_id": order["order_id"], "browser_parent": "1"}, _trace=trace)
        payment, checkout = receipt.get("payment") or {}, receipt.get("checkout") or {}
        metadata = payment.get("metadata") or {}
        operation = observed.get("operation", "bootstrap")
        allowed_status = (
            {"requires_payment_method", "requires_confirmation", "processing", "succeeded", "failed", "cancelled", "requires_capture", "partially_captured"}
            if operation == "poll" else {"requires_payment_method"}
        )
        expected = {key: order[key] for key in (*OWNERSHIP, "order_id", "expected_product_id")}
        # Private comparison only. No identifiers or source values enter the
        # closed diagnostics. A retry may change status, never its identity.
        binding = {
            "source": {key: manifest.get(key) for key in ("source_commit", "source_sha256", "target_revision")},
            "order": {key: order.get(key) for key in (*OWNERSHIP, "order_id", "expected_product_id",
                      "checkout_session_id", "customer_id", "amount_minor", "currency")},
            "payment_id": payment.get("payment_id"), "payment_link": receipt.get("private_payment_link"),
        }
        checks = {
            "receipt_Test_API_source": receipt.get("source") == "dodo_test_api",
            "supported_identity_operation": operation in ("bootstrap", "session_tokens", "confirm", "poll"),
            "receipt_state_allowed": receipt.get("state") in (("pending", "succeeded", "failed") if operation == "poll" else ("pending",)),
            "receipt_exact_owned_order": receipt.get("ownership") == expected,
            "receipt_mismatches_absent": not receipt.get("mismatches"),
            "observed_payment_present": bool(observed.get("payment_id")),
            "exact_observed_payment": payment.get("payment_id") == observed.get("payment_id"),
            "owned_customer_present": bool(order.get("customer_id")),
            "exact_owned_customer": payment.get("customer_id") == order.get("customer_id"),
            "receipt_exact_checkout": receipt.get("checkout_session_id") == order["checkout_session_id"],
            "checkout_exact_id": checkout.get("id") == order["checkout_session_id"],
            "checkout_exact_payment": checkout.get("payment_id") == payment.get("payment_id"),
            "payment_exact_checkout": payment.get("checkout_session_id") == order["checkout_session_id"],
            "checkout_payment_status_matches": checkout.get("payment_status") == payment.get("status"),
            "payment_status_allowed": payment.get("status") in allowed_status,
            "exact_owned_metadata": all(metadata.get(key) == order[key] for key in (*OWNERSHIP, "order_id")),
            "exact_owned_product_metadata": metadata.get("product_id") == order["expected_product_id"],
            "exact_single_owned_product": payment.get("product_cart") == [{"product_id": order["expected_product_id"], "quantity": 1}],
            "exact_owned_amount": payment.get("amount_minor") == order["amount_minor"],
            "exact_owned_currency": payment.get("currency") == order["currency"],
        }
        if not all(checks.values()):
            status_checks = {"receipt_state_allowed", "receipt_mismatches_absent", "checkout_payment_status_matches"}
            temporary = operation == "poll" and receipt.get("state") == "mismatch" \
                and receipt.get("mismatches") == ["checkout.payment_status"] \
                and payment.get("status") == "succeeded" and checkout.get("payment_status") == "requires_payment_method" \
                and all(value for key, value in checks.items() if key not in status_checks)
            result = decision(checks, "receipt", "checkout_status_convergence_pending" if temporary else "denied")
            if temporary:
                result["_identity_binding"] = binding
            result["_identity_read"]["provider_HTTP_status_available"] = False
            result["_identity_read"]["receipt_state"] = receipt.get("state") if receipt.get("state") in (
                "pending", "succeeded", "failed", "mismatch", "provider_error", "setup_required") else "unrecognized"
            return result
        return {
            "_identity_binding": binding,
            "_identity_read": {"stage": "receipt", "outcome": "approved", "checks": checks,
                               "first_failed_predicate": None},
            "approved": True,
            "provider_environment": "test_mode",
            "payment_correlated": True,
            "customer_correlated": True,
            "payment_id": payment["payment_id"],
            "checkout_session_id": order["checkout_session_id"],
            "order_id": order["order_id"],
            "ownership": {key: order[key] for key in OWNERSHIP},
            "receipt_evidence_id": receipt.get("evidence_id"),
            "secret_authenticated": False,
            "payment_status": payment["status"],
            # Private callback only; ordinary verification/evidence excludes it.
            "payment_link": receipt.get("private_payment_link"),
        }

    async def read_private_contact(self, expected):
        """One scoped Test Payments Read in a private pipe; no retries/logs."""
        if not os.environ.get("DODO_TEST_READ_KEY"):
            return {}
        root = Path(__file__).resolve().parents[2]
        process = await asyncio.create_subprocess_exec(
            "node", str(root / "integrations/dodo/read_contact.mjs"), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async with asyncio.timeout(11):
                output, _ = await process.communicate(json.dumps(expected).encode())
            return json.loads(output) if process.returncode == 0 and len(output) <= 3000 else {}
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def private_contact(self, observed, run, fixture):
        """Shared persona/replay callback; no target changes or DOM inference."""
        expected = {}
        try:
            path = Path(os.environ.get("TRYFOLK_DODO_CONTACT_CONTRACT", ""))
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 20_000:
                return {}
            contract = json.loads(path.read_text())
            manifest = (await self.registration())["manifest"]
            required = {"synthetic_email_creation_contract", "exact_fixed_name_creation_contract", "created_customer_used_in_checkout",
                        "name_email_editing_disabled", "receipt_customer_id_order_check", "receipt_customer_email_account_check",
                        "private_authenticated_payment_link_projection", "owned_navigation_customization_closed",
                        "official_SDK_hardcoded_Test_mode", "owned_return_origin_binding", "card_only_checkout"}
            if not (contract.get("kind") == "source_fixed_customer_creation_contract_not_provider_name_readback"
                    and contract.get("passed") is True and contract.get("source_commit") == manifest.get("source_commit")
                    and contract.get("source_digest") == manifest.get("source_sha256")
                    and all(contract.get("checks", {}).get(name) is True for name in required)):
                return {}
            context = {**observed, "operation": "bootstrap"}
            identity = await self.frame_context(context, run, fixture)
            orders = (await self.request("GET", "/api/target/orders", params={"account_id": fixture["account_id"]}))["orders"]
            if len(orders) != 1:
                return {}
            order = orders[0]
            if not (identity.get("approved") is True and identity.get("provider_environment") == "test_mode"
                    and identity.get("customer_correlated") is True and identity.get("ownership") == {key: fixture.get(key) for key in OWNERSHIP}
                    and identity.get("payment_id") == observed.get("payment_id")
                    and identity.get("checkout_session_id") == observed.get("checkout_session_id") == order.get("checkout_session_id")
                    and identity.get("order_id") == order.get("order_id") and order.get("active") is True
                    and all(order.get(key) == fixture.get(key) for key in OWNERSHIP)):
                return {}
            expected = {"payment_id": observed["payment_id"], "checkout_session_id": observed["checkout_session_id"],
                        "customer_id": order["customer_id"], "email": None, "source_account_ownership_verified": True,
                        "amount_minor": order["amount_minor"], "currency": order["currency"], "product_id": order["expected_product_id"],
                        "metadata": {**{key: order[key] for key in (*OWNERSHIP, "order_id")}, "product_id": order["expected_product_id"]}}
            private = await self.read_private_contact(expected)
            if not isinstance(private, dict):
                return {}
            facts, email = private.get("facts", {}), private.get("expected_email")
            if (not isinstance(facts, dict) or not facts or any(type(v) not in (bool, int) for v in facts.values()) or facts.get("contact_binding_passed") is not True
                    or facts.get("test_payments_read") is not True or facts.get("redirects_followed") != 0 or not isinstance(email, str)
                    or not re.fullmatch(r"synthetic_[A-Za-z0-9_-]+@tryfolk\.invalid", email)):
                return {}
            fresh = await self.frame_context(context, run, fixture)
            if any(fresh.get(key) != identity.get(key) for key in ("approved", "provider_environment", "customer_correlated", "payment_correlated", "payment_id", "checkout_session_id", "order_id", "ownership", "payment_status", "payment_link")):
                return {}
            return {"email": email, "config_id": observed.get("config_id"), "generation": observed.get("frame_generation"),
                    "payment_id": identity["payment_id"], "checkout_session_id": identity["checkout_session_id"],
                    "ownership": identity["ownership"], "facts": {**facts, "current_identity_rechecked_after_private_email_read": True}}
        except (ValueError, TypeError, KeyError, OSError, TimeoutError):
            return {}
        finally:
            expected.clear()
