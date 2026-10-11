import pytest
from fastapi.testclient import TestClient
from tryfolk.api import create_app
from tryfolk.config import Settings, Target
from tryfolk.worker import Worker
from test_api import quote, start, auth


class FixtureIO:
    """Only external I/O is replaced. These outcomes are fixtures, never live evidence."""

    def __init__(self):
        self.discovery_calls = 0
        self.accounts = []

    async def discover(self, run):
        self.discovery_calls += 1
        return {"observed": ["credits checkout"], "revision": run["revision"]}

    async def generate(self, brief, count):
        return [
            {
                "id": f"p{i}",
                "index": i,
                "name": f"Customer {i}",
                "profile": {"goal": "buy credits"},
                "conditions": {"history": "new"},
                "memory": [],
            }
            for i in range(count)
        ]

    async def journey(self, run, persona, cancelled, emit):
        self.accounts.append((run["id"], persona["id"]))
        await emit("observation", {"visible": "checkout"}, persona["id"])
        return {
            "outcome": "failed" if run["fault"] == "paid-no-access" else "completed",
            "classification": "paid_without_access",
            "measured": False,
            "provider": "local-fixture",
            "account_id": run["id"] + persona["id"],
        }


@pytest.mark.asyncio
async def test_bounded_population_is_durable_and_cancelled_queue_never_executes(tmp_path):
    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        owner_token="test-owner",
        gates=(),
        targets=(Target(url="http://127.0.0.1:3100", revision="broken-sha", fault="paid-no-access"),),
    )
    client = TestClient(create_app(settings))
    run = start(client, quote(client, 3)).json()
    io = FixtureIO()
    worker = Worker(client.app.state.service, io)
    await worker.tick()
    status = client.get(f"/runs/{run['id']}", headers=auth(run)).json()
    assert status["counts"] == {
        "requested": 3,
        "generated": 3,
        "queued": 0,
        "executed": 3,
        "completed": 0,
        "failed": 3,
        "abandoned": 0,
        "pending": 0,
        "blocked": 0,
    }
    assert io.discovery_calls == 1
    assert len(client.get(f"/runs/{run['id']}/findings", headers=auth(run)).json()["findings"]) == 3
    queued = start(client, quote(client, 3), "queued").json()
    client.post(f"/runs/{queued['id']}/cancel", headers=auth(queued))
    assert await worker.tick() is False
    assert len(io.accounts) == 3


@pytest.mark.asyncio
async def test_replay_preserves_exact_n_saved_personas_reset_conditions_and_reservation(tmp_path):
    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        owner_token="test-owner",
        gates=(),
        targets=(
            Target(
                url="http://127.0.0.1:3100",
                revision="broken-sha",
                fault="paid-no-access",
                repo_path=str(tmp_path),
            ),
        ),
    )
    io = FixtureIO()
    app = create_app(settings)
    client = TestClient(app)
    before = start(client, quote(client, 3)).json()
    worker = Worker(app.state.service, io)
    await worker.tick()
    finding = client.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    assert (
        client.post(
            f"/runs/{before['id']}/repairs", json={"finding_id": finding["id"]}, headers=auth(before)
        ).status_code
        == 403
    )
    repair = client.post(
        f"/runs/{before['id']}/repairs",
        json={"finding_id": finding["id"]},
        headers={"Authorization": "Bearer test-owner"},
    )
    assert repair.status_code == 202, repair.text
    # External repair/preview registration boundary only is represented as an I/O fixture.
    app.state.service.register_repair(
        repair.json()["id"],
        head_revision="fixed-sha",
        preview_url="http://127.0.0.1:3102",
        checkpoint_id="fixture-checkpoint",
        checkpoint_url=None,
        provider="local-fixture",
    )
    response = client.post(
        f"/runs/{before['id']}/replay",
        json={"repair_id": repair.json()["id"]},
        headers={"Authorization": "Bearer test-owner", "Idempotency-Key": "after"},
    )
    assert response.status_code == 201, response.text
    after = response.json()
    await worker.tick()
    b = client.get(f"/runs/{before['id']}", headers=auth(before)).json()
    a = client.get(f"/runs/{after['id']}", headers=auth(after)).json()
    assert a["personas"] == b["personas"]
    assert a["persona_count"] == 3
    assert a["population_id"] == b["population_id"]
    assert a["counts"]["completed"] == 3
    assert a["budget"]["reserved_microusd"] == b["budget"]["reserved_microusd"] == 170_000
    assert a["comparison"]["same_personas"] is True
    assert len(set(io.accounts)) == 6
    assert io.discovery_calls == 1
    assert (
        client.post(
            f"/runs/{before['id']}/replay",
            json={"repair_id": repair.json()["id"]},
            headers={"Authorization": "Bearer test-owner", "Idempotency-Key": "again"},
        ).status_code
        == 409
    )


@pytest.mark.asyncio
async def test_active_cancellation_stops_next_persona_and_discards_late_success(tmp_path):
    import asyncio

    class SlowIO(FixtureIO):
        async def journey(self, run, persona, cancelled, emit):
            self.accounts.append((run["id"], persona["id"]))
            while not cancelled():
                await asyncio.sleep(0.01)
            return {"outcome": "completed", "classification": "late", "measured": False}

    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        gates=(),
        targets=(Target(url="http://127.0.0.1:3100", revision="broken-sha"),),
    )
    app = create_app(settings)
    client = TestClient(app)
    run = start(client, quote(client, 3)).json()
    io = SlowIO()
    task = asyncio.create_task(Worker(app.state.service, io).tick())
    while not io.accounts:
        await asyncio.sleep(0.01)
    client.post(f"/runs/{run['id']}/cancel", headers=auth(run)).json()
    await task
    status = client.get(f"/runs/{run['id']}", headers=auth(run)).json()
    assert status["phase"] == "cancelled"
    assert status["counts"]["completed"] == 0
    assert len(io.accounts) == 1
    assert status["budget"]["spent_microusd"] == 40_000
    assert status["budget"]["released_microusd"] == 130_000


@pytest.mark.asyncio
async def test_cancel_baseline_stops_queued_replay_before_releasing_shared_reservation(tmp_path):
    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        owner_token="test-owner",
        gates=(),
        targets=(
            Target(
                url="http://127.0.0.1:3100",
                revision="broken-sha",
                fault="paid-no-access",
                repo_path=str(tmp_path),
            ),
        ),
    )
    app = create_app(settings)
    client = TestClient(app)
    io = FixtureIO()
    worker = Worker(app.state.service, io)
    before = start(client, quote(client, 2)).json()
    await worker.tick()
    finding = client.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    owner = {"Authorization": "Bearer test-owner"}
    repair = client.post(
        f"/runs/{before['id']}/repairs", headers=owner, json={"finding_id": finding["id"]}
    ).json()
    app.state.service.register_repair(
        repair["id"],
        head_revision="fixed-sha",
        preview_url="http://127.0.0.1:3102",
        checkpoint_id="fixture-only",
        checkpoint_url=None,
        provider="local-fixture",
    )
    after = client.post(
        f"/runs/{before['id']}/replay",
        headers=owner | {"Idempotency-Key": "after"},
        json={"repair_id": repair["id"]},
    ).json()
    client.post(f"/runs/{before['id']}/cancel", headers=auth(before))
    result = client.get(f"/runs/{after['id']}", headers=auth(after)).json()
    assert result["phase"] == "cancelled"
    assert result["counts"]["executed"] == 0
    assert result["budget"]["estimated_consumed_microusd"] == 60_000
    assert result["budget"]["measured_cash_microusd"] == 0
    assert await worker.tick() is False
    assert len(io.accounts) == 2


@pytest.mark.asyncio
async def test_owner_connection_only_recovery_preserves_consumption_and_full_after_reserve(tmp_path):
    class ConnectionFailure(FixtureIO):
        async def known_replay(self, run, population, emit):
            raise ConnectionError("All connection attempts failed")

    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        owner_token="test-owner",
        gates=(),
        targets=(
            Target(
                url="http://127.0.0.1:3100",
                revision="broken-sha",
                fault="paid-no-access",
                repo_path=str(tmp_path),
            ),
        ),
    )
    app = create_app(settings)
    client = TestClient(app)
    worker = Worker(app.state.service, ConnectionFailure())
    before = start(client, quote(client, 1)).json()
    await worker.tick()
    owner = {"Authorization": "Bearer test-owner"}
    finding = client.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    repair = client.post(
        f"/runs/{before['id']}/repairs", headers=owner, json={"finding_id": finding["id"]}
    ).json()
    # External repair registration consumes its quoted allowance via normal worker boundary.
    worker.debit(before["id"], 20_000)
    app.state.service.register_repair(
        repair["id"],
        head_revision="fixed-sha",
        preview_url="http://127.0.0.1:3102",
        checkpoint_id="fixture-only",
        checkpoint_url=None,
        provider="local-fixture",
    )
    after = client.post(
        f"/runs/{before['id']}/replay",
        headers=owner | {"Idempotency-Key": "after"},
        json={"repair_id": repair["id"]},
    ).json()
    await worker.tick()
    assert client.post(f"/runs/{after['id']}/recover", headers=auth(after)).status_code == 403
    result = client.post(f"/runs/{after['id']}/recover", headers=owner)
    assert result.status_code == 202, result.text
    assert result.json()["budget"]["estimated_consumed_microusd"] == 70_000
    assert result.json()["budget"]["unused_microusd"] == 20_000
    assert result.json()["counts"]["requested"] == 1

    class Recovered(FixtureIO):
        async def known_replay(self, run, population, emit):
            await emit("known_failure_replay", {"outcome": "completed", "fixture": True})

    await Worker(app.state.service, Recovered()).tick()
    final = client.get(f"/runs/{after['id']}", headers=auth(after)).json()
    assert final["counts"]["completed"] == 1 and final["counts"]["executed"] == 1
    assert final["budget"]["estimated_consumed_microusd"] == 90_000
    assert final["budget"]["measured_cash_microusd"] == 0
    assert final["recovery"]["diagnostic_allowance_already_consumed_microusd"] == 10_000
    assert final["recovery"]["additional_estimated_consumption_microusd"] == 0


@pytest.mark.asyncio
async def test_diagnostic_recovery_is_bounded_and_never_retries_paid_or_adaptive_work(tmp_path):
    import json

    class InterruptedDiagnostic(FixtureIO):
        async def known_replay(self, run, population, emit):
            await emit(
                "known_failure_replay",
                {
                    "outcome": "blocked",
                    "classification": "verification_unavailable",
                    "payment": None,
                    "diagnostic": True,
                },
            )
            raise RuntimeError("Known failure replay did not pass; full retest stopped honestly.")

    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        owner_token="test-owner",
        gates=(),
        targets=(
            Target(
                url="http://127.0.0.1:3100",
                revision="broken-sha",
                fault="paid-no-access",
                repo_path=str(tmp_path),
            ),
        ),
    )
    app = create_app(settings)
    client = TestClient(app)
    service = app.state.service
    worker = Worker(service, InterruptedDiagnostic())
    before = start(client, quote(client, 1)).json()
    await worker.tick()
    owner = {"Authorization": "Bearer test-owner"}
    finding = client.get(f"/runs/{before['id']}/findings", headers=auth(before)).json()["findings"][0]
    repair = client.post(
        f"/runs/{before['id']}/repairs", headers=owner, json={"finding_id": finding["id"]}
    ).json()
    worker.debit(before["id"], 20_000)
    service.register_repair(
        repair["id"],
        head_revision="fixed",
        preview_url="http://127.0.0.1:3102",
        checkpoint_id="fixture-only",
        checkpoint_url=None,
        provider="local-fixture",
    )
    after = client.post(
        f"/runs/{before['id']}/replay",
        headers=owner | {"Idempotency-Key": "after"},
        json={"repair_id": repair["id"]},
    ).json()
    await worker.tick()
    recovered = client.post(f"/runs/{after['id']}/recover", headers=owner)
    assert recovered.status_code == 202, recovered.text
    assert recovered.json()["budget"]["estimated_consumed_microusd"] == 70_000
    assert recovered.json()["budget"]["unused_microusd"] == 20_000
    await worker.tick()
    # A second recovery consumes no new inference allocation but the third attempt is final.
    assert client.post(f"/runs/{after['id']}/recover", headers=owner).status_code == 202
    await worker.tick()
    assert client.post(f"/runs/{after['id']}/recover", headers=owner).status_code == 409
    with service.store.transaction() as db:
        run = service.store.load(db, "runs", after["id"])
        run.pop("recovery", None)
        run["recoveries"] = []
        service.store.save(db, "runs", run)
        db.execute(
            "INSERT INTO events(run_id,kind,created_at,data) VALUES (?,?,?,?)",
            (
                after["id"],
                "known_failure_replay",
                "now",
                json.dumps({"outcome": "pending", "payment": {"status": "pending"}}),
            ),
        )
    assert client.post(f"/runs/{after['id']}/recover", headers=owner).status_code == 409


@pytest.mark.asyncio
async def test_two_workers_share_one_durable_browser_execution_slot(tmp_path):
    import asyncio

    began = asyncio.Event()
    release = asyncio.Event()

    class PausedDiscovery(FixtureIO):
        async def discover(self, run):
            began.set()
            await release.wait()
            return await super().discover(run)

    client = TestClient(
        create_app(
            Settings(
                db_path=tmp_path / "runs.sqlite",
                owner_token="test-owner",
                gates=(),
                targets=(Target(url="http://127.0.0.1:3100", revision="rev"),),
            )
        )
    )
    first = start(client, quote(client, 1), "first").json()
    second = start(client, quote(client, 1), "second").json()
    task = asyncio.create_task(Worker(client.app.state.service, PausedDiscovery()).tick())
    await began.wait()
    try:
        assert await Worker(client.app.state.service, FixtureIO()).tick() is False
        assert client.get("/runs/" + second["id"], headers=auth(second)).json()["phase"] == "queued"
    finally:
        release.set()
        await task
    assert client.get("/runs/" + first["id"], headers=auth(first)).json()["phase"] == "completed"
    assert await Worker(client.app.state.service, FixtureIO()).tick() is True


@pytest.mark.asyncio
async def test_restart_blocks_interrupted_repair_without_changing_completed_baseline(tmp_path):
    import asyncio
    from contextlib import suppress

    client = TestClient(
        create_app(
            Settings(
                db_path=tmp_path / "runs.sqlite",
                owner_token="test-owner",
                gates=(),
                targets=(
                    Target(
                        url="http://127.0.0.1:3100",
                        revision="broken",
                        fault="paid-no-access",
                        repo_path=str(tmp_path),
                    ),
                ),
            )
        )
    )
    service = client.app.state.service
    before = start(client, quote(client, 1)).json()
    await Worker(service, FixtureIO()).tick()
    finding = client.get("/runs/" + before["id"] + "/findings", headers=auth(before)).json()["findings"][0]
    client.post(
        "/runs/" + before["id"] + "/repairs",
        headers={"Authorization": "Bearer test-owner"},
        json={"finding_id": finding["id"]},
    )
    with service.store.transaction() as db:
        db.execute("UPDATE jobs SET status='running' WHERE run_id=? AND kind='repair'", (before["id"],))
    task = asyncio.create_task(Worker(service, FixtureIO()).serve())
    try:
        await asyncio.sleep(0.01)
        result = client.get("/runs/" + before["id"], headers=auth(before)).json()
        assert result["phase"] == "completed" and result["counts"]["failed"] == 1
        assert (
            result["repair"]["status"] == "blocked" and result["repair"]["gates"][0]["code"] == "interrupted"
        )
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_owner_can_reconcile_saved_repair_without_new_debit_or_population_and_cancel_still_wins(
    tmp_path,
):
    settings = Settings(
        db_path=tmp_path / "runs.sqlite",
        owner_token="test-owner",
        gates=(),
        targets=(
            Target(
                url="http://127.0.0.1:3100",
                revision="broken-sha",
                fault="paid-no-access",
                repo_path=str(tmp_path),
            ),
        ),
    )
    app = create_app(settings)
    client = TestClient(app)
    service = app.state.service
    owner = {"Authorization": "Bearer test-owner"}

    class CaptureIO(FixtureIO):
        async def repair(self, run, emit):
            raise RuntimeError("External Entire fixture captured then timed out")

        async def recover_repair(self, run, emit):
            service.register_repair(
                run["repair"]["id"],
                head_revision="fixture-reconciled-head",
                preview_url="http://127.0.0.1:3102",
                checkpoint_id="fixture-captured-id",
                checkpoint_url=None,
                provider="local-fixture",
            )
            await emit("repair_capture_recovered", {"external_io_fixture": True})

    io = CaptureIO()
    worker = Worker(service, io)
    before = start(client, quote(client, 1)).json()
    await worker.tick()
    original = client.get(f"/runs/{before['id']}", headers=owner).json()
    finding = client.get(f"/runs/{before['id']}/findings", headers=owner).json()["findings"][0]
    client.post(f"/runs/{before['id']}/repairs", headers=owner, json={"finding_id": finding["id"]})
    await worker.tick()
    blocked = client.get(f"/runs/{before['id']}", headers=owner).json()
    assert blocked["budget"]["estimated_consumed_microusd"] == 60_000
    assert blocked["repair"]["status"] == "blocked"
    path = f"/runs/{before['id']}/repairs/recover"
    assert client.post(path, headers=auth(before)).status_code == 403
    requested = client.post(path, headers=owner)
    assert requested.status_code == 202, requested.text
    assert client.post(path, headers=owner).json()["id"] == requested.json()["id"]
    await worker.tick()
    recovered = client.get(f"/runs/{before['id']}", headers=owner).json()
    assert recovered["repair"]["status"] == "completed"
    assert recovered["repair"]["resolved_gates"] == blocked["repair"]["gates"]
    assert recovered["budget"] == blocked["budget"]
    assert recovered["counts"] == original["counts"] and recovered["personas"] == original["personas"]
    assert recovered["population_id"] == original["population_id"] and io.discovery_calls == 1
    assert recovered["budget"]["unused_microusd"] == 30_000
    assert client.post(f"/runs/{before['id']}/cancel", headers=owner).status_code == 200
    assert client.post(path, headers=owner).status_code == 409
