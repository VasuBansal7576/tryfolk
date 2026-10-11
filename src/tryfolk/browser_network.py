"""Exact public-script exceptions beside the unchanged action/frame policy.

Stagehand's hostname-only policy remains enabled. Playwright's documented CDP
connection and context routing add a narrower request boundary; they never act
for the persona. Service workers must be blocked on the routed context.
"""

import asyncio
import base64
import copy
import hashlib
import ipaddress
import re
import socket
from types import SimpleNamespace
from urllib.parse import urlsplit, urljoin, urldefrag

import httpx

from .browser_policy import BrowserPolicy, DODO_CHECKOUT, DODO_RETURN, DODO_SECURE_FRAME, exact_origin
from .secure_frame import BROWSER_API, frame_config, FrameConfigError, redact_frame_config
from .widget_bootstrap import bind_bootstrap, is_browser_api
from .native_card import NativeCardAttempt, approved_redirect, binding
from .native_card_observation import READ_CONTEXT, READ_CONTACT, browser_information
from .browser_settlement import DOCUMENT_STATE
from .checkout_navigation import CheckoutNavigation, NORMALIZER_SOURCE_SHA256, RETURN_SOURCE_SHA256, STATUS_SOURCE_SHA256, WALLET_SOURCE_SHA256, READ_NORMALIZATION_STATE

PUBLIC_SCRIPTS = (
    "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/sdk.js",
    "https://js.stripe.com/clover/stripe.js",
)
FRAME_SCRIPTS = tuple(
    DODO_SECURE_FRAME.replace("checkout.html", "assets/" + name)
    for name in (
        "checkout-BWo9IWjf.js",
        "app-DoOO9T9n.js",
        "app-payment-methods-BbWaNrlq.js",
    )
)


class NetworkPolicy(BrowserPolicy):
    def __init__(self, origin, provider):
        super().__init__(origin, provider)
        self._return_transport_url = self._return_status_url = self._return_owned_location = None
        self._return_attempt = None

    def check_url(self, url):
        if url == self._return_transport_url and self._return_attempt and self._return_attempt.return_phase_open():
            return  # Passive settlement only, never an action origin.
        super().check_url(url)

    def bind(self, action, control, url):
        if exact_origin(url) not in self.origins:
            raise ValueError("Provider return transport has no browser action capability.")
        return super().bind(action, control, url)

    def validate(self, action, url=None):
        if exact_origin((action.get("_tryfolk") or {}).get("url", "")) not in self.origins:
            raise ValueError("Provider return transport has no browser action capability.")
        return super().validate(action, url)

    def redirect_allowed(self, source, location):
        try:
            if source == self._return_transport_url:
                return bool(self._return_attempt) and self._return_attempt.return_phase_open() and location == self._return_owned_location
            return (
                exact_origin(source) in self.origins
                and exact_origin(urljoin(source, location)) in self.origins
            )
        except (ValueError, TypeError):
            return False

    def decide(
        self,
        url,
        method,
        resource_type,
        document_url,
        navigation,
        redirected=False,
        body=None,
        *,
        primary_parent_url=None,
        credentials=False,
    ):
        try:
            if (
                self.provider == "dodo"
                and url == DODO_SECURE_FRAME
                and method == "GET"
                and not redirected
                and body is None
                and not credentials
                and (
                    resource_type == "document"
                    and navigation
                    and primary_parent_url is not None
                    and exact_origin(primary_parent_url) == DODO_CHECKOUT
                    or resource_type == "other"
                    and not navigation
                    and exact_origin(document_url) == DODO_CHECKOUT
                )
            ):
                return "public_frame"
            # A child frame has no inherited network grant from its parent.
            if document_url == DODO_SECURE_FRAME:
                if (
                    self.provider == "dodo"
                    and url in FRAME_SCRIPTS
                    and method == "GET"
                    and resource_type == "script"
                    and not navigation
                    and not redirected
                    and body is None
                    and not credentials
                    and primary_parent_url is not None
                    and exact_origin(primary_parent_url) == DODO_CHECKOUT
                ):
                    return "public_frame_script"
                return "deny"
            origin = exact_origin(url)
            if url == self._return_transport_url and self._return_attempt and self._return_attempt.return_phase_open():
                return "continue" if (method == "GET" and resource_type == "document" and navigation
                                      and not redirected and body is None and not credentials
                                      and document_url == self._return_status_url) else "deny"
            if origin in self.origins:
                return "continue"
            if (
                self.provider == "dodo"
                and url in PUBLIC_SCRIPTS
                and method == "GET"
                and resource_type == "script"
                and not navigation
                and not redirected
                and body is None
                and exact_origin(document_url) == DODO_CHECKOUT
            ):
                return "public_script"
        except (ValueError, TypeError):
            pass
        return "deny"

    @property
    def sdk_domains(self):
        hosts = [urlsplit(origin).hostname for origin in self.origins]
        if self.provider == "dodo":
            hosts.extend(urlsplit(url).hostname for url in PUBLIC_SCRIPTS)
            # Transport only: ResourceGuard still binds every API request to
            # the verified child, exact paths, private config and fresh receipt.
            hosts.append(urlsplit(BROWSER_API).hostname)
            hosts.append(urlsplit(DODO_RETURN).hostname)  # Exact guarded GET transport only.
        return hosts


async def public_script(url):
    """No browser headers, cookies, secrets, proxies, redirects or retries."""
    if url not in (*PUBLIC_SCRIPTS, *FRAME_SCRIPTS):
        raise ValueError("Public script URL is outside the exact resource grant.")
    return await _public_resource(
        url, ("application/javascript", "text/javascript", "application/x-javascript")
    )


async def public_frame(url):
    if url != DODO_SECURE_FRAME:
        raise ValueError("Public frame URL is outside the exact document grant.")
    return await _public_resource(url, ("text/html",))


async def _public_resource(url, content_types):
    addresses = await asyncio.get_running_loop().getaddrinfo(
        urlsplit(url).hostname, 443, type=socket.SOCK_STREAM
    )
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Public script resolved to a private or reserved address.")
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=10) as client:
        accept = "text/html" if "text/html" in content_types else "application/javascript"
        async with client.stream("GET", url, headers={"Accept": accept}) as response:
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if (
                response.status_code != 200
                or response.headers.get("location")
                or content_type not in content_types
            ):
                raise ValueError("Public resource response is not an unredirected approved document.")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 5_000_000:
                    raise ValueError("Public script exceeds the bounded resource limit.")
                chunks.append(chunk)
            headers = {"content-type": response.headers["content-type"]}
            if "access-control-allow-origin" in response.headers:
                headers["access-control-allow-origin"] = response.headers["access-control-allow-origin"]
            for name in (
                "content-security-policy",
                "x-content-type-options",
                "x-frame-options",
                "referrer-policy",
                "permissions-policy",
            ):
                if name in response.headers:
                    headers[name] = response.headers[name]
            return b"".join(chunks), headers


class NativePayAdmissionError(ValueError):
    def __init__(self, checks):
        self.checks = dict(checks)
        self.first_failed_predicate = next(name for name, passed in checks.items() if not passed)
        super().__init__("Native Pay admission failed: " + self.first_failed_predicate)


class NativePaymentParentError(ValueError):
    def __init__(self, checks):
        self.checks = dict(checks)
        self.first_failed_predicate = next(name for name, passed in checks.items() if not passed)
        super().__init__("Native confirmation parent failed: " + self.first_failed_predicate)


class ResourceGuard:
    def __init__(self, *, frame_verify=None):
        self.policy = None
        self.frame_verify = frame_verify
        self.response_guard = None
        self.preflight_guard = None
        self.events = []
        self.primary_page = None
        self.primary_response_target = None
        self.parent_reader = None
        self.protocol_reader = None
        self.native_protocol_readers = {}
        self.select_native_protocol = self.return_protocol_reader = None
        self._native_protocol_target = self._native_protocol_url = None
        self._armed_protocol = self._confirmed_protocol = None
        self._confirmed_generation = self._confirmed_return = self._confirmed_normalization = None
        self._confirmed_return_source_bound = False
        self.page_ids = {}
        self._frame_contexts = {}
        self._bootstrap_requests = {}
        self._bootstrap_observations = {}
        self._normal_generations, self._read_generations = {}, {}
        self._startup_configs = set()
        self.card_enabled, self.card_attempt = False, None
        self._normal_configs, self._successful_reads = set(), {}
        self._owned_checkouts = {}
        self._owned_creation_request = None
        self._owned_creation_requested = False
        self.checkout_navigation = None
        self._billing_submission_request = None
        self._armed_payment_link = self._armed_parent_generation = None
        self._payment_response_task = None
        self._payment_response_pending = None

    def bind_owned_checkout_request(self, request):
        """Retain the single actual browser fetch, without forwarding it."""
        if not (
            self.policy and self.policy.provider == "dodo" and self.primary_page
            and request.frame == self.primary_page.main_frame
            and request.method == "POST" and request.resource_type == "fetch"
            and not request.is_navigation_request() and request.redirected_from is None
            and request.url == self.policy.origin + "/api/target/checkouts"
            and not self._owned_creation_requested
        ):
            return False
        self._owned_creation_request = request
        self._owned_creation_requested = True
        return True

    async def observe_owned_checkout_response(self, response):
        """Retain the owned creation URL privately; reading causes no checkout."""
        request = response.request
        if not (
            self.policy and self.policy.provider == "dodo" and self.primary_page
            and request.frame == self.primary_page.main_frame
            and request.method == "POST" and request.redirected_from is None
            and request.resource_type == "fetch" and response.status == 201
            and request.url == response.url == self.policy.origin + "/api/target/checkouts"
        ):
            return False
        try:
            async with asyncio.timeout(5):
                raw = await response.body()
            if len(raw) > 20_000:
                return False
            import json
            body = json.loads(raw)
            if not isinstance(body, dict):
                return False
            ownership = body.get("ownership")
            url = body.get("checkout_url")
            parsed = urlsplit(url) if isinstance(url, str) and len(url) <= 4096 else urlsplit("")
            names = ("run_id", "persona_id", "account_id", "fixture_id", "target_revision")
            if not (
                body.get("source") == "dodo_test_api"
                and isinstance(ownership, dict) and set(ownership) == set(names)
                and all(isinstance(ownership[key], str) and ownership[key] for key in names)
                and all(isinstance(body.get(key), str) and body[key] for key in ("order_id", "checkout_session_id"))
                and f"{parsed.scheme}://{parsed.netloc}" == DODO_CHECKOUT and parsed.path
                and not (parsed.username or parsed.password) and not self._owned_checkouts
            ):
                return False
            self._owned_checkouts[body["checkout_session_id"]] = {
                "url": url, "order_id": body["order_id"], "ownership": dict(ownership),
            }
            self.checkout_navigation = CheckoutNavigation(body["checkout_session_id"], url, body.get("navigation_customization"))
            self.record(request, "owned_checkout_response_captured", private_url_retained=True)
            return True
        except (ValueError, TypeError, TimeoutError):
            return False

    def owned_checkout(self, identity):
        record = self._owned_checkouts.get(identity.get("checkout_session_id"), {})
        if not all(self.owned_checkout_checks(identity).values()):
            return {}
        return record

    def owned_checkout_checks(self, identity):
        record = self._owned_checkouts.get(identity.get("checkout_session_id"), {})
        return {
            "independent_identity_approved": identity.get("approved") is True,
            "independent_Test_environment": identity.get("provider_environment") == "test_mode",
            "independent_payment_correlated": identity.get("payment_correlated") is True,
            "independent_customer_correlated": identity.get("customer_correlated") is True,
            "owned_checkout_creation_available": bool(record),
            "independent_owned_order_bound": bool(record) and record.get("order_id") == identity.get("order_id"),
            "independent_owned_ownership_bound": bool(record) and record.get("ownership") == identity.get("ownership"),
        }

    def require_arm_admission(self, checks, *, parent_equality_facts=None, parent_query_facts=None, normalization_checks=None):
        if not all(checks.values()):
            error = NativePayAdmissionError(checks)
            self.events.append({"decision": "deny_native_pay_arm", "arm_admission_checks": dict(checks),
                                "first_failed_predicate": error.first_failed_predicate,
                                **({"parent_equality_facts": parent_equality_facts} if parent_equality_facts is not None else {}),
                                **({"parent_query_facts": parent_query_facts} if parent_query_facts is not None else {}),
                                **({"normalization_checks": normalization_checks} if normalization_checks is not None else {})})
            raise error

    async def parent_document(self):
        """Private fresh document/map read; evidence receives closed checks only."""
        document = await self.read_parent(DOCUMENT_STATE)
        if self.checkout_navigation and self.checkout_navigation.normalizer_generation is not None:
            document["_normalization"] = await self.read_parent(READ_NORMALIZATION_STATE)
        return document

    async def read_parent(self, script):
        return await self.parent_reader(script) if self.parent_reader else await self.primary_page.evaluate(script)

    @staticmethod
    def protocol_frame(frame):
        """Private protocol identity; callers emit only closed comparisons."""
        if not isinstance(frame, dict):
            return {}
        fields = {"id": frame.get("id"), "loader_id": frame.get("loaderId"), "url": frame.get("url")}
        if not all(isinstance(value, str) and 0 < len(value) <= 20_000 for value in fields.values()):
            return {}
        fragment = frame.get("urlFragment", "")
        if not isinstance(fragment, str) or fragment and not fragment.startswith("#"):
            return {}
        fields["url"] += fragment
        if "identityEpoch" in frame:
            epoch = frame["identityEpoch"]
            if type(epoch) is not int or epoch < 0:
                return {}
            fields["identity_epoch"] = epoch
        return fields

    def combined_protocol_frames(self, tree, native=None):
        main = self.protocol_frame(tree.get("frame"))
        expected_url = self.card_attempt.raw if self.card_attempt else self._native_protocol_url
        children = []
        for node in tree.get("childFrames", []):
            raw = node.get("frame", {})
            frame = self.protocol_frame(raw)
            if frame.get("url") == expected_url:
                frame.update(parent_id=raw.get("parentId", main.get("id")), target_id=None)
                children.append(frame if frame["parent_id"] == main.get("id") else {})
        if native is not None:
            raw = native.get("frameTree", {}).get("frame", {})
            frame, target = self.protocol_frame(raw), native.get("targetInfo", {})
            parents = [value for value in (raw.get("parentId"), target.get("parentFrameId")) if value is not None]
            bound = bool(frame) and frame["id"] == target.get("targetId") == self._native_protocol_target \
                and target.get("type") == "iframe" and bool(parents) and all(value == main.get("id") for value in parents) \
                and frame["url"] == expected_url
            if not bound:
                children.append({})
            else:
                frame.update(parent_id=main["id"], target_id=self._native_protocol_target)
                matches = [child for child in children if child.get("id") == frame["id"]]
                if matches:
                    comparable = {key: value for key, value in frame.items() if key not in ("target_id", "identity_epoch")}
                    if len(matches) != 1 or {key: value for key, value in matches[0].items() if key not in ("target_id", "identity_epoch")} != comparable:
                        children.append({})
                    else:
                        children[children.index(matches[0])] = frame
                else:
                    children.append(frame)
        return {"main": main, "children": children}

    async def protocol_frames(self):
        if not self.protocol_reader:
            return {"main": {}, "children": []}
        async with asyncio.timeout(5):
            tree = await self.protocol_reader()
            main, native = self.protocol_frame(tree.get("frame")), None
            if main and (not self.card_attempt or main["url"] == self.card_attempt.parent_url) and self._native_protocol_target:
                readers = dict(self.native_protocol_readers)
                reader = readers.get(self._native_protocol_target)
                if not reader:
                    raise ValueError("Selected native protocol session is unavailable.")
                native = await reader()
        return self.combined_protocol_frames(tree, native)

    async def current_return_frames(self, request):
        if not self.return_protocol_reader:
            raise ValueError("Paused navigation lacks active protocol attestation.")
        async with asyncio.timeout(5):
            current = await self.return_protocol_reader(request)
        return self.combined_protocol_frames(current["main"]["frameTree"], current.get("native"))

    async def capture_confirm_protocol(self, document):
        """Freeze live loader identities beside freshly checked CONFIRM facts.

        This runs before the accepted native request is forwarded, while the
        current parent/native documents remain evaluable. Later main-document
        request interception uses live protocol reads exclusively.
        """
        live = await self.protocol_frames()
        capability, navigation = self.card_attempt, self.checkout_navigation
        if not (capability and navigation and self._armed_protocol and live == self._armed_protocol
                and live["main"].get("url") == capability.parent_url
                and document.get("url") == capability.parent_url
                and document.get("generation") == self._armed_parent_generation
                and navigation.armed_normalization_current(document)):
            return False
        self._confirmed_protocol = live
        self._confirmed_generation, self._confirmed_return = document["generation"], capability.return_url
        self._confirmed_normalization = copy.deepcopy(navigation._armed_normalization)
        self._confirmed_return_source_bound = navigation.return_source_current(document)
        return True

    async def bind_return_navigation(self, request):
        """Live CDP document admission; no JavaScript in paused navigation."""
        capability = self.card_attempt
        pending = self._payment_response_pending or self._payment_response_task
        if pending and not pending.done():
            try:
                async with asyncio.timeout(5):
                    await asyncio.shield(pending)
            except TimeoutError:
                pass
        facts = capability.return_navigation_facts(request.url)
        before = await self.current_return_frames(request)
        checks = {
            "accepted_native_CONFIRM": capability.confirmed,
            "return_phase_bounded": capability.return_phase_open(),
            "source_wallet_return_bound": facts["native_body_return_is_source_wallet_route"],
            "accepted_native_response_status": facts["accepted_response_has_finite_status"],
            "exact_source_status_destination": facts["candidate_source_derived_status_exact"],
            "native_top_level_GET": request.frame == self.primary_page.main_frame
            and request.is_navigation_request() and request.resource_type == "document"
            and request.method == "GET" and request.post_data is None and (request.redirected_from is None
                or request.url == capability._admitted_owned_redirect
                and getattr(request.redirected_from, "url", None) == capability._admitted_provider_return),
        }
        result = {"return_facts": facts, "post_confirm_checks": checks, "first_failed_predicate": None}
        for name in ("accepted_native_CONFIRM", "return_phase_bounded", "native_top_level_GET"):
            if not checks[name]:
                result["first_failed_predicate"] = name
                return False, result
        if exact_origin(request.url) == DODO_RETURN:
            return await self.bind_provider_return(request, before)
        if request.url == capability._admitted_owned_redirect:
            # Redirect continuation is bound to the status document reviewed
            # before this same provider response, plus its exact owned Location.
            _, redirect_checks = await self.return_identity()
            after = await self.current_return_frames(request)
            redirect_checks.update(current_status_source_bound=bool(self.checkout_navigation)
                                   and self.checkout_navigation.status_source_current(before["main"]),
                                   status_document_stable=before["main"] == after["main"],
                                   exact_owned_redirect=request.url == self.policy._return_owned_location)
            return all(redirect_checks.values()), {"provider_return_checks": redirect_checks,
                    "first_failed_predicate": next((n for n, v in redirect_checks.items() if not v), None)}
        navigation = self.checkout_navigation
        frozen = self._confirmed_protocol or {"main": {}, "children": []}
        exact_body = facts["candidate_equals_confirmed_native_body_return"]
        exact_owned = facts["candidate_equals_frozen_owned_return"]
        if exact_body or exact_owned:
            checks["source_wallet_return_bound"] = checks["exact_source_status_destination"] = True
        checks.update({
            "single_current_native_child": len(before["children"]) >= 1 and sum(
                bool(child) and child.get("url") == capability.raw for child in before["children"]) == 1,
            "frozen_current_parent": bool(before["main"]) and before["main"] == frozen["main"]
            and before["main"].get("url") == capability.parent_url,
            "frozen_current_child": bool(frozen["children"]) and before["children"] == frozen["children"],
            "reviewed_return_source_current": self._confirmed_return_source_bound and bool(navigation) and navigation.return_source_current(
                {"generation": self._confirmed_generation}),
            "frozen_current_normalization": bool(navigation) and bool(self._confirmed_normalization)
            and navigation._armed_normalization == self._confirmed_normalization,
            "accepted_native_body_unchanged": bool(self._confirmed_return) and capability.return_url == self._confirmed_return,
        })
        _, identity_checks = await self.return_identity()
        checks.update(identity_checks)
        after = await self.current_return_frames(request)
        checks["same_current_documents_during_identity"] = before == after
        admitted = all(checks.values())
        facts.update(current_parent_equals_frozen_parent=checks["frozen_current_parent"],
                     current_parent_loader_equals_accepted_CONFIRM=checks["frozen_current_parent"],
                     current_native_child_loader_equals_accepted_CONFIRM=checks["frozen_current_child"],
                     current_reviewed_return_source_generation=checks["reviewed_return_source_current"])
        if admitted:
            if not exact_owned:
                capability._admitted_post_confirm_status = request.url
                navigation.status_destination = request.url
        else:
            result["first_failed_predicate"] = next(name for name, value in checks.items() if not value)
        return admitted, result

    async def return_identity(self):
        identity = {}
        if self.frame_verify and self.card_attempt:
            async with asyncio.timeout(13):
                identity = await self.frame_verify({**self.card_attempt.context, "operation": "poll"})
        capability = self.card_attempt
        checks = {**self.owned_checkout_checks(identity),
                  "independent_original_payment": identity.get("payment_id") == capability.context["payment_id"],
                  "independent_original_checkout": identity.get("checkout_session_id") == capability.checkout_session_id,
                  "independent_original_link": identity.get("payment_link") == self._armed_payment_link,
                  "independent_response_status_matches": identity.get("payment_status") == capability.response_status}
        return identity, checks

    async def bind_provider_return(self, request, before):
        capability, navigation = self.card_attempt, self.checkout_navigation
        identity, checks = await self.return_identity()
        after = await self.current_return_frames(request)
        checks.update({
            "return_phase_bounded": capability.return_phase_open(),
            "finite_accepted_native_status": capability.response_status in ("succeeded", "failed", "cancelled", "processing"),
            "current_status_source_bound": bool(navigation) and navigation.status_source_current(before["main"]),
            "same_status_document_during_identity": before == after,
            "exact_Test_provider_return": request.url == capability.provider_return_url(),
            "single_provider_return": capability._admitted_provider_return is None,
            "primary_GET_document": request.frame == self.primary_page.main_frame and request.is_navigation_request()
            and request.resource_type == "document" and request.method == "GET"
            and request.post_data is None and request.redirected_from is None,
        })
        admitted = all(checks.values())
        if admitted:
            capability._admitted_provider_return = request.url
            self.policy._return_transport_url, self.policy._return_status_url = request.url, before["main"]["url"]
            self.policy._return_attempt = capability
        return admitted, {"provider_return_checks": checks,
                          "first_failed_predicate": next((name for name, value in checks.items() if not value), None)}

    async def bind_provider_redirect(self, session, event, target):
        capability, navigation = self.card_attempt, self.checkout_navigation
        request, headers = event.get("request", {}), event.get("responseHeaders", [])
        locations = [h["value"] for h in headers if h["name"].lower() == "location"]
        request_identity = SimpleNamespace(url=request.get("url"), method=request.get("method"))
        before = await self.current_return_frames(request_identity)
        tree = before["main"]
        identity, checks = await self.return_identity()
        after = await self.current_return_frames(request_identity)
        checks.update({
            "return_phase_bounded": capability.return_phase_open(),
            "exact_admitted_provider_request": bool(capability._admitted_provider_return) and request.get("url") == capability._admitted_provider_return == capability.provider_return_url(),
            "single_response_redirect": capability._admitted_owned_redirect is None,
            "primary_target_document": target.get("target_type") == "page" and target.get("target_id") == self.primary_response_target
            and event.get("resourceType") == "Document" and event.get("frameId") == tree.get("id"),
            "response_original_GET": request.get("method") == "GET" and not request.get("postData") and not event.get("redirectedRequestId"),
            "one_response_Location": len(locations) == 1,
            "finite_HTTP_redirect": event.get("responseStatusCode") in (301, 302, 303, 307, 308),
            "current_status_source_bound": bool(navigation) and navigation.status_source_current(before["main"]),
            "status_document_stable": before["main"] == after["main"] == tree,
        })
        checks.update(capability.owned_location_checks(locations[0] if len(locations) == 1 else None, identity))
        admitted = all(checks.values())
        if admitted:
            capability._admitted_owned_redirect = self.policy._return_owned_location = locations[0]
        self.events.append({"decision": "provider_return_redirect" if admitted else "deny_provider_return_redirect",
                            "provider_redirect_checks": checks,
                            "first_failed_predicate": next((name for name, value in checks.items() if not value), None)})
        return admitted

    def expect_billing_continue(self, document):
        """Called only after the ordinary current Continue action is bound."""
        if not self.checkout_navigation or not self.checkout_navigation.expect_continue(document):
            raise ValueError("Owned billing Continue has no single current creation document.")

    async def bind_billing_submission(self, request):
        if not (
            self.checkout_navigation and self.primary_page
            and request.frame == self.primary_page.main_frame
            and request.method == "POST" and request.resource_type == "fetch"
            and not request.is_navigation_request() and request.redirected_from is None
            and self._billing_submission_request is None
        ):
            return False
        document = await self.read_parent(DOCUMENT_STATE)
        if not self.checkout_navigation.bind_submission(request.url, request.post_data, document):
            return False
        self._billing_submission_request = request
        return True

    async def bind_api(self, request, headers, frame):
        if self.card_attempt and urlsplit(request.url).path in (
            f"/payments/{self.card_attempt.context['payment_id']}/confirm",
            f"/payments/{self.card_attempt.context['payment_id']}",
        ):
            state = await frame.evaluate(READ_CONTEXT)
            native = state.get("native") or {}
            observed = frame_config(frame.url)
            native_status = None
            if urlsplit(request.url).path.endswith("/confirm"):
                current_url, parent = frame.url, frame.parent_frame
                parent_url = parent.url if parent else None
                parent_before = await self.read_parent(DOCUMENT_STATE)
                identity = {}
                if self.frame_verify:
                    async with asyncio.timeout(13):
                        identity = await self.frame_verify({**observed, "operation": "confirm"})
                fresh = await frame.evaluate(READ_CONTEXT)
                context = self.frame_context(frame.url, parent_url) or {}
                parent_after = await self.parent_document()
                self.card_attempt.recheck_contact_email(await self.read_parent(READ_CONTACT))
                parent_equality_facts = self.checkout_navigation.parent_equality_facts(identity.get("payment_link"), parent_after) if self.checkout_navigation else {}
                parent_query_facts = self.checkout_navigation.parent_query_facts(parent_after) if self.checkout_navigation else {}
                normalization_checks = self.checkout_navigation.normalization_checks(parent_after) if self.checkout_navigation else {}
                parent_checks = self.checkout_navigation.parent_checks(identity.get("payment_link"), parent_after) if self.checkout_navigation else {}
                parent_checks = {**self.owned_checkout_checks(identity), **parent_checks,
                                 "owned_normalization_still_armed": bool(self.checkout_navigation) and self.checkout_navigation.armed_normalization_current(parent_after),
                                 "fresh_native_CONFIRM_parent_source": native.get("return_source") == "CONFIRM",
                                 "payment_link_still_armed": identity.get("payment_link") == self._armed_payment_link,
                                 "payment_still_armed": identity.get("payment_id") == self.card_attempt.context["payment_id"],
                                 "checkout_still_armed": identity.get("checkout_session_id") == self.card_attempt.checkout_session_id,
                                 "parent_generation_still_armed": parent_before.get("generation") == parent_after.get("generation") == self._armed_parent_generation,
                                 "parent_url_still_armed": parent_before.get("url") == parent_after.get("url") == self.card_attempt.parent_url
                                 and bool(parent) and parent.url == parent_after.get("url")}
                parent_verified = bool(self.checkout_navigation) and all(parent_checks.values())
                if not parent_verified:
                    if not self.checkout_navigation:
                        parent_checks["owned_navigation_available"] = False
                    error = NativePaymentParentError(parent_checks)
                    self.events.append({"decision": "deny_native_current_parent", "binding_checks": parent_checks,
                                        "first_failed_predicate": error.first_failed_predicate,
                                        "parent_equality_facts": parent_equality_facts,
                                        "parent_query_facts": parent_query_facts, "normalization_checks": normalization_checks})
                    raise error
                self.events.append({"decision": "native_current_parent_verified", "parent_equality_facts": parent_equality_facts,
                                    "parent_query_facts": parent_query_facts, "normalization_checks": normalization_checks})
                native_status = {
                    "frame_url": frame.url, "generation": fresh["generation"],
                    "parent_url": frame.parent_frame.url if frame.parent_frame else None,
                    "direct_child": bool(self.primary_page) and frame.parent_frame == self.primary_page.main_frame,
                    "stable_during_identity_read": frame.url == current_url and frame.parent_frame == parent
                    and bool(parent) and parent.url == parent_url and fresh.get("generation") == state["generation"]
                    and fresh.get("native") == native,
                    "checkout_session_id": context.get("checkout_session_id"),
                    "owned_checkout_url": parent_after.get("url") if parent_verified else None,
                    "owned_checkout_response_verified": parent_verified,
                    "native": fresh.get("native"), "identity": identity,
                }
            facts = self.card_attempt.check(
                request, headers, frame.url, state["generation"],
                native.get("return_url") if native.get("config_bound") and native.get("generation") == state["generation"] else None,
                native_status=native_status,
            )
            if facts.get("config_bound") and facts.get("operation") == "confirm" and request.method == "POST":
                facts["config_bound"] = await self.capture_confirm_protocol(parent_after)
                facts["binding_checks"]["accepted_CONFIRM_live_documents_bound"] = facts["config_bound"]
                if not facts["config_bound"]:
                    facts["first_failed_predicate"] = "accepted_CONFIRM_live_documents_bound"
        else:
            facts, observed = bind_bootstrap(request, headers, frame.url)
        observed["operation"] = facts.get("operation", "bootstrap")
        return facts, observed

    async def arm_native_pay(self, completed_fields, completed_cards):
        required = {"country", "address", "city", "state", "postal", "card_number", "card_cvc"}
        checks = {
            "ordinary_Test_card_capability_explicit": self.card_enabled is True,
            "required_synthetic_fields_completed": required <= completed_fields,
            "synthetic_expiry_completed": "card_expiry" in completed_fields
            or {"card_month", "card_year"} <= completed_fields,
            "primary_page_present": self.primary_page is not None,
        }
        self.require_arm_admission(checks)
        frames = [frame for frame in self.primary_page.frames if frame.parent_frame == self.primary_page.main_frame
                  and self.frame_context(frame.url, frame.parent_frame.url)]
        checks.update(single_current_direct_child=len(frames) == 1, native_card_attempt_absent=self.card_attempt is None)
        self.require_arm_admission(checks)
        frame = frames[0]
        observed_url = frame.url
        context = self.frame_context(observed_url, frame.parent_frame.url)
        self._native_protocol_url = observed_url
        if self.select_native_protocol:
            self._native_protocol_target = await self.select_native_protocol(frame)
        protocol_before = await self.protocol_frames()
        state = await frame.evaluate(READ_CONTEXT)
        parent_before = await self.read_parent(DOCUMENT_STATE)
        native = state.get("native") or {}
        contact = await self.read_parent(READ_CONTACT)
        identity = await self.frame_verify(frame_config(observed_url))
        private_contact = await self.frame_verify({**frame_config(observed_url), "operation": "contact",
                                                 "frame_generation": state["generation"]})
        current_state = await frame.evaluate(READ_CONTEXT)
        parent_after = await self.parent_document()
        parent_equality_facts = self.checkout_navigation.parent_equality_facts(identity.get("payment_link"), parent_after) if self.checkout_navigation else {}
        parent_query_facts = self.checkout_navigation.parent_query_facts(parent_after) if self.checkout_navigation else {}
        normalization_checks = self.checkout_navigation.normalization_checks(parent_after) if self.checkout_navigation else {}
        setup = self.native_setup_facts(context["config_id"], state["generation"])
        checks.update({
            "same_current_frame": frame.url == observed_url,
            "same_direct_parent": frame.parent_frame == self.primary_page.main_frame,
            "same_current_generation": current_state["generation"] == state["generation"],
            "same_verified_frame_context": self.frame_context(frame.url, frame.parent_frame.url) == context,
            "native_configuration_bound": native.get("config_bound") is True,
            "native_generation_current": native.get("generation") == state["generation"],
            **self.owned_checkout_checks(identity),
            "independent_current_payment_bound": identity.get("payment_id") == context["payment_id"],
            "independent_current_checkout_bound": identity.get("checkout_session_id") == context.get("checkout_session_id"),
            **(self.checkout_navigation.parent_checks(identity.get("payment_link"), parent_after) if self.checkout_navigation
               else {"independent_payment_link_available": False, "owned_checkout_parent_exact": False,
                     "owned_payment_navigation_generation_current": False}),
            "same_current_parent_document": parent_before.get("url") == parent_after.get("url") == frame.parent_frame.url
            and parent_before.get("generation") == parent_after.get("generation"),
            **{field + "_completion_current": completed_cards.get(field) == (context["config_id"], state["generation"])
               for field in sorted(required) if field.startswith("card_")},
            "card_expiry_completion_current": completed_cards.get("card_expiry") == (context["config_id"], state["generation"])
            or all(completed_cards.get(field) == (context["config_id"], state["generation"])
                   for field in ("card_month", "card_year")),
            **{key: setup[key] for key in ("bootstrap_account_methods_200", "bootstrap_customer_methods_200", "normal_type_observed")},
        })
        self.require_arm_admission(checks, parent_equality_facts=parent_equality_facts, parent_query_facts=parent_query_facts,
                                   normalization_checks=normalization_checks)
        checks["owned_parent_frozen"] = self.checkout_navigation.freeze_parent(identity["payment_link"], parent_after)
        self.require_arm_admission(checks, parent_equality_facts=parent_equality_facts, parent_query_facts=parent_query_facts,
                                   normalization_checks=normalization_checks)
        live = await self.protocol_frames()
        live_children = [child for child in live["children"] if child.get("url") == observed_url]
        checks["live_parent_and_native_loader_available"] = live == protocol_before and bool(live["main"]) and live["main"].get("url") == parent_after["url"] and len(live_children) == 1
        self.require_arm_admission(checks)
        self._armed_protocol = live
        billing = {"line1": "123 Test Street", "city": "New York", "state": "New York", "zip": "10001", "country": "US"}
        if contact.get("provider_name_observed"):
            billing.update(first_name="Tryfolk", last_name="Synthetic Customer")
        if contact.get("email"):
            billing["email"] = contact["email"]
        self.card_attempt = NativeCardAttempt(
            observed_url, state["generation"], self.policy.origin, browser_information(state), billing,
            parent_url=parent_after["url"], checkout_session_id=context.get("checkout_session_id"),
        )
        self._armed_payment_link, self._armed_parent_generation = identity["payment_link"], parent_after["generation"]
        self.card_attempt.freeze_contact_email(contact, parent_after["generation"])
        self.card_attempt.freeze_private_contact(private_contact.get("private_contact") or {}, identity, parent_after["generation"])
        self.card_attempt.arm_native_pay(observed_url, state["generation"], normal_bootstrap=self.native_setup_facts(context["config_id"], state["generation"])["normal_type_observed"])
        self.events.append({"decision": "native_pay_armed", "config_id": context["config_id"],
                            "independent_identity_rechecked": True, "frame_generation_bound": True,
                            "owned_checkout_parent_verified": True, "arm_admission_checks": dict(checks),
                            "parent_equality_facts": parent_equality_facts, "parent_query_facts": parent_query_facts,
                            "normalization_checks": normalization_checks})

    async def payment_response(self, response, facts):
        try:
            async with asyncio.timeout(5):
                raw = await response.body()
            if len(raw) > 200_000:
                raise ValueError("Oversized native payment response")
            import json
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Unknown native payment response")
            if facts.get("operation") in ("confirm", "poll") and self.card_attempt:
                error = body.get("error") if isinstance(body.get("error"), dict) else {}
                self.card_attempt.observe_response(body.get("status"), response.status,
                                                   operation=facts["operation"], error_code=error.get("code"))
        except Exception:
            if facts.get("operation") in ("confirm", "poll") and self.card_attempt:
                self.card_attempt.observe_response(None, response.status, operation=facts["operation"])
        finally:
            if self._payment_response_pending and not self._payment_response_pending.done():
                self._payment_response_pending.set_result(None)

    def bootstrap_response(self, response):
        facts = self._bootstrap_requests.pop(id(response.request), None)
        if facts is not None:
            operation = facts.get("operation", "bootstrap")
            decision = "widget_bootstrap_response" if operation == "bootstrap" else f"widget_{operation}_response"
            self.record(response.request, decision, status=response.status, **facts)
            observed = self._bootstrap_observations.pop(id(response.request), None)
            if operation == "bootstrap":
                asyncio.create_task(self.passive_bootstrap_response(response, facts, observed))
            elif self.card_enabled and operation in ("confirm", "poll"):
                self._payment_response_task = asyncio.create_task(self.payment_response(response, facts))

    def native_setup_facts(self, config_id, generation):
        """Read-only readiness for one generation, separate from card authority."""
        reads = self._read_generations.get(config_id, {})
        return {
            "bootstrap_account_methods_200": reads.get("/account/payment_methods") == generation,
            "bootstrap_customer_methods_200": reads.get("/customers/payment_methods") == generation,
            "normal_type_observed": config_id in self._normal_configs and self._normal_generations.get(config_id) == generation,
            "card_capability_disabled": not self.card_enabled,
            "card_attempt_absent": self.card_attempt is None,
        }

    async def passive_bootstrap_response(self, response, facts, observed):
        """Observe an already admitted GET; never arm, admit or consume payment."""
        request = response.request
        path = urlsplit(request.url).path
        checks = {"accepted_bootstrap_get_200": facts.get("config_bound") is True and request.method == "GET" and response.status == 200,
                  "current_child_configuration": False, "current_child_generation": False,
                  "independent_test_identity_current": False, "same_child_after_response": False}
        normal = False
        try:
            if observed:
                frame = request.frame
                context = self.frame_context(frame.url, frame.parent_frame.url) or {}
                checks["current_child_configuration"] = (
                    frame.parent_frame == self.primary_page.main_frame and frame.page == self.primary_page
                    and frame.url == observed["url"] and frame.parent_frame.url == observed["parent_url"]
                    and context.get("config_id") == facts["config_id"]
                )
                async with asyncio.timeout(13):
                    checks["current_child_generation"] = await frame.evaluate("performance.timeOrigin") == observed["generation"]
                    if checks["accepted_bootstrap_get_200"] and checks["current_child_configuration"] and checks["current_child_generation"]:
                        identity = await self.frame_verify(frame_config(frame.url))
                        checks["independent_test_identity_current"] = (
                            identity.get("approved") is True and identity.get("provider_environment") == "test_mode"
                            and identity.get("payment_correlated") is True and identity.get("payment_id") == context.get("payment_id")
                            and identity.get("checkout_session_id") == context.get("checkout_session_id")
                        )
                        if checks["independent_test_identity_current"] and path == "/account/payment_methods":
                            import json
                            raw = await response.body()
                            body = json.loads(raw) if len(raw) <= 200_000 else None
                            normal = isinstance(body, dict) and body.get("payment_type") in (None, "normal")
                    checks["same_child_after_response"] = (
                        frame.url == observed["url"] and frame.parent_frame == self.primary_page.main_frame
                        and frame.parent_frame.url == observed["parent_url"]
                        and await frame.evaluate("performance.timeOrigin") == observed["generation"]
                        and self.frame_context(frame.url, frame.parent_frame.url) == context
                    )
            if all(checks.values()):
                self._successful_reads.setdefault(facts["config_id"], set()).add(path)
                self._read_generations.setdefault(facts["config_id"], {})[path] = observed["generation"]
                if path == "/account/payment_methods" and normal:
                    self._normal_configs.add(facts["config_id"])
                    self._normal_generations[facts["config_id"]] = observed["generation"]
                elif path == "/account/payment_methods":
                    self._normal_configs.discard(facts["config_id"])
                    self._normal_generations.pop(facts["config_id"], None)
            normal = all(checks.values()) and normal
        except Exception as error:
            checks["passive_response_exception_absent"] = False
            checks["error_type"] = type(error).__name__
            normal = False
        self.record(request, "native_normal_type_check" if path == "/account/payment_methods" else "native_bootstrap_read_check",
                    normal_payment_type=normal, config_id=facts["config_id"], binding_checks={k:v for k,v in checks.items() if isinstance(v,bool)})

    async def preflight_request(self, session, event, *, observe_only=False, **target):
        """Validate native OPTIONS before Playwright's automatic synthetic204.

        This is transport permission, not a vendor CORS or business response.
        Raw protocol/header/query values remain local to this method.
        """
        permitted, request, facts = False, None, {}
        try:
            tree = (await session.send("Page.getFrameTree"))["frameTree"]

            def find(node):
                if node["frame"]["id"] == event.get("frameId"):
                    return node["frame"]["url"] + (node["frame"].get("urlFragment") or "")
                return next((url for child in node.get("childFrames", []) if (url := find(child))), None)

            url = find(tree)
            frames = [
                f for f in self.primary_page.frames
                if f.parent_frame == self.primary_page.main_frame
                and url and f.url == url
            ]
            if len(frames) != 1:
                raise ValueError("Preflight has no exact current owned secure-child initiator.")
            frame = frames[0]
            context = self.frame_context(frame.url, frame.parent_frame.url)
            if not context or not self.frame_verify or not self.policy or self.policy.provider != "dodo":
                raise ValueError("Preflight lacks an independently verified child configuration.")
            raw = event["request"]
            request = SimpleNamespace(
                frame=frame, url=raw["url"], method=raw["method"], resource_type="other",
                post_data=raw.get("postData"), redirected_from=event.get("redirectedRequestId"),
                is_navigation_request=lambda: False,
            )
            if request.method != "OPTIONS" or event.get("resourceType") != "Other":
                raise ValueError("Only browser preflight transport is admitted at this request boundary.")
            observed_url = frame.url
            facts, observed = await self.bind_api(request, {k.lower(): v for k, v in raw["headers"].items()}, frame)
            facts.update(frame_id_matched=True, reconstructed_url_matched=observed_url == url)
            if facts["config_bound"]:
                async with asyncio.timeout(13):
                    identity = await self.frame_verify(observed)
                permitted = (
                    identity.get("approved") is True
                    and identity.get("provider_environment") == "test_mode"
                    and identity.get("payment_correlated") is True
                    and identity.get("payment_id") == observed["payment_id"]
                    and identity.get("checkout_session_id") == context.get("checkout_session_id")
                    and frame.url == observed_url
                    and frame.parent_frame == self.primary_page.main_frame
                    and self.frame_context(frame.url, frame.parent_frame.url) == context
                )
                facts["independent_identity_rechecked"] = permitted
                binding(facts, "independent_test_identity_and_current_child", permitted)
                if permitted and facts.get("operation") in ("confirm", "poll"):
                    fresh_facts, _ = await self.bind_api(request, {k.lower(): v for k, v in raw["headers"].items()}, frame)
                    permitted = fresh_facts["config_bound"]
                    binding(facts, "fresh_preflight_child_request_bound", permitted)
        except Exception as error:
            facts["error_type"] = type(error).__name__
            binding(facts, "preflight_boundary_exception", False)
        facts.update(
            **target, vendor_options_response_observed=False,
            transport_layer="native_network_observation_gating_business_get" if observe_only else "cdp_request",
        )
        decision = "widget_cors_transport_guard" if permitted else "deny_cors_transport"
        if request:
            self.record(request, decision, **facts)
        else:
            self.events.append({"decision": decision, "method": "OPTIONS", **facts})
            del self.events[:-200]
        if not observe_only:
            await session.send(
                "Fetch.continueRequest" if permitted else "Fetch.failRequest",
                {"requestId": event["requestId"], **({} if permitted else {"errorReason": "BlockedByClient"})},
            )
        return permitted, facts

    def frame_context(self, url, parent_url):
        if exact_origin(parent_url) != DODO_CHECKOUT:
            return None
        return self._frame_contexts.get(hashlib.sha256(url.encode()).hexdigest())

    def record(self, request, decision, **extra):
        parsed = urlsplit(request.url)
        try:
            frame = request.frame
            page = frame.page
            identity = (
                "primary"
                if page == self.primary_page
                else self.page_ids.setdefault(page, f"extra-{len(self.page_ids) + 1}")
            )
            child_frame = frame != page.main_frame
            frame_unavailable = False
            initiator = urlsplit(frame.url)
            initiator_url = f"{initiator.scheme}://{initiator.hostname or ''}{':' + str(initiator.port) if initiator.port else ''}{initiator.path}"
        except Exception:
            # The first popup navigation can precede its Playwright Frame.
            # Audit it without weakening the route's fail-closed decision.
            identity = "unavailable_new_document"
            child_frame = None
            frame_unavailable = True
            initiator_url = None
        if f"{parsed.scheme}://{parsed.netloc}" == DODO_CHECKOUT:
            # Provider route tokens are private even when no query is present.
            recorded_url = DODO_CHECKOUT + "/:private_checkout_route"
        else:
            recorded_url = f"{parsed.scheme}://{parsed.hostname or ''}{':' + str(parsed.port) if parsed.port else ''}{parsed.path}"
        if initiator_url and initiator_url.startswith(DODO_CHECKOUT + "/"):
            initiator_url = DODO_CHECKOUT + "/:private_checkout_route"
        self.events.append(
            redact_frame_config(
                {
                    "url": recorded_url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "decision": decision,
                    "page_identity": identity,
                    "child_frame": child_frame,
                    "frame_unavailable": frame_unavailable,
                    "initiator_url": initiator_url,
                    **extra,
                }
            )
        )
        del self.events[:-200]

    async def route(self, route):
        request = route.request
        try:
            frame = request.frame
            child_navigation = (
                bool(self.primary_page)
                and request.is_navigation_request()
                and frame != self.primary_page.main_frame
            )
            if self.primary_page and frame.page != self.primary_page:
                self.record(request, "deny", reason="unapproved_extra_document")
                await route.abort("blockedbyclient")
                return
            if self.card_attempt and self.card_attempt.confirmed and request.is_navigation_request() and frame == self.primary_page.main_frame:
                admitted, return_facts = await self.bind_return_navigation(request)
                self.record(request, "native_return_navigation" if admitted else "deny",
                            **return_facts, **({} if admitted else {"reason": "unapproved_card_return"}))
                if not admitted:
                    await route.abort("blockedbyclient")
                    return
            parent_url = (
                frame.parent_frame.url
                if self.primary_page and frame.parent_frame == self.primary_page.main_frame
                else None
            )
            parsed = urlsplit(request.url)
            canonical = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            headers = (
                await request.all_headers()
                if canonical == DODO_SECURE_FRAME or request.url in FRAME_SCRIPTS or is_browser_api(request.url)
                else {}
            )
            credentials = any(name in headers for name in ("cookie", "authorization", "proxy-authorization"))
            policy_url, document_url = request.url, frame.url
            if child_navigation and canonical == DODO_SECURE_FRAME and parsed.query:
                if (
                    not self.policy
                    or self.policy.provider != "dodo"
                    or not parent_url
                    or exact_origin(parent_url) != DODO_CHECKOUT
                    or request.method != "GET"
                    or request.resource_type != "document"
                    or request.redirected_from is not None
                    or request.post_data is not None
                    or credentials
                    or not self.frame_verify
                ):
                    raise ValueError("Generated frame is outside the bounded direct-child document grant.")
                element = await frame.frame_element()
                raw = await element.get_attribute("src")
                if not raw or urldefrag(raw)[0] != request.url:
                    raise ValueError("Observed frame source differs from its requested document.")
                try:
                    context = frame_config(raw)
                except FrameConfigError as error:
                    self.record(request, "deny_frame_config", context=error.evidence)
                    await route.abort("blockedbyclient")
                    return
                async with asyncio.timeout(13):
                    identity = await self.frame_verify(context)
                if (
                    identity.get("approved") is not True
                    or identity.get("provider_environment") != "test_mode"
                    or identity.get("payment_correlated") is not True
                    or identity.get("payment_id") != context["payment_id"]
                ):
                    self.record(request, "deny_frame_identity", context=context, identity=identity)
                    await route.abort("blockedbyclient")
                    return
                context.update(identity)
                if self.response_guard and not await self.response_guard(frame):
                    raise ValueError("Secure child lacks an active response target boundary.")
                self._frame_contexts[context["config_id"]] = context
                self.record(request, "verified_frame_context", context=context)
                policy_url = DODO_SECURE_FRAME
            verified_context = self.frame_context(frame.url, parent_url) if parent_url else None
            if is_browser_api(request.url):
                if (
                    self.policy and self.policy.provider == "dodo"
                    and verified_context and self.frame_verify
                    and not request.is_navigation_request()
                ):
                    observed_url = frame.url
                    facts, observed = await self.bind_api(request, headers, frame)
                    if facts["config_bound"]:
                        async with asyncio.timeout(13):
                            identity = await self.frame_verify(observed)
                        facts["independent_identity_rechecked"] = (
                            identity.get("approved") is True
                            and identity.get("provider_environment") == "test_mode"
                            and identity.get("payment_correlated") is True
                            and identity.get("payment_id") == observed["payment_id"]
                            and identity.get("checkout_session_id") == verified_context.get("checkout_session_id")
                            and frame.url == observed_url
                            and frame.parent_frame == self.primary_page.main_frame
                            and self.frame_context(frame.url, frame.parent_frame.url) == verified_context
                        )
                        binding(facts, "independent_test_identity_and_current_child", facts["independent_identity_rechecked"])
                        response_covered = (
                            facts["independent_identity_rechecked"]
                            and self.response_guard
                            and await self.response_guard(frame)
                        )
                        facts["response_target_guarded"] = bool(response_covered)
                        binding(facts, "active_response_target", response_covered)
                        preflight_bound = (
                            response_covered and self.preflight_guard
                            and await self.preflight_guard(request.url, observed_url)
                        )
                        facts["native_preflight_bound"] = bool(preflight_bound)
                        binding(facts, "native_preflight_bound", preflight_bound)
                        startup = facts.get("operation") == "session_tokens" and request.method == "POST"
                        if startup and observed["config_id"] in self._startup_configs:
                            preflight_bound = False
                            facts["request_limit_exhausted"] = True
                            binding(facts, "single_session_tokens_request", False)
                        if preflight_bound and facts.get("operation") in ("confirm", "poll") and request.method != "OPTIONS":
                            fresh_facts, _ = await self.bind_api(request, headers, frame)
                            binding(facts, "fresh_child_request_bound", fresh_facts["config_bound"])
                            if not fresh_facts["config_bound"]:
                                facts["fresh_binding_failure"] = fresh_facts.get("first_failed_predicate")
                            consumed = fresh_facts["config_bound"] and self.card_attempt.consume(facts["operation"])
                            preflight_bound = binding(facts, "single_confirm_or_bounded_poll_consumed", consumed)
                            if not preflight_bound:
                                facts["request_limit_exhausted"] = True
                        if preflight_bound:
                            if facts.get("operation") in ("confirm", "poll") and request.method != "OPTIONS":
                                self._payment_response_pending = asyncio.get_running_loop().create_future()
                            if startup:
                                self._startup_configs.add(observed["config_id"])
                            if facts.get("operation") == "bootstrap":
                                async with asyncio.timeout(5):
                                    generation = await frame.evaluate("performance.timeOrigin")
                                if frame.url != observed_url or self.frame_context(frame.url, frame.parent_frame.url) != verified_context:
                                    raise ValueError("Bootstrap child changed before passive response binding.")
                                self._bootstrap_observations[id(request)] = {"url": observed_url, "parent_url": frame.parent_frame.url, "generation": generation}
                                if len(self._bootstrap_observations) > 200:
                                    self._bootstrap_observations.pop(next(iter(self._bootstrap_observations)))
                            self._bootstrap_requests[id(request)] = facts
                            if len(self._bootstrap_requests) > 200:
                                self._bootstrap_requests.pop(next(iter(self._bootstrap_requests)))
                            self.record(
                                request,
                                "widget_bootstrap_preflight" if request.method == "OPTIONS" else
                                "widget_session_tokens" if startup else
                                f"widget_{facts['operation']}" if facts.get("operation") in ("confirm", "poll") else "widget_bootstrap_get",
                                **facts,
                            )
                            await route.continue_()
                            return
                    self.record(request, "deny_widget_bootstrap", **facts)
                else:
                    self.record(request, "deny_widget_bootstrap", reason="unverified_child_frame")
                await route.abort("blockedbyclient")
                return
            if verified_context:
                document_url = DODO_SECURE_FRAME
            elif (
                self.primary_page
                and frame != self.primary_page.main_frame
                and not request.is_navigation_request()
            ):
                self.record(request, "deny", reason="unverified_child_frame", initiator=document_url)
                await route.abort("blockedbyclient")
                return
            decision = (
                self.policy.decide(
                    policy_url,
                    request.method,
                    request.resource_type,
                    document_url,
                    request.is_navigation_request(),
                    request.redirected_from is not None,
                    request.post_data,
                    primary_parent_url=parent_url,
                    credentials=credentials,
                )
                if self.policy
                else "deny"
            )
            if child_navigation and decision != "public_frame":
                self.record(request, "deny", reason="unapproved_extra_document")
                await route.abort("blockedbyclient")
                return
            if decision == "continue":
                if self.policy.provider == "dodo" and request.url == self.policy.origin + "/api/target/checkouts":
                    if not self.bind_owned_checkout_request(request):
                        self.record(request, "deny", reason="owned_checkout_creation_not_single_primary_fetch")
                        await route.abort("blockedbyclient")
                        return
                if self.policy.provider == "dodo" and request.url.startswith(DODO_CHECKOUT + "/api/checkout/sessions/") and urlsplit(request.url).path.endswith("/confirm"):
                    if not await self.bind_billing_submission(request):
                        self.events.append({"decision": "deny_billing_submission", "owned_session_request_bound": False})
                        await route.abort("blockedbyclient")
                        return
                await route.continue_()
                return
            if decision in ("public_script", "public_frame_script", "public_frame"):
                # Fulfill with freshly fetched, unmodified provider bytes. Never
                # forward browser Cookie/Authorization/Referer or a redirect.
                async with asyncio.timeout(10):
                    body, headers = await (
                        public_frame(policy_url) if decision == "public_frame" else public_script(request.url)
                    )
                await route.fulfill(status=200, body=body, headers=headers)
                self.record(
                    request,
                    decision,
                    sha256=hashlib.sha256(body).hexdigest(),
                    bytes=len(body),
                    credential_headers_forwarded=False,
                    redirects_followed=0,
                )
                return
            self.record(request, "deny")
        except Exception as error:
            self.record(request, "deny", error_type=type(error).__name__)
        await route.abort("blockedbyclient")

    async def capture_owned_checkout_boundary(self, session, event, target):
        """Read the existing response before the browser can navigate away."""
        request = event.get("request", {})
        if not self.policy or request.get("url") != self.policy.origin + "/api/target/checkouts":
            return False

        checks = {
            "dodo_policy": self.policy.provider == "dodo", "primary_page_present": bool(self.primary_page),
            "primary_page_target": target.get("target_type") == "page"
            and target.get("target_id") == self.primary_response_target,
            # Chrome reports this actual window.fetch as XHR at Fetch's
            # response stage. Authority comes from the observed browser fetch,
            # not from treating a protocol resource label as a new grant.
            "fetch_resource": event.get("resourceType") in ("Fetch", "XHR"),
            "actual_single_browser_fetch": self._owned_creation_request is not None
            and self._owned_creation_request.frame == self.primary_page.main_frame
            and self._owned_creation_request.url == request.get("url")
            and self._owned_creation_request.method == "POST"
            and self._owned_creation_request.resource_type == "fetch"
            and self._owned_creation_request.redirected_from is None,
            "creation_response_201": event.get("responseStatusCode") == 201,
            "creation_POST": request.get("method") == "POST",
            "no_redirect": not event.get("redirectedRequestId"),
        }
        self.events.append({"decision": "owned_checkout_capture_candidate", "binding_checks": checks})
        if not all(checks.values()):
            return False
        try:
            async with asyncio.timeout(5):
                tree = await session.send("Page.getFrameTree")
                checks["primary_main_frame"] = event.get("frameId") == tree["frameTree"]["frame"]["id"]
                if not checks["primary_main_frame"]:
                    return False
                response = await session.send("Fetch.getResponseBody", {"requestId": event["requestId"]})
            encoded = response.get("body")
            if not isinstance(encoded, str) or len(encoded) > 30_000:
                return False
            raw = base64.b64decode(encoded, validate=True) if response.get("base64Encoded") else encoded.encode()

            async def body():
                return raw

            captured = await self.observe_owned_checkout_response(SimpleNamespace(
                request=self._owned_creation_request,
                status=201, url=request["url"], body=body,
            ))
            if captured:
                self._owned_creation_request = None
            return captured
        except Exception as error:
            self.events.append({"decision": "owned_checkout_response_unavailable", "error_type": type(error).__name__})
            return False

    async def capture_billing_boundary(self, session, event, target):
        """Capture existing current source/POST response; never fetch or replay."""
        navigation, request = self.checkout_navigation, event.get("request", {})
        if not navigation or not self.policy or self.policy.provider != "dodo" or not self.primary_page:
            return False
        url = request.get("url", "")
        pending = self._billing_submission_request
        submitted = bool(pending) and url == pending.url
        checks = {}

        def unverified():
            self.events.append({
                "decision": "billing_response_unverified" if submitted else "billing_source_unverified",
                "binding_checks": dict(checks),
                "first_capture_subpredicate": next((name for name, value in checks.items() if not value), None),
            })
            return False

        try:
            parsed = urlsplit(url)
            script = f"{parsed.scheme}://{parsed.netloc}" == DODO_CHECKOUT and parsed.path.endswith(".js")
            if not (submitted or script):
                return False
            if not submitted:
                # Existing Next.js scripts carry one deployment cache key. This
                # recognizes source metadata, never a payment-parent URL alias.
                # Preserve encoded route segments without decoding/rebuilding.
                checks.update({
                    "source_public_static_path": parsed.path.startswith("/_next/static/"),
                    "source_deployment_query_bound": not parsed.query or bool(re.fullmatch(r"dpl=dpl_[A-Za-z0-9]{1,128}", parsed.query)),
                    "source_fragment_absent": not parsed.fragment and "#" not in url,
                    "source_credentials_absent": not (parsed.username or parsed.password),
                })
            headers = event.get("responseHeaders", [])
            types = [header["value"].split(";", 1)[0].strip().lower() for header in headers if header["name"].lower() == "content-type"]
            checks.update({
                "primary_page_target": target.get("target_type") == "page" and target.get("target_id") == self.primary_response_target,
                "response_200": event.get("responseStatusCode") == 200,
                "no_redirect": not event.get("redirectedRequestId") and not any(h["name"].lower() == "location" for h in headers),
                "resource_method_bound": request.get("method") == ("POST" if submitted else "GET")
                and event.get("resourceType") in (("Fetch", "XHR") if submitted else ("Script",)),
                "response_content_type_bound": len(types) == 1 and types[0] in (("application/json",) if submitted else ("application/javascript", "text/javascript")),
                "existing_submission_bound": not submitted or pending.frame == self.primary_page.main_frame
                and pending.method == "POST" and pending.resource_type == "fetch" and pending.redirected_from is None
                and request.get("postData") == pending.post_data,
            })
            if not all(checks.values()):
                return unverified()
            async with asyncio.timeout(5):
                tree = (await session.send("Page.getFrameTree"))["frameTree"]["frame"]
                checks["primary_main_frame"] = event.get("frameId") == tree["id"]
                status_phase = bool(navigation.status_destination) and tree["url"] == navigation.status_destination
                checks["authorized_status_document_current" if status_phase else "original_owned_entry_current"] = status_phase or tree["url"] == navigation.entry_url
                if not all(checks.values()):
                    return unverified()
                source_protocol_before = (await self.protocol_frames())["main"] if status_phase else None
                before = await self.read_parent(DOCUMENT_STATE)
                body = await session.send("Fetch.getResponseBody", {"requestId": event["requestId"]})
                after = await self.read_parent(DOCUMENT_STATE)
            checks["same_document_during_capture"] = before.get("url") == after.get("url") and before.get("generation") == after.get("generation")
            if not all(checks.values()):
                return unverified()
            encoded = body.get("body")
            limit = 30_000 if submitted else 3_000_000
            checks["response_body_bounded"] = isinstance(encoded, str) and 0 < len(encoded) <= limit
            if not all(checks.values()):
                return unverified()
            checks["response_body_decoded"] = False
            raw = base64.b64decode(encoded, validate=True) if body.get("base64Encoded") else encoded.encode()
            checks["response_body_decoded"] = True
            if submitted:
                captured = navigation.observe_response(raw, after)
                self._billing_submission_request = None
                checks["current_source_bound"] = navigation.source_generation == after.get("generation")
                checks["owned_transition_response_bound"] = captured
                self.events.append({"decision": "owned_billing_response_captured" if captured else "billing_response_unverified",
                                    "binding_checks": checks, "private_routing_values_retained": captured,
                                    "current_source_bound": checks["current_source_bound"],
                                    "first_capture_subpredicate": next((name for name, value in checks.items() if not value), None)})
            else:
                source_digest = hashlib.sha256(raw).hexdigest()
                checks.update(navigation.source_checks(source_digest, after))
                if source_digest in (STATUS_SOURCE_SHA256, WALLET_SOURCE_SHA256):
                    current = (await self.protocol_frames())["main"]
                    initial = source_protocol_before
                    checks["status_live_loader_unchanged_during_source_capture"] = bool(current) and current == initial and current.get("url") == after.get("url")
                    checks["status_new_loader_from_accepted_CONFIRM"] = bool(self._confirmed_protocol) and current.get("loader_id") != self._confirmed_protocol["main"].get("loader_id")
                    if not all(checks.values()):
                        return unverified()
                    if navigation.status_protocol and navigation.status_protocol != current:
                        return unverified()
                    navigation.status_protocol = current
                captured = navigation.observe_source(source_digest, after)
                if captured:
                    self.events.append({"decision": "owned_billing_source_captured", "current_generation_bound": True,
                                        "source_role": "query_normalization" if source_digest == NORMALIZER_SOURCE_SHA256
                                        else "post_CONFIRM_return" if source_digest == RETURN_SOURCE_SHA256
                                        else "status_provider_return" if source_digest in (STATUS_SOURCE_SHA256, WALLET_SOURCE_SHA256) else "billing_routing",
                                        "source_sha256": source_digest, "binding_checks": dict(checks)})
                else:
                    unverified()
                    self.events[-1]["source_sha256"] = source_digest
                    return False
            return captured
        except Exception as error:
            if all(checks.values()):
                checks["capture_exception_absent"] = False
            unverified()
            self.events[-1]["error_type"] = type(error).__name__
            return False

    async def redirect_response(self, session, event, **target):
        """Response-stage CDP interception, separate from both request handlers.

        Playwright skips route callbacks on HTTP redirects. Inspect Location
        before the browser can follow it, without issuing a duplicate request.
        Extra pages/frames are denied at their initial document request.
        """
        request_id = event["requestId"]
        status = event.get("responseStatusCode", 0)
        headers = event.get("responseHeaders", [])
        locations = [h["value"] for h in headers if h["name"].lower() == "location"]
        if self.card_attempt and event.get("request", {}).get("url") == self.card_attempt.provider_return_url():
            try:
                admitted = await self.bind_provider_redirect(session, event, target)
            except Exception as error:
                admitted = False
                self.events.append({"decision": "deny_provider_return_redirect", "error_type": type(error).__name__})
            await session.send("Fetch.continueResponse" if admitted else "Fetch.failRequest",
                               {"requestId": request_id, **({} if admitted else {"errorReason": "BlockedByClient"})})
            return
        if status in (301, 302, 303, 307, 308) and (
            len(locations) != 1
            or not self.policy
            or not self.policy.redirect_allowed(event["request"]["url"], locations[0])
            or self.card_attempt and not approved_redirect(self.card_attempt, event["request"]["url"], locations[0])
        ):
            parsed = urlsplit(event["request"]["url"])
            self.events.append(
                {
                    "url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                    "decision": "deny_redirect",
                    "status": status,
                    **target,
                }
            )
            del self.events[:-200]
            await session.send(
                "Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"}
            )
        else:
            await self.capture_owned_checkout_boundary(session, event, target)
            await self.capture_billing_boundary(session, event, target)
            await session.send("Fetch.continueResponse", {"requestId": request_id})
