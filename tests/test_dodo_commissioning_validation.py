"""Synthetic boundary fixtures only; these never enter runtime evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tryfolk import dodo_commissioning_validation as gate
from tryfolk.dodo import correlate
from tryfolk.dodo_runtime_validation import ADAPTER_FILES, digest, execution_ready
from tryfolk.scenarios import digest as semantic_digest
from tryfolk.target_provenance import UPSTREAM


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def diagnostic(tmp_path, monkeypatch):
    # All sponsor/SDK responses below are clearly unit-only boundary fixtures.
    for name in ADAPTER_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unit-only source")
    sdk = {k: {"version": v} for k, v in gate.SDK_PINS.items()}
    sdk["tinytroupe"]["direct_url"] = {"vcs_info": {"commit_id": gate.TINY_COMMIT}}
    monkeypatch.setattr(gate, "installed_sdk_identity", lambda _: deepcopy(sdk))
    monkeypatch.setattr(gate, "source_fingerprint", lambda _: "a" * 64)
    monkeypatch.setattr(gate, "dodo_provenance", lambda *_: {})
    build = {"source_clean": True, "source_commit": "b" * 40, "source_sha256": "a" * 64}
    save(tmp_path / "dodo-target/.target-manifest.json", build)
    (tmp_path / "dodo-target/LICENSE.md").write_text("unit-only license")
    population = [{"id": str(n) * 32, "initial_snapshot": {"unit": n},
                   "initial_semantic_memory": [{"unit": n}]} for n in range(1, 5)]
    cohort = save(tmp_path / "population.json", {"population": population})
    origin, run_id, persona_id, trace_id = "http://127.0.0.1:3300", "c" * 32, "1" * 32, "d" * 32
    manifest = {**build, "target_revision": "sha256:" + "e" * 64, "mode": "healthy", "fault": None,
                "app_origin": origin, "safe_origins": [origin], "checkout_origins": ["https://test.checkout.dodopayments.com"],
                "upstream_commit": UPSTREAM, "product": {"amount_minor": 100, "currency": "USD",
                                                         "type": "one-time", "product_id": "unit-product"}}
    own = {"run_id": run_id, "persona_id": persona_id, "account_id": "unit-account", "fixture_id": "unit-fixture",
           "target_revision": manifest["target_revision"], "order_id": "unit-order", "expected_product_id": "unit-product"}
    order = {**own, "checkout_session_id": "unit-checkout", "amount_minor": 100, "currency": "USD", "active": True}
    receipt = {"source": "dodo_test_api", "state": "succeeded", "ownership": own,
               "checkout_session_id": "unit-checkout", "checkout": {"id": "unit-checkout", "payment_id": "unit-pay", "payment_status": "succeeded"},
               "payment": {"payment_id": "unit-pay", "status": "succeeded", "checkout_session_id": "unit-checkout",
                           "metadata": {**own, "product_id": "unit-product"}, "product_cart": [{"product_id": "unit-product", "quantity": 1}],
                           "amount_minor": 100, "currency": "USD"}}
    entitlement = {"source": "app_local_entitlement", "ownership": own, "has_access": True, "order_has_grant": True,
                   "grant_count": 1, "active": True, "payment_id": "unit-pay"}

    def http(path, query, body):
        return {"http_status": 200, "method": "GET", "redirects_followed": 0,
                "url": origin + path + "?" + query, "body": body}

    svg = tmp_path / "download.svg"
    svg.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><path/></svg>')
    binding = {**own, "checkout_session_id": "unit-checkout", "payment_id": "unit-pay",
               "download_url": origin + "/api/target/download?artwork_id=1"}
    cap = {"available": True, "source": "stagehand_browser_download", "download_path": str(svg),
           "download_sha256": digest(svg), "bytes": svg.stat().st_size, "download_binding": binding,
           "download_admission": {"decision": "owned_download_event", "admission_phase": "actual_primary_download_event_before_save",
                                  "first_failed_predicate": None, "owned_navigation_checks": {"unit": True}, "download_binding": binding}}
    outcome = {**correlate(order, receipt, entitlement, True), "order": order, "capability_evidence": cap,
               "oracle_http": {"orders_after": http("/api/target/orders", "account_id=unit-account", {"orders": [order]}),
                               "provider_receipt": http("/api/target/receipts", "order_id=unit-order", receipt),
                               "app_entitlement": http("/api/target/entitlements", "account_id=unit-account&order_id=unit-order", entitlement)}}
    events = [{"kind": "browser_action", "persona_id": persona_id, "data": {"action": {"_tryfolk": {"kind": "pay"}},
                                                                                "result": {"data": {"success": True}}}},
              {"kind": "verification", "persona_id": persona_id, "data": outcome}]
    event_path = tmp_path / "events.jsonl"
    event_path.write_text("\n".join(json.dumps(e) for e in events))
    viewer = "https://app.neatlogs.com/traces/" + trace_id
    readback = {"run_id": run_id, "persona_id": persona_id, "trace_id": trace_id, "source": "official_neatlogs_trace_api",
                "delivery_flushed": True, "finalized_readback": "verified", "http_status": 200, "hosted_status": "success", "viewer_url": viewer}
    raw = {"_id": trace_id, "status": "success", "spans": [{"span_id": "unit-span", "node_name": "tryfolk.verification",
             "data": {"input": json.dumps({"run_id": run_id, "persona_id": persona_id, "provider": "dodo", "revision": manifest["target_revision"]}),
                      "output": json.dumps({k: outcome[k] for k in ("outcome", "measured", "payment_verified", "entitlement_verified", "capability_available")} |
                                           {"observed_payload_sha256": hashlib.sha256(json.dumps(outcome, default=str).encode()).hexdigest()})}}]}
    save(tmp_path / f"{run_id}-{trace_id}-readback.json", raw)
    save(tmp_path / f"{run_id}-{trace_id}-trace.json", {**readback, "payload_gates": [], "expected_spans": {"unit-span": "tryfolk.verification"}})
    hashes = {n: digest(tmp_path / n) for n in ADAPTER_FILES}
    commission = {"schema": "tryfolk.dodo-commissioning.v1", "run_id": run_id, "diagnostic": True,
                  "not_in_population_denominator": True, "execution_ready_declared": False, "status": "journeys_recorded",
                  "requested": 1, "measured": 1, "blockers": [], "adapter_files_before": hashes, "adapter_files_after": hashes,
                  "adapter_sources_unchanged": True, "installed_sdk_versions": gate.SDK_PINS,
                  "readiness": {"ready": True, "errors": [], "manifest": manifest},
                  "run": {"id": run_id, "provider": "dodo", "preview_url": origin, "revision": manifest["target_revision"], "source_digest": manifest["source_sha256"]},
                  "population_file": str(cohort), "population_sha256": semantic_digest(population), "events_file": str(event_path),
                  "events_sha256": digest(event_path), "trace_readbacks": [readback],
                  "journeys": [{"persona_id": persona_id, "initial_state_sha256": semantic_digest(population[0]), "trace_url": viewer, "outcome": outcome}]}
    return commission, tmp_path, manifest


def test_unit_completed_checkout_gate_rechecks_all_artifacts(diagnostic):
    commission, root, manifest = diagnostic
    source = save(root / "commission.json", commission)
    proof = root / "validation.json"
    result = gate.write_validation(source, output=proof, root=root)
    assert result["population_validation"] == "pending"
    assert result["completed_diagnostic_personas"] == 1
    assert proof.stat().st_mode & 0o777 == 0o600
    assert execution_ready(manifest, proof, root)
    Path(commission["journeys"][0]["outcome"]["capability_evidence"]["download_path"]).write_bytes(b"tampered")
    assert not execution_ready(manifest, proof, root)


@pytest.mark.parametrize("change", ["failed", "source_changed", "sdk_changed", "no_grant", "wrong_owner", "duplicate_pay", "hosted_incomplete", "hosted_other_outcome", "missing_file"])
def test_synthetic_incomplete_or_unbound_checkout_cannot_establish_readiness(diagnostic, change):
    commission, root, _ = diagnostic
    journey = commission["journeys"][0]
    if change == "failed":
        commission["status"] = "unresolved"
    elif change == "source_changed":
        (root / ADAPTER_FILES[0]).write_text("changed")
    elif change == "sdk_changed":
        commission["installed_sdk_versions"] = {**gate.SDK_PINS, "stagehand": "other"}
    elif change == "no_grant":
        journey["outcome"]["entitlement_verified"] = False
    elif change == "wrong_owner":
        journey["outcome"]["order"]["persona_id"] = "other"
    elif change == "duplicate_pay":
        path = Path(commission["events_file"])
        events = path.read_text().splitlines()
        path.write_text("\n".join([events[0], *events]))
        commission["events_sha256"] = digest(path)
    elif change.startswith("hosted_"):
        path = next(root.glob("*-readback.json"))
        raw = json.loads(path.read_text())
        if change == "hosted_incomplete":
            raw["spans"] = []
        else:
            raw["spans"][0]["data"]["output"] = json.dumps({"outcome": "completed"})
        save(path, raw)
    elif change == "missing_file":
        Path(journey["outcome"]["capability_evidence"]["download_path"]).unlink()
    with pytest.raises(ValueError):
        gate.assess_commissioning(commission, root)


def test_real_unresolved_commissioning_cannot_create_readiness():
    root = Path(__file__).resolve().parents[1]
    path = root / "runtime/evidence/9d2c517571524f3096dfc7e64d08ab0c-dodo-commissioning.json"
    if not path.exists():
        pytest.skip("Local real diagnostic artifact is private and not checked into Git")
    with pytest.raises(ValueError, match="no successful genuine diagnostic checkout"):
        gate.assess_commissioning(json.loads(path.read_text()), root)
