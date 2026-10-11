"""Actual paused-return reader/admission with invented CDP and provider I/O."""

import copy
import inspect
from types import SimpleNamespace

import pytest

from test_return_navigation import return_fixture
from tryfolk.response_boundary import ResponseBoundary


async def covered_return(monkeypatch, *, oopif=False):
    guard, page, child, state, identity, request = await return_fixture(monkeypatch, oopif=oopif)
    # Seed continuous coverage from a fresh external CDP observation, before
    # the navigation pauses. Admission below uses the real reader and guard.
    tree = await guard.protocol_reader()
    if oopif:
        tree["childFrames"] = [{"frame": (await guard.native_protocol_readers["child_fixture"]())[
            "frameTree"]["frame"]}]
    root, native = tree["frame"], tree["childFrames"][0]["frame"]
    native["parentId"] = root["id"]
    if "#" in native["url"]:
        native["url"], fragment = native["url"].split("#", 1)
        native["urlFragment"] = "#" + fragment

    class Session:
        def __init__(self, document, kind):
            self.tree = copy.deepcopy(document)
            self.target = {"targetId": document["frame"]["id"], "type": kind,
                           "url": document["frame"]["url"] + document["frame"].get("urlFragment", "")}
            if kind == "iframe":
                self.target["parentFrameId"] = root["id"]
            self.handlers, self.calls, self.paused = {}, [], False
            self.on_target = None

        def on(self, event, callback):
            self.handlers.setdefault(event, []).append(callback)

        async def emit(self, event, value):
            for callback in self.handlers.get(event, []):
                result = callback(copy.deepcopy(value))
                if inspect.isawaitable(result):
                    await result

        async def send(self, method, args=None):
            self.calls.append(method)
            if self.paused:
                assert method == "Target.getTargetInfo", "No renderer, Page or DOM command while paused"
            if method == "Target.getTargetInfo":
                if self.on_target:
                    callback, self.on_target = self.on_target, None
                    await callback()
                return {"targetInfo": copy.deepcopy(self.target)}
            if method == "Page.getFrameTree":
                return {"frameTree": copy.deepcopy(self.tree)}
            return {}

        async def detach(self):
            pass

    primary_session = Session(tree, "page")
    native_session = Session({"frame": native}, "iframe") if oopif else None

    class Context:
        async def new_cdp_session(self, document):
            return native_session if document is child and oopif else primary_session

    boundary = ResponseBoundary(Context(), guard)
    assert await boundary.cover(page)
    if oopif:
        assert await boundary.cover(child)
    guard._armed_protocol = await guard.protocol_frames()
    assert await guard.capture_confirm_protocol(await guard.parent_document())
    await primary_session.emit("Network.requestWillBeSent", {
        "type": "Document", "frameId": root["id"], "loaderId": "new-navigation",
        "requestId": "paused-GET", "request": {"url": request.url, "method": "GET"},
    })
    for session in (primary_session, native_session):
        if session:
            session.paused, session.calls = True, []

    async def forbidden(*_):
        raise AssertionError("No JavaScript or cached DOM fallback during paused return")

    page.evaluate = child.evaluate = guard.parent_reader = forbidden
    return SimpleNamespace(guard=guard, boundary=boundary, primary=primary_session,
                           native_session=native_session, native=copy.deepcopy(native), request=request)


@pytest.mark.parametrize("oopif", [False, True])
async def test_actual_return_reader_admits_exact_covered_child_and_deduplicates_OOPIF(monkeypatch, oopif):
    fixture = await covered_return(monkeypatch, oopif=oopif)
    current = await fixture.guard.return_protocol_reader(fixture.request)
    assert current["main"]["frameTree"]["childFrames"][0]["frame"] == fixture.native
    frames = await fixture.guard.current_return_frames(fixture.request)
    assert len(frames["children"]) == 1
    assert frames["children"][0]["id"] == fixture.native["id"]
    admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
    assert admitted and all(facts["post_confirm_checks"].values())
    assert fixture.primary.calls and all(c == "Target.getTargetInfo" for c in fixture.primary.calls)
    if oopif:
        assert all(c == "Target.getTargetInfo" for c in fixture.native_session.calls)


@pytest.mark.parametrize("mutation", ["missing", "replaced", "frame_id", "detached", "ambiguous", "parent", "full_url"])
async def test_actual_return_reader_never_grants_missing_or_changed_child(monkeypatch, mutation):
    fixture = await covered_return(monkeypatch)
    session, frame = fixture.primary, fixture.native
    if mutation == "missing":
        await session.emit("Page.frameDetached", {"frameId": frame["id"], "reason": "remove"})
    elif mutation == "detached":
        await session.emit("Inspector.detached", {"reason": "fixture coverage lost"})
    else:
        if mutation == "replaced":
            frame["loaderId"] = "replacement"
        elif mutation == "frame_id":
            await session.emit("Page.frameDetached", {"frameId": frame["id"], "reason": "remove"})
            frame["id"] = "replacement-native"
        elif mutation == "ambiguous":
            frame["id"] = "second-native"
        elif mutation == "parent":
            frame["parentId"] = "unrelated-parent"
        else:
            frame["urlFragment"] += "&unreviewed=true"
        await session.emit("Page.frameNavigated", {"frame": frame})
    if mutation == "detached":
        with pytest.raises(ValueError, match="committed"):
            await fixture.guard.bind_return_navigation(fixture.request)
    else:
        admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
        assert not admitted and facts["first_failed_predicate"] in (
            "single_current_native_child", "frozen_current_child")
    assert all(c == "Target.getTargetInfo" for c in session.calls)


async def test_actual_return_reader_denies_child_map_change_during_live_Target_fences(monkeypatch):
    fixture = await covered_return(monkeypatch)

    async def change():
        await fixture.primary.emit("Page.frameNavigated", {
            "frame": {**fixture.native, "loaderId": "replacement"}})

    fixture.primary.on_target = change
    with pytest.raises(ValueError, match="changed|ordering"):
        await fixture.guard.return_protocol_reader(fixture.request)


async def test_return_denies_child_replacement_restored_during_independent_identity_read(monkeypatch):
    fixture = await covered_return(monkeypatch)
    verify = fixture.guard.frame_verify

    async def changed(context):
        await fixture.primary.emit("Page.frameNavigated", {
            "frame": {**fixture.native, "loaderId": "temporary-replacement"}})
        await fixture.primary.emit("Page.frameNavigated", {"frame": fixture.native})
        return await verify(context)

    fixture.guard.frame_verify = changed
    admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
    assert not admitted
    assert facts["post_confirm_checks"]["single_current_native_child"] is True
    assert facts["post_confirm_checks"]["frozen_current_child"] is True
    assert facts["first_failed_predicate"] == "same_current_documents_during_identity"


@pytest.mark.parametrize("mutation", ["destination", "POST", "old_loader", "missing_loader", "body"])
async def test_actual_return_reader_requires_exact_pending_GET_and_distinct_loader(monkeypatch, mutation):
    fixture = await covered_return(monkeypatch)
    coverage = fixture.boundary.coverages[fixture.guard.primary_response_target]
    pending = copy.deepcopy(coverage.pending)
    if mutation == "destination":
        pending["request"]["url"] += "?unapproved=1"
    elif mutation == "POST":
        pending["request"]["method"] = "POST"
    elif mutation == "old_loader":
        pending["loaderId"] = coverage.anchor["loaderId"]
    elif mutation == "missing_loader":
        pending.pop("loaderId")
    else:
        pending["request"]["postData"] = "unapproved"
    await fixture.primary.emit("Network.requestWillBeSent", {"type": "Document", **pending})
    with pytest.raises(ValueError, match="exactly covered"):
        await fixture.guard.return_protocol_reader(fixture.request)


@pytest.mark.parametrize("mutation", ["loader", "parent", "target", "type"])
async def test_return_keeps_separate_OOPIF_validation_and_dedup_strict(monkeypatch, mutation):
    fixture = await covered_return(monkeypatch, oopif=True)
    if mutation == "loader":
        await fixture.primary.emit("Page.frameNavigated", {
            "frame": {**fixture.native, "loaderId": "conflicting-coverage"}})
    elif mutation == "parent":
        fixture.native_session.target["parentFrameId"] = "unrelated-parent"
    else:
        fixture.native_session.target["targetId" if mutation == "target" else "type"] = "unrelated"
    if mutation in ("target", "type"):
        with pytest.raises(ValueError, match="identity changed"):
            await fixture.guard.bind_return_navigation(fixture.request)
    else:
        admitted, facts = await fixture.guard.bind_return_navigation(fixture.request)
        assert not admitted and facts["first_failed_predicate"] in (
            "single_current_native_child", "frozen_current_child")


async def test_return_reader_brackets_OOPIF_revision_through_final_primary_fence(monkeypatch):
    fixture = await covered_return(monkeypatch, oopif=True)
    send = fixture.primary.send
    replies = 0

    async def changed(method, args=None):
        nonlocal replies
        result = await send(method, args)
        if method == "Target.getTargetInfo":
            replies += 1
            if replies == 3:
                await fixture.native_session.emit("Page.frameNavigated", {"frame": fixture.native})
        return result

    fixture.primary.send = changed
    with pytest.raises(ValueError, match="Native protocol identity changed across primary"):
        await fixture.guard.return_protocol_reader(fixture.request)
