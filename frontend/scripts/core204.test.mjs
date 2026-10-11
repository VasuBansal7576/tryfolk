import assert from 'node:assert/strict';
import test from 'node:test';
import { runSchema, scenarioCatalogSchema, scenarioResultSchema } from '../src/contracts.ts';
import { renderPersonaDetail, renderSeedProvenance } from '../src/persona-detail.ts';
import { renderScenarioCoverage, renderSelectionRationale } from '../src/scenario-coverage.ts';
import { redact } from '../src/presentation.ts';
import { scenario, seed, observed, makeRun } from './scenario-inputs.mjs';

globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };

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
  assert.ok(html.includes('Unknown · exercise not reported · journey failed'));
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
