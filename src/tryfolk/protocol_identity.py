"""Private continuous CDP document coverage with live browser-side fences.

Renderer Page commands can wait behind a paused location navigation. Admission
uses the covered committed document and actual Document request events, fenced
by fresh browser Target replies. No DOM value or timeout fallback grants it.
"""
import copy


class FrameIdentityCoverage:
    def __init__(self, session, target_id, target_type):
        self.session, self.target_id, self.target_type = session, target_id, target_type
        self.active = False
        self.frames = {}
        self.root_id = None
        self.pending = None
        self.revision = self.document_revision = 0
        self.anchor = None
        self.anchor_document_revision = None

    async def attach(self):
        self.session.on("Page.frameNavigated", self.navigated)
        self.session.on("Page.navigatedWithinDocument", self.within_document)
        self.session.on("Page.frameDetached", self.detached)
        self.session.on("Page.documentOpened", lambda _: self.invalidate())
        self.session.on("Network.requestWillBeSent", self.requested)
        self.session.on("Inspector.detached", lambda _: self.invalidate())
        self.session.on("Inspector.targetCrashed", lambda _: self.invalidate())
        self.session.on("close", lambda *_: self.invalidate())
        await self.session.send("Page.enable")
        self.active = True

    def invalidate(self):
        self.active = False
        self.revision += 1
        self.frames.clear()
        self.pending = None

    def navigated(self, event):
        frame = event.get("frame", {})
        if not frame.get("id"):
            self.invalidate()
            return
        self.frames[frame["id"]] = copy.deepcopy(frame)
        if frame["id"] in (self.root_id, self.target_id):
            self.document_revision += 1
        if frame["id"] == self.root_id and self.pending and frame.get("loaderId") == self.pending.get("loaderId"):
            self.pending = None
        self.revision += 1

    def within_document(self, event):
        frame = self.frames.get(event.get("frameId"))
        if frame is not None:
            frame["url"], frame["urlFragment"] = event.get("url"), ""
        if event.get("frameId") in (self.root_id, self.target_id):
            self.document_revision += 1
        self.revision += 1

    def detached(self, event):
        self.frames.pop(event.get("frameId"), None)
        if event.get("frameId") in (self.root_id, self.target_id):
            self.document_revision += 1
        self.revision += 1

    def requested(self, event):
        if event.get("type") == "Document" and event.get("frameId") == self.root_id:
            self.pending = {key: copy.deepcopy(event.get(key)) for key in ("frameId", "loaderId", "requestId", "request")}
            self.revision += 1

    @staticmethod
    def full_url(frame):
        url, fragment = frame.get("url"), frame.get("urlFragment", "")
        if not isinstance(url, str) or not isinstance(fragment, str) or fragment and not fragment.startswith("#"):
            return None
        return url + fragment

    async def refresh(self):
        """Fresh renderer observation, only outside paused main navigation."""
        if not self.active:
            raise ValueError("Protocol identity coverage is inactive.")
        revision = self.revision
        tree = (await self.session.send("Page.getFrameTree"))["frameTree"]
        root = tree.get("frame", {})
        current = self.frames.get(root.get("id"))
        if revision != self.revision and current != root:
            raise ValueError("Protocol document changed during fresh observation.")
        if not root.get("id") or not root.get("loaderId") or not self.full_url(root):
            raise ValueError("Fresh protocol document has incomplete identity.")
        self.root_id = root["id"]
        self.frames[self.root_id] = copy.deepcopy(root)
        for node in tree.get("childFrames", []):
            frame = node.get("frame", {})
            if frame.get("id"):
                self.frames[frame["id"]] = copy.deepcopy(frame)
        self.anchor, self.anchor_document_revision = copy.deepcopy(root), self.document_revision
        tree = copy.deepcopy(tree)
        tree["frame"]["identityEpoch"] = self.document_revision
        return tree

    def committed(self):
        frame = self.frames.get(self.root_id, {})
        if not (self.active and frame.get("id") == self.target_id
                and frame.get("loaderId") and self.full_url(frame)
                and frame == self.anchor and self.document_revision == self.anchor_document_revision):
            raise ValueError("Exact committed protocol document is unavailable.")
        return frame

    async def attest(self, destination=None, method=None):
        """No renderer read. Require live target + uninterrupted exact events."""
        revision = self.revision
        frame = copy.deepcopy(self.committed())
        pending = copy.deepcopy(self.pending)
        if destination is None:
            if pending:
                raise ValueError("Native document has an intervening navigation.")
        elif not (pending and pending.get("frameId") == frame["id"]
                  and isinstance(pending.get("loaderId"), str) and pending["loaderId"]
                  and pending["loaderId"] != frame["loaderId"]
                  and pending.get("requestId") and pending.get("request", {}).get("url") == destination
                  and pending["request"].get("method") == method == "GET"
                  and not pending["request"].get("postData")):
            raise ValueError("Paused Document request is not exactly covered.")
        target = None
        for _ in range(2):
            target = (await self.session.send("Target.getTargetInfo"))["targetInfo"]
            if not (revision == self.revision and frame == self.committed() and pending == self.pending
                    and target.get("targetId") == self.target_id and target.get("type") == self.target_type
                    and target.get("url") == self.full_url(frame)):
                raise ValueError("Live Target/document identity changed or has uncertain ordering.")
        frame["identityEpoch"] = self.document_revision
        return {"frameTree": {"frame": frame}, "targetInfo": target}
