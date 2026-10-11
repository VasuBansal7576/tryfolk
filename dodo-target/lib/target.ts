import { randomBytes, createHash, timingSafeEqual } from 'node:crypto';
import { CHECKOUT_ORIGIN, configuration, type Env } from './config';
import { Store } from './store';
import { Account, Event, FixtureRequest, Fulfillment, Id, Login, Order, Ownership, ResetRequest, Run, Session } from './model';
import { CheckoutCreated, CheckoutStatus, CustomerCreated, dodoProvider, orderMetadata, Payment, paymentMismatches, sanitizedPayment, SucceededEvent, unwrapWebhook, type Observation, type Provider } from './provider';
import { z } from 'zod';
import { ARTWORKS, sampleArtwork } from './artworks';
import { specialDownloadsDisposition } from './fulfillment-policy';

class HttpError extends Error {
  constructor(public status:number, public code:string) { super(code); }
}
const opaque=()=>randomBytes(32).toString('base64url');
const digest=(value:string)=>createHash('sha256').update(value).digest('hex');
const id=(prefix:string)=>`${prefix}_${randomBytes(16).toString('hex')}`;
function equalSecret(a:string,b:string) {
  const left=Buffer.from(a),right=Buffer.from(b);
  return left.length === right.length && timingSafeEqual(left,right);
}
export function createTarget(options: {env: Env; databasePath: string; provider?: Provider}) {
  const config = configuration(options.env, process.cwd());
  const store = new Store(options.databasePath);
  let provider:Provider|undefined = options.provider;
  function paymentProvider():Provider {
    provider ??= dodoProvider(config);
    return provider;
  }
  function sandbox() {
    if (!config.sandbox || !config.safeOrigin) throw new HttpError(403,'sandbox_forbidden');
  }
  function controller(request:Request) {
    sandbox();
    if (config.controlToken.length < 32) throw new HttpError(503,'controller_not_configured');
    if (!equalSecret(request.headers.get('authorization') || '',`Bearer ${config.controlToken}`)) throw new HttpError(401,'unauthorized');
  }
  function currentRevision(value:string) { if (value !== config.manifest.target_revision) throw new HttpError(409,'revision_mismatch'); }
  function sameOrigin(request:Request) {
    if (request.headers.get('origin') !== config.origin) throw new HttpError(403,'csrf_forbidden');
  }
  function accountFor(request:Request):Account {
    sandbox();
    const token=(request.headers.get('cookie') || '').split(';').map(part=>part.trim()).find(part=>part.startsWith('tryfolk_target_session='))?.slice('tryfolk_target_session='.length);
    const session=token ? store.get('sessions',digest(token),Session) : null;
    if (!session || session.expires_at < Date.now()) throw new HttpError(401,'login_required');
    const account=store.get('accounts',session.account_id,Account);
    if (!account || !account.active) throw new HttpError(401,'login_required');
    currentRevision(account.target_revision);
    return account;
  }
  function orderFor(value:string|null):Order {
    const parsed=Id.safeParse(value);
    if (!parsed.success) throw new HttpError(400,'order_id_required');
    const order=store.get('orders',parsed.data,Order);
    if (!order) throw new HttpError(404,'order_not_found');
    currentRevision(order.target_revision);
    return order;
  }
  function evidence(data:Record<string,unknown>):string {
    const evidenceId=id('ev');
    store.insertEvidence(evidenceId,{evidence_id:evidenceId,observed_at:new Date().toISOString(),...data});
    return evidenceId;
  }
  // Called only inside the caller's BEGIN IMMEDIATE transaction, after fresh
  // independent provider verification. This ledger is shared by both grant paths.
  function fulfill(current:Order,paymentId:string,source:Fulfillment['source'],record:(state:'granted'|'suppressed'|'already_granted',applied:boolean)=>string) {
    const owner=store.get('accounts',current.account_id,Account);
    const run=store.get('runs',current.run_id,Run);
    if (!owner?.active || !current.active || !run?.active || run.target_revision !== current.target_revision) throw new HttpError(409,'inactive_order');
    currentRevision(owner.target_revision);
    if (JSON.stringify(Ownership.parse(owner)) !== JSON.stringify(Ownership.parse(current))) throw new HttpError(409,'account_order_mismatch');
    if (store.list('orders',Order).some(other=>other.order_id !== current.order_id && other.payment_id === paymentId)) throw new HttpError(409,'payment_already_bound');
    if (current.payment_id && current.payment_id !== paymentId) throw new HttpError(409,'payment_changed');
    const previous=store.get('fulfillments',current.order_id,Fulfillment);
    if (previous) {
      if (previous.payment_id !== paymentId || current.granted !== (previous.disposition === 'granted')) throw new HttpError(409,'fulfillment_conflict');
      return {...previous,state:previous.disposition === 'granted' ? 'already_granted' : 'suppressed',grant_applied:false};
    }
    const state=current.granted ? 'already_granted' : specialDownloadsDisposition(config.mode);
    const grantApplied=state === 'granted';
    const evidenceId=record(state,grantApplied);
    if (state !== 'already_granted') {
      current.payment_id=paymentId;current.granted=grantApplied;
      store.put('orders',current.order_id,current);
    }
    const entry=Fulfillment.parse({order_id:current.order_id,payment_id:paymentId,disposition:current.granted ? 'granted' : 'suppressed',evidence_id:evidenceId,source});
    store.put('fulfillments',current.order_id,entry);
    return {...entry,state,grant_applied:grantApplied};
  }
  async function receipt(order:Order,browserParent=false) {
    const ownership={...Ownership.parse(order),order_id:order.order_id,expected_product_id:order.expected_product_id};
    const source=options.provider?.source || 'dodo_test_api';
    let result:Observation;
    let privatePaymentLink:string|null=null;
    if (config.errors.length) result={state:'setup_required',errors:config.errors};
    else if (!order.checkout_session_id) result=order.state === 'creation_error' ? {state:'provider_error',reason:order.state} : {state:'pending',reason:order.state,checkout:null,payment:null};
    else {
      try {
        const api=paymentProvider();
        const checkout=CheckoutStatus.parse(await api.getCheckout(order.checkout_session_id));
        if (checkout.id !== order.checkout_session_id) result={state:'mismatch',mismatches:['checkout.id'],checkout,payment:null};
        else if (!checkout.payment_id) result={state:'pending',reason:'checkout_details_unsettled',checkout,payment:null};
        else {
          const payment=Payment.parse(await api.getPayment(checkout.payment_id));
          privatePaymentLink=payment.payment_link ?? null;
          const mismatches=paymentMismatches(order,payment);
          if (payment.payment_id !== checkout.payment_id) mismatches.push('payment_id');
          const account=store.get('accounts',order.account_id,Account);
          if (!account || payment.customer.email !== account.email) mismatches.push('customer_email');
          if (order.payment_id && payment.payment_id !== order.payment_id) mismatches.push('previous_payment_id');
          if (checkout.payment_status && checkout.payment_status !== payment.status) mismatches.push('checkout.payment_status');
          const view=sanitizedPayment(payment);
          if (mismatches.length) result={state:'mismatch',mismatches,checkout,payment:view};
          else if (payment.status === 'succeeded' && checkout.payment_status === 'succeeded') result={state:'succeeded',mismatches,checkout,payment:view};
          else if (['failed','cancelled'].includes(payment.status || '')) result={state:'failed',mismatches,checkout,payment:view};
          else result={state:'pending',reason:payment.status || 'payment_status_unavailable',checkout,payment:view};
        }
      } catch(error) {
        result={state:'provider_error',reason:error instanceof z.ZodError ? 'invalid_provider_response' : 'provider_read_failed'};
      }
    }
    const data={...result,source,ownership,checkout_session_id:order.checkout_session_id};
    // Routing authority crosses only the controller's private callback read.
    // Neither normalized receipts nor persisted evidence contain the link.
    return {...data,evidence_id:evidence(data),...(browserParent ? {private_payment_link:privatePaymentLink} : {})};
  }
  async function body<T>(request:Request,schema:z.ZodType<T>):Promise<T> {
    const raw=await request.text();
    if (raw.length > 16_384) throw new HttpError(413,'payload_too_large');
    try {return schema.parse(JSON.parse(raw));} catch {throw new HttpError(400,'invalid_request');}
  }
  async function route(request:Request):Promise<Response> {
    const url=new URL(request.url);
    // Next may normalize a loopback Request URL to localhost. The original HTTP
    // Host must still match the exact configured origin, never forwarded headers.
    const host=request.headers.get('host');
    const internalLoopback=url.protocol === 'http:' && ['127.0.0.1','localhost','[::1]'].includes(url.hostname);
    if (host ? host !== new URL(config.origin).host || !internalLoopback : url.origin !== config.origin) throw new HttpError(403,'origin_forbidden');
    const path=url.pathname;
    if (path === '/api/target/readiness' && request.method === 'GET') return Response.json({ready:config.errors.length === 0,errors:config.errors,warnings:config.warnings,webhook_ready:Boolean(config.webhookKey && config.writeKey),manifest:config.manifest,evidence_source:options.provider?.source || 'dodo_test_api',provider_baseline_verified:false});
    if (path === '/api/target/manifest' && request.method === 'GET') return Response.json(config.manifest);
    if (path === '/api/target/products' && request.method === 'GET') return Response.json({products:[config.manifest.product],ready:config.errors.length === 0});
    if (path === '/api/webhooks/payment' && request.method === 'POST') {
      sandbox();
      if (!config.webhookKey || !config.writeKey) throw new HttpError(503,'webhook_not_configured');
      const raw=await request.text();
      if (raw.length > 262_144) throw new HttpError(413,'payload_too_large');
      const headers=Object.fromEntries(['webhook-id','webhook-signature','webhook-timestamp'].map(key=>[key,request.headers.get(key) || '']));
      let authenticated:unknown;
      try { authenticated=unwrapWebhook(raw,headers,config); }
      catch { throw new HttpError(401,'invalid_signature'); }
      const eventType=z.object({type:z.string()}).safeParse(authenticated);
      if (!eventType.success) throw new HttpError(422,'invalid_event_schema');
      if (eventType.data.type !== 'payment.succeeded') return Response.json({received:true,ignored:true});
      const parsed=SucceededEvent.safeParse(authenticated);
      if (!parsed.success) throw new HttpError(422,'invalid_event_schema');
      const event=parsed.data;
      const eventId=Id.safeParse(headers['webhook-id']);
      if (!eventId.success) throw new HttpError(422,'invalid_event_id');
      const rawHash=digest(raw);
      const previous=store.get('events',eventId.data,Event);
      if (previous) {
        if (previous.raw_sha256 !== rawHash) throw new HttpError(409,'event_id_conflict');
        return Response.json({received:true,duplicate:true,evidence_id:previous.evidence_id});
      }
      const order=orderFor(event.data.metadata.order_id || null);
      const account=store.get('accounts',order.account_id,Account);
      const mismatches=paymentMismatches(order,event.data);
      if (!account || !account.active || !order.active) throw new HttpError(409,'inactive_order');
      if (event.data.status !== 'succeeded') mismatches.push('event.payment_status');
      if (event.business_id !== event.data.business_id) mismatches.push('event.business_id');
      if (event.data.customer.email !== account.email) mismatches.push('customer_email');
      if (mismatches.length) {
        const evidenceId=evidence({source:options.provider?.source || 'dodo_signed_webhook',state:'rejected',mismatches,raw_sha256:rawHash,ownership:Ownership.parse(order),event_id:eventId.data,payment:sanitizedPayment(event.data)});
        return Response.json({error:'event_order_mismatch',mismatches,evidence_id:evidenceId},{status:409});
      }
      const verified=await receipt(order);
      if (verified.state !== 'succeeded') return Response.json({error:'payment_not_settled',receipt:verified},{status:verified.state === 'provider_error' ? 502 : 409});
      const verifiedPayment=Payment.pick({payment_id:true,business_id:true}).parse(verified.payment);
      if (verifiedPayment.payment_id !== event.data.payment_id || verifiedPayment.business_id !== event.business_id) throw new HttpError(409,'event_payment_mismatch');
      const result=store.transaction(()=>{
        const prior=store.get('events',eventId.data,Event);
        if (prior) {
          if (prior.raw_sha256 !== rawHash) throw new HttpError(409,'event_id_conflict');
          return {received:true,duplicate:true,evidence_id:prior.evidence_id};
        }
        const current=orderFor(order.order_id);
        const source=options.provider?.source === 'external_provider_fixture' ? 'external_provider_fixture' : 'dodo_signed_webhook';
        const record=(state:string,applied:boolean)=>evidence({source,state,grant_applied:applied,event_id:eventId.data,raw_sha256:rawHash,authenticated_raw_body:true,ownership:{...Ownership.parse(current),order_id:current.order_id},receipt_evidence_id:verified.evidence_id,payment:sanitizedPayment(event.data)});
        const fulfillment=fulfill(current,event.data.payment_id,source,record);
        const evidenceId=fulfillment.grant_applied ? fulfillment.evidence_id : record(fulfillment.state,false);
        store.put('events',eventId.data,{event_id:eventId.data,raw_sha256:rawHash,order_id:current.order_id,payment_id:event.data.payment_id,disposition:fulfillment.disposition,evidence_id:evidenceId});
        return {received:true,duplicate:false,disposition:fulfillment.disposition,grant_applied:fulfillment.grant_applied,evidence_id:evidenceId,fulfillment_evidence_id:fulfillment.evidence_id};
      });
      return Response.json(result);
    }
    if (path === '/api/target/fixtures' && request.method === 'POST') {
      controller(request);
      const data=await body(request,FixtureRequest);
      currentRevision(data.target_revision);
      const token=opaque();
      const account=Account.parse({...data,account_id:id('acct'),fixture_id:id('fx'),email:`${id('synthetic')}@tryfolk.invalid`,active:true,created_at:new Date().toISOString()});
      store.transaction(()=>{
        const run=store.get('runs',data.run_id,Run);
        if (run && (!run.active || run.target_revision !== data.target_revision)) throw new HttpError(409,'run_closed_or_revision_changed');
        store.put('runs',data.run_id,{run_id:data.run_id,target_revision:data.target_revision,active:true});
        store.put('accounts',account.account_id,account);
        store.put('logins',digest(token),{account_id:account.account_id,expires_at:Date.now()+300_000,used:false});
      });
      return Response.json({...Ownership.parse(account),login_token:token,login_url:`${config.origin}/target/login#token=${token}`},{status:201});
    }
    if (path === '/api/target/login' && request.method === 'POST') {
      sandbox();
      sameOrigin(request);
      const data=await body(request,z.strictObject({login_token:z.string().min(32).max(128)}));
      const token=opaque();
      const account=store.transaction(()=>{
        const key=digest(data.login_token),login=store.get('logins',key,Login);
        if (!login || login.used || login.expires_at < Date.now()) throw new HttpError(401,'invalid_login');
        const account=store.get('accounts',login.account_id,Account);
        if (!account || !account.active) throw new HttpError(401,'invalid_login');
        currentRevision(account.target_revision);
        store.put('logins',key,{...login,used:true});
        store.put('sessions',digest(token),{account_id:account.account_id,expires_at:Date.now()+3_600_000});
        return account;
      });
      return Response.json({ownership:Ownership.parse(account)}, {headers:{'Set-Cookie':`tryfolk_target_session=${token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600`}});
    }
    if ((path === '/api/target/me' || path === '/api/target/return') && request.method === 'GET') {
      const account=accountFor(request);
      const orders=store.list('orders',Order).filter(order=>order.account_id === account.account_id && order.active);
      return Response.json({ownership:Ownership.parse(account),has_access:orders.some(order=>order.granted),orders});
    }
    if (path === '/api/target/download' && request.method === 'GET') {
      const account=accountFor(request);
      if (!store.list('orders',Order).some(order=>order.account_id === account.account_id && order.active && order.granted)) throw new HttpError(403,'purchase_required');
      const index=z.coerce.number().int().min(0).max(ARTWORKS.length-1).safeParse(url.searchParams.get('artwork') ?? '0');
      if (!index.success) throw new HttpError(400,'invalid_artwork');
      return new Response(sampleArtwork(index.data),{headers:{'Content-Type':'image/svg+xml','Content-Disposition':`attachment; filename="${ARTWORKS[index.data].replaceAll(' ','_')}.svg"`}});
    }
    if (path === '/api/target/reset' && request.method === 'POST') {
      controller(request);
      const data=await body(request,ResetRequest);
      currentRevision(data.target_revision);
      const result=store.transaction(()=>{
        const run=store.get('runs',data.run_id,Run);
        if (!run || run.target_revision !== data.target_revision) throw new HttpError(404,'run_not_found');
        const accounts=store.list('accounts',Account).filter(account=>account.run_id === data.run_id && account.target_revision === data.target_revision);
        const orders=store.list('orders',Order).filter(order=>order.run_id === data.run_id && order.target_revision === data.target_revision);
        for (const account of accounts) store.put('accounts',account.account_id,{...account,active:false});
        for (const order of orders) store.put('orders',order.order_id,{...order,active:false});
        store.put('runs',data.run_id,{...run,active:false});
        return {run_id:data.run_id,target_revision:data.target_revision,revoked_accounts:accounts.length,revoked_orders:orders.length,evidence_preserved:true};
      });
      return Response.json(result);
    }
    if (path === '/api/target/checkouts' && request.method === 'POST') {
      const account=accountFor(request);
      sameOrigin(request);
      await body(request,z.strictObject({}));
      if (config.errors.length || config.amountMinor === null || config.currency === null) return Response.json({error:'setup_required',errors:config.errors},{status:503});
      const order=Order.parse({...Ownership.parse(account),order_id:id('ord'),expected_product_id:config.productId,amount_minor:config.amountMinor,currency:config.currency,active:true,created_at:new Date().toISOString(),checkout_session_id:null,state:'creating',granted:false,payment_id:null,customer_id:null});
      store.put('orders',order.order_id,order);
      try {
        const api=paymentProvider();
        const customer=CustomerCreated.parse(await api.createCustomer({email:account.email,name:'Tryfolk Synthetic Customer'}));
        order.customer_id=customer.customer_id;
        store.put('orders',order.order_id,order);
        const created=CheckoutCreated.parse(await api.createCheckout({
          product_cart:[{product_id:order.expected_product_id,quantity:1}],customer:{customer_id:customer.customer_id},
          metadata:orderMetadata(order),confirm:false,show_saved_payment_methods:false,short_link:false,
          allowed_payment_method_types:['credit','debit'],
          billing_currency:config.currency,
          return_url:`${config.origin}/checkout/return`,cancel_url:`${config.origin}/special-downloads`,
          feature_flags:{allow_currency_selection:false,allow_discount_code:false,allow_phone_number_collection:false,allow_tax_id:false,allow_customer_editing_email:false,allow_customer_editing_name:false,always_create_new_customer:false},
        }));
        const checkoutUrl=new URL(created.checkout_url);
        if (checkoutUrl.origin !== CHECKOUT_ORIGIN || checkoutUrl.username || checkoutUrl.password) throw new HttpError(502,'provider_checkout_origin_forbidden');
        if (store.list('orders',Order).some(existing=>existing.checkout_session_id === created.session_id)) throw new Error('reused_checkout');
        store.transaction(()=>{
          const current=store.get('accounts',account.account_id,Account);
          order.active=Boolean(current?.active);
          order.checkout_session_id=created.session_id;order.state='ready';
          store.put('orders',order.order_id,order);
        });
        if (!order.active) throw new HttpError(409,'run_reset_during_checkout');
        return Response.json({order_id:order.order_id,checkout_session_id:created.session_id,checkout_url:created.checkout_url,ownership:Ownership.parse(order),source:api.source,
          // No show_* customization is supplied to createCheckout above. The
          // hosted source applies these flags only for explicit false values.
          navigation_customization:{hide_order_details:false,hide_on_demand_tag:false}},{status:201});
      } catch(error) {
        order.state='creation_error';store.put('orders',order.order_id,order);
        if (error instanceof HttpError) throw error;
        return Response.json({error:'provider_checkout_failed',order_id:order.order_id,source:options.provider?.source || 'dodo_test_api'},{status:502});
      }
    }
    if (path === '/api/target/orders' && request.method === 'GET') {
      controller(request);
      const accountId=Id.safeParse(url.searchParams.get('account_id'));
      if (!accountId.success) throw new HttpError(400,'account_id_required');
      return Response.json({orders:store.list('orders',Order).filter(order=>order.account_id === accountId.data)});
    }
    if (path === '/api/target/receipts' && request.method === 'GET') {
      controller(request);
      const browserParent=url.searchParams.get('browser_parent');
      if (browserParent !== null && browserParent !== '1') throw new HttpError(400,'invalid_browser_parent');
      return Response.json(await receipt(orderFor(url.searchParams.get('order_id')),browserParent === '1'));
    }
    if (path === '/api/target/reconcile' && request.method === 'POST') {
      const account=accountFor(request);
      sameOrigin(request);
      const input=await body(request,z.strictObject({order_id:Id}));
      const order=orderFor(input.order_id);
      if (order.account_id !== account.account_id) throw new HttpError(409,'account_order_mismatch');
      if (!order.active) throw new HttpError(409,'inactive_order');
      const verified=await receipt(order);
      const source=options.provider?.source === 'external_provider_fixture' ? 'external_provider_fixture' : 'dodo_test_api_reconciliation';
      if (verified.state !== 'succeeded') return Response.json({error:'payment_not_settled',state:verified.state,source,fulfillment_path:'app_reconciliation',receipt:verified},{status:verified.state === 'provider_error' ? 502 : verified.state === 'setup_required' ? 503 : 409});
      const payment=Payment.pick({payment_id:true}).parse(verified.payment);
      const result=store.transaction(()=>{
        const current=orderFor(order.order_id);
        if (current.account_id !== account.account_id) throw new HttpError(409,'account_order_mismatch');
        const fulfillment=fulfill(current,payment.payment_id,source,(state,applied)=>evidence({source,state,grant_applied:applied,fulfillment_path:'app_reconciliation',authenticated_raw_body:false,ownership:{...Ownership.parse(current),order_id:current.order_id,expected_product_id:current.expected_product_id},receipt_evidence_id:verified.evidence_id,payment:verified.payment}));
        return {state:fulfillment.state,source,fulfillment_source:fulfillment.source,fulfillment_path:'app_reconciliation',grant_applied:fulfillment.grant_applied,evidence_id:fulfillment.evidence_id,receipt_evidence_id:verified.evidence_id,ownership:{...Ownership.parse(current),order_id:current.order_id},payment_id:payment.payment_id,has_access:current.granted};
      });
      return Response.json(result);
    }
    if (path === '/api/target/entitlements' && request.method === 'GET') {
      controller(request);
      const order=orderFor(url.searchParams.get('order_id'));
      if (url.searchParams.get('account_id') !== order.account_id) throw new HttpError(409,'account_order_mismatch');
      const account=store.get('accounts',order.account_id,Account);
      const paidOrders=store.list('orders',Order).filter(item=>item.account_id === order.account_id && item.active && item.granted);
      return Response.json({source:'app_local_entitlement',has_access:Boolean(account?.active && paidOrders.length),order_has_grant:Boolean(account?.active && order.active && order.granted),grant_count:order.granted ? 1 : 0,account_grant_count:paidOrders.length,active:Boolean(account?.active && order.active),ownership:{...Ownership.parse(order),order_id:order.order_id,expected_product_id:order.expected_product_id},payment_id:order.payment_id});
    }
    if (path === '/api/target/evidence' && request.method === 'GET') {
      controller(request);
      const evidenceId=Id.safeParse(url.searchParams.get('evidence_id'));
      if (!evidenceId.success) throw new HttpError(400,'evidence_id_required');
      const data=store.evidence(evidenceId.data);
      if (!data) throw new HttpError(404,'evidence_not_found');
      return Response.json(data);
    }
    throw new HttpError(404,'not_found');
  }
  return {
    async handle(request:Request):Promise<Response> {
      let response:Response;
      try { response=await route(request); }
      catch(error) { response=error instanceof HttpError ? Response.json({error:error.code},{status:error.status}) : Response.json({error:'target_error'},{status:500}); }
      response.headers.set('Cache-Control','no-store');
      response.headers.set('Referrer-Policy','no-referrer');
      return response;
    },
    close() {store.close();},
  };
}
