"""Reproduce deferred postal calculate versus Continue with invented passive reads."""

import ast
import asyncio
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit, quote

import pytest
from tryfolk.browser_policy import DODO_CHECKOUT, exact_origin
from tryfolk.browser_settlement import DOCUMENT_STATE
from tryfolk.native_read_observation import NativeObservationGate


def helper():
    p = Path(__file__).parents[1] / "integrations/stagehand/adapter.py"
    tree = ast.parse(p.read_text())
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "wait_for_billing_calculation"
    )
    ns = dict(
        asyncio=asyncio,
        time=time,
        urlsplit=urlsplit,
        quote=quote,
        DOCUMENT_STATE=DOCUMENT_STATE,
        exact_origin=exact_origin,
        DODO_CHECKOUT=DODO_CHECKOUT,
        NativeObservationGate=NativeObservationGate,
    )
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(p), "exec"), ns)
    return ns["wait_for_billing_calculation"]


class Fixture:
    def __init__(self):
        self.doc = dict(
            url=DODO_CHECKOUT + "/:owned-entry",
            generation=100,
            ready_state="complete",
            body_present=True,
            busy=False,
        )
        self.navigation = SimpleNamespace(
            checkout_id="diagnostic-checkout",
            entry_url=self.doc["url"],
            source_generation=100,
            continue_generation=None,
        )
        self.marker = (asyncio.get_running_loop().time() - 0.7, time.time() - 0.7, self.doc["url"], 100)
        self.entries = [self.entry()]
        self.events = []
        self.guard = SimpleNamespace(
            policy=SimpleNamespace(check_url=lambda _: None), checkout_navigation=self.navigation
        )

    def entry(self, **kw):
        return dict(
            id=1,
            method="POST",
            origin=DODO_CHECKOUT,
            path="/api/checkout/sessions/diagnostic-checkout/calculate",
            initiator=DODO_CHECKOUT + "/:owned-entry",
            started_at=time.time() - 0.1,
            status=200,
            completion="finished",
            **kw,
        )

    async def evaluate(self, _):
        return deepcopy(self.doc)

    async def run(self, timeout=0.7):
        return await helper()(
            SimpleNamespace(evaluate=self.evaluate),
            self.guard,
            SimpleNamespace(snapshot=lambda: deepcopy(self.entries)),
            original_document=deepcopy(self.doc),
            last_billing_completion=self.marker,
            deadline=asyncio.get_running_loop().time() + timeout,
            on_event=self.events.append,
        )


async def test_current_calculation_complete_and_quiet_before_continue_can_be_observed():
    f = Fixture()
    checks = await f.run()
    assert all(checks.values())
    assert len(f.entries) == 1


async def test_postal_debounce_and_late_pending_calculation_must_finish_before_continue():
    f = Fixture()
    now = asyncio.get_running_loop().time()
    f.marker = (now - 0.5, time.time() - 0.5, f.doc["url"], 100)
    f.entries = []

    async def deferred_calculate():
        await asyncio.sleep(0.02)
        item = f.entry()
        item.update(completion="pending", status=None)
        f.entries.append(item)
        await asyncio.sleep(0.13)
        item.update(completion="finished", status=200)

    task = asyncio.create_task(deferred_calculate())
    await f.run()
    await task
    assert asyncio.get_running_loop().time() - now >= 0.24
    assert any(e.get("pending_calculation") is True for e in f.events)


@pytest.mark.parametrize("dimension", ["foreign_checkout", "foreign_document", "stale", "wrong_method"])
async def test_unrelated_or_stale_response_never_grants_continue(dimension):
    f = Fixture()
    if dimension == "foreign_checkout":
        f.entries[0]["path"] = "/api/checkout/sessions/other/calculate"
    if dimension == "foreign_document":
        f.entries[0]["initiator"] = DODO_CHECKOUT + "/:other-entry"
    if dimension == "stale":
        f.entries[0]["started_at"] = f.marker[1] - 1
    if dimension == "wrong_method":
        f.entries[0]["method"] = "GET"
    with pytest.raises(NativeObservationGate, match="billing_calculation_deadline"):
        await f.run(timeout=0.03)


@pytest.mark.parametrize("status", [403, 500, None])
async def test_failed_or_unknown_completed_calculation_is_terminal(status):
    f = Fixture()
    f.entries[0]["status"] = status
    with pytest.raises(NativeObservationGate, match="billing_calculation_not_successful"):
        await f.run()


async def test_prior_confirm_is_terminal_and_never_replayed():
    f = Fixture()
    item = f.entry()
    item["path"] = item["path"].replace("/calculate", "/confirm")
    f.entries.append(item)
    with pytest.raises(NativeObservationGate, match="billing_confirm_already_started"):
        await f.run()


@pytest.mark.parametrize(
    "change", ["source_generation", "marker_generation", "document_generation", "origin"]
)
async def test_current_document_and_reviewed_source_binding_remain_required(change):
    f = Fixture()
    if change == "source_generation":
        f.navigation.source_generation = 99
    if change == "marker_generation":
        f.marker = (*f.marker[:3], 99)
    if change == "document_generation":
        f.doc["generation"] = 101
    if change == "origin":
        f.doc["url"] = "https://unapproved.invalid/:owned-entry"
    with pytest.raises(NativeObservationGate):
        await f.run()


async def test_existing_pending_current_calculation_cannot_grant_after_newer_success():
    f = Fixture()
    older = f.entry()
    older.update(id=0, started_at=f.marker[1] - 1, completion="pending", status=None)
    f.entries.insert(0, older)
    with pytest.raises(NativeObservationGate, match="billing_calculation_deadline"):
        await f.run(timeout=0.03)


async def test_parent_generation_changed_during_wait_never_grants_continue():
    f = Fixture()
    f.entries[0].update(completion="pending", status=None)

    async def navigation():
        await asyncio.sleep(0.01)
        f.doc["generation"] = 101

    task = asyncio.create_task(navigation())
    with pytest.raises(NativeObservationGate, match="billing_calculation_document_changed"):
        await f.run()
    await task


async def test_observation_deadline_is_not_extended_by_debounce_wait():
    f = Fixture()
    f.marker = (asyncio.get_running_loop().time(), time.time(), f.doc["url"], 100)
    start = asyncio.get_running_loop().time()
    with pytest.raises(NativeObservationGate, match="billing_calculation_deadline"):
        await f.run(timeout=0.02)
    assert asyncio.get_running_loop().time() - start < 0.1
