"""Gate dispatch with intact real preflight artifacts, never with fixture outcomes."""

import hashlib
import json
import os
from pathlib import Path
import re
from .browser_policy import DODO_CHECKOUT, DODO_SECURE_FRAME, exact_origin
from .dodo import correlate
from .target_provenance import UPSTREAM
from .browser_network import PUBLIC_SCRIPTS, FRAME_SCRIPTS
from .secure_frame import BROWSER_API
from .widget_bootstrap import BOOTSTRAP_PATHS

ADAPTER_FILES = (
    "integrations/stagehand/adapter.py",
    "src/tryfolk/browser_policy.py",
    "src/tryfolk/browser_settlement.py",
    "src/tryfolk/browser_observation.py",
    "src/tryfolk/observed_controls.py",
    "src/tryfolk/process.py",
    "src/tryfolk/browser_network.py",
    "integrations/stagehand/pyproject.toml",
    "integrations/stagehand/uv.lock",
    "integrations/stagehand/local_session.py",
    "src/tryfolk/secure_frame.py",
    "src/tryfolk/dodo.py",
    "src/tryfolk/checkout_timing.py",
    "src/tryfolk/widget_bootstrap.py",
    "src/tryfolk/browser_events.py",
    "src/tryfolk/response_boundary.py",
    "src/tryfolk/protocol_identity.py",
    "src/tryfolk/native_preflight.py",
    "src/tryfolk/native_card.py",
    "src/tryfolk/native_card_observation.py",
    "src/tryfolk/native_read_observation.py",
    "src/tryfolk/payment_privacy.py",
    "src/tryfolk/checkout_discovery.py",
    "src/tryfolk/manual_address.py",
    "src/tryfolk/checkout_navigation.py",
    "integrations/dodo/contact_binding.mjs",
    "integrations/dodo/read_contact.mjs",
)


def digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Validation requires intact regular private evidence files.")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assess(preflight, capture, association, healthy, root):
    def require(condition):
        if not condition:
            raise ValueError(
                "Actual Dodo runtime evidence is missing, stale or mismatched; dispatch stays gated."
            )

    source = preflight.get("source_digest")
    require(bool(re.fullmatch(r"[a-f0-9]{64}", source or "")))
    require(
        preflight.get("schema") == "tryfolk.stagehand-hosted-preflight.v1"
        and preflight.get("kind") == "actual_sdk_deterministic_form_preflight_not_persona_outcome"
        and preflight.get("status") == "passed"
        and preflight.get("model") == "gpt-6.1-sol"
        and preflight.get("stagehand") == "4.2.0"
        and preflight.get("persona_jobs") == 0
        and preflight.get("payment_submitted") is False
    )
    files = preflight.get("adapter_files", {})
    require(set(files) == set(ADAPTER_FILES))
    require(all(files[name] == digest(Path(root) / name) for name in ADAPTER_FILES))
    resources = preflight.get("resource_guard_evidence", [])
    require(
        all(
            any(
                event.get("url") == url
                and event.get("decision") == "public_script"
                and event.get("method") == "GET"
                and event.get("resource_type") == "script"
                and event.get("credential_headers_forwarded") is False
                and event.get("redirects_followed") == 0
                and event.get("bytes", 0) > 0
                and re.fullmatch(r"[a-f0-9]{64}", event.get("sha256") or "")
                for event in resources
            )
            for url in PUBLIC_SCRIPTS
        )
    )
    require(
        all(
            any(
                event.get("url") == url
                and event.get("decision") == "public_frame_script"
                and event.get("method") == "GET"
                and event.get("resource_type") == "script"
                and event.get("child_frame") is True
                and event.get("page_identity") == "primary"
                and event.get("credential_headers_forwarded") is False
                and event.get("redirects_followed") == 0
                and event.get("bytes", 0) > 0
                and re.fullmatch(r"[a-f0-9]{64}", event.get("sha256") or "")
                for event in resources
            )
            for url in FRAME_SCRIPTS
        )
    )
    require(
        any(
            event.get("url") == DODO_SECURE_FRAME
            and event.get("decision") == "public_frame"
            and event.get("resource_type") == "document"
            and event.get("child_frame") is True
            and event.get("credential_headers_forwarded") is False
            and event.get("redirects_followed") == 0
            and event.get("bytes", 0) > 0
            for event in resources
        )
    )
    contexts = {
        event["context"]["config_id"]: event["context"]
        for event in resources
        if event.get("decision") == "verified_frame_context"
        and event.get("context", {}).get("approved") is True
        and event["context"].get("provider_environment") == "test_mode"
        and event["context"].get("payment_correlated") is True
        and event["context"].get("parent_matches") is True
        and event["context"].get("api_base_verified") is True
        and event["context"].get("api_url") == BROWSER_API
        and event["context"].get("browser_api_role") == "observed_approved_shared_service_config"
        and event["context"].get("service_sandbox_only_attested") is False
        and event["context"].get("sandbox_key") is True
        and event["context"].get("secret_authenticated") is False
        and event["context"].get("payment_id")
        and re.fullmatch(r"[a-f0-9]{64}", event["context"].get("config_id") or "")
    }
    require(bool(contexts))
    require(all(
        any(
            event.get("decision") == "widget_bootstrap_response"
            and event.get("url") == BROWSER_API + path
            and event.get("method") == "GET"
            and event.get("status") == 200
            and event.get("child_frame") is True
            and event.get("page_identity") == "primary"
            and event.get("config_bound") is True
            and event.get("independent_identity_rechecked") is True
            and event.get("response_target_guarded") is True
            and event.get("native_preflight_bound") is True
            and event.get("config_id") in contexts
            for event in resources
        )
        for path in BOOTSTRAP_PATHS
    ))
    completed, native_cards = set(), set()
    for event in preflight.get("events", []):
        action = event.get("action", {}).get("_tryfolk", {})
        require(action.get("kind") != "pay")
        if action.get("field") and event.get("result", {}).get("field_completed") == action["field"]:
            if action["field"].startswith("card_"):
                require(
                    action.get("frame_url") == DODO_SECURE_FRAME
                    and action.get("frame_config_id") in contexts
                    and isinstance(action.get("frame_generation"), (int, float))
                    and action["frame_generation"] > 0
                    and event["result"].get("data", {}).get("success") is True
                )
                native_cards.add(action["field"])
            completed.add(action["field"])
        for field in event.get("observation", {}).get("field_evidence", []):
            if field.get("approved_value_present") is True:
                completed.add(field["field"])
    require({"country", "address", "city", "state", "postal", "card_number", "card_cvc"} <= completed)
    require("card_expiry" in completed or {"card_month", "card_year"} <= completed)
    require(
        {"card_number", "card_cvc"} <= native_cards
        and ("card_expiry" in native_cards or {"card_month", "card_year"} <= native_cards)
    )
    pay = preflight.get("pay_control_observed_not_activated", {})
    require(
        pay.get("method") == "click"
        and bool(pay.get("selector"))
        and pay.get("_tryfolk", {}).get("kind") == "pay"
    )
    require(exact_origin(pay["_tryfolk"]["url"]) == DODO_CHECKOUT)
    reset = preflight.get("reset", {})
    require(reset.get("revoked_accounts") == 1 and reset.get("evidence_preserved") is True)
    require(
        capture.get("schema") == "tryfolk.entire-source-preflight.v1"
        and capture.get("model") == "gpt-6.1-sol"
        and capture.get("source_digest") == source
        and capture.get("source_unchanged") is True
        and capture.get("protected_hooks_unchanged") is True
        and capture.get("status_exit_code") == 0
    )
    require(bool(re.fullmatch(r"[0-9A-Z]{26}", capture.get("checkpoint_id") or "")))
    require(
        association.get("checkpoint_id") == capture["checkpoint_id"]
        and association.get("registered_commit") == capture.get("captured_commit")
        and association.get("session_id") == capture.get("codex_session_id")
        and association.get("source_unchanged_by_trailer") is True
        and bool(re.fullmatch(r"[a-f0-9]{64}", association.get("transcript_sha256") or ""))
    )
    require(
        healthy.get("provider_fixture_injected") is False
        and healthy.get("matched_population_member") is False
        and healthy.get("signed_webhook_claimed") is False
        and healthy.get("manifest", {}).get("mode") == "healthy"
        and healthy["manifest"].get("source_sha256") == source
    )
    require(
        healthy.get("provider_receipt", {}).get("http_status") == 200
        and healthy.get("app_entitlement", {}).get("http_status") == 200
    )
    receipt, entitlement = healthy["provider_receipt"]["body"], healthy["app_entitlement"]["body"]
    orders = [
        o
        for o in healthy.get("orders_after", {}).get("body", {}).get("orders", [])
        if o.get("order_id") == receipt.get("ownership", {}).get("order_id")
    ]
    require(len(orders) == 1)
    verified = correlate(orders[0], receipt, entitlement)
    require(
        verified.get("measured") is True
        and verified.get("outcome") == "completed"
        and verified.get("payment_verified") is True
        and verified.get("entitlement_verified") is True
    )
    return {
        "status": "passed",
        "source_digest": source,
        "upstream_commit": UPSTREAM,
        "scope": "unpaid_browser_and_source_capture_preflight_with_independent_healthy_control",
        "population_validation": "pending",
        "signed_webhook_verified": False,
        "checkpoint_id": capture["checkpoint_id"],
        "adapter_files": files,
    }


def write_validation(preflight, capture, association, healthy, *, output, root):
    paths = [Path(x).resolve() for x in (preflight, capture, association, healthy)]
    values = [json.loads(p.read_text()) for p in paths]
    result = assess(*values, root)
    result["schema"] = "tryfolk.dodo-runtime-validation.v1"
    result["evidence"] = [{"path": str(p), "sha256": digest(p)} for p in paths]
    path = Path(output)
    if path.is_symlink():
        raise ValueError("Private runtime validation file cannot be a symlink.")
    with open(path, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as handle:
        json.dump(result, handle, indent=2)
    path.chmod(0o600)
    return result


def execution_ready(manifest, validation, root):
    try:
        digest(validation)
        proof = json.loads(Path(validation).read_text())
        if proof.get("schema") != "tryfolk.dodo-runtime-validation.v1" or len(proof.get("evidence", [])) != 4:
            return False
        values = []
        for item in proof["evidence"]:
            if digest(item["path"]) != item["sha256"]:
                return False
            values.append(json.loads(Path(item["path"]).read_text()))
        verified = assess(*values, root)
        return (
            verified["status"] == "passed"
            and proof.get("status") == "passed"
            and manifest["source_sha256"] == proof["source_digest"] == verified["source_digest"]
            and manifest.get("upstream_commit") == UPSTREAM
        )
    except (ValueError, KeyError, TypeError, OSError):
        return False
