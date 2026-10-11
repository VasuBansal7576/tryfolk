import assert from 'node:assert/strict';
import test from 'node:test';
import { runSchema, scenarioResultSchema } from '../src/contracts.ts';
import { renderPersonaDetail } from '../src/persona-detail.ts';
import { renderScenarioCoverage, renderSelectionRationale, scenarioExercised } from '../src/scenario-coverage.ts';
import { makeRun, observed, scenario } from './scenario-inputs.mjs';

globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const catalogState = { kind: 'unavailable', message: 'TEST ONLY no current catalog' };
const result225 = {
  ...observed, scenario_id: scenario.id, provider: 'TEST ONLY provider', finding_ids: ['test-only-finding225'],
  classification: 'fulfilled', exercised: false, condition_fixture_verified: true,
  payment_verified: true, entitlement_verified: true, capability_available: true,
  journey_outcome: 'completed', journey_classification: 'fulfilled', exercise_gate: null,
};
function makeRun225(result = result225, extra = {}) {
  const legacy = makeRun();
  return runSchema.parse({ ...legacy, scenario_plan: { ...legacy.scenario_plan,
    selection_rationale: { ...legacy.scenario_plan.selection_rationale,
      omitted_cases: [{ scenario_id: 'test-only-omitted', reason: 'requires_fixture',
        required_capabilities: { target: ['TEST ONLY omitted download'], provider: ['TEST ONLY omitted provider'], conditions: { fixture_type: 'TEST ONLY omitted fixture TYPE' } } }] } },
    scenario_coverage: { ...legacy.scenario_coverage,
      // Deliberately inconsistent aggregate: only per-persona exercised is authoritative.
      exercised_case_ids: [scenario.id], cases: [{ ...legacy.scenario_coverage.cases[0], observed_scenario_result: result }] }, ...extra });
}

test('DTO225 preserves nullable gates, all finding IDs, provider, canonical snapshots and structured rationale', () => {
  const run = makeRun225(), saved = structuredClone(run);
  const result = run.scenario_coverage.cases[0].observed_scenario_result;
  assert.deepEqual(result, result225);
  assert.equal(run.personas[0].assigned_scenario.scenario_snapshot_json, JSON.stringify(scenario));
  assert.deepEqual(run.personas[0].assigned_scenario.source_refs_snapshot, [{ source: 'TEST ONLY accepted source reference' }]);
  assert.equal(run.scenario_plan.catalog_source_sha256, 'test-only-catalog-source-hash');
  assert.equal(run.personas[0].assigned_scenario.profile_relevance_rationale.measured_relevance, false);
  const html = renderPersonaDetail(run, run.personas[0]);
  assert.ok(html.includes('Finding ids'));
  assert.ok(html.includes('test-only-finding225'));
  assert.ok(html.includes('TEST ONLY provider'));
  assert.ok(html.includes('Scenario exercised'));
  assert.ok(html.includes('Scenario exercise gate'));
  assert.ok(!html.includes('>null<'));
  assert.deepEqual(run, saved);
});

test('a fulfilled class, verified payment/access/conditions and aggregate case IDs cannot override exercised=false', () => {
  const run = makeRun225();
  assert.equal(scenarioExercised(run.scenario_coverage.cases[0].observed_scenario_result), false);
  const html = renderScenarioCoverage(run, catalogState);
  assert.ok(html.includes('Not exercised · journey completed · Fulfilled'));
  assert.ok(html.includes('1 cases without recorded exercised coverage'));
  assert.ok(html.includes('Payment: Verified'));
  assert.ok(html.includes('test-only-finding225'));
  assert.ok(!html.includes('Exercised · service reported'));
});

test('the explicit exercised flag is authoritative independently of the locally loaded event list', () => {
  const run = makeRun225({ ...result225, exercised: true }, { events: [] });
  assert.equal(scenarioExercised(run.scenario_coverage.cases[0].observed_scenario_result), true);
  const html = renderScenarioCoverage(run, catalogState);
  assert.ok(html.includes('Exercised · service reported'));
  assert.ok(!html.includes('1 cases without recorded exercised coverage'));
  assert.ok(html.includes('test-only-finding225'));
});

test('decline, pending and blocked journey results stay visible without manufacturing case exercise', () => {
  for (const classification of ['expected_provider_decline', 'pending', 'blocked_inconclusive', 'app_defect', 'not_exercised']) {
    const run = makeRun225({ ...result225, classification, exercise_gate: 'TEST ONLY required evidence missing' });
    const result = run.scenario_coverage.cases[0].observed_scenario_result;
    assert.equal(result.classification, classification);
    assert.equal(scenarioExercised(result), false);
    const html = renderScenarioCoverage(run, catalogState);
    assert.ok(html.includes('1 cases without recorded exercised coverage'));
    assert.ok(html.includes('TEST ONLY required evidence missing'));
    assert.ok(html.includes('test-only-finding225'));
    assert.ok(!html.includes('Exercised · service reported'));
  }
});

test('null or mismatched scenario IDs retain persona findings but cannot exercise the saved assignment', () => {
  for (const scenario_id of [null, 'test-only-other-case']) {
    const run = makeRun225({ ...result225, scenario_id, exercised: true });
    const detail = renderPersonaDetail(run, run.personas[0]);
    assert.ok(detail.includes('test-only-finding225'));
    assert.ok(detail.includes('This observation does not identify the saved assigned case'));
    const coverage = renderScenarioCoverage(run, catalogState);
    assert.ok(coverage.includes('Not exercised for this assignment'));
    assert.ok(coverage.includes('1 cases without recorded exercised coverage'));
    assert.ok(coverage.includes('test-only-finding225'));
  }
});

test('omitted-case capabilities remain saved input metadata and older omitted records remain unknown', () => {
  const run = makeRun225();
  const omitted = run.scenario_plan.selection_rationale.omitted_cases[0];
  assert.deepEqual(omitted.required_capabilities.conditions, { fixture_type: 'TEST ONLY omitted fixture TYPE' });
  const html = renderSelectionRationale(run.scenario_plan);
  assert.ok(html.includes('TEST ONLY omitted download'));
  assert.ok(html.includes('TEST ONLY omitted provider'));
  assert.ok(html.includes('TEST ONLY omitted fixture TYPE'));
  assert.ok(renderSelectionRationale(makeRun().scenario_plan).includes('Unknown · not reported'));
});

test('invalid explicit exercise fields cannot silently fall back to an older DTO and strip finding or journey evidence', () => {
  for (const exercised of ['true', null, 1]) {
    assert.equal(scenarioResultSchema.safeParse({ ...result225, classification: 'app_defect', exercised }).success, false);
  }
  const missingFindings = { ...result225 };
  delete missingFindings.finding_ids;
  assert.equal(scenarioResultSchema.safeParse(missingFindings).success, false);
  const missingExercise = { ...result225, classification: 'app_defect' };
  delete missingExercise.exercised;
  assert.equal(scenarioResultSchema.safeParse(missingExercise).success, false);
  assert.equal(scenarioResultSchema.safeParse({ ...result225, exercise_gate: 123 }).success, false);
  const oldResult = scenarioResultSchema.parse(observed);
  assert.equal(oldResult.exercised, undefined);
  assert.equal(scenarioExercised(oldResult), false);
});
