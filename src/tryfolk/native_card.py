"""One observed normal Test Mode card attempt, without sending any request."""

import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit, urljoin, quote
from .browser_policy import DODO_CHECKOUT, TEST_FIELDS
from .secure_frame import BROWSER_API, frame_config
from .widget_bootstrap import APP_HEADERS, BROWSER_HEADERS, SDK_ORIGIN


def binding(facts, name, condition):
    """Name only the predicate; never serialize any compared private value."""
    accepted = bool(condition)
    facts.setdefault("binding_checks", {})[name] = accepted
    if not accepted and facts.get("first_failed_predicate") is None:
        facts["first_failed_predicate"] = name
    return accepted


def browser_name_version(user_agent):
    for name, pattern in (
        ("Instagram", r"Instagram/([\d]+\.[\w]?\.?[\w]+)"),
        ("Facebook", r"FBAV/([\d]+\.[\w]?\.?[\w]+)"),
        ("Twitter", r"iPhone/([\d]+\.[\w]?\.?[\w]+)"),
        ("LinkedIn", r"LinkedInApp/([\d]+\.[\w]?\.?[\w]+)"),
        ("Microsoft Edge", r"Edg/([\d]+\.[\w]?\.?[\w]+)"),
        ("Chrome", r"Chrome/([\d]+\.[\w]?\.?[\w]+)"),
        ("Safari", r"Safari/([\d]+\.[\w]?\.?[\w]+)"),
        ("Opera", r"Opera/([\d]+\.[\w]?\.?[\w]+)"),
        ("Firefox", r"Firefox/([\d]+\.[\w]?\.?[\w]+)"),
    ):
        if match := re.search(pattern, user_agent, re.I):
            return name, match.group(1)
    return "Others", "0"


class NormalCardBody:
    """Private closed-body validation without a request/payment capability."""
    def __init__(self, raw, generation, owned_origin, browser_info, billing):
        self.context = frame_config(raw)
        self.raw, self.generation, self.owned_origin = raw, generation, owned_origin
        self.query = dict(parse_qsl(urlsplit(raw).query))
        self.browser_info, self.billing = browser_info, billing
        self.created_utc = datetime.now(timezone.utc)
        self.return_url = None

    def diagnose(self, body, native_return):
        facts = {"binding_checks": {}, "first_failed_predicate": None, "request_admitted": False,
                 "body_field_names": sorted(body)[:50] if isinstance(body, dict) else []}
        facts["closed_body_valid"] = self._normal_body(body, native_return, facts)
        self.return_url = None
        return facts

    def return_allowed(self, raw, *, native_status=None):
        try:
            parsed = urlsplit(raw)
            origin = f"{parsed.scheme}://{parsed.netloc}"
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            if parsed.username or parsed.password or parsed.fragment or len(query) != len(parse_qsl(parsed.query)):
                return False
            if origin == self.owned_origin and parsed.path == "/checkout/return":
                return (
                    set(query) <= {"payment_id", "status"}
                    and query.get("payment_id", self.context["payment_id"]) == self.context["payment_id"]
                    and query.get("status", "succeeded") in ("succeeded", "failed", "cancelled", "processing")
                )
            # A native provider intermediate needs an observed exact return and
            # the independently correlated payment ID in an exact path segment.
            return (
                origin == DODO_CHECKOUT
                and parsed.path == "/" + self.context["payment_id"]
                and not query
            )
        except (ValueError, TypeError):
            return False

    def _normal_body(self, body, native_return, facts, *, native_status=None):
        # Official1.1.0 le() expands either spelling of this sole placeholder
        # when the widget's own Pay button calls Bt(). No bridge CONFIRM is
        # required in that branch; the passive INIT observation stays a template.
        if isinstance(native_return, str):
            payment = quote(self.context["payment_id"], safe="")
            native_return = native_return.replace("{PAYMENT_ID}", payment).replace("%7BPAYMENT_ID%7D", payment)
        required = {"payment_type", "payment_method", "client_secret", "return_url", "browser_info", "payment_method_data"}
        if not binding(facts, "body_fields_closed", isinstance(body, dict) and required <= body.keys() and body.keys() <= required | {"customer_acceptance"}):
            return False
        # Evaluate independent closed fields even after a denied destination.
        # binding keeps the original first failure; no failure grants admission
        # or stores a return URL, and malformed nested objects are never read.
        valid = all([binding(facts, name, accepted) for name, accepted in (
            ("normal_payment_type", body["payment_type"] == "normal"),
            ("normal_card_method", body["payment_method"] == "card"),
            ("client_secret_bound", body["client_secret"] == self.query["clientSecret"]),
            ("browser_info_bound", json.dumps(body["browser_info"], sort_keys=True) == json.dumps(self.browser_info, sort_keys=True)),
            ("native_return_present", isinstance(native_return, str) and bool(native_return)),
            ("native_return_bound", body["return_url"] == native_return),
            ("return_destination_approved", self.return_allowed(native_return, native_status=native_status)),
        )])
        data = body["payment_method_data"]
        data_closed = binding(facts, "payment_method_data_fields_closed", isinstance(data, dict) and {"card"} <= data.keys() and data.keys() <= {"card", "billing"})
        valid = data_closed and valid
        if data_closed:
            card = data["card"]
            required_card = {"card_number", "card_exp_month", "card_exp_year", "card_cvc", "card_issuer"}
            card_closed = binding(facts, "card_fields_closed", isinstance(card, dict) and required_card <= card.keys() and card.keys() <= required_card | {"card_network", "card_holder_name"})
            valid = card_closed and valid
            if card_closed:
                card_valid = all([binding(facts, name, accepted) for name, accepted in (
                    ("test_card_number_bound", card["card_number"] == TEST_FIELDS["card_number"]),
                    ("test_card_expiry_bound", card["card_exp_month"] == "06" and card["card_exp_year"] == "2032"),
                    ("test_card_cvc_bound", card["card_cvc"] == TEST_FIELDS["card_cvc"]),
                    ("test_card_issuer_bound", card["card_issuer"] == ""),
                    ("test_card_network_bound", card.get("card_network", "Visa") == "Visa"),
                    ("test_card_holder_bound", card.get("card_holder_name", TEST_FIELDS["card_name"]) == TEST_FIELDS["card_name"]),
                )])
                valid = card_valid and valid
            if "billing" in data:
                billing = data["billing"]
                billing_closed = binding(facts, "billing_fields_closed", isinstance(billing, dict) and billing.keys() <= {"address", "email"})
                valid = billing_closed and valid
                if billing_closed:
                    if "email" in billing:
                        valid = binding(facts, "billing_email_bound", isinstance(billing["email"], str) and billing["email"] == self.billing.get("email")) and valid
                    address = billing.get("address", {})
                    address_closed = binding(facts, "billing_address_fields_closed", isinstance(address, dict) and address.keys() <= {"line1", "city", "state", "zip", "country", "first_name", "last_name"})
                    valid = address_closed and valid
                    if address_closed:
                        address_valid = all([binding(facts, "billing_" + key + "_bound", isinstance(value, str) and value == self.billing.get(key)) for key, value in address.items()])
                        valid = address_valid and valid
        if "customer_acceptance" in body:
            acceptance = body["customer_acceptance"]
            acceptance_closed = binding(facts, "customer_acceptance_fields_closed", isinstance(acceptance, dict) and set(acceptance) == {"acceptance_type", "accepted_at", "online"})
            valid = acceptance_closed and valid
            if acceptance_closed:
                valid = binding(facts, "customer_acceptance_online_bound", acceptance["acceptance_type"] == "online" and acceptance["online"] == {"user_agent": self.browser_info["user_agent"]}) and valid
                try:
                    accepted = datetime.fromisoformat(acceptance["accepted_at"].replace("Z", "+00:00"))
                    time_valid = accepted.tzinfo is not None and -5 <= (accepted - self.created_utc).total_seconds() <= (datetime.now(timezone.utc) - self.created_utc).total_seconds() + 5
                except (ValueError, TypeError, AttributeError):
                    time_valid = False
                valid = binding(facts, "customer_acceptance_time_bound", time_valid) and valid
        if not valid:
            return False
        self.return_url = native_return
        return True


class NativeCardAttempt(NormalCardBody):
    def __init__(self, raw, generation, owned_origin, browser_info, billing, *,
                 parent_url=None, checkout_session_id=None, clock=time.monotonic):
        super().__init__(raw, generation, owned_origin, browser_info, billing)
        self.parent_url, self.checkout_session_id = parent_url, checkout_session_id
        self._admitted_status_return = None
        self.clock = clock
        self.armed_at = self.confirmed_at = None
        self.confirmed = self.closed = False
        self.polls = 0
        self.fallback_remaining, self.confirm_response_failed = 0, False

    def status_return_checks(self, raw, observation):
        """Private exact native CONFIRM binding; opaque route segments carry no identity."""
        observation = observation if isinstance(observation, dict) else {}
        native = observation.get("native")
        native = native if isinstance(native, dict) else {}
        identity = observation.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        try:
            parsed = urlsplit(raw) if isinstance(raw, str) else urlsplit("")
            parent = urlsplit(self.parent_url or "")
        except ValueError:
            parsed = parent = urlsplit("")
        return {
            "Test_status_origin": f"{parsed.scheme}://{parsed.netloc}" == DODO_CHECKOUT,
            "finite_status_shape": bool(re.fullmatch(r"/status/[A-Za-z0-9_-]{1,256}/[A-Za-z0-9_-]{1,256}", parsed.path)),
            "status_no_query_fragment_userinfo": isinstance(raw, str) and "?" not in raw and "#" not in raw
            and not (parsed.query or parsed.fragment or parsed.username or parsed.password),
            "expected_Test_parent": bool(self.parent_url) and f"{parent.scheme}://{parent.netloc}" == DODO_CHECKOUT,
            "same_current_parent": observation.get("parent_url") == self.parent_url,
            "owned_checkout_response_parent_bound": observation.get("owned_checkout_response_verified") is True
            and observation.get("owned_checkout_url") == self.parent_url,
            "same_current_frame": observation.get("frame_url") == self.raw,
            "same_current_generation": observation.get("generation") == self.generation,
            "direct_current_child": observation.get("direct_child") is True,
            "stable_during_identity_read": observation.get("stable_during_identity_read") is True,
            "native_configuration_bound": native.get("config_bound") is True,
            "native_INIT_origin_parent_bound": native.get("init_origin_bound") is True and native.get("parent_source_bound") is True,
            "native_generation_current": native.get("generation") == self.generation,
            "fresh_native_CONFIRM_source": native.get("return_source") == "CONFIRM" and native.get("confirm_observed") is True,
            "native_CONFIRM_origin_parent_bound": native.get("confirm_origin_bound") is True and native.get("confirm_parent_source_bound") is True,
            "native_CONFIRM_generation_current": native.get("confirm_generation") == self.generation,
            "exact_native_status_return": native.get("return_type") == "string" and native.get("return_url") == raw,
            "independent_Test_identity": identity.get("approved") is True and identity.get("provider_environment") == "test_mode",
            "independent_payment_customer": identity.get("payment_correlated") is True and identity.get("customer_correlated") is True and identity.get("payment_id") == self.context["payment_id"],
            "independent_current_checkout": bool(self.checkout_session_id) and observation.get("checkout_session_id") == self.checkout_session_id and identity.get("checkout_session_id") == self.checkout_session_id,
        }

    def return_allowed(self, raw, *, native_status=None):
        return super().return_allowed(raw) or all(self.status_return_checks(raw, native_status).values())

    def arm_native_pay(self, raw, generation, *, normal_bootstrap):
        if self.closed or self.armed_at is not None or not normal_bootstrap or raw != self.raw or generation != self.generation:
            raise ValueError("Native Pay requires the same current normal-card child and a fresh single action.")
        self.armed_at = self.clock()

    def navigation_allowed(self, raw):
        if not self.confirmed:
            return True
        parsed = urlsplit(raw)
        if f"{parsed.scheme}://{parsed.netloc}" == self.owned_origin:
            return parsed.path in ("/checkout/return", "/special-downloads", "/api/target/download") and (
                parsed.path != "/checkout/return" or self.return_allowed(raw)
            )
        if not self.return_url:
            return False
        if self._admitted_status_return:
            return raw == self._admitted_status_return == self.return_url
        return self.return_allowed(raw) and urlsplit(raw).path == urlsplit(self.return_url).path

    def check(self, request, headers, raw, generation, native_return, *, native_status=None):
        parsed = urlsplit(request.url)
        operation = "confirm" if parsed.path == f"/payments/{self.context['payment_id']}/confirm" else "poll"
        facts = {
            "config_bound": False, "config_id": self.context["config_id"], "operation": operation,
            "query_fields": sorted({key for key, _ in parse_qsl(parsed.query)}),
            "header_names": sorted(headers), "body_absent": request.post_data is None,
            "binding_checks": {}, "first_failed_predicate": None,
        }
        body = None
        if request.post_data is not None:
            try:
                body = json.loads(request.post_data) if len(request.post_data) <= 100_000 else None
            except (ValueError, TypeError):
                pass
            facts["body_field_names"] = sorted(body)[:50] if isinstance(body, dict) else []
        if not all(binding(facts, name, accepted) for name, accepted in (
            ("attempt_armed_and_open", not self.closed and self.armed_at is not None),
            ("current_child_configuration", raw == self.raw and generation == self.generation),
            ("service_origin", f"{parsed.scheme}://{parsed.netloc}" == BROWSER_API and not parsed.fragment),
            ("native_non_navigation_request", request.redirected_from is None and not request.is_navigation_request()),
            ("native_request_type", request.resource_type in ("fetch", "other")),
        )):
            return facts
        is_preflight = request.method == "OPTIONS" and request.resource_type == "other"
        method = "POST" if operation == "confirm" else "GET"
        expected_query = [] if operation == "confirm" else [
            ("force_sync", "true"), ("client_secret", self.query["clientSecret"])
        ]
        phase = (
            not self.confirmed and self.clock() - self.armed_at <= 60 if operation == "confirm"
            else self.confirmed and self.polls < 25 and self.clock() - self.confirmed_at < 300
            and (not self.confirm_response_failed or self.fallback_remaining > 0)
        )
        if not all(binding(facts, name, accepted) for name, accepted in (
            ("same_payment_path", parsed.path in (f"/payments/{self.context['payment_id']}/confirm", f"/payments/{self.context['payment_id']}")),
            ("exact_request_query", parse_qsl(parsed.query, keep_blank_values=True) == expected_query),
            ("native_child_origin", headers.get("origin") == SDK_ORIGIN),
            ("confirmation_phase" if operation == "confirm" else "poll_phase", phase),
        )):
            return facts
        app_headers = APP_HEADERS | ({"x-client-source"} if operation == "confirm" else set())
        if is_preflight:
            names = {part.strip().lower() for part in headers.get("access-control-request-headers", "").split(",")}
            if not all(binding(facts, name, accepted) for name, accepted in (
                ("preflight_body_absent", request.post_data is None),
                ("preflight_operation_method", headers.get("access-control-request-method") == method),
                ("preflight_declared_header_names", names <= app_headers and "api-key" in names),
                ("preflight_transport_header_names", set(headers) <= BROWSER_HEADERS | {"access-control-request-method", "access-control-request-headers"}),
            )):
                return facts
            facts.update(requested_header_names=sorted(names), requested_method=method)
        else:
            name, version = browser_name_version(self.browser_info["user_agent"])
            expected_headers = {
                "api-key": self.query["publishableKey"], "content-type": "application/json",
                "x-client-version": "0.1.0", "x-payment-confirm-source": "sdk", "x-client-platform": "web",
                "x-merchant-domain": urlsplit(DODO_CHECKOUT).hostname,
                "x-browser-name": name, "x-browser-version": version,
                **({"x-client-source": "Payment"} if operation == "confirm" else {}),
            }
            if not all(binding(facts, name, accepted) for name, accepted in (
                ("native_operation_method", request.method == method and request.resource_type == "fetch"),
                ("request_header_names_closed", set(headers) <= app_headers | BROWSER_HEADERS),
                *(("header_" + key.replace("-", "_") + "_bound", headers.get(key) == value) for key, value in expected_headers.items()),
            )):
                return facts
            if operation == "poll" and not binding(facts, "poll_body_absent", request.post_data is None):
                return facts
            if operation == "confirm":
                if native_status is not None:
                    facts["native_status_return_checks"] = self.status_return_checks(native_return, native_status)
                if not self._normal_body(body, native_return, facts, native_status=native_status):
                    return facts
                if not super().return_allowed(self.return_url):
                    self._admitted_status_return = self.return_url
            if operation == "confirm":
                facts["native_return_template_expanded"] = isinstance(native_return, str) and (
                    "{PAYMENT_ID}" in native_return or "%7BPAYMENT_ID%7D" in native_return
                )
        facts["config_bound"] = True
        return facts

    def consume(self, operation):
        if self.closed:
            return False
        if operation == "confirm" and self.armed_at is not None and not self.confirmed and self.clock() - self.armed_at <= 60:
            self.confirmed, self.confirmed_at = True, self.clock()
            return True
        if operation == "poll" and self.confirmed and self.polls < 25 and self.clock() - self.confirmed_at < 300:
            if self.confirm_response_failed:
                if self.fallback_remaining <= 0:
                    return False
                self.fallback_remaining -= 1
            self.polls += 1
            return True
        return False

    def observe_response(self, status, http_status, *, operation="poll", error_code=None):
        # Browser JSON never grants access. Only processing permits another
        # bounded poll; independent Test REST/account checks decide outcome.
        if operation == "confirm" and (http_status != 200 or status is None) and error_code != "IR_16":
            self.confirm_response_failed, self.fallback_remaining = True, 1
        elif http_status == 200 and status == "processing":
            self.confirm_response_failed, self.fallback_remaining = False, 0
        elif http_status != 200 or status != "processing":
            self.closed = True


def approved_redirect(attempt, source, location):
    return attempt.navigation_allowed(urljoin(source, location))
