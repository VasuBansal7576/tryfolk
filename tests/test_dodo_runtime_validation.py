"""Private validation-manifest boundary fixtures, never live sponsor evidence."""

import hashlib
import json
import pytest
from tryfolk.dodo_runtime_validation import write_validation, execution_ready, ADAPTER_FILES
from tryfolk.browser_network import PUBLIC_SCRIPTS, FRAME_SCRIPTS
from tryfolk.browser_policy import DODO_SECURE_FRAME


def save(path, value):
    path.write_text(json.dumps(value))
    return path


def artifacts(tmp_path):
    for name in ADAPTER_FILES:
        adapter = tmp_path / name
        adapter.parent.mkdir(parents=True, exist_ok=True)
        adapter.write_text("# Unit-only file, not a substitute SDK")
    digest = "a" * 64
    capture = save(
        tmp_path / "capture.json",
        {
            "schema": "tryfolk.entire-source-preflight.v1",
            "model": "gpt-6.1-sol",
            "source_digest": digest,
            "checkpoint_id": "01M4MC3MHGWPSN7ETB68ABG2EJ",
            "captured_commit": "b" * 40,
            "codex_session_id": "unit-session",
            "source_unchanged": True,
            "protected_hooks_unchanged": True,
            "status_exit_code": 0,
        },
    )
    association = save(
        tmp_path / "association.json",
        {
            "checkpoint_id": "01M4MC3MHGWPSN7ETB68ABG2EJ",
            "registered_commit": "b" * 40,
            "session_id": "unit-session",
            "transcript_sha256": "c" * 64,
            "source_unchanged_by_trailer": True,
        },
    )
    fields = ["country", "address", "city", "state", "postal", "card_number", "card_expiry", "card_cvc"]
    preflight = save(
        tmp_path / "preflight.json",
        {
            "schema": "tryfolk.stagehand-hosted-preflight.v1",
            "status": "passed",
            "model": "gpt-6.1-sol",
            "kind": "actual_sdk_deterministic_form_preflight_not_persona_outcome",
            "stagehand": "4.2.0",
            "source_digest": digest,
            "payment_submitted": False,
            "persona_jobs": 0,
            "adapter_files": {
                name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in ADAPTER_FILES
            },
            "resource_guard_evidence": [
                {
                    "url": url,
                    "decision": "public_script",
                    "method": "GET",
                    "resource_type": "script",
                    "credential_headers_forwarded": False,
                    "redirects_followed": 0,
                    "bytes": 1,
                    "sha256": "e" * 64,
                }
                for url in PUBLIC_SCRIPTS
            ]
            + [
                {
                    "url": url,
                    "decision": "public_frame_script",
                    "method": "GET",
                    "resource_type": "script",
                    "child_frame": True,
                    "page_identity": "primary",
                    "credential_headers_forwarded": False,
                    "redirects_followed": 0,
                    "bytes": 1,
                    "sha256": "e" * 64,
                }
                for url in FRAME_SCRIPTS
            ]
            + [
                {
                    "url": DODO_SECURE_FRAME,
                    "decision": "public_frame",
                    "resource_type": "document",
                    "child_frame": True,
                    "credential_headers_forwarded": False,
                    "redirects_followed": 0,
                    "bytes": 1,
                },
                {
                    "decision": "verified_frame_context",
                    "context": {
                        "config_id": "f" * 64,
                        "approved": True,
                        "provider_environment": "test_mode",
                        "payment_correlated": True,
                        "parent_matches": True,
                        "api_base_verified": True,
                        "api_url": "https://api.custom.hs.dodopayments.com",
                        "browser_api_role": "observed_approved_shared_service_config",
                        "service_sandbox_only_attested": False,
                        "sandbox_key": True,
                        "secret_authenticated": False,
                        "payment_id": "unit-payment",
                    },
                },
            ]
            + [
                {
                    "url": "https://api.custom.hs.dodopayments.com" + path,
                    "decision": "widget_bootstrap_response",
                    "method": "GET",
                    "status": 200,
                    "child_frame": True,
                    "page_identity": "primary",
                    "config_bound": True,
                    "independent_identity_rechecked": True,
                    "config_id": "f" * 64,
                    "response_target_guarded": True,
                    "native_preflight_bound": True,
                }
                for path in ("/account/payment_methods", "/customers/payment_methods")
            ],
            "filled_fields": fields,
            "events": [
                {
                    "action": {
                        "_tryfolk": {
                            "field": f,
                            "frame_url": DODO_SECURE_FRAME,
                            "frame_config_id": "f" * 64,
                            "frame_generation": 123,
                        }
                    },
                    "result": {"field_completed": f, "data": {"success": True}},
                }
                for f in fields
            ],
            "pay_control_observed_not_activated": {
                "method": "click",
                "selector": "#unit-pay",
                "_tryfolk": {"kind": "pay", "url": "https://test.checkout.dodopayments.com/unit"},
            },
            "reset": {"revoked_accounts": 1, "revoked_orders": 1, "evidence_preserved": True},
        },
    )
    ownership = {
        "run_id": "unit-run",
        "persona_id": "unit-person",
        "account_id": "unit-account",
        "fixture_id": "unit-fixture",
        "target_revision": "unit-revision",
        "order_id": "unit-order",
        "expected_product_id": "unit-product",
    }
    order = ownership | {
        "checkout_session_id": "unit-checkout",
        "amount_minor": 100,
        "currency": "USD",
        "active": True,
    }
    receipt = {
        "source": "dodo_test_api",
        "state": "succeeded",
        "mismatches": [],
        "ownership": ownership,
        "checkout_session_id": "unit-checkout",
        "evidence_id": "unit-evidence",
        "checkout": {"id": "unit-checkout", "payment_id": "unit-payment", "payment_status": "succeeded"},
        "payment": {
            "payment_id": "unit-payment",
            "status": "succeeded",
            "checkout_session_id": "unit-checkout",
            "metadata": ownership | {"product_id": "unit-product"},
            "amount_minor": 100,
            "currency": "USD",
            "product_cart": [{"product_id": "unit-product", "quantity": 1}],
        },
    }
    entitlement = {
        "source": "app_local_entitlement",
        "has_access": True,
        "order_has_grant": True,
        "grant_count": 1,
        "active": True,
        "ownership": ownership,
        "payment_id": "unit-payment",
    }
    healthy = save(
        tmp_path / "healthy.json",
        {
            "provider_fixture_injected": False,
            "matched_population_member": False,
            "signed_webhook_claimed": False,
            "manifest": {"source_sha256": digest, "mode": "healthy"},
            "orders_after": {"body": {"orders": [order]}},
            "provider_receipt": {"http_status": 200, "body": receipt},
            "app_entitlement": {"http_status": 200, "body": entitlement},
        },
    )
    return preflight, capture, association, healthy


@pytest.mark.parametrize(
    "dependency",
    ["src/tryfolk/scenario_evidence.py", "src/tryfolk/scenarios.py", "src/tryfolk/verification.py"],
)
@pytest.mark.parametrize("change", ["omitted", "changed"])
def test_scenario_evidence_dependency_closure_cannot_reuse_stale_runtime_proofs(tmp_path, dependency, change):
    assert dependency in ADAPTER_FILES
    paths = artifacts(tmp_path)
    if change == "omitted":
        preflight = json.loads(paths[0].read_text())
        preflight["adapter_files"].pop(dependency)
        save(paths[0], preflight)
    else:
        (tmp_path / dependency).write_text("# Changed unit-only source dependency")
    with pytest.raises(ValueError):
        write_validation(*paths, output=tmp_path / "result.json", root=tmp_path)


def test_execution_gate_requires_intact_live_artifacts_and_current_source_adapter_identity(tmp_path):
    files = artifacts(tmp_path)
    out = tmp_path / "validation.json"
    manifest = {"source_sha256": "a" * 64, "upstream_commit": "ef7cbd980d8f1e57b036dc2af71ff05521b6175a"}
    assert not execution_ready(manifest, out, tmp_path)
    result = write_validation(*files, output=out, root=tmp_path)
    assert result["status"] == "passed"
    assert result["scope"] == "unpaid_browser_and_source_capture_preflight_with_independent_healthy_control"
    assert result["population_validation"] == "pending"
    assert execution_ready(manifest, out, tmp_path)
    assert not execution_ready(manifest | {"source_sha256": "d" * 64}, out, tmp_path)
    (tmp_path / "integrations/stagehand/adapter.py").write_text("# Changed unit adapter")
    assert not execution_ready(manifest, out, tmp_path)


@pytest.mark.parametrize("missing", ["response_target_guarded", "native_preflight_bound"])
def test_actual_bootstrap_response_requires_native_transport_and_child_response_boundary(tmp_path, missing):
    files = artifacts(tmp_path)
    preflight = json.loads(files[0].read_text())
    for event in preflight["resource_guard_evidence"]:
        if event["decision"] == "widget_bootstrap_response":
            event.pop(missing)
    save(files[0], preflight)
    with pytest.raises(ValueError):
        write_validation(*files, output=tmp_path / "validation.json", root=tmp_path)


def test_card_presence_without_actual_native_field_completions_cannot_clear_dispatch(tmp_path):
    files = artifacts(tmp_path)
    preflight = json.loads(files[0].read_text())
    preflight["events"] = [
        event for event in preflight["events"] if not event["action"]["_tryfolk"]["field"].startswith("card_")
    ]
    preflight["events"].append(
        {
            "observation": {
                "field_evidence": [
                    {"field": field, "approved_value_present": True}
                    for field in ("card_number", "card_expiry", "card_cvc")
                ]
            }
        }
    )
    save(files[0], preflight)
    with pytest.raises(ValueError, match="dispatch stays gated"):
        write_validation(*files, output=tmp_path / "validation.json", root=tmp_path)


@pytest.mark.parametrize(
    "file_index,change",
    [
        (0, {"status": "blocked"}),
        (0, {"resource_guard_evidence": []}),
        (
            0,
            {
                "resource_guard_evidence": [
                    {
                        "url": url,
                        "decision": "public_script",
                        "method": "GET",
                        "resource_type": "script",
                        "credential_headers_forwarded": False,
                        "redirects_followed": 0,
                        "bytes": 1,
                        "sha256": "e" * 64,
                    }
                    for url in PUBLIC_SCRIPTS
                ]
            },
        ),
        (0, {"payment_submitted": True}),
        (0, {"events": [{"action": {"_tryfolk": {"kind": "pay"}}}]}),
        (1, {"protected_hooks_unchanged": False}),
        (2, {"session_id": "different-session"}),
        (3, {"provider_fixture_injected": True}),
    ],
)
def test_blocked_paid_mocked_or_unbound_context_never_clears_dispatch(tmp_path, file_index, change):
    files = artifacts(tmp_path)
    value = json.loads(files[file_index].read_text())
    save(files[file_index], value | change)
    output = tmp_path / "validation.json"
    with pytest.raises(ValueError, match="dispatch stays gated"):
        write_validation(*files, output=output, root=tmp_path)
    assert not output.exists()


@pytest.mark.parametrize("mutation", ["missing", "failed", "unbound", "wrong_frame"])
def test_native_card_claims_without_independently_bound_successful_bootstrap_reads_stay_gated(
    tmp_path, mutation
):
    files = artifacts(tmp_path)
    preflight = json.loads(files[0].read_text())
    events = preflight["resource_guard_evidence"]
    if mutation == "missing":
        preflight["resource_guard_evidence"] = [
            e for e in events if e.get("decision") != "widget_bootstrap_response"
        ]
    else:
        event = next(e for e in events if e.get("decision") == "widget_bootstrap_response")
        event.update(
            {
                "failed": {"status": 401},
                "unbound": {"independent_identity_rechecked": False},
                "wrong_frame": {"config_id": "0" * 64},
            }[mutation]
        )
    save(files[0], preflight)
    with pytest.raises(ValueError, match="dispatch stays gated"):
        write_validation(*files, output=tmp_path / "validation.json", root=tmp_path)
