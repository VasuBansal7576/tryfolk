import assert from 'node:assert/strict';
import test from 'node:test';
import { person, savedRun } from './persona-inputs.mjs';
import { personaOutcome, renderPersonaDetail, selectedPersona } from '../src/persona-detail.ts';

globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const event = (id, data, extra = {}) => ({ id, persona_id: person.id, kind: 'verification', created_at: '2020-01-01', data, trace_url: null, ...extra });

test('selection binds persona to current run, population and navigation epoch, even with shared before/after IDs', () => {
  const run = savedRun();
  const selection = { runId: run.id, populationId: run.population_id, epoch: 2, personaId: person.id };
  const input = { run, viewRunId: run.id, epoch: 2, selection };
  assert.equal(selectedPersona(input), run.personas[0]);
  assert.equal(selectedPersona({ ...input, epoch: 3 }), null);
  assert.equal(selectedPersona({ ...input, viewRunId: 'test-only-after' }), null);
  assert.equal(selectedPersona({ ...input, run: savedRun({ id: 'test-only-after', pass: 'after', parent_run_id: run.id }) }), null);
  assert.equal(selectedPersona({ ...input, run: savedRun({ population_id: 'test-only-new-population' }) }), null);
  assert.equal(selectedPersona({ ...input, run: savedRun({ personas: [], counts: { ...run.counts, generated: 0 } }) }), null);
});

test('missing goal, scenario, payment, access, download and memory stay unknown, never inferred from the profile or run counts', () => {
  const run = savedRun({ personas: [{ ...person, profile: { occupation: 'Test-only artist' }, initial_semantic_memory: undefined }] });
  const html = renderPersonaDetail(run, run.personas[0]);
  assert.ok(html.includes('Test-only artist'));
  assert.ok(html.includes('Unknown · no outcome recorded'));
  assert.ok(html.includes('Seed source and provenance'));
  assert.ok(html.includes('Unknown · not reported in this saved record'));
  assert.ok(!html.includes('Payment succeeded'));
  assert.ok(!html.includes('Test-only original memory'));
});

test('detail preserves original saved memory and filters all observations to the selected persona and run', () => {
  const run = savedRun({ events: [event(1, { outcome: 'failed', payment: { status: 'succeeded', provider: 'test-only' }, entitlement: { granted: false }, download: { observed: false } }), event(2, { outcome: 'completed', payment: 'OTHER-CUSTOMER-MARKER' }, { persona_id: 'test-only-other' })] });
  const before = structuredClone(run);
  const html = renderPersonaDetail(run, run.personas[0]);
  assert.ok(html.includes('Test-only original memory'));
  assert.ok(html.includes('Failed'));
  assert.ok(!html.includes('OTHER-CUSTOMER-MARKER'));
  assert.deepEqual(run, before);
  assert.equal(personaOutcome(run.events.filter(item => item.persona_id === person.id)), 'Failed');
  assert.equal(personaOutcome([event(3, { action: 'Test-only click' }, { kind: 'browser_action' })]), 'Unknown · no outcome recorded');
});

test('user, profile, provider and trace strings remain escaped data; script URLs are not linked', () => {
  const payload = '<img src=x onerror="window.testOnlyXss=1">';
  const run = savedRun({ personas: [{ ...person, name: payload, profile: { goal: payload, preferences: [payload] } }], events: [event(1, { outcome: payload, payment: { provider: payload }, download_url: 'javascript:window.testOnlyXss=1' }, { trace_url: 'javascript:window.testOnlyXss=1' })] });
  const html = renderPersonaDetail(run, run.personas[0]);
  assert.ok(!html.includes('<img src=x'));
  assert.ok(html.includes('&lt;img src=x'));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes('Inspect this customer'));
});

test('later memory never substitutes for absent initial memory, and an explicitly empty initial memory remains empty', () => {
  const run = savedRun({ personas: [{ ...person, initial_semantic_memory: undefined, memory: ['TEST-ONLY-LATER-MEMORY'] }] });
  const html = renderPersonaDetail(run, run.personas[0]);
  const initial = html.slice(html.indexOf('<h3>Saved initial memory</h3>'), html.indexOf('<h3>Other saved memory records</h3>'));
  assert.ok(initial.includes('Unknown'));
  assert.ok(!initial.includes('TEST-ONLY-LATER-MEMORY'));
  assert.ok(html.includes('Other saved memory records'));
  const empty = savedRun({ personas: [{ ...person, initial_semantic_memory: [], memory: ['TEST-ONLY-LATER-MEMORY'] }] });
  const emptyHtml = renderPersonaDetail(empty, empty.personas[0]);
  const emptyInitial = emptyHtml.slice(emptyHtml.indexOf('<h3>Saved initial memory</h3>'), emptyHtml.indexOf('<h3>Other saved memory records</h3>'));
  assert.ok(emptyInitial.includes('No entries saved'));
  assert.ok(!emptyInitial.includes('TEST-ONLY-LATER-MEMORY'));
});

test('actual backend verification keys preserve separate provider, raw app entitlement and observed capability records', () => {
  const run = savedRun({ events: [event(1, {
    outcome: 'failed', payment_verified: true, entitlement_verified: false,
    receipt: { source: 'TEST-ONLY-RECEIPT' }, app_entitlement: { source: 'TEST-ONLY-APP-ENTITLEMENT' },
    capability_checked: true, capability_available: false,
    capability: { source: 'TEST-ONLY-BROWSER-DOWNLOAD', download_sha256: 'TEST-ONLY-DOWNLOAD-HASH' },
  })] });
  const html = renderPersonaDetail(run, run.personas[0]);
  const payment = html.slice(html.indexOf('<h3>Payment evidence</h3>'), html.indexOf('<h3>Account access evidence</h3>'));
  const access = html.slice(html.indexOf('<h3>Account access evidence</h3>'), html.indexOf('<h3>Download and purchased capability evidence</h3>'));
  const download = html.slice(html.indexOf('<h3>Download and purchased capability evidence</h3>'), html.indexOf('<h3>Recorded steps and decisions</h3>'));
  assert.ok(payment.includes('TEST-ONLY-RECEIPT'));
  assert.ok(payment.includes('Payment verified'));
  assert.ok(!payment.includes('TEST-ONLY-APP-ENTITLEMENT'));
  assert.ok(access.includes('TEST-ONLY-APP-ENTITLEMENT'));
  assert.ok(access.includes('Entitlement verified'));
  assert.ok(!access.includes('TEST-ONLY-BROWSER-DOWNLOAD'));
  assert.ok(download.includes('TEST-ONLY-DOWNLOAD-HASH'));
  assert.ok(download.includes('Capability availability alone does not establish a completed download'));
});
