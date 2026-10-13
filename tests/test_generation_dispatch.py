"""Dispatcher fixtures only; real SDK inference is established by live commissioning."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tryfolk.runtime import Runtime
from tryfolk.scenarios import digest


@pytest.mark.parametrize('mutation', [None, 'profile', 'memory'])
async def test_concurrent_unlicensed_dodo_prepares_each_exact_generated_customer(tmp_path, monkeypatch, mutation):
    calls = []
    generated = [{
        'id': str(i), 'index': i, 'name': f'Dispatch fixture {i}', 'profile': {'patience': i},
        'conditions': {'history': 'new'}, 'initial_semantic_memory': [{'type': 'information', 'content': 'Original goal'}],
        'initial_snapshot': {'fixture': True},
    } for i in range(4)]

    class Generator:
        def __init__(self, *args, **kwargs):
            pass

        async def request(self, command, **kwargs):
            calls.append(command)
            if command['op'] == 'generate':
                return {'population': deepcopy(generated)}
            assert command['op'] == 'prepare_intents'
            assert command['population'] == generated
            result = deepcopy(command['population'])
            for person in result:
                person['bulk_scenario_snapshot'] = {'fixture_goal': 'purchase once'}
                person['purchase_plan'] = {
                    'persona_id': person['id'], 'profile_sha256': digest(person['profile']),
                    'scenario_sha256': digest(person['bulk_scenario_snapshot']),
                    'source': 'genuine_codex_batch_intent', 'purchase': True,
                    'rationale': 'Dispatcher fixture, not live inference', 'max_steps': 24,
                    'unexpected_state': 'reconsider',
                }
                person['initial_semantic_memory'].append({'type': 'information', 'content': 'Intention fixture'})
            if mutation == 'profile':
                result[0]['profile']['patience'] = 999
            elif mutation == 'memory':
                result[0]['initial_semantic_memory'][0]['content'] = 'Lost goal'
            return {'population': result}

        async def close(self):
            calls.append('closed')

    monkeypatch.setenv('TRYFOLK_EVIDENCE_ROOT', str(tmp_path))
    monkeypatch.delenv('NEATLOGS_API_KEY', raising=False)
    monkeypatch.delenv('TRYFOLK_NEATLOGS_ENV', raising=False)
    monkeypatch.setattr('tryfolk.runtime.SDKProcess', Generator)
    runtime = Runtime(SimpleNamespace(settings=SimpleNamespace(bulk_execution=True, browser_concurrency=4)))
    runtime.active_run_id = 'dispatch-fixture'
    if mutation:
        with pytest.raises(RuntimeError, match='must (preserve|retain)'):
            await runtime.generate({'provider': 'dodo', 'observed': {}}, 4)
    else:
        population = await runtime.generate({'provider': 'dodo', 'observed': {}}, 4)
        assert len(population) == 4
        assert [person['id'] for person in population] == [person['id'] for person in generated]
        assert all(person['purchase_plan']['max_steps'] == 24 for person in population)
    assert calls[0]['op'] == 'generate'
    assert calls[0]['count'] == 4
    assert calls[1]['op'] == 'prepare_intents'
    assert calls[-1] == 'closed'
    assert runtime.journey_concurrency == 4
