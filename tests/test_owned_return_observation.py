"""Invented browser/provider inputs; these tests are never genuine Dodo evidence."""

import ast
from pathlib import Path

import pytest
from test_owned_download_navigation import Route, navigation, owned_fixture

from tryfolk.browser_observation import observe_fresh, same_document
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.browser_settlement import DOCUMENT_STATE


async def test_owned_return_exposes_both_equivalent_art_pack_links_without_preference_authority():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = policy.origin + "/checkout/return"
    document = {
        "url": url,
        "generation": 1,
        "control_signature": "fixture",
        "ready_state": "complete",
        "body_present": True,
    }

    class Page:
        async def evaluate(self, expression):
            return dict(document) if expression == DOCUMENT_STATE else "Fixture return"

    actions = [
        {"selector": selector, "method": "click", "arguments": []} for selector in ("#global", "#return-cta")
    ]
    controls = {
        "#global": {"label": "Special Downloads", "in_main": False},
        "#return-cta": {"label": "View your art pack", "in_main": True},
    }

    async def infer():
        return actions

    async def read_control(selector):
        return {
            "present": True,
            "visible": True,
            "disabled": False,
            "tag": "A",
            "destination": policy.origin + "/special-downloads",
            **controls[selector],
        }

    result = await observe_fresh(Page(), policy, infer, read_control)
    art_pack = [a for a in result["actions"] if a["_tryfolk"]["kind"] == "art_pack"]
    assert [a["selector"] for a in art_pack] == ["#global", "#return-cta"]
    assert len(result["actions"]) == 2
    assert result["freshness"]["status"] == "fresh"


@pytest.mark.parametrize(
    "changes",
    [
        {"tag": "BUTTON"},
        {"destination": "http://127.0.0.1:3300/special-downloads?other=1"},
        {"destination": "http://127.0.0.1:3300/special-downloads#other"},
    ],
)
def test_return_navigation_retains_anchor_and_exact_destination_checks(changes):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    control = {
        "present": True,
        "visible": True,
        "disabled": False,
        "tag": "A",
        "label": "View your art pack",
        "in_main": True,
        "destination": policy.origin + "/special-downloads",
    }
    action = {"selector": "#return-cta", "method": "click", "arguments": []}
    original = policy.bind(action, control, policy.origin + "/checkout/return")
    rebound = policy.bind(action, control | changes, policy.origin + "/checkout/return")
    assert original["_tryfolk"]["kind"] == "art_pack"
    assert rebound["_tryfolk"]["kind"] == "click"
    assert rebound != original  # Existing pre-act equality rejects the changed control.


@pytest.mark.parametrize(
    "changes",
    [
        {"in_main": False},
        {"in_main": None},
        {"label": "Special Downloads"},
        {"label": "View another art pack"},
    ],
)
def test_equivalent_owned_destination_does_not_gain_authority_from_scope_or_label(changes):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    control = {
        "present": True,
        "visible": True,
        "disabled": False,
        "tag": "A",
        "label": "View your art pack",
        "in_main": True,
        "destination": policy.origin + "/special-downloads",
    }
    bound = policy.bind(
        {"selector": "#observed", "method": "click"}, control | changes, policy.origin + "/checkout/return"
    )
    assert bound["_tryfolk"]["kind"] == "art_pack"
    assert bound["_tryfolk"]["capability"] is False


@pytest.mark.parametrize("label", ["View your art pack", "Return to art pack"])
def test_both_owned_source_return_cta_states_bind_without_requiring_an_id(label):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    action = policy.bind(
        {"selector": "xpath=/html[1]/body[1]/main[1]/a[1]", "method": "click"},
        {"tag": "A", "in_main": True, "label": label, "destination": policy.origin + "/special-downloads"},
        policy.origin + "/checkout/return",
    )
    assert action["_tryfolk"]["kind"] == "art_pack"
    assert action["_tryfolk"]["capability"] is False


def test_foreign_or_unavailable_return_cta_never_binds():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    control = {
        "tag": "A",
        "in_main": True,
        "label": "View your art pack",
        "destination": policy.origin + "/special-downloads",
    }
    for changes in (
        {"destination": "https://unowned.invalid/special-downloads"},
        {"present": False},
        {"visible": False},
        {"disabled": True},
    ):
        with pytest.raises(ValueError):
            policy.bind(
                {"selector": "#return-cta", "method": "click"},
                control | changes,
                policy.origin + "/checkout/return",
            )


def adapter_preparation():
    """Run the adapter's original act branch only through navigation preparation.

    The executable adapter launches SDKs at import. Extract this source prefix
    without rewriting it so the caller regression uses its actual dispatch and
    freshness checks, then stops before any Stagehand action or Pay operation.
    """
    source = Path(__file__).resolve().parents[1] / "integrations/stagehand/adapter.py"
    tree = ast.parse(source.read_text(), filename=str(source))
    branches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.dump(node.test) == ast.dump(ast.parse('op == "act"', mode="eval").body)
    ]
    assert len(branches) == 1
    body = branches[0].body
    preparation = [
        index
        for index, statement in enumerate(body)
        if any(
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == "guard"
            and node.value.func.attr == "prepare_owned_navigation"
            for node in ast.walk(statement)
        )
    ]
    assert len(preparation) == 1
    wrapper = ast.parse(
        "async def prepare(page, policy, command, boundary, fresh_document, "
        "fresh_actions, guard, network_page, control_state):\n    pass\n"
    )
    wrapper.body[0].body = body[: preparation[0] + 1]
    namespace = {"DOCUMENT_STATE": DOCUMENT_STATE, "same_document": same_document}
    exec(compile(ast.fix_missing_locations(wrapper), str(source), "exec"), namespace)
    return namespace["prepare"]


def observed_return(guard, network_page, *, label, in_main):
    document = {
        "url": network_page.main_frame.url,
        "generation": 4000,
        "control_signature": "fixture",
        "ready_state": "complete",
        "body_present": True,
    }
    control = {
        "present": True,
        "visible": True,
        "disabled": False,
        "tag": "A",
        "label": label,
        "in_main": in_main,
        "destination": guard.policy.origin + "/special-downloads",
    }
    action = guard.policy.bind(
        {"selector": "#observed-return-link", "method": "click"}, control, document["url"]
    )

    class Page:
        async def url(self):
            return document["url"]

        async def evaluate(self, expression):
            assert expression == DOCUMENT_STATE
            return dict(document)

    async def control_state(_page, selector, _network_page, _guard):
        assert selector == action["selector"]
        return dict(control)

    return Page(), document, control, action, control_state


@pytest.mark.parametrize(
    ("label", "in_main"),
    [("Special Downloads", False), ("View your art pack", True), ("Return to art pack", True)],
)
async def test_actual_adapter_prepares_equivalent_return_links_before_guarded_get(
    monkeypatch, label, in_main
):
    guard, network_page, _state, _identity, _session, calls = await owned_fixture(monkeypatch)
    page, document, control, action, control_state = observed_return(
        guard, network_page, label=label, in_main=in_main
    )
    boundary = {}
    await adapter_preparation()(
        page,
        guard.policy,
        {"action": action},
        boundary,
        dict(document),
        [action],
        guard,
        network_page,
        control_state,
    )
    route = Route(navigation(network_page, control["destination"]))
    await guard.route(route)
    assert boundary["call"] == "owned_navigation.prepare"
    assert route.continued and not route.aborted
    assert calls == ["poll"]


@pytest.mark.parametrize(
    "change",
    ["missing", "hidden", "disabled", "foreign", "query", "tag", "generation", "signature", "unobserved"],
)
async def test_actual_adapter_rejects_changed_or_stale_return_controls_before_preparation(
    monkeypatch, change
):
    guard, network_page, _state, _identity, _session, calls = await owned_fixture(monkeypatch)
    page, document, control, action, control_state = observed_return(
        guard, network_page, label="Special Downloads", in_main=False
    )
    fresh_document, fresh_actions = dict(document), [action]
    destination = control["destination"]
    if change in ("missing", "hidden", "disabled"):
        control[{"missing": "present", "hidden": "visible", "disabled": "disabled"}[change]] = (
            change == "disabled"
        )
    elif change == "foreign":
        control["destination"] = "https://unowned.invalid/special-downloads"
    elif change == "query":
        control["destination"] += "?other=1"
    elif change == "tag":
        control["tag"] = "BUTTON"
    elif change == "generation":
        document["generation"] += 1
    elif change == "signature":
        document["control_signature"] = "changed"
    else:
        fresh_actions = []
    with pytest.raises((RuntimeError, ValueError)):
        await adapter_preparation()(
            page,
            guard.policy,
            {"action": action},
            {},
            fresh_document,
            fresh_actions,
            guard,
            network_page,
            control_state,
        )
    assert calls == []
    route = Route(navigation(network_page, destination))
    await guard.route(route)
    assert route.aborted and not route.continued
