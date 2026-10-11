import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createTarget } from '../lib/target';

test('missing sandbox configuration is an actionable setup gate', async () => {
  const app = createTarget({ env: {}, databasePath: ':memory:' });
  const result = await app.handle(new Request('http://127.0.0.1:3300/api/target/readiness'));
  const body = await result.json();
  assert.equal(body.ready, false);
  assert.ok(body.errors.some((item: {code: string}) => item.code === 'missing_write_key'));
  assert.ok(body.errors.every((item: {action: string}) => item.action.length > 0));
  app.close();
});

import { randomBytes } from 'node:crypto';
const control = randomBytes(32).toString('hex');
function sandboxEnv(): Record<string, string> {
  return {TARGET_ENV:'local', TARGET_SANDBOX:'true', TARGET_CONTROL_TOKEN:control,
    DODO_TEST_WRITE_KEY:randomBytes(32).toString('hex'), DODO_TEST_READ_KEY:randomBytes(32).toString('hex'),
    DODO_TEST_WEBHOOK_KEY:`whsec_${randomBytes(32).toString('base64')}`,
    DODO_TEST_KEYS_CONFIRMED:'true', DODO_READ_KEY_READ_ONLY:'true', DODO_TEST_PRODUCT_ID:'pdt_fixture_downloads',
    DODO_EXPECTED_AMOUNT_MINOR:'1200', DODO_EXPECTED_CURRENCY:'USD'};
}
function call(app: ReturnType<typeof createTarget>, path: string, body?: unknown, headers: Record<string,string> = {}) {
  return app.handle(new Request(`http://127.0.0.1:3300${path}`, {
    method:body === undefined ? 'GET':'POST',
    headers: {'Authorization':`Bearer ${control}`, 'Content-Type':'application/json', 'Origin':'http://127.0.0.1:3300', ...headers},
    body:body === undefined ? undefined : JSON.stringify(body),
  }));
}
async function revision(app: ReturnType<typeof createTarget>): Promise<string> {
  return (await (await call(app, '/api/target/manifest')).json()).target_revision;
}

test('test-only fixture creation is forbidden in production even with controller credentials', async () => {
  const app=createTarget({env:{...sandboxEnv(), TARGET_ENV:'production'}, databasePath:':memory:'});
  const response=await call(app, '/api/target/fixtures', {run_id:'r1',persona_id:'p1',target_revision:await revision(app)});
  assert.equal(response.status,403);
  assert.equal((await response.json()).error,'sandbox_forbidden');
  app.close();
});

test('fixture creates a fresh account and a single-use login without selecting existing identities', async () => {
  const app=createTarget({env:sandboxEnv(), databasePath:':memory:'});
  const body={run_id:'r1',persona_id:'p1',target_revision:await revision(app)};
  const fixture=await (await call(app,'/api/target/fixtures',body)).json();
  const other=await (await call(app,'/api/target/fixtures',body)).json();
  assert.ok(fixture.account_id);
  assert.notEqual(fixture.account_id,other.account_id);
  const takeover=await call(app,'/api/target/fixtures',{...body,account_id:fixture.account_id});
  assert.equal(takeover.status,400);
  const unauthorized=await call(app,'/api/target/fixtures',body,{'Authorization':'Bearer wrong'});
  assert.equal(unauthorized.status,401);
  const login=await call(app,'/api/target/login',{login_token:fixture.login_token});
  assert.equal(login.status,200);
  assert.match(login.headers.get('set-cookie') ?? '',/HttpOnly/);
  assert.equal((await call(app,'/api/target/login',{login_token:fixture.login_token})).status,401);
  app.close();
});

import type { Provider } from '../lib/provider';

test('authenticated browser parent read keeps payment link out of receipt evidence', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {checkout,event}=await settledFixture(app,provider);
  const link='https://test.checkout.dodopayments.com/Private1';
  provider.payments.set(event.data.payment_id,{...event.data,payment_link:link});
  const path=`/api/target/receipts?order_id=${checkout.order_id}`;
  const ordinary=await (await call(app,path)).json();
  assert.ok(!JSON.stringify(ordinary).includes(link));
  const privateRead=await (await call(app,`${path}&browser_parent=1`)).json();
  assert.equal(privateRead.private_payment_link,link);
  const evidence=await (await call(app,`/api/target/evidence?evidence_id=${privateRead.evidence_id}`)).json();
  assert.ok(!JSON.stringify(evidence).includes(link));
  assert.equal((await call(app,`${path}&browser_parent=1`,undefined,{'Authorization':'Bearer wrong'})).status,401);
  assert.equal((await call(app,`${path}&browser_parent=all`)).status,400);
  app.close();
});

class ExternalProviderFixture implements Provider {
  readonly source='external_provider_fixture';
  checkouts = new Map<string,unknown>();
  payments = new Map<string,unknown>();
  checkoutInputs: Parameters<Provider['createCheckout']>[0][]=[];
  checkoutOrigin='https://test.checkout.dodopayments.com';
  failReads=false;
  customerInputs: {email:string;name:string}[]=[];
  async createCustomer(input:{email:string;name:string}) { this.customerInputs.push(input); return {customer_id:`cus_${randomBytes(8).toString('hex')}`}; }
  async createCheckout(input: Parameters<Provider['createCheckout']>[0]) {
    this.checkoutInputs.push(input);
    const session_id=`cks_${randomBytes(8).toString('hex')}`;
    this.checkouts.set(session_id,{id:session_id,payment_id:null,payment_status:null});
    return {session_id,checkout_url:`${this.checkoutOrigin}/session/${session_id}`};
  }
  async getCheckout(id:string) { if(this.failReads)throw new Error('fixture timeout'); return this.checkouts.get(id); }
  async getPayment(id:string) { if(this.failReads)throw new Error('fixture timeout'); return this.payments.get(id); }
}
test('the exact authorized hosted test checkout origin is accepted and listed separately from the API', async () => {
  const provider=new ExternalProviderFixture();
  provider.checkoutOrigin='https://test.checkout.dodopayments.com';
  const app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {cookie}=await customer(app);
  const response=await call(app,'/api/target/checkouts',{}, {Cookie:cookie});
  assert.equal(response.status,201);
  assert.equal(new URL((await response.json()).checkout_url).origin,'https://test.checkout.dodopayments.com');
  const manifest=await (await call(app,'/api/target/manifest')).json();
  assert.deepEqual(manifest.checkout_origins,['https://test.checkout.dodopayments.com']);
  app.close();
});
async function customer(app:ReturnType<typeof createTarget>,run_id='r1',persona_id='p1') {
  const fixture=await (await call(app,'/api/target/fixtures',{run_id,persona_id,target_revision:await revision(app)})).json();
  const login=await call(app,'/api/target/login',{login_token:fixture.login_token});
  const cookie=(login.headers.get('set-cookie') || '').split(';')[0];
  return {fixture,cookie};
}

test('checkout creates a fresh server-bound order each attempt and exposes labelled pending receipts', async () => {
  const provider=new ExternalProviderFixture();
  const app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {fixture,cookie}=await customer(app);
  const first=await (await call(app,'/api/target/checkouts',{}, {'Cookie':cookie,'Origin':'http://127.0.0.1:3300'})).json();
  const second=await (await call(app,'/api/target/checkouts',{}, {'Cookie':cookie,'Origin':'http://127.0.0.1:3300'})).json();
  assert.ok(first.order_id);
  assert.notEqual(first.order_id,second.order_id);
  assert.notEqual(first.checkout_session_id,second.checkout_session_id);
  assert.equal(first.ownership.account_id,fixture.account_id);
  const receipt=await (await call(app,`/api/target/receipts?order_id=${first.order_id}`)).json();
  assert.equal(receipt.state,'pending');
  assert.equal(receipt.source,'external_provider_fixture');
  const entitlement=await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${first.order_id}`)).json();
  assert.equal(entitlement.has_access,false);
  const substitution=await call(app,'/api/target/checkouts',{product_id:'someone_else'}, {'Cookie':cookie,'Origin':'http://127.0.0.1:3300'});
  assert.equal(substitution.status,400);
  app.close();
});

import { createHmac } from 'node:crypto';
function signedWebhook(raw:string,secret:string,eventId=`evt_${randomBytes(10).toString('hex')}`) {
  const timestamp=String(Math.floor(Date.now()/1000));
  const key=Buffer.from(secret.replace(/^whsec_/,''),'base64');
  const signature=createHmac('sha256',key).update(`${eventId}.${timestamp}.${raw}`).digest('base64');
  return {'webhook-id':eventId,'webhook-timestamp':timestamp,'webhook-signature':`v1,${signature}`};
}
async function postWebhook(app:ReturnType<typeof createTarget>,raw:string,headers:Record<string,string>) {
  return app.handle(new Request('http://127.0.0.1:3300/api/webhooks/payment',{method:'POST',body:raw,headers:{'Content-Type':'application/json',...headers}}));
}

test('a bad raw webhook signature is rejected before any entitlement can be granted', async () => {
  const env=sandboxEnv(), provider=new ExternalProviderFixture();
  const app=createTarget({env,databasePath:':memory:',provider});
  const {fixture,cookie}=await customer(app);
  const order=await (await call(app,'/api/target/checkouts',{}, {'Cookie':cookie})).json();
  const raw=JSON.stringify({type:'payment.succeeded',data:{metadata:{order_id:order.order_id}}});
  const headers=signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY);
  const invalid=await postWebhook(app,`${raw} `,headers);
  assert.equal(invalid.status,401);
  const entitlement=await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${order.order_id}`)).json();
  assert.equal(entitlement.has_access,false);
  app.close();
});

async function settledFixture(app:ReturnType<typeof createTarget>,provider:ExternalProviderFixture) {
  const {fixture,cookie}=await customer(app);
  const checkout=await (await call(app,'/api/target/checkouts',{}, {'Cookie':cookie})).json();
  const stored=await (await call(app,`/api/target/orders?account_id=${fixture.account_id}`)).json();
  const order=stored.orders.find((item:{order_id:string})=>item.order_id === checkout.order_id);
  const payment_id=`pay_${randomBytes(8).toString('hex')}`;
  const payment={business_id:'biz_external_fixture',payment_provider:'dodo',payment_id,status:'succeeded',checkout_session_id:checkout.checkout_session_id,
    customer:{customer_id:order.customer_id,email:`placeholder@tryfolk.invalid`},
    metadata:{order_id:order.order_id,run_id:fixture.run_id,persona_id:fixture.persona_id,account_id:fixture.account_id,fixture_id:fixture.fixture_id,target_revision:fixture.target_revision,product_id:order.expected_product_id,plan:'Special Downloads'},
    product_cart:[{product_id:'pdt_fixture_downloads',quantity:1}],total_amount:1200,currency:'USD'};
  // Synthetic provider identity is taken from the real outgoing customer request.
  payment.customer.email=provider.customerInputs.at(-1)?.email || '';
  provider.checkouts.set(checkout.checkout_session_id,{id:checkout.checkout_session_id,payment_id,payment_status:'succeeded'});
  provider.payments.set(payment_id,payment);
  const event={type:'payment.succeeded',business_id:'biz_external_fixture',timestamp:new Date().toISOString(),data:payment};
  return {fixture,cookie,checkout,payment,event};
}

test('duplicate signed events grant a settled bound order once and oracle reads cannot grant', async () => {
  const env=sandboxEnv(),provider=new ExternalProviderFixture();
  const app=createTarget({env,databasePath:':memory:',provider});
  const {fixture,checkout,event}=await settledFixture(app,provider);
  const entitlementUrl=`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`;
  const before=await (await call(app,entitlementUrl)).json();
  assert.equal(before.has_access,false);
  const receipt=await (await call(app,`/api/target/receipts?order_id=${checkout.order_id}`)).json();
  assert.equal(receipt.state,'succeeded');
  assert.equal((await (await call(app,entitlementUrl)).json()).has_access,false);
  const raw=JSON.stringify(event),headers=signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY);
  assert.equal((await postWebhook(app,raw,headers)).status,200);
  assert.equal((await postWebhook(app,raw,headers)).status,200);
  assert.equal((await postWebhook(app,raw,signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY))).status,200);
  const after=await (await call(app,entitlementUrl)).json();
  assert.equal(after.has_access,true);
  assert.equal(after.grant_count,1);
  app.close();
});

test('run reset revokes only run-owned accounts/orders and preserves immutable receipts', async () => {
  const env=sandboxEnv(),provider=new ExternalProviderFixture();
  const app=createTarget({env,databasePath:':memory:',provider});
  const first=await settledFixture(app,provider);
  const second=await customer(app,'r2','p2');
  const receipt=await (await call(app,`/api/target/receipts?order_id=${first.checkout.order_id}`)).json();
  const reset=await call(app,'/api/target/reset',{run_id:'r1',target_revision:await revision(app)});
  assert.equal(reset.status,200);
  assert.equal((await call(app,'/api/target/me',undefined,{'Cookie':first.cookie})).status,401);
  assert.equal((await call(app,'/api/target/me',undefined,{'Cookie':second.cookie})).status,200);
  assert.equal((await call(app,`/api/target/evidence?evidence_id=${receipt.evidence_id}`)).status,200);
  const eventRaw=JSON.stringify(first.event);
  assert.equal((await postWebhook(app,eventRaw,signedWebhook(eventRaw,env.DODO_TEST_WEBHOOK_KEY))).status,409);
  app.close();
});

test('a return-page status or payment ID cannot fake purchase and download stays protected', async () => {
  const env=sandboxEnv(),provider=new ExternalProviderFixture();
  const app=createTarget({env,databasePath:':memory:',provider});
  const {cookie,checkout,event}=await settledFixture(app,provider);
  const returned=await call(app,`/api/target/return?status=succeeded&payment_id=${event.data.payment_id}`,undefined,{'Cookie':cookie});
  assert.equal(returned.status,200);
  assert.equal((await returned.json()).has_access,false);
  assert.equal((await call(app,'/api/target/download?artwork=0',undefined,{'Cookie':cookie})).status,403);
  const raw=JSON.stringify(event);
  assert.equal((await postWebhook(app,raw,signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY))).status,200);
  const download=await call(app,'/api/target/download?artwork=0',undefined,{'Cookie':cookie});
  assert.equal(download.status,200);
  assert.match(download.headers.get('content-disposition') || '',/attachment/);
  assert.equal((await call(app,`/api/target/receipts?order_id=${checkout.order_id}`)).status,200);
  app.close();
});

test('fault control: disclosed broken mode withholds a verified grant while the healthy control grants; neither is source repair evidence', async () => {
  const results:Record<string,boolean>={};
  const revisions:string[]=[];
  for (const mode of ['broken','healthy']) {
    const env:Record<string,string>={...sandboxEnv(),TARGET_MODE:mode};
    const provider=new ExternalProviderFixture();
    const app=createTarget({env,databasePath:':memory:',provider});
    const {fixture,checkout,event}=await settledFixture(app,provider);
    revisions.push(await revision(app));
    const raw=JSON.stringify(event);
    assert.equal((await postWebhook(app,raw,signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY))).status,200);
    assert.equal((await (await call(app,`/api/target/receipts?order_id=${checkout.order_id}`)).json()).state,'succeeded');
    results[mode]=(await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json()).has_access;
    app.close();
  }
  assert.deepEqual(results,{broken:false,healthy:true});
  assert.notEqual(revisions[0],revisions[1]);
});

test('signed wrong account/order/product/revision/customer/amount/currency/session cannot grant', async () => {
  const cases=[
    {name:'account',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.metadata.account_id='acct_other';},status:409},
    {name:'order',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.metadata.order_id='ord_other';},status:404},
    {name:'product',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.product_cart[0].product_id='pdt_other';},status:409},
    {name:'revision',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.metadata.target_revision='sha256:wrong';},status:409},
    {name:'customer',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.customer.customer_id='cus_other';},status:409},
    {name:'amount',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.total_amount=1;},status:409},
    {name:'currency',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.currency='INR';},status:409},
    {name:'session',mutate:(payment:Awaited<ReturnType<typeof settledFixture>>['payment'])=>{payment.checkout_session_id='cks_other';},status:409},
  ];
  for(const scenario of cases){
    const env=sandboxEnv(),provider=new ExternalProviderFixture();
    const app=createTarget({env,databasePath:':memory:',provider});
    const {fixture,checkout,event}=await settledFixture(app,provider);
    scenario.mutate(event.data);
    const raw=JSON.stringify(event);
    const response=await postWebhook(app,raw,signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY));
    assert.equal(response.status,scenario.status,scenario.name);
    assert.equal((await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json()).has_access,false,scenario.name);
    app.close();
  }
});

test('provider pending/failed/errors and mismatches are distinct from entitlement absence', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {fixture,checkout,payment}=await settledFixture(app,provider);
  const receiptUrl=`/api/target/receipts?order_id=${checkout.order_id}`;
  payment.status='processing';
  provider.checkouts.set(checkout.checkout_session_id,{id:checkout.checkout_session_id,payment_id:payment.payment_id,payment_status:'processing'});
  assert.equal((await (await call(app,receiptUrl)).json()).state,'pending');
  payment.status='failed';
  provider.checkouts.set(checkout.checkout_session_id,{id:checkout.checkout_session_id,payment_id:payment.payment_id,payment_status:'failed'});
  assert.equal((await (await call(app,receiptUrl)).json()).state,'failed');
  provider.failReads=true;
  assert.equal((await (await call(app,receiptUrl)).json()).state,'provider_error');
  provider.failReads=false;
  payment.total_amount=5000;
  assert.equal((await (await call(app,receiptUrl)).json()).state,'mismatch');
  const entitlement=await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json();
  assert.equal(entitlement.has_access,false);
  assert.equal((await call(app,`/api/target/entitlements?account_id=acct_other&order_id=${checkout.order_id}`)).status,409);
  app.close();
});

test('cross-origin login and cookie checkout requests cannot mutate a fixture', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const fixture=await (await call(app,'/api/target/fixtures',{run_id:'r1',persona_id:'p1',target_revision:await revision(app)})).json();
  assert.equal((await call(app,'/api/target/login',{login_token:fixture.login_token},{Origin:'https://unowned.example'})).status,403);
  const login=await call(app,'/api/target/login',{login_token:fixture.login_token});
  const cookie=(login.headers.get('set-cookie') || '').split(';')[0];
  assert.equal((await call(app,'/api/target/checkouts',{}, {Cookie:cookie,Origin:'https://unowned.example'})).status,403);
  assert.equal((await call(app,'/api/target/checkouts',{})).status,401);
  app.close();
});

import { mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
test('signed event ledger and grant survive a server restart without duplicate fulfillment', async () => {
  const dir=mkdtempSync(join(tmpdir(),'tryfolk-dodo-ledger-'));
  const databasePath=join(dir,'target.sqlite');
  const env=sandboxEnv(),provider=new ExternalProviderFixture();
  let app=createTarget({env,databasePath,provider});
  try {
    const {fixture,checkout,event}=await settledFixture(app,provider);
    const raw=JSON.stringify(event),headers=signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY);
    const delivered=await postWebhook(app,raw,headers);
    assert.equal(delivered.status,200);
    const evidenceId=(await delivered.json()).evidence_id;
    app.close();app=createTarget({env,databasePath,provider});
    assert.equal((await postWebhook(app,raw,headers)).status,200);
    const result=await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json();
    assert.equal(result.grant_count,1);
    assert.equal(result.has_access,true);
    const evidence=await (await call(app,`/api/target/evidence?evidence_id=${evidenceId}`)).json();
    assert.equal(evidence.source,'external_provider_fixture');
    assert.equal('email' in evidence.payment,false);
  } finally {app.close();rmSync(dir,{recursive:true,force:true});}
});

test('a signed success envelope remains unsettled when independent provider reads are pending', async () => {
  const env=sandboxEnv(),provider=new ExternalProviderFixture();
  const app=createTarget({env,databasePath:':memory:',provider});
  const {fixture,checkout,event,payment}=await settledFixture(app,provider);
  const raw=JSON.stringify(event);
  provider.payments.set(payment.payment_id,{...payment,status:'processing'});
  provider.checkouts.set(checkout.checkout_session_id,{id:checkout.checkout_session_id,payment_id:payment.payment_id,payment_status:'processing'});
  const response=await postWebhook(app,raw,signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY));
  assert.equal(response.status,409);
  assert.equal((await response.json()).receipt.state,'pending');
  assert.equal((await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json()).has_access,false);
  app.close();
});

test('live checkout URLs and provider override configuration are rejected', async () => {
  class WrongHostFixture extends ExternalProviderFixture {
    async createCheckout(input:Parameters<Provider['createCheckout']>[0]) {
      const created=await super.createCheckout(input);
      return {...created,checkout_url:'https://live.dodopayments.com/checkout/fixture'};
    }
  }
  const app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider:new WrongHostFixture()});
  const {cookie}=await customer(app);
  const response=await call(app,'/api/target/checkouts',{}, {Cookie:cookie});
  assert.equal(response.status,502);
  assert.equal((await response.json()).error,'provider_checkout_origin_forbidden');
  app.close();
  const other=createTarget({env:{...sandboxEnv(),DODO_PAYMENTS_BASE_URL:'https://live.dodopayments.com'},databasePath:':memory:'});
  const readiness=await (await call(other,'/api/target/readiness')).json();
  assert.equal(readiness.ready,false);
  assert.ok(readiness.errors.some((error:{code:string})=>error.code==='provider_override_forbidden'));
  other.close();
});

import { configuration } from '../lib/config';
import { dodoProvider } from '../lib/provider';
test('official SDK transport uses test host, write key for creation and distinct read key for the two GETs, external responses are fixtures', async () => {
  const env=sandboxEnv(),config=configuration(env,process.cwd());
  const requests:{path:string;method:string;readKey:boolean;writeKey:boolean}[]=[];
  const original=globalThis.fetch;
  globalThis.fetch=async (input,init)=>{
    const request=new Request(input,init);
    const url=new URL(request.url);
    assert.equal(url.origin,'https://test.dodopayments.com');
    assert.equal(request.redirect,'error');
    const authorization=request.headers.get('authorization');
    requests.push({path:url.pathname,method:request.method,readKey:authorization===`Bearer ${env.DODO_TEST_READ_KEY}`,writeKey:authorization===`Bearer ${env.DODO_TEST_WRITE_KEY}`});
    const response=url.pathname==='/customers' ? {customer_id:'cus_transport_fixture'} : url.pathname==='/checkouts' ? {session_id:'cks_transport_fixture',checkout_url:'https://test.dodopayments.com/checkout/fixture'} : url.pathname.startsWith('/checkouts/') ? {id:'cks_transport_fixture',payment_id:'pay_transport_fixture',payment_status:'succeeded'} : {payment_id:'pay_transport_fixture',status:'succeeded'};
    return Response.json(response);
  };
  try {
    const sdk=dodoProvider(config);
    await sdk.createCustomer({name:'Transport Fixture',email:'transport@tryfolk.invalid'});
    await sdk.createCheckout({product_cart:[{product_id:'pdt_fixture_downloads',quantity:1}],confirm:false});
    await sdk.getCheckout('cks_transport_fixture');
    await sdk.getPayment('pay_transport_fixture');
    assert.deepEqual(requests,[
      {path:'/customers',method:'POST',readKey:false,writeKey:true},
      {path:'/checkouts',method:'POST',readKey:false,writeKey:true},
      {path:'/checkouts/cks_transport_fixture',method:'GET',readKey:true,writeKey:false},
      {path:'/payments/pay_transport_fixture',method:'GET',readKey:true,writeKey:false},
    ]);
  } finally {globalThis.fetch=original;}
});

test('framework loopback URL normalization is accepted only with the exact configured Host header', async () => {
  const app=createTarget({env:sandboxEnv(),databasePath:':memory:'});
  const response=await app.handle(new Request('http://localhost:3300/api/target/readiness',{headers:{Host:'127.0.0.1:3300'}}));
  assert.equal(response.status,200);
  const wrong=await app.handle(new Request('http://localhost:3300/api/target/readiness',{headers:{Host:'unowned.example'}}));
  assert.equal(wrong.status,403);
  app.close();
});

test('entitlement oracle reports actual account access after an unpaid retry and separates that order grant', async () => {
  const env=sandboxEnv(),provider=new ExternalProviderFixture();
  const app=createTarget({env,databasePath:':memory:',provider});
  const {fixture,cookie,event}=await settledFixture(app,provider);
  const raw=JSON.stringify(event);
  assert.equal((await postWebhook(app,raw,signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY))).status,200);
  const retry=await (await call(app,'/api/target/checkouts',{}, {Cookie:cookie})).json();
  const oracle=await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${retry.order_id}`)).json();
  assert.equal(oracle.has_access,true);
  assert.equal(oracle.order_has_grant,false);
  assert.equal((await call(app,'/api/target/download?artwork=0',undefined,{Cookie:cookie})).status,200);
  app.close();
});

test('a signed-in customer can recover a settled owned purchase without a webhook; read oracles cannot trigger recovery', async () => {
  const env=sandboxEnv();delete env.DODO_TEST_WEBHOOK_KEY;
  const provider=new ExternalProviderFixture();
  const app=createTarget({env,databasePath:':memory:',provider});
  const ready=await (await call(app,'/api/target/readiness')).json();
  assert.equal(ready.ready,true);
  assert.equal(ready.webhook_ready,false);
  assert.ok(ready.warnings.some((item:{code:string})=>item.code==='webhook_not_configured'));
  const {fixture,cookie,checkout,payment}=await settledFixture(app,provider);
  const oracle=`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`;
  assert.equal((await (await call(app,`/api/target/receipts?order_id=${checkout.order_id}`)).json()).state,'succeeded');
  assert.equal((await (await call(app,oracle)).json()).has_access,false);
  assert.equal((await (await call(app,`/api/target/return?status=succeeded&payment_id=${payment.payment_id}`,undefined,{Cookie:cookie})).json()).has_access,false);
  const response=await call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie});
  assert.equal(response.status,200);
  const recovered=await response.json();
  assert.equal(recovered.state,'granted');
  assert.equal(recovered.source,'external_provider_fixture');
  assert.equal(recovered.fulfillment_path,'app_reconciliation');
  const evidence=await (await call(app,`/api/target/evidence?evidence_id=${recovered.evidence_id}`)).json();
  assert.equal(evidence.authenticated_raw_body,false);
  assert.equal(evidence.payment.payment_id,payment.payment_id);
  assert.equal(evidence.ownership.account_id,fixture.account_id);
  assert.equal('email' in evidence.payment,false);
  assert.equal((await (await call(app,oracle)).json()).has_access,true);
  assert.equal((await call(app,'/api/target/download?artwork=0',undefined,{Cookie:cookie})).status,200);
  app.close();
});

test('concurrent recovery and later signed delivery apply a single durable fulfillment', async () => {
  const dir=mkdtempSync(join(tmpdir(),'tryfolk-dodo-recovery-'));
  const env=sandboxEnv(),provider=new ExternalProviderFixture(),databasePath=join(dir,'target.sqlite');
  let app=createTarget({env,databasePath,provider});
  try {
    const {fixture,cookie,checkout,event}=await settledFixture(app,provider);
    const responses=await Promise.all([call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie}),call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie})]);
    const results=await Promise.all(responses.map(response=>response.json()));
    assert.deepEqual(results.map(result=>result.state).sort(),['already_granted','granted']);
    assert.equal(results.filter(result=>result.grant_applied).length,1);
    assert.equal(results[0].evidence_id,results[1].evidence_id);
    app.close();app=createTarget({env,databasePath,provider});
    const retried=await (await call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie})).json();
    assert.equal(retried.state,'already_granted');
    assert.equal(retried.evidence_id,results[0].evidence_id);
    const raw=JSON.stringify(event);
    const delivered=await (await postWebhook(app,raw,signedWebhook(raw,env.DODO_TEST_WEBHOOK_KEY))).json();
    assert.equal(delivered.grant_applied,false);
    const oracle=await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json();
    assert.equal(oracle.account_grant_count,1);
    assert.equal(oracle.grant_count,1);
  } finally {app.close();rmSync(dir,{recursive:true,force:true});}
});

test('recovery requires the own active session order and exact origin, never controller credentials or return-query claims', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {fixture,cookie,checkout,payment}=await settledFixture(app,provider);
  const other=await customer(app,'another_run','another_persona');
  const input={order_id:checkout.order_id};
  assert.equal((await call(app,'/api/target/reconcile',input)).status,401);
  assert.equal((await call(app,'/api/target/reconcile',input,{Cookie:other.cookie})).status,409);
  assert.equal((await call(app,'/api/target/reconcile',input,{Cookie:cookie,Origin:'https://unowned.example'})).status,403);
  assert.equal((await call(app,'/api/target/reconcile',{...input,payment_id:payment.payment_id},{Cookie:cookie})).status,400);
  assert.equal((await call(app,`/api/target/reconcile?order_id=${checkout.order_id}&status=succeeded`,undefined,{Cookie:cookie})).status,404);
  assert.equal((await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json()).has_access,false);
  assert.equal((await call(app,'/api/target/reset',{run_id:fixture.run_id,target_revision:fixture.target_revision})).status,200);
  assert.equal((await call(app,'/api/target/reconcile',input,{Cookie:cookie})).status,401);
  app.close();
});

test('recovery rejects pending failed unavailable and mismatched actual provider observations', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {fixture,cookie,checkout,payment}=await settledFixture(app,provider);
  const input={order_id:checkout.order_id};
  async function rejected(state:string,status=409) {
    const response=await call(app,'/api/target/reconcile',input,{Cookie:cookie});
    assert.equal(response.status,status);
    assert.equal((await response.json()).state,state);
    assert.equal((await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json()).has_access,false);
  }
  for (const state of ['processing','requires_customer_action','failed']) {
    payment.status=state;
    provider.checkouts.set(checkout.checkout_session_id,{id:checkout.checkout_session_id,payment_id:payment.payment_id,payment_status:state});
    await rejected(state === 'failed' ? 'failed':'pending');
  }
  provider.failReads=true;await rejected('provider_error',502);provider.failReads=false;
  payment.status='succeeded';
  provider.checkouts.set(checkout.checkout_session_id,{id:checkout.checkout_session_id,payment_id:payment.payment_id,payment_status:'succeeded'});
  const valid=structuredClone(payment);
  const mismatches=[
    {...valid,total_amount:101},{...valid,currency:'INR'},
    {...valid,customer:{...valid.customer,customer_id:'cus_wrong'}},
    {...valid,customer:{...valid.customer,email:'another@tryfolk.invalid'}},
    {...valid,checkout_session_id:'cks_wrong'},
    {...valid,product_cart:[{product_id:'pdt_wrong',quantity:1}]},
    {...valid,product_cart:[{product_id:valid.product_cart[0].product_id,quantity:2}]},
    ...['run_id','persona_id','account_id','fixture_id','target_revision','product_id','order_id'].map(key=>({...valid,metadata:{...valid.metadata,[key]:'wrong'}})),
    {...valid,subscription_id:'sub_wrong'},
  ];
  for (const invalid of mismatches) {provider.payments.set(payment.payment_id,invalid);await rejected('mismatch');}
  provider.payments.set(payment.payment_id,valid);
  assert.equal((await call(app,'/api/target/reconcile',input,{Cookie:cookie})).status,200);
  app.close();
});

test('reset while provider verification is in flight prevents recovery granting the revoked run', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {fixture,cookie,checkout}=await settledFixture(app,provider);
  let enter:()=>void=()=>{},release:()=>void=()=>{};
  const entered=new Promise<void>(resolve=>{enter=resolve;});
  const released=new Promise<void>(resolve=>{release=resolve;});
  const readPayment=provider.getPayment.bind(provider);
  provider.getPayment=async id=>{enter();await released;return readPayment(id);};
  const recovering=call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie});
  await entered;
  assert.equal((await call(app,'/api/target/reset',{run_id:fixture.run_id,target_revision:fixture.target_revision})).status,200);
  release();
  const response=await recovering;
  assert.equal(response.status,409);
  assert.equal((await response.json()).error,'inactive_order');
  assert.equal((await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json()).grant_count,0);
  app.close();
});

test('a preconfigured repaired mode cannot stand in for a source repair', async () => {
  const app=createTarget({env:{...sandboxEnv(),TARGET_MODE:'repaired'},databasePath:':memory:'});
  const ready=await (await call(app,'/api/target/readiness')).json();
  assert.equal(ready.ready,false);
  assert.ok(ready.errors.some((item:{code:string})=>item.code==='invalid_mode'));
  app.close();
});

test('a prior revision session cannot recover an order after the target source configuration changes', async () => {
  const dir=mkdtempSync(join(tmpdir(),'tryfolk-dodo-revision-'));
  const env=sandboxEnv(),provider=new ExternalProviderFixture(),databasePath=join(dir,'target.sqlite');
  let app=createTarget({env,databasePath,provider});
  try {
    const {cookie,checkout}=await settledFixture(app,provider);
    app.close();app=createTarget({env:{...env,TARGET_MODE:'broken'},databasePath,provider});
    const response=await call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie});
    assert.equal(response.status,409);
    assert.equal((await response.json()).error,'revision_mismatch');
    assert.equal((await call(app,'/api/target/download?artwork=0',undefined,{Cookie:cookie})).status,409);
  } finally {app.close();rmSync(dir,{recursive:true,force:true});}
});

test('only the exact hosted sandbox origin is usable, with no API-host short links or lookalike live origins', async () => {
  for (const checkoutOrigin of ['https://test.dodopayments.com','https://checkout.dodopayments.com','https://live.checkout.dodopayments.com','https://test.checkout.dodopayments.com.unowned.example','http://test.checkout.dodopayments.com','https://test.checkout.dodopayments.com:444','https://user@test.checkout.dodopayments.com']) {
    const provider=new ExternalProviderFixture();provider.checkoutOrigin=checkoutOrigin;
    const app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
    const {cookie}=await customer(app);
    const response=await call(app,'/api/target/checkouts',{}, {Cookie:cookie});
    assert.equal(response.status,502,checkoutOrigin);
    assert.equal((await response.json()).error,'provider_checkout_origin_forbidden');
    app.close();
  }
});

test('fault control: disclosed broken reconciliation records settled receipt but cannot grant via repeated retries', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:{...sandboxEnv(),TARGET_MODE:'broken'},databasePath:':memory:',provider});
  const {fixture,cookie,checkout}=await settledFixture(app,provider);
  const first=await (await call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie})).json();
  const second=await (await call(app,'/api/target/reconcile',{order_id:checkout.order_id},{Cookie:cookie})).json();
  assert.equal(first.state,'suppressed');assert.equal(second.state,'suppressed');
  assert.equal(first.grant_applied,false);assert.equal(second.evidence_id,first.evidence_id);
  const receipt=await (await call(app,`/api/target/receipts?order_id=${checkout.order_id}`)).json();
  assert.equal(receipt.state,'succeeded');assert.equal(receipt.source,'external_provider_fixture');
  const oracle=await (await call(app,`/api/target/entitlements?account_id=${fixture.account_id}&order_id=${checkout.order_id}`)).json();
  assert.equal(oracle.has_access,false);assert.equal(oracle.grant_count,0);
  app.close();
});

test('owned synthetic checkout limits payment to card entry and protects its non-deliverable customer identity', async () => {
  const provider=new ExternalProviderFixture(),app=createTarget({env:sandboxEnv(),databasePath:':memory:',provider});
  const {cookie}=await customer(app);
  assert.equal((await call(app,'/api/target/checkouts',{}, {Cookie:cookie})).status,201);
  const sent=provider.checkoutInputs[0];
  assert.deepEqual(sent.allowed_payment_method_types,['credit','debit']);
  assert.equal(sent.feature_flags?.allow_customer_editing_email,false);
  assert.equal(sent.feature_flags?.allow_customer_editing_name,false);
  assert.equal(sent.short_link,false);
  assert.match(provider.customerInputs[0].email,/@tryfolk\.invalid$/);
  app.close();
});

test('manifest names the fulfillment-only repair seam and its effective decision', async () => {
  const app=createTarget({env:sandboxEnv(),databasePath:':memory:'});
  try {
    const manifest=await (await call(app,'/api/target/manifest')).json();
    assert.deepEqual(manifest.repair,{source_file:'lib/fulfillment-policy.ts',disposition:'granted'});
    assert.equal(manifest.fault,null);
  } finally {app.close();}
});
