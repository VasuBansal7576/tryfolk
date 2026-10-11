import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { z } from 'zod';

const catalogPath = new URL('../scenarios/payment-catalog.json', import.meta.url);
const text = z.string().trim().min(1);
const caseSchema = z.object({
  id: text,
  area: text,
  title: text,
  customer_context: z.object({
    background: text, account_history: text, device: text,
    locale: text, billing_country: text, constraint: text,
  }),
  hypothetical_problem: text,
  customer_goal: text,
  expected_behavior: z.array(text).min(1),
  check_kind: z.enum(['customer_journey', 'merchant_event', 'provider_control']),
  availability: z.enum(['runnable', 'requires_fixture', 'provider_owned', 'unsupported_by_target']),
  exercise_status: z.literal('not_exercised'),
  required_capabilities: z.object({
    target: z.array(text).min(1), provider: z.array(text).min(1),
    condition: z.array(text).min(1), missing: z.array(text),
  }),
  provider_support: z.object({
    status: z.enum(['documented', 'conditional', 'not_established']),
    claim: text, limitations: z.array(text), source_refs: z.array(text).min(1),
  }),
  source_refs: z.array(text).min(1),
  evidence_requirements: z.array(text).min(1),
  verification_boundary: z.object({
    pass: z.array(text).min(1), limits: z.array(text).min(1),
    app_defect_rule: text, nonpayment_is_app_defect: z.literal(false),
  }),
  expected_outcome: z.enum(['fulfilled', 'expected_decline', 'rejected', 'pending', 'lifecycle_policy', 'provider_control']),
});

test('a selected payment case distinguishes a hypothetical goal from observed coverage', () => {
  const catalog = z.object({
    input_semantics: text,
    coverage_policy: z.object({ catalog_entries_are_observed_results: z.boolean(), denominator: text }),
    scenarios: z.array(caseSchema),
  }).parse(JSON.parse(readFileSync(catalogPath, 'utf8')));
  const purchase = catalog.scenarios.find(scenario => scenario.id === 'one-time-card-download');
  assert.ok(purchase, 'the existing one-time download journey must be selectable');
  assert.equal(purchase.exercise_status, 'not_exercised');
  assert.equal(catalog.input_semantics, 'hypothetical_test_inputs');
  assert.equal(catalog.coverage_policy.catalog_entries_are_observed_results, false);
  assert.equal(catalog.coverage_policy.denominator, 'selected_accepted_quote_assignments');
});

test('accepted replay and small selections cannot drift into new personas or whole-catalog claims', () => {
  const catalog = z.object({
    replay_policy: z.object({
      freeze_at: z.literal('quote_acceptance'),
      immutable_fields: z.array(text),
      restore_exactly: z.array(text),
      fresh_identifiers: z.array(text),
      accepted_quote_may_change: z.literal(false),
    }),
    selection_policy: z.object({
      demographic_payment_predictions: z.literal(false),
      requires_registered_condition: z.literal(true),
      whole_catalog_coverage_from_N: z.literal(false),
      exercised_ids_separate_from_assigned_ids: z.literal(true),
      merchant_events_count_as_browser_personas: z.literal(false),
    }),
    scenarios: z.array(caseSchema.extend({
      risk: z.object({ priority: z.enum(['high', 'medium']), consequence: text }),
      selection_rationale: text,
      condition_fixture: z.object({ id: text, registration: z.enum(['existing_driver', 'not_registered']) }),
    })),
  }).parse(JSON.parse(readFileSync(catalogPath, 'utf8')));
  for (const name of ['scenario_snapshot', 'profile_snapshot', 'initial_memory_snapshot', 'condition_fixture_snapshot', 'accepted_quote_snapshot']) {
    assert.ok(catalog.replay_policy.immutable_fields.includes(name), `accepted ${name} must be frozen`);
  }
  assert.deepEqual(catalog.replay_policy.restore_exactly, ['scenario_snapshot', 'profile_snapshot', 'initial_memory_snapshot', 'condition_fixture_snapshot']);
  assert.ok(catalog.replay_policy.fresh_identifiers.includes('account_id'));
  assert.ok(catalog.replay_policy.fresh_identifiers.includes('checkout_session_id'));
  for (const scenario of catalog.scenarios) {
    if (scenario.availability === 'runnable') assert.equal(scenario.condition_fixture.registration, 'existing_driver');
  }
});

test('requested cases expose honest prerequisites and independent verification boundaries', () => {
  const catalog = z.object({
    sources: z.record(text, z.object({ kind: text })),
    readiness_policy: z.object({
      execution_authorized_by_catalog: z.literal(false),
      gates: z.array(text).min(1),
    }),
    scenarios: z.array(caseSchema).min(1),
  }).parse(JSON.parse(readFileSync(catalogPath, 'utf8')));
  const ids = catalog.scenarios.map(scenario => scenario.id);
  assert.equal(new Set(ids).size, ids.length, 'scenario IDs must be unambiguous in saved quotes');
  const requestedAreas = [
    'gateway_integration', 'payment_methods', 'checkout_security', 'fraud_signals',
    'disputes_chargebacks', 'pci_integration', 'international_currency', 'localization',
    'tax_display', 'refunds', 'subscriptions', 'renewal', 'cancellation',
    'invoice_receipt', 'webhooks_events', 'paid_without_access',
  ];
  for (const area of requestedAreas) {
    assert.ok(catalog.scenarios.some(scenario => scenario.area === area), `missing requested area ${area}`);
  }
  for (const scenario of catalog.scenarios) {
    for (const ref of [...scenario.source_refs, ...scenario.provider_support.source_refs]) {
      assert.ok(catalog.sources[ref], `${scenario.id} has an unresolved evidence source ${ref}`);
    }
    for (const ref of scenario.provider_support.source_refs) {
      assert.ok(['official_docs', 'official_sdk'].includes(catalog.sources[ref].kind), `${scenario.id} must use primary provider evidence`);
    }
    if (scenario.availability === 'runnable') {
      assert.equal(scenario.check_kind, 'customer_journey');
      assert.equal(scenario.required_capabilities.missing.length, 0);
      assert.equal(scenario.customer_context.device, 'desktop Chromium');
      assert.equal(scenario.customer_context.locale, 'en-US');
      assert.equal(scenario.customer_context.billing_country, 'US');
    } else {
      assert.ok(scenario.required_capabilities.missing.length > 0, `${scenario.id} must explain its execution gap`);
    }
    if (scenario.check_kind === 'provider_control') assert.equal(scenario.availability, 'provider_owned');
    if (scenario.expected_outcome === 'expected_decline') assert.equal(scenario.verification_boundary.nonpayment_is_app_defect, false);
  }
});
