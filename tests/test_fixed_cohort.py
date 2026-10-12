"""Public fixed-cohort boundary; local fixture requests are not provider evidence."""
from dataclasses import replace
from fastapi.testclient import TestClient
from tryfolk.api import create_app
from test_api import client_at, quote, start, auth


def fixed_client(client):
    settings = replace(client.app.state.service.settings, fixed_persona_count=4,
                       max_personas=4, browser_concurrency=4, bulk_execution=True)
    return TestClient(create_app(settings))


def test_fixed_four_is_advertised_and_other_counts_never_issue_quotes(tmp_path):
    client = fixed_client(client_at(tmp_path/'db'))
    ready = client.get('/readiness').json()
    assert ready['fixed_persona_count'] == ready['max_personas'] == 4
    for n in (1, 3, 5, 10, 100):
        denied = client.post('/quotes', json={'app_url':'http://127.0.0.1:3100','persona_count':n})
        assert denied.status_code == 422
        assert denied.json()['detail']['code'] == 'fixed_cohort_count'
    q = quote(client,4)
    assert q['fixed_persona_count'] == q['persona_count'] == 4
    assert q['ledger'] == {'estimated_consumed_microusd':0,'held_microusd':0}
    accepted = start(client,q).json()
    assert accepted['status']['persona_count'] == 4


def test_reloading_fixed_mode_rejects_old_other_count_quote_without_changing_history_or_holds(tmp_path):
    base = client_at(tmp_path/'db')
    old_quote = quote(base,3)
    old_run = start(base,quote(base,2),'historical').json()
    fixed = fixed_client(base)
    history = fixed.get('/runs/'+old_run['id'],headers=auth(old_run)).json()
    balance = quote(fixed,4)['ledger']
    denied = start(fixed,old_quote,'old-count')
    assert denied.status_code == 422
    assert denied.json()['detail']['code'] == 'fixed_cohort_count'
    assert quote(fixed,4)['ledger'] == balance
    assert fixed.get('/runs/'+old_run['id'],headers=auth(old_run)).json() == history
    assert start(fixed,old_run['status']['budget_contract'],'historical').json() == old_run


async def test_other_count_replay_is_rejected_but_completed_old_run_remains_readable(tmp_path):
    from tryfolk.worker import Worker
    from test_worker import FixtureIO
    base = client_at(tmp_path/'db')
    service = base.app.state.service
    settings = replace(service.settings,targets=(replace(service.settings.targets[0],repo_path=str(tmp_path)),))
    base = TestClient(create_app(settings))
    before = start(base,quote(base,2)).json()
    class RepairIO(FixtureIO):
        async def repair(self, run, emit):
            base.app.state.service.register_repair(run['repair']['id'],head_revision='fixture-fixed',
                preview_url='http://127.0.0.1:3102',checkpoint_id='offline-checkpoint',checkpoint_url=None,provider='local-fixture')
    worker = Worker(base.app.state.service,RepairIO())
    await worker.tick()
    finding = base.get('/runs/'+before['id']+'/findings',headers=auth(before)).json()['findings'][0]
    repair = base.post('/runs/'+before['id']+'/repairs',json={'finding_id':finding['id']},
                       headers={'Authorization':'Bearer test-owner'}).json()
    await worker.tick()
    fixed = fixed_client(base)
    history = fixed.get('/runs/'+before['id'],headers=auth(before)).json()
    balance = quote(fixed,4)['ledger']
    response = fixed.post('/runs/'+before['id']+'/replay',json={'repair_id':repair['id']},
                          headers={'Authorization':'Bearer test-owner','Idempotency-Key':'other-after'})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'fixed_cohort_count'
    assert fixed.get('/runs/'+before['id'],headers=auth(before)).json() == history
    assert quote(fixed,4)['ledger'] == balance


def test_fixed_configuration_cannot_silently_queue_the_four_buyers(tmp_path, monkeypatch):
    import pytest
    from tryfolk.config import Settings
    for wrong in ({'browser_concurrency':2},{'bulk_execution':False},{'max_personas':10},
                  {'fixed_persona_count':True},{'fixed_persona_count':5}):
        with pytest.raises(ValueError):
            Settings(**({'fixed_persona_count':4,'max_personas':4,'browser_concurrency':4,'bulk_execution':True}|wrong))
    monkeypatch.chdir(tmp_path)
    for key,value in {'TRYFOLK_FIXED_PERSONAS':'4','TRYFOLK_MAX_PERSONAS':'4',
                      'TRYFOLK_BROWSER_CONCURRENCY':'4','TRYFOLK_BULK_EXECUTION':'true'}.items():
        monkeypatch.setenv(key,value)
    assert Settings.from_env().fixed_persona_count == 4
    for bad in ('5','04','','true'):
        monkeypatch.setenv('TRYFOLK_FIXED_PERSONAS',bad)
        with pytest.raises(ValueError):Settings.from_env()
