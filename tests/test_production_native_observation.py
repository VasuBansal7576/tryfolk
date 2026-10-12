"""Invented SDK document/frame reads only; no provider, browser, model or payments."""

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tryfolk.browser_policy import DODO_CHECKOUT, DODO_SECURE_FRAME, exact_origin
from tryfolk.browser_settlement import DOCUMENT_STATE
from tryfolk.native_card_observation import READ_CONTEXT, native_configuration_facts
from tryfolk.native_read_observation import NativeObservationGate, read_current_native_controls


def helper():
    source = Path(__file__).parents[1] / "integrations/stagehand/adapter.py"
    tree = ast.parse(source.read_text())
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "read_production_native_controls"
    )
    ns = dict(
        asyncio=asyncio,
        DOCUMENT_STATE=DOCUMENT_STATE,
        READ_CONTEXT=READ_CONTEXT,
        exact_origin=exact_origin,
        DODO_CHECKOUT=DODO_CHECKOUT,
        NativeObservationGate=NativeObservationGate,
        native_configuration_facts=native_configuration_facts,
        read_current_native_controls=read_current_native_controls,
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), ns)
    return ns["read_production_native_controls"]


class Fixture:
    def __init__(self):
        self.document = dict(
            url=DODO_CHECKOUT + "/:diagnostic",
            generation=100,
            control_signature="current",
            ready_state="complete",
            body_present=True,
            busy=False,
        )
        self.page = SimpleNamespace(evaluate=self.evaluate)
        self.network = SimpleNamespace(url=self.document["url"], main_frame=object(), frames=[])
        self.frame = SimpleNamespace(
            url=DODO_SECURE_FRAME + "/:diagnostic",
            parent_frame=self.network.main_frame,
            evaluate=self.child_read,
        )
        # Playwright main_frame is an object with its current URL.
        self.network.main_frame = SimpleNamespace(url=self.network.url)
        self.frame.parent_frame = self.network.main_frame
        self.context = dict(
            approved=True,
            config_id="diagnostic-config",
            payment_id="diagnostic-payment",
            checkout_session_id="diagnostic-checkout",
        )
        self.observed = dict(
            observer_available=True,
            generation=200,
            native=dict(
                config_bound=True,
                generation=200,
                parent_source_bound=True,
                return_source="INIT",
                return_field_present=False,
            ),
        )
        self.identity = dict(
            approved=True,
            provider_environment="test_mode",
            payment_correlated=True,
            customer_correlated=True,
            payment_id="diagnostic-payment",
            checkout_session_id="diagnostic-checkout",
        )
        self.ready = True
        self.already_generated = False
        self.reads = self.snapshots = self.verifies = 0
        self.events = []
        self.guard = SimpleNamespace(
            policy=SimpleNamespace(origin="http://127.0.0.1:3301", check_url=self.check_url),
            frame_context=lambda url, parent: self.context,
            frame_verify=self.verify,
            native_setup_facts=self.setup,
        )

    def check_url(self, url):
        assert exact_origin(url) == DODO_CHECKOUT

    async def evaluate(self, script):
        return deepcopy(self.document) if script == DOCUMENT_STATE else self.already_generated

    async def child_read(self, script):
        assert script == READ_CONTEXT
        self.reads += 1
        return deepcopy(self.observed)

    async def verify(self, context):
        assert context == self.context
        self.verifies += 1
        return deepcopy(self.identity)

    def setup(self, config, generation):
        assert config == "diagnostic-config"
        return {
            k: self.ready
            for k in (
                "bootstrap_account_methods_200",
                "bootstrap_customer_methods_200",
                "normal_type_observed",
            )
        }

    async def snapshot(self):
        self.snapshots += 1
        return [{"selector": "invented-card-selector", "method": "fill"}]

    async def run(self, **kw):
        return await helper()(
            self.page,
            self.network,
            self.guard,
            kw.pop("read_controls", self.snapshot),
            original_document=kw.pop("original_document", deepcopy(self.document)),
            completed_cards=kw.pop("completed_cards", {}),
            deadline=kw.pop("deadline", asyncio.get_running_loop().time() + 0.4),
            on_event=self.events.append,
            **kw,
        )


async def test_continue_waits_for_actual_child_and_bootstrap_without_empty_persona_observation():
    f = Fixture()
    f.ready = False

    async def hydrate():
        await asyncio.sleep(0.01)
        f.network.frames = [f.frame]
        await asyncio.sleep(0.11)
        f.ready = True

    task = asyncio.create_task(hydrate())
    result = await f.run()
    await task
    assert result == [{"selector": "invented-card-selector", "method": "fill"}]
    assert f.snapshots == 1
    assert any(e.get("checks", {}).get("bootstrap_readiness_current") is False for e in f.events)


async def test_explicit_already_generated_error_stops_before_snapshot_or_identity_call():
    f = Fixture()
    f.already_generated = True
    with pytest.raises(NativeObservationGate, match="hosted_checkout_session_already_generated"):
        await f.run()
    assert f.snapshots == f.verifies == 0
    assert f.events == [{"decision": "deny_hosted_checkout_session_already_generated"}]


@pytest.mark.parametrize("dimension", ["url", "generation"])
async def test_parent_binding_change_is_terminal_without_snapshot(dimension):
    f = Fixture()
    original = deepcopy(f.document)
    f.document[dimension] = f.document[dimension] + "/changed" if dimension == "url" else 101
    with pytest.raises(NativeObservationGate, match="native_parent_document_changed"):
        await f.run(original_document=original)
    assert f.snapshots == f.verifies == 0


@pytest.mark.parametrize("failure", ["customer", "payment", "checkout", "bootstrap", "observer"])
async def test_missing_native_predicate_cannot_expose_card_actions(failure):
    f = Fixture()
    f.network.frames = [f.frame]
    if failure == "customer":
        f.identity["customer_correlated"] = False
    if failure == "payment":
        f.identity["payment_id"] = "other"
    if failure == "checkout":
        f.identity["checkout_session_id"] = "other"
    if failure == "bootstrap":
        f.ready = False
    if failure == "observer":
        f.observed["observer_available"] = False
    with pytest.raises(NativeObservationGate, match="original_deadline_exhausted"):
        await f.run(deadline=asyncio.get_running_loop().time() + 0.01)
    assert f.snapshots == 0


async def test_changed_child_after_card_completion_is_terminal_without_new_snapshot():
    f = Fixture()
    f.network.frames = [f.frame]
    with pytest.raises(NativeObservationGate, match="native_changed_after_executed_card"):
        await f.run(completed_cards={"card_number": ("diagnostic-config", 199)})
    assert f.snapshots == 0


async def test_snapshot_child_drift_discards_unexecuted_candidates_and_returns_fresh_read_once():
    f = Fixture()
    f.network.frames = [f.frame]

    async def drifting_read():
        f.snapshots += 1
        if f.snapshots == 1:
            f.observed["generation"] = f.observed["native"]["generation"] = 201
            return ["stale-unexecuted"]
        return ["fresh-unexecuted"]

    assert await f.run(read_controls=drifting_read) == ["fresh-unexecuted"]
    assert f.snapshots == 2
    assert any(e.get("decision") == "discard_changed_native_state" for e in f.events)


async def test_snapshot_drift_after_card_execution_is_never_retried():
    f = Fixture()
    f.network.frames = [f.frame]

    async def drifting_read():
        f.snapshots += 1
        f.observed["generation"] = f.observed["native"]["generation"] = 201
        return ["stale-unexecuted"]

    with pytest.raises(NativeObservationGate, match="native_changed_after_executed_card"):
        await f.run(read_controls=drifting_read, completed_cards={"card_number": ("diagnostic-config", 200)})
    assert f.snapshots == 1


async def test_multiple_current_children_are_terminal():
    f = Fixture()
    f.network.frames = [f.frame, f.frame]
    with pytest.raises(NativeObservationGate, match="single_current_native_child"):
        await f.run()
    assert f.snapshots == 0


async def test_ready_child_with_empty_sdk_snapshot_never_reaches_persona_as_empty_actions():
    f = Fixture()
    f.network.frames = [f.frame]

    async def empty_read():
        f.snapshots += 1
        return []

    with pytest.raises(NativeObservationGate, match="original_deadline_exhausted"):
        await f.run(read_controls=empty_read, deadline=asyncio.get_running_loop().time() + 0.02)
    assert f.snapshots == 1
    assert any(e.get("decision") == "native_snapshot_no_executable_controls" for e in f.events)


async def test_no_initial_time_budget_calls_no_snapshot_or_identity():
    f = Fixture()
    with pytest.raises(NativeObservationGate, match="original_deadline_exhausted"):
        await f.run(deadline=asyncio.get_running_loop().time())
    assert f.snapshots == f.verifies == 0
