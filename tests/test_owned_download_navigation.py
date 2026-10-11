"""Invented browser/provider/session inputs; never live payment or access proof."""

import copy
import json
from types import SimpleNamespace

import pytest
from test_return_navigation import response_fixture


async def owned_fixture(monkeypatch):
    origin = "http://127.0.0.1:3300"
    returned = origin + "/checkout/return?payment_id=pay_fixture&status=succeeded"
    guard, page, state, identity, event, session = await response_fixture(monkeypatch, returned)
    await guard.redirect_response(session, event, target_type="page", target_id="target")
    assert guard.card_attempt._admitted_owned_redirect == returned
    page.main_frame.url = returned
    state["parent_generation"] = 4000
    body = {
        "ownership": copy.deepcopy(identity["ownership"]),
        "has_access": True,
        "orders": [
            {
                "order_id": identity["order_id"],
                "checkout_session_id": identity["checkout_session_id"],
                "payment_id": identity["payment_id"],
                "active": True,
                "granted": True,
                **identity["ownership"],
            }
        ],
    }
    authenticated = {"http_status": 200, "same_origin_no_redirect": True, "body": body}

    async def app_session():
        return copy.deepcopy(authenticated)

    guard.owned_session_reader = app_session
    calls = []
    old_verify = guard.frame_verify

    async def verify(context):
        calls.append(context["operation"])
        return await old_verify(context)

    guard.frame_verify = verify
    return guard, page, state, identity, authenticated, calls


def navigation(page, destination):
    async def headers():
        return {}

    return SimpleNamespace(
        url=destination,
        method="GET",
        resource_type="document",
        post_data=None,
        redirected_from=None,
        frame=page.main_frame,
        is_navigation_request=lambda: True,
        all_headers=headers,
    )


class Route:
    def __init__(self, request):
        self.request = request
        self.continued = False
        self.aborted = False

    async def continue_(self):
        self.continued = True

    async def abort(self, _):
        self.aborted = True


async def test_owned_confirmed_return_can_reach_page_then_one_protected_download(monkeypatch):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    page_url = guard.policy.origin + "/special-downloads"
    # Current defect: registered origin/native allowlist alone cannot pass this route.
    original = Route(navigation(page, page_url))
    await guard.route(original)
    assert original.aborted and not original.continued
    await guard.prepare_owned_navigation(page_url)
    route = Route(navigation(page, page_url))
    await guard.route(route)
    assert route.continued and not route.aborted
    page.main_frame.url = page_url
    state["parent_generation"] = 5000
    download = guard.policy.origin + "/api/target/download?artwork=0"
    await guard.prepare_owned_navigation(download)
    route = Route(navigation(page, download))
    await guard.route(route)
    assert route.continued and not route.aborted
    reused = Route(navigation(page, download))
    await guard.route(reused)
    assert reused.aborted and not reused.continued
    assert calls == ["poll", "poll"]
    assert guard.card_attempt.confirmed and guard.card_attempt.closed
    assert not guard.card_attempt.consume("confirm") and not guard.card_attempt.consume("poll")


@pytest.mark.parametrize(
    "mutation",
    [
        "unconfirmed",
        "native_processing",
        "no_owned_redirect",
        "owned_redirect_not_committed",
        "changed_source",
        "wrong_payment",
        "wrong_checkout",
        "wrong_order",
        "wrong_account",
        "wrong_revision",
        "wrong_customer",
        "live",
        "unapproved",
        "unpaid",
        "wrong_link",
        "session_401",
        "session_redirect",
        "session_account",
        "session_order",
        "session_checkout",
        "session_payment",
        "session_revision",
        "session_inactive",
        "session_ambiguous",
        "session_missing",
        "provider_changes_document",
        "session_changes_document",
        "foreign",
        "arbitrary",
        "query",
        "fragment",
    ],
)
async def test_owned_navigation_requires_current_paid_identity_session_source_and_exact_destination(
    monkeypatch, mutation
):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    destination = guard.policy.origin + "/special-downloads"
    if mutation == "unconfirmed":
        guard.card_attempt.confirmed = False
    elif mutation == "native_processing":
        guard.card_attempt.response_status = "processing"
    elif mutation == "no_owned_redirect":
        guard.card_attempt._admitted_owned_redirect = None
    elif mutation in ("owned_redirect_not_committed", "changed_source"):
        page.main_frame.url = guard.policy.origin + "/elsewhere"
    elif mutation.startswith("wrong_"):
        key = {
            "wrong_payment": "payment_id",
            "wrong_checkout": "checkout_session_id",
            "wrong_order": "order_id",
            "wrong_customer": "customer_correlated",
            "wrong_link": "payment_link",
        }.get(mutation)
        if key:
            identity[key] = False if key == "customer_correlated" else "other"
        else:
            identity["ownership"]["account_id" if mutation == "wrong_account" else "target_revision"] = (
                "other"
            )
    elif mutation == "live":
        identity["provider_environment"] = "live_mode"
    elif mutation == "unapproved":
        identity["approved"] = False
    elif mutation == "unpaid":
        identity["payment_status"] = "processing"
    elif mutation == "session_401":
        session["http_status"] = 401
    elif mutation == "session_redirect":
        session["same_origin_no_redirect"] = False
    elif mutation == "session_account":
        session["body"]["ownership"]["account_id"] = "other"
    elif mutation == "session_order":
        session["body"]["orders"][0]["order_id"] = "other"
    elif mutation == "session_checkout":
        session["body"]["orders"][0]["checkout_session_id"] = "other"
    elif mutation == "session_payment":
        session["body"]["orders"][0]["payment_id"] = "other"
    elif mutation == "session_revision":
        session["body"]["orders"][0]["target_revision"] = "other"
    elif mutation == "session_inactive":
        session["body"]["orders"][0]["active"] = False
    elif mutation == "session_ambiguous":
        session["body"]["orders"] *= 2
    elif mutation == "session_missing":
        guard.owned_session_reader = None
    elif mutation == "provider_changes_document":
        verify = guard.frame_verify

        async def changed(context):
            result = await verify(context)
            state["parent_generation"] = 9000
            return result

        guard.frame_verify = changed
    elif mutation == "session_changes_document":
        read = guard.owned_session_reader

        async def changed():
            result = await read()
            state["parent_generation"] = 9000
            return result

        guard.owned_session_reader = changed
    elif mutation == "foreign":
        destination = destination.replace("3300", "3301")
    elif mutation == "arbitrary":
        destination = guard.policy.origin + "/admin"
    elif mutation == "query":
        destination += "?ignored=1"
    else:
        destination += "#ignored"
    with pytest.raises(ValueError):
        await guard.prepare_owned_navigation(destination)
    if mutation != "unconfirmed":
        route = Route(navigation(page, destination))
        await guard.route(route)
        assert route.aborted and not route.continued


@pytest.mark.parametrize(
    "mutation", ["method", "body", "child", "redirect", "changed_document", "expired", "other_destination"]
)
async def test_single_prepared_owned_navigation_cannot_be_rebound_or_used_after_document_change(
    monkeypatch, mutation
):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    destination = guard.policy.origin + "/special-downloads"
    await guard.prepare_owned_navigation(destination)
    request = navigation(page, destination)
    if mutation == "method":
        request.method = "POST"
    elif mutation == "body":
        request.post_data = "{}"
    elif mutation == "child":
        request.frame = SimpleNamespace(page=page, parent_frame=page.main_frame, url=page.main_frame.url)
    elif mutation == "redirect":
        request.redirected_from = object()
    elif mutation == "changed_document":
        state["parent_generation"] = 9000
    elif mutation == "expired":
        guard.card_attempt.clock = lambda: 10**20
    else:
        request.url += "?extra=1"
    route = Route(request)
    await guard.route(route)
    assert route.aborted and not route.continued


async def test_ungranted_account_can_view_owned_page_but_cannot_prepare_protected_download(monkeypatch):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    session["body"]["has_access"] = False
    session["body"]["orders"][0]["granted"] = False
    destination = guard.policy.origin + "/special-downloads"
    await guard.prepare_owned_navigation(destination)
    route = Route(navigation(page, destination))
    await guard.route(route)
    assert route.continued
    page.main_frame.url = destination
    state["parent_generation"] = 5000
    with pytest.raises(ValueError):
        await guard.prepare_owned_navigation(guard.policy.origin + "/api/target/download?artwork=0")


@pytest.mark.parametrize("body", [None, [], "unexpected", {"ownership": {}, "orders": "unexpected"}])
async def test_malformed_authenticated_app_response_is_denied(monkeypatch, body):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    session["body"] = body
    with pytest.raises(ValueError):
        await guard.prepare_owned_navigation(guard.policy.origin + "/special-downloads")
    assert guard.events[-1]["decision"] == "deny_owned_navigation_prepare"
    assert guard.events[-1]["first_failed_predicate"]


@pytest.mark.parametrize("resource_type,allowed", [("other", True), ("fetch", False), ("xhr", False)])
async def test_html_download_request_also_requires_single_authenticated_preparation(
    monkeypatch, resource_type, allowed
):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    page_url = guard.policy.origin + "/special-downloads"
    await guard.prepare_owned_navigation(page_url)
    initial = Route(navigation(page, page_url))
    await guard.route(initial)
    page.main_frame.url = page_url
    state["parent_generation"] = 5000
    destination = guard.policy.origin + "/api/target/download?artwork=0"
    # An unprepared non-navigation download must not inherit the origin grant.
    request = navigation(page, destination)
    request.resource_type = resource_type
    request.is_navigation_request = lambda: False
    unprepared = Route(request)
    await guard.route(unprepared)
    assert unprepared.aborted
    await guard.prepare_owned_navigation(destination)
    route = Route(request)
    await guard.route(route)
    assert route.continued is allowed and route.aborted is not allowed


@pytest.mark.parametrize(
    "query", ["artwork=12", "artwork=-1", "artwork=0&extra=1", "artwork=00", "%61rtwork=0"]
)
async def test_non_navigation_download_cannot_bypass_exact_destination_bounds(monkeypatch, query):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    request = navigation(page, guard.policy.origin + "/api/target/download?" + query)
    request.resource_type = "other"
    request.is_navigation_request = lambda: False
    route = Route(request)
    await guard.route(route)
    assert route.aborted and not route.continued


@pytest.mark.parametrize(
    "mutation",
    [None, "unprepared", "foreign_url", "other_artwork", "foreign_page", "changed_document", "expired"],
)
async def test_actual_download_event_consumes_exact_fresh_preparation_before_file_save(monkeypatch, mutation):
    guard, page, state, identity, session, calls = await owned_fixture(monkeypatch)
    destination = guard.policy.origin + "/api/target/download?artwork=0"
    if mutation != "unprepared":
        await guard.prepare_owned_navigation(destination)
    download = SimpleNamespace(page=page, url=destination, cancelled=False)

    async def cancel():
        download.cancelled = True

    download.cancel = cancel
    if mutation == "foreign_url":
        download.url = destination.replace("3300", "3301")
    elif mutation == "other_artwork":
        download.url = destination.replace("artwork=0", "artwork=1")
    elif mutation == "foreign_page":
        download.page = object()
    elif mutation == "changed_document":
        state["parent_generation"] = 9000
    elif mutation == "expired":
        guard.card_attempt.clock = lambda: 10**20
    if mutation:
        with pytest.raises(ValueError):
            await guard.admit_owned_download(download)
        assert download.cancelled and guard.events[-1]["first_failed_predicate"]
    else:
        await guard.admit_owned_download(download)
        assert not download.cancelled and guard.events[-1]["decision"] == "owned_download_event"
        with pytest.raises(ValueError):
            await guard.admit_owned_download(download)
        assert download.cancelled


async def test_download_evidence_retains_the_actual_authenticated_order_and_selected_artwork(monkeypatch):
    guard, page, _, identity, session, calls = await owned_fixture(monkeypatch)
    session["body"]["orders"][0]["expected_product_id"] = "owned-product"
    destination = guard.policy.origin + "/api/target/download?artwork=2"
    await guard.prepare_owned_navigation(destination)
    download = SimpleNamespace(page=page, url=destination)
    await guard.admit_owned_download(download)
    assert guard.events[-1]["download_binding"] == {
        **identity["ownership"],
        **{key: identity[key] for key in ("order_id", "checkout_session_id", "payment_id")},
        "expected_product_id": "owned-product", "artwork_id": 2, "download_url": destination,
    }
    assert calls == ["poll"]  # Existing independent identity read, no additional provider call.


@pytest.mark.parametrize("field", ["id", "loaderId", "url", "identityEpoch"])
async def test_download_document_failure_retains_closed_per_field_comparisons(monkeypatch, field):
    guard, page, _state, _identity, _session, _calls = await owned_fixture(monkeypatch)
    reader = guard.protocol_reader
    changed = {}

    async def external_protocol_reply():
        tree = await reader()
        tree["frame"].update({"identityEpoch": 0, **changed})
        return tree

    guard.protocol_reader = external_protocol_reply
    destination = guard.policy.origin + "/api/target/download?artwork=0"
    await guard.prepare_owned_navigation(destination)
    changed[field] = 1 if field == "identityEpoch" else "private-other-document"
    download = SimpleNamespace(page=page, url=destination, cancelled=False)

    async def cancel():
        download.cancelled = True

    download.cancel = cancel
    with pytest.raises(ValueError):
        await guard.admit_owned_download(download)
    event = guard.events[-1]
    assert download.cancelled
    assert event["first_failed_predicate"] == "download_source_document_continuously_current"
    comparisons = event["owned_document_comparisons"]
    key = {"loaderId": "loader_id", "identityEpoch": "identity_epoch"}.get(field, field)
    for pair in ("before_prepared", "after_prepared"):
        assert comparisons[pair]["both_available"]
        assert comparisons[pair][key] == {"both_present": True, "equal": False}
    assert all(
        value["equal"] for key, value in comparisons["before_after"].items() if key != "both_available"
    )
    assert event["error_type"] is None
    encoded = json.dumps(event)
    assert not any(
        value in encoded for value in (destination, "main_fixture", "main_4000", "private-other-document")
    )


@pytest.mark.parametrize("failed_read", [1, 2])
async def test_download_attestation_exception_keeps_type_and_available_closed_comparisons(
    monkeypatch, failed_read
):
    guard, page, _state, _identity, _session, _calls = await owned_fixture(monkeypatch)
    destination = guard.policy.origin + "/api/target/download?artwork=0"
    await guard.prepare_owned_navigation(destination)
    reader = guard.return_protocol_reader
    reads = 0

    async def external_protocol_reply(request):
        nonlocal reads
        reads += 1
        if reads == failed_read:
            raise ValueError("private-url-and-token-must-not-be-recorded")
        return await reader(request)

    guard.return_protocol_reader = external_protocol_reply
    download = SimpleNamespace(page=page, url=destination, cancelled=False)

    async def cancel():
        download.cancelled = True

    download.cancel = cancel
    with pytest.raises(ValueError):
        await guard.admit_owned_download(download)
    event = guard.events[-1]
    assert download.cancelled
    assert event["first_failed_predicate"] == "download_source_document_continuously_current"
    assert event["error_type"] == "ValueError"
    comparisons = event["owned_document_comparisons"]
    assert comparisons["before_prepared"]["both_available"] is (failed_read == 2)
    assert comparisons["after_prepared"]["both_available"] is False
    assert comparisons["before_after"]["both_available"] is False
    assert "private-url-and-token" not in json.dumps(event)
