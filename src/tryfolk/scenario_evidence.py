"""Admit measured evidence for the two existing success-card customer cases.

Saved stories and declarations cannot establish browser conditions or payment.
This reader never performs a provider call, fulfillment, or a browser action.
"""

import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .dodo import OWNERSHIP, correlate

FIXTURE = "dodo-public-success-card-us-desktop-v1"
SUPPORTED = frozenset(("one-time-card-download", "paid-no-access-download-retest"))
CONDITIONS = {
    "fixture_id": FIXTURE,
    "account_history": "new",
    "device": "desktop Chromium",
    "locale": "en-US",
    "billing_country": "US",
}
HASHES = (
    "scenario_id",
    "scenario_sha256",
    "saved_persona_sha256",
    "profile_sha256",
    "initial_memory_sha256",
    "condition_fixture_sha256",
)
DOWNLOAD_CHECKS = (
    "explicit_download_preparation_available",
    "same_primary_download_page",
    "single_exact_owned_protected_download",
    "prepared_download_identity_fresh",
    "download_source_document_continuously_current",
)


def binding(run, persona, fixture):
    assignment = persona["assigned_scenario"]
    return {
        **{key: fixture[key] for key in OWNERSHIP},
        **{key: assignment[key] for key in HASHES},
        "condition_fixture_id": assignment["condition_fixture_snapshot"]["fixture_id"],
    }


def expiry_completed(fields):
    return "card_expiry" in fields or {"card_month", "card_year"}.issubset(fields)


def checkout_observation(guard, completed_fields):
    """Only completed policy-validated inputs plus the actually admitted CONFIRM.

    Input values and private browser protocol data never leave the SDK process.
    """
    attempt = guard.card_attempt
    fields = {"country", "card_number", "card_cvc"}
    if (
        not attempt
        or not attempt.confirmed
        or not fields.issubset(completed_fields)
        or not expiry_completed(completed_fields)
    ):
        return None
    fields |= {"card_expiry", "card_month", "card_year"}.intersection(completed_fields)
    return {
        "source": "stagehand_validated_test_checkout",
        "fixture_id": FIXTURE,
        "completed_fields": sorted(fields),
        "billing_country": "US",
        "accepted_native_confirm": True,
        "checkout_session_id": attempt.checkout_session_id,
        "payment_id": attempt.context.get("payment_id"),
    }


def file_verified(capability, evidence_root, expected_binding=None, origin=None):
    if not isinstance(capability, dict) or capability.get("source") != "stagehand_browser_download":
        return False
    admission = capability.get("download_admission") or {}
    download = capability.get("download_binding") or {}
    if not isinstance(admission, dict) or not isinstance(download, dict):
        return False
    artwork = download.get("artwork_id")
    if (
        not expected_binding
        or not origin
        or any(download.get(k) != v for k, v in expected_binding.items())
        or admission.get("download_binding") != download
        or type(artwork) is not int
        or artwork not in range(12)
        or download.get("download_url") != origin.rstrip("/") + "/api/target/download?artwork=" + str(artwork)
        or capability.get("available") is not True
        or admission.get("decision") != "owned_download_event"
        or admission.get("first_failed_predicate") is not None
        or admission.get("admission_phase") != "actual_primary_download_event_before_save"
        or any(admission.get("owned_navigation_checks", {}).get(k) is not True for k in DOWNLOAD_CHECKS)
    ):
        return False
    try:
        path = Path(capability["download_path"]).resolve(strict=True)
        if not path.is_relative_to(evidence_root.resolve()) or not path.is_file():
            return False
        with path.open("rb") as source:
            raw = source.read(1_000_001)
        import hashlib

        return (
            0 < len(raw) <= 1_000_000
            and len(raw) == capability.get("bytes")
            and b"<svg" in raw[:1024]
            and hashlib.sha256(raw).hexdigest() == capability.get("download_sha256")
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def oracle_verified(result, origin):
    """Actual read-only response envelopes must agree with normalized evidence."""
    order, receipt, grant = result["order"], result["receipt"], result["app_entitlement"]
    responses = result.get("oracle_http") or {}
    if not isinstance(responses, dict):
        return False
    expected = (
        ("orders_after", "/api/target/orders", {"account_id": order["account_id"]}),
        ("provider_receipt", "/api/target/receipts", {"order_id": order["order_id"]}),
        (
            "app_entitlement",
            "/api/target/entitlements",
            {"account_id": order["account_id"], "order_id": order["order_id"]},
        ),
    )
    for key, path, params in expected:
        response = responses.get(key) or {}
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("body"), dict)
            or response.get("http_status") != 200
            or response.get("method") != "GET"
            or response.get("redirects_followed") != 0
            or response.get("url") != origin.rstrip("/") + path + "?" + urlencode(params)
        ):
            return False
    orders = responses["orders_after"].get("body", {}).get("orders")
    return (
        isinstance(orders, list)
        and len(orders) == 1
        and isinstance(orders[0], dict)
        and all(orders[0].get(key) == value for key, value in order.items())
        and responses["provider_receipt"].get("body")
        == {k: v for k, v in receipt.items() if k != "evidence_url"}
        and responses["app_entitlement"].get("body") == grant
    )


def observed(run, persona, result, events, findings, evidence_root, population_error=None):
    assignment = persona.get("assigned_scenario") or {}
    selected = [e for e in events if e["run_id"] == run["id"] and e["persona_id"] == persona["id"]]
    output = {
        "scenario_id": assignment.get("scenario_id"),
        "provider": result.get("provider"),
        "finding_ids": [],
        "classification": "not_exercised",
        "condition_fixture_verified": False,
        "journey_outcome": result.get("outcome"),
        "journey_classification": result.get("classification"),
        "measured": False,
        "payment_verified": False,
        "entitlement_verified": False,
        "capability_checked": False,
        "capability_available": None,
        "evidence_event_ids": [],
        "exercise_gate": "No complete measured per-persona evidence binding.",
        "exercised": False,
    }

    def blocked(message, classification="blocked_inconclusive"):
        return {**output, "classification": classification, "exercise_gate": message}

    if population_error:
        return blocked(population_error)
    scenario = assignment.get("scenario_snapshot") or {}
    if (
        assignment.get("scenario_id") not in SUPPORTED
        or scenario.get("condition_fixture") != {"id": FIXTURE, "registration": "existing_driver"}
        or scenario.get("required_capabilities", {}).get("conditions") != CONDITIONS
        or assignment.get("condition_fixture_snapshot") != CONDITIONS
    ):
        return blocked(
            "Only the two registered existing success-card scenarios have evidence admission.",
            "not_exercised",
        )
    if not result or result.get("measured") is not True or run.get("provider") != "dodo":
        return blocked(
            "Fixtures, assignments and unfinished journeys are not measured case coverage.", "not_exercised"
        )
    try:

        def one(kind):
            matches = [e for e in selected if e["kind"] == kind]
            if len(matches) != 1:
                raise ValueError("Exactly one owned " + kind + " observation is required.")
            return matches[0]

        verification = one("verification")
        if verification["data"] != result:
            raise ValueError("Persisted verification does not match this persona outcome.")
        order, receipt, grant = result["order"], result["receipt"], result["app_entitlement"]
        if (
            any(
                order.get(k) != v
                for k, v in {
                    "run_id": run["id"],
                    "persona_id": persona["id"],
                    "target_revision": run["revision"],
                    "account_id": result.get("account_id"),
                }.items()
            )
            or not order.get("fixture_id")
            or not order.get("account_id")
            or order.get("active") is not True
        ):
            raise ValueError("Order ownership/revision must match this exact saved customer and run.")
        if not oracle_verified(result, run["preview_url"]):
            raise ValueError(
                "Actual same-cycle HTTP200 orders/receipt/entitlement GET envelopes are required."
            )
        truth = correlate(order, receipt, grant, result.get("capability_available"))
        if truth.get("payment_verified") is not True or any(
            truth.get(k) != result.get(k)
            for k in ("outcome", "classification", "payment_verified", "entitlement_verified")
        ):
            raise ValueError("Independent provider receipt and account entitlement correlation failed.")
        output.update(
            measured=True, payment_verified=True, entitlement_verified=truth["entitlement_verified"]
        )
        expected = binding(run, persona, order)
        fixture_event, condition_event = one("scenario_fixture"), one("scenario_payment_condition")
        fixture, condition = fixture_event["data"], condition_event["data"]
        if fixture.get("binding") != expected or condition.get("binding") != expected:
            raise ValueError(
                "Actual condition observations do not bind the saved assignment/profile/memory and account."
            )
        manifest = fixture["source_manifest"]
        origin = urlsplit(run["preview_url"])
        if (
            origin.scheme != "http"
            or origin.hostname != "127.0.0.1"
            or not origin.port
            or origin.port not in range(3300, 3400)
            or origin.path not in ("", "/")
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
            or not re.fullmatch(r"[a-f0-9]{40}", run.get("source_commit") or "")
            or not re.fullmatch(r"[a-f0-9]{64}", run.get("source_digest") or "")
            or any(
                manifest.get(k) != v
                for k, v in {
                    "target_revision": run["revision"],
                    "source_commit": run["source_commit"],
                    "source_sha256": run["source_digest"],
                    "app_origin": run["preview_url"],
                    "provider_environment": "test_mode",
                    "source_clean": True,
                    "mode": run.get("target_mode"),
                }.items()
            )
        ):
            raise ValueError("Registered owned source manifest is missing or changed.")
        account, browser = fixture["account"], fixture["browser"]
        if (
            account
            != {
                "source": "dodo_owned_fixture_provision",
                "ownership": {k: order[k] for k in OWNERSHIP},
                "history": "new",
            }
            or browser.get("source") != "stagehand_owned_context"
            or browser.get("fresh_context") is not True
            or browser.get("locale") != "en-US"
            or browser.get("has_touch") is not False
            or not re.search(r"(?:Chrome|Chromium)/", browser.get("user_agent", ""))
            or "Mobile" in browser.get("user_agent", "")
            or any(
                type(browser.get("viewport", {}).get(k)) is not int or browser["viewport"][k] <= 0
                for k in ("width", "height")
            )
        ):
            raise ValueError(
                "Fresh account and observed isolated desktop Chromium/en-US conditions are required."
            )
        observed_condition = condition["observation"]
        if (
            observed_condition.get("source") != "stagehand_validated_test_checkout"
            or observed_condition.get("fixture_id") != FIXTURE
            or observed_condition.get("billing_country") != "US"
            or observed_condition.get("accepted_native_confirm") is not True
            or not {"country", "card_number", "card_cvc"}.issubset(
                observed_condition.get("completed_fields", [])
            )
            or not expiry_completed(observed_condition.get("completed_fields", []))
            or observed_condition.get("checkout_session_id") != order["checkout_session_id"]
            or observed_condition.get("payment_id") != receipt["payment"]["payment_id"]
        ):
            raise ValueError(
                "Approved US card field completion and actual owned native confirmation are required."
            )
        output.update(
            condition_fixture_verified=True,
            evidence_event_ids=[fixture_event["id"], condition_event["id"], verification["id"]],
        )
        output["finding_ids"] = [
            f["id"]
            for f in findings
            if f.get("run_id") == run["id"]
            and f.get("persona_id") == persona["id"]
            and f.get("evidence") == result
        ]
        if truth["classification"] == "decline_handled":
            return blocked(
                "Provider decline is handled; it does not complete this success-card scenario.",
                "expected_provider_decline",
            )
        if truth["outcome"] == "pending":
            return blocked("Provider/entitlement state has not settled.", "pending")
        if truth["classification"] == "paid_without_access":
            return {**output, "classification": "app_defect", "exercise_gate": None, "exercised": True}
        if truth["classification"] != "payment_and_entitlement":
            return blocked(
                "Observed outcome does not establish this scenario's fulfillment or settled access defect."
            )
        capability_event = one("capability")
        capability = result.get("capability_evidence")
        download_owner = {
            k: order[k] for k in (*OWNERSHIP, "order_id", "expected_product_id", "checkout_session_id")
        }
        download_owner["payment_id"] = receipt["payment"]["payment_id"]
        if (
            capability_event["id"] >= verification["id"]
            or capability_event["data"] != capability
            or not file_verified(capability, evidence_root, download_owner, run["preview_url"])
        ):
            raise ValueError(
                "Actual protected download must match account/order/revision/payment/product and selected artwork, file bytes and SHA."
            )
        output.update(capability_checked=True, capability_available=True)
        output["evidence_event_ids"].append(capability_event["id"])
        return {**output, "classification": "fulfilled", "exercise_gate": None, "exercised": True}
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return blocked(str(error))
