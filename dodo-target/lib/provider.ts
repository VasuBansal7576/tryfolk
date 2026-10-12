import DodoPayments from 'dodopayments';
import { APIError, APIConnectionError, APIConnectionTimeoutError, APIUserAbortError } from 'dodopayments';
import type { CheckoutSessionCreateParams } from 'dodopayments/resources/checkout-sessions';
import { z } from 'zod';
import { PROVIDER_ORIGIN, type Config, type SetupError } from './config';
import { Id, type Order } from './model';

export interface Provider {
  readonly source: 'dodo_test_api' | 'external_provider_fixture';
  createCustomer(input:{email:string;name:string}):Promise<unknown>;
  createCheckout(input:CheckoutSessionCreateParams):Promise<unknown>;
  getCheckout(id:string):Promise<unknown>;
  getPayment(id:string):Promise<unknown>;
}

/** Private closed diagnostics: never retain SDK text, body, headers or keys. */
export function providerReadDiagnostic(error:unknown, stage:'checkout'|'payment') {
  if (error instanceof z.ZodError) return {stage,kind:'schema_error',http_status:null};
  if (error instanceof APIConnectionTimeoutError) return {stage,kind:'sdk_timeout',http_status:null};
  if (error instanceof APIUserAbortError) return {stage,kind:'sdk_aborted',http_status:null};
  if (error instanceof APIConnectionError) return {stage,kind:'sdk_connection_error',http_status:null};
  if (error instanceof APIError) {
    const status=typeof error.status === 'number' && Number.isInteger(error.status)
      && error.status >= 400 && error.status <= 599 ? error.status : null;
    const kind=status === 401 || status === 403 ? 'sdk_authentication_error'
      : status === 429 ? 'sdk_rate_limit_error' : status !== null && status >= 500 ? 'sdk_server_error'
      : status !== null ? 'sdk_client_error' : 'sdk_api_error';
    return {stage,kind,http_status:status};
  }
  return {stage,kind:'unrecognized_error',http_status:null};
}

function client(key:string, webhookKey?:string) {
  return new DodoPayments({
    bearerToken:key, webhookKey, environment:'test_mode', baseURL:null,
    maxRetries:0, timeout:10_000, logLevel:'off',
    fetch:async (input,init)=>{
      const url=new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url);
      if (url.origin !== PROVIDER_ORIGIN) throw new Error('provider_origin_forbidden');
      return fetch(input,{...init,redirect:'error',cache:'no-store'});
    },
  });
}
export function dodoProvider(config:Config):Provider {
  const write=client(config.writeKey),read=client(config.readKey);
  return {
    source:'dodo_test_api',
    createCustomer:input=>write.customers.create(input),
    createCheckout:input=>write.checkoutSessions.create(input),
    getCheckout:id=>read.checkoutSessions.retrieve(id),
    getPayment:id=>read.payments.retrieve(id),
  };
}
export function unwrapWebhook(raw:string,headers:Record<string,string>,config:Config):unknown {
  // unwrap verifies the untouched raw string before parsing. Never unsafeUnwrap.
  return client(config.writeKey,config.webhookKey).webhooks.unwrap(raw,{headers});
}
export const CheckoutCreated=z.object({session_id:Id,checkout_url:z.url()});
export const CustomerCreated=z.object({customer_id:Id});
export const CheckoutStatus=z.object({id:Id,payment_id:Id.nullish(),payment_status:z.string().nullish()});
export const Payment=z.object({
  business_id:Id,payment_provider:z.literal('dodo'),
  payment_id:Id,status:z.string().nullish(),checkout_session_id:Id.nullish(),
  payment_link:z.string().nullish(),
  customer:z.object({customer_id:Id,email:z.email()}),
  metadata:z.record(z.string(),z.string()),product_cart:z.array(z.object({product_id:Id,quantity:z.number().int()})).nullish(),
  total_amount:z.number().int().nonnegative(),currency:z.string(),
  subscription_id:Id.nullish(),subscription_ids:z.array(Id).optional(),
});
export type Payment = z.infer<typeof Payment>;
export const SucceededEvent=z.object({type:z.literal('payment.succeeded'),business_id:Id,timestamp:z.string(),data:Payment});

export function orderMetadata(order:Order):Record<string,string> {
  return {
    order_id:order.order_id,run_id:order.run_id,persona_id:order.persona_id,
    account_id:order.account_id,fixture_id:order.fixture_id,target_revision:order.target_revision,
    product_id:order.expected_product_id,plan:'Special Downloads',
  };
}
export function paymentMismatches(order:Order,payment:Payment):string[] {
  const mismatches:string[]=[];
  if (payment.customer.customer_id !== order.customer_id) mismatches.push('customer_id');
  if (payment.checkout_session_id && payment.checkout_session_id !== order.checkout_session_id) mismatches.push('checkout_session_id');
  for (const [key,value] of Object.entries(orderMetadata(order))) {
    if (payment.metadata[key] !== value) mismatches.push(`metadata.${key}`);
  }
  if (payment.total_amount !== order.amount_minor) mismatches.push('amount');
  if (payment.currency !== order.currency) mismatches.push('currency');
  if (!payment.product_cart || payment.product_cart.length !== 1 || payment.product_cart[0].product_id !== order.expected_product_id || payment.product_cart[0].quantity !== 1) mismatches.push('product_cart');
  if (payment.subscription_id || payment.subscription_ids?.length) mismatches.push('subscription_forbidden');
  return mismatches;
}
export function sanitizedPayment(payment:Payment) {
  return {
    payment_id:payment.payment_id,status:payment.status ?? null,checkout_session_id:payment.checkout_session_id ?? null,
    customer_id:payment.customer.customer_id,
    business_id:payment.business_id,payment_provider:payment.payment_provider,
    metadata:Object.fromEntries(['order_id','run_id','persona_id','account_id','fixture_id','target_revision','product_id','plan'].map(key=>[key,payment.metadata[key] ?? null])),
    product_cart:payment.product_cart,amount_minor:payment.total_amount,currency:payment.currency,
  };
}
export type Observation =
  | {state:'setup_required';errors:SetupError[]}
  | {state:'provider_error';reason:string}
  | {state:'pending';reason:string;checkout:z.infer<typeof CheckoutStatus>|null;payment:ReturnType<typeof sanitizedPayment>|null}
  | {state:'mismatch';mismatches:string[];checkout:z.infer<typeof CheckoutStatus>;payment:ReturnType<typeof sanitizedPayment>|null}
  | {state:'succeeded'|'failed';mismatches:string[];checkout:z.infer<typeof CheckoutStatus>;payment:ReturnType<typeof sanitizedPayment>};
