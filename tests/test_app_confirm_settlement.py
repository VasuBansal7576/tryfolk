"""Offline browser fixtures: no real payment, callback or provider action."""

import hashlib
from types import SimpleNamespace
import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.browser_settlement import settle, SettlementDeadline

ORIGIN = "http://127.0.0.1:3301"
PUBLIC_SUPPRESSED = "Your payment is confirmed, but the art pack is unavailable. The sandbox owner needs to repair fulfillment."
PUBLIC_PENDING = "Payment is still awaiting confirmation. You can check again after completing checkout."
SUPPRESSED = hashlib.sha256(PUBLIC_SUPPRESSED.encode()).hexdigest()
PENDING = hashlib.sha256(PUBLIC_PENDING.encode()).hexdigest()
BASE = {
    "url": ORIGIN + "/checkout/return",
    "generation": 1,
    "ready_state": "complete",
    "body_present": True,
    "visible_controls": 2,
    "busy": False,
    "control_signature": "enabled confirm and owned link",
    "error_signature": "[]",
    "completion_status_sha256": None,
}
ACTION = {"method": "click", "_tryfolk": {"kind": "confirm", "url": BASE["url"]}}


class Page:
    def __init__(self, states):
        self.states = list(states)
        self.last = None

    async def url(self):
        return self.states[0]["url"] if self.last is None else self.last["url"]

    async def evaluate(self, _):
        self.last = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return dict(self.last)

    async def snapshot(self, **_):
        return SimpleNamespace(formatted_tree="current owned Confirm and link")


async def test_suppressed_confirm_status_only_completion_settles_without_inferring_payment():
    state = BASE | {"completion_status_sha256": SUPPRESSED}
    result = await settle(
        Page([state]), BrowserPolicy(ORIGIN, "dodo"), ACTION, previous_document=BASE, timeout=0.02
    )
    assert result["transition_state"] == "ready"
    assert result["completion_status_sha256"] == SUPPRESSED
    assert not any(k in result for k in ("payment_verified", "entitlement_verified", "outcome"))


@pytest.mark.parametrize(
    "condition",
    [
        "unchanged",
        "busy",
        "foreign_page",
        "foreign_origin",
        "wrong_action",
        "unrecognized",
        "unstable_status",
    ],
)
async def test_status_is_not_a_general_transition_or_readiness_bypass(condition):
    previous = dict(BASE)
    action = dict(ACTION)
    state = BASE | {"completion_status_sha256": SUPPRESSED}
    if condition == "unchanged":
        previous["completion_status_sha256"] = SUPPRESSED
    elif condition == "busy":
        state["busy"] = True
    elif condition == "foreign_page":
        previous["url"] = state["url"] = ORIGIN + "/special-downloads"
    elif condition == "foreign_origin":
        previous["url"] = state["url"] = "https://test.checkout.dodopayments.com/checkout/return"
    elif condition == "wrong_action":
        action = {"method": "click", "_tryfolk": {"kind": "pay", "url": BASE["url"]}}
    elif condition == "unrecognized":
        state["completion_status_sha256"] = "not a source-bound SHA"
    states = [state]
    if condition == "unstable_status":
        states = [state, state | {"completion_status_sha256": PENDING}] * 30
    with pytest.raises(SettlementDeadline):
        await settle(
            Page(states), BrowserPolicy(ORIGIN, "dodo"), action, previous_document=previous, timeout=0.01
        )


async def test_healthy_confirm_removing_button_keeps_existing_ready_path():
    result = await settle(
        Page([BASE | {"control_signature": "only owned link", "visible_controls": 1,
                      "completion_status_sha256": SUPPRESSED}]),
        BrowserPolicy(ORIGIN, "dodo"),
        ACTION,
        previous_document=BASE,
        timeout=0.02,
    )
    assert result["transition_state"] == "ready"


def test_passive_dom_reader_emits_only_known_public_completion_hash_on_source_route():
    import json
    import subprocess
    from tryfolk.browser_settlement import DOCUMENT_STATE
    script = """
const data=JSON.parse(process.argv[1]);
global.location={href:data.url,pathname:new URL(data.url).pathname};
global.performance={timeOrigin:1};
global.getComputedStyle=el=>({visibility:el.hidden?'hidden':'visible'});
const statuses=data.texts.map(text=>({innerText:text,getClientRects:()=>[{}]}));
global.document={readyState:'complete',body:{},querySelectorAll:selector=>selector==='[role=status]'?statuses:[]};
process.stdout.write(JSON.stringify(eval(data.reader)));
"""
    for url,texts,expected in [
        (BASE['url'],[PUBLIC_SUPPRESSED],SUPPRESSED),
        (BASE['url'],['private contact or unrelated content'],None),
        (ORIGIN+'/special-downloads',[PUBLIC_SUPPRESSED],None),
        (BASE['url'],[PUBLIC_SUPPRESSED,PUBLIC_PENDING],None),
    ]:
        output=subprocess.check_output(['node','-e',script,json.dumps({'url':url,'texts':texts,'reader':DOCUMENT_STATE})],text=True)
        state=__import__('json').loads(output)
        assert state['completion_status_sha256']==expected
        assert 'private contact' not in output and PUBLIC_SUPPRESSED not in output


async def test_changed_confirming_button_cannot_finish_before_owned_app_response():
    # The real app changes the button to disabled 'Confirming purchase…'.
    # This changed signature previously settled even with no public completion.
    in_progress = BASE | {"control_signature": "disabled Confirming purchase and owned link"}
    with pytest.raises(SettlementDeadline):
        await settle(Page([in_progress]), BrowserPolicy(ORIGIN, "dodo"), ACTION,
                     previous_document=BASE, timeout=.02)


async def test_confirming_signature_is_discarded_until_source_bound_completion():
    in_progress = BASE | {"control_signature": "disabled Confirming purchase and owned link"}
    complete = BASE | {"control_signature": "only owned link", "visible_controls": 1,
                       "completion_status_sha256": SUPPRESSED}
    result = await settle(Page([in_progress, in_progress, complete, complete]),
                          BrowserPolicy(ORIGIN, "dodo"), ACTION,
                          previous_document=BASE, timeout=.2)
    assert result["completion_status_sha256"] == SUPPRESSED
    assert result["control_signature"] == "only owned link"
