"""Real coverage interface with invented CDP events/replies, not Chrome evidence."""

import copy

import pytest

from tryfolk.protocol_identity import FrameIdentityCoverage


class Session:
    def __init__(self, *, iframe=False):
        self.handlers = {}
        self.calls = []
        self.frame = {
            "id": "native" if iframe else "main",
            "loaderId": "committed",
            "url": "https://owned.invalid/current",
        }
        if iframe:
            self.frame["parentId"] = "main"
        self.target = {
            "targetId": self.frame["id"],
            "type": "iframe" if iframe else "page",
            "url": self.frame["url"],
        }
        self.paused = False

    def on(self, event, callback):
        self.handlers[event] = callback

    def emit(self, event, payload):
        self.handlers.get(event, lambda _: None)(payload)

    async def send(self, method, params=None):
        self.calls.append(method)
        if method == "Page.getFrameTree":
            assert not self.paused, "Renderer read forbidden while navigation paused"
            return {"frameTree": {"frame": copy.deepcopy(self.frame)}}
        if method == "Target.getTargetInfo":
            return {"targetInfo": copy.deepcopy(self.target)}
        assert method == "Page.enable"
        return {}


async def fixture(iframe=False):
    session = Session(iframe=iframe)
    coverage = FrameIdentityCoverage(session, session.frame["id"], session.target["type"])
    await coverage.attach()
    await coverage.refresh()
    if not iframe:
        session.emit(
            "Network.requestWillBeSent",
            {
                "type": "Document",
                "frameId": "main",
                "loaderId": "provisional",
                "requestId": "request",
                "request": {"url": "https://owned.invalid/next", "method": "GET"},
            },
        )
    session.paused = True
    return session, coverage


async def test_paused_attestation_uses_live_Target_and_continuously_covered_committed_loader():
    session, coverage = await fixture()
    state = await coverage.attest("https://owned.invalid/next", "GET")
    assert all(state["frameTree"]["frame"][key] == value for key, value in session.frame.items())
    assert session.calls[-2:] == ["Target.getTargetInfo", "Target.getTargetInfo"]


@pytest.mark.parametrize(
    "mutation",
    [
        "gap",
        "detach",
        "closed",
        "loader",
        "same_document",
        "target",
        "type",
        "live_url",
        "destination",
        "request_frame",
        "request_method",
        "missing_loader",
        "replacement_during_fence",
    ],
)
async def test_paused_attestation_rejects_uncertain_or_changed_protocol_identity(mutation):
    session, coverage = await fixture()
    url = "https://owned.invalid/next"
    if mutation == "gap":
        coverage.active = False
    elif mutation == "detach":
        session.emit("Page.frameDetached", {"frameId": "main", "reason": "remove"})
    elif mutation == "closed":
        session.emit("close", {})
    elif mutation == "loader":
        session.emit("Page.frameNavigated", {"frame": {**session.frame, "loaderId": "replacement"}})
    elif mutation == "same_document":
        session.emit(
            "Page.navigatedWithinDocument", {"frameId": "main", "url": session.frame["url"] + "?other=1"}
        )
    elif mutation in ("target", "type", "live_url"):
        session.target[{"target": "targetId", "type": "type", "live_url": "url"}[mutation]] = "replacement"
    elif mutation == "destination":
        url += "?other=1"
    elif mutation in ("request_frame", "request_method", "missing_loader"):
        event = {
            "type": "Document",
            "frameId": "main",
            "loaderId": "provisional",
            "requestId": "request",
            "request": {"url": url, "method": "GET"},
        }
        if mutation == "request_frame":
            event["frameId"] = "other"
        elif mutation == "request_method":
            event["request"]["method"] = "POST"
        else:
            event.pop("loaderId")
        coverage.pending = None
        session.emit("Network.requestWillBeSent", event)
    else:
        send = session.send

        async def replaced(method, params=None):
            result = await send(method, params)
            if method == "Target.getTargetInfo":
                session.emit("Page.frameNavigated", {"frame": {**session.frame, "loaderId": "replacement"}})
            return result

        session.send = replaced
    with pytest.raises(ValueError):
        await coverage.attest(url, "GET")


async def test_native_session_rejects_intervening_document_request_without_commit():
    session, coverage = await fixture(iframe=True)
    assert (await coverage.attest())["frameTree"]["frame"]["id"] == "native"
    session.emit(
        "Network.requestWillBeSent",
        {
            "type": "Document",
            "frameId": "native",
            "loaderId": "new",
            "requestId": "replacement",
            "request": {"url": session.frame["url"], "method": "GET"},
        },
    )
    with pytest.raises(ValueError):
        await coverage.attest()


async def test_canceled_document_download_finishes_request_without_replacing_committed_identity():
    session, coverage = await fixture()
    session.emit(
        "Network.loadingFailed",
        {"requestId": "request", "type": "Document", "canceled": True, "errorText": "net::ERR_ABORTED"},
    )
    observed = await coverage.attest()
    assert observed["frameTree"]["frame"] == {**session.frame, "identityEpoch": 0}
    assert session.calls[-2:] == ["Target.getTargetInfo", "Target.getTargetInfo"]


async def test_failed_request_without_identity_cannot_clear_unknown_pending_navigation():
    session, coverage = await fixture()
    session.emit(
        "Network.requestWillBeSent",
        {"type": "Document", "frameId": "main", "loaderId": "unknown", "request": {}},
    )
    session.emit(
        "Network.loadingFailed",
        {"type": "Document", "canceled": True, "errorText": "net::ERR_ABORTED"},
    )
    with pytest.raises(ValueError, match="intervening navigation"):
        await coverage.attest()


@pytest.mark.parametrize(
    "changes",
    [
        {"requestId": "other"},
        {"requestId": None},
        {"type": "Other"},
        {"canceled": False},
        {"canceled": 1},
        {"errorText": "net::ERR_FAILED"},
    ],
)
async def test_unmatched_or_non_canceled_failure_cannot_end_pending_document(changes):
    session, coverage = await fixture()
    session.emit(
        "Network.loadingFailed",
        {
            "requestId": "request",
            "type": "Document",
            "canceled": True,
            "errorText": "net::ERR_ABORTED",
            **changes,
        },
    )
    with pytest.raises(ValueError, match="intervening navigation"):
        await coverage.attest()
    # The original pending request remains exactly attested for its route.
    await coverage.attest("https://owned.invalid/next", "GET")


@pytest.mark.parametrize("replacement_first", [False, True])
async def test_canceled_old_request_never_clears_a_later_document_navigation(replacement_first):
    session, coverage = await fixture()
    replacement = {
        "type": "Document",
        "frameId": "main",
        "loaderId": "later",
        "requestId": "later",
        "request": {"url": "https://owned.invalid/later", "method": "GET"},
    }
    canceled = {"requestId": "request", "type": "Document", "canceled": True, "errorText": "net::ERR_ABORTED"}
    if replacement_first:
        session.emit("Network.requestWillBeSent", replacement)
        session.emit("Network.loadingFailed", canceled)
    else:
        session.emit("Network.loadingFailed", canceled)
        session.emit("Network.requestWillBeSent", replacement)
    with pytest.raises(ValueError, match="intervening navigation"):
        await coverage.attest()
    await coverage.attest("https://owned.invalid/later", "GET")


@pytest.mark.parametrize("change", ["loader", "url", "epoch", "detached", "target", "closed"])
async def test_canceled_request_never_restores_changed_or_uncovered_committed_document(change):
    session, coverage = await fixture()
    session.emit(
        "Network.loadingFailed",
        {"requestId": "request", "type": "Document", "canceled": True, "errorText": "net::ERR_ABORTED"},
    )
    if change in ("loader", "epoch"):
        frame = {**session.frame, **({"loaderId": "replacement"} if change == "loader" else {})}
        session.emit("Page.frameNavigated", {"frame": frame})
    elif change == "url":
        session.emit(
            "Page.navigatedWithinDocument", {"frameId": "main", "url": session.frame["url"] + "#changed"}
        )
    elif change == "detached":
        session.emit("Page.frameDetached", {"frameId": "main", "reason": "remove"})
    elif change == "target":
        session.target["targetId"] = "other"
    else:
        session.emit("close", {})
    with pytest.raises(ValueError):
        await coverage.attest()


async def test_canceled_request_during_live_target_fence_invalidates_that_attestation():
    session, coverage = await fixture()
    send = session.send

    async def external_reply(method, params=None):
        reply = await send(method, params)
        if method == "Target.getTargetInfo":
            session.emit(
                "Network.loadingFailed",
                {
                    "requestId": "request",
                    "type": "Document",
                    "canceled": True,
                    "errorText": "net::ERR_ABORTED",
                },
            )
        return reply

    session.send = external_reply
    with pytest.raises(ValueError, match="uncertain ordering"):
        await coverage.attest("https://owned.invalid/next", "GET")
    # A subsequent independent read can attest the unchanged original page.
    assert (await coverage.attest())["frameTree"]["frame"] == {**session.frame, "identityEpoch": 0}


async def test_same_fullURL_history_preserves_fragmented_frame_anchor_without_renderer_refresh():
    session = Session()
    session.frame["urlFragment"] = "#owned"
    session.target["url"] += "#owned"
    coverage = FrameIdentityCoverage(session, "main", "page")
    await coverage.attach()
    await coverage.refresh()
    session.emit("Page.navigatedWithinDocument", {"frameId": "main", "url": session.target["url"]})
    session.paused, session.calls = True, []
    observed = await coverage.attest()
    assert observed["frameTree"]["frame"]["url"] == session.frame["url"]
    assert observed["frameTree"]["frame"]["urlFragment"] == "#owned"
    assert session.calls == ["Target.getTargetInfo", "Target.getTargetInfo"]


@pytest.mark.parametrize("event", ["Page.documentOpened", "Inspector.targetCrashed", "Inspector.detached"])
async def test_same_fullURL_history_never_restores_lost_document_coverage(event):
    session, coverage = await fixture(iframe=True)
    session.emit(event, {})
    session.emit("Page.navigatedWithinDocument", {"frameId": "native", "url": session.target["url"]})
    with pytest.raises(ValueError, match="committed"):
        await coverage.attest()
