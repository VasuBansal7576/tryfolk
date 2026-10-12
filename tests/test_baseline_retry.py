"""Owned blocked-state and CFO fixtures; no provider/browser/model calls or live evidence."""

import json
import pytest
from dataclasses import replace

from fastapi.testclient import TestClient

from tryfolk.api import create_app
from tryfolk.scenarios import digest
from tryfolk.worker import Worker
from test_api import auth, client_at, quote, start
from test_cfo_budget import finalized_plan

OWNER = {"Authorization": "Bearer test-owner"}


def blocked_fixture(tmp_path):
    original = client_at(tmp_path / "runs.sqlite", budget=210_000)
    plan = finalized_plan(lifetime_budget_microusd=210_000)
    plan["native_ledger_snapshot"].update(
        estimated_consumed_microusd=40_000, held_microusd=80_000, available_microusd=90_000
    )
    plan_path = tmp_path / "cfo.json"
    plan_path.write_text(json.dumps(plan))
    original.app.state.service.settings = replace(
        original.app.state.service.settings, cfo_plan_path=plan_path
    )
    old = start(original, quote(original, 1)).json()
    population = [
        {
            "id": "saved-persona",
            "index": 0,
            "profile": {"goal": "Special Downloads"},
            "initial_snapshot": {"fixture": "initial TinyPerson"},
            "initial_semantic_memory": {"goal": "buy"},
            "conditions": {"history": "new"},
        }
    ]
    service = original.app.state.service
    # Explicit fixture of a retained pre-submission native failure, not manufactured Dodo evidence.
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", old["id"])
        run.update(
            provider="dodo",
            phase="blocked",
            target_mode="broken",
            source_commit="a" * 40,
            source_digest="b" * 64,
            gates=[{"code": "runtime", "message": "fixture pre-submission gate"}],
        )
        service.store.save(db, "runs", run)
        db.execute("UPDATE jobs SET status='blocked' WHERE run_id=?", (old["id"],))
        db.execute(
            "UPDATE populations SET payload=? WHERE id=?", (json.dumps(population), run["population_id"])
        )
        db.execute("UPDATE reservations SET spent=40000 WHERE id=?", (old["id"],))
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(
        json.dumps(
            {
                "schema": "tryfolk.baseline-retry-approval.v1",
                "run_id": old["id"],
                "population_id": run["population_id"],
                "population_sha256": digest(population),
                "source_commit": run["source_commit"],
                "source_digest": run["source_digest"],
                "events_sha256": digest([]),
                "historical_spent_microusd": 40_000,
                "release_microusd": 80_000,
                "pre_submission_confirmed": True,
                "evidence_sha256": "c" * 64,
            }
        )
    )
    approval_path.chmod(0o600)
    target = replace(
        service.settings.targets[0],
        provider="dodo",
        execution_ready=True,
        source_commit="a" * 40,
        source_digest="b" * 64,
        mode="broken",
        repo_path=str(tmp_path),
    )
    settings = replace(service.settings, targets=(target,), baseline_retry_approval_path=approval_path)
    client = TestClient(create_app(settings))
    return client, old, population, approval_path


def retry_quote(client, old):
    return client.post(f"/runs/{old['id']}/baseline-retry/quote", json={}, headers=OWNER)


def accept(client, old, q, key="retry"):
    return client.post(
        f"/runs/{old['id']}/baseline-retry",
        json={"quote_id": q["id"]},
        headers={**OWNER, "Idempotency-Key": key},
    )


def test_owner_retry_releases_only_original_hold_and_reserves_both_passes_atomically(tmp_path):
    client, old, population, _ = blocked_fixture(tmp_path)
    quoted = retry_quote(client, old)
    assert quoted.status_code == 200, quoted.text
    q = quoted.json()
    assert q["retry_budget"] == {
        "historical_spent_microusd": 40_000,
        "original_reserved_microusd": 120_000,
        "release_microusd": 80_000,
        "available_before_release_microusd": 90_000,
        "available_after_release_microusd": 170_000,
        "new_reserved_microusd": 120_000,
        "available_after_reserve_microusd": 50_000,
        "measured_cash_microusd": 0,
        "cost_basis": "estimate",
    }
    assert client.get(f"/runs/{old['id']}", headers=OWNER).json()["budget"]["released_microusd"] == 0
    accepted = accept(client, old, q)
    assert accepted.status_code == 201, accepted.text
    new = accepted.json()
    status = new["status"]
    assert status["id"] != old["id"] and status["pass"] == "before"
    assert status["parent_run_id"] is None and status["retry_of_run_id"] == old["id"]
    assert status["personas"] == population
    assert status["budget"]["reserved_microusd"] == 120_000
    assert status["budget"]["spent_microusd"] == 0
    prior = client.get(f"/runs/{old['id']}", headers=OWNER).json()
    assert prior["phase"] == "blocked" and prior["counts"]["executed"] == 0
    assert prior["budget"]["spent_microusd"] == 40_000
    assert prior["budget"]["released_microusd"] == 80_000
    assert prior["baseline_retry"]["retry_run_id"] == new["id"]
    assert quote(client, 1)["available_microusd"] == 50_000
    assert accept(client, old, q).json() == new
    assert accept(client, old, q, "competing").status_code == 409
    assert client.get(f"/runs/{new['id']}/findings", headers=auth(new)).json()["findings"] == []


@pytest.mark.asyncio
async def test_retry_worker_reuses_exact_initial_population_without_generation_or_known_replay(tmp_path):
    client, old, population, _ = blocked_fixture(tmp_path)
    new = accept(client, old, retry_quote(client, old).json()).json()

    class FreshJourney:
        def __init__(self):
            self.received = []

        async def discover(self, run):
            raise AssertionError("Retry must not rediscover/regenerate saved population")

        async def generate(self, brief, count):
            raise AssertionError("Retry must not generate")

        async def known_replay(self, run, personas, emit):
            raise AssertionError("A distinct before is not known-failure replay")

        async def journey(self, run, persona, cancelled, emit):
            self.received.append((run["id"], json.loads(json.dumps(persona))))
            persona["initial_snapshot"]["fixture"] = "fixture session mutated only"
            return {
                "outcome": "pending",
                "classification": "fixture_unsettled",
                "measured": False,
                "provider": "dodo",
                "payment_verified": False,
            }

    io = FreshJourney()
    await Worker(client.app.state.service, io).tick()
    status = client.get(f"/runs/{new['id']}", headers=auth(new)).json()
    assert status["phase"] == "completed"
    assert status["counts"]["requested"] == status["counts"]["executed"] == 1
    assert (
        status["personas"] == population == client.get(f"/runs/{old['id']}", headers=OWNER).json()["personas"]
    )
    assert io.received == [(new["id"], population[0])]
    assert status["budget"]["spent_microusd"] == 20_000
    assert status["budget"]["unused_microusd"] >= status["after_reserve_microusd"]
    assert not any(e["kind"] == "discovery" for e in status["events"])
    assert (
        client.post(
            f"/runs/{old['id']}/replay",
            json={"repair_id": "missing"},
            headers={**OWNER, "Idempotency-Key": "invalid-after"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("population", "population_integrity"),
        ("approval", "baseline_retry_approval"),
        ("event", "baseline_retry_approval"),
        ("outcome", "baseline_retry_ineligible"),
        ("job", "baseline_retry_ineligible"),
        ("pay", "baseline_retry_ineligible"),
        ("confirm", "baseline_retry_ineligible"),
        ("mode", "revision_changed"),
        ("setup", "setup_not_ready"),
        ("budget", "budget"),
        ("expiry", "quote_expired"),
        ("cfo", "baseline_retry_stale"),
    ],
)
def test_rejected_accept_never_releases_original_or_creates_new_attempt(tmp_path, mutation, code):
    client, old, _, approval = blocked_fixture(tmp_path)
    q = retry_quote(client, old).json()
    service = client.app.state.service
    if mutation == "approval":
        approval.unlink()
    elif mutation == "mode":
        service.settings = replace(
            service.settings, targets=(replace(service.settings.targets[0], mode="healthy"),)
        )
    elif mutation == "setup":
        service.settings = replace(service.settings, gates=({"code": "fixture_setup_missing"},))
    elif mutation == "budget":
        with service.store.transaction() as db:
            db.execute("INSERT INTO reservations(id,reserved) VALUES (?,?)", ("other-owned-hold", 100_000))
    elif mutation == "cfo":
        data = json.loads(service.settings.cfo_plan_path.read_text())
        data["snapshot_sha256"] = "d" * 64
        service.settings.cfo_plan_path.write_text(json.dumps(data))
    else:
        with service.store.transaction() as db:
            run = service.store.load(db, "runs", old["id"])
            if mutation == "population":
                pop = service.store.load(db, "populations", run["population_id"])
                pop[0]["profile"]["goal"] = "changed"
                db.execute(
                    "UPDATE populations SET payload=? WHERE id=?", (json.dumps(pop), run["population_id"])
                )
            elif mutation == "outcome":
                run["outcomes"] = {"saved-persona": {"outcome": "pending"}}
                service.store.save(db, "runs", run)
            elif mutation == "job":
                db.execute("UPDATE jobs SET status='running' WHERE run_id=?", (old["id"],))
            elif mutation == "expiry":
                q["expires_at"] = "2000-01-01T00:00:00+00:00"
                service.store.save(db, "quotes", q)
            else:
                data = (
                    {"action": {"_tryfolk": {"kind": mutation}}}
                    if mutation in ("pay", "confirm")
                    else {"fixture": "new"}
                )
                db.execute(
                    "INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
                    (
                        old["id"],
                        "browser_action" if mutation in ("pay", "confirm") else "observation",
                        "2026-10-12",
                        json.dumps(data),
                    ),
                )
    response = accept(client, old, q)
    assert response.status_code in (402, 409), response.text
    assert response.json()["detail"]["code"] == code
    status = client.get(f"/runs/{old['id']}", headers=OWNER).json()
    assert status["budget"]["released_microusd"] == 0
    assert "baseline_retry" not in status


def test_retry_owner_csrf_strict_bodies_and_regular_quote_bypass_are_denied(tmp_path):
    client, old, _, _ = blocked_fixture(tmp_path)
    url = f"/runs/{old['id']}/baseline-retry/quote"
    assert client.post(url, json={}, headers=auth(old)).status_code == 403
    assert client.post(url, json={"persona_count": 1}, headers=OWNER).status_code == 422
    q = retry_quote(client, old).json()
    assert start(client, q, "bypass").status_code == 409
    assert (
        client.post(
            f"/runs/{old['id']}/baseline-retry",
            json={"quote_id": q["id"], "release": True},
            headers={**OWNER, "Idempotency-Key": "extra"},
        ).status_code
        == 422
    )
    login = client.post(
        "/owner/session", json={"secret": "test-owner"}, headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert client.post(url, json={}).status_code == 403
    assert (
        client.post(
            url,
            json={},
            headers={"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": login.json()["csrf_token"]},
        ).status_code
        == 200
    )


def test_two_competing_keys_and_restart_can_only_release_and_reserve_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    client, old, _, _ = blocked_fixture(tmp_path)
    first, second = retry_quote(client, old).json(), retry_quote(client, old).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda item: accept(client, old, *item), [(first, "one"), (second, "two")]))
    assert sorted(r.status_code for r in responses) == [201, 409]
    index = next(i for i, r in enumerate(responses) if r.status_code == 201)
    restarted = TestClient(create_app(client.app.state.service.settings))
    assert (
        accept(restarted, old, (first, second)[index], ("one", "two")[index]).json()
        == responses[index].json()
    )
    assert (
        accept(restarted, old, (first, second)[1 - index], ("one", "two")[index]).json()["detail"]["code"]
        == "idempotency_conflict"
    )
    assert quote(restarted, 1)["available_microusd"] == 50_000


def test_unrelated_hold_is_retained_and_quote_cannot_release_it(tmp_path):
    client, old, _, _ = blocked_fixture(tmp_path)
    with client.app.state.service.store.transaction() as db:
        db.execute("INSERT INTO reservations(id,reserved) VALUES (?,?)", ("unrelated-fixture", 30_000))
    q = retry_quote(client, old).json()
    assert q["retry_budget"]["available_before_release_microusd"] == 60_000
    assert q["retry_budget"]["available_after_release_microusd"] == 140_000
    assert accept(client, old, q).status_code == 201
    ledger = quote(client, 1)
    assert ledger["available_microusd"] == 20_000
    assert ledger["ledger"] == {"estimated_consumed_microusd": 40_000, "held_microusd": 150_000}


@pytest.mark.asyncio
async def test_only_completed_new_before_finding_can_form_after_pair(tmp_path):
    from tryfolk.verification import Payment, Entitlement, verify

    client, old, population, _ = blocked_fixture(tmp_path)
    new = accept(client, old, retry_quote(client, old).json()).json()
    assert (
        client.post(
            f"/runs/{new['id']}/replay",
            json={"repair_id": "not-ready"},
            headers={**OWNER, "Idempotency-Key": "early-after"},
        ).status_code
        == 409
    )

    class PaymentFixture:
        """Invented independent provider/entitlement inputs exercise classification, not live Dodo proof."""

        def __init__(self):
            self.journeys = []
            self.known = []

        async def known_replay(self, run, personas, emit):
            self.known.append((run["id"], len(personas)))
            await emit("known_failure_replay", {"fixture": True, "excluded_from_customer_denominator": True})

        async def journey(self, run, persona, cancelled, emit):
            self.journeys.append((run["id"], persona["id"], persona["initial_snapshot"]))
            account = run["id"] + persona["id"]
            payment = Payment(
                provider="dodo",
                id="fixture-payment-" + run["id"],
                status="succeeded",
                account_id=account,
                checkout_id="fixture-checkout",
                product_id="fixture-download",
                amount=1200,
                currency="USD",
                evidence_url="https://example.org/unit-fixture",
            )
            entitlement = Entitlement(
                account_id=account,
                product_id="fixture-download",
                granted=run["pass"] == "after",
                payment_id=payment.id,
                units=int(run["pass"] == "after"),
            )
            return {
                **verify(
                    payment,
                    entitlement,
                    account_id=account,
                    checkout_id=payment.checkout_id,
                    product_id=payment.product_id,
                    amount=1200,
                    currency="USD",
                    settled=True,
                ),
                "external_evidence_is_fixture": True,
            }

    io = PaymentFixture()
    worker = Worker(client.app.state.service, io)
    await worker.tick()
    before = client.get(f"/runs/{new['id']}", headers=auth(new)).json()
    assert before["phase"] == "completed" and before["counts"]["failed"] == 1
    finding = client.get(f"/runs/{new['id']}/findings", headers=OWNER).json()["findings"][0]
    assert (
        client.post(
            f"/runs/{old['id']}/repairs", json={"finding_id": finding["id"]}, headers=OWNER
        ).status_code
        == 409
    )
    repair = client.post(
        f"/runs/{new['id']}/repairs", json={"finding_id": finding["id"]}, headers=OWNER
    ).json()
    client.app.state.service.register_repair(
        repair["id"],
        head_revision="e" * 40,
        preview_url="http://127.0.0.1:3310",
        checkpoint_id="fixture-only",
        checkpoint_url=None,
        provider="dodo",
        source_digest="f" * 64,
        target_revision="sha256:" + "f" * 64,
    )
    after = client.post(
        f"/runs/{new['id']}/replay",
        json={"repair_id": repair["id"]},
        headers={**OWNER, "Idempotency-Key": "new-pair-after"},
    ).json()
    await worker.tick()
    status = client.get(f"/runs/{after['id']}", headers=auth(after)).json()
    assert status["phase"] == "completed"
    assert status["comparison"]["before_run_id"] == new["id"]
    assert status["comparison"]["after_run_id"] == after["id"]
    assert status["comparison"]["requested"] == 1 and status["counts"]["executed"] == 1
    assert status["personas"] == population
    assert io.known == [(after["id"], 1)]
    assert io.journeys == [
        (new["id"], "saved-persona", population[0]["initial_snapshot"]),
        (after["id"], "saved-persona", population[0]["initial_snapshot"]),
    ]
    prior = client.get(f"/runs/{old['id']}", headers=OWNER).json()
    assert prior["phase"] == "blocked" and prior["budget"]["spent_microusd"] == 40_000


@pytest.mark.asyncio
async def test_new_typed_presubmit_failure_gets_server_receipt_and_needs_no_manual_approval_file(tmp_path):
    from tryfolk.pre_submission import PreSubmissionGate

    client, old, _, approval = blocked_fixture(tmp_path)
    service = client.app.state.service
    # External SDK/manifest observations below are fixtures, never genuine Dodo evidence.
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", old["id"])
        run["phase"] = "queued"
        service.store.save(db, "runs", run)
        db.execute("UPDATE reservations SET spent=0 WHERE id=?", (old["id"],))
        db.execute("UPDATE jobs SET status='queued' WHERE run_id=?", (old["id"],))

    class TypedFailureIO:
        async def discover(self, run):
            return {"external_fixture": True}

        async def generate(self, brief, count):
            return [
                {
                    "id": "saved-persona",
                    "index": 0,
                    "profile": {"goal": "Special Downloads"},
                    "initial_snapshot": {"fixture": "initial TinyPerson"},
                    "initial_semantic_memory": {"goal": "buy"},
                    "conditions": {"history": "new"},
                }
            ]

        async def journey(self, run, persona, cancelled, emit):
            raise PreSubmissionGate(
                "fixture manual_document_unstable",
                {
                    "stage": "manual_address",
                    "reason": "manual_document_unstable",
                    "financial_submission_started": False,
                    "financial_poll_started": False,
                    "provider_environment": "test_mode",
                    "run_id": run["id"],
                    "persona_id": "saved-persona",
                    "target_revision": run["revision"],
                    "target_mode": "broken",
                    "source_commit": "a" * 40,
                    "source_digest": "b" * 64,
                    "evidence_sha256": "c" * 64,
                },
            )

    await Worker(service, TypedFailureIO()).tick()
    approval.unlink()
    service.settings = replace(service.settings, baseline_retry_approval_path=None)
    q = retry_quote(client, old)
    assert q.status_code == 200, q.text
    accepted = accept(client, old, q.json())
    assert accepted.status_code == 201, accepted.text
    assert (
        accepted.json()["status"]["baseline_retry"]["payment_safety_basis"]
        == "server_recorded_pre_submission_evidence_not_provider_receipt"
    )


def test_retry_uses_frozen_scenario_seed_and_initial_memory_without_reloading_catalog(tmp_path):
    from test_scenario_api import configured, CONDITIONS
    from tryfolk.scenarios import freeze_population

    client, old, _, approval = blocked_fixture(tmp_path)
    service = client.app.state.service
    scenario_settings, catalog, bank = configured(tmp_path)
    target = replace(
        service.settings.targets[0],
        scenario_capabilities=scenario_settings.targets[0].scenario_capabilities,
        scenario_conditions=CONDITIONS,
    )
    service.settings = replace(
        service.settings,
        targets=(target,),
        scenario_catalog_path=catalog,
        seed_bank_path=bank,
        seed_bank_min_records=4,
    )
    plan = quote(client, 1)["scenario_plan"]
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", old["id"])
        population = service.store.load(db, "populations", run["population_id"])
        population[0]["conditions"] = CONDITIONS
        population = freeze_population(population, plan)
        db.execute(
            "UPDATE populations SET payload=? WHERE id=?", (json.dumps(population), run["population_id"])
        )
        run["scenario_plan"] = plan
        service.store.save(db, "runs", run)
    approved = json.loads(approval.read_text())
    approved["population_sha256"] = digest(population)
    approval.write_text(json.dumps(approved))
    catalog.unlink()
    bank.unlink()
    q = retry_quote(client, old)
    assert q.status_code == 200, q.text
    assert q.json()["scenario_plan"] == plan
    response = accept(client, old, q.json())
    assert response.status_code == 201, response.text
    assert response.json()["status"]["personas"] == population
    assert response.json()["status"]["scenario_plan"] == plan


@pytest.mark.asyncio
async def test_population_tampering_after_acceptance_blocks_before_any_new_journey_debit(tmp_path):
    client, old, _, _ = blocked_fixture(tmp_path)
    new = accept(client, old, retry_quote(client, old).json()).json()
    service = client.app.state.service
    with service.store.transaction() as db:
        status = service.store.load(db, "runs", new["id"])
        population = service.store.load(db, "populations", status["population_id"])
        population[0]["initial_semantic_memory"]["goal"] = "tampered"
        db.execute(
            "UPDATE populations SET payload=? WHERE id=?", (json.dumps(population), status["population_id"])
        )
    await Worker(service, object()).tick()
    status = client.get(f"/runs/{new['id']}", headers=auth(new)).json()
    assert status["phase"] == "blocked" and status["budget"]["spent_microusd"] == 0
    assert status["counts"]["executed"] == 0
