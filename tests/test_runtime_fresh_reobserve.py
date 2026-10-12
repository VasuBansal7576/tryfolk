"""Real runtime decisions with invented SDK/provider I/O, never live payment evidence."""
import pytest
from types import SimpleNamespace
import tryfolk.runtime as module
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.runtime import Runtime, FreshActionRetry


def stale(data=None):
    error=RuntimeError('invented nonexecuted adapter control signature drift')
    error.sdk_code='nonfinancial_control_stale'
    error.sdk_data=data or {'same_url':True,'same_generation':True,'same_control_signature':False,
        'nonfinancial':True,'executed':False,'field':'address'}
    return error

@pytest.mark.parametrize('change',[{'same_url':False},{'same_generation':False},{'executed':True},
    {'executed':0},{'nonfinancial':False},{'field':'card_number'},{'extra':'foreign'}])
def test_only_closed_same_document_nonfinancial_unexecuted_signal_can_retry(change):
    error=stale();error.sdk_data.update(change)
    action={'method':'fill','_tryfolk':{'field':'address'}}
    assert not FreshActionRetry().allow(error,action)


def test_original_deadline_and_three_reobservations_are_bounded():
    action={'method':'fill','_tryfolk':{'field':'address'}}
    retry=FreshActionRetry(timeout=10)
    assert all(retry.allow(stale(),action) for _ in range(3))
    assert not retry.allow(stale(),action)
    assert not FreshActionRetry(timeout=0).allow(stale(),action)
    assert not FreshActionRetry().allow(RuntimeError('ambiguous SDK failure'),action)
    assert not FreshActionRetry().allow(stale(),{'method':'click','_tryfolk':{'kind':'pay'}})

@pytest.mark.parametrize('change',[None,{'same_url':False},{'same_generation':False},{'executed':True}])
async def test_real_journey_reobserves_reselects_and_executes_only_fresh_replacement(tmp_path,monkeypatch,change):
    monkeypatch.setenv('TRYFOLK_EVIDENCE_ROOT',str(tmp_path/'evidence'))
    monkeypatch.delenv('NEATLOGS_API_KEY',raising=False)
    policy=BrowserPolicy('http://127.0.0.1:3301','dodo')
    url='https://test.checkout.dodopayments.com/session-fixture'
    control={'present':True,'visible':True,'disabled':False,'tag':'INPUT','type':'text','label':'Street address','autocomplete':'address-line1','value':''}
    actions=[policy.bind({'selector':selector,'method':'fill','description':'Address'},control,url) for selector in ('#old','#replacement')]
    requests=[];executed=[];closed=[];events=[]
    class SDK:
        def __init__(self,name,*args,**kwargs):self.name=name;self.process=SimpleNamespace(returncode=None);self.observed=0
        async def request(self,data,timeout=180):
            op=data['op'];requests.append((self.name,op))
            if self.name=='tinytroupe':
                if op=='decide':
                    choices=data['observation']['actions']
                    return {'intent':{'type':'BROWSER' if choices else 'FINISH','target':choices[0]['id'] if choices else '','content':'invented typed intent'}}
                return {'memory':'invented SDK memory'}
            if op=='observe':
                choice=[actions[self.observed]] if self.observed<2 else []
                self.observed+=1
                return {'url':url,'text':'Current billing','actions':choice}
            if op=='act':
                if data['action']['selector']=='#old':
                    error=stale()
                    if change:error.sdk_data.update(change)
                    raise error
                executed.append(data['action'])
                return {'data':{'success':True}}
            return {}
        async def close(self):closed.append(self.name);self.process.returncode=0
    monkeypatch.setattr(module,'SDKProcess',SDK)
    runtime=Runtime(SimpleNamespace())
    async def provision(*args):return {'login_url':'http://127.0.0.1:3301/target/login','account_id':'fixture-account'}
    async def revision(*args):return {}
    async def verify(*args):return {'outcome':'pending','measured':False}
    async def emit(kind,data,persona):events.append((kind,data))
    runtime.provision=provision;runtime.check_revision=revision;runtime.verification=verify
    run={'id':'fixture-run','provider':'dodo','preview_url':'http://127.0.0.1:3301','revision':'fixture-rev'}
    persona={'id':'fixture-persona','conditions':{},'initial_snapshot':{},'profile':{}}
    if change:
        with pytest.raises(RuntimeError):await runtime.journey(run,persona,lambda:False,emit)
        assert executed==[] and requests.count(('stagehand','observe'))==1
    else:
        result=await runtime.journey(run,persona,lambda:False,emit)
        assert result['measured'] is False and len(executed)==1 and executed[0]['selector']=='#replacement'
        mutations=[data for kind,data in events if kind=='browser_action']
        assert len(mutations)==1 and mutations[0]['action']['selector']=='#replacement'
        assert requests.count(('stagehand','observe'))==3
    assert sorted(closed)==['stagehand','tinytroupe']
