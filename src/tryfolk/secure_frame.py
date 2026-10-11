"""Private generated-frame parsing; only sanitized facts leave the adapter."""

import hashlib
import re
from urllib.parse import parse_qsl, urlsplit
from .browser_policy import DODO_CHECKOUT, DODO_SECURE_FRAME

FRAME_URL = re.compile(re.escape(DODO_SECURE_FRAME) + r"(?:\?[^\s\"'<>]*)?(?:\#[^\s\"'<>]*)?")
PRIVATE_VALUE = re.compile(r"\bpk_(?:snd|dev|prd)_[A-Za-z0-9_-]+|\b[A-Za-z0-9_-]+_secret_[A-Za-z0-9]+")
BROWSER_API = "https://api.custom.hs.dodopayments.com"


def redact_frame_config(value):
    if isinstance(value, str):
        return PRIVATE_VALUE.sub("[redacted]", FRAME_URL.sub(DODO_SECURE_FRAME, value))
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key in ("publishableKey", "clientSecret", "payment_link", "private_payment_link") else redact_frame_config(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_frame_config(item) for item in value]
    return value


class FrameConfigError(ValueError):
    def __init__(self, evidence):
        super().__init__(
            "Generated secure frame is not an independently verified synthetic Test Mode configuration."
        )
        self.evidence = evidence


def frame_config(url):
    parsed = urlsplit(url)
    query, fragment = (
        parse_qsl(parsed.query, keep_blank_values=True),
        parse_qsl(parsed.fragment, keep_blank_values=True),
    )
    q, f = dict(query), dict(fragment)
    api = urlsplit(q.get("apiUrl", ""))
    payment = re.fullmatch(r"([^\s]+?)_secret_[A-Za-z0-9]+", q.get("clientSecret", ""))
    try:
        port = api.port
    except ValueError:
        port = "invalid"
    evidence = {
        "schema": "tryfolk.private-frame-context.v1",
        "frame_url": DODO_SECURE_FRAME,
        "query_fields": sorted(q),
        "fragment_fields": sorted(f),
        "api_url": f"{api.scheme}://{api.hostname or ''}{':' + str(port) if port else ''}{api.path}",
        "api_base_verified": q.get("apiUrl") == BROWSER_API,
        "browser_api_role": "observed_approved_shared_service_config",
        "service_sandbox_only_attested": False,
        "sandbox_key": q.get("publishableKey", "").startswith("pk_snd_"),
        "parent_matches": f.get("parentOrigin") == DODO_CHECKOUT,
        "payment_id": payment.group(1) if payment else None,
        "config_id": hashlib.sha256(url.encode()).hexdigest(),
    }
    if (
        len(url) > 8192
        or f"{parsed.scheme}://{parsed.netloc}{parsed.path}" != DODO_SECURE_FRAME
        or len(q) != len(query)
        or len(f) != len(fragment)
        or not {"publishableKey", "clientSecret", "apiUrl"} <= q.keys()
        or not q.keys() <= {"publishableKey", "clientSecret", "apiUrl", "locale"}
        or not f.keys() <= {"parentOrigin", "merchantName"}
        or q.get("locale", "en") not in ("en", "en-US")
        or not all(
            evidence[key] for key in ("sandbox_key", "parent_matches", "payment_id", "api_base_verified")
        )
        or api.username
        or api.password
        or api.query
        or api.fragment
    ):
        raise FrameConfigError(evidence)
    return evidence
