"""External browser/provider fixtures; never genuine payment evidence."""

import copy
import json
from types import SimpleNamespace
from urllib.parse import urlencode, urldefrag

import pytest
from tryfolk.browser_network import ResourceGuard, NetworkPolicy
import tryfolk.browser_network as network
from tryfolk.browser_policy import DODO_CHECKOUT, DODO_SECURE_FRAME
from tryfolk.secure_frame import frame_config, BROWSER_API
from tryfolk.native_card_observation import READ_CONTEXT
from tryfolk.browser_settlement import DOCUMENT_STATE

PARENT = DODO_CHECKOUT + "/session/fixture_parent"
RAW = DODO_SECURE_FRAME + "?" + urlencode({
    "publishableKey": "pk_snd_fixture", "clientSecret": "pay_fixture_secret_ABC123",
    "apiUrl": BROWSER_API,
}) + "#" + urlencode({"parentOrigin": DODO_CHECKOUT})
FIELDS = {"country", "address", "city", "state", "postal", "card_number", "card_cvc", "card_expiry"}


async def admission_fixture(monkeypatch):
    state = {"generation": 1234, "native": {"config_bound": True, "generation": 1234},
             "ua": "Chrome/120.0", "language": "en-US", "color_depth": 24,
             "screen_height": 800, "screen_width": 1280, "time_zone": 0}
    ownership = dict(run_id="run_fixture", persona_id="persona_fixture", account_id="account_fixture",
                     fixture_id="fixture_fixture", target_revision="revision_fixture")
    identity = {"approved": True, "provider_environment": "test_mode", "payment_correlated": True,
                "customer_correlated": True, "payment_id": "pay_fixture", "checkout_session_id": "cks_fixture",
                "order_id": "order_fixture", "ownership": ownership, "payment_link": PARENT}
    page = SimpleNamespace()
    page.main_frame = SimpleNamespace(page=page, url=PARENT, parent_frame=None)

    async def contact(_):
        return {"url": page.main_frame.url, "generation": state.get("parent_generation", 1000), "ready_state": "complete", "body_present": True} if _ == DOCUMENT_STATE else {}

    page.evaluate = contact

    async def observe(script):
        return copy.deepcopy(state) if script == READ_CONTEXT else state["generation"]

    async def source(_):
        return RAW

    async def element():
        return SimpleNamespace(get_attribute=source)

    child = SimpleNamespace(page=page, parent_frame=page.main_frame, url=RAW,
                            evaluate=observe, frame_element=element)
    page.frames = [page.main_frame, child]

    async def verify(_):
        return copy.deepcopy(identity)

    async def covered(_):
        return True

    async def document(_):
        return b"<html>External fixture</html>", {"content-type": "text/html"}

    monkeypatch.setattr(network, "public_frame", document)
    guard = ResourceGuard(frame_verify=verify)
    guard.primary_page, guard.policy, guard.response_guard = page, NetworkPolicy("http://127.0.0.1:3300", "dodo"), covered
    guard.card_enabled = True

    async def headers():
        return {}

    request = SimpleNamespace(frame=child, method="GET", resource_type="document", url=urldefrag(RAW)[0],
                              is_navigation_request=lambda: True, redirected_from=None, post_data=None,
                              all_headers=headers)

    class Route:
        async def fulfill(self, **kwargs):
            pass

        async def abort(self, _):
            raise AssertionError("Fixture secure child was unexpectedly rejected")

    route = Route()
    route.request = request
    await guard.route(route)
    context = frame_config(RAW)
    for path in ("/account/payment_methods", "/customers/payment_methods"):
        async def body():
            return b'{"payment_type":"normal"}'

        response = SimpleNamespace(status=200, body=body, request=SimpleNamespace(
            frame=child, url=BROWSER_API + path, method="GET", resource_type="fetch"))
        await guard.passive_bootstrap_response(response, {"config_bound": True, "config_id": context["config_id"]},
                                               {"url": RAW, "parent_url": PARENT, "generation": 1234})
    creation = {"source": "dodo_test_api", "checkout_url": PARENT, "checkout_session_id": "cks_fixture",
                "order_id": "order_fixture", "ownership": ownership}

    async def creation_body():
        return json.dumps(creation).encode()

    url = guard.policy.origin + "/api/target/checkouts"
    response = SimpleNamespace(status=201, url=url, body=creation_body, request=SimpleNamespace(
        frame=page.main_frame, url=url, method="POST", resource_type="fetch", redirected_from=None))
    assert await guard.observe_owned_checkout_response(response)
    cards = {key: (context["config_id"], 1234) for key in FIELDS if key.startswith("card_")}
    return guard, page, child, state, identity, cards


@pytest.mark.parametrize("mutation,predicate", [
    ("parent", "owned_checkout_parent_exact"), ("order", "independent_owned_order_bound"),
    ("ownership", "independent_owned_ownership_bound"), ("customer", "independent_customer_correlated"),
    ("card", "card_number_completion_current"), ("capability", "ordinary_Test_card_capability_explicit"),
    (None, None),
])
async def test_native_pay_admission_reports_exact_closed_failure(monkeypatch, mutation, predicate):
    guard, page, child, state, identity, cards = await admission_fixture(monkeypatch)
    if mutation == "parent":
        page.main_frame.url += "_other"
    elif mutation == "order":
        identity["order_id"] = "order_other"
    elif mutation == "ownership":
        identity["ownership"]["account_id"] = "account_other"
    elif mutation == "customer":
        identity["customer_correlated"] = False
    elif mutation == "card":
        cards["card_number"] = (cards["card_number"][0], 5678)
    elif mutation == "capability":
        guard.card_enabled = False
    if predicate:
        with pytest.raises(ValueError) as caught:
            await guard.arm_native_pay(FIELDS, cards)
        assert caught.value.first_failed_predicate == predicate
        assert caught.value.checks[predicate] is False
        assert guard.card_attempt is None
        assert guard.events[-1]["arm_admission_checks"] == caught.value.checks
    else:
        await guard.arm_native_pay(FIELDS, cards)
        assert guard.card_attempt is not None
        assert all(guard.events[-1]["arm_admission_checks"].values())
    assert PARENT not in json.dumps(guard.events[-1])


async def test_pay_freezes_authenticated_distinct_payment_parent_and_rejects_unrelated_url(monkeypatch):
    guard, page, child, state, identity, cards = await admission_fixture(monkeypatch)
    identity["payment_link"] = DODO_CHECKOUT + "/Private1"
    page.main_frame.url = identity["payment_link"]
    await guard.arm_native_pay(FIELDS, cards)
    assert guard.card_attempt.parent_url == identity["payment_link"]
    assert guard.owned_checkout(identity)["url"] == PARENT


@pytest.mark.parametrize("mutation", ["link", "parent", "generation", "order", "account", "checkout", "payment"])
async def test_fresh_confirmation_rechecks_frozen_parent_even_for_owned_return(monkeypatch, mutation):
    guard, page, child, state, identity, cards = await admission_fixture(monkeypatch)
    await guard.arm_native_pay(FIELDS, cards)
    state["native"].update(return_source="CONFIRM", return_url=guard.policy.origin + "/checkout/return")
    if mutation == "link":
        identity["payment_link"] = DODO_CHECKOUT + "/Other111"
    elif mutation == "parent":
        page.main_frame.url += "?other=1"
    elif mutation == "generation":
        state["parent_generation"] = 2000
    elif mutation == "order":
        identity["order_id"] = "order_other"
    elif mutation == "account":
        identity["ownership"]["account_id"] = "account_other"
    elif mutation == "checkout":
        identity["checkout_session_id"] = "cks_other"
    elif mutation == "payment":
        identity["payment_id"] = "pay_other"
    request = SimpleNamespace(url=BROWSER_API + "/payments/pay_fixture/confirm", method="POST", resource_type="fetch",
                              post_data="{}", redirected_from=None, is_navigation_request=lambda: False)
    with pytest.raises(ValueError, match="Native confirmation parent failed"):
        await guard.bind_api(request, {}, child)
    assert guard.card_attempt.confirmed is False
    assert guard.events[-1]["decision"] == "deny_native_current_parent"
    assert PARENT not in json.dumps(guard.events[-1])
