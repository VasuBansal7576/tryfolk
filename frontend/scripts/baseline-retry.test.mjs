import assert from 'node:assert/strict';
import test from 'node:test';
import { baselineRetryQuoteSchema, quoteSchema, runSchema } from '../src/contracts.ts';
import { baselineRetryBlocker, createBaselineRetryApi, retryQuoteApprovalBlocker, renderRetryBudget, renderBaselineRetryHistory } from '../src/baseline-retry.ts';
import { RequestJournal, recoverRequest } from '../src/operations.ts';
import { ApiError, createTransport } from '../src/transport.ts';
import { savedRun } from './persona-inputs.mjs';

// OFFLINE DTO/HTTP fixtures only. No genuine retry, customer, payment, publication or live API.
globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const source = 'a'.repeat(40), digest = 'b'.repeat(64), populationDigest = 'c'.repeat(64);
const now = Date.parse('2020-01-01T00:00:00Z');
const original = savedRun({
  phase: 'blocked', provider: 'dodo', source_commit: source, source_digest: digest,
  budget: { reserved_microusd: 120000, spent_microusd: 40000, unused_microusd: 80000, released_microusd: 0, cost_basis: 'estimate' },
  counts: { requested: 1, generated: 1, queued: 0, executed: 0, completed: 0, failed: 0, abandoned: 0, pending: 0, blocked: 1 },
  gates: [{ code: 'test-only-setup', message: 'Test-only runtime blocker', action: 'Review test-only setup.' }],
});
const budget = {
  historical_spent_microusd: 40000, original_reserved_microusd: 120000, release_microusd: 80000,
  available_before_release_microusd: 90000, available_after_release_microusd: 170000,
  new_reserved_microusd: 120000, available_after_reserve_microusd: 50000,
  measured_cash_microusd: 0, cost_basis: 'estimate',
};
const quoteInput = {
  id: 'test-only-retry-quote', kind: 'baseline_retry', app_url: original.app_url, persona_count: 1,
  expires_at: '2020-01-01T01:00:00Z', affordable: true, ready: true,
  before_microusd: 20000, after_microusd: 20000, overhead_microusd: 60000,
  reserved_microusd: 120000, available_microusd: 90000, currency: 'USD', cost_basis: 'estimate',
  cfo_plan_status: 'missing', gates: [], provider: 'dodo', revision: original.revision,
  retry_of_run_id: original.id, population_id: original.population_id, population_sha256: populationDigest,
  persona_ids: original.personas.map(person => person.id), retry_budget: budget,
};
const quote = baselineRetryQuoteSchema.parse(quoteInput);
const lineage = {
  original_run_id: original.id, retry_run_id: 'test-only-distinct-retry', population_id: original.population_id,
  population_sha256: populationDigest, persona_ids: quote.persona_ids, approval_sha256: 'd'.repeat(64),
  accepted_at: '2020-01-01T00:01:00+00:00', retry_budget: budget,
  history_excluded_from_retry_denominator: true,
  payment_safety_basis: 'owner_reviewed_pre_submission_evidence_not_provider_receipt',
};
const statusInput = {
  ...original, id: lineage.retry_run_id, phase: 'queued', gates: [], retry_of_run_id: original.id,
  baseline_retry: lineage, retry_budget: budget, budget_contract: quote,
  counts: { ...original.counts, queued: 1, blocked: 0 },
  budget: { ...original.budget, spent_microusd: 0, unused_microusd: 120000 },
};
const status = runSchema.parse(statusInput);
const result = { id: status.id, access_token: 'test-only-access-value', status };
const pending = {
  kind: 'baseline-retry', key: 'test-only-original-retry-key', approvedAt: '2020-01-01T00:00:00Z',
  runId: original.id, request: { quote_id: quote.id }, quote,
  originalRevision: original.revision, originalSourceCommit: source, originalSourceDigest: digest,
};
const json = (value, status = 200) => new Response(JSON.stringify(value), { status });
const storage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};

test('retry DTO retains the separate budget and lineage; malformed explicit metadata cannot fall through as ordinary', () => {
  assert.equal(quoteSchema.parse(quoteInput).kind, 'baseline_retry');
  assert.deepEqual(status.baseline_retry, lineage);
  const historical = runSchema.parse({ ...original, baseline_retry: lineage, budget: { ...original.budget, unused_microusd: 0, released_microusd: 80000 } });
  assert.equal(historical.id, original.id);
  assert.equal(historical.phase, 'blocked');
  assert.equal(historical.budget.spent_microusd, 40000);
  assert.equal(historical.retry_budget, undefined);
  for (const bad of [
    { ...quoteInput, retry_budget: undefined }, { ...quoteInput, kind: 'test-only-wrong-kind' },
    { ...quoteInput, persona_ids: [] }, { ...quoteInput, population_sha256: 'not-a-digest' },
    { ...quoteInput, retry_budget: { ...budget, historical_spent_microusd: 0 } },
    { ...quoteInput, retry_budget: { ...budget, release_microusd: 120000 } },
    { ...quoteInput, retry_budget: { ...budget, available_after_release_microusd: 210000 } },
  ]) assert.equal(quoteSchema.safeParse(bad).success, false);
  for (const bad of [
    { ...statusInput, baseline_retry: undefined }, { ...statusInput, retry_of_run_id: undefined },
    { ...statusInput, retry_budget: undefined }, { ...statusInput, population_id: 'test-only-other' },
    { ...statusInput, id: original.id }, { ...statusInput, parent_run_id: original.id },
    { ...statusInput, baseline_retry: { ...lineage, history_excluded_from_retry_denominator: false } },
    { ...statusInput, baseline_retry: { ...lineage, payment_safety_basis: 'provider_verified' } },
  ]) assert.equal(runSchema.safeParse(bad).success, false);
});

test('negative remaining allowance is an honest unaffordable quote, never an executable accepted reservation', () => {
  const short = { ...budget, available_before_release_microusd: 0, available_after_release_microusd: 80000, available_after_reserve_microusd: -40000 };
  const parsed = baselineRetryQuoteSchema.parse({ ...quoteInput, affordable: false, retry_budget: short });
  assert.equal(parsed.retry_budget.available_after_reserve_microusd, -40000);
  assert.match(retryQuoteApprovalBlocker(parsed, original, true, now), /full new before-and-after reservation/);
  assert.match(renderRetryBudget(short), /Additional allowance needed/);
  assert.match(renderRetryBudget(short), /\$0\.04/);
  assert.equal(runSchema.safeParse({ ...statusInput, retry_budget: short, baseline_retry: { ...lineage, retry_budget: short } }).success, false);
  assert.equal(runSchema.safeParse({ ...original, baseline_retry: { ...lineage, retry_budget: short } }).success, false);
});

test('a blocked service count is separate from outcomes; owner, complete N, source, findings and payment-stage gates remain required', () => {
  assert.equal(baselineRetryBlocker(original, true, true, false), null);
  assert.match(baselineRetryBlocker(original, false, true, false), /owner/);
  for (const invalid of [
    { ...original, phase: 'completed' }, { ...original, pass: 'after' }, { ...original, provider: 'local-fixture' },
    { ...original, personas: [] }, { ...original, source_commit: null },
    { ...original, counts: { ...original.counts, executed: 1 } },
    { ...original, budget: { ...original.budget, released_microusd: 80000 } },
    { ...original, baseline_retry: lineage },
    { ...original, events: [{ id: 1, kind: 'verification', data: {}, persona_id: original.personas[0].id }] },
    { ...original, events: [{ id: 1, kind: 'browser_action', data: { action: { _tryfolk: { kind: 'pay' } } }, persona_id: original.personas[0].id }] },
  ]) assert.notEqual(baselineRetryBlocker(invalid, true, true, false), null);
  assert.match(baselineRetryBlocker(original, true, false, false), /Refresh/);
  assert.match(baselineRetryBlocker(original, true, true, true), /findings/);
  assert.equal(retryQuoteApprovalBlocker(quote, original, true, now), null);
  assert.notEqual(retryQuoteApprovalBlocker({ ...quote, ready: false }, original, true, now), null);
  assert.notEqual(retryQuoteApprovalBlocker({ ...quote, gates: original.gates }, original, true, now), null);
  assert.notEqual(retryQuoteApprovalBlocker(quote, original, true, now + 3_600_000), null);
  assert.notEqual(retryQuoteApprovalBlocker({ ...quote, expires_at: 'test-only-invalid-date' }, original, true, now), null);
});

test('both admitted safety bases are labelled evidence rather than a provider receipt; after comparison belongs to the NEW baseline', () => {
  for (const basis of ['owner_reviewed_pre_submission_evidence_not_provider_receipt', 'server_recorded_pre_submission_evidence_not_provider_receipt']) {
    const accepted = runSchema.parse({ ...statusInput, baseline_retry: { ...lineage, payment_safety_basis: basis } });
    const html = renderBaselineRetryHistory(accepted, true);
    assert.match(html, /not a provider payment receipt/);
    assert.match(html, basis.startsWith('server') ? /Server-recorded/ : /Owner-reviewed/);
  }
  const afterInput = { ...statusInput, id: 'test-only-after', pass: 'after', parent_run_id: status.id, phase: 'completed', comparison: {
    before_run_id: status.id, after_run_id: 'test-only-after', population_id: status.population_id,
    requested: 1, before_counts: status.counts, after_counts: status.counts, same_personas: true,
    known_failure_replay: 'Test-only offline comparison', total_spent_microusd: 0, unused_microusd: 120000,
  } };
  const after = runSchema.parse(afterInput);
  assert.match(renderBaselineRetryHistory(after, true), /This after pass belongs to the new initial attempt/);
  for (const bad of [
    { ...afterInput, parent_run_id: original.id },
    { ...afterInput, comparison: { ...afterInput.comparison, before_run_id: original.id } },
    { ...afterInput, comparison: { ...afterInput.comparison, after_run_id: original.id } },
    { ...afterInput, comparison: { ...afterInput.comparison, requested: 2 } },
  ]) assert.equal(runSchema.safeParse(bad).success, false);
});

test('a multi-customer retry binds every original customer in saved order, never a subset or regenerated ID', async () => {
  const people = [original.personas[0], { ...original.personas[0], id: 'test-only-second-customer', index: 1 }];
  const crowd = { ...original, persona_count: 2, personas: people, counts: { ...original.counts, requested: 2, generated: 2, blocked: 2 } };
  const crowdQuote = { ...quoteInput, persona_count: 2, persona_ids: people.map(person => person.id) };
  const apiFor = input => createBaselineRetryApi(createTransport('', () => null, async () => json(input)));
  assert.equal((await apiFor(crowdQuote).baselineRetryQuote(crowd)).persona_count, 2);
  for (const bad of [
    { ...crowdQuote, persona_ids: [...crowdQuote.persona_ids].reverse() },
    { ...crowdQuote, persona_ids: [people[0].id] },
    { ...crowdQuote, persona_ids: [people[0].id, people[0].id] },
    { ...crowdQuote, persona_ids: [people[0].id, 'test-only-regenerated-customer'] },
  ]) await assert.rejects(apiFor(bad).baselineRetryQuote(crowd), error => error instanceof ApiError);
  const crowdLineage = { ...lineage, persona_ids: crowdQuote.persona_ids };
  const crowdStatus = { ...statusInput, persona_count: 2, personas: people, counts: { ...status.counts, requested: 2, generated: 2, queued: 2 }, baseline_retry: crowdLineage, budget_contract: crowdQuote };
  assert.equal(runSchema.safeParse(crowdStatus).success, true);
  assert.equal(runSchema.safeParse({ ...crowdStatus, budget_contract: quoteInput }).success, false);
  assert.equal(runSchema.safeParse({ ...crowdStatus, personas: [...people].reverse() }).success, false);
});

test('owner API sends strict empty quote body and exact keyed create body without a run Bearer, then validates the saved budget join', async () => {
  const calls = [];
  const api = createBaselineRetryApi(createTransport('', () => 'test-only-csrf', async (url, options) => {
    calls.push({ url, options }); return json(url.endsWith('/quote') ? quoteInput : result, url.endsWith('/quote') ? 200 : 201);
  }));
  await api.baselineRetryQuote(original);
  await api.baselineRetry(original.id, quote.id, pending.key);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, `/runs/${original.id}/baseline-retry/quote`);
  assert.equal(calls[0].options.body, '{}');
  assert.equal(calls[1].url, `/runs/${original.id}/baseline-retry`);
  assert.equal(calls[1].options.body, JSON.stringify({ quote_id: quote.id }));
  assert.equal(calls[1].options.headers.get('Idempotency-Key'), pending.key);
  for (const call of calls) {
    assert.equal(call.options.method, 'POST');
    assert.equal(call.options.credentials, 'include');
    assert.equal(call.options.headers.get('X-CSRF-Token'), 'test-only-csrf');
    assert.equal(call.options.headers.has('Authorization'), false);
  }
  for (const wrong of [
    { ...quoteInput, retry_of_run_id: 'test-only-other' }, { ...quoteInput, persona_ids: ['test-only-other'] },
    { ...quoteInput, revision: 'test-only-other-source' },
    { ...quoteInput, retry_budget: { ...budget, original_reserved_microusd: 160000, historical_spent_microusd: 80000 } },
  ]) await assert.rejects(createBaselineRetryApi(createTransport('', () => null, async () => json(wrong))).baselineRetryQuote(original), error => error instanceof ApiError);
});

test('response loss/reload recovers one distinct before with its original key; ordinary create cannot accept a retry quote', async () => {
  const tab = storage(), journal = new RequestJournal(tab);
  journal.prepare(pending);
  const accepted = new Map();let creations = 0;const sent = [];
  const send = async request => {
    sent.push(structuredClone(request));
    if (!accepted.has(request.key)) { accepted.set(request.key, result);creations += 1;throw new Error('Test-only response loss after atomic acceptance'); }
    return accepted.get(request.key);
  };
  await assert.rejects(recoverRequest(journal, send, () => true), /response loss/);
  const reloaded = new RequestJournal(tab);
  assert.equal(reloaded.prepare({ ...pending, key: 'test-only-new-key' }).key, pending.key);
  await recoverRequest(reloaded, send, () => true);
  assert.equal(creations, 1);assert.deepEqual(sent[0], sent[1]);assert.equal(reloaded.read(), null);
  assert.throws(() => new RequestJournal(storage()).prepare({ kind: 'create', key: 'test-only-wrong-route', approvedAt: pending.approvedAt, request: { quote_id: quote.id }, quote }), /owner baseline retry endpoint/);
  assert.throws(() => new RequestJournal(storage()).prepare({ ...pending, runId: 'test-only-other' }), /original approved quote/);
});

test('recovery rejects changed population/source/quote/history money without saving or deleting its request', async () => {
  for (const changed of [
    { ...status, population_id: 'test-only-other' }, { ...status, pass: 'after' },
    { ...status, source_commit: 'e'.repeat(40) }, { ...status, source_digest: 'e'.repeat(64) },
    { ...status, baseline_retry: { ...lineage, population_sha256: 'e'.repeat(64) } },
    { ...status, budget_contract: { ...quote, id: 'test-only-other-quote' } },
    { ...status, retry_budget: { ...budget, historical_spent_microusd: 0 } },
    { ...status, personas: [] },
  ]) {
    const journal = new RequestJournal(storage());journal.prepare(pending);let saved = false;
    await assert.rejects(recoverRequest(journal, async () => ({ ...result, status: changed }), () => { saved = true;return true; }), /does not match/);
    assert.equal(saved, false);assert.deepEqual(journal.read().pending, pending);
  }
  // Lifetime availability can change between quote and acceptance; immutable use/release/reserve cannot.
  const fresh = { ...budget, available_before_release_microusd: 100000, available_after_release_microusd: 180000, available_after_reserve_microusd: 60000 };
  const accepted = runSchema.parse({ ...status, retry_budget: fresh, baseline_retry: { ...lineage, retry_budget: fresh } });
  const journal = new RequestJournal(storage());journal.prepare(pending);
  await recoverRequest(journal, async () => ({ ...result, status: accepted }), () => true);
  assert.equal(journal.read(), null);
});

test('documented owner/setup/budget gates remain errors with no automatic retry or manufactured success', async () => {
  for (const [http, code] of [
    [403, 'owner_required'], [403, 'csrf'], [404, 'run_missing'], [404, 'quote_missing'], [422, 'cfo_export_invalid'],
    ...['baseline_retry_ineligible', 'baseline_retry_approval', 'population_integrity', 'baseline_retry_stale', 'baseline_retry_exists', 'idempotency_conflict', 'quote_expired', 'quote_consumed', 'revision_changed', 'scenario_support_changed', 'setup_not_ready'].map(code => [409, code]),
    [402, 'quote_budget'], [402, 'budget'],
  ]) {
    let calls = 0;
    const api = createBaselineRetryApi(createTransport('', () => null, async () => { calls += 1;return json({ detail: { code, message: 'Test-only reported gate.' } }, http); }));
    await assert.rejects(api.baselineRetryQuote(original), error => error instanceof ApiError && error.code === code && error.status === http);
    assert.equal(calls, 1);
  }
});
