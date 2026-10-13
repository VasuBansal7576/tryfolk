"""Replay the actual adapter settlement call with offline page I/O, never payments."""

import ast
from pathlib import Path

import pytest


def post_action_settlement():
    path = Path(__file__).parents[1] / "integrations/stagehand/adapter.py"
    node = next(node for node in ast.walk(ast.parse(path.read_text()))
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant) and target.slice.value == "settlement"
                        for target in node.targets))
    function = ast.AsyncFunctionDef(
        name="dispatch", args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                            kw_defaults=[], defaults=[]),
        body=[ast.Return(value=node.value)], decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    return compile(module, str(path), "exec")


@pytest.mark.parametrize("action,expected", [
    ({"method": "click", "_tryfolk": {"kind": "pay"}}, 30),
    ({"method": "click", "_tryfolk": {"kind": "billing_continue"}}, 30),
    ({"method": "click", "_tryfolk": {"kind": "confirm"}}, 10),
    ({"method": "fill", "_tryfolk": {"field": "card_number"}}, 10),
])
async def test_actual_post_action_dispatch_extends_only_acknowledged_pay_and_continue(action, expected):
    from tryfolk.browser_policy import BrowserPolicy

    page = object()
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    original = {"url": "https://test.checkout.dodopayments.com/offline-fixture", "generation": 123}
    calls = []

    async def capture(received_page, received_policy, received_action, *, timeout, previous_document):
        calls.append(timeout)
        assert received_page is page and received_policy is policy and received_action is action
        assert previous_document is original
        return {"observed": True}

    namespace = {"settle": capture, "page": page, "policy": policy,
                 "action": action, "fresh_document": original}
    exec(post_action_settlement(), namespace)
    assert await namespace["dispatch"]() == {"observed": True}
    assert calls == [expected]
