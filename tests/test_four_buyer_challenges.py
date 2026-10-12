"""Offline invented person/model fixtures; no real customer or payment outcomes."""
from copy import deepcopy
import json
import pytest
from test_bulk_personas import adapter, command, Person, inferred
from tryfolk.bulk_personas import validated_purchase_plan
from tryfolk.scenarios import (FOUR_BUYER_CHALLENGES, challenge_binding, digest,
                              freeze_population, validate_population)


def four_command():
    value = command(4)
    for index, assignment in enumerate(value['brief']['generation_plan']['assignments']):
        assignment['scenario_snapshot']['id'] = ('one-time-card-download' if index % 2 else 'paid-no-access-download-retest')
        assignment['scenario_sha256'] = digest(assignment['scenario_snapshot'])
        descriptor = deepcopy(FOUR_BUYER_CHALLENGES[index])
        assignment.update(buyer_challenge=descriptor, buyer_challenge_sha256=digest(descriptor))
    return value


def test_four_constraints_reach_each_model_and_actual_memory_before_snapshot():
    value = four_command()
    def model(messages, schema, **kwargs):
        requests = json.loads(messages[-1]['content'])['requests']
        assert [r['buyer_challenge'] for r in requests] == list(FOUR_BUYER_CHALLENGES)
        return inferred(messages, schema, **kwargs)
    population = adapter().generate_bulk(value, Person, model)['population']
    # Invented model intentionally declines two: challenge never forces purchase.
    assert [p['purchase_plan']['purchase'] for p in population] == [True, False, True, False]
    for p, descriptor in zip(population, FOUR_BUYER_CHALLENGES):
        assert p['buyer_challenge'] == descriptor
        assert p['purchase_plan']['buyer_challenge_sha256'] == digest(descriptor)
        assert descriptor['buyer_constraint'] in json.dumps(p['initial_semantic_memory'])
        assert descriptor['buyer_constraint'] in json.dumps(p['initial_snapshot']['memory'])
        validated_purchase_plan(p)
    plan = value['brief']['generation_plan']
    freeze_population(population, plan)
    before = deepcopy(population)
    after = deepcopy(population)
    validate_population(after, plan)
    assert after == before  # Same saved four; no regeneration/memory rewriting.
    after[1]['buyer_challenge']['buyer_constraint'] = 'silently changed'
    with pytest.raises(ValueError):
        validate_population(after, plan)


def test_rehashed_noncanonical_descriptor_rejected_before_model_and_plan_tamper_rejected():
    value=four_command()
    a=value['brief']['generation_plan']['assignments'][0]
    a['buyer_challenge']['buyer_constraint']='invented fraud outcome'
    a['buyer_challenge_sha256']=digest(a['buyer_challenge'])
    with pytest.raises(ValueError):
        adapter().generate_bulk(value,Person,lambda *_:pytest.fail('model called'))
    population=adapter().generate_bulk(four_command(),Person,inferred)['population']
    population[0]['purchase_plan']['buyer_challenge_sha256']='0'*64
    with pytest.raises(ValueError):
        validated_purchase_plan(population[0])


def test_historical_unchallenged_population_remains_valid_and_partial_binding_denied():
    population=adapter().generate_bulk(command(),Person,inferred)['population']
    assert all('buyer_challenge' not in p for p in population)
    assert all(validated_purchase_plan(p) for p in population)
    assert challenge_binding({}) is None
    with pytest.raises(ValueError):
        challenge_binding({'buyer_challenge':FOUR_BUYER_CHALLENGES[0]})


def test_supported_exact_four_selection_assigns_distinct_constraints_without_new_coverage():
    from pathlib import Path
    from types import SimpleNamespace
    from tryfolk.scenarios import select_plan, generation_constraints
    from tryfolk.scenario_evidence import CONDITIONS, FIXTURE
    catalog=json.loads((Path(__file__).parents[1]/'dodo-target/scenarios/payment-catalog.json').read_text())
    catalog['source_file_sha256']=digest(catalog)
    target=SimpleNamespace(scenario_conditions=CONDITIONS,scenario_fixture_ids=[FIXTURE],
                           scenario_capabilities={'target':[], 'provider':[]})
    for scenario in catalog['scenarios']:
        for k in ('target','provider'):
            target.scenario_capabilities[k] += scenario['required_capabilities'].get(k,[])
    bank={'version':'invented-v1','source':{'license':'CC-BY-4.0'},'seeds':[]}
    for i in range(4):
        bank['seeds'].append({'id':f'invented-{i}','profile':{'interests':['art']},
                              'supported_context':{k:[v] for k,v in CONDITIONS.items()}})
    plan=select_plan(catalog,bank,target,4)
    assert [a['buyer_challenge'] for a in plan['assignments']]==list(FOUR_BUYER_CHALLENGES)
    assert len({a['seed']['id'] for a in plan['assignments']})==4
    assert set(plan['selection_rationale']['selected_case_ids'])=={'one-time-card-download','paid-no-access-download-retest'}
    for i,a in enumerate(plan['assignments']):
        assert a['condition_fixture_snapshot']==CONDITIONS
        assert generation_constraints({'generation_plan':plan},i)['buyer_challenge']==FOUR_BUYER_CHALLENGES[i]
    assert all('buyer_challenge' not in a for a in select_plan(catalog,bank,target,2)['assignments'])
