"""Explicit capture-only diagnostic boundary; this never grants payment scope."""
import json
from urllib.parse import urlsplit

from .browser_policy import DODO_CHECKOUT
from .native_card_observation import return_route_facts
from .secure_frame import BROWSER_API


class CaptureOnlyGuard:
    def __init__(self, base_route, base_redirect, inspect_body, *, owned_origin,
                 payment_id, checkout_id, parent_url):
        self.base_route, self.base_redirect, self.inspect_body = base_route, base_redirect, inspect_body
        self.owned_origin, self.payment_id, self.checkout_id, self.parent_url = owned_origin, payment_id, checkout_id, parent_url
        self.events = []
        self.parent_write_guard_active = False

    def begin_native_pay_capture(self):
        if self.parent_write_guard_active:
            raise ValueError("The capture-only Pay phase cannot repeat.")
        self.parent_write_guard_active = True

    def path_template(self, url):
        return return_route_facts(url, payment_id=self.payment_id, checkout_id=self.checkout_id,
                                  owned_origin=self.owned_origin, checkout_url=self.parent_url).get('resolved_path_skeleton', ':invalid_path')

    async def route(self, route):
        request = route.request
        parsed = urlsplit(request.url)
        origin = f'{parsed.scheme}://{parsed.netloc}'
        if origin == BROWSER_API and self.payment_id and parsed.path in (
            f'/payments/{self.payment_id}/confirm', f'/payments/{self.payment_id}'
        ):
            operation = 'confirm' if parsed.path.endswith('/confirm') else 'poll'
            event = {'decision': 'deny_capture_only_payment', 'operation': operation, 'method': request.method,
                     'current_payment_path_bound': parsed.path in (f'/payments/{self.payment_id}/confirm', f'/payments/{self.payment_id}'),
                     'request_admitted': False}
            try:
                if operation == 'confirm' and request.method == 'POST':
                    event['body_diagnostics'] = await self.inspect_body(request)
            except Exception as error:
                event['diagnostic_error_type'] = type(error).__name__
            finally:
                self.events.append(event)
                await route.abort('blockedbyclient')
            return
        if self.parent_write_guard_active and origin == DODO_CHECKOUT and request.method not in ('GET', 'HEAD', 'OPTIONS'):
            # Setup retains the existing base policy; Pay adds no write grant. Deny
            # every such request, including unfamiliar methods and paths.
            try:
                raw = request.post_data
                body = json.loads(raw) if isinstance(raw, str) and len(raw) <= 100_000 else None
            except (ValueError, TypeError):
                body = None
            self.events.append({'decision': 'deny_unknown_parent_write', 'method': request.method,
                                'path_template': self.path_template(request.url),
                                'body_field_names': sorted(body)[:50] if isinstance(body, dict) else []})
            await route.abort('blockedbyclient')
            return
        await self.base_route(route)

    async def redirect_response(self, session, event, **target):
        if event.get('responseStatusCode') in (301, 302, 303, 307, 308):
            self.events.append({'decision': 'deny_capture_only_redirect', 'status': event['responseStatusCode'],
                                'path_template': self.path_template(event['request']['url']), **target})
            await session.send('Fetch.failRequest', {'requestId': event['requestId'], 'errorReason': 'BlockedByClient'})
            return
        await self.base_redirect(session, event, **target)
