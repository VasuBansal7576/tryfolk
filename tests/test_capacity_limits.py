"""Offline scheduling bounds; these callbacks are not browser capacity proof."""
import asyncio
import pytest

from tryfolk.config import Settings
from tryfolk.bulk_policy import bounded_journeys


@pytest.mark.parametrize("lanes", [5, 10])
async def test_reviewed_five_and_ten_lanes_start_separately_and_persist_each_once(lanes):
    assert Settings(browser_concurrency=lanes).browser_concurrency == lanes
    started, finished, persisted = [], [], []
    ready = asyncio.Event()

    async def execute(persona):
        started.append(persona["persona_id"])
        if len(started) == lanes:
            ready.set()
        await ready.wait()
        finished.append(persona["persona_id"])
        return {"id": persona["persona_id"]}

    async def persist(persona, result):
        assert result["id"] == persona["persona_id"]
        persisted.append(result["id"])

    jobs = [{"persona_id": str(i), "run_id": "offline-run"} for i in range(lanes)]
    await asyncio.wait_for(bounded_journeys(jobs, execute, persist, concurrency=lanes,
                                           reviewed_guard_boundary=True), 1)
    assert len(started) == len(finished) == len(persisted) == lanes
    assert set(persisted) == {str(i) for i in range(lanes)}


@pytest.mark.parametrize("lanes", [0, 11, True, 5.0])
async def test_invalid_lane_count_cannot_execute_any_persona(lanes):
    with pytest.raises(ValueError):
        Settings(browser_concurrency=lanes)
    with pytest.raises(ValueError):
        await bounded_journeys([], None, None, concurrency=lanes, reviewed_guard_boundary=True)


async def test_ten_lanes_still_require_reviewed_provider_boundary():
    with pytest.raises(ValueError, match="reviewed"):
        await bounded_journeys([], None, None, concurrency=10)
