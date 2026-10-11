"""Public route boundary with invented credentials and independent receipt I/O fixtures."""

import json
from types import SimpleNamespace
from urllib.parse import urlencode, urldefrag, urlsplit
import pytest
import tryfolk.browser_network as network
from tryfolk.browser_policy import DODO_CHECKOUT, DODO_SECURE_FRAME

API = "https://api.custom.hs.dodopayments.com"
KEY = "pk_snd_fixture"
SECRET = "pay_fixture_secret_ABC123"


class Request:
    def __init__(self, frame, url, *, method="GET", kind="fetch", headers=None, body=None, redirect=None):
        self.frame, self.url, self.method = frame, url, method
        self.resource_type, self.post_data, self.redirected_from = kind, body, redirect
        self._headers = headers or {}

    def is_navigation_request(self):
        return self.resource_type == "document"

    async def all_headers(self):
        return self._headers


class Route:
    def __init__(self, request):
        self.request, self.result = request, None

    async def abort(self, reason):
        self.result = "abort"

    async def fulfill(self, **reply):
        self.result = "fulfill"

    async def continue_(self):
        self.result = "continue"


@pytest.fixture
async def bound(monkeypatch):
    raw = (
        DODO_SECURE_FRAME
        + "?"
        + urlencode(
            {
                "publishableKey": KEY,
                "clientSecret": SECRET,
                "apiUrl": API,
            }
        )
        + "#"
        + urlencode({"parentOrigin": DODO_CHECKOUT})
    )
    parent = SimpleNamespace(url=DODO_CHECKOUT + "/fresh-checkout", parent_frame=None)
    page = SimpleNamespace(main_frame=parent)
    parent.page = page

    class Frame:
        generation = 42

        async def evaluate(self, script):
            assert script == "performance.timeOrigin"
            return self.generation

        url = "about:blank"
        parent_frame = parent

        async def frame_element(self):
            class Element:
                async def get_attribute(self, name):
                    return raw

            return Element()

    frame = Frame()
    frame.page = page
    identity = {
        "approved": True,
        "provider_environment": "test_mode",
        "payment_correlated": True,
        "payment_id": "pay_fixture",
        "secret_authenticated": False,
    }
    reads = []

    async def verify(context):
        reads.append(context)
        return dict(identity)

    async def document(url):
        return b"fixture", {"content-type": "text/html"}

    monkeypatch.setattr(network, "public_frame", document)
    guard = network.ResourceGuard(frame_verify=verify)

    async def response_coverage_fixture(frame):
        return True

    guard.response_guard = response_coverage_fixture

    async def preflight_coverage_fixture(url, frame_url):
        return True

    guard.preflight_guard = preflight_coverage_fixture
    guard.policy = network.NetworkPolicy("http://127.0.0.1:3301", "dodo")
    guard.primary_page = page
    admission = Route(Request(frame, urldefrag(raw)[0], kind="document"))
    await guard.route(admission)
    assert admission.result == "fulfill"
    frame.url = raw
    headers = {
        "api-key": KEY,
        "origin": "https://sdk-v2.custom.hs.dodopayments.com",
        "content-type": "application/json",
        "x-client-version": "0.1.0",
        "x-payment-confirm-source": "sdk",
        "x-client-platform": "web",
        "x-browser-name": "Chrome",
        "x-browser-version": "fixture",
        "x-merchant-domain": "test.checkout.dodopayments.com",
    }
    return SimpleNamespace(
        guard=guard,
        frame=frame,
        parent=parent,
        page=page,
        raw=raw,
        headers=headers,
        identity=identity,
        reads=reads,
    )


async def test_only_two_bound_widget_reads_continue_natively_without_static_proxy_or_secret_retention(bound):
    for path in ("/account/payment_methods", "/customers/payment_methods"):
        request = Request(
            bound.frame, API + path + "?" + urlencode({"client_secret": SECRET}), headers=bound.headers
        )
        route = Route(request)
        await bound.guard.route(route)
        assert route.result == "continue"
        event = bound.guard.events[-1]
        assert event["decision"] == "widget_bootstrap_get"
        assert event["query_fields"] == ["client_secret"]
        assert event["config_bound"] and event["independent_identity_rechecked"]
        assert "api-key" in event["header_names"]
    assert len(bound.reads) == 3  # Document plus an independent check for each read.
    retained = json.dumps(bound.guard.events + bound.reads)
    assert KEY not in retained and SECRET not in retained


async def test_native_empty_wallet_startup_uses_bound_test_identity_only_once_without_payment_submission(bound):
    body = json.dumps({"payment_id": "pay_fixture", "client_secret": SECRET,
                       "wallets": [], "delayed_session_token": False})
    first = Route(Request(bound.frame, API + "/payments/session_tokens", method="POST",
                          headers=bound.headers, body=body))
    await bound.guard.route(first)
    assert first.result == "continue"
    assert bound.guard.events[-1]["decision"] == "widget_session_tokens"
    duplicate = Route(Request(bound.frame, API + "/payments/session_tokens", method="POST",
                              headers=bound.headers, body=body))
    await bound.guard.route(duplicate)
    assert duplicate.result == "abort"
    retained = json.dumps(bound.guard.events)
    assert KEY not in retained and SECRET not in retained


@pytest.mark.parametrize("mutation", ["payment", "secret", "wallet", "delay", "unknown", "query", "key", "reset"])
async def test_startup_rejects_wallet_authority_other_payment_and_stale_identity(bound, mutation):
    body = {"payment_id": "pay_fixture", "client_secret": SECRET, "wallets": [], "delayed_session_token": False}
    headers, url = dict(bound.headers), API + "/payments/session_tokens"
    if mutation == "payment":
        body["payment_id"] = "pay_other"
    elif mutation == "secret":
        body["client_secret"] = "pay_other_secret_ABC"
    elif mutation == "wallet":
        body["wallets"] = ["apple_pay"]
    elif mutation == "delay":
        body["delayed_session_token"] = 0
    elif mutation == "unknown":
        body["new_mandate"] = {}
    elif mutation == "query":
        url += "?unknown=fixture"
    elif mutation == "key":
        headers["api-key"] = "pk_prd_fixture"
    else:
        bound.identity["approved"] = False
    route = Route(Request(bound.frame, url, method="POST", headers=headers, body=json.dumps(body)))
    await bound.guard.route(route)
    assert route.result == "abort"
    assert "api.custom.hs.dodopayments.com" in bound.guard.policy.sdk_domains
    with pytest.raises(ValueError):
        bound.guard.policy.check_url(API)


async def test_cors_transport_is_bound_to_same_child_secret_get_and_declared_header_names(bound):
    url = API + "/account/payment_methods?" + urlencode({"client_secret": SECRET})
    headers = {
        "origin": "https://sdk-v2.custom.hs.dodopayments.com",
        "access-control-request-method": "GET",
        "access-control-request-headers": "api-key,content-type,x-client-version,x-merchant-domain",
    }
    route = Route(Request(bound.frame, url, method="OPTIONS", kind="other", headers=headers))
    await bound.guard.route(route)
    assert route.result == "continue"
    event = bound.guard.events[-1]
    assert event["decision"] == "widget_bootstrap_preflight"
    assert event["requested_header_names"] == [
        "api-key",
        "content-type",
        "x-client-version",
        "x-merchant-domain",
    ]
    assert event["requested_method_get"] and event["config_bound"] and event["independent_identity_rechecked"]
    assert len(bound.reads) == 2
    for change in (
        {"headers": headers | {"access-control-request-method": "POST"}},
        {"headers": headers | {"access-control-request-headers": "api-key,authorization"}},
        {"headers": headers | {"access-control-request-headers": "content-type"}},
        {"headers": headers | {"origin": DODO_CHECKOUT}},
        {"headers": headers | {"cookie": "fixture"}},
        {"body": "fixture"},
        {"url": API + "/payments/session_tokens?" + urlencode({"client_secret": SECRET})},
        {"url": API + "/account/payment_methods?client_secret=wrong"},
        {"redirect": object()},
        {"kind": "document"},
    ):
        options = dict(method="OPTIONS", kind="other", headers=headers)
        address = change.get("url", url)
        options.update({key: value for key, value in change.items() if key != "url"})
        denied = Route(Request(bound.frame, address, **options))
        await bound.guard.route(denied)
        assert denied.result == "abort"


@pytest.mark.parametrize(
    "mutation",
    [
        "key",
        "production",
        "secret",
        "extra_query",
        "unknown_header",
        "cookie",
        "authorization",
        "server_key",
        "body",
        "method",
        "type",
        "port",
        "path",
        "redirect",
        "parent",
        "main",
        "stale_frame",
        "reset",
        "provider_unavailable",
        "other_payment",
        "other_checkout",
    ],
)
async def test_bootstrap_rejects_every_unbound_or_stale_request_without_private_data(bound, mutation):
    address = API + "/account/payment_methods?" + urlencode({"client_secret": SECRET})
    options = dict(headers=dict(bound.headers))
    frame = bound.frame
    if mutation in ("key", "production", "server_key"):
        options["headers"]["api-key"] = {
            "key": "pk_snd_other",
            "production": "pk_prd_fixture",
            "server_key": "fixture_server_key",
        }[mutation]
    elif mutation == "secret":
        address = API + "/account/payment_methods?client_secret=pay_other_secret_OTHER"
    elif mutation == "extra_query":
        address += "&extra=fixture"
    elif mutation in ("unknown_header", "cookie", "authorization"):
        options["headers"][
            {"unknown_header": "x-private", "cookie": "cookie", "authorization": "authorization"}[mutation]
        ] = "fixture"
    elif mutation == "body":
        options["body"] = '{"payment_id":"private-fixture-value"}'
    elif mutation == "method":
        options["method"] = "POST"
    elif mutation == "type":
        options["kind"] = "script"
    elif mutation == "port":
        address = address.replace(".com/", ".com:443/")
    elif mutation == "path":
        address = address.replace("/account/payment_methods", "/payments/session_tokens")
    elif mutation == "redirect":
        options["redirect"] = object()
    elif mutation == "parent":
        bound.parent.url = "https://checkout.dodopayments.com/live"
    elif mutation == "main":
        frame = bound.parent
    elif mutation == "stale_frame":
        bound.frame.url += "&stale=fixture"
    elif mutation in ("reset", "provider_unavailable"):
        bound.identity["approved"] = False
    elif mutation == "other_payment":
        bound.identity["payment_id"] = "pay_other"
    elif mutation == "other_checkout":
        bound.identity["checkout_session_id"] = "other"
    route = Route(Request(frame, address, **options))
    await bound.guard.route(route)
    assert route.result == "abort"
    retained = json.dumps(bound.guard.events + bound.reads)
    assert KEY not in retained and SECRET not in retained and "private-fixture-value" not in retained


async def test_response_audit_keeps_status_and_field_names_without_reading_bodies(bound):
    request = Request(
        bound.frame,
        API + "/customers/payment_methods?" + urlencode({"client_secret": SECRET}),
        headers=bound.headers,
    )
    await bound.guard.route(Route(request))

    class Response:
        status = 200

        async def body(self):
            raise AssertionError("Private response body must not be read for evidence")

    response = Response()
    response.request = request
    bound.guard.bootstrap_response(response)
    event = bound.guard.events[-1]
    assert event["decision"] == "widget_bootstrap_response" and event["status"] == 200
    assert event["method"] == "GET" and event["query_fields"] == ["client_secret"]
    assert KEY not in json.dumps(event) and SECRET not in json.dumps(event)
    denied = Request(
        bound.frame,
        API + "/payments/session_tokens",
        method="POST",
        headers=bound.headers,
        body='{"client_secret":"private-fixture-value","component":"card"}',
    )
    await bound.guard.route(Route(denied))
    event = bound.guard.events[-1]
    assert event["body_field_names"] == ["client_secret", "component"]
    assert event["body_absent"] is False
    assert "private-fixture-value" not in json.dumps(event)


async def test_child_cannot_navigate_while_independent_receipt_read_is_pending(bound):
    async def verify(context):
        bound.frame.url += "&changed=fixture"
        return dict(bound.identity)

    bound.guard.frame_verify = verify
    route = Route(
        Request(
            bound.frame,
            API + "/account/payment_methods?" + urlencode({"client_secret": SECRET}),
            headers=bound.headers,
        )
    )
    await bound.guard.route(route)
    assert route.result == "abort"


async def test_authenticated_child_read_cannot_continue_without_active_response_target_guard(bound):
    bound.guard.response_guard = None
    route = Route(
        Request(
            bound.frame,
            API + "/account/payment_methods?" + urlencode({"client_secret": SECRET}),
            headers=bound.headers,
        )
    )
    await bound.guard.route(route)
    assert route.result == "abort"


@pytest.mark.parametrize(
    "mutation", [None, "origin", "method", "headers", "secret", "path", "body", "frame", "redirect", "reset"]
)
async def test_actual_cdp_preflight_layer_checks_private_binding_before_any_transport_continuation(
    bound, mutation
):
    headers = {
        "Origin": "https://sdk-v2.custom.hs.dodopayments.com",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "api-key,content-type,x-client-version,x-merchant-domain",
    }
    event = {
        "requestId": "cdp-fixture",
        "frameId": "frame-fixture",
        "resourceType": "Other",
        "request": {
            "url": API + "/account/payment_methods?" + urlencode({"client_secret": SECRET}),
            "method": "OPTIONS",
            "headers": headers,
        },
    }
    if mutation == "origin":
        headers["Origin"] = "https://other.example"
    if mutation == "method":
        headers["Access-Control-Request-Method"] = "POST"
    if mutation == "headers":
        headers["Access-Control-Request-Headers"] += ",authorization"
    if mutation == "secret":
        event["request"]["url"] = API + "/account/payment_methods?client_secret=wrong"
    if mutation == "path":
        event["request"]["url"] = API + "/payments/session_tokens?client_secret=wrong"
    if mutation == "body":
        event["request"]["postData"] = "fixture"
    if mutation == "frame":
        event["frameId"] = "other-frame"
    if mutation == "redirect":
        event["redirectedRequestId"] = "prior"
    if mutation == "reset":
        bound.identity["approved"] = False

    class Session:
        def __init__(self):
            self.commands = []

        async def send(self, method, args=None):
            self.commands.append((method, args))
            if method == "Page.getFrameTree":
                return {
                    "frameTree": {
                        "frame": {"id": "parent", "url": bound.parent.url},
                        "childFrames": [{"frame": {
                            "id": "frame-fixture", "url": urldefrag(bound.frame.url)[0],
                            "urlFragment": "#" + urlsplit(bound.frame.url).fragment,
                        }}],
                    }
                }
            return {}

    session = Session()
    bound.page.frames = [bound.parent, bound.frame]
    await bound.guard.preflight_request(session, event, target_id="fixture-target", target_type="iframe")
    expected = "Fetch.continueRequest" if mutation is None else "Fetch.failRequest"
    assert session.commands[-1][0] == expected
    audit = bound.guard.events[-1]
    assert audit["decision"] == ("widget_cors_transport_guard" if mutation is None else "deny_cors_transport")
    assert KEY not in json.dumps(audit) and SECRET not in json.dumps(audit)
    if mutation is None:
        assert audit["independent_identity_rechecked"] and audit["requested_method_get"]
        assert audit["vendor_options_response_observed"] is False


async def test_private_get_waits_for_matching_native_preflight_instead_of_trusting_synthetic_204(bound):
    async def absent(url, frame_url):
        return False

    bound.guard.preflight_guard = absent
    route = Route(
        Request(
            bound.frame,
            API + "/account/payment_methods?" + urlencode({"client_secret": SECRET}),
            headers=bound.headers,
        )
    )
    await bound.guard.route(route)
    assert route.result == "abort"


@pytest.mark.parametrize("mutation", [None, "fragment", "parent", "stale"])
async def test_cdp_frame_tree_fragment_is_separate_while_private_child_context_stays_exact(bound, mutation):
    headers = {
        "Origin": "https://sdk-v2.custom.hs.dodopayments.com",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "api-key,content-type",
    }
    event = {
        "requestId": "fixture",
        "frameId": "child",
        "resourceType": "Other",
        "request": {
            "url": API + "/account/payment_methods?" + urlencode({"client_secret": SECRET}),
            "method": "OPTIONS",
            "headers": headers,
        },
    }

    class Session:
        async def send(self, method, args=None):
            return {
                "frameTree": {
                    "frame": {
                        "id": "child",
                        "url": urldefrag(bound.raw)[0],
                        "urlFragment": "#wrong" if mutation == "fragment" else "#" + urlsplit(bound.raw).fragment,
                    }
                }
            }

    bound.page.frames = [bound.parent, bound.frame]
    if mutation == "parent":
        bound.frame.parent_frame = SimpleNamespace(url=bound.parent.url)
    elif mutation == "stale":
        bound.frame.url += "&stale=fixture"
    approved, facts = await bound.guard.preflight_request(Session(), event, observe_only=True)
    assert approved is (mutation is None)
    if approved:
        assert facts["config_bound"] and facts["independent_identity_rechecked"]
        assert facts["frame_id_matched"] and facts["reconstructed_url_matched"]


@pytest.mark.parametrize("preflight_first", [False, True])
async def test_native_preflight_and_matching_get_can_arrive_in_either_protocol_event_order(bound, preflight_first):
    import asyncio
    from tryfolk.native_preflight import NativePreflightGate

    gate = NativePreflightGate(bound.guard, API)
    bound.page.frames = [bound.parent, bound.frame]
    address = API + "/customers/payment_methods?" + urlencode({"client_secret": SECRET})

    class Session:
        async def send(self, method, args=None):
            return {"frameTree": {"frame": {
                "id": "child", "url": urldefrag(bound.raw)[0],
                "urlFragment": "#" + urlsplit(bound.raw).fragment,
            }}}

    get = {"requestId": "original", "frameId": "child", "request": {"url": address, "method": "GET"}}
    options = {"requestId": "preflight", "initiator": {"requestId": "original"}, "request": {
        "url": address, "method": "OPTIONS", "headers": {
            "Origin": "https://sdk-v2.custom.hs.dodopayments.com",
            "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "api-key,content-type",
        },
    }}
    if preflight_first:
        task = asyncio.create_task(gate.observe(Session(), options))
        await asyncio.sleep(0)
        await gate.observe(Session(), get)
        await task
    else:
        await gate.observe(Session(), get)
        await gate.observe(Session(), options)
    assert await gate.wait(address, bound.raw)
    assert bound.guard.events[-1]["decision"] == "widget_cors_transport_guard"
    assert bound.guard.events[-1]["reconstructed_url_matched"]


async def test_capture_diagnostic_delegates_exact_startup_and_denies_current_and_wrong_payment_paths(bound):
    from tryfolk.native_capture import CaptureOnlyGuard

    inspected = []

    async def inspect(request):
        inspected.append(request.url)
        return {'request_admitted': False, 'evaluated': True}

    capture = CaptureOnlyGuard(bound.guard.route, bound.guard.redirect_response, inspect,
                               owned_origin=bound.guard.policy.origin, payment_id='pay_fixture',
                               checkout_id='cks_fixture', parent_url=bound.parent.url)
    body = json.dumps({'payment_id': 'pay_fixture', 'client_secret': SECRET,
                       'wallets': [], 'delayed_session_token': False})
    startup = Route(Request(bound.frame, API + '/payments/session_tokens', method='POST',
                            headers=bound.headers, body=body))
    await capture.route(startup)
    assert startup.result == 'continue'
    assert bound.guard.events[-1]['decision'] == 'widget_session_tokens'
    assert capture.events == [] and inspected == []
    capture.begin_native_pay_capture()
    current = Route(Request(bound.frame, API + '/payments/pay_fixture/confirm', method='POST',
                            headers=bound.headers, body='{}'))
    await capture.route(current)
    assert current.result == 'abort' and len(inspected) == 1
    poll = Route(Request(bound.frame, API + '/payments/pay_fixture', headers=bound.headers))
    await capture.route(poll)
    assert poll.result == 'abort' and capture.events[-1]['operation'] == 'poll'
    for path, method in (('/payments/pay_other/confirm', 'POST'), ('/payments/pay_other', 'GET')):
        wrong = Route(Request(bound.frame, API + path, method=method, headers=bound.headers))
        await capture.route(wrong)
        assert wrong.result == 'abort'
        assert bound.guard.events[-1]['decision'] == 'deny_widget_bootstrap'
    duplicate = Route(Request(bound.frame, API + '/payments/session_tokens', method='POST',
                              headers=bound.headers, body=body))
    await capture.route(duplicate)
    assert duplicate.result == 'abort' and bound.guard.events[-1]['request_limit_exhausted']
    assert len(capture.events) == 2 and bound.guard.card_attempt is None
    assert not bound.guard.card_enabled
    assert KEY not in json.dumps(capture.events) and SECRET not in json.dumps(capture.events)


@pytest.mark.parametrize('status,payment_type,normal', [(200, 'normal', True), (200, 'wallet', False), (500, 'normal', False)])
async def test_native_bootstrap_readiness_is_observed_without_card_authority(bound, status, payment_type, normal):
    import asyncio

    request = Request(bound.frame, API + '/account/payment_methods?' + urlencode({'client_secret': SECRET}),
                      headers=bound.headers)
    route = Route(request)
    await bound.guard.route(route)
    assert route.result == 'continue'

    async def body():
        return json.dumps({'payment_type': payment_type}).encode()

    bound.guard.bootstrap_response(SimpleNamespace(request=request, status=status, body=body))
    await asyncio.sleep(0)
    events = [e for e in bound.guard.events if e['decision'] == 'native_normal_type_check']
    assert len(events) == 1 and events[0]['normal_payment_type'] is normal
    facts = bound.guard.native_setup_facts(bound.guard.frame_context(bound.raw, bound.parent.url)['config_id'], bound.frame.generation)
    assert facts['bootstrap_account_methods_200'] == (status == 200)
    assert facts['normal_type_observed'] is normal
    assert facts['card_capability_disabled'] and facts['card_attempt_absent']
    assert not any(e['decision'] in ('native_pay_armed', 'widget_confirm', 'widget_poll') for e in bound.guard.events)


@pytest.mark.parametrize('mutation', ['configuration', 'generation', 'parent', 'identity', 'during_body'])
async def test_passive_normal_readiness_rejects_wrong_or_stale_child_even_with_normal_fixture_body(bound, mutation):
    import asyncio

    request = Request(bound.frame, API + '/account/payment_methods?' + urlencode({'client_secret': SECRET}), headers=bound.headers)
    route = Route(request)
    await bound.guard.route(route)
    assert route.result == 'continue'
    context = bound.guard.frame_context(bound.raw, bound.parent.url)
    if mutation == 'configuration':
        bound.frame.url = bound.raw.replace(SECRET, 'pay_other_secret_fixture')
    elif mutation == 'generation':
        bound.frame.generation += 1
    elif mutation == 'parent':
        bound.parent.url += '/other'
    elif mutation == 'identity':
        bound.identity['approved'] = False

    async def body():
        if mutation == 'during_body':
            bound.frame.generation += 1
        return b'{"payment_type":"normal"}'

    bound.guard.bootstrap_response(SimpleNamespace(request=request, status=200, body=body))
    await asyncio.sleep(0)
    facts = bound.guard.native_setup_facts(context['config_id'], bound.frame.generation)
    assert not facts['normal_type_observed'] and not facts['bootstrap_account_methods_200']
    assert facts['card_capability_disabled'] and facts['card_attempt_absent']
    assert bound.guard.events[-1]['decision'] == 'native_normal_type_check'
    assert bound.guard.events[-1]['normal_payment_type'] is False
