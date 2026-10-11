"""Boundary fixtures; these responses are never live provider evidence."""

import pytest
from tryfolk.browser_network import NetworkPolicy, PUBLIC_SCRIPTS
from tryfolk.browser_policy import DODO_CHECKOUT
import httpx
import tryfolk.browser_network as network
import json
from types import SimpleNamespace
from urllib.parse import urlencode, urldefrag
from tryfolk.browser_settlement import DOCUMENT_STATE
from tryfolk.checkout_navigation import BILLING_SOURCE_SHA256


def owned_response_fixture(body):
    page = SimpleNamespace()
    page.main_frame = SimpleNamespace(page=page, url="http://127.0.0.1:3300")
    request = SimpleNamespace(
        frame=page.main_frame, method="POST", redirected_from=None,
        resource_type="fetch", url=page.main_frame.url + "/api/target/checkouts",
        is_navigation_request=lambda: False,
    )

    async def read_body():
        return json.dumps(body).encode()

    response = SimpleNamespace(request=request, status=201, url=request.url, body=read_body)
    guard = network.ResourceGuard()
    guard.policy = NetworkPolicy(page.main_frame.url, "dodo")
    guard.primary_page = page
    return guard, response


def owned_checkout_fixture():
    return {
        "source": "dodo_test_api", "order_id": "order_fixture",
        "checkout_session_id": "cks_fixture",
        "checkout_url": DODO_CHECKOUT + "/session/private_fixture",
        "ownership": dict(run_id="run_fixture", persona_id="persona_fixture",
                          account_id="account_fixture", fixture_id="fixture_fixture",
                          target_revision="revision_fixture"),
    }


@pytest.mark.parametrize("body", [[], None, "unexpected", {"source": "dodo_test_api"}])
async def test_malformed_owned_checkout_response_cannot_supply_parent_authority(body):
    guard, response = owned_response_fixture(body)
    assert not await guard.observe_owned_checkout_response(response)
    assert not guard.owned_checkout({"checkout_session_id": "cks_fixture"})


async def test_owned_checkout_response_requires_matching_independent_order_customer_and_ownership():
    body = owned_checkout_fixture()
    guard, response = owned_response_fixture(body)
    assert await guard.observe_owned_checkout_response(response)
    identity = {
        "approved": True, "provider_environment": "test_mode",
        "payment_correlated": True, "customer_correlated": True,
        "checkout_session_id": body["checkout_session_id"],
        "order_id": body["order_id"], "ownership": body["ownership"],
    }
    assert guard.owned_checkout(identity)["url"] == body["checkout_url"]
    for change in (
        {"approved": False}, {"provider_environment": "live_mode"},
        {"payment_correlated": False}, {"customer_correlated": False},
        {"checkout_session_id": "cks_other"}, {"order_id": "order_other"},
        {"ownership": body["ownership"] | {"account_id": "account_other"}},
        {"ownership": body["ownership"] | {"target_revision": "revision_other"}},
    ):
        assert not guard.owned_checkout(identity | change)
    assert not await guard.observe_owned_checkout_response(response)
    assert body["checkout_url"] not in json.dumps(guard.events)


@pytest.mark.parametrize("change", ["foreign_frame", "foreign_url", "redirect", "wrong_status", "wrong_method"])
async def test_foreign_checkout_response_cannot_supply_parent_authority(change):
    guard, response = owned_response_fixture(owned_checkout_fixture())
    if change == "foreign_frame":
        response.request.frame = object()
    elif change == "foreign_url":
        response.url = DODO_CHECKOUT + "/api/target/checkouts"
    elif change == "redirect":
        response.request.redirected_from = object()
    elif change == "wrong_status":
        response.status = 200
    elif change == "wrong_method":
        response.request.method = "GET"
    assert not await guard.observe_owned_checkout_response(response)


@pytest.mark.parametrize("change", [None, "cdp_xhr", "child_frame", "foreign_target", "redirected", "missing_browser_request", "browser_xhr", "cdp_document"])
async def test_owned_checkout_body_is_captured_before_response_continues(change):
    guard, response = owned_response_fixture(owned_checkout_fixture())
    guard.primary_response_target = "target_fixture"
    event = {
        "requestId": "request_fixture", "frameId": "frame_fixture", "resourceType": "Fetch",
        "request": {"url": response.url, "method": "POST"}, "responseStatusCode": 201,
    }
    target = dict(target_id="target_fixture", target_type="page")
    if change == "browser_xhr":
        response.request.resource_type = "xhr"
    if change != "missing_browser_request":
        guard.bind_owned_checkout_request(response.request)
    if change == "cdp_xhr":
        event["resourceType"] = "XHR"
    elif change == "cdp_document":
        event["resourceType"] = "Document"
    if change == "child_frame":
        event["frameId"] = "child_fixture"
    elif change == "foreign_target":
        target["target_id"] = "target_other"
    elif change == "redirected":
        event["redirectedRequestId"] = "prior_fixture"

    class Session:
        calls = []

        async def send(self, name, options=None):
            self.calls.append(name)
            if name == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "frame_fixture"}}}
            if name == "Fetch.getResponseBody":
                return {"body": json.dumps(owned_checkout_fixture()), "base64Encoded": False}
            if name == "Fetch.continueResponse" and change in (None, "cdp_xhr"):
                assert any(e["decision"] == "owned_checkout_response_captured" for e in guard.events)
            return {}

    session = Session()
    await guard.redirect_response(session, event, **target)
    assert session.calls[-1] == "Fetch.continueResponse"
    assert ("Fetch.getResponseBody" in session.calls) is (change in (None, "cdp_xhr"))


@pytest.mark.parametrize("change", [None, "foreign_target", "child_frame", "redirect", "wrong_status", "wrong_body", "wrong_method", "wrong_session", "foreign_request_frame", "stale_document", "unknown_source"])
async def test_session_billing_response_captures_only_existing_owned_current_primary_fetch(change):
    entry = DODO_CHECKOUT + "/session/cks_fixture?theme=light"
    link = DODO_CHECKOUT + "/Private1"
    creation = owned_checkout_fixture() | {"checkout_url": entry, "navigation_customization": {"hide_order_details": False, "hide_on_demand_tag": False}}
    guard, response = owned_response_fixture(creation)
    assert await guard.observe_owned_checkout_response(response)
    page = guard.primary_page
    page.main_frame.url = entry
    document = {"url": entry, "generation": 1000, "body_present": True, "ready_state": "complete"}

    async def evaluate(script):
        assert script == DOCUMENT_STATE
        return dict(document)
    page.evaluate = evaluate
    guard.primary_response_target = "target_fixture"
    guard.checkout_navigation.observe_source("0" * 64 if change == "unknown_source" else BILLING_SOURCE_SHA256, document)
    guard.expect_billing_continue(document)
    request = SimpleNamespace(frame=page.main_frame, method="POST", resource_type="fetch", redirected_from=None,
                              url=DODO_CHECKOUT + "/api/checkout/sessions/cks_fixture/confirm",
                              post_data='{"mode":"test","data":{}}', is_navigation_request=lambda: False)
    if change == "wrong_session":
        request.url = request.url.replace("cks_fixture", "cks_other")
    elif change == "foreign_request_frame":
        request.frame = object()
    bound = await guard.bind_billing_submission(request)
    assert bound is (change not in ("wrong_session", "foreign_request_frame"))
    event = {"requestId": "billing_fixture", "frameId": "main_fixture", "resourceType": "XHR",
             "request": {"url": request.url, "method": "POST", "postData": request.post_data},
             "responseStatusCode": 200, "responseHeaders": [{"name": "Content-Type", "value": "application/json"}]}
    target = {"target_id": "target_other" if change == "foreign_target" else "target_fixture", "target_type": "page"}
    if change == "child_frame":
        event["frameId"] = "other_frame"
    elif change == "redirect":
        event["redirectedRequestId"] = "prior_fixture"
    elif change == "wrong_status":
        event["responseStatusCode"] = 201
    elif change == "wrong_body":
        event["request"]["postData"] = '{"mode":"live","data":{}}'
    elif change == "wrong_method":
        event["request"]["method"] = "GET"
    elif change == "stale_document":
        document["generation"] = 2000

    class Session:
        calls = []
        async def send(self, method, params=None):
            self.calls.append(method)
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "main_fixture", "url": entry}}}
            if method == "Fetch.getResponseBody":
                return {"body": json.dumps({"payment_link": link}), "base64Encoded": False}
            if method == "Fetch.continueResponse" and change is None:
                assert any(e.get("decision") == "owned_billing_response_captured" for e in guard.events)
            return {}
    session = Session()
    await guard.redirect_response(session, event, **target)
    assert session.calls[-1] == "Fetch.continueResponse"
    parent = {"url": link + "?theme=light&from=session", "generation": 1000}
    assert all(guard.checkout_navigation.parent_checks(link, parent).values()) is (change is None)
    assert entry not in json.dumps(guard.events) and link not in json.dumps(guard.events)


def test_three_reviewed_modules_load_only_as_scripts_inside_the_direct_secure_frame():
    frame = "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html"
    policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    urls = [
        frame.replace("checkout.html", "assets/" + name)
        for name in ("checkout-BWo9IWjf.js", "app-DoOO9T9n.js", "app-payment-methods-BbWaNrlq.js")
    ]
    for url in urls:
        options = {
            "url": url,
            "method": "GET",
            "resource_type": "script",
            "document_url": frame,
            "navigation": False,
            "primary_parent_url": DODO_CHECKOUT + "/payment",
        }
        assert policy.decide(**options) == "public_frame_script"
        for change in (
            {"url": url + "?key=fixture"},
            {"url": url + "#fragment"},
            {"url": url.replace(".com/", ".com:8443/")},
            {"url": url.replace("https:", "http:")},
            {"method": "POST"},
            {"resource_type": "fetch"},
            {"resource_type": "document"},
            {"resource_type": "other"},
            {"document_url": DODO_CHECKOUT},
            {"primary_parent_url": None},
            {"credentials": True},
            {"redirected": True},
            {"body": "fixture"},
            {"navigation": True},
        ):
            assert policy.decide(**(options | change)) == "deny"
    assert (
        policy.decide(
            frame.replace("checkout.html", "assets/unknown.js"),
            "GET",
            "script",
            frame,
            False,
            primary_parent_url=DODO_CHECKOUT,
        )
        == "deny"
    )


def test_exact_secure_frame_document_and_prefetch_do_not_grant_navigation_or_other_resources():
    frame = "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html"
    policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    assert (
        policy.decide(
            frame, "GET", "document", "about:blank", True, primary_parent_url=DODO_CHECKOUT + "/payment"
        )
        == "public_frame"
    )
    assert policy.decide(frame, "GET", "other", DODO_CHECKOUT + "/payment", False) == "public_frame"
    for changes in (
        {"primary_parent_url": None},
        {"primary_parent_url": policy.origin},
        {"url": frame + "?session=fixture"},
        {"url": frame + "#fixture"},
        {"url": frame + "/extra"},
        {"method": "POST"},
        {"redirected": True},
        {"body": "fixture"},
        {"credentials": True},
        {"url": "https://js.stripe.com/v3/controller.html"},
    ):
        options = {
            "url": frame,
            "method": "GET",
            "resource_type": "document",
            "document_url": "about:blank",
            "navigation": True,
            "primary_parent_url": DODO_CHECKOUT + "/payment",
        }
        assert policy.decide(**(options | changes)) == "deny"
    assert policy.decide(frame, "GET", "other", policy.origin, False) == "deny"
    assert policy.decide(DODO_CHECKOUT + "/api/payment", "POST", "fetch", frame, False) == "deny"
    for url in (frame, frame.replace("checkout.html", "assets/unapproved.js")):
        with pytest.raises(ValueError):
            policy.check_url(url)


def test_public_scripts_have_exact_method_path_type_and_document_boundaries():
    policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    for url in PUBLIC_SCRIPTS:
        assert policy.decide(url, "GET", "script", DODO_CHECKOUT + "/payment", False) == "public_script"
        for value in (
            url + "?token=synthetic",
            url + "#fragment",
            url + "/other",
            url.replace("https:", "http:"),
            url.replace(".com/", ".com:8443/"),
        ):
            assert policy.decide(value, "GET", "script", DODO_CHECKOUT, False) == "deny"
        for method, kind, document, navigation, redirected, body in (
            ("POST", "script", DODO_CHECKOUT, False, False, None),
            ("GET", "fetch", DODO_CHECKOUT, False, False, None),
            ("GET", "document", DODO_CHECKOUT, True, False, None),
            ("GET", "script", "http://127.0.0.1:3301", False, False, None),
            ("GET", "script", DODO_CHECKOUT, False, True, None),
            ("GET", "script", DODO_CHECKOUT, False, False, "synthetic-secret"),
        ):
            assert policy.decide(url, method, kind, document, navigation, redirected, body) == "deny"


def test_resource_permission_does_not_authorize_navigation_other_origins_or_fixture():
    policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    assert policy.decide(DODO_CHECKOUT + "/test", "GET", "document", policy.origin, True) == "continue"
    assert (
        policy.decide(policy.origin + "/api/target/test-login", "POST", "fetch", policy.origin, False)
        == "continue"
    )
    for url in (
        "http://127.0.0.1:3300",
        "http://192.168.1.1",
        "https://checkout.dodopayments.com",
        "https://test.checkout.dodopayments.com:8443",
        "https://sentry.dodopayments.tech/api/15/envelope/",
        "https://u:p@test.checkout.dodopayments.com",
    ):
        assert policy.decide(url, "GET", "script", DODO_CHECKOUT, False) == "deny"
    fixture = NetworkPolicy("http://127.0.0.1:3101", "local-fixture")
    for url in PUBLIC_SCRIPTS:
        assert fixture.decide(url, "GET", "script", DODO_CHECKOUT, False) == "deny"
        with pytest.raises(ValueError):
            policy.check_url(url)


def test_redirect_response_cannot_expand_the_request_or_resource_grant():
    policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    assert policy.redirect_allowed(policy.origin + "/first", "/next")
    assert policy.redirect_allowed(DODO_CHECKOUT + "/first", DODO_CHECKOUT + "/next")
    for location in (
        *PUBLIC_SCRIPTS,
        "http://127.0.0.1:3300/leak",
        "//192.168.1.1/leak",
        "javascript:alert(1)",
    ):
        assert not policy.redirect_allowed(policy.origin + "/first", location)
    for url in PUBLIC_SCRIPTS:
        assert not policy.redirect_allowed(url, url)


@pytest.mark.parametrize(
    "status,content_type", [(200, "text/javascript"), (302, "text/javascript"), (200, "text/html")]
)
async def test_static_fetch_uses_no_browser_credentials_proxies_or_redirects(
    monkeypatch, status, content_type
):
    requests = []
    client = httpx.AsyncClient

    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("8.8.8.8", 443))]  # External DNS fixture only.

    def response(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={
                "content-type": content_type,
                **({"location": "http://127.0.0.1/private"} if status == 302 else {}),
            },
            content=b"fixture()",
        )

    def external_client(**options):
        assert options == {"trust_env": False, "follow_redirects": False, "timeout": 10}
        return client(**options, transport=httpx.MockTransport(response))

    monkeypatch.setattr(network.asyncio.get_running_loop(), "getaddrinfo", resolve)
    monkeypatch.setattr(network.httpx, "AsyncClient", external_client)
    if status == 200 and content_type == "text/javascript":
        assert (await network.public_script(PUBLIC_SCRIPTS[0]))[0] == b"fixture()"
    else:
        with pytest.raises(ValueError, match="unredirected"):
            await network.public_script(PUBLIC_SCRIPTS[0])
    assert len(requests) == 1
    assert requests[0].method == "GET" and str(requests[0].url) == PUBLIC_SCRIPTS[0]
    assert not {"cookie", "authorization", "referer", "proxy-authorization"} & set(requests[0].headers)


async def test_private_dns_is_rejected_before_any_asset_request(monkeypatch):
    async def resolve(*args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(network.asyncio.get_running_loop(), "getaddrinfo", resolve)
    with pytest.raises(ValueError, match="private"):
        await network.public_script(PUBLIC_SCRIPTS[0])


async def test_new_document_with_no_playwright_frame_is_aborted_and_audited():
    class RequestFixture:
        url = PUBLIC_SCRIPTS[0]
        method = "GET"
        resource_type = "document"

        @property
        def frame(self):
            raise RuntimeError("New navigation request has no frame yet")

    class RouteFixture:
        request = RequestFixture()
        aborted = False

        async def abort(self, reason):
            assert reason == "blockedbyclient"
            self.aborted = True

    guard = network.ResourceGuard()
    guard.policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    guard.primary_page = object()
    route = RouteFixture()
    await guard.route(route)
    assert route.aborted
    assert guard.events[-1]["decision"] == "deny"
    assert guard.events[-1]["frame_unavailable"] is True


@pytest.mark.parametrize("approved", [True, False])
async def test_generated_secure_document_needs_independent_identity_and_keeps_url_private(
    monkeypatch, approved
):
    bare = "https://sdk-v2.custom.hs.dodopayments.com/1.1.0/checkout.html"
    raw = (
        bare
        + "?"
        + urlencode(
            {
                "publishableKey": "pk_snd_fixture",
                "clientSecret": "pay_fixture_secret_ABC123",
                "apiUrl": "https://api.custom.hs.dodopayments.com",
            }
        )
        + "#"
        + urlencode({"parentOrigin": DODO_CHECKOUT})
    )

    class Element:
        async def get_attribute(self, name):
            assert name == "src"
            return raw

    class Page:
        main_frame = type("Main", (), {"url": DODO_CHECKOUT + "/payment"})()

    page = Page()

    class Frame:
        url = "about:blank"
        parent_frame = page.main_frame

        async def frame_element(self):
            return Element()

    frame = Frame()
    frame.page = page

    class Request:
        url = urldefrag(raw)[0]
        method = "GET"
        resource_type = "document"
        redirected_from = post_data = None

        def is_navigation_request(self):
            return True

        async def all_headers(self):
            return {}

    request = Request()
    request.frame = frame

    class Route:
        aborted = False
        fulfilled = None

        async def abort(self, reason):
            self.aborted = True

        async def fulfill(self, **reply):
            self.fulfilled = reply

    route = Route()
    route.request = request
    reads, fetches = [], []

    async def verify(data):
        reads.append(data)
        return {
            "approved": approved,
            "payment_id": "pay_fixture",
            "provider_environment": "test_mode",
            "payment_correlated": approved,
        }

    async def public_fetch(url):
        fetches.append(url)
        return b"fixture document", {"content-type": "text/html"}

    monkeypatch.setattr(network, "public_frame", public_fetch)
    guard = network.ResourceGuard(frame_verify=verify)
    guard.primary_page = page
    guard.policy = NetworkPolicy("http://127.0.0.1:3301", "dodo")
    await guard.route(route)
    assert reads[0]["payment_id"] == "pay_fixture"
    import json

    assert "pk_snd_fixture" not in json.dumps(reads + guard.events)
    assert "ABC123" not in json.dumps(reads + guard.events)
    if approved:
        assert fetches == [bare] and route.fulfilled["body"] == b"fixture document" and not route.aborted
        assert guard.frame_context(raw, DODO_CHECKOUT + "/payment")["payment_correlated"]
        assert guard.frame_context(raw + "extra", DODO_CHECKOUT + "/payment") is None
    else:
        assert not fetches and route.aborted and not route.fulfilled
        assert guard.frame_context(raw, DODO_CHECKOUT + "/payment") is None
