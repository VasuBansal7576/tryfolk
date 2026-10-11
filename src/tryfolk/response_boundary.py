"""Response interception on actual page and OOPIF targets, before private GET."""

import asyncio
import copy
from .secure_frame import BROWSER_API
from .native_preflight import NativePreflightGate
from .protocol_identity import FrameIdentityCoverage


class ResponseBoundary:
    def __init__(self, context, guard):
        self.context, self.guard = context, guard
        self.targets = {}
        self.coverages = {}
        self.frame_targets = {}
        guard.select_native_protocol = self.select_native_protocol
        guard.return_protocol_reader = self.return_protocol_reader
        guard.response_protocol_reader = self.response_protocol_reader
        if not hasattr(guard, "native_protocol_readers"):
            guard.native_protocol_readers = {}
        self.lock = asyncio.Lock()
        self.preflights = NativePreflightGate(guard, BROWSER_API)
        guard.preflight_guard = self.preflights.wait

    async def select_native_protocol(self, document):
        if not await self.cover(document):
            raise ValueError("Native document has no continuous protocol coverage.")
        observed, target_id, target_type = self.frame_targets[id(document)]
        if observed is not document:
            raise ValueError("Native document protocol registration changed.")
        return target_id if target_type == "iframe" else None

    async def return_protocol_reader(self, request):
        primary = self.coverages.get(self.guard.primary_response_target)
        if not primary:
            raise ValueError("Primary protocol coverage is unavailable.")
        revision, frames = primary.revision, copy.deepcopy(primary.frames)
        before = await primary.attest(request.url, request.method)
        native = None
        native_revision = None
        main_url = FrameIdentityCoverage.full_url(before["frameTree"]["frame"])
        capability = self.guard.card_attempt
        if capability and main_url == capability.parent_url and self.guard._native_protocol_target:
            coverage = self.coverages.get(self.guard._native_protocol_target)
            if not coverage:
                raise ValueError("Selected native protocol coverage is unavailable.")
            native_revision = coverage.revision
            native = await coverage.attest()
        after = await primary.attest(request.url, request.method)
        if before != after or revision != primary.revision or frames != primary.frames:
            raise ValueError("Primary protocol identity changed across native attestation.")
        if native is not None and native_revision != coverage.revision:
            raise ValueError("Native protocol identity changed across primary attestation.")
        root_id = before["frameTree"]["frame"]["id"]
        before["frameTree"]["childFrames"] = [
            {"frame": frame} for frame in frames.values() if frame.get("parentId") == root_id
        ]
        return {"main": before, "native": native,
                "protocol_revision": {"main": revision, "native": native_revision}}

    async def response_protocol_reader(self):
        """Live browser Target fences and exact continuous document events only."""
        primary = self.coverages.get(self.guard.primary_response_target)
        if not primary:
            raise ValueError("Primary protocol coverage is unavailable.")
        revision, frames = primary.revision, copy.deepcopy(primary.frames)
        before = await primary.attest()
        native = None
        if self.guard._native_protocol_target:
            coverage = self.coverages.get(self.guard._native_protocol_target)
            if not coverage:
                raise ValueError("Selected native protocol coverage is unavailable.")
            native = await coverage.attest()
        after = await primary.attest()
        if before != after or revision != primary.revision or frames != primary.frames:
            raise ValueError("Protocol documents changed across response attestation.")
        root_id = before["frameTree"]["frame"]["id"]
        before["frameTree"]["childFrames"] = [
            {"frame": frame} for frame in frames.values() if frame.get("parentId") == root_id
        ]
        return {"main": before, "native": native}

    async def cover(self, document):
        async with self.lock:
            try:
                session = await self.context.new_cdp_session(document)
            except Exception as error:
                if "This frame does not have a separate CDP session" not in str(error):
                    raise
                # The documented Frame API reports this exact condition for
                # an in-process frame. Its traffic belongs to the owned page.
                session = await self.context.new_cdp_session(self.guard.primary_page)
            info = (await session.send("Target.getTargetInfo"))["targetInfo"]
            target_id, target_type = info["targetId"], info["type"]
            if document == self.guard.primary_page and target_type == "page":
                self.guard.primary_response_target = target_id
            if target_type not in ("page", "iframe"):
                await session.detach()
                return False
            self.frame_targets[id(document)] = (document, target_id, target_type)
            if target_id in self.targets:
                await session.detach()
                return True
            coverage = FrameIdentityCoverage(session, target_id, target_type)
            await coverage.attach()
            self.coverages[target_id] = coverage
            if document == self.guard.primary_page and target_type == "page":
                async def protocol_reader():
                    return await coverage.refresh()

                self.guard.protocol_reader = protocol_reader
            elif target_type == "iframe":
                async def native_protocol_reader():
                    # The main Page tree omits out-of-process children. Read
                    # this attached target's current frame/loader and target
                    # identity; ResourceGuard binds its parent to live main.
                    return {"frameTree": await coverage.refresh(),
                            "targetInfo": (await session.send("Target.getTargetInfo"))["targetInfo"]}

                self.guard.native_protocol_readers[target_id] = native_protocol_reader

            async def response(event):
                await self.guard.redirect_response(
                    session, event, target_id=target_id, target_type=target_type
                )

            async def network(event):
                if hasattr(self.guard, "native_responses"):
                    self.guard.native_responses.observe_network(event, target_id=target_id, target_type=target_type)
                await self.preflights.observe(session, event, target_id=target_id, target_type=target_type)

            session.on("Fetch.requestPaused", response)
            session.on("Network.requestWillBeSent", network)
            await session.send("Network.enable")
            await session.send(
                "Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]}
            )
            self.targets[target_id] = session
            self.guard.events.append(
                {
                    "decision": "response_target_guard",
                    "target_id": target_id,
                    "target_type": target_type,
                    "active": True,
                }
            )
            del self.guard.events[:-200]
            return True
