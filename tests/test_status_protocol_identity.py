"""Real source capture/coverage/admission; disclosed source/CDP/provider I/O fixtures."""

import copy

import pytest

import tryfolk.browser_network as network
import tryfolk.checkout_navigation as navigation
from test_billing_source_transport import FIXTURE_SOURCE, FIXTURE_SOURCE_SHA256, PUBLIC_SOURCE_URL
from test_return_protocol_reader import covered_return
from tryfolk.browser_policy import DODO_RETURN
from tryfolk.browser_settlement import DOCUMENT_STATE


async def captured_status(monkeypatch, *, source=FIXTURE_SOURCE):
    fixture = await covered_return(monkeypatch)
    guard, session = fixture.guard, fixture.primary
    assert (await guard.bind_return_navigation(fixture.request))[0]
    session.paused = False
    frame = {**session.tree["frame"], "url": fixture.request.url, "loaderId": "new-navigation"}
    session.tree = {"frame": frame}
    session.target["url"] = frame["url"]
    fixture.page.main_frame.url = frame["url"]
    await session.emit("Page.frameNavigated", {"frame": frame})

    async def document(script):
        assert not session.paused and script == DOCUMENT_STATE
        return {"url": frame["url"], "generation": 3000, "body_present": True, "ready_state": "complete"}

    fixture.page.evaluate = guard.parent_reader = document
    send = session.send

    async def response(method, params=None):
        if method == "Fetch.getResponseBody":
            assert not session.paused
            return {"body": source.decode(), "base64Encoded": False}
        return await send(method, params)

    session.send = response
    monkeypatch.setattr(navigation, "STATUS_SOURCE_SHA256", FIXTURE_SOURCE_SHA256)
    monkeypatch.setattr(network, "STATUS_SOURCE_SHA256", FIXTURE_SOURCE_SHA256)
    captured = await guard.capture_billing_boundary(session, {
        "requestId": "status-script", "frameId": frame["id"], "resourceType": "Script",
        "request": {"url": PUBLIC_SOURCE_URL, "method": "GET"}, "responseStatusCode": 200,
        "responseHeaders": [{"name": "content-type", "value": "application/javascript"}],
    }, {"target_id": guard.primary_response_target, "target_type": "page"})
    fixture.captured = captured
    fixture.request.url = DODO_RETURN + "/return/Private1"
    return fixture


async def pause_provider(fixture):
    session = fixture.primary
    await session.emit("Network.requestWillBeSent", {
        "type": "Document", "frameId": session.tree["frame"]["id"],
        "loaderId": "provider-navigation", "requestId": "provider-GET",
        "request": {"url": fixture.request.url, "method": "GET"},
    })
    session.paused, session.calls = True, []


@pytest.mark.parametrize("history_events,refresh", [(0, False), (1, False), (1, True), (2, True)])
async def test_captured_status_source_survives_same_fullURL_history_before_provider_GET(
    monkeypatch, history_events, refresh
):
    fixture = await captured_status(monkeypatch)
    assert fixture.captured
    capture = next(e for e in fixture.guard.events if e.get("source_role") == "status_provider_return")
    assert all(capture["binding_checks"].values())
    saved = copy.deepcopy(fixture.guard.checkout_navigation.status_protocol)
    for _ in range(history_events):
        await fixture.primary.emit("Page.navigatedWithinDocument", {
            "frameId": fixture.primary.tree["frame"]["id"],
            "url": fixture.primary.target["url"], "navigationType": "historyApi",
        })
    if refresh:
        await fixture.guard.protocol_frames()  # Fresh observation from a later script capture.
    await pause_provider(fixture)
    current = (await fixture.guard.current_return_frames(fixture.request))["main"]
    assert all(current[key] == saved[key] for key in ("id", "loader_id", "url"))
    admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
    assert admitted and all(facts["provider_return_checks"].values())
    assert fixture.primary.calls and set(fixture.primary.calls) == {"Target.getTargetInfo"}


@pytest.mark.parametrize("mutation", ["url", "fragment", "restored_url", "loader", "recommit", "source", "account"])
async def test_history_correction_keeps_real_status_change_and_ownership_denied(monkeypatch, mutation):
    fixture = await captured_status(monkeypatch)
    assert fixture.captured
    session = fixture.primary
    frame = copy.deepcopy(session.tree["frame"])
    if mutation in ("url", "fragment", "restored_url"):
        changed = frame["url"] + ("#changed" if mutation == "fragment" else "?changed=1")
        await session.emit("Page.navigatedWithinDocument", {"frameId": frame["id"], "url": changed})
        if mutation == "restored_url":
            await session.emit("Page.navigatedWithinDocument", {"frameId": frame["id"], "url": frame["url"]})
        else:
            frame["url"] = changed
    elif mutation in ("loader", "recommit"):
        if mutation == "loader":
            frame["loaderId"] = "replacement-loader"
        await session.emit("Page.frameNavigated", {"frame": frame})
    elif mutation == "source":
        fixture.guard.checkout_navigation.status_source_generation = None
    else:
        fixture.identity["ownership"]["account_id"] = "unowned"
    session.tree, session.target["url"] = {"frame": frame}, frame["url"]
    await fixture.guard.protocol_frames()
    await pause_provider(fixture)
    admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
    assert not admitted and facts["first_failed_predicate"]
    assert fixture.guard.policy._return_transport_url is None
    assert set(session.calls) == {"Target.getTargetInfo"}


async def test_changed_source_bytes_never_establish_status_authority(monkeypatch):
    fixture = await captured_status(monkeypatch, source=b"/* changed unapproved fixture */")
    assert not fixture.captured
    assert fixture.guard.checkout_navigation.status_source_generation is None
    await pause_provider(fixture)
    admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
    assert not admitted and facts["first_failed_predicate"] == "current_status_source_bound"


async def test_same_URL_event_during_independent_identity_still_breaks_ordering_fence(monkeypatch):
    fixture = await captured_status(monkeypatch)
    assert fixture.captured
    verify = fixture.guard.frame_verify

    async def intervening(context):
        await fixture.primary.emit("Page.navigatedWithinDocument", {
            "frameId": fixture.primary.tree["frame"]["id"], "url": fixture.primary.target["url"],
        })
        return await verify(context)

    fixture.guard.frame_verify = intervening
    await pause_provider(fixture)
    admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
    assert not admitted
    assert facts["provider_return_checks"]["current_status_source_bound"] is True
    assert facts["first_failed_predicate"] == "same_status_document_during_identity"
