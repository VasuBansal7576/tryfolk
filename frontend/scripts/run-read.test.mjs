import assert from 'node:assert/strict';
import test from 'node:test';
import { findingsSchema, runSchema } from '../src/contracts.ts';
import { ApiError, createTransport } from '../src/transport.ts';
import { readRunEvidence } from '../src/run-read.ts';

// Test-only contract inputs. No server, browser, payment, model, job or reservation.
const run = runSchema.parse({
  id: 'test-only-run', app_url: 'http://127.0.0.1:3101', persona_count: 2,
  population_id: 'test-only-population', parent_run_id: null, pass: 'before', phase: 'running',
  revision: 'test-only-source', fault: 'test-only', preview_url: 'http://127.0.0.1:3101',
  created_at: '2020-01-01', updated_at: '2020-01-01',
  counts: { requested: 2, generated: 2, queued: 2, executed: 0, completed: 0, failed: 0, abandoned: 0, pending: 0, blocked: 0 },
  budget: { reserved_microusd: 130000, spent_microusd: 60000, unused_microusd: 70000, released_microusd: 0, cost_basis: 'estimate' },
  gates: [], events: [], comparison: null, repair: null,
  personas: [0, 1].map(index => ({ id: 'test-only-person-' + index, index, name: 'Test-only person', profile: { goal: 'Test-only goal' }, conditions: { locale: 'en-US' }, memory: ['Test-only saved memory'] })),
});
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function client(fetcher) {
  const request = createTransport('', () => 'test-only-csrf', fetcher);
  return {
    run: (id, token) => request(`/runs/${id}`, runSchema, { token }),
    findings: (id, token) => request(`/runs/${id}/findings`, findingsSchema, { token }),
  };
}

test('independent GETs start together with identical access and preserve all original data', async () => {
  const calls = [], runReply = deferred(), findingReply = deferred();
  const reads = readRunEvidence(client(async (url, options) => {
    calls.push({ url, options });
    return url.endsWith('/findings') ? findingReply.promise : runReply.promise;
  }), { id: run.id, token: 'test-only-access' });
  assert.deepEqual(calls.map(call => call.url), [`/runs/${run.id}`, `/runs/${run.id}/findings`]);
  for (const { options } of calls) {
    assert.equal(options.method, 'GET');
    assert.equal(options.credentials, 'include');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.headers.get('Authorization'), 'Bearer test-only-access');
    assert.equal(options.headers.has('X-CSRF-Token'), false);
    assert.equal(options.body, undefined);
  }
  findingReply.resolve(json({ findings: [] }));
  assert.deepEqual(await reads.findings, { kind: 'ready', findings: [] });
  runReply.resolve(json(run));
  assert.deepEqual(await reads.run, run);
});

test('a findings refusal remains unavailable evidence beside a valid run', async () => {
  const reads = readRunEvidence(client(async url => url.endsWith('/findings')
    ? json({ detail: { code: 'evidence_denied', message: 'Test-only evidence refused.' } }, 403)
    : json(run)), { id: run.id, token: '' });
  assert.deepEqual(await reads.run, run);
  const result = await reads.findings;
  assert.equal(result.kind, 'unavailable');
  assert.ok(result.error instanceof ApiError);
  assert.equal(result.error.status, 403);
});

test('run authorization failure rejects before a slow findings response finishes', async () => {
  const findingReply = deferred();
  const reads = readRunEvidence(client(async url => url.endsWith('/findings') ? findingReply.promise
    : json({ detail: 'Test-only run refused.' }, 401)), { id: run.id, token: '' });
  await assert.rejects(reads.run, error => error instanceof ApiError && error.status === 401);
  findingReply.resolve(json({ detail: 'Test-only evidence refused.' }, 403));
  assert.equal((await reads.findings).kind, 'unavailable');
});

test('a fresh validated owner/run response skips only its duplicate GET', async () => {
  const calls = [];
  const reads = readRunEvidence(client(async (url, options) => {
    calls.push({ url, options }); return json({ findings: [] });
  }), { id: run.id, token: '', initial: run });
  assert.equal(await reads.run, run);
  assert.deepEqual(await reads.findings, { kind: 'ready', findings: [] });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `/runs/${run.id}/findings`);
  assert.equal(calls[0].options.credentials, 'include');
});

test('a response for another run is rejected before starting reads', () => {
  let calls = 0;
  assert.throws(() => readRunEvidence({ run: async () => { calls++; return run; }, findings: async () => { calls++; return { findings: [] }; } },
    { id: 'test-only-different-run', token: '', initial: run }), /does not match/);
  assert.equal(calls, 0);
});
