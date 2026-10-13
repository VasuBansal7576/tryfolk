"""Actual Dodo identity adapter with disclosed independent HTTP response fixtures."""
import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
import test_dodo

from tryfolk.dodo import DodoTarget, OWNERSHIP
from tryfolk.secure_frame import BROWSER_API


@pytest.fixture
def records():
    return test_dodo.records.__wrapped__()


def identity_fixture(monkeypatch, records, *, mismatch_reads=1, mutation=None, after_first=None,
                     slow_stage=None, origin='http://127.0.0.1:3338', provider_error_reads=0,
                     provider_error_reason='provider_read_failed', provider_error_mutation=None,
                     expected_read_purpose=None):
    order, receipt, _ = copy.deepcopy(records)
    order['customer_id'] = receipt['payment']['customer_id'] = 'closed_fixture_customer'
    receipt['private_payment_link'] = 'https://test.checkout.dodopayments.com/Private1'
    calls, reads = [], []
    readiness = {'manifest': {'app_origin': origin,
        'safe_origins': [origin], 'provider_environment': 'test_mode',
        'upstream_commit': 'ef7cbd980d8f1e57b036dc2af71ff05521b6175a', 'source_clean': True,
        'source_commit': 'a' * 40, 'source_sha256': 'b' * 64,
        'checkout_origins': ['https://test.checkout.dodopayments.com'], 'target_revision': order['target_revision']}}

    async def respond(request):
        calls.append((request.method, request.url.path))
        assert request.method == 'GET'
        if request.url.path == slow_stage:
            await asyncio.sleep(0.1)
        if request.url.path == '/api/target/readiness':
            if reads and after_first:
                after_first(readiness, order, receipt)
            return httpx.Response(200, json=readiness)
        if request.url.path == '/api/target/orders':
            return httpx.Response(200, json={'orders': [order]})
        assert request.url.path == '/api/target/receipts' and request.url.params['browser_parent'] == '1'
        if expected_read_purpose is not None:
            assert request.url.params['read_purpose'] == expected_read_purpose
        if mutation == 'HTTP_error':
            return httpx.Response(503, content=b'not JSON')
        if mutation == 'malformed':
            return httpx.Response(200, content=b'not JSON')
        body = copy.deepcopy(receipt)
        if len(reads) < mismatch_reads:
            body.update(state='mismatch', mismatches=['checkout.payment_status'])
            body['checkout']['payment_status'] = 'requires_payment_method'
        if mutation == 'payment':
            body['checkout']['payment_id'] = 'changed_payment'
        elif mutation == 'checkout':
            body['checkout']['id'] = 'changed_checkout'
        elif mutation == 'ownership':
            body['ownership']['account_id'] = 'changed_owner'
        elif mutation == 'customer':
            body['payment']['customer_id'] = 'changed_customer'
        elif mutation == 'amount':
            body['payment']['amount_minor'] += 1
        elif mutation == 'product':
            body['payment']['product_cart'][0]['quantity'] = 2
        elif mutation == 'metadata':
            body['payment']['metadata']['persona_id'] = 'changed_persona'
        elif mutation == 'source':
            body['source'] = 'external_fixture'
        elif mutation == 'extra_mismatch':
            body['mismatches'].append('amount')
        elif mutation == 'provider_error':
            body.update(state='provider_error', reason='provider_read_failed')
        elif mutation == 'different_status_pair':
            body['payment']['status'] = 'processing'
        if len(reads) < provider_error_reads:
            body = {key: body[key] for key in ('source', 'ownership', 'checkout_session_id')}
            body.update(state='provider_error', reason=provider_error_reason, private_payment_link=None)
            if provider_error_mutation:
                provider_error_mutation(body)
        reads.append(body)
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **options: client(
        **options, transport=httpx.MockTransport(respond)))
    observed = {'payment_id': receipt['payment']['payment_id'], 'api_base_verified': True, 'api_url': BROWSER_API,
        'browser_api_role': 'observed_approved_shared_service_config', 'service_sandbox_only_attested': False,
        'sandbox_key': True, 'parent_matches': True, 'operation': 'poll',
        'identity_stage': 'native_response_capture'}
    return DodoTarget(origin), observed, {key: order[key] for key in OWNERSHIP}, calls, reads


@pytest.mark.parametrize('operation,identity_stage,purpose', [
    ('bootstrap', None, 'pre_payment_identity'),
    ('session_tokens', None, 'pre_payment_identity'),
    ('bootstrap', 'native_response_capture', 'fresh'),
    ('session_tokens', 'native_response_capture', 'fresh'),
    ('confirm', None, 'fresh'),
    ('poll', None, 'fresh'),
    ('poll', 'native_response_capture', 'fresh'),
    ('unknown_operation', None, 'fresh'),
])
async def test_only_pre_payment_identity_operations_request_bounded_reuse(
    monkeypatch, records, operation, identity_stage, purpose
):
    target, observed, fixture, calls, reads = identity_fixture(
        monkeypatch, records, mismatch_reads=0, expected_read_purpose=purpose)
    observed.update(operation=operation, identity_stage=identity_stage)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert len(reads) == 1 and all(method == 'GET' for method, _ in calls)
    # A fixture showing an already succeeded payment remains inadmissible before
    # Pay. Choosing the cache purpose cannot relax the independent status guard.
    assert result['approved'] is (operation == 'poll')


async def test_native_capture_identity_converges_only_after_independent_checkout_status_matches(
    monkeypatch, records
):
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is True
    assert len(reads) == 2 and result['payment_status'] == 'succeeded'
    diagnostics = result['identity_diagnostics']['reads']
    assert diagnostics[0]['outcome'] == 'checkout_status_convergence_pending'
    assert diagnostics[0]['first_failed_predicate'] == 'receipt_state_allowed'
    assert diagnostics[0]['checks']['checkout_payment_status_matches'] is False
    assert diagnostics[1]['outcome'] == 'approved'
    assert all(method == 'GET' for method, _ in calls)
    assert len(calls) == 6  # Only the same readiness/orders/receipt reads; no confirmation or grant.
    assert all(item['http_status'] == 200 for read in diagnostics for item in read['HTTP_reads'])
    assert not any(value in json.dumps(diagnostics) for value in ('Private1', '"pay"', 'closed_fixture_customer', BROWSER_API))


async def test_ordinary_poll_retains_immediate_denial_and_named_status_disagreement(monkeypatch, records):
    target, observed, fixture, _, reads = identity_fixture(monkeypatch, records)
    observed.pop('identity_stage')
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 1
    assert result['identity_diagnostics']['reads'][0]['checks']['checkout_payment_status_matches'] is False


async def test_persistent_same_owned_status_disagreement_exhausts_finite_read_only_bound(monkeypatch, records):
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records, mismatch_reads=10)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 3
    assert len(calls) == 9 and all(method == 'GET' for method, _ in calls)
    assert result['identity_diagnostics']['retry_bound_exhausted'] is True


@pytest.mark.parametrize('mutation', ['payment', 'checkout', 'ownership', 'customer', 'amount',
    'product', 'metadata', 'source', 'extra_mismatch', 'provider_error', 'different_status_pair'])
async def test_status_convergence_never_retries_a_changed_independent_identity(monkeypatch, records, mutation):
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records, mutation=mutation)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 1 and len(calls) == 3
    assert result['identity_diagnostics']['reads'][0]['outcome'] == 'denied'


@pytest.mark.parametrize('mutation,error_type,status', [('HTTP_error', httpx.HTTPStatusError, 503),
    ('malformed', json.JSONDecodeError, 200)])
async def test_identity_HTTP_and_JSON_errors_preserve_actual_stage_status_without_retry(
    monkeypatch, records, mutation, error_type, status
):
    target, observed, fixture, calls, _ = identity_fixture(monkeypatch, records, mutation=mutation)
    with pytest.raises(error_type) as raised:
        await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert len(calls) == 3
    facts = raised.value.identity_diagnostics['reads'][0]
    assert facts['outcome'] == 'error'
    assert facts['HTTP_reads'][-1]['stage'] == 'receipt'
    assert facts['HTTP_reads'][-1]['http_status'] == status
    assert facts['HTTP_reads'][-1]['error_type'] == error_type.__name__


async def test_deadline_between_identity_reads_does_not_duplicate_completed_HTTP_facts(monkeypatch, records):
    target, observed, fixture, calls, _ = identity_fixture(monkeypatch, records)
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda _: real_timeout(0.02))
    with pytest.raises(TimeoutError) as raised:
        await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    reads = raised.value.identity_diagnostics['reads']
    assert len(calls) == 3 and len(reads) == 2
    assert len(reads[0]['HTTP_reads']) == 3
    assert reads[1] == {'stage': 'identity_read_deadline', 'outcome': 'error',
                        'error_type': 'TimeoutError', 'HTTP_reads': []}


@pytest.mark.parametrize('change', ['source', 'order', 'checkout', 'amount', 'currency', 'product', 'customer'])
async def test_converged_status_cannot_approve_a_changed_otherwise_consistent_binding(monkeypatch, records, change):
    def replace(readiness, order, receipt):
        if change == 'source':
            readiness['manifest']['source_sha256'] = 'c' * 64
        elif change == 'order':
            order['order_id'] = receipt['ownership']['order_id'] = receipt['payment']['metadata']['order_id'] = 'other_order'
        elif change == 'checkout':
            order['checkout_session_id'] = receipt['checkout_session_id'] = receipt['checkout']['id'] = receipt['payment']['checkout_session_id'] = 'other_checkout'
        elif change in ('amount', 'currency'):
            key, value = ('amount_minor', 1300) if change == 'amount' else ('currency', 'EUR')
            order[key] = receipt['payment'][key] = value
        elif change == 'product':
            order['expected_product_id'] = receipt['ownership']['expected_product_id'] = 'other_product'
            receipt['payment']['metadata']['product_id'] = 'other_product'
            receipt['payment']['product_cart'][0]['product_id'] = 'other_product'
        elif change == 'customer':
            order['customer_id'] = receipt['payment']['customer_id'] = 'other_customer'

    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records, after_first=replace)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 2 and len(calls) == 6
    last = result['identity_diagnostics']['reads'][-1]
    assert last['outcome'] == 'denied'
    assert last['first_failed_predicate'] == 'same_identity_across_convergence_reads'


async def test_in_flight_identity_deadline_retains_stage_without_inventing_HTTP_status(monkeypatch, records):
    target, observed, fixture, calls, _ = identity_fixture(monkeypatch, records,
        slow_stage='/api/target/receipts')
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda _: real_timeout(0.02))
    with pytest.raises(TimeoutError) as raised:
        await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    reads = raised.value.identity_diagnostics['reads']
    assert len(calls) == 3 and len(reads) == 1
    assert reads[0]['HTTP_reads'][-1] == {'stage': 'receipt', 'response_received': False}


@pytest.mark.parametrize('document_changes', [False, True])
@pytest.mark.parametrize('provider_unavailable', [False, True])
async def test_native_response_capture_waits_for_same_identity_and_denies_document_change_during_retry(
    monkeypatch, records, document_changes, provider_unavailable
):
    from test_native_card import confirmation
    from test_return_navigation import return_fixture

    guard, page, child, state, identity, _ = await return_fixture(monkeypatch)
    order, receipt, entitlement = copy.deepcopy(records)
    order.update(**identity['ownership'], order_id=identity['order_id'],
                 checkout_session_id=identity['checkout_session_id'])
    receipt['ownership'] = {key: order[key] for key in (*OWNERSHIP, 'order_id', 'expected_product_id')}
    receipt['checkout_session_id'] = receipt['checkout']['id'] = receipt['payment']['checkout_session_id'] = identity['checkout_session_id']
    receipt['checkout']['payment_id'] = receipt['payment']['payment_id'] = identity['payment_id']
    receipt['payment']['metadata'] = {**receipt['ownership'], 'product_id': order['expected_product_id']}

    def change_document(*_):
        if document_changes:
            state['generation'] += 1

    target, _, fixture, calls, reads = identity_fixture(monkeypatch, (order, receipt, entitlement),
        after_first=change_document, origin=guard.policy.origin,
        mismatch_reads=0 if provider_unavailable else 1,
        provider_error_reads=1 if provider_unavailable else 0)
    run = {'id': fixture['run_id'], 'revision': fixture['target_revision']}

    async def verify(observed):
        return await target.frame_context(observed, run, fixture)

    guard.frame_verify = verify
    guard.primary_response_target = 'primary-response'
    guard.response_protocol_reader = lambda: guard.return_protocol_reader(None)
    capability = guard.card_attempt
    capability.response_status, capability.closed = None, False
    request, _ = confirmation()
    request.frame = child
    wire = {'url': request.url, 'method': request.method, 'postData': request.post_data}
    frozen = guard._confirmed_protocol['children'][0]
    target_info = {'target_id': guard.primary_response_target, 'target_type': 'page'}
    guard.native_responses.observe_network({'requestId': 'network', 'frameId': frozen['id'],
        'loaderId': frozen['loader_id'], 'type': 'Fetch', 'request': wire}, **target_info)
    assert await guard.register_native_response(request,
        {'config_bound': True, 'operation': 'confirm', 'binding_checks': {'fixture_binding': True}})
    commands = []

    async def send(method, args):
        commands.append(method)
        if method == 'Fetch.getResponseBody':
            return {'body': '{"status":"succeeded"}', 'base64Encoded': False}
        if method == 'Fetch.continueResponse':
            assert capability.response_status == 'succeeded'
        return {}

    await guard.redirect_response(SimpleNamespace(send=send), {'requestId': 'paused', 'networkId': 'network',
        'frameId': frozen['id'], 'resourceType': 'Fetch', 'request': wire, 'responseStatusCode': 200,
        'responseHeaders': [{'name': 'Content-Type', 'value': 'application/json'}]}, **target_info)
    if document_changes:
        assert commands == ['Fetch.failRequest'] and capability.response_status is None
        assert guard.events[-1]['first_failed_predicate'] == 'documents_unchanged_during_independent_identity_read'
        assert len(reads) == 2
    else:
        assert commands == ['Fetch.getResponseBody', 'Fetch.continueResponse']
        assert capability.response_status == 'succeeded' and len(reads) == 3
    assert all(method == 'GET' for method, _ in calls)
    assert any(event.get('decision') == 'independent_native_identity_read' for event in guard.events)


@pytest.mark.parametrize("field,new_value", [("source_commit", "c" * 40), ("source_sha256", "d" * 64)])
async def test_fresh_callback_rejects_changed_admitted_run_source_pins_without_more_provider_reads(
    monkeypatch, records, field, new_value
):
    def change(readiness, order, receipt):
        readiness["manifest"][field] = new_value
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records, after_first=change)
    result = await target.frame_context(
        observed, {"id": "r", "revision": "rev", "source_commit": "a" * 40, "source_digest": "b" * 64}, fixture
    )
    assert result["approved"] is False
    expected = "exact_run_source_commit" if field == "source_commit" else "exact_run_source_digest"
    assert result["identity_diagnostics"]["reads"][-1]["first_failed_predicate"] == expected
    assert len(reads) == 1 and len(calls) == 4


async def test_matching_admitted_source_pins_use_existing_readiness_cycle_only(monkeypatch, records):
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records, mismatch_reads=0)
    result = await target.frame_context(
        observed, {"id": "r", "revision": "rev", "source_commit": "a" * 40, "source_digest": "b" * 64}, fixture
    )
    assert result["approved"] is True and len(calls) == 3


async def test_closed_provider_read_error_retries_same_gets_and_requires_fresh_complete_identity(monkeypatch, records):
    target, observed, fixture, calls, reads = identity_fixture(
        monkeypatch, records, mismatch_reads=0, provider_error_reads=1)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is True and len(reads) == 2
    assert calls == [('GET', path) for _ in range(2) for path in
        ('/api/target/readiness', '/api/target/orders', '/api/target/receipts')]
    facts = result['identity_diagnostics']['reads']
    assert facts[0]['outcome'] == 'provider_read_pending'
    assert facts[0]['provider_error_reason'] == 'provider_read_failed'
    assert facts[0]['provider_HTTP_status_available'] is False
    assert facts[1]['outcome'] == 'approved' and all(facts[1]['checks'].values())
    assert not any(secret in json.dumps(facts) for secret in ('Private1', 'closed_fixture_customer', BROWSER_API))


async def test_persistent_provider_error_exhausts_existing_read_bound_without_approval(monkeypatch, records):
    target, observed, fixture, calls, reads = identity_fixture(
        monkeypatch, records, mismatch_reads=0, provider_error_reads=10)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 3 and len(calls) == 9
    assert result['identity_diagnostics']['retry_bound_exhausted'] is True


@pytest.mark.parametrize('reason', ['invalid_provider_response', 'creation_error', 'authentication_failed', 'unknown'])
async def test_provider_error_other_reasons_never_retry(monkeypatch, records, reason):
    target, observed, fixture, calls, reads = identity_fixture(
        monkeypatch, records, mismatch_reads=0, provider_error_reads=1, provider_error_reason=reason)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 1 and len(calls) == 3


@pytest.mark.parametrize('field', ['ownership', 'source', 'checkout_session_id', 'payment'])
async def test_provider_error_wrong_or_partial_identity_never_retries(monkeypatch, records, field):
    def mutate(body):
        body[field] = {'payment_id': 'untrusted'} if field == 'payment' else {} if field == 'ownership' else 'wrong'
    target, observed, fixture, calls, reads = identity_fixture(
        monkeypatch, records, mismatch_reads=0, provider_error_reads=1, provider_error_mutation=mutate)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 1 and len(calls) == 3


@pytest.mark.parametrize('change', ['source', 'order', 'checkout', 'amount', 'currency', 'product', 'customer'])
async def test_provider_recovery_cannot_substitute_otherwise_consistent_source_order(monkeypatch, records, change):
    def replace(readiness, order, receipt):
        if change == 'source':
            readiness['manifest']['source_sha256'] = 'c' * 64
        elif change == 'order':
            order['order_id'] = receipt['ownership']['order_id'] = receipt['payment']['metadata']['order_id'] = 'other_order'
        elif change == 'checkout':
            order['checkout_session_id'] = receipt['checkout_session_id'] = receipt['checkout']['id'] = receipt['payment']['checkout_session_id'] = 'other_checkout'
        elif change in ('amount', 'currency'):
            key, value = ('amount_minor', 1300) if change == 'amount' else ('currency', 'EUR')
            order[key] = receipt['payment'][key] = value
        elif change == 'product':
            order['expected_product_id'] = receipt['ownership']['expected_product_id'] = 'other_product'
            receipt['payment']['metadata']['product_id'] = 'other_product'
            receipt['payment']['product_cart'][0]['product_id'] = 'other_product'
        elif change == 'customer':
            order['customer_id'] = receipt['payment']['customer_id'] = 'other_customer'
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records, mismatch_reads=0,
        provider_error_reads=1, after_first=replace)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 2 and len(calls) == 6
    assert result['identity_diagnostics']['reads'][-1]['first_failed_predicate'] == 'same_identity_across_convergence_reads'


async def test_ordinary_provider_error_denies_without_retry(monkeypatch, records):
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records,
        mismatch_reads=0, provider_error_reads=1)
    observed.pop('identity_stage')
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 1 and len(calls) == 3


async def test_provider_read_recovery_cannot_change_captured_observed_payment(monkeypatch, records):
    def change_observation(*_):
        observed['payment_id'] = 'changed_observed_payment'
    target, observed, fixture, calls, reads = identity_fixture(monkeypatch, records,
        mismatch_reads=0, provider_error_reads=1, after_first=change_observation)
    result = await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert result['approved'] is False and len(reads) == 2 and len(calls) == 6
    assert result['identity_diagnostics']['reads'][-1]['first_failed_predicate'] == 'exact_observed_payment'


async def test_provider_read_retry_remains_inside_existing_capture_deadline(monkeypatch, records):
    target, observed, fixture, calls, _ = identity_fixture(monkeypatch, records,
        mismatch_reads=0, provider_error_reads=1)
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda _: real_timeout(0.02))
    with pytest.raises(TimeoutError) as raised:
        await target.frame_context(observed, {'id': 'r', 'revision': 'rev'}, fixture)
    assert len(calls) == 3
    assert raised.value.identity_diagnostics['retry_bound_exhausted'] is True
