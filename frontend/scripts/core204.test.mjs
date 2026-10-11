import assert from 'node:assert/strict';
import test from 'node:test';
import { runSchema, scenarioCatalogSchema, scenarioResultSchema } from '../src/contracts.ts';
import { renderPersonaDetail, renderSeedProvenance } from '../src/persona-detail.ts';
import { renderScenarioCoverage, renderSelectionRationale } from '../src/scenario-coverage.ts';
import { redact } from '../src/presentation.ts';
import { person, savedRun } from './persona-inputs.mjs';

globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const scenario = {
  id: 'test-only-core204', area: 'test-only-payment', title: 'TEST ONLY saved case',
  customer_context: { goal_context: 'TEST ONLY artwork purchase' }, hypothetical_problem: 'TEST ONLY hypothetical access gap',
  customer_goal: 'TEST ONLY purchase goal', expected_behavior: ['TEST ONLY fulfilled'],
  check_kind: 'customer_journey', availability: 'runnable', exercise_status: 'not_exercised',
  required_capabilities: { target: ['TEST ONLY download'], provider: ['TEST ONLY payment'], conditions: { billing_country: 'TEST ONLY explicit fixture country' } },
  source_refs: [], evidence_requirements: [], verification_boundary: { scope: 'TEST ONLY condition binding' }, expected_outcome: 'fulfilled', risk_priority: 3,
};
const seed = {
  id: 'test-only-seed', bank_version: 'test-only-bank-v1', bank_sha256: 'test-only-bank-hash', record_sha256: 'test-only-record-hash',
  record_snapshot: { id: 'test-only-seed', profile: { arts: 'TEST ONLY seed art context' }, source_record_id: 'test-only-row',
    supported_context: { billing_country: ['TEST ONLY fixture eligibility'] }, eligibility: { adult: true, commercial_use: true, quality_passed: true },
    source_row_sha256: 'test-only-source-row-hash', normalized_sha256: 'test-only-normalized-hash',
    selection_rationale: 'TEST ONLY seed relevance reason', changes: ['TEST ONLY normalization'], provider_conditions_established: false },
  source: { dataset: 'TEST ONLY synthetic dataset', url: 'https://example.invalid/test-only-dataset', revision: 'test-only-source-revision', license: 'CC-BY-4.0', attribution: 'TEST ONLY attribution' },
};
const assignment = { persona_index: 0, catalog_version: 'test-only-catalog-v1', catalog_sha256: 'test-only-catalog-hash',
  scenario_id: scenario.id, scenario_sha256: 'test-only-case-hash', scenario_snapshot: scenario,
  scenario_snapshot_json: JSON.stringify(scenario), source_refs_snapshot: [{ source: 'TEST ONLY accepted source reference' }],
  condition_fixture_snapshot: { billing_country: 'TEST ONLY explicit fixture country' }, condition_fixture_sha256: 'test-only-condition-hash', condition_fixture_verified: false, seed,
  saved_persona_sha256: 'test-only-saved-person-hash', profile_sha256: 'test-only-profile-hash', initial_memory_sha256: 'test-only-memory-hash',
  profile_relevance_rationale: { basis: 'constrained_TinyTroupe_generation', customer_goal: 'TEST ONLY generated purchase goal', customer_context: { relevance: 'TEST ONLY constrained profile context' }, seed_id: seed.id, measured_relevance: false } };
const rationale = { mode: 'automatic_capability_risk', policy_version: 'supported-risk-seed-v1', selected_case_ids: [scenario.id],
  omitted_cases: [{ scenario_id: 'test-only-omitted', reason: 'requires_fixture' }], why_selected: [{ scenario_id: scenario.id,
    risk_priority: 3, capability_basis: scenario.required_capabilities, context: scenario.customer_context, assignment_count: 1 }],
  library_seed_count: 2001, personality_variation_is_case_coverage: false, whole_catalog_coverage_claimed: false };
const observed = { classification: 'not_exercised', condition_fixture_verified: false, journey_outcome: 'failed', journey_classification: 'paid_without_access',
  measured: false, payment_verified: true, entitlement_verified: false, capability_checked: true, capability_available: false, evidence_event_ids: [1], exercise_gate: 'TEST ONLY scenario fixture binding not implemented' };
const makeRun = (extra = {}) => savedRun({ personas: [{ ...person, profile: { goal: 'TEST ONLY actual generated profile goal' }, assigned_scenario: assignment }],
  scenario_plan: { catalog_version: 'test-only-catalog-v1', catalog_sha256: 'test-only-catalog-hash', catalog_source_sha256: 'test-only-catalog-source-hash', catalog_case_count: 2, requested_personas: 1,
    assignments: [assignment], does_not_imply_exercised_coverage: true, selection_rationale: rationale },
  scenario_coverage: { catalog_case_count: 2, requested_personas: 1, assigned_case_ids: [scenario.id], exercised_case_ids: [],
    counting_basis: 'actual_saved_persona_journeys', whole_catalog_coverage_claimed: false,
    cases: [{ persona_id: person.id, assigned_scenario: assignment, observed_scenario_result: observed }] },
  events: [{ id: 1, persona_id: person.id, kind: 'verification', created_at: '2020-01-01', data: { outcome: 'failed', payment_verified: true, entitlement_verified: false }, trace_url: null }], ...extra });

test('implemented core204 records parse and display frozen seed/source hashes and structured rationale, separately from the actual profile', () => {
  const run = makeRun(), before = structuredClone(run);
  const html = renderPersonaDetail(run, run.personas[0]);
  assert.ok(html.includes('test-only-source-revision'));
  assert.ok(html.includes('test-only-source-row-hash'));
  assert.ok(html.includes('test-only-record-hash'));
  assert.ok(html.includes('test-only-catalog-source-hash'));
  assert.ok(html.includes('TEST ONLY accepted source reference'));
  assert.equal(run.personas[0].assigned_scenario.scenario_snapshot_json, JSON.stringify(scenario));
  assert.ok(html.includes('TEST ONLY attribution'));
  assert.ok(html.includes('TEST ONLY constrained profile context'));
  assert.ok(html.includes('TEST ONLY actual generated profile goal'));
  assert.ok(!html.includes('[object Object]'));
  assert.ok(html.includes('Synthetic seed context · not a generated customer or observed result'));
  assert.ok(html.includes('TEST ONLY scenario fixture binding not implemented'));
  assert.deepEqual(run, before);
});

test('core204 N1 assignment and real journey checks remain unexercised case coverage and bank size stays an input count', () => {
  const run = makeRun();
  const catalog = scenarioCatalogSchema.parse({ catalog_version: 'test-only-new-catalog', catalog_sha256: 'test-only-new-hash', catalog_case_count: 2,
    does_not_imply_exercised_coverage: true, scenarios: [{ ...scenario, title: 'NEW-CATALOG-MUST-NOT-REPLACE-ACCEPTED' }, { ...scenario, id: 'test-only-omitted', selection_ready: false, selection_gate: 'TEST ONLY missing fixture' }] });
  const html = renderScenarioCoverage(run, { kind: 'ready', catalog });
  assert.ok(html.includes('Not exercised · journey failed'));
  assert.ok(html.includes('Journey checks only · scenario conditions not verified'));
  assert.ok(html.includes('Payment: Verified'));
  assert.ok(html.includes('Access: Not verified'));
  assert.ok(html.includes('2 cases without recorded exercised coverage'));
  assert.ok(html.includes('2,001 source seeds are context inputs, not tested customers or case coverage'));
  assert.ok(html.includes('TEST ONLY missing fixture'));
  assert.ok(!html.includes('NEW-CATALOG-MUST-NOT-REPLACE-ACCEPTED'));
  assert.equal(scenarioResultSchema.safeParse({ ...observed, classification: 'exercised_provider' }).success, false);
});

test('core204 planned seeds at generated0 cannot become saved persona profiles or executed coverage', () => {
  const run = makeRun();
  const pending = makeRun({ personas: [], events: [], counts: { ...run.counts, generated: 0 }, scenario_coverage: { ...run.scenario_coverage, cases: [] } });
  const html = renderScenarioCoverage(pending, { kind: 'unavailable', message: 'TEST ONLY no catalog' });
  assert.ok(html.includes('1 planned · no saved profile yet'));
  assert.ok(!html.includes('data-action="person"'));
  assert.ok(!html.includes('TEST ONLY actual generated profile goal'));
  assert.equal(runSchema.parse(pending).personas.length, 0);
});

test('frozen before/after seed provenance is unaffected by current catalog or bank updates and malicious source text stays data', () => {
  const before = makeRun(), after = makeRun({ id: 'test-only-after', parent_run_id: before.id, pass: 'after' });
  assert.deepEqual(after.personas[0].assigned_scenario, before.personas[0].assigned_scenario);
  const payload = '<img src=x onerror="testOnlyXss=1">';
  const unsafe = { ...seed, source: { ...seed.source, attribution: payload, url: 'javascript:testOnlyXss=1' } };
  const html = renderSeedProvenance(unsafe);
  assert.ok(html.includes('&lt;img src=x'));
  assert.ok(!html.includes('<img src=x'));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(renderSeedProvenance(undefined).includes('Unknown'));
  assert.ok(renderSeedProvenance({ ...seed, id: 'test-only-mismatched-binding' }).includes('Unknown'));
  assert.ok(renderSelectionRationale(before.scenario_plan).includes('TEST ONLY artwork purchase'));
});

test('contract212 canonical scenario JSON stays escaped and credential fields are redacted before displaying parsed values', () => {
  const run = makeRun();
  run.personas[0].assigned_scenario.scenario_snapshot_json = JSON.stringify({ goal: '<img src=x onerror="testOnlyXss=1">', api_key: 'TEST ONLY FIELD THAT MUST BE REDACTED' });
  const saved = structuredClone(run);
  const html = renderPersonaDetail(run, run.personas[0]);
  assert.ok(html.includes('Saved canonical scenario record'));
  assert.ok(!html.includes('<img src=x'));
  assert.ok(!html.includes('TEST ONLY FIELD THAT MUST BE REDACTED'));
  assert.ok(html.includes('[redacted]'));
  assert.ok(!JSON.stringify(redact(run)).includes('TEST ONLY FIELD THAT MUST BE REDACTED'));
  assert.deepEqual(run, saved);
  run.personas[0].assigned_scenario.scenario_snapshot_json = 'TEST ONLY INVALID JSON';
  assert.ok(renderPersonaDetail(run, run.personas[0]).includes('saved canonical scenario record is unavailable'));
});
