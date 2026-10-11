// External provider fixtures only. These tests deliberately fail on the disclosed
// fault source and must pass without editing this file or any provider/auth code.
import assert from 'node:assert/strict';
import { randomBytes, createHmac } from 'node:crypto';
import { test } from 'node:test';
import { createTarget } from '../lib/target';
import type { Provider } from '../lib/provider';

class FixtureProvider implements Provider {
  readonly source='external_provider_fixture';
  email='';
  checkout:unknown={id:'cks_repair_fixture',payment_id:null,payment_status:null};
  payment:unknown;
  async createCustomer(input:{email:string;name:string}) {this.email=input.email;return {customer_id:'cus_repair_fixture'};}
  async createCheckout() {return {session_id:'cks_repair_fixture',checkout_url:'https://test.checkout.dodopayments.com/session/cks_repair_fixture'};}
  async getCheckout() {return this.checkout;}
  async getPayment() {return this.payment;}
}
async function scenario() {
  const control=randomBytes(32).toString('hex');
  const webhook=`whsec_${randomBytes(32).toString('base64')}`;
  const provider=new FixtureProvider();
  const app=createTarget({databasePath:':memory:',provider,env:{
    TARGET_ENV:'local',TARGET_SANDBOX:'true',TARGET_MODE:'broken',TARGET_CONTROL_TOKEN:control,
    DODO_TEST_WRITE_KEY:randomBytes(32).toString('hex'),DODO_TEST_READ_KEY:randomBytes(32).toString('hex'),
    DODO_TEST_WEBHOOK_KEY:webhook,DODO_TEST_KEYS_CONFIRMED:'true',DODO_READ_KEY_READ_ONLY:'true',
    DODO_TEST_PRODUCT_ID:'pdt_repair_fixture',DODO_EXPECTED_AMOUNT_MINOR:'100',DODO_EXPECTED_CURRENCY:'USD',
  }});
  function call(path:string,body?:unknown,cookie='') {
    return app.handle(new Request(`http://127.0.0.1:3300${path}`,{method:body===undefined?'GET':'POST',
      headers:{Authorization:`Bearer ${control}`,Origin:'http://127.0.0.1:3300','Content-Type':'application/json',Cookie:cookie},
      body:body===undefined?undefined:JSON.stringify(body)}));
  }
  const manifest=await (await call('/api/target/manifest')).json();
  const fixture=await (await call('/api/target/fixtures',{run_id:'repair_diagnostic',persona_id:'saved_persona',target_revision:manifest.target_revision})).json();
  const login=await call('/api/target/login',{login_token:fixture.login_token});
  const cookie=(login.headers.get('set-cookie')||'').split(';')[0];
  const checkout=await (await call('/api/target/checkouts',{},cookie)).json();
  function settle() {
    provider.checkout={id:checkout.checkout_session_id,payment_id:'pay_repair_fixture',payment_status:'succeeded'};
    provider.payment={business_id:'biz_external_fixture',payment_provider:'dodo',payment_id:'pay_repair_fixture',status:'succeeded',
      checkout_session_id:checkout.checkout_session_id,customer:{customer_id:'cus_repair_fixture',email:provider.email},
      metadata:{order_id:checkout.order_id,run_id:fixture.run_id,persona_id:fixture.persona_id,account_id:fixture.account_id,
        fixture_id:fixture.fixture_id,target_revision:fixture.target_revision,product_id:'pdt_repair_fixture',plan:'Special Downloads'},
      product_cart:[{product_id:'pdt_repair_fixture',quantity:1}],total_amount:100,currency:'USD'};
  }
  const oracle=()=>call(`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`);
  function signedEvent() {
    const raw=JSON.stringify({type:'payment.succeeded',business_id:'biz_external_fixture',timestamp:new Date().toISOString(),data:provider.payment});
    const eventId='evt_repair_fixture',timestamp=String(Math.floor(Date.now()/1000));
    const signature=createHmac('sha256',Buffer.from(webhook.slice(6),'base64')).update(`${eventId}.${timestamp}.${raw}`).digest('base64');
    return ()=>app.handle(new Request('http://127.0.0.1:3300/api/webhooks/payment',{method:'POST',body:raw,
      headers:{'Content-Type':'application/json','webhook-id':eventId,'webhook-timestamp':timestamp,'webhook-signature':`v1,${signature}`}}));
  }
  return {app,call,cookie,checkout,settle,oracle,signedEvent,manifest};
}
test('repair regression: settled original broken-config purchase grants once and enables protected download',async()=>{
  const s=await scenario();
  try {
    s.settle();
    const receipt=await (await s.call(`/api/target/receipts?order_id=${s.checkout.order_id}`)).json();
    assert.equal(receipt.state,'succeeded');assert.equal(receipt.source,'external_provider_fixture');
    assert.equal((await (await s.oracle()).json()).has_access,false);
    assert.equal((await s.call('/api/target/download?artwork=0',undefined,s.cookie)).status,403);
    const first=await (await s.call('/api/target/reconcile',{order_id:s.checkout.order_id},s.cookie)).json();
    assert.equal(first.state,'granted');assert.equal(first.grant_applied,true);
    const second=await (await s.call('/api/target/reconcile',{order_id:s.checkout.order_id},s.cookie)).json();
    assert.equal(second.state,'already_granted');assert.equal(second.evidence_id,first.evidence_id);
    const oracle=await (await s.oracle()).json();assert.equal(oracle.has_access,true);assert.equal(oracle.grant_count,1);
    assert.equal((await s.call('/api/target/download?artwork=0',undefined,s.cookie)).status,200);
    assert.equal((await s.call('/api/target/manifest').then(response=>response.json())).mode,'broken');
  } finally {s.app.close();}
});
test('repair regression: independently verified signed settlement uses the same repaired grant decision',async()=>{
  const s=await scenario();
  try {
    s.settle();const deliver=s.signedEvent();
    assert.equal((await deliver()).status,200);assert.equal((await deliver()).status,200);
    const oracle=await (await s.oracle()).json();assert.equal(oracle.has_access,true);assert.equal(oracle.grant_count,1);
    assert.equal((await s.call('/api/target/download?artwork=0',undefined,s.cookie)).status,200);
  } finally {s.app.close();}
});
test('repair guard: pending payment and return claims still cannot grant under the original broken configuration',async()=>{
  const s=await scenario();
  try {
    const returned=await (await s.call('/api/target/return?status=succeeded&payment_id=pay_repair_fixture',undefined,s.cookie)).json();
    assert.equal(returned.has_access,false);
    const response=await s.call('/api/target/reconcile',{order_id:s.checkout.order_id},s.cookie);
    assert.equal(response.status,409);assert.equal((await response.json()).state,'pending');
    assert.equal((await (await s.oracle()).json()).grant_count,0);
    assert.equal((await s.call('/api/target/download?artwork=0',undefined,s.cookie)).status,403);
  } finally {s.app.close();}
});
