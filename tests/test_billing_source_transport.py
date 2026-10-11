"""External CDP/HTML source fixtures, never genuine provider payment evidence."""

import base64
import json

import pytest
from types import SimpleNamespace

import tryfolk.checkout_navigation as navigation
from tryfolk.browser_policy import DODO_CHECKOUT
from tryfolk.browser_settlement import DOCUMENT_STATE
from test_browser_network import owned_checkout_fixture, owned_response_fixture

# The retained073 public HTTP-cache key has this exact deployment query. It is
# metadata for a known public source, not an invented actual5 URL observation.
PUBLIC_SOURCE_URL = (
    DODO_CHECKOUT + "/_next/static/chunks/1257-6a47aeb14b038869.js?dpl=dpl_6F9AsGp5qT76drjp5w4FM2svRKyd"
)
FIXTURE_SOURCE = b"/* disclosed reviewed public source I/O fixture */"
FIXTURE_SOURCE_SHA256 = "66f0cfdc0572ad5e20d1bffcc713256c10911449ef3a701f0c11fb9dbfcc4ce9"


@pytest.mark.parametrize(
    "source_url",
    [
        PUBLIC_SOURCE_URL,
        DODO_CHECKOUT
        + "/_next/static/chunks/app/(split%20layout)/session/%5Bsession_id%5D/page-786762b1c6529cfe.js?dpl=dpl_6F9AsGp5qT76drjp5w4FM2svRKyd",
    ],
)
async def test_current_deployment_script_response_establishes_distinct_owned_payment_parent(
    monkeypatch, source_url
):
    # Substitute only the external source approval fingerprint and CDP I/O.
    # The genuine retained-byte SDK fixture separately keeps the real pin.
    monkeypatch.setattr(navigation, "BILLING_SOURCE_SHA256", FIXTURE_SOURCE_SHA256)
    entry = DODO_CHECKOUT + "/session/cks_fixture?theme=light"
    link = DODO_CHECKOUT + "/Private1"
    creation = owned_checkout_fixture() | {
        "checkout_url": entry,
        "navigation_customization": {"hide_order_details": False, "hide_on_demand_tag": False},
    }
    guard, response = owned_response_fixture(creation)
    assert await guard.observe_owned_checkout_response(response)
    page = guard.primary_page
    page.main_frame.url = entry
    document = {"url": entry, "generation": 1000, "body_present": True, "ready_state": "complete"}

    async def evaluate(script):
        assert script == DOCUMENT_STATE
        return dict(document)

    page.evaluate = evaluate
    guard.primary_response_target = "primary_fixture"
    event = {
        "requestId": "source_fixture",
        "frameId": "main_fixture",
        "resourceType": "Script",
        "request": {"url": source_url, "method": "GET"},
        "responseStatusCode": 200,
        "responseHeaders": [{"name": "content-type", "value": "application/javascript"}],
    }
    continued = []

    class Session:
        async def send(self, method, params=None):
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "main_fixture", "url": entry}}}
            if method == "Fetch.getResponseBody":
                body = (
                    FIXTURE_SOURCE.decode()
                    if params["requestId"] == "source_fixture"
                    else json.dumps({"payment_link": link})
                )
                return {"body": body, "base64Encoded": False}
            if method == "Fetch.continueResponse":
                continued.append(params["requestId"])
            return {}

    session = Session()
    target = {"target_id": "primary_fixture", "target_type": "page"}
    await guard.redirect_response(session, event, **target)
    guard.expect_billing_continue(document)
    request = SimpleNamespace(
        frame=page.main_frame,
        method="POST",
        resource_type="fetch",
        redirected_from=None,
        url=DODO_CHECKOUT + "/api/checkout/sessions/cks_fixture/confirm",
        post_data='{"mode":"test","data":{}}',
        is_navigation_request=lambda: False,
    )
    assert await guard.bind_billing_submission(request)
    billing = {
        "requestId": "billing_fixture",
        "frameId": "main_fixture",
        "resourceType": "XHR",
        "request": {"url": request.url, "method": "POST", "postData": request.post_data},
        "responseStatusCode": 200,
        "responseHeaders": [{"name": "content-type", "value": "application/json"}],
    }
    await guard.redirect_response(session, billing, **target)
    parent = {**document, "url": link + "?theme=light&from=session"}
    assert all(guard.checkout_navigation.parent_checks(link, parent).values())
    assert continued == ["source_fixture", "billing_fixture"]
    assert guard.checkout_navigation.entry_url == entry != parent["url"]


async def test_wrong_source_bytes_report_the_first_capture_exclusion_without_routing_values(monkeypatch):
    monkeypatch.setattr(navigation, "BILLING_SOURCE_SHA256", FIXTURE_SOURCE_SHA256)
    entry = DODO_CHECKOUT + "/session/cks_fixture"
    guard, response = owned_response_fixture(owned_checkout_fixture() | {"checkout_url": entry})
    assert await guard.observe_owned_checkout_response(response)
    guard.primary_response_target = "primary_fixture"

    async def evaluate(_):
        return {"url": entry, "generation": 1000}

    guard.primary_page.evaluate = evaluate

    class Session:
        async def send(self, method, params=None):
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "main_fixture", "url": entry}}}
            if method == "Fetch.getResponseBody":
                return {"body": "unreviewed changed source", "base64Encoded": False}
            return {}

    await guard.redirect_response(
        Session(),
        {
            "requestId": "source_fixture",
            "frameId": "main_fixture",
            "resourceType": "Script",
            "request": {"url": PUBLIC_SOURCE_URL, "method": "GET"},
            "responseStatusCode": 200,
            "responseHeaders": [{"name": "content-type", "value": "application/javascript"}],
        },
        target_id="primary_fixture",
        target_type="page",
    )
    exclusion = next(e for e in guard.events if e.get("decision") == "billing_source_unverified")
    assert exclusion["first_capture_subpredicate"] == "reviewed_source_hash_bound"
    assert not guard.checkout_navigation.parent_checks(
        DODO_CHECKOUT + "/Private1", {"url": DODO_CHECKOUT + "/Private1?from=session", "generation": 1000}
    )["owned_checkout_parent_exact"]
    assert entry not in json.dumps(guard.events) and PUBLIC_SOURCE_URL not in json.dumps(guard.events)


@pytest.mark.parametrize(
    "change,predicate",
    [
        ("duplicate_dpl", "source_deployment_query_bound"),
        ("unrelated_query", "source_deployment_query_bound"),
        ("different_query", "source_deployment_query_bound"),
        ("empty_dpl", "source_deployment_query_bound"),
        ("encoded_dpl", "source_deployment_query_bound"),
        ("fragment", "source_fragment_absent"),
        ("empty_fragment", "source_fragment_absent"),
        ("private_path", "source_public_static_path"),
        ("wrong_method", "resource_method_bound"),
        ("wrong_resource", "resource_method_bound"),
        ("redirect", "no_redirect"),
        ("wrong_status", "response_200"),
        ("wrong_content_type", "response_content_type_bound"),
        ("foreign_target", "primary_page_target"),
        ("child_frame", "primary_main_frame"),
        ("other_entry", "original_owned_entry_current"),
        ("changed_document", "same_document_during_capture"),
        ("missing_generation", "source_generation_available"),
        ("invalid_base64", "response_body_decoded"),
        ("oversized_body", "response_body_bounded"),
    ],
)
async def test_source_capture_exclusions_never_grant_a_distinct_parent(monkeypatch, change, predicate):
    monkeypatch.setattr(navigation, "BILLING_SOURCE_SHA256", FIXTURE_SOURCE_SHA256)
    entry = DODO_CHECKOUT + "/session/cks_fixture"
    guard, response = owned_response_fixture(owned_checkout_fixture() | {"checkout_url": entry})
    assert await guard.observe_owned_checkout_response(response)
    guard.primary_response_target = "primary_fixture"
    reads = 0

    async def evaluate(_):
        nonlocal reads
        reads += 1
        generation = (
            None
            if change == "missing_generation"
            else 2000
            if change == "changed_document" and reads == 2
            else 1000
        )
        return {"url": entry, "generation": generation}

    guard.primary_page.evaluate = evaluate
    url = PUBLIC_SOURCE_URL
    if change == "duplicate_dpl":
        url += "&dpl=dpl_other"
    elif change == "unrelated_query":
        url += "&extra=1"
    elif change == "different_query":
        url = url.split("?")[0] + "?key=fixture"
    elif change == "empty_dpl":
        url = url.split("?")[0] + "?dpl="
    elif change == "encoded_dpl":
        url = url.split("?")[0] + "?dpl=dpl_%41"
    elif change == "fragment":
        url += "#fixture"
    elif change == "empty_fragment":
        url += "#"
    elif change == "private_path":
        url = url.replace("/_next/static/", "/api/private/")
    event = {
        "requestId": "source_fixture",
        "frameId": "child_fixture" if change == "child_frame" else "main_fixture",
        "resourceType": "XHR" if change == "wrong_resource" else "Script",
        "request": {"url": url, "method": "POST" if change == "wrong_method" else "GET"},
        "responseStatusCode": 201 if change == "wrong_status" else 200,
        "responseHeaders": [
            {
                "name": "content-type",
                "value": "text/html" if change == "wrong_content_type" else "application/javascript",
            }
        ],
    }
    if change == "redirect":
        event["redirectedRequestId"] = "previous_fixture"
    continued = []

    class Session:
        async def send(self, method, params=None):
            if method == "Page.getFrameTree":
                return {
                    "frameTree": {
                        "frame": {
                            "id": "main_fixture",
                            "url": entry + "?other=1" if change == "other_entry" else entry,
                        }
                    }
                }
            if method == "Fetch.getResponseBody":
                body = (
                    "not valid base64!"
                    if change == "invalid_base64"
                    else "x" * 3_000_001
                    if change == "oversized_body"
                    else base64.b64encode(FIXTURE_SOURCE).decode()
                )
                return {"body": body, "base64Encoded": change != "oversized_body"}
            if method == "Fetch.continueResponse":
                continued.append(params["requestId"])
            return {}

    await guard.redirect_response(
        Session(),
        event,
        target_id="other_fixture" if change == "foreign_target" else "primary_fixture",
        target_type="page",
    )
    exclusion = next(e for e in guard.events if e.get("decision") == "billing_source_unverified")
    assert exclusion["first_capture_subpredicate"] == predicate
    parent_checks = guard.checkout_navigation.parent_checks(
        DODO_CHECKOUT + "/Private1", {"url": DODO_CHECKOUT + "/Private1?from=session", "generation": 1000}
    )
    assert not parent_checks["owned_checkout_parent_exact"]
    assert not parent_checks["owned_payment_navigation_generation_current"]
    assert continued == ["source_fixture"]
    assert entry not in json.dumps(guard.events) and url not in json.dumps(guard.events)
