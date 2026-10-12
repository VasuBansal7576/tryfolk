"""Invented source/identity reads: acknowledged Continue settles before native binding."""

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_checkout_navigation import captured_navigation, document, ENTRY, LINK
from test_checkout_normalization import normalized_document, SOURCE
from tryfolk.browser_policy import DODO_CHECKOUT, exact_origin
from tryfolk.native_read_observation import NativeObservationGate


def helper():
    p = Path(__file__).parents[1] / "integrations/stagehand/adapter.py"
    fn = next(
        n
        for n in ast.parse(p.read_text()).body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "settle_owned_native_parent"
    )
    ns = dict(
        asyncio=asyncio,
        DODO_CHECKOUT=DODO_CHECKOUT,
        exact_origin=exact_origin,
        NativeObservationGate=NativeObservationGate,
    )
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(p), "exec"), ns)
    return ns["settle_owned_native_parent"]


class Fixture:
    def __init__(self):
        self.nav = captured_navigation()
        self.nav.observe_source(SOURCE, document())
        self.doc = document(ENTRY) | {"busy": False}
        self.main = SimpleNamespace(url=ENTRY)
        self.child = SimpleNamespace(
            url="https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html?fixture",
            parent_frame=self.main,
        )
        self.network = SimpleNamespace(main_frame=self.main, frames=[])
        self.context = dict(approved=True, payment_id="diagnostic-payment", checkout_session_id="cks_fixture")
        self.identity = dict(
            approved=True,
            provider_environment="test_mode",
            payment_correlated=True,
            customer_correlated=True,
            payment_id="diagnostic-payment",
            checkout_session_id="cks_fixture",
            payment_link=LINK,
        )
        self.events = []
        self.guard = SimpleNamespace(
            checkout_navigation=self.nav,
            card_attempt=None,
            policy=SimpleNamespace(check_url=lambda _: None),
            parent_document=self.read,
            read_parent=self.read_error,
            frame_context=lambda *_: self.context,
            frame_verify=self.verify,
            owned_checkout_checks=lambda identity: {"current_owned_order": True},
        )

    async def read(self):
        return deepcopy(self.doc)

    async def read_error(self, _):
        return False

    async def verify(self, _):
        return deepcopy(self.identity)

    async def run(self, timeout=0.3, completed_cards=None):
        return await helper()(
            self.network,
            self.guard,
            original_document=document(),
            completed_cards=completed_cards or {},
            deadline=asyncio.get_running_loop().time() + timeout,
            on_event=self.events.append,
        )

    def ready(self):
        self.doc = normalized_document() | {"busy": False}
        self.main.url = self.doc["url"]
        self.network.frames = [self.child]


async def test_acknowledged_continue_can_finish_exact_owned_alias_normalization_before_native_binding():
    f = Fixture()

    async def arrive():
        await asyncio.sleep(0.02)
        f.ready()

    task = asyncio.create_task(arrive())
    result = await f.run()
    await task
    assert result["document"]["url"] == f.doc["url"]
    assert all(result["checks"].values())
    assert "_normalization" not in result["document"]
    assert all(type(v) is bool for v in result["checks"].values())


@pytest.mark.parametrize(
    "failure",
    [
        "foreign_origin",
        "foreign_alias",
        "generation",
        "customer",
        "checkout",
        "payment",
        "independent_link",
        "source",
        "storage",
        "owned_order",
    ],
)
async def test_unapproved_parent_or_identity_never_rebinds_native_document(failure):
    f = Fixture()
    f.ready()
    if failure == "foreign_origin":
        f.doc["url"] = "https://unapproved.invalid/Private1"
    if failure == "foreign_alias":
        f.doc["url"] = DODO_CHECKOUT + "/Other111?session=sess_Abc123xyZ9"
    if failure == "generation":
        f.doc["generation"] = 2000
    if failure == "customer":
        f.identity["customer_correlated"] = False
    if failure == "checkout":
        f.identity["checkout_session_id"] = "other"
    if failure == "payment":
        f.identity["payment_id"] = "other"
    if failure == "independent_link":
        f.identity["payment_link"] = DODO_CHECKOUT + "/Other111"
    if failure == "source":
        f.nav.source_generation = 999
    if failure == "storage":
        f.doc["_normalization"]["storage_value"] = "{}"
    if failure == "owned_order":
        f.guard.owned_checkout_checks = lambda _: {"current_owned_order": False}
    with pytest.raises(NativeObservationGate):
        await f.run(timeout=0.03)


async def test_card_started_and_multiple_children_never_allow_settlement_rebinding():
    f = Fixture()
    f.ready()
    with pytest.raises(NativeObservationGate, match="native_changed_after_executed_card"):
        await f.run(completed_cards={"card_number": ("diagnostic", 1000)})
    f.network.frames.append(f.child)
    with pytest.raises(NativeObservationGate, match="single_current_native_child"):
        await f.run()


async def test_hosted_error_remains_primary_before_any_native_child_settlement():
    f = Fixture()

    async def error(_):
        return True

    f.guard.read_parent = error
    with pytest.raises(NativeObservationGate, match="hosted_checkout_session_already_generated"):
        await f.run()


async def test_exact_independent_payment_link_still_requires_current_owned_generation():
    f = Fixture()
    f.ready()
    f.doc = document(LINK) | {"busy": False}
    assert all((await f.run())["checks"].values())
    f.doc["generation"] = 2000
    with pytest.raises(NativeObservationGate, match="native_parent_document_changed"):
        await f.run()
