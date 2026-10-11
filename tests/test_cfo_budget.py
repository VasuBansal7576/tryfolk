"""Finalized CFO contracts are unit fixtures, never native CFO model evidence."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from tryfolk.api import create_app
from tryfolk.worker import Worker
from test_api import auth, client_at, quote, start
from test_worker import FixtureIO


def finalized_plan(**overrides):
    assumptions = {
        "before_per_persona_microusd": 20_000,
        "after_per_persona_microusd": 20_000,
        "discovery_microusd": 10_000,
        "generation_microusd": 10_000,
        "investigation_microusd": 10_000,
        "repair_microusd": 10_000,
        "known_replay_microusd": 10_000,
        "contingency_basis_points": 2_000,
        "quote_budget_microusd": 1_000_000,
        "lifetime_budget_microusd": 1_000_000,
    }
    assumptions.update(overrides)
    return {
        "schema_version": 2,
        "source": "cfo.ai-inspected-plan",
        "source_url": "https://app.cfo.ai/unit-fixture/pages/unit-fixture",
        "plan_id": "UNIT-TEST-FIXTURE",
        "status": "finalized",
        "currency": "USD",
        "inspected_at": "2026-10-10T18:00:00+00:00",
        "inspection_evidence_sha256": "a" * 64,
        "snapshot_sha256": "b" * 64,
        "assumptions": assumptions,
        "assumption_provenance": {
            key: {"basis": "forecast", "reference": f"unit-fixture/table/{key}", "evidence_sha256": "a" * 64}
            for key in assumptions
        },
        "native_ledger_snapshot": {
            "estimated_consumed_microusd": 550_000,
            "held_microusd": 0,
            "available_microusd": 450_000,
            "observed_at": "2026-10-10T18:00:00+00:00",
            "evidence_sha256": "a" * 64,
        },
    }


def client_with_plan(client, path, value):
    path.write_text(json.dumps(value))
    return TestClient(create_app(replace(client.app.state.service.settings, cfo_plan_path=path)))


@pytest.mark.asyncio
async def test_contingency_quote_uses_lifetime_headroom_once_and_preserves_requested_n(tmp_path):
    original = client_at(tmp_path / "runs.sqlite")
    io = FixtureIO()
    worker = Worker(original.app.state.service, io)
    # Actual durable public lifecycles establish 550k consumed; only external I/O is a fixture.
    for n, key in [(10, "old10"), (15, "old15")]:
        run = start(original, quote(original, n), key).json()
        await worker.tick()
        original.post(f"/runs/{run['id']}/cancel", headers=auth(run))

    class DiscoveryUnavailable(FixtureIO):
        async def discover(self, run):
            raise RuntimeError("unit fixture: discovery unavailable")

    failed = start(original, quote(original, 1), "old-failed").json()
    await Worker(original.app.state.service, DiscoveryUnavailable()).tick()
    original.post(f"/runs/{failed['id']}/cancel", headers=auth(failed))
    assert quote(original, 1)["available_microusd"] == 450_000
    client = client_with_plan(original, tmp_path / "plan.json", finalized_plan())
    eight = quote(client, 8)
    assert eight["persona_count"] == 8
    assert eight["before_microusd"] == eight["after_microusd"] == 160_000
    assert eight["overhead_microusd"] == 60_000
    assert eight["contingency_microusd"] == 76_000
    assert eight["reserved_microusd"] == 456_000
    assert eight["after_contingency_microusd"] == 34_000
    assert eight["after_reserve_microusd"] == 204_000
    assert eight["lifetime_available_microusd"] == eight["available_microusd"] == 450_000
    assert eight["ledger"] == {"estimated_consumed_microusd": 550_000, "held_microusd": 0}
    assert eight["forecast_comparison"]["native_reserved_microusd"] == 444_000
    assert eight["affordable"] is False
    assert start(client, eight, "eight").status_code == 402
    ten = quote(client, 10)
    assert ten["persona_count"] == 10 and ten["affordable"] is False
    assert ten["reserved_microusd"] == 552_000
    seven = quote(client, 7)
    assert seven["reserved_microusd"] == 408_000 and seven["affordable"] is True
    assert start(client, seven, "seven").json()["status"]["persona_count"] == 7
    assert quote(client, 7)["lifetime_available_microusd"] == 42_000


def test_customer_quote_cap_is_independent_and_cannot_be_bypassed_by_plan_reload(tmp_path):
    base = client_at(tmp_path / "runs.sqlite")
    path = tmp_path / "plan.json"
    client = client_with_plan(base, path, finalized_plan(quote_budget_microusd=120_000))
    denied = quote(client, 2)
    assert denied["reserved_microusd"] == 168_000
    assert denied["lifetime_available_microusd"] == 1_000_000
    assert denied["available_microusd"] == denied["quote_limit_microusd"] == 120_000
    assert denied["affordable"] is False
    assert start(client, denied).status_code == 402
    # A newer plan may raise the quote cap, but cannot rewrite an already issued price/cap.
    current = client_with_plan(base, path, finalized_plan(quote_budget_microusd=1_000_000))
    assert quote(current, 2)["affordable"] is True
    assert start(current, denied, "bypass-old-cap").status_code == 402
    assert quote(current, 2)["lifetime_available_microusd"] == 1_000_000


@pytest.mark.asyncio
async def test_rounded_contingency_protects_same_n_replay_and_original_quote_after_configuration_changes(tmp_path):
    from tryfolk.config import Target

    base = client_at(tmp_path / "runs.sqlite")
    settings = replace(base.app.state.service.settings, targets=(Target(
        url="http://127.0.0.1:3100", revision="broken-sha", fault="paid-no-access", repo_path=str(tmp_path)
    ),))
    original = TestClient(create_app(settings))
    path = tmp_path / "plan.json"
    client = client_with_plan(original, path, finalized_plan(
        before_per_persona_microusd=20_001, after_per_persona_microusd=20_003,
        contingency_basis_points=1,
    ))
    accepted = quote(client, 3)
    # One basis point on subtotal180012 is18.0012 microUSD, rounded up once to19.
    assert accepted["reserved_microusd"] == 180_031
    assert accepted["contingency_microusd"] == 19
    assert accepted["after_contingency_microusd"] == 8
    assert accepted["after_reserve_microusd"] == 70_017
    path.write_text(json.dumps(finalized_plan(before_per_persona_microusd=100_000)))
    current = TestClient(create_app(replace(client.app.state.service.settings, per_persona_microusd=100_000)))
    before = start(current, accepted).json()
    assert before["status"]["budget_contract"] == accepted
    assert before["status"]["budget"]["reserved_microusd"] == 180_031

    class RepairIO(FixtureIO):
        async def repair(self, run, emit):
            current.app.state.service.register_repair(
                run["repair"]["id"], head_revision="unit-fixed-sha",
                preview_url="http://127.0.0.1:3102", checkpoint_id="unit-fixture-checkpoint",
                checkpoint_url=None, provider="local-fixture",
            )

        async def known_replay(self, run, population, emit):
            await emit("known_failure_replay", {"fixture": True, "passed": True})

    io = RepairIO()
    worker = Worker(current.app.state.service, io)
    await worker.tick()
    b = current.get(f"/runs/{before['id']}", headers=auth(before)).json()
    assert b["phase"] == "completed"
    assert b["budget"]["spent_microusd"] == 80_003
    # Pre-retest work must not take even one microUSD from the protected known+after+contingency.
    with pytest.raises(RuntimeError, match="retest reserve protected"):
        worker.debit(before["id"], 30_012)
    assert current.get(f"/runs/{before['id']}", headers=auth(before)).json()["budget"]["spent_microusd"] == 80_003
    finding = current.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    owner = {"Authorization": "Bearer test-owner"}
    repair = current.post(f"/runs/{before['id']}/repairs", json={"finding_id": finding["id"]}, headers=owner)
    assert repair.status_code == 202
    await worker.tick()
    response = current.post(
        f"/runs/{before['id']}/replay", json={"repair_id": repair.json()["id"]},
        headers={**owner, "Idempotency-Key": "paired"},
    )
    assert response.status_code == 201, response.text
    after = response.json()
    await worker.tick()
    a = current.get(f"/runs/{after['id']}", headers=auth(after)).json()
    assert a["phase"] == "completed" and a["counts"]["completed"] == 3
    assert a["personas"] == b["personas"] and a["population_id"] == b["population_id"]
    assert a["persona_count"] == 3 and a["budget_contract"] == accepted
    assert a["budget"]["reserved_microusd"] == 180_031
    assert a["budget"]["spent_microusd"] == 170_012
    assert a["budget"]["released_microusd"] == 10_019
    assert a["budget"]["unused_microusd"] == 0
    assert io.discovery_calls == 1 and len(set(io.accounts)) == 6


def test_importer_requires_final_inspection_evidence_and_preserves_existing_output_on_rejection(tmp_path):
    import hashlib
    from pathlib import Path
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[1] / "scripts/cfo_export.py"
    evidence = tmp_path / "inspection.txt"
    evidence.write_bytes(b"")  # Explicit unit fixture, known SHA256 of empty content.
    known = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    value = finalized_plan()
    value["inspection_evidence_sha256"] = known
    value["native_ledger_snapshot"]["evidence_sha256"] = known
    for source in value["assumption_provenance"].values():
        source["evidence_sha256"] = known
    snapshot = tmp_path / "inspected.json"
    output = tmp_path / "imported.json"
    output.write_text("previous accepted contract")
    command = [sys.executable, str(script), str(snapshot), "--source-url", value["source_url"],
               "--plan-id", value["plan_id"], "--evidence", str(evidence), "--output", str(output)]
    value["status"] = "building"
    snapshot.write_text(json.dumps(value))
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0 and "finalized" in rejected.stderr
    assert output.read_text() == "previous accepted contract"
    value["status"] = "finalized"
    snapshot.write_text(json.dumps(value))
    evidence.write_text("different inspected evidence")
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0 and "evidence" in rejected.stderr
    assert output.read_text() == "previous accepted contract"
    evidence.write_bytes(b"")
    accepted = subprocess.run(command, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    imported = json.loads(output.read_text())
    assert imported["assumptions"] == value["assumptions"]
    assert imported["assumption_provenance"] == value["assumption_provenance"]
    assert imported["native_ledger_snapshot"] == value["native_ledger_snapshot"]
    assert imported["snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    assert imported["source_url"] == value["source_url"]
    assert json.loads(accepted.stdout)["status"] == "imported_inspected_forecast"


def test_invalid_forecast_blocks_new_quotes_and_old_quote_acceptance_with_actionable_error(tmp_path):
    base = client_at(tmp_path / "runs.sqlite")
    accepted = quote(base, 1)
    path = tmp_path / "plan.json"
    path.write_text("[]")
    client = TestClient(create_app(replace(base.app.state.service.settings, cfo_plan_path=path)))
    response = client.post("/quotes", json={"app_url": accepted["app_url"], "persona_count": 1})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "cfo_export_invalid"
    response = start(client, accepted)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "cfo_export_invalid"
    # Failed validation did not reserve the old quote or spend anything.
    assert start(base, accepted).status_code == 201


@pytest.mark.asyncio
async def test_unpaid_known_replay_recovery_keeps_after_contingency_without_reserving_diagnostic_twice(tmp_path):
    from tryfolk.config import Target

    base = client_at(tmp_path / "runs.sqlite")
    base = TestClient(create_app(replace(base.app.state.service.settings, targets=(Target(
        url="http://127.0.0.1:3100", revision="broken-sha", fault="paid-no-access", repo_path=str(tmp_path)
    ),))))
    client = client_with_plan(base, tmp_path / "plan.json", finalized_plan())
    before = start(client, quote(client, 1)).json()

    class InterruptedReplay(FixtureIO):
        async def repair(self, run, emit):
            client.app.state.service.register_repair(
                run["repair"]["id"], head_revision="unit-fixed", preview_url="http://127.0.0.1:3102",
                checkpoint_id="unit-fixture", checkpoint_url=None, provider="local-fixture",
            )

        async def known_replay(self, run, population, emit):
            raise RuntimeError("All connection attempts failed")

    worker = Worker(client.app.state.service, InterruptedReplay())
    await worker.tick()
    owner = {"Authorization": "Bearer test-owner"}
    finding = client.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    repair = client.post(f"/runs/{before['id']}/repairs", json={"finding_id": finding["id"]}, headers=owner)
    await worker.tick()
    # Bounded prior investigation uses all baseline contingency, stopping at the after floor.
    worker.debit(before["id"], 24_000)
    after = client.post(f"/runs/{before['id']}/replay", json={"repair_id": repair.json()["id"]},
                        headers={**owner, "Idempotency-Key": "after"}).json()
    await worker.tick()
    blocked = client.get(f"/runs/{after['id']}", headers=auth(after)).json()
    assert blocked["budget"]["reserved_microusd"] == 120_000
    assert blocked["after_reserve_microusd"] == 36_000
    assert blocked["budget"]["spent_microusd"] == 94_000
    recovered = client.post(f"/runs/{after['id']}/recover", headers=owner)
    assert recovered.status_code == 202, recovered.text
    assert recovered.json()["budget"] == blocked["budget"]
    assert recovered.json()["personas"] == blocked["personas"]
    assert recovered.json()["persona_count"] == 1
    assert recovered.json()["after_reserve_microusd"] == 36_000


def test_replay_rejects_a_caller_supplied_population_count(tmp_path):
    client = client_at(tmp_path / "runs.sqlite")
    response = client.post(
        "/runs/not-a-run/replay", json={"repair_id": "not-a-repair", "persona_count": 1},
        headers={"Authorization": "Bearer test-owner", "Idempotency-Key": "cannot-resize"},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "persona_count"]
