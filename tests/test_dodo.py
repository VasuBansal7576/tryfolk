from copy import deepcopy
import asyncio
from contextvars import ContextVar
import pytest
from tryfolk.dodo import correlate
from tryfolk.dodo import DodoTarget, OWNERSHIP
import httpx


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "other_payment",
        "other_persona",
        "missing_payment",
        "fixture",
        "settled",
        "inactive",
        "poll_processing",
        "poll_succeeded",
        "poll_unknown",
        "other_customer",
        "missing_customer",
    ],
)
async def test_pre_pay_frame_identity_requires_fresh_independent_test_receipt(records, monkeypatch, mutation):
    order, receipt, _ = deepcopy(records)
    receipt.update(state="pending", reason="requires_payment_method")
    receipt["payment"]["status"] = "requires_payment_method"
    receipt["checkout"]["payment_status"] = "requires_payment_method"
    order["customer_id"] = "cus_fixture"
    receipt["payment"]["customer_id"] = "cus_fixture"
    receipt["private_payment_link"] = "https://test.checkout.dodopayments.com/Private1"
    if mutation == "other_customer":
        receipt["payment"]["customer_id"] = "cus_other"
    if mutation == "missing_customer":
        receipt["payment"].pop("customer_id")
    if mutation == "other_payment":
        receipt["payment"]["payment_id"] = "other"
    if mutation == "other_persona":
        receipt["payment"]["metadata"]["persona_id"] = "other"
    if mutation == "missing_payment":
        receipt["payment"] = None
    if mutation == "fixture":
        receipt["source"] = "external_provider_fixture"
    if mutation == "settled":
        receipt["state"] = "succeeded"
    if mutation == "inactive":
        order["active"] = False
    if mutation in ("poll_processing", "poll_succeeded", "poll_unknown"):
        status = {
            "poll_processing": "processing",
            "poll_succeeded": "succeeded",
            "poll_unknown": "unexpected",
        }[mutation]
        receipt["payment"]["status"] = receipt["checkout"]["payment_status"] = status
        receipt["state"] = "succeeded" if status == "succeeded" else "pending"
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/target/readiness":
            body = {
                "manifest": {
                    "app_origin": "http://127.0.0.1:3301",
                    "safe_origins": ["http://127.0.0.1:3301"],
                    "provider_environment": "test_mode",
                    "upstream_commit": "ef7cbd980d8f1e57b036dc2af71ff05521b6175a",
                    "source_clean": True,
                    "source_commit": "a" * 40,
                    "source_sha256": "b" * 64,
                    "checkout_origins": ["https://test.checkout.dodopayments.com"],
                    "target_revision": "rev",
                }
            }
        elif request.url.path == "/api/target/orders":
            body = {"orders": [order]}
        elif request.url.path == "/api/target/receipts":
            assert request.url.params["browser_parent"] == "1"
            body = receipt
        else:
            raise AssertionError("No additional endpoint or provider write authorized")
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **options: client(**options, transport=httpx.MockTransport(respond))
    )
    target = DodoTarget("http://127.0.0.1:3301")
    fixture = {key: order[key] for key in OWNERSHIP}
    result = await target.frame_context(
        {
            "payment_id": "pay",
            "api_base_verified": True,
            "api_url": "https://api.custom.hs.dodopayments.com",
            "browser_api_role": "observed_approved_shared_service_config",
            "service_sandbox_only_attested": False,
            "sandbox_key": True,
            "parent_matches": True,
            "operation": "poll" if mutation and mutation.startswith("poll_") else "bootstrap",
        },
        {"id": "r", "revision": "rev"},
        fixture,
    )
    assert result["approved"] is (mutation in (None, "poll_processing", "poll_succeeded"))
    assert result.get("secret_authenticated", False) is False
    assert all(method == "GET" for method, _ in calls)
    if mutation is None:
        assert result["customer_correlated"]
        assert result["payment_correlated"] and result["provider_environment"] == "test_mode"
        assert result["checkout_session_id"] == "cs"
        assert result["payment_link"] == receipt["private_payment_link"]


@pytest.fixture
def records():
    ownership = {
        "run_id": "r",
        "persona_id": "p",
        "account_id": "a",
        "fixture_id": "f",
        "target_revision": "rev",
        "order_id": "o",
        "expected_product_id": "product",
    }
    order = {
        **ownership,
        "checkout_session_id": "cs",
        "amount_minor": 1200,
        "currency": "USD",
        "active": True,
    }
    receipt = {
        "state": "succeeded",
        "source": "dodo_test_api",
        "ownership": ownership,
        "checkout_session_id": "cs",
        "evidence_id": "ev",
        "checkout": {"id": "cs", "payment_id": "pay", "payment_status": "succeeded"},
        "payment": {
            "payment_id": "pay",
            "status": "succeeded",
            "checkout_session_id": "cs",
            "metadata": {**ownership, "product_id": "product"},
            "product_cart": [{"product_id": "product", "quantity": 1}],
            "amount_minor": 1200,
            "currency": "USD",
        },
    }
    entitlement = {
        "source": "app_local_entitlement",
        "ownership": ownership,
        "has_access": False,
        "order_has_grant": False,
        "grant_count": 0,
        "active": True,
        "payment_id": "pay",
    }
    return order, receipt, entitlement


def test_dodo_independent_receipt_and_exact_order_entitlement(records):
    order, receipt, entitlement = records
    failed = correlate(order, receipt, entitlement, capability=False)
    assert failed["outcome"] == "failed" and failed["classification"] == "paid_without_access"
    assert failed["measured"] and failed["payment_verified"] and not failed["entitlement_verified"]
    entitlement.update(has_access=True, order_has_grant=True, grant_count=1)
    success = correlate(order, receipt, entitlement, capability=False)
    assert success["outcome"] == "completed" and success["capability_available"] is False
    assert success["entitlement_verified"]
    wrong = deepcopy(receipt)
    wrong["payment"]["metadata"]["persona_id"] = "other"
    assert correlate(order, wrong, entitlement)["classification"] == "correlation_mismatch"
    entitlement["payment_id"] = "other"
    assert correlate(order, receipt, entitlement)["classification"] == "entitlement_correlation_mismatch"


def test_dodo_pending_setup_and_fixtures_never_become_measured_success(records):
    order, receipt, entitlement = records
    receipt["state"] = "pending"
    assert correlate(order, receipt, entitlement)["outcome"] == "pending"


async def test_verification_retains_actual_http_envelopes_and_complete_order_body(records, monkeypatch):
    order, receipt, entitlement = deepcopy(records)
    order.update(created_at="unit-time", state="ready", granted=False, payment_id="pay", customer_id="unit-customer")
    calls = []
    bodies = {"/api/target/orders": {"orders": [order]}, "/api/target/receipts": receipt,
        "/api/target/entitlements": entitlement}

    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        return httpx.Response(200, json=bodies[request.url.path])

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: client(**options, transport=httpx.MockTransport(respond)))
    result = await DodoTarget("http://127.0.0.1:3301").verification(
        {"id": "r", "revision": "rev"}, {key: order[key] for key in OWNERSHIP}
    )
    assert [request.url.path for request in calls] == list(bodies)
    for key, request in zip(("orders_after", "provider_receipt", "app_entitlement"), calls):
        assert result["oracle_http"][key] == {"http_status": 200, "body": bodies[request.url.path],
            "method": "GET", "url": str(request.url), "redirects_followed": 0}
    assert "evidence_url" not in result["oracle_http"]["provider_receipt"]["body"]
    assert result["order"]["order_id"] == "o"
    receipt["state"] = "setup_required"
    assert correlate(order, receipt, entitlement)["outcome"] == "blocked"
    receipt["source"] = "external_provider_fixture"
    receipt["state"] = "succeeded"
    assert not correlate(order, receipt, entitlement)["measured"]
    assert correlate(order, receipt, entitlement)["outcome"] == "blocked"


@pytest.mark.parametrize("settled", [False, True])
async def test_legacy_response_observer_can_verify_without_internal_transport_keywords(records, monkeypatch, settled):
    """Invented offline responses through the preserved healthy21/22 observer pattern."""
    order, receipt, entitlement = deepcopy(records)
    entitlement.update(has_access=True, order_has_grant=True, grant_count=1)
    if not settled:
        receipt["state"] = "pending"
        receipt["payment"]["status"] = receipt["checkout"]["payment_status"] = "requires_payment_method"
    bodies = {"/api/target/orders": {"orders": [order]}, "/api/target/receipts": receipt,
              "/api/target/entitlements": entitlement}
    calls = []

    def respond(request):
        assert request.method == "GET"
        calls.append(request.url.path)
        return httpx.Response(200, json=bodies[request.url.path])

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **opts: client(**opts, transport=httpx.MockTransport(respond)))

    class ResponseObserver(DodoTarget):
        def __init__(self, origin):
            super().__init__(origin)
            self.capture = ContextVar("offline_response_observer", default=None)

        async def request(self, method, path, **kwargs):
            capture = self.capture.get()
            if not (capture and capture[0] is asyncio.current_task() and method == "GET" and path in bodies):
                return await super().request(method, path, **kwargs)
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15) as observed:
                response = await observed.request(method, self.origin + path, headers=self.headers, **kwargs)
                response.raise_for_status()
                body = response.json()
                capture[1][path] = {"http_status": response.status_code, "body": deepcopy(body)}
                return body

        async def verification(self, run, fixture, capability=None):
            self.responses = {}
            token = self.capture.set((asyncio.current_task(), self.responses))
            try:
                return await super().verification(run, fixture, capability)
            finally:
                self.capture.reset(token)

    target = ResponseObserver("http://127.0.0.1:3338")
    result = await target.verification({"id": "r", "revision": "rev"}, {key: order[key] for key in OWNERSHIP})
    assert result["outcome"] == ("completed" if settled else "pending")
    assert calls == list(bodies)
    assert all(value["http_status"] == 200 for value in target.responses.values())
    # A legacy body observer does not produce the new production envelopes.
    # Preserve that evidence gap instead of inventing request metadata.
    assert result["oracle_http"] == {"orders_after": {}, "provider_receipt": {}, "app_entitlement": {}}


async def test_delegating_observer_keeps_concurrent_account_response_evidence_separate(records, monkeypatch):
    """Invented responses; interleaved reads never become another account's evidence."""
    bodies = {}
    fixtures = []
    for label in ("first", "second"):
        order, receipt, entitlement = deepcopy(records)
        ownership = {key: label + "_" + key for key in (*OWNERSHIP, "order_id", "expected_product_id")}
        order.update(ownership)
        receipt["ownership"] = entitlement["ownership"] = ownership
        receipt["payment"]["metadata"] = {**ownership, "product_id": ownership["expected_product_id"]}
        receipt["payment"]["product_cart"] = [{"product_id": ownership["expected_product_id"], "quantity": 1}]
        entitlement.update(has_access=True, order_has_grant=True, grant_count=1)
        bodies[ownership["account_id"]] = {"orders": [order]}
        bodies[ownership["order_id"]] = {"receipt": receipt, "entitlement": entitlement}
        fixtures.append({key: order[key] for key in OWNERSHIP})

    async def respond(request):
        assert request.method == "GET"
        await asyncio.sleep(0)
        if request.url.path == "/api/target/orders":
            body = bodies[request.url.params["account_id"]]
        else:
            kind = "receipt" if request.url.path == "/api/target/receipts" else "entitlement"
            body = bodies[request.url.params["order_id"]][kind]
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **opts: client(**opts, transport=httpx.MockTransport(respond)))

    class DelegatingObserver(DodoTarget):
        async def request(self, method, path, **kwargs):
            return await super().request(method, path, **kwargs)

    target = DelegatingObserver("http://127.0.0.1:3338")
    results = await asyncio.gather(*(target.verification(
        {"id": fixture["run_id"], "revision": fixture["target_revision"]}, fixture
    ) for fixture in fixtures))
    for result, fixture in zip(results, fixtures):
        assert result["outcome"] == "completed"
        for key, envelope in result["oracle_http"].items():
            assert envelope["http_status"] == 200 and envelope["method"] == "GET"
            assert envelope["redirects_followed"] == 0
            body = envelope["body"]
            observed_account = body["orders"][0]["account_id"] if key == "orders_after" else body["ownership"]["account_id"]
            assert observed_account == fixture["account_id"]

    first_result_orders = deepcopy(results[0]["oracle_http"]["orders_after"])
    await target.request("GET", "/api/target/orders", params={"account_id": fixtures[1]["account_id"]})
    assert results[0]["oracle_http"]["orders_after"] == first_result_orders


async def test_private_contact_callback_uses_owned_source_contract_and_fresh_identity(monkeypatch, tmp_path):
    import json
    from tryfolk.dodo import DodoTarget

    source = {"source_commit": "a" * 40, "source_sha256": "b" * 64}
    ownership = {key: key + "_fixture" for key in OWNERSHIP}
    fixture = {**ownership, "login_url": "http://127.0.0.1:3300/private", "login_token": "fixture_private"}
    run = {"id": ownership["run_id"], "revision": ownership["target_revision"]}
    order = {
        **ownership,
        "order_id": "order_fixture",
        "checkout_session_id": "cks_fixture",
        "customer_id": "cus_fixture",
        "active": True,
        "amount_minor": 100,
        "currency": "USD",
        "expected_product_id": "pdt_fixture",
    }
    identity = {
        "approved": True,
        "provider_environment": "test_mode",
        "customer_correlated": True,
        "payment_id": "pay_fixture",
        "checkout_session_id": "cks_fixture",
        "order_id": "order_fixture",
        "ownership": ownership,
    }
    target = DodoTarget("http://127.0.0.1:3300")

    async def registration():
        return {"manifest": source}

    async def request(method, path, **kwargs):
        assert method == "GET" and path == "/api/target/orders"
        return {"orders": [order]}

    target.registration, target.request = registration, request

    async def frame(observed, *args):
        return identity.copy()

    target.frame_context = frame
    calls = []

    async def private_read(expected):
        calls.append(expected.copy())
        return {
            "facts": {"contact_binding_passed": True, "test_payments_read": True, "redirects_followed": 0},
            "expected_email": "synthetic_fixture@tryfolk.invalid",
        }

    target.read_private_contact = private_read
    path = tmp_path / "contract.json"
    path.write_text(
        json.dumps(
            {
                "kind": "source_fixed_customer_creation_contract_not_provider_name_readback",
                "source_commit": "a" * 40,
                "source_digest": "b" * 64,
                "checks": {
                    name: True
                    for name in [
                        "synthetic_email_creation_contract",
                        "exact_fixed_name_creation_contract",
                        "created_customer_used_in_checkout",
                        "name_email_editing_disabled",
                        "receipt_customer_id_order_check",
                        "receipt_customer_email_account_check",
                        "private_authenticated_payment_link_projection",
                        "owned_navigation_customization_closed",
                        "official_SDK_hardcoded_Test_mode",
                        "owned_return_origin_binding",
                        "card_only_checkout",
                    ]
                },
                "passed": True,
            }
        )
    )
    monkeypatch.setenv("TRYFOLK_DODO_CONTACT_CONTRACT", str(path))
    observed = {
        "payment_id": "pay_fixture",
        "checkout_session_id": "cks_fixture",
        "config_id": "c" * 64,
        "frame_generation": 1234,
    }
    result = await DodoTarget.private_contact(target, observed, run, fixture)
    assert result["email"] == "synthetic_fixture@tryfolk.invalid"
    assert result["facts"]["current_identity_rechecked_after_private_email_read"]
    assert result["ownership"] == ownership and calls[0]["email"] is None
    identity["ownership"] = {**ownership, "account_id": "other"}
    assert not await DodoTarget.private_contact(target, observed, run, fixture)
    assert len(calls) == 1
    path.write_text("{}")
    assert not await DodoTarget.private_contact(target, observed, run, fixture)
