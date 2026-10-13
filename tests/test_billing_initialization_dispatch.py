"""Run the actual observe dispatch prefix with invented external document I/O."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from tryfolk.browser_policy import BrowserPolicy, DODO_CHECKOUT, exact_origin
from tryfolk.browser_settlement import DOCUMENT_STATE
from tryfolk.manual_address import wait_for_billing_initialization


def observation_initialization():
    # The executable SDK adapter launches at import. Like the owned-return
    # boundary tests, preserve its actual dispatch prefix unchanged and stop
    # before SDK inference. External page reads alone are invented.
    source = Path(__file__).resolve().parents[1] / "integrations/stagehand/adapter.py"
    tree = ast.parse(source.read_text(), filename=str(source))
    dispatch = ast.parse('op in ("observe", "observe_native")', mode="eval").body
    branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                  and ast.dump(n.test) == ast.dump(dispatch))
    stop = next(i for i, n in enumerate(branch.body)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "infer")
    wrapper = ast.parse("async def initialize(page, policy, billing_initialized_binding, guard):\n    return billing_initialized_binding\n")
    wrapper.body[0].body = branch.body[:stop] + wrapper.body[0].body
    namespace = {"op": "observe", "asyncio": asyncio, "DOCUMENT_STATE": DOCUMENT_STATE,
                 "exact_origin": exact_origin, "DODO_CHECKOUT": DODO_CHECKOUT,
                 "wait_for_billing_initialization": wait_for_billing_initialization}
    exec(compile(ast.fix_missing_locations(wrapper), str(source), "exec"), namespace)
    return namespace["initialize"]


async def test_actual_observer_never_reenters_initial_billing_wait_on_payment_document_transition():
    entry = DODO_CHECKOUT + "/session/invented"
    payment = DODO_CHECKOUT + "/inventedpayment"
    documents = iter([
        {"url": payment, "generation": 10, "hosted_test_mode_visible": False},
        {"url": payment, "generation": 10, "hosted_test_mode_visible": False},
        {"url": payment, "generation": 11, "hosted_test_mode_visible": True},
    ])
    reads = []

    class Page:
        async def evaluate(self, _):
            reads.append(True)
            return next(documents) | {"ready_state": "complete", "body_present": True, "busy": False}

    original = (entry, 10)
    result = await observation_initialization()(Page(), BrowserPolicy("http://127.0.0.1:3301", "dodo"), original,
        SimpleNamespace(checkout_navigation=SimpleNamespace(entry_url=entry, continue_generation=10)))
    assert result == original
    assert len(reads) == 1


async def test_actual_initial_billing_still_waits_for_the_exact_owned_entry_test_mode():
    entry = DODO_CHECKOUT + "/session/invented"
    documents = iter([False, False, True])
    reads = []

    class Page:
        async def evaluate(self, _):
            reads.append(True)
            return {"url": entry, "generation": 10, "hosted_test_mode_visible": next(documents),
                    "ready_state": "complete", "body_present": True, "busy": False}

    guard = SimpleNamespace(checkout_navigation=SimpleNamespace(entry_url=entry, continue_generation=None))
    result = await observation_initialization()(Page(), BrowserPolicy("http://127.0.0.1:3301", "dodo"), None, guard)
    assert result == (entry, 10)
    assert len(reads) == 3


async def test_actual_observer_cannot_enter_initial_billing_wait_after_continue_even_without_saved_marker():
    entry = DODO_CHECKOUT + "/session/invented"
    reads = []

    class Page:
        async def evaluate(self, _):
            reads.append(True)
            return {"url": entry, "generation": 10, "hosted_test_mode_visible": False}

    guard = SimpleNamespace(checkout_navigation=SimpleNamespace(entry_url=entry, continue_generation=10))
    result = await observation_initialization()(Page(), BrowserPolicy("http://127.0.0.1:3301", "dodo"), None, guard)
    assert result is None
    assert len(reads) == 1
