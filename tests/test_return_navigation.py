"""Invented current browser/API I/O; no actual9 denied URL reconstruction."""

import json
from types import SimpleNamespace

import pytest

from test_native_pay_admission import normalized_admission_fixture, FIELDS
from tryfolk.browser_policy import DODO_CHECKOUT

RETURN_SOURCE = "78448c8d8f29f9f76004d79dc7c00ef22839fe72fc198b0316ef849945c02bfa"


async def return_fixture(monkeypatch, *, oopif=False):
    guard, page, child, state, identity, cards = await normalized_admission_fixture(monkeypatch)
    nav = guard.checkout_navigation
    entry = {"url": nav.entry_url, "generation": 1000, "body_present": True, "ready_state": "complete"}
    assert nav.observe_source(RETURN_SOURCE, entry)
    from tryfolk.native_card_observation import READ_CONTACT

    old_evaluate = page.evaluate

    async def contact_evaluate(script):
        if script == READ_CONTACT:
            return {
                "email": "synthetic_fixture@tryfolk.invalid",
                "return_email_source_exact": True,
                "url": page.main_frame.url,
                "generation": state.get("parent_generation", 1000),
            }
        return await old_evaluate(script)

    page.evaluate = contact_evaluate
    if oopif:
        primary_reader = guard.protocol_reader

        async def main_only():
            tree = await primary_reader()
            tree.pop("childFrames", None)
            return tree

        async def separate_native():
            tree = await primary_reader()
            frame = tree.get("childFrames", [{}])[0].get("frame", {})
            frame["parentId"] = tree["frame"]["id"]
            base, fragment = frame.get("url", "").split("#", 1)
            frame["url"], frame["urlFragment"] = base, "#" + fragment
            return {
                "frameTree": {"frame": frame},
                "targetInfo": {"targetId": "child_fixture", "type": "iframe"},
            }

        guard.protocol_reader = main_only
        guard.native_protocol_readers = {"child_fixture": separate_native}
        guard._native_protocol_target = "child_fixture"
    await guard.arm_native_pay(FIELDS, cards)
    native_return = DODO_CHECKOUT + "/status/Private1/wallet-return"
    state["native"].update(
        return_source="CONFIRM",
        return_url=native_return,
        confirm_observed=True,
        confirm_origin_bound=True,
        confirm_parent_source_bound=True,
        confirm_generation=state["generation"],
    )
    guard.card_attempt.return_url = guard.card_attempt._admitted_status_return = native_return
    assert await guard.capture_confirm_protocol(await guard.parent_document())
    assert guard.card_attempt.consume("confirm")
    guard.card_attempt.observe_response("succeeded", 200, operation="confirm")
    identity["payment_status"] = "succeeded"
    request = SimpleNamespace(
        url=DODO_CHECKOUT + "/status/Private1/succeeded",
        method="GET",
        resource_type="document",
        post_data=None,
        redirected_from=None,
        frame=page.main_frame,
        is_navigation_request=lambda: True,
    )
    return guard, page, child, state, identity, request


async def test_post_confirm_source_derived_status_requires_fresh_owned_payment_and_current_document(
    monkeypatch,
):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    admitted, facts = await guard.bind_return_navigation(request)
    assert admitted
    assert all(facts["post_confirm_checks"].values())
    assert all(type(v) is bool for v in facts["return_facts"].values())
    assert facts["return_facts"]["candidate_equals_confirmed_native_body_return"] is False
    assert facts["return_facts"]["candidate_source_derived_status_exact"] is True
    assert guard.card_attempt.navigation_allowed(request.url)
    assert not guard.card_attempt.navigation_allowed(request.url + "?extra=1")
    assert not guard.card_attempt.navigation_allowed(request.url.replace("Private1", "Other111"))
    serialized = json.dumps(facts)
    assert not any(v in serialized for v in ("Private1", "sess_Abc123xyZ9", "pay_fixture", "succeeded"))


@pytest.mark.parametrize(
    "mutation",
    [
        "source_missing",
        "source_unknown",
        "source_stale",
        "native_alias",
        "native_status",
        "native_query",
        "native_missing",
        "not_confirmed",
        "response_missing",
        "response_failed",
        "other_status",
        "other_alias",
        "extra_query",
        "empty_query",
        "fragment",
        "empty_fragment",
        "foreign",
        "encoded",
        "redirected",
        "method",
        "body",
        "parent_changed",
        "parent_generation",
        "storage",
        "child_generation",
        "child_parent",
        "child_native_return",
        "identity_account",
        "identity_order",
        "identity_checkout",
        "identity_payment",
        "identity_live",
        "identity_customer",
        "identity_payment_state",
    ],
)
async def test_post_confirm_status_denies_unbound_source_state_or_destination(monkeypatch, mutation):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    capability, nav = guard.card_attempt, guard.checkout_navigation
    if mutation == "source_missing":
        nav.return_source_generation = None
    elif mutation == "source_unknown":
        nav.return_source_generation = None
        nav.observe_source("0" * 64, {"url": nav.entry_url, "generation": 1000})
    elif mutation == "source_stale":
        nav.return_source_generation = 2000
    elif mutation == "native_alias":
        capability.return_url = capability._admitted_status_return = (
            DODO_CHECKOUT + "/status/Other111/wallet-return"
        )
    elif mutation == "native_status":
        capability.return_url = capability._admitted_status_return = (
            DODO_CHECKOUT + "/status/Private1/unknown"
        )
    elif mutation == "native_query":
        capability.return_url += "?extra=1"
    elif mutation == "native_missing":
        capability.return_url = None
    elif mutation == "not_confirmed":
        capability.confirmed = False
    elif mutation == "response_missing":
        capability.response_status = None
    elif mutation == "response_failed":
        capability.observe_response(None, 500, operation="confirm")
    elif mutation in {
        "other_status",
        "other_alias",
        "extra_query",
        "empty_query",
        "fragment",
        "empty_fragment",
        "foreign",
        "encoded",
    }:
        request.url = {
            "other_status": request.url.replace("succeeded", "failed"),
            "other_alias": request.url.replace("Private1", "Other111"),
            "extra_query": request.url + "?extra=1",
            "empty_query": request.url + "?",
            "fragment": request.url + "#x",
            "empty_fragment": request.url + "#",
            "foreign": request.url.replace("test.checkout.", "checkout."),
            "encoded": request.url.replace("Private1", "%50rivate1"),
        }[mutation]
    elif mutation == "redirected":
        request.redirected_from = object()
    elif mutation == "method":
        request.method = "POST"
    elif mutation == "body":
        request.post_data = "{}"
    elif mutation == "parent_changed":
        page.main_frame.url += "&other=1"
    elif mutation == "parent_generation":
        state["parent_generation"] = 2000
    elif mutation == "storage":
        nav._armed_normalization = None
    elif mutation == "child_generation":
        state["generation"] = 2000
    elif mutation == "child_parent":
        child.parent_frame = object()
    elif mutation == "child_native_return":
        capability.return_url += "other"
    elif mutation in {"identity_account", "identity_order", "identity_checkout", "identity_payment"}:
        field = {
            "identity_account": "account_id",
            "identity_order": "order_id",
            "identity_checkout": "checkout_session_id",
            "identity_payment": "payment_id",
        }[mutation]
        if field == "account_id":
            identity["ownership"][field] = "other"
        else:
            identity[field] = "other"
    elif mutation == "identity_live":
        identity["provider_environment"] = "live_mode"
    elif mutation == "identity_customer":
        identity["customer_correlated"] = False
    else:
        identity["payment_status"] = "failed"
    admitted, facts = await guard.bind_return_navigation(request)
    assert not admitted
    assert not all(facts["post_confirm_checks"].values())
    assert capability._admitted_post_confirm_status is None
    assert not any(v in json.dumps(facts) for v in ("Private1", "sess_Abc123xyZ9", "pay_fixture"))


async def test_return_diagnostics_keep_exact_body_and_owned_return_distinct(monkeypatch):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    request.url = guard.card_attempt.return_url
    admitted, facts = await guard.bind_return_navigation(request)
    assert admitted and facts["return_facts"]["candidate_equals_confirmed_native_body_return"]
    request.url = guard.policy.origin + "/checkout/return"
    admitted, facts = await guard.bind_return_navigation(request)
    assert admitted and facts["return_facts"]["candidate_equals_frozen_owned_return"]
    assert not facts["return_facts"]["candidate_source_derived_status_exact"]


async def provider_fixture(monkeypatch):
    from tryfolk.checkout_navigation import STATUS_SOURCE_SHA256
    from tryfolk.browser_policy import DODO_RETURN

    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    assert (await guard.bind_return_navigation(request))[0]
    page.main_frame.url = request.url
    state["parent_generation"] = 3000
    assert guard.checkout_navigation.observe_source(STATUS_SOURCE_SHA256, await guard.parent_document())
    guard.checkout_navigation.status_protocol = (await guard.protocol_frames())["main"]
    request.url = DODO_RETURN + "/return/Private1"
    return guard, page, child, state, identity, request


async def test_terminal_closes_payment_but_exact_provider_return_remains_bounded(monkeypatch):
    guard, page, child, state, identity, request = await provider_fixture(monkeypatch)
    assert guard.card_attempt.closed
    assert not guard.card_attempt.consume("confirm") and not guard.card_attempt.consume("poll")
    admitted, facts = await guard.bind_return_navigation(request)
    assert admitted and all(facts["provider_return_checks"].values())
    guard.policy.check_url(request.url)  # Passive settlement observation only.
    assert guard.policy.decide(request.url, "GET", "document", page.main_frame.url, True) == "continue"
    assert guard.policy.decide(request.url, "POST", "document", page.main_frame.url, True) == "deny"
    assert guard.policy.decide(request.url, "GET", "fetch", page.main_frame.url, False) == "deny"
    with pytest.raises(ValueError):
        guard.policy.bind(
            {"selector": "#x", "method": "click", "arguments": []}, {"destination": request.url}, request.url
        )
    assert not (await guard.bind_return_navigation(request))[0]  # Single provider hop.
    assert not any(v in json.dumps(facts) for v in ("Private1", "pay_fixture", "succeeded"))


@pytest.mark.parametrize(
    "mutation",
    [
        "route",
        "status",
        "origin",
        "query",
        "fragment",
        "method",
        "body",
        "redirected",
        "source",
        "generation",
        "parent",
        "payment",
        "account",
        "checkout",
        "link",
        "receipt",
        "expired",
    ],
)
async def test_provider_return_requires_current_exact_source_and_owned_identity(monkeypatch, mutation):
    guard, page, child, state, identity, request = await provider_fixture(monkeypatch)
    if mutation in ("route", "status", "origin", "query", "fragment"):
        request.url = {
            "route": request.url.replace("Private1", "Other111"),
            "status": request.url + "/succeeded",
            "origin": request.url.replace("test.", ""),
            "query": request.url + "?status=succeeded",
            "fragment": request.url + "#",
        }[mutation]
    elif mutation == "method":
        request.method = "POST"
    elif mutation == "body":
        request.post_data = "{}"
    elif mutation == "redirected":
        request.redirected_from = object()
    elif mutation == "source":
        guard.checkout_navigation.status_source_generation = None
    elif mutation == "generation":
        state["parent_generation"] = 4000
    elif mutation == "parent":
        page.main_frame.url += "?status=succeeded"
    elif mutation == "account":
        identity["ownership"]["account_id"] = "other"
    elif mutation in ("payment", "checkout", "link", "receipt"):
        identity[
            {
                "payment": "payment_id",
                "checkout": "checkout_session_id",
                "link": "payment_link",
                "receipt": "payment_status",
            }[mutation]
        ] = "other"
    else:
        guard.card_attempt.confirmed_at -= 301
    admitted, facts = await guard.bind_return_navigation(request)
    assert not admitted and facts["first_failed_predicate"]
    assert guard.policy._return_transport_url is None


class ReturnResponse:
    def __init__(self):
        self.calls = []

    async def send(self, method, params=None):
        self.calls.append(method)
        return (
            {"frameTree": {"frame": {"id": "main_fixture", "loaderId": "main_3000", "url": self.url}}}
            if method == "Page.getFrameTree"
            else {}
        )


async def response_fixture(monkeypatch, destination):
    guard, page, child, state, identity, request = await provider_fixture(monkeypatch)
    assert (await guard.bind_return_navigation(request))[0]
    guard.primary_response_target = "target"
    session = ReturnResponse()
    session.url = page.main_frame.url
    event = {
        "requestId": "response",
        "request": {"url": request.url, "method": "GET"},
        "responseStatusCode": 302,
        "resourceType": "Document",
        "frameId": "main_fixture",
        "responseHeaders": [{"name": "Location", "value": destination}],
    }
    return guard, page, state, identity, event, session


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?payment_id=pay_fixture&status=succeeded",
        "?status=succeeded&email=synthetic_fixture%40tryfolk.invalid&payment_id=pay_fixture",
    ],
)
async def test_exact_provider_response_redirect_only_to_owned_bound_one_time_return(monkeypatch, query):
    destination = "http://127.0.0.1:3300/checkout/return" + query
    guard, page, state, identity, event, session = await response_fixture(monkeypatch, destination)
    await guard.redirect_response(session, event, target_type="page", target_id="target")
    assert "Fetch.continueResponse" in session.calls and "Fetch.failRequest" not in session.calls
    assert guard.policy.redirect_allowed(event["request"]["url"], destination)
    assert guard.card_attempt.navigation_allowed(destination)
    assert all(guard.events[-1]["provider_redirect_checks"].values())
    assert not guard.policy.redirect_allowed(event["request"]["url"], destination + "#")
    assert not any(
        v in json.dumps(guard.events[-1])
        for v in ("Private1", "pay_fixture", "synthetic_fixture@tryfolk.invalid")
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_payment",
        "wrong_status",
        "wrong_email",
        "duplicate",
        "unknown",
        "session_id",
        "subscription",
        "license",
        "empty",
        "encoded_name",
        "fragment",
        "external",
        "wrong_path",
        "userinfo",
        "protocol_relative",
        "double_location",
        "no_location",
        "wrong_http",
        "wrong_target",
        "wrong_frame",
        "wrong_method",
        "redirected",
        "source",
        "generation",
        "wrong_receipt",
        "wrong_account",
    ],
)
async def test_provider_redirect_rejects_unowned_or_ambiguous_locations(monkeypatch, mutation):
    base = "http://127.0.0.1:3300/checkout/return"
    query = "?payment_id=pay_fixture&status=succeeded"
    changes = {
        "wrong_payment": query.replace("pay_fixture", "pay_other"),
        "wrong_status": query.replace("succeeded", "failed"),
        "wrong_email": query + "&email=other%40example.com",
        "duplicate": query + "&status=succeeded",
        "unknown": query + "&x=1",
        "session_id": query + "&session_id=cks_fixture",
        "subscription": query + "&subscription_id=sub_fixture",
        "license": query + "&license_key=abc",
        "empty": "?",
        "encoded_name": query.replace("payment_id", "payment%5fid"),
        "fragment": query + "#",
        "external": None,
        "wrong_path": None,
        "userinfo": None,
        "protocol_relative": None,
    }
    dest = base + changes[mutation] if mutation in changes and changes[mutation] is not None else base + query
    dest = {
        "external": "https://example.com/checkout/return" + query,
        "wrong_path": base + "/other" + query,
        "userinfo": "http://evil@127.0.0.1:3300/checkout/return" + query,
        "protocol_relative": "//127.0.0.1:3300/checkout/return" + query,
    }.get(mutation, dest)
    guard, page, state, identity, event, session = await response_fixture(monkeypatch, dest)
    if mutation == "double_location":
        event["responseHeaders"] *= 2
    elif mutation == "no_location":
        event["responseHeaders"] = []
    elif mutation == "wrong_http":
        event["responseStatusCode"] = 200
    elif mutation == "wrong_frame":
        event["frameId"] = "child"
    elif mutation == "wrong_method":
        event["request"]["method"] = "POST"
    elif mutation == "redirected":
        event["redirectedRequestId"] = "prior"
    elif mutation == "source":
        guard.checkout_navigation.status_source_generation = None
    elif mutation == "generation":
        state["parent_generation"] = 4000
    elif mutation == "wrong_receipt":
        identity["payment_status"] = "failed"
    elif mutation == "wrong_account":
        identity["ownership"]["account_id"] = "other"
    await guard.redirect_response(
        session, event, target_type="page", target_id="other" if mutation == "wrong_target" else "target"
    )
    assert "Fetch.failRequest" in session.calls and "Fetch.continueResponse" not in session.calls
    assert guard.card_attempt._admitted_owned_redirect is None


async def test_result_event_before_python_response_body_waits_for_existing_observation(monkeypatch):
    import asyncio

    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    guard.card_attempt.response_status = None
    guard.card_attempt.closed = False

    async def response():
        await asyncio.sleep(0.01)
        guard.card_attempt.observe_response("succeeded", 200, operation="confirm")

    guard._payment_response_task = asyncio.create_task(response())
    assert (await guard.bind_return_navigation(request))[0]
    assert guard._payment_response_task.done() and guard.card_attempt.closed


async def test_unregistered_passive_response_does_not_grant_result_navigation(monkeypatch):
    import asyncio

    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    guard.card_attempt.response_status = None
    guard.card_attempt.closed = False
    guard._payment_response_pending = asyncio.get_running_loop().create_future()

    async def response_event():
        await asyncio.sleep(0.01)

        async def body():
            return b'{"status":"succeeded"}'

        await guard.payment_response(SimpleNamespace(body=body, status=200), {"operation": "confirm"})

    task = asyncio.create_task(response_event())
    admitted, facts = await guard.bind_return_navigation(request)
    assert not admitted and facts["first_failed_predicate"] == "accepted_native_response_status"
    await task
    assert guard.card_attempt.response_status is None


@pytest.mark.parametrize(
    "mutation",
    ["unknown", "wrong_status", "same_entry_generation", "wrong_path", "wrong_query", "changed_generation"],
)
async def test_status_source_only_on_exact_authorized_new_document(monkeypatch, mutation):
    from tryfolk.checkout_navigation import STATUS_SOURCE_SHA256

    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    assert (await guard.bind_return_navigation(request))[0]
    document = {"url": request.url, "generation": 3000}
    digest = STATUS_SOURCE_SHA256
    if mutation == "unknown":
        digest = "0" * 64
    elif mutation == "wrong_status":
        document["url"] = request.url.replace("succeeded", "failed")
    elif mutation == "same_entry_generation":
        document["generation"] = 1000
    elif mutation == "wrong_path":
        document["url"] = request.url.replace("Private1", "Other111")
    elif mutation == "wrong_query":
        document["url"] += "?status=succeeded"
    else:
        assert guard.checkout_navigation.observe_source(digest, document)
        document["generation"] = 4000
    assert not guard.checkout_navigation.observe_source(digest, document)


async def test_paused_new_document_does_not_wait_on_playwright_future_navigation_context(monkeypatch):
    import asyncio

    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    current_read = page.evaluate

    async def paused_playwright(script):
        await asyncio.Future()  # New document is paused by this very route.

    page.evaluate = paused_playwright
    guard.parent_reader = current_read  # Live current CDP-default-context I/O fixture.
    admitted, facts = await asyncio.wait_for(guard.bind_return_navigation(request), 0.05)
    assert admitted and all(facts["post_confirm_checks"].values())


@pytest.mark.parametrize("missing", ["url", "loaderId"])
async def test_paused_protocol_reader_never_substitutes_cached_parent_facts(monkeypatch, missing):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    read = guard.protocol_reader

    async def changed():
        tree = await read()
        tree["frame"].pop(missing, None)
        return tree

    guard.protocol_reader = changed
    admitted, facts = await guard.bind_return_navigation(request)
    assert not admitted


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "payment",
        "checkout",
        "generation",
        "config",
        "ownership",
        "missing_ownership",
        "source",
        "raw_email",
        "fresh_identity",
        "redirect",
    ],
)
async def test_private_provider_contact_freezes_exact_current_frame_and_owned_identity(monkeypatch, mutation):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    attempt = guard.card_attempt
    attempt._frozen_contact_email = None
    attempt._contact_email_current = False
    record = {
        "email": "synthetic_fixture@tryfolk.invalid",
        "config_id": attempt.context["config_id"],
        "generation": attempt.generation,
        "payment_id": identity["payment_id"],
        "checkout_session_id": identity["checkout_session_id"],
        "ownership": dict(identity["ownership"]),
        "facts": {
            "contact_binding_passed": True,
            "test_payments_read": True,
            "redirects_followed": 0,
            "current_identity_rechecked_after_private_email_read": True,
        },
    }
    if mutation in ("payment", "checkout", "generation", "config"):
        record[
            {
                "payment": "payment_id",
                "checkout": "checkout_session_id",
                "generation": "generation",
                "config": "config_id",
            }[mutation]
        ] = "other"
    elif mutation == "ownership":
        record["ownership"]["account_id"] = "other"
    elif mutation == "missing_ownership":
        record["ownership"] = None
    elif mutation == "source":
        record["facts"]["test_payments_read"] = False
    elif mutation == "raw_email":
        record["email"] = "other@example.com"
    elif mutation == "fresh_identity":
        record["facts"]["current_identity_rechecked_after_private_email_read"] = False
    elif mutation == "redirect":
        record["facts"]["redirects_followed"] = 1
    attempt.freeze_private_contact(record, identity, 1000)
    assert attempt._private_contact_bound is (mutation is None)
    if mutation is None:
        attempt.recheck_contact_email(
            {"email": None, "email_input_present": False, "url": attempt.parent_url, "generation": 1000}
        )
        assert attempt._contact_email_current
        checks = attempt.owned_location_checks(
            attempt.owned_return_url
            + "?payment_id=pay_fixture&status=succeeded&email=synthetic_fixture%40tryfolk.invalid",
            identity,
        )
        assert all(checks.values())
        attempt.recheck_contact_email(
            {"email": None, "email_input_present": True, "url": attempt.parent_url, "generation": 1000}
        )
        assert not attempt.owned_location_checks(
            attempt.owned_return_url
            + "?payment_id=pay_fixture&status=succeeded&email=synthetic_fixture%40tryfolk.invalid",
            identity,
        )["optional_email_current_observed_account_correlated"]
        assert all(
            attempt.owned_location_checks(
                attempt.owned_return_url + "?payment_id=pay_fixture&status=succeeded", identity
            ).values()
        )
    else:
        assert attempt._frozen_contact_email is None


@pytest.mark.parametrize("status_url", ["wallet", "owned", "derived"])
async def test_expired_return_phase_never_accepts_known_old_document(monkeypatch, status_url):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    request.url = {
        "wallet": guard.card_attempt.return_url,
        "owned": guard.card_attempt.owned_return_url,
        "derived": request.url,
    }[status_url]
    guard.card_attempt.confirmed_at -= 301
    admitted, facts = await guard.bind_return_navigation(request)
    assert not admitted and facts["first_failed_predicate"] == "return_phase_bounded"


async def test_paused_return_forbids_all_page_child_and_runtime_JS_reads(monkeypatch):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)

    async def forbidden(*args):
        raise AssertionError("Paused navigation cannot evaluate JavaScript")

    page.evaluate = child.evaluate = guard.parent_reader = forbidden
    admitted, facts = await guard.bind_return_navigation(request)
    assert admitted and all(facts["post_confirm_checks"].values())


@pytest.mark.parametrize(
    "mutation",
    [
        "main_loader",
        "main_id",
        "main_missing",
        "child_loader",
        "child_id",
        "child_missing",
        "main_url",
        "child_url",
    ],
)
async def test_paused_return_checks_live_frame_loader_identity(monkeypatch, mutation):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    reader = guard.protocol_reader

    async def changed():
        tree = await reader()
        main = tree["frame"]
        child = tree.get("childFrames", [{}])[0].get("frame", {})
        if mutation == "main_loader":
            main["loaderId"] = "new"
        elif mutation == "main_id":
            main["id"] = "other"
        elif mutation == "main_missing":
            main.pop("loaderId")
        elif mutation == "child_loader":
            child["loaderId"] = "new"
        elif mutation == "child_id":
            child["id"] = "other"
        elif mutation == "child_missing":
            tree.pop("childFrames", None)
        elif mutation == "main_url":
            main["url"] += "?x=1"
        else:
            child["url"] += "?x=1"
        return tree

    guard.protocol_reader = changed
    admitted, facts = await guard.bind_return_navigation(request)
    assert not admitted and facts["first_failed_predicate"]


@pytest.mark.parametrize("phase", ["provider_request", "provider_redirect"])
async def test_status_settlement_forbids_all_JS_reads_while_navigation_paused(monkeypatch, phase):
    async def forbidden(*args):
        raise AssertionError("No JavaScript read during paused settlement")

    if phase == "provider_request":
        guard, page, child, state, identity, request = await provider_fixture(monkeypatch)
        page.evaluate = child.evaluate = guard.parent_reader = forbidden
        assert (await guard.bind_return_navigation(request))[0]
    else:
        guard, page, state, identity, event, session = await response_fixture(
            monkeypatch, "http://127.0.0.1:3300/checkout/return?payment_id=pay_fixture&status=succeeded"
        )
        page.evaluate = guard.parent_reader = forbidden
        await guard.redirect_response(session, event, target_type="page", target_id="target")
        assert "Fetch.continueResponse" in session.calls


@pytest.mark.parametrize("mutation", ["missing", "frame", "loader", "destroyed"])
async def test_provider_return_denies_missing_or_replaced_reviewed_status_document(monkeypatch, mutation):
    guard, page, child, state, identity, request = await provider_fixture(monkeypatch)
    if mutation == "missing":
        guard.checkout_navigation.status_protocol = None
    else:
        read = guard.protocol_reader

        async def changed():
            tree = await read()
            if mutation == "frame":
                tree["frame"]["id"] = "replacement"
            elif mutation == "loader":
                tree["frame"]["loaderId"] = "replacement"
            else:
                tree["frame"] = {}
            return tree

        guard.protocol_reader = changed
    assert not (await guard.bind_return_navigation(request))[0]


async def test_source_observed_only_after_CONFIRM_cannot_authorize_return(monkeypatch):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch)
    guard._confirmed_return_source_bound = False
    assert not (await guard.bind_return_navigation(request))[0]


async def test_OOPIF_native_return_binds_live_separate_session_to_current_main_loader(monkeypatch):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch, oopif=True)

    async def forbidden(*args):
        raise AssertionError("No JavaScript reads in paused OOPIF admission")

    page.evaluate = child.evaluate = guard.parent_reader = forbidden
    admitted, facts = await guard.bind_return_navigation(request)
    assert admitted and all(facts["post_confirm_checks"].values())


@pytest.mark.parametrize(
    "mutation", ["target", "type", "parent", "frame", "loader", "url", "fragment", "destroyed"]
)
async def test_OOPIF_native_return_denies_live_separate_identity_changes(monkeypatch, mutation):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch, oopif=True)
    read = guard.native_protocol_readers["child_fixture"]

    async def changed():
        result = await read()
        frame = result["frameTree"]["frame"]
        if mutation == "target":
            result["targetInfo"]["targetId"] = "replacement"
        elif mutation == "type":
            result["targetInfo"]["type"] = "page"
        elif mutation == "destroyed":
            result["frameTree"]["frame"] = {}
        else:
            key = {
                "parent": "parentId",
                "frame": "id",
                "loader": "loaderId",
                "url": "url",
                "fragment": "urlFragment",
            }[mutation]
            frame[key] = "replacement"
        return result

    guard.native_protocol_readers["child_fixture"] = changed
    assert not (await guard.bind_return_navigation(request))[0]
