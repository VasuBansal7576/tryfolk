import assert from 'node:assert/strict';
import test from 'node:test';
import { quoteSchema } from '../src/contracts.ts';
import { hasBudgetDetails, affordabilityMessage, quoteBudget, forecastSource } from '../src/budget.ts';
import { money, findingEvidenceLabel, repairableFinding } from '../src/presentation.ts';

// Contract tests only. These invented inputs never appear in the running app or a result report.
const legacy = {
  id: 'test-only-legacy', app_url: 'http://127.0.0.1:3101', persona_count: 8,
  expires_at: '2020-01-01T00:00:00Z', affordable: true, ready: true,
  before_microusd: 160000, after_microusd: 160000, overhead_microusd: 50000,
  reserved_microusd: 370000, available_microusd: 450000, currency: 'USD', cost_basis: 'estimate',
  cfo_plan_status: 'missing', gates: [],
};
const overhead = { discovery_microusd: 10000, generation_microusd: 10000, investigation_microusd: 10000, repair_microusd: 20000, known_replay_microusd: 10000 };
const v2 = {
  ...legacy, id: 'test-only-v2', affordable: false, overhead_microusd: 60000,
  subtotal_microusd: 380000, contingency_microusd: 76000, contingency_basis_points: 2000,
  after_contingency_microusd: 34000, after_reserve_microusd: 204000, after_reserve_includes_known_replay: true,
  reserved_microusd: 456000, quote_limit_microusd: 500000,
  lifetime_limit_microusd: 2500000, lifetime_available_microusd: 450000,
  ledger: { estimated_consumed_microusd: 2050000, held_microusd: 0 },
  execution_allowances: { ...overhead, before_per_persona_microusd: 20000, after_per_persona_microusd: 20000 },
  forecast_comparison: null,
};

test('historical quote keeps its original price without acquiring contingency or caps', () => {
  const quote = quoteSchema.parse(legacy);
  assert.equal(quote.reserved_microusd, 370000);
  assert.equal(quote.persona_count, 8);
  assert.equal(hasBudgetDetails(quote), false);
  assert.equal(quote.contingency_microusd, undefined);
  assert.equal(quote.quote_limit_microusd, undefined);
  const rendered = quoteBudget(quote, true);
  assert.ok(rendered.includes('$0.37'));
  assert.ok(rendered.includes('does not disclose a separate contingency'));
  assert.ok(!rendered.includes('20%'));
});

test('lifetime headroom and independent quote cap each report their own affordability failure', () => {
  const quote = quoteSchema.parse(v2);
  assert.equal(quote.persona_count, 8);
  assert.equal(quote.reserved_microusd, 456000);
  assert.match(affordabilityMessage(quote), /\$0.45 lifetime remaining/);
  assert.ok(!affordabilityMessage(quote).includes('customer quote cap'));
  const capped = quoteSchema.parse({ ...v2, quote_limit_microusd: 400000, lifetime_available_microusd: 900000 });
  assert.match(affordabilityMessage(capped), /\$0.40 customer quote cap/);
  assert.ok(!affordabilityMessage(capped).includes('lifetime remaining'));
  const both = quoteSchema.parse({ ...v2, quote_limit_microusd: 400000 });
  assert.match(affordabilityMessage(both), /customer quote cap and the \$0.45 lifetime remaining/);
  assert.ok(affordabilityMessage(both).includes('All 8 requested customers remain selected'));
});

test('accepted costs do not change when native assumptions or current balance differ', () => {
  const quote = quoteSchema.parse(v2);
  const before = JSON.stringify(quote);
  const first = quoteBudget(quote, true);
  const second = quoteBudget(quote, true);
  assert.equal(first, second);
  assert.equal(JSON.stringify(quote), before);
  assert.ok(first.includes('$0.456'));
  assert.ok(first.includes('$0.204'));
  assert.ok(first.includes('20%'));
  assert.ok(first.includes('original quote’s ledger snapshot'));
});

test('partial or invalid additive budget fails boundary validation instead of silently losing fields', () => {
  assert.equal(quoteSchema.safeParse({ ...legacy, contingency_microusd: 1 }).success, false);
  const { ledger: omitted, ...incomplete } = v2;
  assert.ok(omitted);
  assert.equal(quoteSchema.safeParse(incomplete).success, false);
  assert.equal(quoteSchema.safeParse({ ...v2, contingency_basis_points: 10001 }).success, false);
  assert.equal(quoteSchema.safeParse({ ...v2, reserved_microusd: -1 }).success, false);
});

test('a native draft link never becomes an active imported forecast', () => {
  const quote = quoteSchema.parse(v2);
  const rendered = forecastSource(quote, 'https://app.cfo.ai/test-only/pages/draft');
  assert.ok(rendered.includes('Cost forecast pending'));
  assert.ok(rendered.includes('No validated native forecast is active'));
  assert.ok(!rendered.includes('finalized, inspected forecast'));
});

test('finalized imported assumptions retain source rows and native-versus-scheduler discrepancy', () => {
  const assumptions = { ...v2.execution_allowances, repair_microusd: 10000, contingency_basis_points: 2000, quote_budget_microusd: 500000, lifetime_budget_microusd: 2500000 };
  const snapshot = { ...v2.ledger, available_microusd: 450000, observed_at: '2026-01-01T00:00:00Z', evidence_sha256: 'a'.repeat(64) };
  const raw = { ...v2, cfo_plan_status: 'imported', cfo_plan: {
    schema_version: 2, source: 'cfo.ai-inspected-plan', source_url: 'https://app.cfo.ai/test-only/pages/test-plan', plan_id: 'test-plan',
    status: 'finalized', currency: 'USD', snapshot_sha256: 'b'.repeat(64), inspected_at: '2026-01-01T00:00:00Z', inspection_evidence_sha256: 'c'.repeat(64),
    assumptions, assumption_provenance: Object.fromEntries(Object.keys(assumptions).map(key => [key, { basis: 'forecast', reference: 'test-only table row ' + key, evidence_sha256: 'd'.repeat(64) }])), native_ledger_snapshot: snapshot,
  }, forecast_comparison: {
    native_subtotal_microusd: 370000, native_reserved_microusd: 444000, scheduler_overhead_caps: overhead,
    native_overhead_caps: { ...overhead, repair_microusd: 10000 }, native_ledger_snapshot: snapshot, basis: 'Test-only native balance comparison',
  } };
  const quote = quoteSchema.parse(raw);
  const rendered = forecastSource(quote);
  assert.ok(rendered.includes('$0.444'));
  assert.ok(rendered.includes('$0.456'));
  assert.ok(rendered.includes('test-only table row repair_microusd'));
  assert.ok(rendered.includes('not another ceiling or deduction'));
  assert.ok(rendered.includes('planning assumptions, not measured cash'));
  assert.equal(quote.persona_count, 8);
  assert.equal(quoteSchema.safeParse({ ...raw, cfo_plan: { ...raw.cfo_plan, status: 'draft' } }).success, false);
});

test('display preserves microdollar precision, including rounded contingency', () => {
  assert.equal(money(1), '$0.000001');
  assert.equal(money(456001), '$0.456001');
});

test('fixture repair review requires completed baseline and preserves nonmeasured provider disclosure', () => {
  const finding = { id: 'test-only', run_id: 'test-only', persona_id: 'test-only', classification: 'test-only', summary: 'Test-only defect', scope: 'test-only', measured: false, evidence: { provider: 'local-fixture' } };
  const baseline = { phase: 'completed', pass: 'before', provider: 'local-fixture' };
  assert.equal(repairableFinding(finding, baseline), true);
  assert.ok(findingEvidenceLabel(finding).includes('provider not measured'));
  assert.equal(finding.measured, false);
  assert.equal(repairableFinding(finding, { ...baseline, phase: 'running' }), false);
  assert.equal(repairableFinding(finding, { ...baseline, pass: 'after' }), false);
  assert.equal(repairableFinding(finding, { ...baseline, provider: 'dodo' }), false);
  assert.equal(repairableFinding({ ...finding, evidence: {} }, baseline), false);
});
