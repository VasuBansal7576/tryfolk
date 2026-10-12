import assert from 'node:assert/strict';
import test from 'node:test';
import { createLaunchApi, fixedPopulationBlocker } from '../src/launch.ts';
import { ApiError, createTransport } from '../src/transport.ts';
import { buyerChallengeSchema, personaSchema, quoteSchema } from '../src/contracts.ts';
import { person, savedRun } from './persona-inputs.mjs';
import { personaOutcome, renderPersonaDetail } from '../src/persona-detail.ts';

// Offline boundary inputs only. No browser, API, job, payment or result fixture mode.
const quote = {
  id: 'test-only-blocked-four', app_url: 'http://127.0.0.1:3101', persona_count: 4,
  expires_at: '2020-01-01T00:00:00Z', affordable: false, ready: false,
  before_microusd: 0, after_microusd: 0, overhead_microusd: 0,
  reserved_microusd: 0, available_microusd: 0, currency: 'USD', cost_basis: 'estimate',
  cfo_plan_status: 'missing', gates: [{ code: 'test-only-offline', message: 'Offline contract input.', action: 'No execution.' }],
};
const response = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });

test('new launch serializes exactly four and only the URL using the actual validated transport', async () => {
  const calls = [];
  const client = createLaunchApi(createTransport('', () => 'test-only-csrf', async (url, options) => {
    calls.push({ url, options }); return response(quote);
  }));
  const result = await client.quote(quote.app_url);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/quotes');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.credentials, 'include');
  assert.equal(calls[0].options.headers.get('X-CSRF-Token'), 'test-only-csrf');
  assert.deepEqual(JSON.parse(calls[0].options.body), { app_url: quote.app_url, persona_count: 4 });
  assert.deepEqual(result, quote);
});

test('a changed population or incomplete plan is rejected before any approval, without resizing', async () => {
  for (const data of [
    { ...quote, persona_count: 5 },
    { ...quote, scenario_plan: { catalog_version: 'test-only', catalog_sha256: 'test-only', assignments: [], requested_personas: 4, does_not_imply_exercised_coverage: true } },
  ]) {
    const original = structuredClone(data);
    let calls = 0;
    const client = createLaunchApi(createTransport('', () => null, async () => { calls += 1; return response(data); }));
    await assert.rejects(() => client.quote(quote.app_url), error => error instanceof ApiError && error.code === 'contract_mismatch');
    assert.equal(calls, 1); assert.deepEqual(data, original);
  }
});

test('historical 5, 10 and 100 populations retain their count and no-outcome state while new approvals stay fixed', () => {
  for (const n of [5, 10, 100]) {
    assert.equal(quoteSchema.parse({ ...quote, persona_count: n }).persona_count, n);
    const run = savedRun({ persona_count: n, counts: { requested: n, generated: 0, queued: n, executed: 0, completed: 0, failed: 0, abandoned: 0, pending: 0, blocked: 0 }, personas: [] });
    assert.equal(run.persona_count, n); assert.equal(run.counts.requested, n); assert.equal(run.counts.executed, 0);
    assert.match(fixedPopulationBlocker(n), /cannot be resized/);
  }
  assert.equal(fixedPopulationBlocker(4), null);
});

test('optional buyer challenge stays hypothetical and cannot supply an outcome or exercise evidence', () => {
  const challenge = { version: 'test-only-v1', id: 'test-only-challenge', title: 'TEST-ONLY-CHALLENGE', buyer_constraint: 'Test-only constraint', check_emphasis: ['Test-only measurable check'], hypothetical_problem_is_unobserved: true };
  const p = personaSchema.parse({ ...person, buyer_challenge: challenge, buyer_challenge_sha256: 'a'.repeat(64) });
  const run = savedRun({ personas: [p] });
  globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
  const html = renderPersonaDetail(run, p);
  const observed = html.slice(html.indexOf('<div class="persona-observed">'));
  assert.ok(html.includes('TEST-ONLY-CHALLENGE'));
  assert.ok(!observed.includes('TEST-ONLY-CHALLENGE'));
  assert.equal(personaOutcome(run.events), 'Unknown · no outcome recorded');
  assert.equal(run.scenario_coverage, undefined);
  assert.deepEqual(personaSchema.parse(person), person);
  assert.equal(buyerChallengeSchema.safeParse({ ...challenge, hypothetical_problem_is_unobserved: false }).success, false);
  assert.equal(personaSchema.safeParse({ ...p, buyer_challenge_sha256: 'invalid' }).success, false);
});
