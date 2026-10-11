import { z } from 'zod';

const money = z.number().int().nonnegative();
const count = z.number().int().nonnegative();
export const gateSchema = z.object({ code: z.string(), message: z.string(), action: z.string() });
export const ownerSessionSchema = z.discriminatedUnion('owner', [
  z.object({ owner: z.literal(true), csrf_token: z.string().min(1) }),
  z.object({ owner: z.literal(false), csrf_token: z.null() }),
]);
export const ownerLogoutSchema = z.object({ owner: z.literal(false) });
export const targetProvenanceSchema = z.object({
  target_id: z.literal('tryfolk-owned-dodo-special-downloads'), ownership: z.literal('owned_sandbox'), license: z.literal('MIT'),
  license_sha256: z.string().regex(/^[a-f0-9]{64}$/), upstream_commit: z.string().regex(/^[a-f0-9]{40}$/), upstream_url: z.url(),
  source_commit: z.string().regex(/^[a-f0-9]{40}$/), source_digest: z.string().regex(/^[a-f0-9]{64}$/), baseline_target_revision: z.string().regex(/^sha256:[a-f0-9]{64}$/),
  baseline_mode: z.enum(['healthy', 'broken']), baseline_fulfillment_disposition: z.enum(['granted', 'suppressed']),
  controlled_fulfillment_defect: z.boolean(), sandbox: z.literal('dodo_test_mode'),
});
export const provenanceSchema = z.object({
  provider_mode: z.enum(['local_fixture', 'dodo_test']),
  payment_truth: z.enum(['local_fixture', 'dodo_sandbox']),
  cost_basis: z.enum(['non_cfo_estimate', 'cfo_forecast']),
  cfo_plan_status: z.enum(['missing', 'imported']), cfo_plan_url: z.string().nullable(),
  model: z.string(), model_billing: z.literal('existing_codex_allowance'), cash_charged_microusd: z.literal(0),
});
export const ledgerSchema = z.object({ estimated_consumed_microusd: money, held_microusd: money });
const ledgerSnapshotSchema = ledgerSchema.extend({
  available_microusd: money, observed_at: z.string(), evidence_sha256: z.string(),
});
const planBaseSchema = z.object({
  source: z.literal('cfo.ai-inspected-plan'), source_url: z.string(), plan_id: z.string(),
  snapshot_sha256: z.string(), assumptions: z.record(z.string(), money),
});
export const cfoPlanSchema = z.union([
  planBaseSchema.extend({
    schema_version: z.literal(2), status: z.literal('finalized'), currency: z.literal('USD'),
    inspected_at: z.string(), inspection_evidence_sha256: z.string(),
    assumption_provenance: z.record(z.string(), z.object({
      basis: z.literal('forecast'), reference: z.string(), evidence_sha256: z.string(),
    })), native_ledger_snapshot: ledgerSnapshotSchema,
  }),
  planBaseSchema.extend({ schema_version: z.literal(1).default(1) }),
]);
export const quoteBudgetSchema = z.object({
  subtotal_microusd: money, contingency_microusd: money,
  contingency_basis_points: z.number().int().min(0).max(10_000),
  after_contingency_microusd: money, after_reserve_microusd: money,
  after_reserve_includes_known_replay: z.boolean(),
  quote_limit_microusd: money, lifetime_limit_microusd: money, lifetime_available_microusd: money,
  ledger: ledgerSchema,
  execution_allowances: z.record(z.string(), money),
  forecast_comparison: z.object({
    native_subtotal_microusd: money, native_reserved_microusd: money,
    scheduler_overhead_caps: z.record(z.string(), money), native_overhead_caps: z.record(z.string(), money),
    native_ledger_snapshot: ledgerSnapshotSchema, basis: z.string(),
  }).nullable(),
});
const quoteBaseSchema = z.object({
  id: z.string(), app_url: z.string(), persona_count: count, expires_at: z.string(),
  affordable: z.boolean(), ready: z.boolean(), before_microusd: money, after_microusd: money,
  overhead_microusd: money, reserved_microusd: money, available_microusd: money,
  currency: z.literal('USD'), cost_basis: z.enum(['estimate', 'measured']),
  cfo_plan_status: z.enum(['missing', 'imported']), gates: z.array(gateSchema),
  provider: z.string().optional(), revision: z.string().optional(), fault: z.string().optional(),
  target_provenance: targetProvenanceSchema.nullable().optional(),
  provenance: provenanceSchema.optional(), estimate_basis: z.string().optional(),
  cfo_plan: cfoPlanSchema.nullable().optional(),
});
export const quoteV2Schema = quoteBaseSchema.extend(quoteBudgetSchema.shape);
// The additive budget is complete or absent. Old contracts never acquire new zero-valued fields.
export const quoteSchema = z.union([quoteV2Schema, quoteBaseSchema.extend({
  subtotal_microusd: z.undefined().optional(), contingency_microusd: z.undefined().optional(), contingency_basis_points: z.undefined().optional(),
  after_contingency_microusd: z.undefined().optional(), after_reserve_microusd: z.undefined().optional(),
  after_reserve_includes_known_replay: z.undefined().optional(), quote_limit_microusd: z.undefined().optional(),
  lifetime_limit_microusd: z.undefined().optional(), lifetime_available_microusd: z.undefined().optional(),
  ledger: z.undefined().optional(), execution_allowances: z.undefined().optional(), forecast_comparison: z.undefined().optional(),
})]);
export const countsSchema = z.object({
  requested: count, generated: count, queued: count, executed: count, completed: count,
  failed: count, abandoned: count, pending: count, blocked: count,
});
export const personaSchema = z.object({
  id: z.string(), index: count, name: z.string(), profile: z.record(z.string(), z.unknown()),
  conditions: z.record(z.string(), z.unknown()), memory: z.array(z.unknown()),
});
export const eventSchema = z.object({
  id: z.number().int(), persona_id: z.string().nullable(), kind: z.string(), created_at: z.string(),
  data: z.record(z.string(), z.unknown()), trace_url: z.string().nullable(),
});
export const repairSchema = z.object({
  id: z.string(), status: z.enum(['queued', 'running', 'completed', 'blocked', 'failed']),
  base_revision: z.string(), head_revision: z.string().nullable(), preview_url: z.string().nullable(),
  checkpoint_id: z.string().nullable(), checkpoint_url: z.string().nullable(), gates: z.array(gateSchema),
  target_revision: z.string().nullable().optional(), source_digest: z.string().nullable().optional(),
});
export const comparisonSchema = z.object({
  before_run_id: z.string(), after_run_id: z.string(), population_id: z.string(), requested: count,
  before_counts: countsSchema, after_counts: countsSchema, same_personas: z.boolean(),
  known_failure_replay: z.string(), total_spent_microusd: money, unused_microusd: money,
  held_out_checks: z.object({
    total: count, passed: count, failed: count, not_in_population_denominator: z.literal(true),
    cases: z.array(z.object({
      case: z.string(), passed: z.boolean(), revision: z.string(), preview_url: z.string(),
      provider: z.string(), measured: z.boolean(), account_id: z.string(), evidence: z.record(z.string(), z.unknown()),
    })),
  }).optional(),
});
export const runSchema = z.object({
  id: z.string(), app_url: z.string(), persona_count: count, population_id: z.string(),
  parent_run_id: z.string().nullable(), pass: z.enum(['before', 'after']),
  phase: z.enum(['queued', 'discovering', 'generating', 'running', 'verifying', 'completed', 'failed', 'blocked', 'cancelled']),
  revision: z.string(), fault: z.string(), provider: z.string().optional(), preview_url: z.string(), created_at: z.string(), updated_at: z.string(),
  source_commit: z.string().nullable().optional(), source_digest: z.string().nullable().optional(), target_mode: z.string().nullable().optional(),
  target_provenance: targetProvenanceSchema.nullable().optional(),
  counts: countsSchema,
  budget: z.object({
    reserved_microusd: money, spent_microusd: money, unused_microusd: money,
    released_microusd: money, cost_basis: z.string(),
    estimated_consumed_microusd: money.optional(), measured_cash_microusd: money.optional(), cash_basis: z.string().optional(),
  }),
  provenance: provenanceSchema.optional(), budget_contract: quoteSchema.optional(),
  after_reserve_microusd: money.optional(), after_reserve_includes_known_replay: z.boolean().optional(),
  gates: z.array(gateSchema), personas: z.array(personaSchema), events: z.array(eventSchema),
  repair: repairSchema.nullable(), comparison: comparisonSchema.nullable(),
});
export const createdRunSchema = z.object({ id: z.string(), access_token: z.string(), status: runSchema });
export const findingSchema = z.object({
  id: z.string(), run_id: z.string(), persona_id: z.string(), classification: z.string(),
  summary: z.string(), scope: z.string(), evidence: z.record(z.string(), z.unknown()), measured: z.boolean(),
});
export const findingsSchema = z.object({ findings: z.array(findingSchema) });
export const readinessSchema = z.object({
  integrations: z.record(z.string(), z.object({ status: z.string(), detail: z.string(), evidence: z.string().optional() })),
  gates: z.array(gateSchema), target_url: z.string().nullable(), max_personas: count, cfo_plan_status: z.string(),
});
export const connectionSchema = z.object({
  id: z.string().min(1), app_url: z.url(), registration_status: z.enum(['registered', 'unregistered']),
  execution_ready: z.boolean(), provider: z.string(), revision: z.string(),
  source: z.object({
    status: z.string(), repository_label: z.string(), repository_url: z.string().nullable(),
    commit: z.string().nullable(), digest: z.string().nullable(), detail: z.string(),
  }),
  gates: z.array(gateSchema),
});
export const connectionsSchema = z.object({
  version: z.literal('1'), registration_supported: z.boolean(), ownership_scope: z.string(),
  supported_adapters: z.array(z.string()), targets: z.array(connectionSchema),
});
export type Gate = z.infer<typeof gateSchema>;
export type Quote = z.infer<typeof quoteSchema>;
export type QuoteV2 = z.infer<typeof quoteV2Schema>;
export type Counts = z.infer<typeof countsSchema>;
export type Persona = z.infer<typeof personaSchema>;
export type RunEvent = z.infer<typeof eventSchema>;
export type Repair = z.infer<typeof repairSchema>;
export type Run = z.infer<typeof runSchema>;
export type CreatedRun = z.infer<typeof createdRunSchema>;
export type Finding = z.infer<typeof findingSchema>;
export type Readiness = z.infer<typeof readinessSchema>;
export type Provenance = z.infer<typeof provenanceSchema>;
export type TargetProvenance = z.infer<typeof targetProvenanceSchema>;
export type Connection = z.infer<typeof connectionSchema>;
export type Connections = z.infer<typeof connectionsSchema>;
