"""External SDK/native records are disclosed fixtures; these checks never prove Dodo payment."""

import json
from types import SimpleNamespace

import pytest

from tryfolk.manual_address import ManualAddressGate
from tryfolk.pre_submission import adapter_safety, runtime_gate, signature, verified_receipt


def facts():
    return (
        ManualAddressGate("manual_document_unstable"),
        {"call": "manual_address_effect_verification"},
        SimpleNamespace(provider="dodo", origin="http://127.0.0.1:3301"),
        {"url": "https://test.checkout.dodopayments.com/fixture-checkout"},
    )


def test_typed_manual_stage_records_pre_submission_without_url_or_message_inference():
    proof = adapter_safety(*facts(), financial_started=False, card_attempt=None)
    assert proof["stage"] == "manual_address" and proof["financial_submission_started"] is False
    error, boundary, policy, document = facts()
    assert (
        adapter_safety(
            RuntimeError(str(error)), boundary, policy, document, financial_started=False, card_attempt=None
        )
        is None
    )


@pytest.mark.parametrize(
    "kind", ["pay", "armed", "confirmed", "production", "unknown_gate", "unknown_boundary"]
)
def test_financial_or_unfamiliar_adapter_states_never_create_retry_safety(kind):
    error, boundary, policy, document = facts()
    attempt = None
    if kind == "armed":
        attempt = SimpleNamespace(armed_at=1, confirmed=False)
    elif kind == "confirmed":
        attempt = SimpleNamespace(armed_at=None, confirmed=True)
    elif kind == "production":
        document["url"] = "https://checkout.dodopayments.com/checkout"
    elif kind == "unknown_gate":
        error = ManualAddressGate("unknown")
    elif kind == "unknown_boundary":
        boundary["call"] = "dispatch"
    assert (
        adapter_safety(
            error, boundary, policy, document, financial_started=kind == "pay", card_attempt=attempt
        )
        is None
    )


def test_runtime_requires_owned_manifest_and_matching_private_adapter_error_bytes(tmp_path):
    raw = adapter_safety(*facts(), financial_started=False, card_attempt=None)
    path = tmp_path / "stagehand-error-fixture.json"
    path.write_text(json.dumps({"pre_submission_evidence": raw, "error_type": "ManualAddressGate"}))
    path.chmod(0o600)
    error = RuntimeError("external SDK fixture")
    error.pre_submission_evidence, error.evidence_path = raw, str(path)
    run = {
        "id": "fixture-run",
        "provider": "dodo",
        "revision": "fixture-rev",
        "source_commit": "a" * 40,
        "source_digest": "b" * 64,
        "target_mode": "broken",
        "preview_url": "http://127.0.0.1:3301",
    }
    manifest = {
        "target_revision": "fixture-rev",
        "source_commit": "a" * 40,
        "source_sha256": "b" * 64,
        "mode": "broken",
        "provider_environment": "test_mode",
        "app_origin": run["preview_url"],
        "source_clean": True,
    }
    fixture = {"_retry_target_manifest": manifest}
    assert runtime_gate(error, run, {"id": "fixture-persona"}, fixture, tmp_path) is not None
    manifest["provider_environment"] = "live_mode"
    assert runtime_gate(error, run, {"id": "fixture-persona"}, fixture, tmp_path) is None
    manifest["provider_environment"] = "test_mode"
    path.write_text(json.dumps({"error_type": "ManualAddressGate"}))
    assert runtime_gate(error, run, {"id": "fixture-persona"}, fixture, tmp_path) is None


def test_private_server_seal_rejects_changed_receipt_and_changed_state():
    payload = {
        "schema": "tryfolk.baseline-retry-safety.v1",
        "state": {"fixture": "original"},
        "evidence": {"fixture": True},
    }
    receipt = {**payload, "seal": signature(payload, "fixture-owner-key")}
    assert verified_receipt(receipt, {"fixture": "original"}, "fixture-owner-key")
    assert not verified_receipt(receipt, {"fixture": "changed"}, "fixture-owner-key")
    receipt["evidence"] = {"fixture": "changed"}
    assert not verified_receipt(receipt, {"fixture": "original"}, "fixture-owner-key")
