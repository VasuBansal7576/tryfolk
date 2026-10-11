"""Gate vendor GETs on native preflight, never on Playwright's synthetic204."""

import asyncio
import hashlib
from urllib.parse import urlsplit


class NativePreflightGate:
    def __init__(self, guard, api):
        self.guard, self.api = guard, api
        self.requests, self.bindings = {}, {}

    async def observe(self, session, event, **target):
        request = event["request"]
        parsed = urlsplit(request["url"])
        if f"{parsed.scheme}://{parsed.netloc}" != self.api:
            return
        url_id = hashlib.sha256(request["url"].encode()).hexdigest()
        if request["method"] != "OPTIONS":
            self.requests[event["requestId"]] = {"url_id": url_id, "frame_id": event.get("frameId")}
            if len(self.requests) > 200:
                self.requests.pop(next(iter(self.requests)))
            return
        initiator_id = event.get("initiator", {}).get("requestId")
        future = asyncio.get_running_loop().create_future()
        self.bindings[url_id] = future
        if len(self.bindings) > 200:
            self.bindings.pop(next(iter(self.bindings)))
        # Chromium can deliver native OPTIONS before its initiating GET.
        # Wait only for that exact protocol request ID; never reuse another
        # URL's preflight or Playwright's synthetic204 as permission.
        try:
            async with asyncio.timeout(13):
                while initiator_id and initiator_id not in self.requests:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            future.set_result(None)
            return
        origin = self.requests.get(initiator_id)
        if not origin or origin["url_id"] != url_id or not origin["frame_id"]:
            future.set_result(None)
            return
        observed_event = {
            "requestId": event["requestId"],
            "frameId": origin["frame_id"],
            "resourceType": "Other",
            "request": request,
        }
        try:
            allowed, facts = await self.guard.preflight_request(
                session, observed_event, observe_only=True, **target
            )
            future.set_result(facts.get("config_id") if allowed else None)
        except Exception:
            future.set_result(None)

    async def wait(self, url, frame_url):
        url_id = hashlib.sha256(url.encode()).hexdigest()
        config_id = hashlib.sha256(frame_url.encode()).hexdigest()
        try:
            async with asyncio.timeout(15):
                while url_id not in self.bindings:
                    await asyncio.sleep(0.01)
                return await asyncio.shield(self.bindings[url_id]) == config_id
        except TimeoutError:
            return False
