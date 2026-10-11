"""Capture-only boundaries, with invented external requests and native bodies."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from tryfolk.native_card import NormalCardBody
from tryfolk.native_capture import CaptureOnlyGuard
from test_native_card import RAW, INFO, RETURN, confirmation


def body_diagnostic(return_url=RETURN):
    request, _ = confirmation()
    body = json.loads(request.post_data)
    body['return_url'] = return_url
    body['payment_method_data']['billing'] = {'address': {'country': 'US'}, 'email': 'private@example.invalid'}
    body['customer_acceptance'] = {'acceptance_type': 'online', 'accepted_at': datetime.now(timezone.utc).isoformat(), 'online': {'user_agent': INFO['user_agent']}}
    checker = NormalCardBody(RAW, 42, 'http://127.0.0.1:3300', INFO, {'country': 'US', 'email': 'private@example.invalid'})
    result = checker.diagnose(body, return_url)
    assert result['request_admitted'] is False
    assert checker.return_url is None
    assert not hasattr(checker, 'arm_native_pay') and not hasattr(checker, 'consume')
    assert 'private@example.invalid' not in json.dumps(result)
    return result


def test_capture_only_valid_closed_body_has_no_arm_consume_or_admission():
    result = body_diagnostic()
    assert result['closed_body_valid']
    assert all(result['binding_checks'].values())


def test_capture_only_denied_return_still_evaluates_independent_card_billing_and_acceptance():
    result = body_diagnostic('https://test.checkout.dodopayments.com/status/private-payment?session=private-session')
    assert not result['closed_body_valid']
    assert result['first_failed_predicate'] == 'return_destination_approved'
    for name in ('test_card_number_bound', 'test_card_expiry_bound', 'test_card_cvc_bound', 'billing_country_bound', 'billing_email_bound', 'customer_acceptance_online_bound', 'customer_acceptance_time_bound'):
        assert result['binding_checks'][name]
    assert 'private-payment' not in json.dumps(result) and 'private-session' not in json.dumps(result)


class Route:
    def __init__(self, url, method='POST', body=None):
        self.request = SimpleNamespace(url=url, method=method, post_data=body)
        self.aborted = False
    async def abort(self, reason):
        self.aborted = True


@pytest.mark.asyncio
async def test_capture_guard_denies_confirm_poll_and_unknown_parent_write_before_base_admission():
    calls, captured = [], []
    async def base(route): calls.append(route)
    async def redirect(*args, **kwargs): calls.append('redirect')
    async def inspect(request):
        captured.append(request.url)
        return {'binding_checks': {'card_fields_closed': True}, 'request_admitted': False}
    guard = CaptureOnlyGuard(base, redirect, inspect, owned_origin='http://127.0.0.1:3300', payment_id='pay_private', checkout_id='cks_private', parent_url='https://test.checkout.dodopayments.com/private-parent?session=private-session')
    for path, method in (('/payments/pay_private/confirm', 'POST'), ('/payments/pay_private', 'GET')):
        route = Route('https://api.custom.hs.dodopayments.com' + path, method)
        await guard.route(route)
        assert route.aborted
    setup = Route('https://test.checkout.dodopayments.com/api/checkout/native-billing', body=json.dumps({'billing_country': 'US'}))
    await guard.route(setup)
    assert calls == [setup] and not setup.aborted
    guard.begin_native_pay_capture()
    unknown = Route('https://test.checkout.dodopayments.com/session/private-parent?token=private-value', body=json.dumps({'customer': 'private-value'}))
    await guard.route(unknown)
    assert unknown.aborted and calls == [setup]
    assert len(captured) == 1
    assert guard.events[-1] == {'decision': 'deny_unknown_parent_write', 'method': 'POST', 'path_template': '/session/:current_checkout_route', 'body_field_names': ['customer']}
    assert 'private' not in json.dumps(guard.events)
    owned = Route('http://127.0.0.1:3300/api/target/login')
    await guard.route(owned)
    assert calls == [setup, owned] and not owned.aborted
    after = Route('https://api.custom.hs.dodopayments.com/payments/pay_private/confirm')
    await guard.route(after)
    assert after.aborted and calls == [setup, owned]
    with pytest.raises(ValueError):
        guard.begin_native_pay_capture()


@pytest.mark.asyncio
async def test_capture_guard_unconditionally_denies_even_owned_response_redirect():
    sent, base_calls = [], []
    async def base(*args, **kwargs): base_calls.append(True)
    async def inspect(request): return {}
    guard = CaptureOnlyGuard(base, base, inspect, owned_origin='http://127.0.0.1:3300', payment_id='pay_private', checkout_id='cks_private', parent_url='https://test.checkout.dodopayments.com/private-parent')
    async def send(name, arguments): sent.append(name)
    event = {'requestId': 'fixture', 'request': {'url': 'http://127.0.0.1:3300/checkout/return'}, 'responseStatusCode': 302, 'responseHeaders': [{'name': 'Location', 'value': 'http://127.0.0.1:3300/special-downloads'}]}
    await guard.redirect_response(SimpleNamespace(send=send), event, target_type='page')
    assert sent == ['Fetch.failRequest'] and not base_calls
    assert guard.events[-1]['decision'] == 'deny_capture_only_redirect'
