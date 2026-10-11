"""Response publication boundary; browser/provider I/O below is explicitly mocked."""

from types import SimpleNamespace
import pytest
import test_dodo

from test_native_card import attempt, confirmation
from tryfolk.native_response import NativeResponseCapture

provider_records = test_dodo.records


async def test_exact_accepted_native_response_status_is_latched_before_original_bytes_are_released():
    capability = attempt()
    capability.arm_native_pay(capability.raw, capability.generation, normal_bootstrap=True)
    assert capability.consume("confirm")
    request, _ = confirmation()
    facts = {"config_bound": True, "operation": "confirm", "binding_checks": {"fresh": True}}
    protocol = {
        "main": {"id": "primary", "loader_id": "parent-loader", "url": "parent"},
        "children": [{"id": "native", "loader_id": "native-loader", "url": capability.raw,
                      "parent_id": "primary", "target_id": "native"}],
    }
    target = {"target_id": "native", "target_type": "iframe"}
    native_request = {"url": request.url, "method": request.method, "postData": request.post_data}
    network_event = {"requestId": "network-one", "frameId": "native", "loaderId": "native-loader",
                     "type": "Fetch", "request": native_request}
    paused = {"requestId": "paused-one", "networkId": "network-one", "frameId": "native",
              "resourceType": "Fetch", "request": native_request, "responseStatusCode": 200,
              "responseHeaders": [{"name": "Content-Type", "value": "application/json"}]}
    checks, commands = [], []

    async def verify(bound_request, bound_facts):
        assert bound_request is request and bound_facts is facts
        checks.append("fresh mocked ownership/context")
        return {"current_owned_Test_identity": True, "current_source_and_documents": True}

    async def send(method, args):
        commands.append((method, args))
        if method == "Fetch.getResponseBody":
            return {"body": '{"status":"succeeded"}', "base64Encoded": False}
        assert method == "Fetch.continueResponse"
        assert capability.response_status == "succeeded"
        assert args == {"requestId": "paused-one"}  # Original response is unchanged.
        return {}

    events = []
    gate = NativeResponseCapture("https://api.custom.hs.dodopayments.com", verify, events.append)
    gate.observe_network(network_event, **target)
    assert await gate.admit(request, facts, protocol, capability, "primary")
    assert await gate.capture(SimpleNamespace(send=send), paused, **target)
    assert [method for method, _ in commands] == ["Fetch.getResponseBody", "Fetch.continueResponse"]
    assert len(checks) == 2
    assert events[-1]["decision"] == "native_response_captured_before_delivery"
    assert all(events[-1]["binding_checks"].values())


async def test_resource_guard_publishes_before_delivery_and_passive_body_cannot_overwrite_it(monkeypatch):
    from test_return_navigation import return_fixture

    guard, page, child, state, identity, _ = await return_fixture(monkeypatch)
    guard.primary_response_target = "primary-response"
    guard.response_protocol_reader = lambda: guard.return_protocol_reader(None)
    capability = guard.card_attempt
    capability.response_status, capability.closed = None, False
    request, _ = confirmation()
    request.frame = child
    facts = {"config_bound": True, "operation": "confirm", "binding_checks": {"fresh": True}}
    frozen = guard._confirmed_protocol["children"][0]
    native_request = {"url": request.url, "method": request.method, "postData": request.post_data}
    target = {"target_id": guard.primary_response_target, "target_type": "page"}
    guard.native_responses.observe_network({
        "requestId": "network-one", "frameId": frozen["id"], "loaderId": frozen["loader_id"],
        "type": "Fetch", "request": native_request,
    }, **target)
    assert await guard.register_native_response(request, facts)

    async def send(method, args):
        if method == "Fetch.getResponseBody":
            return {"body": '{"status":"succeeded"}', "base64Encoded": False}
        assert method == "Fetch.continueResponse" and capability.response_status == "succeeded"
        return {}

    await guard.redirect_response(SimpleNamespace(send=send), {
        "requestId": "paused-one", "networkId": "network-one", "frameId": frozen["id"],
        "resourceType": "Fetch", "request": native_request, "responseStatusCode": 200,
        "responseHeaders": [{"name": "content-type", "value": "application/json"}],
    }, **target)

    async def body():
        raise AssertionError("Passive response body must not republish native navigation status")

    await guard.payment_response(SimpleNamespace(status=200, body=body), facts)
    assert capability.response_status == "succeeded"
    assert guard._payment_response_pending.done()
    assert any(e["decision"] == "native_response_captured_before_delivery" for e in guard.events)


@pytest.fixture
async def native_reply():
    capability = attempt()
    capability.arm_native_pay(capability.raw, capability.generation, normal_bootstrap=True)
    assert capability.consume("confirm")
    request, _ = confirmation()
    facts = {"config_bound": True, "operation": "confirm", "binding_checks": {"fresh": True}}
    target = {"target_id": "native", "target_type": "iframe"}
    wire = {"url": request.url, "method": request.method, "postData": request.post_data}
    state = SimpleNamespace(
        capability=capability, request=request, facts=facts, target=target, commands=[], events=[],
        body={"body": '{"status":"succeeded"}', "base64Encoded": False},
        context={"current_owned_Test_identity": True, "current_source_and_documents": True},
        after_body=None,
        paused={"requestId": "paused", "networkId": "network", "frameId": "native",
                "resourceType": "Fetch", "request": dict(wire), "responseStatusCode": 200,
                "responseHeaders": [{"name": "Content-Type", "value": "application/json"}]},
    )

    async def verify(*_):
        return dict(state.context)

    async def send(method, args):
        state.commands.append((method, args))
        if method == "Fetch.getResponseBody":
            if state.after_body:
                state.after_body()
            return state.body
        return {}

    state.session = SimpleNamespace(send=send)
    state.gate = NativeResponseCapture("https://api.custom.hs.dodopayments.com", verify, state.events.append)
    state.gate.observe_network({"requestId": "network", "frameId": "native", "loaderId": "loader",
                               "type": "Fetch", "request": wire}, **target)
    protocol = {"main": {"id": "primary"},
                "children": [{"id": "native", "parent_id": "primary", "loader_id": "loader",
                              "url": capability.raw, "target_id": "native"}]}
    assert await state.gate.admit(request, facts, protocol, capability, "primary")
    return state


async def test_duplicate_status_fields_cannot_publish_an_ambiguous_native_status(native_reply):
    reply = native_reply
    reply.body["body"] = '{"status":"unknown","status":"succeeded"}'
    assert await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status is None
    assert reply.commands[-1][0] == "Fetch.failRequest"


async def test_native_fetch_can_have_the_actual_CDP_response_XHR_label_for_the_same_network_ID(native_reply):
    reply = native_reply
    reply.paused["resourceType"] = "XHR"
    await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status == "succeeded"
    assert reply.commands[-1][0] == "Fetch.continueResponse"


@pytest.mark.parametrize("ir16", [False, True])
async def test_native_HTTP_error_preserves_exact_fallback_limit_without_publishing_status(native_reply, ir16):
    reply = native_reply
    reply.paused["responseStatusCode"] = 400
    reply.body["body"] = '{"error":{"code":"IR_16"}}' if ir16 else '{"error":{"code":"other"}}'
    await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status is None
    assert reply.capability.closed is ir16
    assert reply.capability.fallback_remaining == (0 if ir16 else 1)
    assert reply.commands[-1] == ("Fetch.continueResponse", {"requestId": "paused"})
    assert reply.capability.consume("poll") is (not ir16)
    assert not reply.capability.consume("poll")


@pytest.mark.parametrize("shape", ["entries_only", "matching_both", "fragmented", "mismatched_both", "missing_bytes", "missing_body"])
async def test_native_POST_byte_representations_bind_at_network_and_response(native_reply, shape):
    import base64

    reply = native_reply
    wire = {"url": reply.request.url, "method": "POST", "hasPostData": True,
            "postDataEntries": [{"bytes": base64.b64encode(reply.request.post_data.encode()).decode()}]}
    if shape in ("matching_both", "mismatched_both"):
        wire["postData"] = reply.request.post_data if shape == "matching_both" else "{}"
    elif shape == "fragmented":
        raw = reply.request.post_data.encode()
        wire["postDataEntries"] = [{"bytes": base64.b64encode(piece).decode()} for piece in (raw[:10], raw[10:])]
    elif shape == "missing_bytes":
        wire["postDataEntries"] = [{}]
    elif shape == "missing_body":
        wire.pop("postDataEntries")

    async def verify(*_):
        return dict(reply.context)

    gate = NativeResponseCapture("https://api.custom.hs.dodopayments.com", verify, reply.events.append)
    gate.observe_network({"requestId": "network", "frameId": "native", "loaderId": "loader",
                          "type": "Fetch", "request": wire}, **reply.target)
    protocol = {"main": {"id": "primary"}, "children": [
        {"id": "native", "parent_id": "primary", "loader_id": "loader",
         "url": reply.capability.raw, "target_id": "native"},
    ]}
    admitted = await gate.admit(reply.request, reply.facts, protocol, reply.capability, "primary")
    assert admitted is (shape in ("entries_only", "matching_both", "fragmented"))
    if not admitted:
        assert reply.capability.response_status is None and reply.commands == []
        return
    reply.paused["request"] = wire
    await gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status == "succeeded"
    assert reply.commands[-1][0] == "Fetch.continueResponse"


@pytest.mark.parametrize("body", [
    "{}", '{"paymentIntent":{"status":"succeeded"}}', '{"status":"unknown"}',
    '{"status":true}', '{"status":1}', '{"status":[]}', '[{"status":"succeeded"}]',
    '{"status":"succeeded","other":NaN}', "{", " ", "x" * 200_001,
])
async def test_unknown_or_malformed_body_cannot_be_replaced_with_provider_success(native_reply, body):
    reply = native_reply
    reply.body["body"] = body
    await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status is None
    assert reply.commands[-1][0] == "Fetch.failRequest"
    assert reply.events[-1]["decision"] == "deny_native_response"


@pytest.mark.parametrize("mutation", [
    "network", "frame", "target", "method", "body", "redirect", "resource",
    "content_type", "duplicate_content_type", "HTTP_redirect", "HTTP_type", "encoding_flag",
    "invalid_base64", "invalid_UTF8", "current_identity", "identity_changed_during_body",
    "entries_missing", "entries_mismatch", "entries_invalid_base64", "entries_oversized", "presence_flag",
])
async def test_native_response_metadata_body_bytes_and_identity_fail_closed(native_reply, mutation):
    import base64

    reply = native_reply
    if mutation == "network":
        reply.paused["networkId"] = "another"
    elif mutation == "frame":
        reply.paused["frameId"] = "another"
    elif mutation == "target":
        reply.target = {"target_id": "another", "target_type": "iframe"}
    elif mutation == "method":
        reply.paused["request"]["method"] = "GET"
    elif mutation == "body":
        reply.paused["request"]["postData"] = "{}"
    elif mutation == "redirect":
        reply.paused["redirectedRequestId"] = "earlier"
    elif mutation == "resource":
        reply.paused["resourceType"] = "Document"
    elif mutation == "content_type":
        reply.paused["responseHeaders"][0]["value"] = "text/html"
    elif mutation == "duplicate_content_type":
        reply.paused["responseHeaders"] *= 2
    elif mutation == "HTTP_redirect":
        reply.paused["responseStatusCode"] = 302
    elif mutation == "HTTP_type":
        reply.paused["responseStatusCode"] = "200"
    elif mutation == "encoding_flag":
        reply.body["base64Encoded"] = "false"
    elif mutation == "invalid_base64":
        reply.body.update(body="%%%", base64Encoded=True)
    elif mutation == "invalid_UTF8":
        reply.body.update(body=base64.b64encode(b"\xff").decode(), base64Encoded=True)
    elif mutation == "current_identity":
        reply.context["current_owned_Test_identity"] = False
    elif mutation == "identity_changed_during_body":
        reply.after_body = lambda: reply.context.update(current_owned_Test_identity=False)
    elif mutation == "entries_missing":
        reply.paused["request"].pop("postData")
        reply.paused["request"]["hasPostData"] = True
    elif mutation.startswith("entries_"):
        reply.paused["request"]["postDataEntries"] = [{"bytes": {
            "entries_mismatch": base64.b64encode(b"{}").decode(),
            "entries_invalid_base64": "%%%", "entries_oversized": "a" * 133_337,
        }[mutation]}]
    elif mutation == "presence_flag":
        reply.paused["request"]["hasPostData"] = False
    await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status is None
    assert reply.commands[-1][0] == "Fetch.failRequest"


async def test_replayed_response_network_ID_never_reads_or_republishes_a_second_body(native_reply):
    reply = native_reply
    await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status == "succeeded"
    reply.body["body"] = '{"status":"failed"}'
    await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status == "succeeded"
    assert [method for method, _ in reply.commands].count("Fetch.getResponseBody") == 1
    assert reply.commands[-1][0] == "Fetch.failRequest"


async def test_unregistered_passive_reply_cannot_publish_status_even_when_body_is_available(monkeypatch):
    from test_return_navigation import return_fixture

    guard, *_ = await return_fixture(monkeypatch)
    guard.card_attempt.response_status = None

    async def body():
        return b'{"status":"succeeded"}'

    await guard.payment_response(SimpleNamespace(body=body, status=200), {"operation": "confirm"})
    assert guard.card_attempt.response_status is None


async def test_network_redirect_reusing_ID_cannot_publish_an_original_native_status(native_reply):
    reply = native_reply
    reply.gate.observe_network({
        "requestId": "network", "frameId": "native", "loaderId": "loader", "type": "Fetch",
        "request": dict(reply.paused["request"]), "redirectResponse": {"status": 307},
    }, **reply.target)
    await reply.gate.capture(reply.session, reply.paused, **reply.target)
    assert reply.capability.response_status is None
    assert reply.commands[-1][0] == "Fetch.failRequest"


async def test_successive_bounded_polls_bind_fresh_network_IDs_in_the_same_current_native_document(native_reply):
    reply = native_reply
    reply.capability.observe_response("processing", 200, operation="confirm")
    reply.body["body"] = '{"status":"processing"}'
    request = SimpleNamespace(url="https://api.custom.hs.dodopayments.com/payments/pay_fixture?force_sync=true",
                              method="GET", post_data=None, resource_type="fetch")
    protocol = {"main": {"id": "primary"}, "children": [
        {"id": "native", "parent_id": "primary", "loader_id": "loader",
         "url": reply.capability.raw, "target_id": "native"},
    ]}
    for index in range(2):
        network_id = f"poll-{index}"
        wire = {"url": request.url, "method": "GET"}
        reply.gate.observe_network({"requestId": network_id, "frameId": "native", "loaderId": "loader",
                                   "type": "Fetch", "request": wire}, **reply.target)
        assert reply.capability.consume("poll")
        facts = {"config_bound": True, "operation": "poll", "binding_checks": {"fresh": True}}
        assert await reply.gate.admit(request, facts, protocol, reply.capability, "primary")
        reply.paused.update(networkId=network_id, request=wire, requestId=f"paused-{index}")
        await reply.gate.capture(reply.session, reply.paused, **reply.target)
        assert reply.capability.response_status == "processing"
        assert reply.commands[-1][0] == "Fetch.continueResponse"
    assert reply.capability.polls == 2


@pytest.mark.parametrize("status", ["succeeded", "processing", "failed"])
async def test_post_confirm_response_uses_actual_target_callback_phase_and_preserves_ownership(
    monkeypatch, provider_records, status,
):
    from copy import deepcopy
    import httpx
    from test_return_navigation import return_fixture
    from tryfolk.dodo import DodoTarget, OWNERSHIP

    guard, page, child, state, identity, _ = await return_fixture(monkeypatch)
    guard.response_protocol_reader = lambda: guard.return_protocol_reader(None)
    order, receipt, _ = deepcopy(provider_records)
    order.update(identity["ownership"], order_id=identity["order_id"],
                 checkout_session_id=identity["checkout_session_id"], customer_id="cus_fixture", active=True)
    payment = receipt["payment"]
    payment.update(payment_id=identity["payment_id"], checkout_session_id=order["checkout_session_id"],
                   customer_id=order["customer_id"], status=status)
    payment["metadata"].update({key: order[key] for key in (*OWNERSHIP, "order_id")})
    receipt.update(state="failed" if status == "failed" else "succeeded" if status == "succeeded" else "pending",
                   ownership={key: order[key] for key in (*OWNERSHIP, "order_id", "expected_product_id")},
                   checkout_session_id=order["checkout_session_id"], private_payment_link=identity["payment_link"])
    receipt["checkout"].update(id=order["checkout_session_id"], payment_id=payment["payment_id"], payment_status=status)
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/target/readiness":
            body = {"manifest": {
                "app_origin": guard.policy.origin, "safe_origins": [guard.policy.origin],
                "provider_environment": "test_mode", "checkout_origins": ["https://test.checkout.dodopayments.com"],
                "upstream_commit": "ef7cbd980d8f1e57b036dc2af71ff05521b6175a", "source_clean": True,
                "source_commit": "a" * 40, "source_sha256": "b" * 64,
                "target_revision": order["target_revision"],
            }}
        elif request.url.path == "/api/target/orders":
            body = {"orders": [order]}
        elif request.url.path == "/api/target/receipts":
            assert request.url.params["browser_parent"] == "1"
            body = receipt
        else:
            raise AssertionError("Only independent read-only owned identity endpoints are permitted")
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(**kw, transport=httpx.MockTransport(respond)))
    target = DodoTarget(guard.policy.origin)
    run = {"id": order["run_id"], "revision": order["target_revision"]}
    fixture = {key: order[key] for key in OWNERSHIP}
    guard.frame_verify = lambda observed: target.frame_context(observed, run, fixture)
    request, _ = confirmation()
    request.frame = child
    checks = await guard.verify_native_response(request, {"operation": "confirm"})
    assert all(checks.values()), checks
    assert all(method == "GET" for method, _ in calls)
    # A post-confirm phase does not admit a receipt for another customer.
    payment["customer_id"] = "cus_other"
    assert not all((await guard.verify_native_response(request, {"operation": "confirm"})).values())
