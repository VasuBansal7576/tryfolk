"""Closed native pre-submission evidence and private server-sealed retry safety.

These receipts attest adapter stages, never payment success or provider balances.
Unfamiliar errors and ambiguous SDK failures deliberately produce no receipt.
"""

import hashlib
import hmac
import json
import re
from pathlib import Path

from .manual_address import ManualAddressGate
from .scenarios import digest

SCHEMA = "tryfolk.baseline-retry-safety.v1"
REASONS = frozenset(
    {
        "manual_document_unstable",
        "manual_document_changed",
        "manual_effect_partial",
        "manual_country_currently_unverified",
        "manual_entry_open_unverified",
        "manual_control_unresolved",
        "bounded_manual_effect_unobserved",
    }
)


class PreSubmissionGate(RuntimeError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.pre_submission_evidence = evidence


def adapter_safety(error, boundary, policy, document, *, financial_started, card_attempt):
    if (
        not isinstance(error, ManualAddressGate)
        or error.reason not in REASONS
        or boundary.get("call") != "manual_address_effect_verification"
        or not policy
        or policy.provider != "dodo"
        or not document
        or not isinstance(document.get("url"), str)
        or not document["url"].startswith("https://test.checkout.dodopayments.com/")
        or financial_started
        or (card_attempt and (card_attempt.armed_at is not None or card_attempt.confirmed))
    ):
        return None
    return {
        "stage": "manual_address",
        "reason": error.reason,
        "financial_submission_started": False,
        "financial_poll_started": False,
        "owned_origin": policy.origin,
        "checkout_origin": "https://test.checkout.dodopayments.com",
    }


def runtime_gate(error, run, persona, fixture, evidence_root):
    raw = getattr(error, "pre_submission_evidence", None)
    if not raw:
        return None
    manifest = fixture.get("_retry_target_manifest") or {}
    expected = {
        "target_revision": run["revision"],
        "source_commit": run.get("source_commit"),
        "source_sha256": run.get("source_digest"),
        "mode": run.get("target_mode"),
        "provider_environment": "test_mode",
        "app_origin": run["preview_url"],
        "source_clean": True,
    }
    if (
        run["provider"] != "dodo"
        or raw.get("stage") != "manual_address"
        or raw.get("reason") not in REASONS
        or raw.get("financial_submission_started") is not False
        or raw.get("financial_poll_started") is not False
        or raw.get("owned_origin") != run["preview_url"]
        or raw.get("checkout_origin") != "https://test.checkout.dodopayments.com"
        or any(manifest.get(k) != v for k, v in expected.items())
    ):
        return None
    try:
        root = Path(evidence_root).resolve()
        path = Path(error.evidence_path).resolve()
        if (
            path.parent != root
            or not path.name.startswith("stagehand-error-")
            or path.suffix != ".json"
            or path.stat().st_mode & 0o077
        ):
            return None
        data = path.read_bytes()
        document = json.loads(data)
        if (
            document.get("pre_submission_evidence") != raw
            or document.get("error_type") != "ManualAddressGate"
        ):
            return None
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return PreSubmissionGate(
        str(error),
        {
            **raw,
            "provider_environment": "test_mode",
            "run_id": run["id"],
            "persona_id": persona["id"],
            "target_revision": run["revision"],
            "target_mode": run.get("target_mode"),
            "source_commit": run.get("source_commit"),
            "source_digest": run.get("source_digest"),
            "evidence_sha256": hashlib.sha256(data).hexdigest(),
        },
    )


def bound_state(service, db, run):
    population = service.store.load(db, "populations", run["population_id"])
    money = db.execute("SELECT * FROM reservations WHERE id=?", (run["reservation_id"],)).fetchone()
    events = [
        {**dict(row), "data": json.loads(row["data"])}
        for row in db.execute("SELECT * FROM events WHERE run_id=? ORDER BY id", (run["id"],))
    ]
    return {
        "run_id": run["id"],
        "population_id": run["population_id"],
        "population_sha256": digest(population),
        "source_commit": run.get("source_commit"),
        "source_digest": run.get("source_digest"),
        "events_sha256": digest(events),
        "historical_spent_microusd": money["spent"],
        "release_microusd": money["reserved"] - money["spent"],
    }


def signature(payload, secret):
    return hmac.new(secret.encode(), digest(payload).encode(), hashlib.sha256).hexdigest()


def seal_failure(service, db, run, error):
    if not isinstance(error, PreSubmissionGate) or not service.settings.owner_token:
        return None
    evidence = error.pre_submission_evidence
    population = service.store.load(db, "populations", run["population_id"]) or []
    if (
        run["provider"] != "dodo"
        or run["pass"] != "before"
        or run.get("retry_of_run_id")
        or run["outcomes"]
        or evidence.get("run_id") != run["id"]
        or evidence.get("persona_id") not in [p["id"] for p in population]
        or evidence.get("stage") != "manual_address"
        or evidence.get("reason") not in REASONS
        or evidence.get("financial_submission_started") is not False
        or evidence.get("financial_poll_started") is not False
        or evidence.get("provider_environment") != "test_mode"
        or evidence.get("target_revision") != run["revision"]
        or evidence.get("target_mode") != run.get("target_mode")
        or evidence.get("source_commit") != run.get("source_commit")
        or evidence.get("source_digest") != run.get("source_digest")
        or not re.fullmatch(r"[a-f0-9]{64}", evidence.get("evidence_sha256", ""))
    ):
        return None
    payload = {"schema": SCHEMA, "state": bound_state(service, db, run), "evidence": evidence}
    return {**payload, "seal": signature(payload, service.settings.owner_token)}


def verified_receipt(receipt, state, secret):
    if (
        not isinstance(receipt, dict)
        or not secret
        or receipt.get("schema") != SCHEMA
        or receipt.get("state") != state
    ):
        return False
    payload = {k: v for k, v in receipt.items() if k != "seal"}
    return isinstance(receipt.get("seal"), str) and hmac.compare_digest(
        receipt["seal"], signature(payload, secret)
    )
