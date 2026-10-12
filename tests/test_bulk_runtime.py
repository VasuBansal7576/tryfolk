"""Invented external I/O fixtures; these checks do not establish provider/model success."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from tryfolk.api import create_app
from tryfolk.config import Settings, Target
from tryfolk.worker import Worker
from test_api import quote, start, auth
from test_worker import FixtureIO


@pytest.mark.parametrize('value', [0, 11, True, '2'])
def test_browser_capacity_is_bounded(value):
    with pytest.raises(ValueError):
        Settings(browser_concurrency=value)


async def test_real_worker_runs_two_distinct_personas_and_persists_each_actual_reply(tmp_path):
    class IO(FixtureIO):
        journey_concurrency = 2
        active = maximum = 0
        async def journey(self, run, persona, cancelled, emit):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            self.accounts.append((run['id'], persona['id']))
            if len(self.accounts) < 2:
                await asyncio.sleep(.03)
            await asyncio.sleep(.005)
            self.active -= 1
            return {'outcome': 'pending', 'classification': 'external_fixture_pending', 'measured': False}
    app = create_app(Settings(db_path=tmp_path/'db', gates=(), targets=(Target(url='http://127.0.0.1:3100', revision='broken-sha'),)))
    client = TestClient(app)
    run = start(client, quote(client, 5)).json()
    io = IO()
    await Worker(app.state.service, io).tick()
    result = client.get('/runs/'+run['id'], headers=auth(run)).json()
    assert len(set(io.accounts)) == 5 and io.maximum == 2
    assert result['phase'] == 'completed' and result['counts']['pending'] == 5
    assert result['counts']['requested'] == 5
    assert result['budget']['estimated_consumed_microusd'] == 120_000


async def test_parallel_failure_drains_peers_and_keeps_requested_n(tmp_path):
    stopped = asyncio.Event()
    class IO(FixtureIO):
        journey_concurrency = 2
        async def journey(self, run, persona, cancelled, emit):
            self.accounts.append((run['id'], persona['id']))
            if persona['id'] == 'p0':
                await asyncio.sleep(.02)
                raise RuntimeError('invented SDK failure')
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
    app = create_app(Settings(db_path=tmp_path/'db', gates=(), targets=(Target(url='http://127.0.0.1:3100', revision='broken-sha'),)))
    client = TestClient(app)
    run = start(client, quote(client, 5)).json()
    io = IO()
    await Worker(app.state.service, io).tick()
    result = client.get('/runs/'+run['id'], headers=auth(run)).json()
    assert stopped.is_set() and len(io.accounts) == 2
    assert result['phase'] == 'blocked' and result['counts']['requested'] == 5
    assert result['counts']['executed'] == 0
