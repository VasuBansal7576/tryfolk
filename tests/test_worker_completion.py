"""Offline fixture lifecycles; independent readers inspect each committed boundary."""

from contextlib import contextmanager
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from tryfolk.config import Settings, Target
from tryfolk.service import Service
from tryfolk.store import Store
from tryfolk.worker import Worker
from test_cfo_budget import finalized_plan
from test_worker import FixtureIO


class ObservedStore(Store):
    """Database-boundary scheduling seam, using real independent SQLite connections."""

    def __init__(self, path):
        self.on_commit = None
        super().__init__(path)

    @contextmanager
    def transaction(self):
        with super().transaction() as db:
            yield db
        if self.on_commit:
            self.on_commit()


class PairIO(FixtureIO):
    def __init__(self, service):
        super().__init__()
        self.service = service

    async def repair(self, run, emit):
        self.service.register_repair(
            run["repair"]["id"], head_revision="fixture-fixed",
            preview_url="http://127.0.0.1:3102", checkpoint_id="fixture-only",
            checkpoint_url=None, provider="local-fixture",
        )

    async def known_replay(self, run, population, emit):
        await emit("known_failure_replay", {"fixture": True, "passed": True})


async def queued_pair(tmp_path):
    plan = tmp_path / "fixture-plan.json"
    plan.write_text(json.dumps(finalized_plan()))
    settings = Settings(
        db_path=tmp_path / "fixture.sqlite", owner_token="fixture-owner", gates=(),
        cfo_plan_path=plan,
        targets=(Target(
            url="http://127.0.0.1:3100", revision="fixture-broken",
            fault="paid-no-access", repo_path=str(tmp_path),
        ),),
    )
    service, reader = Service(settings), Service(settings)
    service.store = ObservedStore(settings.db_path)
    io = PairIO(service)
    worker = Worker(service, io)
    quote = service.quote("http://127.0.0.1:3100", 2)
    # Execution must still use the issued allowances after configuration changes.
    service.settings = replace(settings, per_persona_microusd=100_000)
    before = service.create_run(quote["id"], "fixture-before")
    baseline_reads = []
    service.store.on_commit = lambda: baseline_reads.append(
        reader.get_run(before["id"], before["access_token"])
    )
    await worker.tick()
    baseline = next(view for view in baseline_reads if view["phase"] == "completed")
    service.store.on_commit = None
    finding = service.findings(before["id"], before["access_token"])["findings"][0]
    repair = service.repair(before["id"], finding["id"], "fixture-owner")
    await worker.tick()
    after = service.replay(before["id"], repair["id"], "fixture-after", "fixture-owner")
    return SimpleNamespace(
        service=service, reader=reader, worker=worker, io=io, quote=quote,
        before=before, baseline=baseline, after=after,
    )


@pytest.mark.asyncio
async def test_first_observable_after_completion_includes_comparison_and_released_budget(tmp_path):
    pair = await queued_pair(tmp_path)
    observations = []
    # Read after every writer commit, before the worker can enter its next transaction.
    pair.service.store.on_commit = lambda: observations.append(
        pair.reader.get_run(pair.after["id"], pair.after["access_token"])
    )
    await pair.worker.tick()
    first = next(view for view in observations if view["phase"] == "completed")
    assert first["comparison"] is not None
    assert first["comparison"]["before_run_id"] == pair.before["id"]
    assert first["comparison"]["after_run_id"] == pair.after["id"]
    assert first["comparison"]["population_id"] == pair.baseline["population_id"]
    assert first["comparison"]["requested"] == 2
    assert first["comparison"]["same_personas"] is True
    assert first["comparison"]["before_counts"]["failed"] == 2
    assert first["comparison"]["after_counts"]["completed"] == 2
    assert first["comparison"]["known_failure_replay"] == "recorded"
    assert first["comparison"]["total_spent_microusd"] == 130_000
    assert first["comparison"]["unused_microusd"] == 38_000
    assert first["budget"]["reserved_microusd"] == 168_000
    assert first["budget"]["spent_microusd"] == 130_000
    assert first["budget"]["released_microusd"] == 38_000
    assert first["budget"]["unused_microusd"] == 0
    assert first["budget_contract"] == pair.quote
    assert first["personas"] == pair.baseline["personas"]
    assert first["counts"]["requested"] == first["counts"]["executed"] == 2


@pytest.mark.asyncio
async def test_before_completion_keeps_the_original_protected_retest_allowance(tmp_path):
    pair = await queued_pair(tmp_path)
    assert pair.baseline["phase"] == "completed"
    assert pair.baseline["comparison"] is None
    assert pair.baseline["budget_contract"] == pair.quote
    assert pair.baseline["after_reserve_microusd"] == 60_000
    assert pair.baseline["budget"]["reserved_microusd"] == 168_000
    assert pair.baseline["budget"]["spent_microusd"] == 60_000
    assert pair.baseline["budget"]["unused_microusd"] == 108_000
    assert pair.baseline["budget"]["released_microusd"] == 0
    # Repair used20k, but the full known-replay/N-persona/contingency floor remains protected.
    with pytest.raises(RuntimeError, match="retest reserve protected"):
        pair.worker.debit(pair.before["id"], 28_001)


@pytest.mark.asyncio
async def test_cancellation_after_last_verification_wins_over_terminal_publication(tmp_path):
    pair = await queued_pair(tmp_path)
    observations = []

    def cancel_after_verification():
        view = pair.reader.get_run(pair.after["id"], pair.after["access_token"])
        observations.append(view)
        if view["phase"] == "running" and view["events"][-1]["kind"] == "verification" \
                and view["events"][-1]["persona_id"] == "p1":
            pair.reader.cancel(pair.after["id"], pair.after["access_token"])

    pair.service.store.on_commit = cancel_after_verification
    await pair.worker.tick()
    final = pair.reader.get_run(pair.after["id"], pair.after["access_token"])
    assert final["phase"] == "cancelled"
    assert final["comparison"] is None
    assert final["budget"]["spent_microusd"] == 130_000
    assert final["budget"]["released_microusd"] == 38_000
    assert final["budget"]["unused_microusd"] == 0
    assert not any(view["phase"] == "completed" for view in observations)


@pytest.mark.asyncio
async def test_failed_finalization_does_not_publish_completed_or_release_the_reservation(tmp_path):
    pair = await queued_pair(tmp_path)
    # A real SQLite write failure at the persistence boundary exercises transaction rollback.
    with pair.service.store.transaction() as db:
        db.executescript("""
            CREATE TRIGGER fixture_reject_comparison BEFORE UPDATE ON runs
            WHEN json_extract(NEW.payload, '$.comparison') IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'fixture-only comparison write failure'); END;
        """)
    observations = []
    pair.service.store.on_commit = lambda: observations.append(
        pair.reader.get_run(pair.after["id"], pair.after["access_token"])
    )
    await pair.worker.tick()
    final = pair.reader.get_run(pair.after["id"], pair.after["access_token"])
    assert final["phase"] == "blocked"
    assert final["comparison"] is None
    assert final["budget"]["spent_microusd"] == 130_000
    assert final["budget"]["released_microusd"] == 0
    assert final["budget"]["unused_microusd"] == 38_000
    assert not any(view["phase"] == "completed" for view in observations)
