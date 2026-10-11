"""Response interception on actual page and OOPIF targets, before private GET."""

import asyncio
from .secure_frame import BROWSER_API
from .native_preflight import NativePreflightGate


class ResponseBoundary:
    def __init__(self, context, guard):
        self.context, self.guard = context, guard
        self.targets = {}
        self.lock = asyncio.Lock()
        self.preflights = NativePreflightGate(guard, BROWSER_API)
        guard.preflight_guard = self.preflights.wait

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
            if target_id in self.targets:
                await session.detach()
                return True

            async def response(event):
                await self.guard.redirect_response(
                    session, event, target_id=target_id, target_type=target_type
                )

            async def network(event):
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
