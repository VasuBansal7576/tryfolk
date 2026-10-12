"""Offline invented reads: impossible native controls never spend provider reads."""

import asyncio
import pytest

from test_production_native_observation import Fixture
from tryfolk.native_read_observation import NativeObservationGate


@pytest.mark.parametrize("not_ready", ["bootstrap", "observer", "init", "configuration", "parent", "generation", "document_busy"])
async def test_locally_impossible_card_readiness_never_calls_independent_provider(not_ready):
    f = Fixture()
    f.network.frames = [f.frame]
    if not_ready == "bootstrap":
        f.ready = False
    elif not_ready == "observer":
        f.observed["observer_available"] = False
    elif not_ready == "init":
        f.observed["native"]["return_source"] = "unobserved"
    elif not_ready == "configuration":
        f.observed["native"]["config_bound"] = False
    elif not_ready == "parent":
        f.observed["native"]["parent_source_bound"] = False
    elif not_ready == "generation":
        f.observed["native"]["generation"] = 201
    else:
        f.document["busy"] = True
    with pytest.raises(NativeObservationGate, match="original_deadline_exhausted"):
        await f.run(deadline=asyncio.get_running_loop().time() + 0.015)
    assert f.verifies == 0
    assert f.snapshots == 0


async def test_ready_snapshot_retains_independent_before_after_checks_and_rechecks_each_observation():
    f = Fixture()
    f.network.frames = [f.frame]
    await f.run()
    first = f.verifies
    assert first == 3
    await f.run()
    assert f.verifies == first * 2
    assert f.snapshots == 2


async def test_not_ready_after_card_completion_is_terminal_without_provider_or_snapshot():
    f = Fixture()
    f.network.frames = [f.frame]
    f.ready = False
    with pytest.raises(NativeObservationGate, match="native_changed_after_executed_card"):
        await f.run(completed_cards={"card_number": ("diagnostic-config", 200)})
    assert f.verifies == f.snapshots == 0
