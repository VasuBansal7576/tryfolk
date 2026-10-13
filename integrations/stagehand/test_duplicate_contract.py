"""Owned HTTP boundary fixtures; not measured Dodo journey evidence."""
import json
import unittest
from copy import deepcopy
from local_session import reconcile_duplicate


class DuplicateContract(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, changed_order=False, changed_response=False):
        ownership = {key: key + '-fixture' for key in ('run_id', 'persona_id', 'account_id', 'fixture_id', 'target_revision')}
        body = {'ownership': ownership, 'has_access': True, 'orders': [{**ownership, 'order_id': 'ord-fixture', 'checkout_session_id': 'checkout-fixture', 'payment_id': 'payment-fixture', 'active': True, 'granted': True}]}
        if changed_order:
            body['orders'][0]['order_id'] = 'another-order'
        calls = []
        class Response:
            status = 200
            url = 'http://127.0.0.1:3310/api/target/reconcile'
            headers = {'content-type': 'application/json'}
            async def body(self):
                result = {'state': 'already_granted', 'source': 'dodo_test_api_reconciliation', 'grant_applied': False, 'has_access': True, 'payment_id': 'payment-fixture', 'ownership': {**ownership, 'order_id': 'ord-fixture'}}
                if changed_response:
                    result['payment_id'] = 'different-payment'
                return json.dumps(result).encode()
            async def dispose(self):
                calls.append('disposed')
        class CookieJar:
            async def post(self, url, **kwargs):
                calls.append((url, kwargs))
                return Response()
        async def reader():
            return {'http_status': 200, 'same_origin_no_redirect': True, 'body': deepcopy(body)}
        try:
            result = await reconcile_duplicate(CookieJar(), reader, 'http://127.0.0.1:3310', ownership=ownership, order_id='ord-fixture', payment_id='payment-fixture', checkout_id='checkout-fixture')
            return result, calls
        except ValueError:
            return None, calls

    async def test_same_actual_cookiejar_posts_exact_order_once_without_cookie_export(self):
        result, calls = await self.exercise()
        self.assertEqual(result['state'], 'already_granted')
        self.assertFalse(result['additional_checkout_or_pay'])
        posts = [call for call in calls if isinstance(call, tuple)]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0], ('http://127.0.0.1:3310/api/target/reconcile', {'data': {'order_id': 'ord-fixture'}, 'headers': {'Origin': 'http://127.0.0.1:3310'}, 'max_redirects': 0, 'timeout': 5000}))

    async def test_changed_browser_order_blocks_before_post(self):
        result, calls = await self.exercise(changed_order=True)
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    async def test_changed_payment_response_is_rejected_without_retry(self):
        result, calls = await self.exercise(changed_response=True)
        self.assertIsNone(result)
        self.assertEqual(len([call for call in calls if isinstance(call, tuple)]), 1)
        self.assertIn('disposed', calls)
