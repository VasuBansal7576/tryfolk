import assert from 'node:assert/strict';
import test from 'node:test';
import { scenarioCatalogSchema, scenarioResultSchema } from '../src/contracts.ts';
import { renderPersonaDetail } from '../src/persona-detail.ts';
import { renderScenarioCoverage, scenarioExercised } from '../src/scenario-coverage.ts';
import { person, savedRun } from './persona-inputs.mjs';

globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const scenario = {
  id: 'test-only-case', area: 'test-only-payment', title: 'Test-only frozen case',
  customer_context: { billing_country: 'Test-only country', background: 'Test-only context' },
  hypothetical_problem: 'Test-only hypothetical missing access', customer_goal: 'Test-only download goal',
  expected_behavior: ['Test-only expected access'], check_kind: 'customer_journey',
  availability: 'runnable', exercise_status: 'not_exercised', expected_outcome: 'fulfilled',
  required_capabilities: ['Test-only fixture'], provider_support: 'not_established', source_refs: [],
  evidence_requirements: ['Test-only payment and access'], verification_boundary: 'Test-only boundary',
};
const assignment = {
  persona_index: 0, catalog_version: 'test-only-v1', catalog_sha256: 'test-only-catalog-hash',
  scenario_id: scenario.id, scenario_sha256: 'test-only-case-hash', scenario_snapshot: scenario,
  profile_sha256: 'test-only-profile-hash', initial_memory_sha256: 'test-only-memory-hash', condition_fixture_sha256: 'test-only-conditions-hash',
  profile_relevance_rationale: 'Test-only generated-profile relevance',
};
const result = scenarioResultSchema.parse({
  scenario_id: scenario.id, classification: 'app_defect', evidence_event_ids: [1], finding_ids: [],
  measured: true, provider: 'test-only', payment_verified: true, entitlement_verified: false,
  capability_checked: true, capability_available: false, condition_fixture_verified: true,
});
const makeRun = (extra = {}) => savedRun({
  personas: [{ ...person, assigned_scenario: assignment }],
  scenario_plan: { catalog_version: 'test-only-v1', catalog_sha256: 'test-only-catalog-hash', assignments: [assignment], requested_personas: 1, does_not_imply_exercised_coverage: true,
    selection_rationale: { mode: 'automatic_capability_risk', policy_version: 'test-only-policy', selected_scenario_ids: [scenario.id], why_selected: [{ scenario_id: scenario.id, risk_basis: 'Test-only relevant risk', capability_basis: ['Test-only actual fixture'], required_fixture_ready: true, relevant_customer_context: 'Test-only relevant context', assignment_count: 1 }], omitted_cases: [{ scenario_id: 'test-only-provider-case', reason: 'provider_owned', required_capabilities: ['Test-only provider control'] }], requested_personas: 1, reserved_both_passes_microusd: 120000, personality_variation_adds_case_coverage: false, whole_catalog_coverage_claimed: false } },
  scenario_coverage: { catalog_case_count: 3, requested_personas: 1, assigned_case_ids: [scenario.id], exercised_case_ids: [scenario.id], cases: [{ persona_id: person.id, assigned_scenario: assignment, observed_scenario_result: result }], counting_basis: 'actual_saved_persona_journeys', whole_catalog_coverage_claimed: false },
  events: [{ id: 1, persona_id: person.id, kind: 'verification', created_at: '2020-01-01', data: { outcome: 'failed' }, trace_url: null }], ...extra,
});
const catalog = scenarioCatalogSchema.parse({ catalog_version: 'test-only-v2', catalog_sha256: 'test-only-new-catalog-hash', scenarios: [{ ...scenario, title: 'NEW-CATALOG-MUST-NOT-REPLACE-SAVED-CASE' }, { ...scenario, id: 'test-only-provider-case', title: 'Test-only provider challenge', availability: 'provider_owned', check_kind: 'provider_control' }, { ...scenario, id: 'test-only-unassigned-case', title: 'Test-only unsupported device', availability: 'requires_fixture' }] });

test('N1 coverage preserves the frozen case, shows omitted/provider-owned/unexercised cases and does not inflate the population', () => {
  const run = makeRun(), before = structuredClone(run);
  const html = renderScenarioCoverage(run, { kind: 'ready', catalog });
  assert.ok(html.includes('Test-only frozen case'));
  assert.ok(!html.includes('NEW-CATALOG-MUST-NOT-REPLACE-SAVED-CASE'));
  assert.ok(html.includes('Provider-owned'));
  assert.ok(html.includes('Not assigned'));
  assert.ok(html.includes('Not exercised'));
  assert.ok(html.includes('3 cases without recorded exercised coverage'));
  assert.ok(html.includes('Payment: Verified'));
  assert.ok(html.includes('Access: Not verified'));
  assert.ok(html.includes('original 1 requested customers'));
  assert.deepEqual(run, before);
});

test('legacy classes, condition flags and linked events cannot supply a missing authoritative exercise flag', () => {
  const run = makeRun();
  assert.equal(scenarioExercised(result), false);
  assert.equal(scenarioExercised({ ...result, classification: 'expected_provider_decline' }), false);
  const html = renderScenarioCoverage(run, { kind: 'ready', catalog });
  assert.ok(html.includes('Unknown · exercise not reported'));
  assert.ok(html.includes('3 cases without recorded exercised coverage'));
  const missing = renderScenarioCoverage(makeRun({ events: [] }), { kind: 'ready', catalog });
  assert.ok(missing.includes('The reported evidence event is not present in this run view'));
  assert.ok(missing.includes('service-reported checks'));
});

test('selection and profile relevance are saved rationale, expected declines stay expected, and missing legacy rationale stays unknown', () => {
  const run = makeRun();
  const html = renderPersonaDetail(run, run.personas[0]);
  assert.ok(html.includes('Test-only generated-profile relevance'));
  assert.ok(html.includes('Test-only relevant risk'));
  assert.ok(html.includes('Reported ready at selection time · not proof of execution'));
  assert.ok(html.indexOf('Hypothetical input · not an observed failure') < html.indexOf('Observed in this run'));
  const decline = makeRun();
  decline.scenario_coverage.cases[0].observed_scenario_result = { ...result, classification: 'expected_provider_decline', payment_verified: false };
  const coverage = renderScenarioCoverage(decline, { kind: 'ready', catalog });
  assert.ok(coverage.includes('Expected provider decline'));
  assert.ok(!coverage.includes('App defect'));
  const legacy = renderScenarioCoverage(savedRun(), { kind: 'unavailable', message: 'Test-only missing route' });
  assert.ok(legacy.includes('Selection rationale: Unknown'));
  assert.ok(legacy.includes('no category coverage is inferred'));
});

test('planned assignments at generated=0 do not manufacture a customer profile or observed evidence', () => {
  const current = makeRun();
  const run = makeRun({ personas: [], events: [], counts: { ...current.counts, generated: 0 }, scenario_coverage: { ...current.scenario_coverage, cases: [], exercised_case_ids: [] } });
  const html = renderScenarioCoverage(run, { kind: 'ready', catalog });
  assert.ok(html.includes('1 planned · no saved profile yet'));
  assert.ok(!html.includes('data-action="person"'));
  assert.ok(!html.includes('Payment: Verified'));
});

test('unsafe catalog/rationale strings are escaped and unsupported whole-catalog success claims fail boundary validation', () => {
  const payload = '<script>window.testOnlyXss=1</script>';
  const run = makeRun();
  run.scenario_plan.selection_rationale.why_selected[0].risk_basis = payload;
  const html = renderScenarioCoverage(run, { kind: 'ready', catalog: { ...catalog, scenarios: [...catalog.scenarios, { ...scenario, id: 'test-only-xss', title: payload, customer_context: { background: payload } }] } });
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.throws(() => makeRun({ scenario_coverage: { ...run.scenario_coverage, whole_catalog_coverage_claimed: true } }));
});
