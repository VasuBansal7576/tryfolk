"""Forecast/queue fixtures; these tests never claim real provider execution."""
from dataclasses import replace
import json

import pytest

from test_cfo_budget import finalized_plan
from test_dodo_after_recovery import blocked_after, OWNER
from tryfolk.budget import allowance
from tryfolk.worker import Worker
import tryfolk.after_retry as retry


def prepared(tmp_path, monkeypatch):
    client, old = blocked_after(tmp_path)
    service = client.app.state.service
    plan = tmp_path / "cfo.json"
    plan.write_text(json.dumps(finalized_plan()))
    service.settings = replace(service.settings, cfo_plan_path=plan,
                               targets=tuple(replace(t, provider="dodo", execution_ready=True, mode="broken")
                                             for t in service.settings.targets))
    costs = allowance(service.settings, 4)
    proof = {"source_run_id": old["id"], "trace_id": "d" * 32, "events": [],
             "target_revision": old["revision"], "source_digest": old["source_digest"],
             "head_revision": old["source_commit"], "held_out_cases": [
                 {"case": "unpaid_access_denied", "passed": True},
                 {"case": "duplicate_reconciliation_grants_once", "passed": True}],
             "completed_diagnostic_recorded_at": "2026-10-12T21:00:00+00:00",
             "held_out_recorded_at": "2026-10-12T21:00:01+00:00"}
    monkeypatch.setattr(retry, "diagnostic_proof", lambda db, run: proof)
    with service.store.transaction() as db:
        before = service.store.load(db, "runs", old["parent_run_id"])
        before["budget_contract"].update(cfo_plan=costs["plan"], execution_allowances=costs["caps"],
                                          contingency_basis_points=2000)
        service.store.save(db, "runs", before)
        population = service.store.load(db, "populations", old["population_id"])
        for p in population:
            p.update(initial_snapshot={"fixture": p["id"]}, initial_semantic_memory=[])
        db.execute("UPDATE populations SET payload=? WHERE id=?", (json.dumps(population), old["population_id"]))
        db.execute("UPDATE reservations SET spent=230000 WHERE id=?", (old["reservation_id"],))
    return client, old


def test_owner_reviewed_after_only_quote_and_accept_preserve_history_and_hold(tmp_path, monkeypatch):
    client, old = prepared(tmp_path, monkeypatch)
    path = f"/runs/{old['id']}/after-retry"
    assert client.post(path + "/quote", json={}, headers={"Authorization": "Bearer after-access"}).status_code == 403
    q = client.post(path + "/quote", json={}, headers=OWNER).json()
    assert q["kind"] == "after_retry" and q["before_microusd"] == q["overhead_microusd"] == 0
    assert q["after_microusd"] == 80000 and q["reserved_microusd"] == 96000
    assert q["retry_budget"]["historical_spent_microusd"] == 230000
    assert q["retry_budget"]["release_microusd"] == 34000
    assert q["known_replay_reused"]["source_run_id"] == old["id"]
    assert client.post("/runs", json={"quote_id": q["id"]}, headers={"Idempotency-Key": "wrong-path"}).status_code == 409
    response = client.post(path, json={"quote_id": q["id"]}, headers={**OWNER, "Idempotency-Key": "approved"})
    assert response.status_code == 201, response.text
    fresh = response.json()
    assert fresh["id"] != old["id"]
    assert fresh["status"]["parent_run_id"] == old["parent_run_id"]
    assert fresh["status"]["population_id"] == old["population_id"]
    assert fresh["status"]["after_retry_of_run_id"] == old["id"]
    assert fresh["status"]["target_mode"] == old.get("target_mode")
    assert client.post(path, json={"quote_id": q["id"]}, headers={**OWNER, "Idempotency-Key": "approved"}).json() == fresh
    service = client.app.state.service
    with service.store.transaction() as db:
        retained = service.store.load(db, "runs", old["id"])
        assert retained["phase"] == "blocked" and retained["gates"] == old["gates"]
        assert dict(db.execute("SELECT * FROM reservations WHERE id=?", (old["reservation_id"],)).fetchone())["released"] == 34000
        assert dict(db.execute("SELECT * FROM reservations WHERE id=?", (fresh["id"],)).fetchone())["reserved"] == 96000


@pytest.mark.parametrize("phase,recorded", [("completed", 4), ("blocked", 2)])
def test_completed_after_can_repeat_with_fresh_hold_without_relabeling_prior_outcomes(tmp_path, monkeypatch, phase, recorded):
    client, old = prepared(tmp_path, monkeypatch)
    service = client.app.state.service
    with service.store.transaction() as db:
        previous = service.store.load(db, "runs", old["id"])
        people = service.store.load(db, "populations", old["population_id"])
        previous.update(phase=phase, gates=[], outcomes={p["id"]: {
            "outcome": "completed", "classification": "payment_and_entitlement",
            "provider": "dodo", "measured": True, "payment_verified": True,
            "entitlement_verified": True, "capability_checked": True, "capability_available": True,
        } for p in people[:recorded]})
        service.store.save(db, "runs", previous)
        if phase == "completed":
            db.execute("UPDATE reservations SET released=reserved-spent WHERE id=?", (old["reservation_id"],))
    path = f"/runs/{old['id']}/after-retry"
    quoted = client.post(path + "/quote", json={}, headers=OWNER)
    assert quoted.status_code == 200, quoted.text
    q = quoted.json()
    assert q["retry_budget"]["release_microusd"] == (0 if phase == "completed" else 34000)
    response = client.post(path, json={"quote_id": q["id"]}, headers={**OWNER, "Idempotency-Key": "repeat-completed"})
    assert response.status_code == 201, response.text
    fresh = response.json()
    assert fresh["id"] != old["id"] and fresh["status"]["counts"]["executed"] == 0
    with service.store.transaction() as db:
        retained = service.store.load(db, "runs", old["id"])
        assert retained["phase"] == phase and retained["outcomes"] == previous["outcomes"]
        assert service.store.load(db, "runs", fresh["id"])["outcomes"] == {}
        assert db.execute("SELECT spent,released FROM reservations WHERE id=?", (old["reservation_id"],)).fetchone()[:] == (230000, 34000)


@pytest.mark.parametrize("field", ["measured", "payment_verified", "entitlement_verified", "capability_checked", "capability_available"])
def test_completed_repeat_rejects_one_unverified_customer_without_queue_or_ledger_mutation(tmp_path, monkeypatch, field):
    client, old = prepared(tmp_path, monkeypatch)
    service = client.app.state.service
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", old["id"])
        people = service.store.load(db, "populations", old["population_id"])
        run.update(phase="completed", outcomes={p["id"]: {
            "outcome": "completed", "classification": "payment_and_entitlement", "provider": "dodo",
            "measured": True, "payment_verified": True, "entitlement_verified": True,
            "capability_checked": True, "capability_available": True,
        } for p in people})
        run["outcomes"][people[-1]["id"]][field] = False
        service.store.save(db, "runs", run)
        before = db.execute("SELECT sum(reserved),sum(spent),sum(released) FROM reservations").fetchone()[:]
    response = client.post(f"/runs/{old['id']}/after-retry/quote", json={}, headers=OWNER)
    assert response.status_code == 409 and "every original customer" in response.text
    with service.store.transaction() as db:
        assert db.execute("SELECT sum(reserved),sum(spent),sum(released) FROM reservations").fetchone()[:] == before
        assert not db.execute("SELECT 1 FROM jobs WHERE status='queued'").fetchone()


@pytest.mark.asyncio
async def test_worker_reuses_bound_diagnostic_without_new_known_checkout_or_debit(tmp_path, monkeypatch):
    client, old = prepared(tmp_path, monkeypatch)
    path = f"/runs/{old['id']}/after-retry"
    q = client.post(path + "/quote", json={}, headers=OWNER).json()
    fresh = client.post(path, json={"quote_id": q["id"]}, headers={**OWNER, "Idempotency-Key": "approved"}).json()

    class FixtureIO:
        async def known_replay(self, *args):
            raise AssertionError("No new diagnostic checkout is permitted")
        async def journey(self, run, persona, cancelled, emit):
            return {"outcome": "completed", "classification": "fixture", "measured": False,
                    "account_id": run["id"] + persona["id"]}
    await Worker(client.app.state.service, FixtureIO()).tick()
    result = client.get(f"/runs/{fresh['id']}", headers=OWNER).json()
    assert result["counts"]["executed"] == 4
    assert result["budget"]["spent_microusd"] == 80000
    assert result["budget"]["released_microusd"] == 16000
    assert result["comparison"]["known_failure_replay"] == "reused_recorded"
    assert any(e["kind"] == "known_replay_reused" for e in result["events"])


def test_stale_source_or_diagnostic_refuses_accept_without_releasing_old_hold(tmp_path, monkeypatch):
    client, old = prepared(tmp_path, monkeypatch)
    path = f"/runs/{old['id']}/after-retry"
    q = client.post(path + "/quote", json={}, headers=OWNER).json()
    monkeypatch.setattr(retry, "diagnostic_proof", lambda db, run: {"changed": True})
    assert client.post(path, json={"quote_id": q["id"]}, headers={**OWNER, "Idempotency-Key": "approved"}).status_code == 409
    with client.app.state.service.store.transaction() as db:
        assert db.execute("SELECT released FROM reservations WHERE id=?", (old["reservation_id"],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 1


def test_reuse_trace_retains_source_hashes_and_original_time_without_private_payloads():
    from tryfolk.trace_policy import TracePolicy
    policy = TracePolicy({"id": "a" * 32, "provider": "dodo", "pass": "after"})
    data = {"source_run_id": "b" * 32, "trace_id": "c" * 32, "reused": True,
            "new_diagnostic_execution": False, "not_in_population_denominator": True,
            "recorded_at": "2026-10-12T21:00:00+00:00", "download_path": "private-path",
            "original_events": [{"event_id": 123, "kind": "known_failure_replay",
                                 "payload_sha256": "d" * 64, "payment": "private-payment"}]}
    safe = policy.summary("known_replay_reused", data)
    assert safe["source_run_id"] == data["source_run_id"]
    assert safe["recorded_at"] == data["recorded_at"]
    assert safe["original_events"] == [{"event_id": 123, "kind": "known_failure_replay", "payload_sha256": "d" * 64}]
    assert "private-path" not in json.dumps(safe) and "private-payment" not in json.dumps(safe)


def test_failed_retry_chain_keeps_canonical_diagnostic_and_fresh_reservations(tmp_path, monkeypatch):
    client, original = prepared(tmp_path, monkeypatch)
    path = f"/runs/{original['id']}/after-retry"
    q = client.post(path + "/quote", json={}, headers=OWNER).json()
    first = client.post(path, json={"quote_id": q["id"]}, headers={**OWNER, "Idempotency-Key": "first"}).json()
    service = client.app.state.service
    with service.store.transaction() as db:
        retained = service.store.load(db, "runs", first["id"])
        retained.update(phase="blocked", gates=[{"code": "runtime", "message": "fixture boundary failed"}])
        service.store.save(db, "runs", retained)
        db.execute("UPDATE jobs SET status='done' WHERE run_id=?", (first["id"],))
        db.execute("UPDATE reservations SET spent=80000 WHERE id=?", (first["id"],))
    second_path = f"/runs/{first['id']}/after-retry"
    q2 = client.post(second_path + "/quote", json={}, headers=OWNER).json()
    assert q2["known_replay_source_run_id"] == original["id"]
    assert q2["retry_budget"]["release_microusd"] == 16000
    second = client.post(second_path, json={"quote_id": q2["id"]}, headers={**OWNER, "Idempotency-Key": "second"})
    assert second.status_code == 201, second.text
    fresh = second.json()
    assert len({original["id"], first["id"], fresh["id"]}) == 3
    assert fresh["status"]["after_retry"]["known_replay_source_run_id"] == original["id"]
    with service.store.transaction() as db:
        current = service.store.load(db, "runs", fresh["id"])
        population = service.store.load(db, "populations", current["population_id"])
        assert retry.validate_reuse(service, db, current, population)["source_run_id"] == original["id"]
        historical = service.store.load(db, "runs", first["id"])
        assert historical["phase"] == "blocked" and historical["gates"] == retained["gates"]
        assert db.execute("SELECT spent,released FROM reservations WHERE id=?", (first["id"],)).fetchone()[:] == (80000, 16000)
        historical["known_replay_reused"] = {"source_run_id": "wrong"}
        service.store.save(db, "runs", historical)
        with pytest.raises(Exception, match="original genuinely completed"):
            retry.validate_reuse(service, db, current, population)


def test_added_operator_allocation_keeps_every_native_field_and_discloses_no_cash(tmp_path, monkeypatch):
    from test_cfo_budget import allocated_plan
    client, old = prepared(tmp_path, monkeypatch)
    service = client.app.state.service
    plan = allocated_plan()
    plan["assumptions"]["quote_budget_microusd"] = 1000000
    plan["snapshot_sha256"] = "e" * 64  # Imported derivative, native inspection fields stay exact.
    service.settings.cfo_plan_path.write_text(json.dumps(plan))
    q = client.post(f"/runs/{old['id']}/after-retry/quote", json={}, headers=OWNER)
    assert q.status_code == 200, q.text
    q = q.json()
    assert q["operator_forecast"]["allocation"] == plan["operator_allocation"]
    assert q["operator_forecast"]["incremental_cash_authorized"] is False
    assert q["reserved_microusd"] == 96000
    plan["source_url"] += "/changed"
    plan["operator_allocation"]["native_forecast_source_url"] = plan["source_url"]
    service.settings.cfo_plan_path.write_text(json.dumps(plan))
    denied = client.post(f"/runs/{old['id']}/after-retry/quote", json={}, headers=OWNER)
    assert denied.status_code == 409
    with service.store.transaction() as db:
        assert db.execute("SELECT released FROM reservations WHERE id=?", (old["reservation_id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("field", ["before_per_persona_microusd", "repair_microusd", "contingency_basis_points", "snapshot_sha256"])
def test_changed_native_rates_caps_or_contingency_cannot_create_retry(tmp_path, monkeypatch, field):
    client, old = prepared(tmp_path, monkeypatch)
    service = client.app.state.service
    plan = json.loads(service.settings.cfo_plan_path.read_text())
    if field == "snapshot_sha256":
        plan[field] = "e" * 64
    else:
        plan["assumptions"][field] += 1
    service.settings.cfo_plan_path.write_text(json.dumps(plan))
    assert client.post(f"/runs/{old['id']}/after-retry/quote", json={}, headers=OWNER).status_code == 409
    with service.store.transaction() as db:
        assert db.execute("SELECT released FROM reservations WHERE id=?", (old["reservation_id"],)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM reservations").fetchone()[0] == 1


def test_hosted_checkout_admission_is_closed_and_never_reports_inferred_payment():
    from tryfolk.trace_policy import TracePolicy
    policy = TracePolicy({"id": "a" * 32, "provider": "dodo", "pass": "after"})
    safe = policy.summary("hosted_checkout_admission", {"phase": "exit", "result": "owned_return_settled",
        "capacity": 1, "wait_ms": 123, "duration_ms": 456, "queue_bound_ms": 300000,
        "payment_outcome_inferred": False,
        "payment_id": "private", "url": "https://private"})
    assert safe["phase"] == "exit" and safe["duration_ms"] == 456
    assert safe["payment_outcome_inferred"] is False
    assert safe["queue_bound_ms"] == 300000
    assert "private" not in json.dumps(safe)
    invalid = policy.summary("hosted_checkout_admission", {"phase": "invalid", "result": "success",
        "capacity": 4, "queue_bound_ms": 999999, "payment_outcome_inferred": True})
    assert not any(k in invalid for k in ("phase", "result", "capacity", "queue_bound_ms", "payment_outcome_inferred"))


def test_admission_capacity_one_can_be_scoped_to_independent_persona_without_private_identity():
    from tryfolk.trace_policy import TracePolicy
    policy = TracePolicy({"id": "a" * 32, "provider": "dodo", "pass": "after"})
    data = {"phase": "enter", "capacity": 1, "ownership_scope": "persona",
            "spacing_basis": "independent_persona_worker", "configured_minimum_spacing_ms": 0,
            "cooldown_ms": 0, "wait_ms": 0, "payment_outcome_inferred": False,
            "account_id": "private-account", "lock_key": "private-lock"}
    for kind in ("native_pay_admission", "hosted_checkout_admission"):
        safe = policy.summary(kind, data)
        assert safe["capacity"] == 1 and safe["ownership_scope"] == "persona"
        assert "private" not in json.dumps(safe)
        if kind == "native_pay_admission":
            assert safe["configured_minimum_spacing_ms"] == 0
            assert safe["spacing_basis"] == "independent_persona_worker"
        rejected = policy.summary(kind, {**data, "ownership_scope": "arbitrary-owner", "spacing_basis": "unproven-provider-quota"})
        assert "ownership_scope" not in rejected and "spacing_basis" not in rejected
