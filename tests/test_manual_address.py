"""Public lifecycle tests; current browser/source I/O below is invented."""

import asyncio
from types import SimpleNamespace
import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.manual_address import ManualAddressGate, resolve_manual_address
from tryfolk.manual_address import wait_for_billing_initialization

URL = "https://test.checkout.dodopayments.com/session/invented"
SOURCE = "594824753cd5c68673c5719ec09a77477991381b992f82f1b2542fc44fda0895"


def observed(*, visible=(), country=True, variant="enter_manually", generation=1, stable=True):
    action = {"method": "click", "selector": "xpath=/html[1]/body[1]/button[1]",
              "arguments": [], "_tryfolk": {"url": URL, "kind": "click",
              "diagnostic_manual": True, "manual_variant": variant}}
    discovery = SimpleNamespace(
        candidates=[(3, action)], observed_fields={"country", "address", *visible},
        observed_completion={"country"} if country else set(),
        observed_manual_variants=set() if {"city", "state", "postal"} <= set(visible) else {variant},
    )
    return discovery, {"url": URL, "generation": generation, "body_present": True,
                       "ready_state": "complete", "busy": False, "document_stable": stable}


async def run(states, *, source=SOURCE, first_success=True):
    actions, events = [], []
    iterator = iter(states)
    current = states[-1]

    async def read():
        nonlocal current
        current = next(iterator, current)
        return current

    async def act(action):
        actions.append(action)
        return first_success

    try:
        result = await resolve_manual_address(
            read, act, BrowserPolicy("http://127.0.0.1:3300", "dodo"),
            source_sha256=source, deadline=asyncio.get_running_loop().time() + 1,
            effect_timeout=0.001, on_event=events.append,
        )
        return result, actions, events
    except ManualAddressGate as error:
        error.actions, error.events = actions, events
        raise


async def test_sdk_success_without_effect_is_not_completion_and_fresh_idempotent_open_is_bounded():
    result, actions, events = await run([
        observed(), observed(), observed(), observed(visible={"city", "state", "postal"}),
    ])
    assert len(actions) == 2
    assert result[0].observed_fields >= {"city", "state", "postal"}
    assert [e["decision"] for e in events if e["decision"].startswith("manual_act")] == [
        "manual_act_succeeded_effect_pending", "manual_act_succeeded_effect_pending",
    ]
    assert events[-1]["decision"] == "manual_effect_verified"


async def test_hydrated_effect_on_first_click_needs_no_second_action():
    _, actions, events = await run([observed(), observed(visible={"city", "state", "postal"})])
    assert len(actions) == 1
    assert events[-1]["decision"] == "manual_effect_verified"


async def test_current_manual_fields_already_visible_require_no_action():
    _, actions, _ = await run([observed(visible={"city", "state", "postal"})])
    assert actions == []


@pytest.mark.parametrize("source,variant", [(None, "enter_manually"), (SOURCE, "enter_address_manually"),
                                           ("0" * 64, "enter_manually")])
async def test_unreviewed_source_or_exact_control_never_repeats_a_successful_click(source, variant):
    with pytest.raises(ManualAddressGate) as stopped:
        await run([observed(variant=variant)], source=source)
    assert stopped.value.reason == "manual_entry_open_unverified"
    assert len(stopped.value.actions) == 1


async def test_second_success_without_visible_effect_stops_at_two_clicks():
    with pytest.raises(ManualAddressGate) as stopped:
        await run([observed()])
    assert stopped.value.reason == "bounded_manual_effect_unobserved"
    assert len(stopped.value.actions) == 2


@pytest.mark.parametrize("state,reason", [
    (observed(generation=2), "manual_document_changed"),
    (observed(stable=False), "manual_document_unstable"),
    (observed(country=False), "manual_country_currently_unverified"),
    (observed(visible={"city"}), "manual_effect_partial"),
])
async def test_changed_generation_initialization_and_partial_manual_state_stop_before_repeated_actions(state, reason):
    with pytest.raises(ManualAddressGate) as stopped:
        await run([observed(), state])
    assert stopped.value.reason == reason
    assert len(stopped.value.actions) == 1


async def test_initial_async_country_not_ready_requires_fresh_selection_before_any_manual_click():
    with pytest.raises(ManualAddressGate) as stopped:
        await run([observed(country=False)])
    assert stopped.value.reason == "manual_country_currently_unverified"
    assert stopped.value.actions == []


async def test_failed_or_ambiguous_sdk_result_never_repeats():
    with pytest.raises(ManualAddressGate) as stopped:
        await run([observed()], first_success=False)
    assert stopped.value.reason == "manual_SDK_not_successful"
    assert len(stopped.value.actions) == 1


async def test_closed_evidence_contains_no_private_actions_controls_or_urls():
    import json
    _, _, events = await run([observed(), observed(visible={"city", "state", "postal"})])
    evidence = json.dumps(events)
    assert all(private not in evidence for private in (URL, "xpath=", "button[", SOURCE))


async def test_country_initialization_waits_for_passively_observed_test_mode_after_async_default_change():
    documents = iter([
        observed()[1] | {"hosted_test_mode_visible": False},
        observed(country=False)[1] | {"hosted_test_mode_visible": False},
        observed(country=False)[1] | {"hosted_test_mode_visible": True},
    ])
    actions = []

    async def read():
        actions.append("read")
        return next(documents)

    result = await wait_for_billing_initialization(
        read, BrowserPolicy("http://127.0.0.1:3300", "dodo"),
        deadline=asyncio.get_running_loop().time() + 1,
    )
    assert result["hosted_test_mode_visible"] is True
    assert actions == ["read"] * 3


async def test_already_executed_sdk_manual_action_cannot_be_repeated_without_current_source():
    first = observed(variant="enter_address_manually")
    acts = []

    async def read():
        return first

    async def act(action):
        acts.append(action)
        return True

    with pytest.raises(ManualAddressGate, match="entry_open_unverified"):
        await resolve_manual_address(
            read, act, BrowserPolicy("http://127.0.0.1:3300", "dodo"), source_sha256=None,
            deadline=asyncio.get_running_loop().time() + 1, effect_timeout=0.001,
            executed=(first[0].candidates[0][1], first[1]),
        )
    assert acts == []


@pytest.mark.parametrize("kind", ["login", "purchase", "pay"])
async def test_manual_lifecycle_rejects_consequential_actions_even_when_fields_are_open(kind):
    state = observed(visible={"city", "state", "postal"})
    action = state[0].candidates[0][1]
    action["_tryfolk"]["kind"] = kind

    async def forbidden():
        raise AssertionError("Invalid action must fail before any I/O")

    with pytest.raises(ManualAddressGate, match="control_unresolved"):
        await resolve_manual_address(
            forbidden, forbidden, BrowserPolicy("http://127.0.0.1:3300", "dodo"),
            deadline=asyncio.get_running_loop().time() + 1, executed=(action, state[1]),
        )
