"""Passive, bounded first-party timing. Never reads request/response content."""

import re
import time
from urllib.parse import urlsplit
from .browser_policy import DODO_CHECKOUT, exact_origin


class CheckoutTiming:
    def __init__(self, primary_page):
        self.primary_page = primary_page
        self._requests = {}
        self._clock = {}
        self._sequence = 0

    def started(self, request):
        try:
            if exact_origin(request.url) != DODO_CHECKOUT or request.frame.page != self.primary_page:
                return
            parsed, frame = urlsplit(request.url), urlsplit(request.frame.url)
            self._sequence += 1
            self._requests[request] = {
                "id": self._sequence,
                "method": request.method,
                "origin": DODO_CHECKOUT,
                "path": parsed.path,
                "initiator": f"{frame.scheme}://{frame.hostname or ''}{':' + str(frame.port) if frame.port else ''}{frame.path}",
                "started_at": time.time(),
                "elapsed_ms": 0,
                "status": None,
                "completion": "pending",
                "failure": None,
            }
            self._clock[request] = time.monotonic()
            if len(self._requests) > 200:
                oldest = next(iter(self._requests))
                self._requests.pop(oldest)
                self._clock.pop(oldest)
        except (ValueError, TypeError, AttributeError):
            return

    def response(self, response):
        if response.request in self._requests:
            self._requests[response.request]["status"] = response.status

    def finished(self, request):
        if request in self._requests:
            self._requests[request].update(
                completion="finished", elapsed_ms=round((time.monotonic() - self._clock[request]) * 1000, 3)
            )

    def failed(self, request):
        if request in self._requests:
            failure = str(request.failure or "")
            self._requests[request].update(
                completion="failed",
                elapsed_ms=round((time.monotonic() - self._clock[request]) * 1000, 3),
                failure=failure
                if re.fullmatch(r"net::[A-Za-z0-9_:.-]+", failure)
                else "browser_request_failed",
            )

    def snapshot(self):
        return [
            {
                **entry,
                "elapsed_ms": entry["elapsed_ms"]
                if entry["completion"] != "pending"
                else round((time.monotonic() - self._clock[request]) * 1000, 3),
            }
            for request, entry in self._requests.items()
        ]
