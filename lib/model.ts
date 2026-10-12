import { z } from 'zod';
export const Id = z.string().min(1).max(128).regex(/^[a-zA-Z0-9_:-]+$/);
export const Ownership = z.object({
  run_id:Id, persona_id:Id, account_id:Id, fixture_id:Id,
  target_revision:z.string().regex(/^sha256:[a-f0-9]{64}$/),
});
export type Ownership = z.infer<typeof Ownership>;
export const Account = Ownership.extend({email:z.email(), active:z.boolean(), created_at:z.string()});
export type Account = z.infer<typeof Account>;
export const Login = z.object({account_id:Id,expires_at:z.number(),used:z.boolean()});
export const Session = z.object({account_id:Id,expires_at:z.number()});
export const Run = z.object({run_id:Id,target_revision:z.string(),active:z.boolean()});
export const Order = Ownership.extend({
  order_id:Id, expected_product_id:Id, amount_minor:z.number().int().positive(), currency:z.string(),
  active:z.boolean(), created_at:z.string(), checkout_session_id:Id.nullable(),
  state:z.enum(['creating','ready','creation_error']), granted:z.boolean(), payment_id:Id.nullable(),
  customer_id:Id.nullable(),
});
export type Order = z.infer<typeof Order>;
export const Event = z.object({event_id:Id,raw_sha256:z.string(),order_id:Id,payment_id:Id,disposition:z.enum(['granted','suppressed']),evidence_id:Id});
export const Fulfillment = z.object({order_id:Id,payment_id:Id,disposition:z.enum(['granted','suppressed']),evidence_id:Id,source:z.enum(['dodo_signed_webhook','dodo_test_api_reconciliation','external_provider_fixture'])});
export type Fulfillment = z.infer<typeof Fulfillment>;
export const FixtureRequest = z.strictObject({run_id:Id,persona_id:Id,target_revision:Ownership.shape.target_revision});
export const ResetRequest = z.strictObject({run_id:Id,target_revision:Ownership.shape.target_revision});
