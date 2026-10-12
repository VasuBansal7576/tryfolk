"""Fresh native-control fixtures only; no actual SDK/payment claim."""
from tryfolk.checkout_discovery import discover_checkout_candidates
from tryfolk.browser_policy import BrowserPolicy
import pytest


@pytest.mark.parametrize('kind,path,tag,control_id', [
    ('confirm','/special-downloads','BUTTON','confirm-art-pack'),
    ('art_pack','/special-downloads','A',''),
    ('download','/api/target/download','A',''),
])
async def test_native_bulk_discovery_includes_only_bound_owned_post_checkout(kind,path,tag,control_id):
    origin='http://127.0.0.1:3300'
    async def read(selector):
        return {'present':True,'visible':True,'disabled':False,'tag':tag,'id':control_id,
                'destination':origin+path,'label':'Owned action'}
    args=({'xpath_map':{'1':'/html[1]/body[1]/'+tag.lower()+'[1]'}},read,BrowserPolicy(origin,'dodo'),origin+'/checkout/return')
    result=await discover_checkout_candidates(*args,filled=set(),completed_cards={},clicked=set(),phase='owned_purchase',include_owned_post_payment=True)
    assert [a['_tryfolk']['kind'] for _,a in result.candidates]==[kind]
    repeated=await discover_checkout_candidates(*args,filled=set(),completed_cards={},clicked={kind},phase='owned_purchase',include_owned_post_payment=True)
    assert repeated.candidates==[]


async def test_native_bulk_unknown_button_cannot_become_purchase_intent():
    origin='http://127.0.0.1:3300'
    async def read(selector):
        return {'present':True,'visible':True,'tag':'BUTTON','id':'destroy','label':'Delete account','destination':origin+'/settings'}
    result=await discover_checkout_candidates({'xpath_map':{'1':'/html[1]/body[1]/button[1]'}},read,BrowserPolicy(origin,'dodo'),origin+'/',filled=set(),completed_cards={},clicked=set(),phase='owned_purchase',include_owned_post_payment=True)
    assert result.candidates==[]
