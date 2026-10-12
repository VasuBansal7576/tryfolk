"""Invented DOM/SDK responses; no provider or browser success is measured."""
import pytest
from tryfolk.browser_policy import BrowserPolicy
from tryfolk.browser_observation import observe_fresh
from tryfolk.browser_settlement import DOCUMENT_STATE
from tryfolk.checkout_discovery import discover_checkout_candidates

URL='https://test.checkout.dodopayments.com/session/invented'
OUTER='xpath=/html[1]/body[1]/button[1]'
INNER='xpath=/html[1]/body[1]/button[2]'

def control(label,hit):
 return {'present':True,'visible':True,'disabled':False,'tag':'BUTTON','label':label,
         'destination':URL,'center_hit_self':hit}

@pytest.mark.parametrize('hit',[False,None])
def test_visible_manual_control_requires_actual_current_hit_evidence(hit):
 p=BrowserPolicy('http://127.0.0.1:3301','dodo')
 with pytest.raises(ValueError,match='hittable'):
  p.bind({'method':'click','selector':OUTER},control('Enter address manually',hit),URL)

async def test_snapshot_discovery_rejects_occluded_outer_and_offers_inner_only():
 controls={OUTER:control('Enter address manually',False),INNER:control('Enter manually',True)}
 async def read(selector):return controls[selector]
 d=await discover_checkout_candidates({'xpath_map':{'0':OUTER[6:],'1':INNER[6:]}},read,
  BrowserPolicy('http://127.0.0.1:3301','dodo'),URL,filled={'country'},completed_cards={},
  clicked={'login','purchase'},phase='primary_billing')
 assert [(a['selector'],a['_tryfolk']['manual_variant']) for _,a in d.candidates]==[(INNER,'enter_manually')]
 assert d.rejection_counts['manual_control_not_hittable']==1

async def test_fresh_observation_exposes_hittable_sdk_snapshot_footer_when_inference_only_lists_outer():
 from tryfolk.checkout_discovery import include_snapshot_manual_choices
 p=BrowserPolicy('http://127.0.0.1:3301','dodo')
 doc={'url':URL,'generation':1,'ready_state':'complete','body_present':True,'control_signature':'stable'}
 controls={OUTER:control('Enter address manually',False),INNER:control('Enter manually',True)}
 async def read(selector):return controls[selector]
 async def infer():
  return await include_snapshot_manual_choices([{'method':'click','selector':OUTER}],
    {'xpath_map':{'0':OUTER[6:],'1':INNER[6:]}},read,p,URL)
 class Page:
  async def evaluate(self,expression):return dict(doc) if expression==DOCUMENT_STATE else 'invented public label'
 r=await observe_fresh(Page(),p,infer,read)
 assert [a['selector'] for a in r['actions']]==[INNER]
 assert r['actions'][0]['_tryfolk']['manual_variant']=='enter_manually'

async def test_hittable_outer_only_remains_single_primary_choice():
 p=BrowserPolicy('http://127.0.0.1:3301','dodo')
 a=p.bind({'method':'click','selector':OUTER},control('Enter address manually',True),URL)
 assert a['_tryfolk']['diagnostic_manual'] is True
 assert a['_tryfolk']['manual_variant']=='enter_address_manually'

async def test_unique_hittable_popup_footer_supersedes_outer_and_duplicate_inference():
 from tryfolk.checkout_discovery import include_snapshot_manual_choices
 p=BrowserPolicy('http://127.0.0.1:3301','dodo')
 controls={OUTER:control('Enter address manually',True),
           INNER:control('Enter manually',True)|{'popup_ancestor':True}}
 async def read(selector):return controls[selector]
 actions=await include_snapshot_manual_choices(
   [{'method':'click','selector':OUTER},{'method':'click','selector':INNER}],
   {'xpath_map':{'0':OUTER[6:],'1':INNER[6:]}},read,p,URL)
 assert [a['selector'] for a in actions]==[INNER]

async def test_snapshot_supplement_never_reads_or_exposes_foreign_frame_or_invented_selector():
 from tryfolk.checkout_discovery import include_snapshot_manual_choices
 async def forbidden(selector):raise AssertionError('Foreign/nonbutton snapshot path must not be read')
 actions=await include_snapshot_manual_choices([],
   {'xpath_map':{'0':'/html[1]/body[1]/iframe[1]/html[1]/body[1]/button[1]',
                 '1':'/html[1]/body[1]/input[1]'}},forbidden,
   BrowserPolicy('http://127.0.0.1:3301','dodo'),URL)
 assert actions==[]
