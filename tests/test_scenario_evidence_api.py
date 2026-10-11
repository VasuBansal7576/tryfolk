"""Read actual response classification through API using disclosed external-I/O fixtures.

All receipt/browser/file records here are invented local test inputs. Passing tests
validate evidence admission logic, never count as live measured product coverage.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from tryfolk.api import create_app
from tryfolk.dodo import correlate
from tryfolk.scenarios import freeze_population
from test_api import auth, start
from test_scenario_api import configured

FIXTURE = "dodo-public-success-card-us-desktop-v1"
CONDITIONS = {
    "fixture_id": FIXTURE,
    "account_history": "new",
    "device": "desktop Chromium",
    "locale": "en-US",
    "billing_country": "US",
}
CASES = ["one-time-card-download", "paid-no-access-download-retest"]


def prepared(tmp_path, monkeypatch, *, case=CASES[0], granted=True):
    settings, _, bank = configured(tmp_path)
    root = Path(__file__).resolve().parents[1]
    catalog_path = root / "dodo-target/scenarios/payment-catalog.json"
    catalog = json.loads(catalog_path.read_text())
    runnable = [s for s in catalog["scenarios"] if s["id"] in CASES]
    seeds = json.loads(bank.read_text())
    for seed in seeds["seeds"]:
        seed["supported_context"] = {k: [v] for k, v in CONDITIONS.items()}
    bank.write_text(json.dumps(seeds))
    target = replace(
        settings.targets[0],
        url="http://127.0.0.1:3301",
        provider="dodo",
        revision="sha256:" + "a" * 64,
        source_commit="b" * 40,
        source_digest="c" * 64,
        mode="healthy",
        scenario_conditions=CONDITIONS,
        scenario_fixture_ids=(FIXTURE,),
        scenario_capabilities={
            k: sorted({cap for s in runnable for cap in s["required_capabilities"][k]})
            for k in ("target", "provider")
        },
    )
    settings = replace(settings, targets=(target,), scenario_catalog_path=catalog_path)
    app = create_app(settings)
    client = TestClient(app)
    q = client.post(
        "/quotes", json={"app_url": target.url, "persona_count": 1, "scenario_ids": [case]}
    ).json()
    accepted = start(client, q).json()
    service = app.state.service
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", accepted["id"])
        population = freeze_population(
            [
                {
                    "id": "unit-person",
                    "index": 0,
                    "name": "Unit fixture adult",
                    "profile": {"background": "Adult illustrator"},
                    "conditions": CONDITIONS,
                    "initial_snapshot": {"memory": "unit snapshot"},
                    "initial_semantic_memory": [{"content": "unit purchase goal"}],
                }
            ],
            q["scenario_plan"],
        )
        db.execute(
            "UPDATE populations SET payload=? WHERE id=?", (json.dumps(population), run["population_id"])
        )
    person = population[0]
    assignment = person["assigned_scenario"]
    ownership = {
        "run_id": run["id"],
        "persona_id": person["id"],
        "account_id": "unit-account",
        "fixture_id": "unit-account-fixture",
        "target_revision": run["revision"],
    }
    order = ownership | {
        "order_id": "unit-order",
        "expected_product_id": "unit-product",
        "checkout_session_id": "unit-checkout",
        "amount_minor": 1200,
        "currency": "USD",
        "active": True,
    }
    receipt_owner = ownership | {k: order[k] for k in ("order_id", "expected_product_id")}
    receipt = {
        "source": "dodo_test_api",
        "state": "succeeded",
        "ownership": receipt_owner,
        "checkout_session_id": "unit-checkout",
        "evidence_id": "unit-receipt",
        "checkout": {"id": "unit-checkout", "payment_id": "unit-payment", "payment_status": "succeeded"},
        "payment": {
            "payment_id": "unit-payment",
            "checkout_session_id": "unit-checkout",
            "status": "succeeded",
            "metadata": receipt_owner | {"product_id": "unit-product"},
            "product_cart": [{"product_id": "unit-product", "quantity": 1}],
            "amount_minor": 1200,
            "currency": "USD",
        },
    }
    entitlement = {
        "source": "app_local_entitlement",
        "ownership": receipt_owner,
        "has_access": granted,
        "order_has_grant": granted,
        "grant_count": int(granted),
        "active": True,
        "payment_id": "unit-payment",
    }
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(evidence_root))
    file = evidence_root / "unit-owned.svg"
    file.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    capability = {
        "source": "stagehand_browser_download",
        "available": True,
        "download_path": str(file),
        "download_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        "bytes": file.stat().st_size,
        "download_admission": {
            "decision": "owned_download_event",
            "first_failed_predicate": None,
            "admission_phase": "actual_primary_download_event_before_save",
            "owned_navigation_checks": {
                k: True
                for k in (
                    "explicit_download_preparation_available",
                    "same_primary_download_page",
                    "single_exact_owned_protected_download",
                    "prepared_download_identity_fresh",
                    "download_source_document_continuously_current",
                )
            },
        },
    }
    download_binding = ownership | {
        "order_id": order["order_id"],
        "checkout_session_id": order["checkout_session_id"],
        "payment_id": receipt["payment"]["payment_id"],
        "expected_product_id": order["expected_product_id"],
        "artwork_id": 2,
        "download_url": target.url + "/api/target/download?artwork=2",
    }
    capability["download_binding"] = deepcopy(download_binding)
    capability["download_admission"]["download_binding"] = deepcopy(download_binding)
    result = correlate(order, receipt, entitlement, capability=True if granted else None)
    result.update(order=order, account_id=ownership["account_id"], revision=run["revision"])
    result["oracle_http"] = {
        key: {
            "http_status": 200,
            "body": deepcopy(body),
            "method": "GET",
            "url": target.url + path + "?" + urlencode(params),
            "redirects_followed": 0,
        }
        for key, path, params, body in (
            (
                "orders_after",
                "/api/target/orders",
                {"account_id": ownership["account_id"]},
                {"orders": [order]},
            ),
            ("provider_receipt", "/api/target/receipts", {"order_id": order["order_id"]}, receipt),
            (
                "app_entitlement",
                "/api/target/entitlements",
                {"account_id": ownership["account_id"], "order_id": order["order_id"]},
                entitlement,
            ),
        )
    }
    if granted:
        result["capability_evidence"] = capability
    binding = (
        ownership
        | {"condition_fixture_id": FIXTURE}
        | {
            k: assignment[k]
            for k in (
                "scenario_id",
                "scenario_sha256",
                "saved_persona_sha256",
                "profile_sha256",
                "initial_memory_sha256",
                "condition_fixture_sha256",
            )
        }
    )
    manifest = {
        "target_revision": run["revision"],
        "source_commit": run["source_commit"],
        "source_sha256": run["source_digest"],
        "app_origin": run["preview_url"],
        "provider_environment": "test_mode",
        "source_clean": True,
        "mode": run["target_mode"],
    }
    fixture_event = {
        "binding": binding,
        "source_manifest": manifest,
        "account": {"source": "dodo_owned_fixture_provision", "ownership": ownership, "history": "new"},
        "browser": {
            "source": "stagehand_owned_context",
            "fresh_context": True,
            "locale": "en-US",
            "user_agent": "Unit fixture Chrome/1 desktop",
            "has_touch": False,
            "viewport": {"width": 1280, "height": 800},
        },
    }
    condition_event = {
        "binding": binding,
        "observation": {
            "source": "stagehand_validated_test_checkout",
            "fixture_id": FIXTURE,
            "completed_fields": ["country", "card_number", "card_month", "card_year", "card_cvc"],
            "billing_country": "US",
            "accepted_native_confirm": True,
            "checkout_session_id": "unit-checkout",
            "payment_id": "unit-payment",
        },
    }
    events = [("scenario_fixture", fixture_event), ("scenario_payment_condition", condition_event)]
    if granted:
        events.append(("capability", capability))
    return client, service, accepted, run, result, events, file


def install(service, run, result, events, *, finding=False):
    with service.store.transaction() as db:
        run = deepcopy(run)
        run.update(phase="completed", outcomes={"unit-person": result})
        service.store.save(db, "runs", run)
        for kind, data in [*events, ("verification", result)]:
            db.execute(
                "INSERT INTO events(run_id,persona_id,kind,created_at,data) VALUES (?,?,?,?,?)",
                (run["id"], "unit-person", kind, "unit-time", json.dumps(data)),
            )
        if finding:
            f = {
                "id": "unit-finding",
                "run_id": run["id"],
                "persona_id": "unit-person",
                "classification": result["classification"],
                "evidence": result,
                "measured": True,
            }
            db.execute("INSERT INTO findings VALUES (?,?,?)", (f["id"], run["id"], json.dumps(f)))


def observed(client, accepted):
    response = client.get("/runs/" + accepted["id"], headers=auth(accepted))
    assert response.status_code == 200, response.text
    coverage = response.json()["scenario_coverage"]
    return coverage, coverage["cases"][0]["observed_scenario_result"]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("granted", [True, False])
def test_supported_case_binds_saved_assignment_actual_conditions_receipt_grant_and_file(
    tmp_path, monkeypatch, case, granted
):
    client, service, accepted, run, result, events, _ = prepared(
        tmp_path, monkeypatch, case=case, granted=granted
    )
    install(service, run, result, events, finding=not granted)
    coverage, actual = observed(client, accepted)
    assert actual["scenario_id"] == case
    assert actual["provider"] == "dodo"
    assert actual["classification"] == ("fulfilled" if granted else "app_defect")
    assert actual["condition_fixture_verified"] is True
    assert actual["finding_ids"] == ([] if granted else ["unit-finding"])
    assert actual["payment_verified"] and actual["entitlement_verified"] is granted
    assert actual["capability_checked"] is granted  # No absent-file inference in access defect.
    assert coverage["exercised_case_ids"] == [case]
    assert len(actual["evidence_event_ids"]) == len(events) + 1


@pytest.mark.parametrize(
    "missing", ["scenario_fixture", "scenario_payment_condition", "capability", "verification"]
)
def test_missing_required_observation_never_becomes_exercised_from_assignment_or_paid_truth(
    tmp_path, monkeypatch, missing
):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    if missing == "verification":
        install(service, run, result, events)
        with service.store.transaction() as db:
            db.execute("DELETE FROM events WHERE kind='verification'")
    else:
        install(service, run, result, [e for e in events if e[0] != missing])
    coverage, actual = observed(client, accepted)
    assert coverage["exercised_case_ids"] == []
    assert actual["classification"] in ("not_exercised", "blocked_inconclusive")
    assert actual["exercise_gate"]


@pytest.mark.parametrize(
    "tamper",
    [
        "account",
        "revision",
        "source",
        "condition",
        "persona_hash",
        "receipt_source",
        "payment_metadata",
        "entitlement_owner",
        "fixture_country",
        "file",
        "download_guard",
    ],
)
def test_foreign_or_changed_evidence_cannot_verify_case_conditions(tmp_path, monkeypatch, tamper):
    client, service, accepted, run, result, events, file = prepared(tmp_path, monkeypatch)
    if tamper == "account":
        events[0][1]["binding"]["account_id"] = "foreign"
    if tamper == "revision":
        events[0][1]["binding"]["target_revision"] = "foreign"
    if tamper == "source":
        events[0][1]["source_manifest"]["source_sha256"] = "f" * 64
    if tamper == "condition":
        events[0][1]["browser"]["locale"] = "fr-FR"
    if tamper == "persona_hash":
        events[0][1]["binding"]["initial_memory_sha256"] = "changed"
    if tamper == "receipt_source":
        result["receipt"]["source"] = "external_provider_fixture"
    if tamper == "payment_metadata":
        result["receipt"]["payment"]["metadata"]["persona_id"] = "foreign"
    if tamper == "entitlement_owner":
        result["app_entitlement"]["ownership"]["account_id"] = "foreign"
    if tamper == "fixture_country":
        events[1][1]["observation"]["billing_country"] = "GB"
    if tamper == "file":
        file.write_bytes(b"changed")
    if tamper == "download_guard":
        result["capability_evidence"]["download_admission"]["owned_navigation_checks"][
            "same_primary_download_page"
        ] = False
    install(service, run, result, events)
    coverage, actual = observed(client, accepted)
    assert coverage["exercised_case_ids"] == []
    assert actual["classification"] in ("not_exercised", "blocked_inconclusive")
    assert actual["exercise_gate"]


def test_expected_provider_decline_is_never_an_app_defect_even_for_success_fixture(tmp_path, monkeypatch):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch, granted=False)
    receipt = result["receipt"]
    receipt["state"] = "failed"
    receipt["payment"]["status"] = receipt["checkout"]["payment_status"] = "failed"
    updated = correlate(result["order"], receipt, result["app_entitlement"])
    updated.update(order=result["order"], account_id=result["account_id"], revision=run["revision"])
    updated["oracle_http"] = result["oracle_http"]
    updated["oracle_http"]["provider_receipt"]["body"] = deepcopy(receipt)
    install(service, run, updated, events)
    coverage, actual = observed(client, accepted)
    assert actual["classification"] == "expected_provider_decline"
    assert actual["finding_ids"] == []
    assert coverage["exercised_case_ids"] == []  # Success-card scenario prerequisites not fulfilled.


def test_a_fixture_result_cannot_be_promoted_to_measured_scenario_coverage(tmp_path, monkeypatch):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    result["measured"] = False
    install(service, run, result, events)
    coverage, actual = observed(client, accepted)
    assert actual["classification"] == "not_exercised" and actual["measured"] is False
    assert actual["condition_fixture_verified"] is False
    assert coverage["exercised_case_ids"] == []


@pytest.mark.parametrize("expiry", [["card_expiry"], ["card_month", "card_year"]])
def test_existing_policy_verified_expiry_forms_admit_the_same_case(tmp_path, monkeypatch, expiry):
    from types import SimpleNamespace
    from tryfolk.scenario_evidence import checkout_observation

    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    fields = {"country", "card_number", "card_cvc", *expiry}
    guard = SimpleNamespace(
        card_attempt=SimpleNamespace(
            confirmed=True, checkout_session_id="unit-checkout", context={"payment_id": "unit-payment"}
        )
    )
    observed_fields = checkout_observation(guard, fields)
    assert observed_fields is not None
    assert set(observed_fields["completed_fields"]) == fields
    events[1][1]["observation"] = observed_fields
    install(service, run, result, events)
    assert observed(client, accepted)[1]["classification"] == "fulfilled"


@pytest.mark.parametrize("expiry", [[], ["card_month"], ["card_year"]])
def test_incomplete_expiry_never_establishes_actual_card_fixture(tmp_path, monkeypatch, expiry):
    from types import SimpleNamespace
    from tryfolk.scenario_evidence import checkout_observation

    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    fields = {"country", "card_number", "card_cvc", *expiry}
    guard = SimpleNamespace(
        card_attempt=SimpleNamespace(
            confirmed=True, checkout_session_id="unit-checkout", context={"payment_id": "unit-payment"}
        )
    )
    assert checkout_observation(guard, fields) is None
    events[1][1]["observation"]["completed_fields"] = sorted(fields)
    install(service, run, result, events)
    assert observed(client, accepted)[0]["exercised_case_ids"] == []


def test_observed_alternative_desktop_viewport_is_not_invented_as_unsupported(tmp_path, monkeypatch):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    events[0][1]["browser"]["viewport"] = {"width": 1600, "height": 900}
    install(service, run, result, events)
    assert observed(client, accepted)[1]["classification"] == "fulfilled"


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "persona_id",
        "account_id",
        "fixture_id",
        "target_revision",
        "order_id",
        "checkout_session_id",
        "payment_id",
        "expected_product_id",
        "download_url",
        "artwork_id",
    ],
)
def test_protected_file_for_a_foreign_account_cannot_complete_this_saved_customer_case(
    tmp_path, monkeypatch, field
):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    capability = result["capability_evidence"]
    capability["download_binding"][field] = "foreign"
    capability["download_admission"]["download_binding"][field] = "foreign"
    install(service, run, result, events)
    coverage, actual = observed(client, accepted)
    assert coverage["exercised_case_ids"] == []
    assert actual["classification"] == "blocked_inconclusive"
    assert actual["capability_checked"] is False


@pytest.mark.parametrize("missing", ["download_binding", "admission_binding"])
def test_unbound_historical_file_proof_cannot_be_relabelled_as_current_case(tmp_path, monkeypatch, missing):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    capability = result["capability_evidence"]
    if missing == "download_binding":
        capability.pop("download_binding")
    else:
        capability["download_admission"].pop("download_binding")
    install(service, run, result, events)
    assert observed(client, accepted)[0]["exercised_case_ids"] == []


def test_paid_truth_without_observed_oracle_http_responses_does_not_exercise_the_case(tmp_path, monkeypatch):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch, granted=False)
    result.pop("oracle_http")
    install(service, run, result, events)
    coverage, actual = observed(client, accepted)
    assert coverage["exercised_case_ids"] == []
    assert actual["classification"] == "blocked_inconclusive"
    assert actual["payment_verified"] is False


@pytest.mark.parametrize("field", ["orders_after", "provider_receipt", "app_entitlement"])
@pytest.mark.parametrize(
    "tamper",
    ["missing", "HTTP_status", "method", "redirect", "foreign_origin", "foreign_body", "malformed_body"],
)
def test_missing_or_foreign_http_response_never_supplies_independent_case_proof(
    tmp_path, monkeypatch, field, tamper
):
    client, service, accepted, run, result, events, _ = prepared(tmp_path, monkeypatch)
    response = result["oracle_http"][field]
    if tamper == "missing":
        result["oracle_http"].pop(field)
    elif tamper == "HTTP_status":
        response["http_status"] = 401
    elif tamper == "method":
        response["method"] = "POST"
    elif tamper == "redirect":
        response["redirects_followed"] = 1
    elif tamper == "foreign_origin":
        response["url"] = response["url"].replace("3301", "3338")
    elif tamper == "foreign_body":
        if field == "orders_after":
            response["body"]["orders"][0]["account_id"] = "foreign-account"
        else:
            response["body"]["ownership"]["account_id"] = "foreign-account"
    else:
        response["body"] = []
    install(service, run, result, events)
    coverage, actual = observed(client, accepted)
    assert coverage["exercised_case_ids"] == []
    assert actual["classification"] == "blocked_inconclusive"
    assert actual["payment_verified"] is False
