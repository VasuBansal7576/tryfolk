"""Boundary fixtures; these responses are never live provider evidence."""

import pytest
from tryfolk.browser_network import NetworkPolicy, PUBLIC_SCRIPTS
from tryfolk.browser_policy import DODO_CHECKOUT
import httpx
import tryfolk.browser_network as network
from urllib.parse import urlencode, urldefrag


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
