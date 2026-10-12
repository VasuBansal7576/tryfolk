"""Invented page/SDK records only; zero real browser/payment actions."""

from copy import deepcopy
from types import SimpleNamespace
import pytest
from test_nonfinancial_pre_act_drift import preparation, Page
from tryfolk.browser_policy import BrowserPolicy, DODO_CHECKOUT
from tryfolk.runtime import FreshActionRetry


def fixture():
    policy = BrowserPolicy("http://127.0.0.1:3301", "dodo")
    observed = {
        "url": DODO_CHECKOUT + "/session/offline",
        "generation": 100,
        "control_signature": "before",
        "body_present": True,
        "ready_state": "complete",
        "busy": False,
    }
    control = {
        "present": True,
        "visible": True,
        "disabled": False,
        "tag": "BUTTON",
        "type": "button",
        "label": "Enter manually",
        "destination": observed["url"],
        "center_hit_self": True,
        "popup_ancestor": True,
    }
    action = policy.bind(
        {"selector": "#old-inner", "method": "click", "arguments": [], "description": "Fresh manual choice"}, control, observed["url"]
    )
    return policy, observed, control, action


async def test_stale_unexecuted_inner_manual_is_discarded_and_only_fresh_replacement_executes():
    prepare, _ = preparation()
    prepare.__globals__["clicked"] = set()
    policy, observed, control, action = fixture()
    current = observed | {"control_signature": "late suggestion changes"}

    async def read(*_):
        return deepcopy(control)

    executed = []
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
    assert caught.value.code == "nonfinancial_manual_control_stale"
    error = RuntimeError("offline")
    error.sdk_code = caught.value.code
    error.sdk_data = caught.value.data
    retry = FreshActionRetry()
    assert retry.allow(error, action, observed)
    replacement = action | {"selector": "#new-inner"}
    assert retry.accepts_replacement(replacement, current)
    executed.append(
        await prepare(
            Page(current),
            policy,
            {"action": replacement},
            current,
            [replacement],
            SimpleNamespace(card_attempt=None),
            read,
        )
    )
    assert executed == [replacement]
    assert not retry.accepts_replacement(replacement, current | {"generation": 101})
    assert not retry.accepts_replacement(replacement, current | {"url": current["url"] + "other"})


@pytest.mark.parametrize(
    "invalid", ["outer", "executed", "generation", "url", "armed", "clicked", "financial", "no_body", "busy"]
)
async def test_manual_drift_never_repeats_executed_or_financial_or_foreign_document(invalid):
    prepare, _ = preparation()
    prepare.__globals__["clicked"] = set()
    policy, observed, control, action = fixture()
    current = observed | {"control_signature": "late change"}
    attempt = None
    if invalid == "outer":
        control["label"] = "Enter address manually"
        action = policy.bind(
            {"selector": "#outer", "method": "click", "arguments": []}, control, observed["url"]
        )
    elif invalid == "generation":
        current["generation"] = 101
    elif invalid == "url":
        current["url"] += "foreign"
    elif invalid == "armed":
        attempt = SimpleNamespace(armed_at=1, confirmed=False)
    elif invalid == "clicked":
        prepare.__globals__["clicked"] = {"manual"}
    elif invalid == "financial":
        action = action | {"_tryfolk": action["_tryfolk"] | {"kind": "pay"}}
    elif invalid == "no_body":
        current["body_present"] = False
    elif invalid == "busy":
        current["busy"] = True

    async def read(*_):
        return deepcopy(control)

    if invalid == "executed":
        error = RuntimeError("offline")
        error.sdk_code = "nonfinancial_manual_control_stale"
        error.sdk_data = {
            "same_url": True,
            "same_generation": True,
            "same_control_signature": False,
            "nonfinancial": True,
            "executed": True,
            "manual_variant": "enter_manually",
        }
        assert not FreshActionRetry().allow(error, action, observed)
        return
    with pytest.raises(RuntimeError) as caught:
        await prepare(
            Page(current),
            policy,
            {"action": action},
            observed,
            [action],
            SimpleNamespace(card_attempt=attempt),
            read,
        )
    assert getattr(caught.value, "code", None) is None


def test_manual_reobservations_share_original_deadline_and_three_discard_bound():
    _, document, _, action = fixture()
    error = RuntimeError("offline unexecuted inner choice")
    error.sdk_code = "nonfinancial_manual_control_stale"
    error.sdk_data = {
        "same_url": True,
        "same_generation": True,
        "same_control_signature": False,
        "nonfinancial": True,
        "executed": False,
        "manual_variant": "enter_manually",
    }
    retry = FreshActionRetry(timeout=10)
    deadline = retry.deadline
    assert all(retry.allow(error, action, document) for _ in range(3))
    assert retry.deadline == deadline and not retry.allow(error, action, document)
    assert not FreshActionRetry(timeout=0).allow(error, action, document)


async def test_runtime_reobserves_manual_and_dispatches_fresh_same_document_choice_only(
    tmp_path, monkeypatch
):
    import tryfolk.runtime as module

    monkeypatch.setenv("TRYFOLK_EVIDENCE_ROOT", str(tmp_path / "private"))
    monkeypatch.delenv("NEATLOGS_API_KEY", raising=False)
    policy, document, _, old = fixture()
    fresh = old | {"selector": "#fresh-inner"}
    executed = []
    observations = []

    class SDK:
        def __init__(self, name, *_, **__):
            self.name = name
            self.process = SimpleNamespace(returncode=None)

        async def request(self, data, timeout=180):
            if self.name == "tinytroupe":
                if data["op"] == "decide":
                    choices = data["observation"]["actions"]
                    return {
                        "intent": {
                            "type": "BROWSER" if choices else "FINISH",
                            "target": choices[0]["id"] if choices else "",
                            "content": "offline individual intent",
                        }
                    }
                return {}
            if data["op"] == "observe":
                index = len(observations)
                observations.append(index)
                return {
                    "url": document["url"],
                    "document": document,
                    "text": "offline billing",
                    "actions": [old if index == 0 else fresh] if index < 2 else [],
                }
            if data["op"] == "act":
                if data["action"] == old:
                    error = RuntimeError("offline")
                    error.sdk_code = "nonfinancial_manual_control_stale"
                    error.sdk_data = {
                        "same_url": True,
                        "same_generation": True,
                        "same_control_signature": False,
                        "nonfinancial": True,
                        "executed": False,
                        "manual_variant": "enter_manually",
                    }
                    raise error
                executed.append(data["action"])
                return {"data": {"success": True}}
            return {}

        async def close(self):
            self.process.returncode = 0

    monkeypatch.setattr(module, "SDKProcess", SDK)
    runtime = module.Runtime(SimpleNamespace())

    async def provision(*_):
        return {"login_url": policy.origin + "/target/login", "account_id": "offline"}

    async def revision(*_):
        return {}

    async def verify(*_):
        return {"outcome": "pending", "measured": False}

    async def emit(*_):
        pass

    runtime.provision = provision
    runtime.check_revision = revision
    runtime.verification = verify
    result = await runtime.journey(
        {"id": "offline", "provider": "dodo", "preview_url": policy.origin, "revision": "offline"},
        {"id": "offline-person", "conditions": {}, "initial_snapshot": {}, "profile": {}},
        lambda: False,
        emit,
    )
    assert executed == [fresh] and len(observations) == 3 and result["measured"] is False
