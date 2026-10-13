"""Recovery fixtures exercise durable ownership/budget rules, never live payments."""
import json

import pytest

from test_api import auth, client_at, quote, start
from tryfolk.scenarios import freeze_population
from tryfolk.worker import Worker


GATE = "Recorded intent has no unique freshly observed control; diagnostic stops without navigation substitution."
OWNER = {"Authorization": "Bearer test-owner"}


def blocked_after(tmp_path):
    client = client_at(tmp_path / "runs.sqlite")
    original = start(client, quote(client, 4)).json()
    service = client.app.state.service
    with service.store.transaction() as db:
        before = service.store.load(db, "runs", original["id"])
        population = freeze_population([
            {"id": f"persona-{n}", "profile": {"goal": "fixture purchase"},
             "conditions": {"history": "new"}, "memory": []} for n in range(4)
        ], None)
        db.execute("UPDATE populations SET payload=? WHERE id=?", (json.dumps(population), before["population_id"]))
        repair = {"id": "repair-fixture", "run_id": before["id"], "status": "completed", "provider": "dodo",
                  "head_revision": "a" * 40, "preview_url": "http://127.0.0.1:3310",
                  "checkpoint_id": "checkpoint-fixture", "source_digest": "b" * 64,
                  "target_revision": "sha256:" + "c" * 64}
        before.update(phase="completed", provider="dodo", repair=repair)
        service.store.save(db, "runs", before)
        db.execute("INSERT INTO repairs VALUES (?,?,?)", (repair["id"], before["id"], json.dumps(repair)))
        after = {**before, "id": "after-fixture", "parent_run_id": before["id"], "pass": "after", "phase": "blocked",
                 "source_commit": repair["head_revision"], "source_digest": repair["source_digest"],
                 "preview_url": repair["preview_url"], "revision": repair["target_revision"],
                 "gates": [{"code": "runtime", "message": GATE}], "outcomes": {},
                 "after_reserve_microusd": 108_000, "after_reserve_includes_known_replay": True,
                 "known_replay_allowance_consumed": True,
                 "budget_contract": {"execution_allowances": {"known_replay_microusd": 10_000,
                                                               "after_per_persona_microusd": 20_000}}}
        db.execute("INSERT INTO runs VALUES (?,?,?)", (after["id"], json.dumps(after), "after-access"))
        db.execute("UPDATE jobs SET status='completed' WHERE run_id=?", (before["id"],))
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", ("after-job", after["id"], "run", "blocked", "fixture-time"))
        db.execute("UPDATE reservations SET reserved=264000,spent=130000,after_started=1 WHERE id=?", (before["reservation_id"],))
        db.execute("INSERT INTO events(run_id,kind,created_at,data,trace_url) VALUES (?,?,?,?,?)",
                   (after["id"], "known_replay_action", "fixture-time", json.dumps({"diagnostic": True}),
                    "https://app.neatlogs.com/traces/" + "d" * 32))
    return client, after


def test_owner_recovery_preserves_after_cohort_history_and_original_hold(tmp_path):
    client, after = blocked_after(tmp_path)
    endpoint = f"/runs/{after['id']}/recover"
    assert client.post(endpoint, headers=auth({"access_token": "after-access"})).status_code == 403
    result = client.post(endpoint, headers=OWNER)
    assert result.status_code == 202, result.text
    recovered = result.json()
    assert recovered["id"] == after["id"] and recovered["population_id"] == after["population_id"]
    assert recovered["phase"] == "queued" and recovered["execution_attempt"] == 2
    assert recovered["budget"]["reserved_microusd"] == 264_000
    assert recovered["budget"]["spent_microusd"] == 140_000
    assert recovered["budget"]["unused_microusd"] == 124_000
    assert recovered["recovery"]["protected_after_allowance_microusd"] == 98_000
    assert recovered["recovery"]["prior_gates"] == after["gates"]
    assert recovered["recovery"]["prior_trace_urls"] == ["https://app.neatlogs.com/traces/" + "d" * 32]
    assert [e["kind"] for e in recovered["events"]] == ["known_replay_action", "after_diagnostic_recovery_requested"]
    assert client.post(endpoint, headers=OWNER).json()["budget"] == recovered["budget"]
    with client.app.state.service.store.transaction() as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE run_id=?", (after["id"],)).fetchone()[0] == 1
        assert db.execute("SELECT status FROM jobs WHERE id='after-job'").fetchone()[0] == "queued"


def test_second_recovery_accepts_exact_preaction_drift_but_keeps_cap(tmp_path):
    client, after = blocked_after(tmp_path)
    service = client.app.state.service
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", after["id"])
        run.update(execution_attempt=2, recoveries=[{"kind": "fresh_dodo_known_case_diagnostic"}],
                   gates=[{"code": "runtime", "message": "stagehand: Document changed after observation; no browser action was executed or retried."}])
        service.store.save(db, "runs", run)
    response = client.post(f"/runs/{after['id']}/recover", headers=OWNER)
    assert response.status_code == 202, response.text
    assert response.json()["execution_attempt"] == 3
    assert len(response.json()["recoveries"]) == 2


@pytest.mark.asyncio
async def test_requeued_worker_uses_attempt_two_without_debiting_diagnostic_twice(tmp_path):
    client, after = blocked_after(tmp_path)
    assert client.post(f"/runs/{after['id']}/recover", headers=OWNER).status_code == 202

    class FixtureIO:
        async def known_replay(self, run, population, emit):
            assert run["execution_attempt"] == 2 and len(population) == 4
            await emit("known_failure_replay", {"diagnostic": True, "outcome": "completed"})

        async def journey(self, run, persona, cancelled, emit):
            return {"outcome": "completed", "classification": "fixture", "measured": False}

    await Worker(client.app.state.service, FixtureIO()).tick()
    result = client.get(f"/runs/{after['id']}", headers=OWNER).json()
    assert result["phase"] == "completed" and result["counts"]["executed"] == 4
    assert result["budget"]["spent_microusd"] == 220_000  # historical 130k + fresh 10k + four 20k
    assert result["budget"]["released_microusd"] == 44_000
    assert [e["kind"] for e in result["events"]][:3] == [
        "known_replay_action", "after_diagnostic_recovery_requested", "connection_recovery",
    ]


def test_comparison_deduplicates_same_paid_check_without_merging_distinct_accounts(tmp_path):
    client, after = blocked_after(tmp_path)
    service = client.app.state.service
    first = {"case": "duplicate_reconciliation_grants_once", "account_id": "first-account",
             "order_id": "first-order", "passed": True}
    other = {**first, "account_id": "second-account", "order_id": "second-order"}
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", after["id"])
        run["comparison"] = {"after_run_id": after["id"]}
        service.store.save(db, "runs", run)
        for checks in [[first], [first, other]]:
            db.execute("INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
                       (after["id"], "held_out_checks", "fixture-time", json.dumps({"cases": checks})))
    result = client.get(f"/runs/{after['id']}", headers=OWNER).json()
    checks = result["comparison"]["held_out_checks"]
    assert checks["total"] == checks["passed"] == 2
    assert checks["recorded_total"] == 3 and checks["deduplicated_repeated_records"] == 1
    assert checks["cases"] == [first, other]
    assert len([e for e in result["events"] if e["kind"] == "held_out_checks"]) == 2


@pytest.mark.parametrize("change", ["adaptive", "outcome", "repair", "budget", "released", "limit", "gate", "ambiguous_pay", "other_drift", "mixed_gates"])
def test_recovery_rejects_unsafe_or_unfunded_after_without_mutation(tmp_path, change):
    client, after = blocked_after(tmp_path)
    service = client.app.state.service
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", after["id"])
        if change == "adaptive":
            db.execute("INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
                       (after["id"], "persona_initialized", "fixture-time", "{}"))
        elif change == "outcome":
            run["outcomes"] = {"persona-0": {"outcome": "completed"}}
        elif change == "repair":
            run["revision"] = "sha256:" + "e" * 64
        elif change == "budget":
            db.execute("UPDATE reservations SET spent=160001 WHERE id=?", (after["reservation_id"],))
        elif change == "released":
            db.execute("UPDATE reservations SET released=1 WHERE id=?", (after["reservation_id"],))
        elif change == "limit":
            run["recoveries"] = [{}, {}]
        elif change == "gate":
            run["gates"] = [{"message": "ambiguous Pay"}]
        elif change == "ambiguous_pay":
            run["gates"] = [{"message": "stagehand: Pay may have executed; do not retry."}]
        elif change == "other_drift":
            run["gates"] = [{"message": "stagehand: Document changed after observation."}]
        elif change == "mixed_gates":
            run["gates"].append({"message": "Pay may have executed"})
        service.store.save(db, "runs", run)
        initial = tuple(db.execute("SELECT reserved,spent,released FROM reservations WHERE id=?", (after["reservation_id"],)).fetchone())
    response = client.post(f"/runs/{after['id']}/recover", headers=OWNER)
    assert response.status_code == 409, response.text
    with service.store.transaction() as db:
        assert tuple(db.execute("SELECT reserved,spent,released FROM reservations WHERE id=?", (after["reservation_id"],)).fetchone()) == initial
        assert service.store.load(db, "runs", after["id"])["phase"] == "blocked"
