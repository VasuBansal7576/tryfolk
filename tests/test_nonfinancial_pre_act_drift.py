"""Invented documents only: exercise the real adapter dispatch before SDK execution."""

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from tryfolk.browser_observation import same_document
from tryfolk.browser_policy import BrowserPolicy, DODO_CHECKOUT, exact_origin
from tryfolk.browser_settlement import DOCUMENT_STATE


def preparation():
    source = Path(__file__).parents[1] / "integrations/stagehand/adapter.py"
    tree = ast.parse(source.read_text())
    drift = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PreActDocumentDrift")
    act = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If) and ast.dump(n.test) == ast.dump(ast.parse('op == "act"', mode="eval").body)
    )
    stop = next(
        i
        for i, n in enumerate(act.body)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "fresh_actions" for t in n.targets)
    )
    wrapper = ast.parse(
        "async def prepare(page, policy, command, fresh_document, fresh_actions, guard, control_state, financial_started=False):\n    pass\n"
    )
    fn = wrapper.body[0]
    fn.body = (
        ast.parse("boundary = {}\nnetwork_page = None").body
        + act.body[:stop]
        + ast.parse("return action").body
    )
    namespace = {
        "DOCUMENT_STATE": DOCUMENT_STATE,
        "same_document": same_document,
        "DODO_CHECKOUT": DODO_CHECKOUT,
        "exact_origin": exact_origin,
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[drift, fn], type_ignores=[])), str(source), "exec"
        ),
        namespace,
    )
    return namespace["prepare"], namespace["PreActDocumentDrift"]


def fixture(field="address"):
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    doc = {
        "url": DODO_CHECKOUT + "/session/diagnostic-only",
        "generation": 100,
        "control_signature": "before",
        "body_present": True,
        "ready_state": "complete",
        "busy": False,
    }
    autocomplete = {
        "address": "address-line1",
        "city": "address-level2",
        "state": "address-level1",
        "postal": "postal-code",
        "card_name": "cc-name",
    }[field]
    control = {
        "present": True,
        "visible": True,
        "disabled": False,
        "tag": "INPUT",
        "type": "text",
        "autocomplete": autocomplete,
        "label": field,
        "destination": doc["url"],
    }
    action = policy.bind(
        {"selector": "#old-" + field, "method": "fill", "arguments": []}, control, doc["url"]
    )
    return policy, doc, control, action


class Page:
    def __init__(self, document):
        self.document = document

    async def url(self):
        return self.document["url"]

    async def evaluate(self, expression):
        return deepcopy(self.document)


@pytest.mark.parametrize("field", ["address", "city", "state", "postal"])
async def test_stale_address_layout_never_executes_and_fresh_replacement_executes_once(field):
    prepare, Drift = preparation()
    policy, observed, control, action = fixture(field)
    current = {**observed, "control_signature": "after delayed layout update"}
    sdk_calls = []

    async def read(*args):
        return deepcopy(control)

    async def execute(candidate, document):
        validated = await prepare(
            Page(current),
            policy,
            {"action": candidate},
            document,
            [candidate],
            SimpleNamespace(card_attempt=None),
            read,
        )
        sdk_calls.append(validated)

    with pytest.raises(RuntimeError) as caught:
        await execute(action, observed)
    assert sdk_calls == []
    assert caught.value.code == "nonfinancial_control_stale"
    assert caught.value.data == {
        "same_url": True,
        "same_generation": True,
        "same_control_signature": False,
        "nonfinancial": True,
        "executed": False,
        "field": field,
    }
    replacement = {**action, "selector": "#fresh-" + field}
    await execute(replacement, current)
    assert sdk_calls == [replacement]


@pytest.mark.parametrize("dimension", ["url", "generation"])
async def test_new_document_remains_terminal_with_closed_comparison(dimension):
    prepare, Drift = preparation()
    policy, observed, control, action = fixture()
    current = {
        **observed,
        "control_signature": "after",
        dimension: (observed[dimension] + "-other" if dimension == "url" else 101),
    }

    async def read(*args):
        return control

    with pytest.raises(RuntimeError) as caught:
        await prepare(
            Page(current),
            policy,
            {"action": action},
            observed,
            [action],
            SimpleNamespace(card_attempt=None),
            read,
        )
    assert getattr(caught.value, "code", None) is None
    assert caught.value.data["same_" + dimension] is False
    assert caught.value.data["executed"] is False
    assert caught.value.data["nonfinancial"] is False
    assert "field" not in caught.value.data


@pytest.mark.parametrize("kind", ["pay", "confirm", "card_name"])
async def test_financial_and_card_actions_never_request_reobservation(kind):
    prepare, Drift = preparation()
    policy, observed, control, action = fixture("card_name" if kind == "card_name" else "address")
    if kind != "card_name":
        action = {
            "selector": "#financial",
            "method": "click",
            "arguments": [],
            "_tryfolk": {"url": observed["url"], "kind": kind},
        }

    async def read(*args):
        return control

    with pytest.raises(RuntimeError) as caught:
        await prepare(
            Page({**observed, "control_signature": "after"}),
            policy,
            {"action": action},
            observed,
            [action],
            SimpleNamespace(card_attempt=None),
            read,
        )
    assert getattr(caught.value, "code", None) is None
    assert caught.value.data["nonfinancial"] is False
    assert caught.value.data["executed"] is False


@pytest.mark.parametrize("invalid", ["not_observed", "wrong_value", "hidden", "armed", "busy", "no_body"])
async def test_untrusted_or_unsafe_current_control_is_not_retryable(invalid):
    prepare, Drift = preparation()
    policy, observed, control, action = fixture()
    current = {**observed, "control_signature": "after"}
    fresh = [deepcopy(action)]
    attempt = None
    if invalid == "not_observed":
        fresh = []
    if invalid == "wrong_value":
        action = {**action, "arguments": ["unapproved value"]}
        fresh = [action]
    if invalid == "hidden":
        control = {**control, "visible": False}
    if invalid == "armed":
        attempt = SimpleNamespace(armed_at=1, confirmed=False)
    if invalid == "busy":
        current["busy"] = True
    if invalid == "no_body":
        current["body_present"] = False

    async def read(*args):
        return control

    with pytest.raises(RuntimeError) as caught:
        await prepare(
            Page(current),
            policy,
            {"action": action},
            observed,
            fresh,
            SimpleNamespace(card_attempt=attempt),
            read,
        )
    assert getattr(caught.value, "code", None) is None
    assert caught.value.data["nonfinancial"] is False
