"""Public observation/policy behavior; browser metadata is external I/O fixture."""
import pytest

from tryfolk.browser_observation import observe_fresh
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.browser_settlement import DOCUMENT_STATE


async def test_owned_return_exposes_one_main_cta_with_global_download_navigation_present():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    url = policy.origin + "/checkout/return"
    document = {"url": url, "generation": 1, "control_signature": "fixture",
                "ready_state": "complete", "body_present": True}

    class Page:
        async def evaluate(self, expression):
            return dict(document) if expression == DOCUMENT_STATE else "Fixture return"

    actions = [{"selector": selector, "method": "click", "arguments": []}
               for selector in ("#global", "#return-cta")]
    controls = {
        "#global": {"label": "Special Downloads", "in_main": False},
        "#return-cta": {"label": "View your art pack", "in_main": True},
    }

    async def infer():
        return actions

    async def read_control(selector):
        return {"present": True, "visible": True, "disabled": False, "tag": "A",
                "destination": policy.origin + "/special-downloads", **controls[selector]}

    result = await observe_fresh(Page(), policy, infer, read_control)
    art_pack = [a for a in result["actions"] if a["_tryfolk"]["kind"] == "art_pack"]
    assert [a["selector"] for a in art_pack] == ["#return-cta"]
    assert len(result["actions"]) == 2  # Header remains an independently bound ordinary click.
    assert result["freshness"]["status"] == "fresh"


@pytest.mark.parametrize("changes", [
    {"in_main": False}, {"in_main": None}, {"tag": "BUTTON"},
    {"label": "Special Downloads"}, {"label": "View another art pack"},
    {"destination": "http://127.0.0.1:3300/special-downloads?other=1"},
])
def test_return_cta_requires_current_owned_scope_label_tag_and_exact_destination(changes):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    control = {"present": True, "visible": True, "disabled": False, "tag": "A",
               "label": "View your art pack", "in_main": True,
               "destination": policy.origin + "/special-downloads"}
    action = {"selector": "#return-cta", "method": "click", "arguments": []}
    original = policy.bind(action, control, policy.origin + "/checkout/return")
    rebound = policy.bind(action, control | changes, policy.origin + "/checkout/return")
    assert original["_tryfolk"]["kind"] == "art_pack"
    assert rebound["_tryfolk"]["kind"] == "click"
    assert rebound != original  # Existing pre-act equality rejects the changed control.


@pytest.mark.parametrize("label", ["View your art pack", "Return to art pack"])
def test_both_owned_source_return_cta_states_bind_without_requiring_an_id(label):
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    action = policy.bind(
        {"selector": "xpath=/html[1]/body[1]/main[1]/a[1]", "method": "click"},
        {"tag": "A", "in_main": True, "label": label,
         "destination": policy.origin + "/special-downloads"},
        policy.origin + "/checkout/return",
    )
    assert action["_tryfolk"]["kind"] == "art_pack"
    assert action["_tryfolk"]["capability"] is False


def test_foreign_or_unavailable_return_cta_never_binds():
    policy = BrowserPolicy("http://127.0.0.1:3300", "dodo")
    control = {"tag": "A", "in_main": True, "label": "View your art pack",
               "destination": policy.origin + "/special-downloads"}
    for changes in ({"destination": "https://unowned.invalid/special-downloads"},
                    {"present": False}, {"visible": False}, {"disabled": True}):
        with pytest.raises(ValueError):
            policy.bind({"selector": "#return-cta", "method": "click"},
                        control | changes, policy.origin + "/checkout/return")
