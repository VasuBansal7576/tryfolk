"""Replay policy tests use invented external observations, never measured checkout evidence."""

import pytest
from contextlib import nullcontext
from types import SimpleNamespace
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.dodo_replay import rebind_action, failed_persona, replay_observation_contract
from tryfolk.scenarios import digest


def test_replay_rebinds_recorded_intent_to_fresh_observed_identity_and_never_old_selector():
    policy = BrowserPolicy("http://127.0.0.1:3310", "dodo")
    url = "https://test.checkout.dodopayments.com/new-checkout"
    old = {
        "method": "fill",
        "selector": "#old-card",
        "arguments": ["old"],
        "_tryfolk": {"url": "https://test.checkout.dodopayments.com/old", "field": "card_number"},
    }
    fresh = policy.bind(
        {"method": "fill", "selector": "#fresh-card"}, {"tag": "INPUT", "autocomplete": "cc-number"}, url
    )
    assert rebind_action(old, [fresh], policy, url) == fresh
    with pytest.raises(ValueError):
        rebind_action(old, [], policy, url)
    with pytest.raises(ValueError):
        rebind_action(old, [fresh, {**fresh, "selector": "#other"}], policy, url)
    with pytest.raises(ValueError):
        rebind_action(old, [fresh], policy, "https://test.checkout.dodopayments.com/stale")


def test_replay_matches_semantic_clicks_with_fixed_description_fallback_and_actual_failed_persona():
    policy = BrowserPolicy("http://127.0.0.1:3310", "dodo")
    old = {
        "method": "click",
        "selector": "#old",
        "description": "Confirm purchase",
        "_tryfolk": {"url": "http://127.0.0.1:3301/checkout/return", "kind": "confirm"},
    }
    fresh = policy.bind(
        {"method": "click", "selector": "#new", "description": "Confirm purchase"},
        {"id": "confirm-art-pack"},
        policy.origin + "/checkout/return",
    )
    assert rebind_action(old, [fresh], policy, fresh["_tryfolk"]["url"]) == fresh
    population = [
        {"id": "first", "conditions": {"device": "desktop"}},
        {"id": "failed", "conditions": {"device": "desktop", "locale": "en-US"}},
    ]
    before = {
        "outcomes": {"failed": {"outcome": "failed", "classification": "paid_no_access", "actions": [old]}}
    }
    persona, result = failed_persona(before, population)
    assert persona == population[1] and result["actions"] == [old]
    with pytest.raises(ValueError):
        failed_persona(before, population[:1])


def test_native_known_replay_preserves_saved_plan_capacity_and_rejects_changed_customer():
    persona = {'id': 'original', 'profile': {'patience': 'moderate'},
               'bulk_scenario_snapshot': {'goal': 'purchase once'}}
    persona['purchase_plan'] = {
        'persona_id': persona['id'], 'profile_sha256': digest(persona['profile']),
        'scenario_sha256': digest(persona['bulk_scenario_snapshot']),
        'source': 'genuine_codex_batch_intent', 'purchase': True,
        'rationale': 'Replay fixture, not live inference', 'max_steps': 32,
        'unexpected_state': 'reconsider',
    }
    assert replay_observation_contract(persona, {'actions': [None] * 25}) == 'observe_native'
    with pytest.raises(ValueError, match='original saved'):
        replay_observation_contract(persona, {'actions': [None] * 33})
    with pytest.raises(ValueError, match='profile binding changed'):
        replay_observation_contract({**persona, 'profile': {'patience': 'substituted'}}, {'actions': []})
    assert replay_observation_contract({'id': 'legacy'}, {'actions': [None] * 24}) == 'observe'
    with pytest.raises(ValueError, match='original saved'):
        replay_observation_contract({'id': 'legacy'}, {'actions': [None] * 25})


async def test_duplicate_diagnostic_occurs_before_artpack_when_final_page_has_no_confirm(monkeypatch):
    """Lifecycle fixtures exercise the real replay loop; no payment evidence claim."""
    from tryfolk.dodo_replay import replay
    origin = 'http://127.0.0.1:3310'
    policy = BrowserPolicy(origin, 'dodo')
    confirm = policy.bind({'method': 'click', 'selector': '#confirm', 'description': 'Confirm purchase'},
                          {'id': 'confirm-art-pack'}, origin + '/checkout/return')
    art = policy.bind({'method': 'click', 'selector': '#art', 'description': 'View art pack'},
                      {'tag': 'A', 'destination': origin + '/special-downloads'}, origin + '/checkout/return')
    footer_art = policy.bind({'method': 'click', 'selector': '#footer-art', 'description': 'View art pack'},
                             {'tag': 'A', 'destination': origin + '/special-downloads'}, origin + '/checkout/return')
    download = policy.bind({'method': 'click', 'selector': '#download', 'description': 'Download artwork'},
                           {'tag': 'A', 'destination': origin + '/api/target/download?artwork=0'}, origin + '/special-downloads')
    persona = {'id': 'saved', 'conditions': {'history': 'new'}}
    before = {'outcomes': {'saved': {'outcome': 'failed', 'actions': [confirm, art]}}}
    fixture = {key: key + '-fixture' for key in ('run_id', 'persona_id', 'account_id', 'fixture_id', 'target_revision')}
    fixture['login_url'] = origin + '/target/login#token=fixture'
    log = []
    class Browser:
        def __init__(self, *args, **kwargs):
            self.stage = 0
        async def request(self, command):
            log.append(command)
            if command['op'].startswith('observe'):
                return {'url': origin + ('/checkout/return' if self.stage < 2 else '/special-downloads'),
                        'actions': [[confirm], [footer_art, art], [download]][min(self.stage, 2)]}
            if command['op'] == 'act':
                self.stage += 1
                return {'capability': {'available': command['action'] == download}}
            if command['op'] == 'duplicate_reconcile':
                assert self.stage == 1  # Confirm accepted, still before art navigation.
                return {'state': 'already_granted', 'grant_applied': False, 'same_account_order': True}
            return {}
        async def close(self):
            pass
    class Runtime:
        service = SimpleNamespace(store=SimpleNamespace(transaction=lambda: nullcontext(None), load=lambda *args: before))
        def cancelled(self, run_id):
            return False
        async def provision(self, *args):
            return fixture
        async def check_revision(self, run):
            pass
        async def verification(self, run, fixture, capability):
            return {'outcome': 'completed', 'provider': 'dodo', 'payment_verified': True, 'entitlement_verified': True,
                    'payment': {'id': 'payment-fixture', 'checkout_id': 'checkout-fixture'},
                    'receipt': {'ownership': {'order_id': 'order-fixture'}}, 'app_entitlement': {'grant_count': 1}}
    async def unpaid(*args):
        return {'case': 'unpaid_access_denied', 'passed': True}
    events = []
    async def emit(kind, data, *args):
        events.append((kind, data))
    monkeypatch.setattr('tryfolk.dodo_replay.SDKProcess', Browser)
    monkeypatch.setattr('tryfolk.dodo_replay.unpaid_control', unpaid)
    await replay(Runtime(), {'id': 'after', 'parent_run_id': 'before', 'preview_url': origin, 'revision': 'repair'}, [persona], emit)
    assert len([item for item in log if item['op'] == 'duplicate_reconcile']) == 1
    assert [item['action']['_tryfolk']['kind'] for item in log if item['op'] == 'act'] == ['confirm', 'art_pack', 'download']
    checks = [data for kind, data in events if kind == 'held_out_checks'][-1]['cases']
    assert checks[0]['passed'] is True
    assert [kind for kind, _ in events].count('held_out_checks') == 2


def test_only_equivalent_fresh_owned_return_art_links_can_resolve_ambiguity():
    origin = 'http://127.0.0.1:3310'
    policy = BrowserPolicy(origin, 'dodo')
    url = origin + '/checkout/return'
    def link(selector, destination=origin + '/special-downloads', current=url):
        return policy.bind({'method': 'click', 'selector': selector},
                           {'tag': 'A', 'destination': destination}, current)
    recorded = link('#old-header')
    fresh = [link('#z-footer'), link('#a-header')]
    assert rebind_action(recorded, fresh, policy, url) is fresh[1]
    assert rebind_action(fresh[0], fresh, policy, url) is fresh[0]
    # A different owned destination is not an equivalent art-pack candidate.
    different = link('#orders', origin + '/orders')
    assert different['_tryfolk']['kind'] != 'art_pack'
    with pytest.raises(ValueError):
        rebind_action(recorded, [different, different], policy, url)
    # Outside the exact owned return, even two nominal art-pack links are ambiguous.
    other_url = origin + '/special-downloads'
    with pytest.raises(ValueError):
        rebind_action(recorded, [link('#a', current=other_url), link('#b', current=other_url)], policy, other_url)
    with pytest.raises(ValueError):
        rebind_action(recorded, [fresh[0], {**fresh[1], '_tryfolk': {**fresh[1]['_tryfolk'], 'url': origin + '/other'}}], policy, url)
    confirm = policy.bind({'method': 'click', 'selector': '#confirm'}, {'id': 'confirm-art-pack'}, url)
    with pytest.raises(ValueError):
        rebind_action(confirm, [confirm, {**confirm, 'selector': '#other-confirm'}], policy, url)


@pytest.mark.parametrize('attempt,expected', [(1, 'saved-known-replay'), (2, 'saved-known-replay-attempt-2'), (3, 'saved-known-replay-attempt-3')])
def test_diagnostic_attempt_preserves_persona_but_provisions_distinct_account(attempt, expected):
    from tryfolk.dodo_replay import diagnostic_persona_id
    assert diagnostic_persona_id('saved', {'execution_attempt': attempt}) == expected
    for invalid in (0, -1, True, '2', None):
        with pytest.raises(ValueError):
            diagnostic_persona_id('saved', {'execution_attempt': invalid})


async def test_missing_art_control_after_confirm_gets_only_two_passive_fresh_rereads():
    from tryfolk.dodo_replay import observed_replay_action, MissingReplayControl
    origin = 'http://127.0.0.1:3310'
    url = origin + '/checkout/return'
    policy = BrowserPolicy(origin, 'dodo')
    fresh = policy.bind({'method': 'click', 'selector': '#fresh-art'},
                        {'tag': 'A', 'destination': origin + '/special-downloads'}, url)
    log = []
    class Browser:
        async def request(self, command):
            log.append(command)
            return {'url': url, 'actions': [] if len(log) < 3 else [fresh]}
    assert await observed_replay_action(Browser(), fresh, policy, 'observe_native', after_confirm=True) is fresh
    assert log == [{'op': 'observe_native'}] * 3  # No act, payment or navigation.
    log.clear()
    with pytest.raises(MissingReplayControl):
        await observed_replay_action(Browser(), fresh, policy, 'observe_native')
    assert len(log) == 1


async def test_missing_control_rereads_cannot_extend_original_observation_deadline():
    import asyncio
    from tryfolk.dodo_replay import observed_replay_action
    policy = BrowserPolicy('http://127.0.0.1:3310', 'dodo')
    log = []
    class Browser:
        async def request(self, command):
            log.append(command)
            await asyncio.sleep(0.02)
            return {'url': policy.origin + '/checkout/return', 'actions': []}
    recorded = {'method': 'click', '_tryfolk': {'kind': 'art_pack'}}
    with pytest.raises(TimeoutError):
        await observed_replay_action(Browser(), recorded, policy, 'observe_native', after_confirm=True, timeout=0.01)
    assert log == [{'op': 'observe_native'}]


@pytest.mark.parametrize('change', [None, {'executed': True}, {'same_generation': False}, {'field': 'card_number'}])
async def test_replay_reobserves_only_explicit_unexecuted_same_document_field_drift(change):
    """Invented SDK error exercises the real shared policy, not payment evidence."""
    from tryfolk.dodo_replay import execute_replay_action
    policy = BrowserPolicy('http://127.0.0.1:3310', 'dodo')
    url = 'https://test.checkout.dodopayments.com/session-fixture'
    fields = [policy.bind({'method': 'fill', 'selector': selector},
                         {'tag': 'INPUT', 'autocomplete': 'address-level2', 'label': 'City'}, url)
              for selector in ('#old-city', '#fresh-city')]
    log, executed = [], []
    class Browser:
        async def request(self, command):
            log.append(command['op'])
            if command['op'] == 'observe_native':
                return {'url': url, 'actions': [fields[0 if len(log) == 1 else 1]],
                        'document': {'url': url, 'generation': 1}}
            if command['action'] == fields[0]:
                error = RuntimeError('invented nonexecuted city drift')
                error.sdk_code = 'nonfinancial_control_stale'
                error.sdk_data = {'same_url': True, 'same_generation': True, 'same_control_signature': False,
                                  'nonfinancial': True, 'executed': False, 'field': 'city'}
                error.sdk_data.update(change or {})
                raise error
            executed.append(command['action'])
            return {'success': True}
    if change:
        with pytest.raises(RuntimeError):
            await execute_replay_action(Browser(), fields[0], policy, 'observe_native')
        assert log == ['observe_native', 'act'] and executed == []
    else:
        action, result = await execute_replay_action(Browser(), fields[0], policy, 'observe_native')
        assert action is fields[1] and result == {'success': True}
        assert log == ['observe_native', 'act', 'observe_native', 'act'] and executed == [fields[1]]


async def test_replay_action_cannot_discard_more_than_existing_three_stale_fields():
    from tryfolk.dodo_replay import execute_replay_action
    policy = BrowserPolicy('http://127.0.0.1:3310', 'dodo')
    url = 'https://test.checkout.dodopayments.com/session-fixture'
    action = policy.bind({'method': 'fill', 'selector': '#city'},
                         {'tag': 'INPUT', 'autocomplete': 'address-level2', 'label': 'City'}, url)
    log = []
    class Browser:
        async def request(self, command):
            log.append(command['op'])
            if command['op'] == 'observe_native':
                return {'url': url, 'actions': [action]}
            error = RuntimeError('invented nonexecuted city drift')
            error.sdk_code = 'nonfinancial_control_stale'
            error.sdk_data = {'same_url': True, 'same_generation': True, 'same_control_signature': False,
                              'nonfinancial': True, 'executed': False, 'field': 'city'}
            raise error
    with pytest.raises(RuntimeError):
        await execute_replay_action(Browser(), action, policy, 'observe_native')
    assert log == ['observe_native', 'act'] * 4
