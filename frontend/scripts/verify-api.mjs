import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { quoteSchema, createdRunSchema, runSchema, findingsSchema, readinessSchema } from '../src/contracts.ts';
import { redact } from '../src/presentation.ts';

// An actual local API check, with no mock response server and no embedded credentials.
const base = process.env.TRYFOLK_API_URL || 'http://127.0.0.1:8000';
const local = new URL(base);
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(local.hostname), 'Verification is local-only.');
const jsonHeaders = { 'Content-Type': 'application/json' };
async function call(path, options = {}) {
  const response = await fetch(base + path, { ...options, signal: AbortSignal.timeout(20_000) });
  const body = await response.json();
  return { response, body };
}

const readiness = readinessSchema.parse((await call('/readiness')).body);
const target = process.env.TRYFOLK_TEST_TARGET || readiness.target_url;
assert.ok(target, 'Register an owned local test target before verification.');
const invalidCount = await call('/quotes', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ app_url: target, persona_count: 5 }) });
assert.equal(invalidCount.response.status, 422, 'Invalid count must be rejected without reduction.');
const unknownTarget = await call('/quotes', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ app_url: 'https://unconnected.invalid', persona_count: 4 }) });
assert.equal(unknownTarget.response.status, 422, 'Unconnected origins must be rejected.');
const quoteResponse = await call('/quotes', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ app_url: target, persona_count: 4 }) });
assert.equal(quoteResponse.response.status, 200);
const quote = quoteSchema.parse(quoteResponse.body);
assert.equal(quote.persona_count, 4);
assert.ok(quote.provider || quote.provenance, 'The quote must identify its payment source.');
assert.equal(quote.reserved_microusd, quote.before_microusd + quote.after_microusd + quote.overhead_microusd + (quote.contingency_microusd ?? 0), 'Reserve must cover both passes, overhead and the original explicit contingency.');
assert.ok(quote.affordable, 'Verification needs enough local allowance for N=4.');

const key = crypto.randomUUID();
const createOptions = { method: 'POST', headers: { ...jsonHeaders, 'Idempotency-Key': key }, body: JSON.stringify({ quote_id: quote.id }) };
const createdResponse = await call('/runs', createOptions);
assert.equal(createdResponse.response.status, 201);
const created = createdRunSchema.parse(createdResponse.body);
const auth = { ...jsonHeaders, Authorization: 'Bearer ' + created.access_token };
let proof;
try {
  const duplicate = createdRunSchema.parse((await call('/runs', createOptions)).body);
  assert.equal(duplicate.id, created.id, 'Duplicate creation must return the saved run.');
  assert.equal(duplicate.access_token, created.access_token);
  const unauthorized = await call(`/runs/${created.id}`);
  assert.equal(unauthorized.response.status, 401, 'Run access must require authorization.');
  const guestRepair = await call(`/runs/${created.id}/repairs`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ finding_id: 'not-a-finding' }) });
  assert.equal(guestRepair.response.status, 403, 'Guests cannot approve a repair.');
  const guestReplay = await call(`/runs/${created.id}/replay`, { method: 'POST', headers: { ...jsonHeaders, 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ repair_id: 'not-a-repair' }) });
  assert.equal(guestReplay.response.status, 403, 'Guests cannot start a retest.');
  const run = runSchema.parse((await call(`/runs/${created.id}`, { headers: auth })).body);
  assert.equal(run.persona_count, 4);
  assert.equal(run.counts.requested, 4);
  if (quote.revision) assert.equal(run.revision, quote.revision, 'The quoted target revision must be pinned to the run.');
  if (quote.fault) assert.equal(run.fault, quote.fault, 'The quoted fault configuration must be preserved.');
  if (quote.provenance && run.provenance) assert.equal(run.provenance.payment_truth, quote.provenance.payment_truth, 'Payment-source provenance must be preserved.');
  else if (quote.provider) assert.equal(run.provider, quote.provider, 'Fixture/provider identity must be preserved.');
  if (!quote.ready) {
    assert.equal(run.phase, 'blocked');
    assert.equal(run.counts.generated, 0);
    assert.equal(run.counts.executed, 0);
    assert.equal(run.counts.blocked, 4);
    assert.equal(run.personas.length, 0, 'Blocked setup must not invent people.');
  }
  const findings = findingsSchema.parse((await call(`/runs/${created.id}/findings`, { headers: auth })).body);
  proof = { verified_at: new Date().toISOString(), target, quote, run, findings: findings.findings, checks: ['combined reserve', 'no silent count reduction', 'unconnected target rejection', 'create idempotency', 'run authorization', 'owner-only repair/replay', 'saved status and findings', 'pinned revision/fault', 'fixture/provider provenance'] };
} finally {
  const cancel = await call(`/runs/${created.id}/cancel`, { method: 'POST', headers: auth, body: '{}' });
  assert.equal(cancel.response.status, 200);
  const cancelled = runSchema.parse(cancel.body);
  assert.equal(cancelled.phase, 'cancelled');
  assert.equal(cancelled.budget.unused_microusd, 0, 'Cancel must release the unused allowance.');
  const repeated = runSchema.parse((await call(`/runs/${created.id}/cancel`, { method: 'POST', headers: auth, body: '{}' })).body);
  assert.equal(repeated.budget.released_microusd, cancelled.budget.released_microusd, 'Repeated cancel must not double-release.');
  if (proof) { proof.cancelled = cancelled; proof.checks.push('cancel reservation release', 'cancel idempotency'); }
}
if (process.env.TRYFOLK_PROOF_PATH) await fs.writeFile(process.env.TRYFOLK_PROOF_PATH, JSON.stringify(redact(proof), null, 2) + '\n');
console.log(JSON.stringify({ verified: true, run_id: created.id, initial_phase: proof.run.phase, generated: proof.run.counts.generated, executed: proof.run.counts.executed, checks: proof.checks }));
