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
export const scenarioSchema = z.object({
  id: z.string(), area: z.string(), title: z.string(), customer_context: z.record(z.string(), z.unknown()),
  hypothetical_problem: z.string(), customer_goal: z.string(), expected_behavior: z.array(z.string()),
  check_kind: z.enum(['customer_journey', 'merchant_event', 'provider_control']),
  availability: z.enum(['runnable', 'requires_fixture', 'provider_owned', 'unsupported_by_target']),
  exercise_status: z.literal('not_exercised'), expected_outcome: z.string(),
  required_capabilities: z.unknown(), provider_support: z.unknown().optional(), source_refs: z.unknown(),
  evidence_requirements: z.unknown(), verification_boundary: z.unknown(),
  risk_priority: count.optional(), selection_ready: z.boolean().optional(), selection_gate: z.string().nullable().optional(),
}).passthrough();
const seedRecordSchema = z.object({
  id: z.string(), profile: z.record(z.string(), z.unknown()), source_record_id: z.string(),
  supported_context: z.record(z.string(), z.array(z.string())),
  eligibility: z.object({ adult: z.boolean(), commercial_use: z.boolean(), quality_passed: z.boolean() }),
}).passthrough();
export const seedBindingSchema = z.object({
  id: z.string(), bank_version: z.string(), bank_sha256: z.string(), record_sha256: z.string(),
  record_snapshot: seedRecordSchema,
  source: z.object({ dataset: z.string(), url: z.string(), revision: z.string(), license: z.literal('CC-BY-4.0'), attribution: z.string() }).passthrough(),
});
const scenarioAssignmentSchema = z.object({
  persona_index: count, catalog_version: z.string(), catalog_sha256: z.string(),
  scenario_id: z.string(), scenario_sha256: z.string(), scenario_snapshot: scenarioSchema,
  scenario_snapshot_json: z.string().optional(), source_refs_snapshot: z.array(z.unknown()).optional(),
  condition_fixture_snapshot: z.record(z.string(), z.string()).optional(), condition_fixture_sha256: z.string().optional(),
  condition_fixture_verified: z.literal(false).optional(), seed: seedBindingSchema.optional(),
});
const draftSelectionRationaleSchema = z.object({
  mode: z.enum(['automatic_capability_risk', 'manual']), policy_version: z.string(), selected_scenario_ids: z.array(z.string()),
  why_selected: z.array(z.object({ scenario_id: z.string(), risk_basis: z.string(), capability_basis: z.array(z.string()), required_fixture_ready: z.boolean(), relevant_customer_context: z.string(), assignment_count: count })),
  omitted_cases: z.array(z.object({ scenario_id: z.string(), reason: z.enum(['budget_limited_N', 'requires_fixture', 'provider_owned', 'unsupported_by_target', 'merchant_event_not_persona']), required_capabilities: z.unknown() })),
  requested_personas: count, reserved_both_passes_microusd: money,
  personality_variation_adds_case_coverage: z.literal(false), whole_catalog_coverage_claimed: z.literal(false),
});
const requiredCapabilitiesSchema = z.object({
  target: z.array(z.string()), provider: z.array(z.string()), conditions: z.record(z.string(), z.string()),
});
const implementedSelectionRationaleSchema = z.object({
  mode: z.enum(['manual', 'automatic_capability_risk']), policy_version: z.string(), selected_case_ids: z.array(z.string()),
  omitted_cases: z.array(z.object({ scenario_id: z.string(), reason: z.string(), required_capabilities: requiredCapabilitiesSchema.optional() })),
  why_selected: z.array(z.object({ scenario_id: z.string(), risk_priority: count,
    capability_basis: requiredCapabilitiesSchema,
    context: z.record(z.string(), z.unknown()), assignment_count: count })),
  library_seed_count: count, personality_variation_is_case_coverage: z.literal(false), whole_catalog_coverage_claimed: z.literal(false),
});
export const selectionRationaleSchema = z.union([implementedSelectionRationaleSchema, draftSelectionRationaleSchema]);
export const scenarioPlanSchema = z.object({
  catalog_version: z.string(), catalog_sha256: z.string(), assignments: z.array(scenarioAssignmentSchema),
  catalog_source_sha256: z.string().optional(),
  requested_personas: count, does_not_imply_exercised_coverage: z.literal(true),
  catalog_case_count: count.optional(), selection_rationale: selectionRationaleSchema.optional(),
});
export const savedScenarioSchema = scenarioAssignmentSchema.extend({
  profile_sha256: z.string(), initial_memory_sha256: z.string(), condition_fixture_sha256: z.string(),
  saved_persona_sha256: z.string().optional(),
  profile_relevance_rationale: z.union([z.string(), z.object({
    basis: z.literal('constrained_TinyTroupe_generation'), customer_goal: z.string(),
    customer_context: z.record(z.string(), z.unknown()), seed_id: z.string(), measured_relevance: z.literal(false),
  })]).optional(),
});
const draftScenarioResultSchema = z.object({
  exercised: z.undefined().optional(),
  journey_outcome: z.undefined().optional(), journey_classification: z.undefined().optional(), exercise_gate: z.undefined().optional(),
  scenario_id: z.string(), classification: z.enum(['not_exercised', 'exercised_fixture', 'exercised_provider', 'expected_provider_decline', 'app_defect', 'provider_owned', 'blocked_inconclusive']),
  evidence_event_ids: z.array(z.number().int()), finding_ids: z.array(z.string()), measured: z.boolean(), provider: z.string().nullable(),
  payment_verified: z.boolean().nullable(), entitlement_verified: z.boolean().nullable(),
  capability_checked: z.boolean(), capability_available: z.boolean().nullable(), condition_fixture_verified: z.boolean(),
});
const implementedScenarioResultSchema = z.object({
  exercised: z.undefined().optional(),
  scenario_id: z.undefined().optional(), provider: z.undefined().optional(), finding_ids: z.undefined().optional(),
  classification: z.literal('not_exercised'), condition_fixture_verified: z.literal(false),
  journey_outcome: z.string().nullable(), journey_classification: z.string().nullable(), measured: z.boolean(),
  payment_verified: z.boolean(), entitlement_verified: z.boolean(), capability_checked: z.boolean(), capability_available: z.boolean().nullable(),
  evidence_event_ids: z.array(z.number().int()), exercise_gate: z.string(),
});
const evidenceBoundScenarioResultSchema = z.object({
  scenario_id: z.string().nullable(), provider: z.string().nullable(), finding_ids: z.array(z.string()),
  classification: z.enum(['not_exercised', 'blocked_inconclusive', 'fulfilled', 'app_defect', 'expected_provider_decline', 'pending']),
  exercised: z.boolean(), condition_fixture_verified: z.boolean(),
  journey_outcome: z.string().nullable(), journey_classification: z.string().nullable(), measured: z.boolean(),
  payment_verified: z.boolean(), entitlement_verified: z.boolean(), capability_checked: z.boolean(), capability_available: z.boolean().nullable(),
  evidence_event_ids: z.array(z.number().int()), exercise_gate: z.string().nullable(),
});
// Legacy records remain readable. An invalid explicit exercise flag cannot fall
// through to a legacy variant and silently lose the evidence-binding fields.
export const scenarioResultSchema = z.union([evidenceBoundScenarioResultSchema, implementedScenarioResultSchema, draftScenarioResultSchema]);
export const scenarioCoverageSchema = z.object({
  catalog_case_count: count, requested_personas: count, assigned_case_ids: z.array(z.string()), exercised_case_ids: z.array(z.string()),
  cases: z.array(z.object({ persona_id: z.string(), assigned_scenario: savedScenarioSchema.nullable(), observed_scenario_result: scenarioResultSchema.nullable() })),
  counting_basis: z.literal('actual_saved_persona_journeys'), whole_catalog_coverage_claimed: z.literal(false),
});
export const scenarioCatalogSchema = z.object({ catalog_version: z.string(), catalog_sha256: z.string(), scenarios: z.array(scenarioSchema), catalog_case_count: count.optional(), does_not_imply_exercised_coverage: z.literal(true).optional() });
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
  scenario_plan: scenarioPlanSchema.nullable().optional(),
});
export const quoteV2Schema = quoteBaseSchema.extend(quoteBudgetSchema.shape);
// The additive budget is complete or absent. Old contracts never acquire new zero-valued fields.
const ordinaryQuoteDataSchema = z.union([quoteV2Schema, quoteBaseSchema.extend({
  subtotal_microusd: z.undefined().optional(), contingency_microusd: z.undefined().optional(), contingency_basis_points: z.undefined().optional(),
  after_contingency_microusd: z.undefined().optional(), after_reserve_microusd: z.undefined().optional(),
  after_reserve_includes_known_replay: z.undefined().optional(), quote_limit_microusd: z.undefined().optional(),
  lifetime_limit_microusd: z.undefined().optional(), lifetime_available_microusd: z.undefined().optional(),
  ledger: z.undefined().optional(), execution_allowances: z.undefined().optional(), forecast_comparison: z.undefined().optional(),
})]);
export const retryBudgetSchema = z.object({
  historical_spent_microusd: money, original_reserved_microusd: money, release_microusd: money,
  available_before_release_microusd: money, available_after_release_microusd: money,
  new_reserved_microusd: money, available_after_reserve_microusd: z.number().int(),
  measured_cash_microusd: z.literal(0), cost_basis: z.literal('estimate'),
}).refine(budget => budget.release_microusd === budget.original_reserved_microusd - budget.historical_spent_microusd
  && budget.available_after_release_microusd === budget.available_before_release_microusd + budget.release_microusd
  && budget.available_after_reserve_microusd === budget.available_after_release_microusd - budget.new_reserved_microusd,
'Retry budget must retain historical use and disclose the exact hold release and new reservation');
export const baselineRetryQuoteSchema = ordinaryQuoteDataSchema.and(z.object({
  kind: z.literal('baseline_retry'), retry_of_run_id: z.string().min(1), population_id: z.string().min(1),
  population_sha256: z.string().regex(/^[a-f0-9]{64}$/), persona_ids: z.array(z.string().min(1)), retry_budget: retryBudgetSchema,
})).refine(quote => quote.persona_ids.length === quote.persona_count
  && new Set(quote.persona_ids).size === quote.persona_count
  && quote.retry_budget.new_reserved_microusd === quote.reserved_microusd,
'Retry quote must retain the full saved population and its new reservation');
// A malformed explicit retry must never fall through and lose its budget/lineage.
export const quoteSchema = z.union([baselineRetryQuoteSchema, ordinaryQuoteDataSchema.and(z.object({
  kind: z.undefined().optional(), retry_of_run_id: z.undefined().optional(), population_id: z.undefined().optional(),
  population_sha256: z.undefined().optional(), persona_ids: z.undefined().optional(), retry_budget: z.undefined().optional(),
}))]);
export const countsSchema = z.object({
  requested: count, generated: count, queued: count, executed: count, completed: count,
  failed: count, abandoned: count, pending: count, blocked: count,
});
export const personaSchema = z.object({
  id: z.string(), index: count, name: z.string(), profile: z.record(z.string(), z.unknown()),
  conditions: z.record(z.string(), z.unknown()), memory: z.array(z.unknown()),
  initial_semantic_memory: z.array(z.unknown()).optional(), initial_snapshot: z.unknown().optional(),
  assigned_scenario: savedScenarioSchema.nullable().optional(),
});
export const eventSchema = z.object({
  id: z.number().int(), persona_id: z.string().nullable(), kind: z.string(), created_at: z.string(),
  data: z.record(z.string(), z.unknown()), trace_url: z.string().nullable(),
});
const checkpointResourceSchema = z.object({
  object: z.string().regex(/^[a-f0-9]{40}$/), path: z.string().min(1),
  bytes: count, sha256: z.string().regex(/^[a-f0-9]{64}$/),
});
const checkpointAccessSchema = z.object({
  kind: z.literal('github_git_checkpoint'), status: z.literal('anonymous_verified'),
  repository: z.string().min(1), checkpoint_id: z.string().min(1), checkpoint_ref: z.string().min(1),
  checkpoint_object: z.string().regex(/^[a-f0-9]{40}$/), source_commit: z.string().regex(/^[a-f0-9]{40}$/),
  url: z.string(), transcript_url: z.string(), source_url: z.string(),
  binding_sha256: z.string().regex(/^[a-f0-9]{64}$/), receipt_sha256: z.string().regex(/^[a-f0-9]{64}$/),
  verified_at: z.iso.datetime({ offset: true }), resource_digests: z.array(checkpointResourceSchema).length(4),
  hosted_entire_url: z.null(), hosted_entire_status: z.literal('unverified'),
}).refine(access => {
  const raw = (object: string, path: string) => `https://raw.githubusercontent.com/${access.repository}/${object}/${path}`;
  return /^[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(access.repository)
    && access.url === raw(access.checkpoint_object, 'metadata.json')
    && ['0/full.jsonl', '0/transcript.jsonl'].some(path => access.transcript_url === raw(access.checkpoint_object, path))
    && access.source_url === raw(access.source_commit, 'lib/fulfillment-policy.ts');
}, 'Checkpoint links must match the recorded immutable repository and objects');
export const repairSchema = z.object({
  id: z.string(), status: z.enum(['queued', 'running', 'completed', 'blocked', 'failed']),
  base_revision: z.string(), head_revision: z.string().nullable(), preview_url: z.string().nullable(),
  checkpoint_id: z.string().nullable(), checkpoint_url: z.string().nullable(), gates: z.array(gateSchema),
  target_revision: z.string().nullable().optional(), source_digest: z.string().nullable().optional(),
  checkpoint_access: checkpointAccessSchema.nullable().optional(),
}).refine(repair => !repair.checkpoint_access || (
  repair.checkpoint_access.checkpoint_id === repair.checkpoint_id
  && repair.checkpoint_access.source_commit === repair.head_revision
  && repair.checkpoint_access.url === repair.checkpoint_url
), 'Checkpoint access must belong to this registered repair');
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
export const baselineRetryLineageSchema = z.object({
  original_run_id: z.string().min(1), retry_run_id: z.string().min(1), population_id: z.string().min(1),
  population_sha256: z.string().regex(/^[a-f0-9]{64}$/), persona_ids: z.array(z.string().min(1)),
  approval_sha256: z.string().regex(/^[a-f0-9]{64}$/), accepted_at: z.iso.datetime({ offset: true }),
  retry_budget: retryBudgetSchema, history_excluded_from_retry_denominator: z.literal(true),
  payment_safety_basis: z.enum(['owner_reviewed_pre_submission_evidence_not_provider_receipt', 'server_recorded_pre_submission_evidence_not_provider_receipt']),
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
  scenario_plan: scenarioPlanSchema.nullable().optional(), scenario_coverage: scenarioCoverageSchema.optional(),
  after_reserve_microusd: money.optional(), after_reserve_includes_known_replay: z.boolean().optional(),
  gates: z.array(gateSchema), personas: z.array(personaSchema), events: z.array(eventSchema),
  repair: repairSchema.nullable(), comparison: comparisonSchema.nullable(),
  retry_of_run_id: z.string().min(1).optional(), baseline_retry: baselineRetryLineageSchema.optional(), retry_budget: retryBudgetSchema.optional(),
}).refine(current => {
  const lineage = current.baseline_retry;
  if (!lineage) return !current.retry_of_run_id && !current.retry_budget && current.budget_contract?.kind !== 'baseline_retry';
  if (current.provider !== 'dodo' || lineage.original_run_id === lineage.retry_run_id
    || lineage.retry_budget.available_after_reserve_microusd < 0
    || lineage.population_id !== current.population_id || lineage.persona_ids.length !== current.persona_count
    || new Set(lineage.persona_ids).size !== current.persona_count
    || current.personas.length !== current.persona_count
    || current.personas.some((person, index) => person.id !== lineage.persona_ids[index])) return false;
  if (current.id === lineage.original_run_id) return current.pass === 'before' && !current.retry_of_run_id && !current.retry_budget && !current.comparison;
  const quote = current.budget_contract;
  if (current.retry_of_run_id !== lineage.original_run_id || !current.retry_budget
    || JSON.stringify(current.retry_budget) !== JSON.stringify(lineage.retry_budget)
    || current.retry_budget.available_after_reserve_microusd < 0
    || current.budget.reserved_microusd !== current.retry_budget.new_reserved_microusd
    || quote?.kind !== 'baseline_retry' || quote.retry_of_run_id !== lineage.original_run_id
    || quote.population_sha256 !== lineage.population_sha256 || quote.population_id !== current.population_id
    || quote.persona_count !== current.persona_count || quote.persona_ids.length !== lineage.persona_ids.length
    || quote.reserved_microusd !== current.retry_budget.new_reserved_microusd
    || quote.persona_ids.some((id, index) => id !== lineage.persona_ids[index])) return false;
  if (current.comparison && (current.comparison.before_run_id !== lineage.retry_run_id
    || current.comparison.population_id !== current.population_id || current.comparison.requested !== current.persona_count)) return false;
  return current.pass === 'before' ? current.id === lineage.retry_run_id && current.parent_run_id === null
    : current.parent_run_id === lineage.retry_run_id && (!current.comparison || current.comparison.after_run_id === current.id);
}, 'Baseline retry lineage must preserve its full population, separate attempt and accepted reservation');
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
export type BaselineRetryQuote = z.infer<typeof baselineRetryQuoteSchema>;
export type RetryBudget = z.infer<typeof retryBudgetSchema>;
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
export type Scenario = z.infer<typeof scenarioSchema>;
export type SavedScenario = z.infer<typeof savedScenarioSchema>;
export type ScenarioResult = z.infer<typeof scenarioResultSchema>;
export type ScenarioCatalog = z.infer<typeof scenarioCatalogSchema>;
