"""Bounded SDK event buffers sanitized before retention."""

import re
from .payment_privacy import redact_payment


def public_urls(value):
    if isinstance(value, str):
        return re.sub(r"(https?://[^\s?#]+)[?#][^\s]*", r"\1", value)
    if isinstance(value, dict):
        return {key: public_urls(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_urls(item) for item in value]
    return value


class EventCapture:
    def __init__(self):
        self.blocked_domains, self.console_events = [], []

    def network_log(self, event):
        if event.message == "Blocked request by domain policy":
            self.blocked_domains.append(redact_payment(public_urls(event.data.model_dump(mode="json"))))
            del self.blocked_domains[:-200]

    def console(self, event):
        params = event.params.model_dump(mode="json")
        if params.get("type") not in ("error", "warning", "assert"):
            return
        messages = []
        for arg in params.get("args", []):
            value = public_urls(str(arg.get("value") or arg.get("description") or ""))
            value = re.sub(
                r"(?i)(bearer\s+|(?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,]+", r"\1[redacted]", value
            )
            messages.append(redact_payment(value)[:1000])
        self.console_events.append({"type": params.get("type"), "messages": messages})
        del self.console_events[:-100]
