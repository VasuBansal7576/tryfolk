"""Private binding for widget reads and one empty-wallet startup; no API proxy."""

import json
from urllib.parse import parse_qsl, urlsplit

from .browser_policy import DODO_CHECKOUT
from .secure_frame import BROWSER_API, frame_config

BOOTSTRAP_PATHS = ("/account/payment_methods", "/customers/payment_methods")
SDK_ORIGIN = "https://sdk-v2.custom.hs.dodopayments.com"
APP_HEADERS = frozenset(
    (
        "api-key",
        "content-type",
        "x-client-version",
        "x-payment-confirm-source",
        "x-browser-name",
        "x-browser-version",
        "x-client-platform",
        "x-merchant-domain",
    )
)
BROWSER_HEADERS = frozenset(
    (
        "origin",
        "referer",
        "accept",
        "accept-language",
        "accept-encoding",
        "user-agent",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "priority",
    )
)


def bind_bootstrap(request, headers, frame_url):
    """Return names/booleans only; private comparisons never leave this call."""
    parsed = urlsplit(request.url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    frame_query = dict(parse_qsl(urlsplit(frame_url).query, keep_blank_values=True))
    observed = frame_config(frame_url)
    startup = parsed.path == "/payments/session_tokens"
    operation_method = "POST" if startup else "GET"
    body = None
    facts = {
        "query_fields": sorted({name for name, _ in query}),
        "header_names": sorted(headers),
        "body_absent": request.post_data is None,
        "config_bound": False,
        "config_id": observed["config_id"],
        "operation": "session_tokens" if startup else "bootstrap",
    }
    if request.post_data is not None:
        try:
            body = json.loads(request.post_data) if len(request.post_data) <= 100_000 else None
        except (ValueError, TypeError):
            body = None
        facts["body_is_json_object"] = isinstance(body, dict)
        facts["body_field_names"] = sorted(body)[:50] if isinstance(body, dict) else []
    preflight = request.method == "OPTIONS" and request.resource_type == "other"
    if preflight:
        requested = [
            part.strip().lower() for part in headers.get("access-control-request-headers", "").split(",")
        ]
        facts.update(
            requested_header_names=sorted(set(requested)),
            requested_method_get=headers.get("access-control-request-method") == "GET",
            requested_method=headers.get("access-control-request-method"),
        )
    else:
        requested = []
    if (
        f"{parsed.scheme}://{parsed.netloc}" != BROWSER_API
        or not (parsed.path in BOOTSTRAP_PATHS or startup)
        or parsed.fragment
        or not (preflight or request.method == operation_method and request.resource_type == "fetch")
        or request.is_navigation_request()
        or request.redirected_from is not None
        or (request.post_data is not None and (preflight or not startup))
        or query != ([] if startup else [("client_secret", frame_query["clientSecret"])])
        or headers.get("origin") != SDK_ORIGIN
    ):
        return facts, observed
    if preflight:
        if (
            not set(headers)
            <= BROWSER_HEADERS | {"access-control-request-method", "access-control-request-headers"}
            or facts["requested_method"] != operation_method
            or not set(requested) <= APP_HEADERS
            or "api-key" not in requested
        ):
            return facts, observed
    elif (
        headers.get("api-key") != frame_query["publishableKey"]
        or not set(headers) <= APP_HEADERS | BROWSER_HEADERS
        or headers.get("content-type") != "application/json"
        or headers.get("x-client-version") != "0.1.0"
        or headers.get("x-payment-confirm-source") != "sdk"
        or headers.get("x-client-platform") != "web"
        or headers.get("x-merchant-domain") != urlsplit(DODO_CHECKOUT).hostname
    ):
        return facts, observed
    if startup and not preflight and (
        not isinstance(body, dict)
        or set(body) != {"payment_id", "client_secret", "wallets", "delayed_session_token"}
        or body["payment_id"] != observed["payment_id"]
        or body["client_secret"] != frame_query["clientSecret"]
        or body["wallets"] != []
        or body["delayed_session_token"] is not False
    ):
        return facts, observed
    facts["config_bound"] = True
    return facts, observed


def is_browser_api(url):
    return urlsplit(url).hostname == urlsplit(BROWSER_API).hostname
