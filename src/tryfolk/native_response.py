"""Publish an exact admitted native reply before Chromium can deliver its bytes.

This status is a navigation hint. It never verifies provider payment, grants
entitlement, or replaces the independent owned account/download checks.
"""

import asyncio
import base64
import hashlib
import json
from urllib.parse import urlsplit


def body_digest(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Native request body is not text.")
    raw = value.encode("utf-8")
    if len(raw) > 100_000:
        raise ValueError("Native request body exceeds the admitted byte bound.")
    return hashlib.sha256(raw).hexdigest()


def request_body_digest(request):
    """Exact CDP bytes, including entries-only bodies used by Playwright1.63."""
    representations = []
    if "hasPostData" in request and type(request["hasPostData"]) is not bool:
        raise ValueError("Unknown native request body presence flag.")
    if "postData" in request:
        text = request["postData"]
        if not isinstance(text, str):
            raise ValueError("Native legacy body is not text.")
        representations.append(text.encode("utf-8"))
    if "postDataEntries" in request:
        entries = request["postDataEntries"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
            raise ValueError("Native entries are unavailable or oversized.")
        pieces, size = [], 0
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"bytes"}:
                raise ValueError("Native entry bytes are unavailable.")
            encoded = entry["bytes"]
            if not isinstance(encoded, str) or len(encoded) > 133_336:
                raise ValueError("Native encoded entry is oversized.")
            piece = base64.b64decode(encoded, validate=True)
            size += len(piece)
            if size > 100_000:
                raise ValueError("Native decoded entries are oversized.")
            pieces.append(piece)
        representations.append(b"".join(pieces))
    if not representations:
        if request.get("hasPostData") is True or request.get("method") == "POST":
            raise ValueError("Native POST bytes are unavailable.")
        return None
    if request.get("hasPostData") is False or any(len(raw) > 100_000 for raw in representations):
        raise ValueError("Native body presence or size is inconsistent.")
    if any(raw != representations[0] for raw in representations[1:]):
        raise ValueError("Native body representations differ.")
    return hashlib.sha256(representations[0]).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Ambiguous duplicate native JSON field.")
        result[key] = value
    return result


def reject_constant(_):
    raise ValueError("Native body contains a non-JSON numeric constant.")


class NativeResponseCapture:
    def __init__(self, api, verify, record):
        self.api, self.verify, self.record = api, verify, record
        self.network, self.accepted = {}, {}

    def observe_network(self, event, **target):
        request = event.get("request", {})
        url = request.get("url", "")
        if not isinstance(url, str) or len(url) > 20_000:
            return
        parsed = urlsplit(url)
        if f"{parsed.scheme}://{parsed.netloc}" != self.api or request.get("method") == "OPTIONS":
            return
        network_id = event.get("requestId")
        if not isinstance(network_id, str) or not network_id:
            return
        try:
            digest = request_body_digest(request)
        except (ValueError, UnicodeError):
            digest = False  # Cannot equal a valid exact byte digest or absent GET body.
        self.network[network_id] = {
            "url": request.get("url"), "method": request.get("method"),
            "body_digest": digest,
            "frame_id": event.get("frameId"), "loader_id": event.get("loaderId"),
            "target_id": target.get("target_id"), "target_type": target.get("target_type"),
            "resource_type": event.get("type"), "redirected": bool(event.get("redirectResponse")),
        }
        if len(self.network) > 200:
            self.network.pop(next(iter(self.network)))

    async def admit(self, request, facts, protocol, capability, primary_target):
        children = [child for child in protocol.get("children", []) if child.get("url") == capability.raw]
        if not (facts.get("config_bound") is True and facts.get("operation") in ("confirm", "poll")
                and facts.get("binding_checks") and all(value is True for value in facts["binding_checks"].values())
                and request.method == ("POST" if facts["operation"] == "confirm" else "GET")
                and request.resource_type in ("fetch", "xhr")
                and capability.confirmed and len(children) == 1 and len(self.accepted) < 26):
            return False
        child, main = children[0], protocol.get("main", {})
        if not (child.get("id") and child.get("loader_id") and child.get("parent_id") == main.get("id")):
            return False
        target_id = child.get("target_id") or primary_target
        matches = [(key, value) for key, value in self.network.items()
                   if key not in self.accepted and value == {"url": request.url, "method": request.method,
                                "body_digest": body_digest(request.post_data),
                                "frame_id": child["id"], "loader_id": child["loader_id"],
                                "target_id": target_id,
                                "target_type": "iframe" if child.get("target_id") else "page",
                                "resource_type": "Fetch" if request.resource_type == "fetch" else "XHR",
                                "redirected": False}]
        if len(matches) != 1 or matches[0][0] in self.accepted:
            return False
        network_id, value = matches[0]
        self.accepted[network_id] = {
            **value, "request": request, "facts": facts, "capability": capability, "used": False,
        }
        facts["native_response_registered"] = True
        return True

    async def capture(self, session, event, **target):
        request = event.get("request", {})
        if not any(request.get("url") == value["url"] for value in self.accepted.values()):
            return False
        bound = self.accepted.get(event.get("networkId"))
        checks = {"accepted_network_request_ID": bound is not None,
                  "single_response": bool(bound) and not bound["used"]}
        status = event.get("responseStatusCode")
        body, context_verified, error_code = None, False, None
        try:
            if not all(checks.values()):
                raise ValueError("Native response has no unused admitted network request.")
            bound["used"] = True
            checks["accepted_network_transaction_unchanged"] = all(
                self.network.get(event["networkId"], {}).get(key) == bound[key]
                for key in ("url", "method", "body_digest", "frame_id", "loader_id", "target_id",
                            "target_type", "resource_type", "redirected")
            )
            checks["native_request_body_bytes_decoded"] = False
            digest = request_body_digest(request)
            checks["native_request_body_bytes_decoded"] = True
            headers = event.get("responseHeaders", [])
            types = [h["value"].split(";", 1)[0].strip().lower()
                     for h in headers if h["name"].lower() == "content-type"]
            checks.update({
                "exact_target_and_frame": target.get("target_id") == bound["target_id"]
                and target.get("target_type") == bound["target_type"] and event.get("frameId") == bound["frame_id"],
                "exact_request_and_body": request.get("url") == bound["url"]
                and request.get("method") == bound["method"]
                and digest == bound["body_digest"],
                # Chromium's Fetch interception can label a Network Fetch as
                # XHR. The exact network ID/frame/target/body still bind it.
                "native_fetch_response": event.get("resourceType") in ("Fetch", "XHR")
                and bound["resource_type"] in ("Fetch", "XHR"),
                "no_redirect": not event.get("redirectedRequestId")
                and not any(h["name"].lower() == "location" for h in headers),
                "response_JSON_content_type": types == ["application/json"],
                "response_HTTP_status_supported": type(status) is int and (status == 200 or 400 <= status <= 599),
            })
            if not all(checks.values()):
                raise ValueError("Native response metadata differs from its admitted request.")
            async with asyncio.timeout(5):
                before = await self.verify(bound["request"], bound["facts"])
                checks.update(before)
                if not before or not all(value is True for value in checks.values()):
                    raise ValueError("Native response context is no longer current.")
                context_verified = True
                response = await session.send("Fetch.getResponseBody", {"requestId": event["requestId"]})
                encoded = response.get("body")
                checks["encoded_body_bounded"] = isinstance(encoded, str) and 0 < len(encoded) <= 266_668
                checks["encoding_flag_boolean"] = type(response.get("base64Encoded")) is bool
                if not all(checks.values()):
                    raise ValueError("Native response body is unavailable or oversized.")
                raw = base64.b64decode(encoded, validate=True) if response["base64Encoded"] else encoded.encode()
                checks["decoded_body_bounded"] = 0 < len(raw) <= 200_000
                if not checks["decoded_body_bounded"]:
                    raise ValueError("Native response decoded body is oversized.")
                checks["body_decoded_UTF8_and_JSON"] = False
                body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
                checks["body_decoded_UTF8_and_JSON"] = True
                checks["top_level_JSON_object"] = isinstance(body, dict)
                finite = body.get("status") if isinstance(body, dict) else None
                finite_status = isinstance(finite, str) and finite in (
                    "succeeded", "processing", "failed", "cancelled"
                )
                checks["finite_top_level_status_or_HTTP_error"] = finite_status or status != 200
                if not all(checks.values()):
                    raise ValueError("Native response has no source-supported finite status.")
                after = await self.verify(bound["request"], bound["facts"])
                checks["same_current_context_after_body"] = bool(after) and after == before \
                    and all(value is True for value in after.values())
                if not checks["same_current_context_after_body"]:
                    raise ValueError("Native response context changed during body observation.")
                error = body.get("error") if isinstance(body.get("error"), dict) else {}
                error_code = "IR_16" if error.get("code") == "IR_16" else None
                bound["capability"].observe_response(finite if status == 200 else None, status,
                                                    operation=bound["facts"]["operation"], error_code=error_code)
                self.record({"decision": "native_response_captured_before_delivery" if status == 200
                             else "native_response_error_captured_before_delivery", "binding_checks": checks,
                             "status_enum": finite if status == 200 else "unknown", "body_bytes": len(raw),
                             "latched_before_continueResponse": status == 200,
                             "HTTP_error_observed": status != 200, "IR_16_cutoff": error_code == "IR_16"})
            await session.send("Fetch.continueResponse", {"requestId": event["requestId"]})
        except Exception as error:
            if bound and checks.get("single_response"):
                projection = "unknown" if isinstance(body, dict) and body.get("status") is not None else None
                bound["capability"].observe_response(projection, status, operation=bound["facts"]["operation"],
                                                    error_code=error_code)
                if not context_verified or checks.get("same_current_context_after_body") is False:
                    bound["capability"].closed = True
            self.record({"decision": "deny_native_response", "binding_checks": checks,
                         "first_failed_predicate": next((key for key, value in checks.items() if not value), None),
                         "error_type": type(error).__name__})
            await session.send("Fetch.failRequest", {"requestId": event["requestId"], "errorReason": "BlockedByClient"})
        return True
