"""Public response-coverage contract with external CDP I/O fixtures."""

from types import SimpleNamespace
import pytest
from tryfolk.response_boundary import ResponseBoundary


@pytest.mark.parametrize("oopif", [False, True])
async def test_in_process_frames_use_owned_page_session_and_oopif_uses_its_distinct_target(oopif):
    primary, child = object(), object()
    sessions, attached = [], []

    class Session:
        def __init__(self, identity, kind):
            self.identity, self.kind, self.commands = identity, kind, []

        async def send(self, method, args=None):
            self.commands.append((method, args))
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": self.identity, "type": self.kind}}
            return {}

        def on(self, name, fn):
            pass

        async def detach(self):
            pass

    class Context:
        async def new_cdp_session(self, document):
            attached.append(document)
            if document is child and not oopif:
                raise RuntimeError(
                    "This frame does not have a separate CDP session, it is a part of the parent frame's session"
                )
            session = Session(
                "child" if document is child else "primary", "iframe" if document is child else "page"
            )
            sessions.append(session)
            return session

    guard = SimpleNamespace(primary_page=primary, events=[])
    boundary = ResponseBoundary(Context(), guard)
    assert await boundary.cover(primary)
    assert await boundary.cover(child)
    assert len([s for s in sessions if any(c[0] == "Fetch.enable" for c in s.commands)]) == (
        2 if oopif else 1
    )
    assert any(e["target_type"] == "iframe" for e in guard.events) is oopif


async def test_unknown_child_session_error_never_falls_back_or_claims_coverage():
    class Context:
        async def new_cdp_session(self, document):
            raise RuntimeError("External target unavailable")

    boundary = ResponseBoundary(Context(), SimpleNamespace(primary_page=object(), events=[]))
    with pytest.raises(RuntimeError, match="unavailable"):
        await boundary.cover(object())


async def test_response_boundary_binds_live_primary_frame_tree_without_runtime_evaluation():
    primary = object()
    calls = []
    tree = {"frame": {"id": "main", "loaderId": "document", "url": "fixture"}}

    class Session:
        async def send(self, method, args=None):
            calls.append(method)
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": "primary", "type": "page"}}
            if method == "Page.getFrameTree":
                return {"frameTree": tree}
            assert not method.startswith("Runtime.")
            return {}

        def on(self, name, fn):
            pass

    class Context:
        async def new_cdp_session(self, document):
            return Session()

    guard = SimpleNamespace(primary_page=primary, events=[])
    assert await ResponseBoundary(Context(), guard).cover(primary)
    assert (await guard.protocol_reader())["frame"]["loaderId"] == tree["frame"]["loaderId"]
    tree["frame"]["loaderId"] = "new_document"
    assert (await guard.protocol_reader())["frame"]["loaderId"] == "new_document"
    assert not any(method.startswith("Runtime.") for method in calls)


async def test_response_attestation_tracks_native_child_without_renderer_commands_or_detach_fallback():
    primary, calls, handlers = object(), [], {}
    root = {"id": "primary", "loaderId": "parent", "url": "http://127.0.0.1:3300/"}
    child = {"id": "child", "loaderId": "native", "url": "https://fixture.invalid/", "parentId": "primary"}

    class Session:
        async def send(self, method, args=None):
            calls.append(method)
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": "primary", "type": "page", "url": root["url"]}}
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": root, "childFrames": [{"frame": child}]}}
            return {}

        def on(self, name, callback):
            handlers[name] = callback

    class Context:
        async def new_cdp_session(self, _):
            return Session()

    guard = SimpleNamespace(primary_page=primary, events=[], _native_protocol_target=None)
    boundary = ResponseBoundary(Context(), guard)
    assert await boundary.cover(primary)
    await guard.protocol_reader()
    calls.clear()
    current = await guard.response_protocol_reader()
    assert current["main"]["frameTree"]["childFrames"][0]["frame"] == child
    assert all(method == "Target.getTargetInfo" for method in calls)
    handlers["Page.frameDetached"]({"frameId": "child"})
    current = await guard.response_protocol_reader()
    assert current["main"]["frameTree"]["childFrames"] == []
