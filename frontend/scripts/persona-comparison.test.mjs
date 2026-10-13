import assert from 'node:assert/strict';
import test from 'node:test';
import { person, savedRun } from './persona-inputs.mjs';
import { personaTraceLinks, renderPersonaComparison, sameSavedCustomer } from '../src/persona-comparison.ts';
import { comparisonTable } from '../src/presentation.ts';

globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const event = (id, kind, data, trace_url = null, persona_id = person.id) => ({ id, kind, data, trace_url, persona_id, created_at: '2020-01-01' });
const afterRun = (before, input = {}) => savedRun({ id: 'test-only-after', pass: 'after', parent_run_id: before.id, ...input });

test('comparison pairs persisted verification outcomes without treating actions or missing results as passes', () => {
  const before = savedRun({ events: [event(1, 'verification', { outcome: 'failed' }, 'https://ingest.neatlogs.com/api/traces/v3/test-only-before')] });
  const after = afterRun(before, { events: [event(2, 'browser_action', { outcome: 'completed' }, 'https://ingest.neatlogs.com/api/traces/v3/test-only-after')] });
  const html = renderPersonaComparison(after, before);
  assert.ok(html.includes('Failed'));
  assert.ok(html.includes('No outcome recorded'));
  assert.ok(!html.includes('>Passed<'));
  assert.ok(html.includes('test-only-before'));
  assert.ok(html.includes('test-only-after'));
  assert.ok(html.includes('before-person-trace'));
  assert.ok(html.includes('Profile, conditions and initial memory match'));
});

test('profile identity requires original conditions and initial memory and does not depend on object key order', () => {
  const returned = { ...structuredClone(person), conditions: { locale: 'en-US', device: 'desktop' } };
  assert.ok(sameSavedCustomer(person, returned));
  assert.equal(sameSavedCustomer(person, { ...returned, conditions: { device: 'mobile' } }), false);
  assert.equal(sameSavedCustomer(person, { ...returned, initial_semantic_memory: undefined }), false);
  assert.equal(sameSavedCustomer(person, { ...returned, profile: { ...person.profile, goal: 'Different goal' } }), false);
});

test('different lineage is never paired and missing original profiles remain disclosed', () => {
  const before = savedRun();
  const mismatched = renderPersonaComparison(afterRun(before, { population_id: 'test-only-other' }), before);
  assert.ok(mismatched.includes('No customer evidence has been paired'));
  assert.ok(!mismatched.includes('Inspect initial evidence'));
  const partial = savedRun({ persona_count: 4 });
  assert.ok(renderPersonaComparison(afterRun(partial, { persona_count: 4 }), partial).includes('1 / 4 original profiles'));
});

test('trace URLs are deduplicated per persona and unsafe URLs are excluded', () => {
  const run = savedRun({ events: [event(1, 'decision', {}, 'https://ingest.neatlogs.com/test-only'), event(2, 'observation', {}, 'https://ingest.neatlogs.com/test-only'), event(3, 'observation', {}, 'javascript:alert(1)'), event(4, 'observation', {}, 'https://ingest.neatlogs.com/another-person', 'other')] });
  const html = personaTraceLinks(run, person.id);
  assert.equal((html.match(/href=/g) ?? []).length, 1);
  assert.ok(!html.includes('javascript:'));
  assert.ok(!html.includes('another-person'));
});

test('aggregate comparison includes unexecuted customers in unresolved denominator', () => {
  const counts = savedRun().counts;
  const html = comparisonTable(counts, { ...counts, completed: 1, executed: 1, queued: 0 }, 4);
  assert.match(html, /Unresolved, including missing outcomes<\/th><td>4 \/ 4<\/td><td>3 \/ 4/);
});
