import assert from 'node:assert/strict';
import test from 'node:test';
import { afterRetryQuoteSchema, quoteSchema, runSchema, comparisonSchema } from '../src/contracts.ts';
import { RequestJournal, recoverRequest } from '../src/operations.ts';
import { person, savedRun } from './persona-inputs.mjs';

// OFFLINE generated DTO/journal fixtures only. Never imported by the shipped app,
// served by a live API, or evidence of any real checkout, diagnostic or payment.
const head = 'a'.repeat(40), sourceDigest = 'b'.repeat(64), cohortDigest = 'c'.repeat(64);
const target = `sha256:${'d'.repeat(64)}`;
const people = Array.from({ length: 4 }, (_, index) => ({ ...structuredClone(person), id: `test-only-person-${index}`, index }));
const counts = { requested: 4, generated: 4, queued: 4, executed: 0, completed: 0, failed: 0, abandoned: 0, pending: 0, blocked: 0 };
const retryBudget = {
  historical_spent_microusd: 16000, original_reserved_microusd: 96000, release_microusd: 80000,
  available_before_release_microusd: 100000, available_after_release_microusd: 180000,
  new_reserved_microusd: 96000, available_after_reserve_microusd: 84000,
  measured_cash_microusd: 0, cost_basis: 'estimate',
};
const repair = {
  id: 'test-only-repair', status: 'completed', base_revision: 'test-only-broken-source',
  head_revision: head, source_digest: sourceDigest, target_revision: target,
  preview_url: 'http://127.0.0.1:3310', checkpoint_id: null, checkpoint_url: null, gates: [],
};
const original = savedRun({
  id: 'test-only-original-after', pass: 'after', parent_run_id: 'test-only-before', phase: 'blocked',
  provider: 'dodo', persona_count: 4, personas: people, counts: { ...counts, queued: 0, blocked: 4 },
  revision: target, source_commit: head, source_digest: sourceDigest, repair,
  budget: { reserved_microusd: 96000, spent_microusd: 16000, unused_microusd: 80000, released_microusd: 0, cost_basis: 'estimate' },
});
const quoteInput = {
  id: 'test-only-after-retry-quote', kind: 'after_retry', app_url: original.app_url, persona_count: 4,
  expires_at: '2020-01-01T01:00:00Z', affordable: true, ready: true,
  before_microusd: 0, after_microusd: 96000, overhead_microusd: 0,
  reserved_microusd: 96000, available_microusd: 100000, currency: 'USD', cost_basis: 'estimate',
  cfo_plan_status: 'missing', gates: [], provider: 'dodo', revision: target,
  retry_of_run_id: original.id, before_run_id: original.parent_run_id,
  population_id: original.population_id, population_sha256: cohortDigest,
  persona_ids: people.map(p => p.id), retry_budget: retryBudget,
  repair_id: repair.id, head_revision: head, target_revision: target, source_digest: sourceDigest,
};
const quote = afterRetryQuoteSchema.parse(quoteInput);
const lineage = {
  original_run_id: original.id, retry_run_id: 'test-only-new-after', parent_before_run_id: original.parent_run_id,
  population_id: original.population_id, population_sha256: cohortDigest, persona_ids: quote.persona_ids,
  repair_id: repair.id, head_revision: head, target_revision: target, source_digest: sourceDigest,
  retry_budget: retryBudget, known_replay_source_run_id: original.id,
};
const acceptedInput = {
  ...original, id: lineage.retry_run_id, phase: 'queued', counts, gates: [],
  after_retry_of_run_id: original.id, after_retry: lineage, retry_budget: retryBudget, budget_contract: quote,
  budget: { reserved_microusd: 96000, spent_microusd: 0, unused_microusd: 96000, released_microusd: 0, cost_basis: 'estimate' },
};
const accepted = runSchema.parse(acceptedInput);
const result = { id: accepted.id, access_token: 'test-only-access-token', status: accepted };
const pending = {
  kind: 'after-retry', key: 'test-only-original-request-key', approvedAt: '2020-01-01T00:00:00Z',
  runId: original.id, request: { quote_id: quote.id }, quote,
};
const storage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
};

test('minimal owned Dodo diagnostic DTOs remain outside the four-person comparison denominator', () => {
  const comparison = comparisonSchema.parse({
    before_run_id: original.parent_run_id, after_run_id: accepted.id, population_id: original.population_id,
    requested: 4, before_counts: { ...counts, queued: 0, executed: 4, failed: 4 },
    after_counts: { ...counts, queued: 0, executed: 4, completed: 4 }, same_personas: true,
    known_failure_replay: 'Test-only offline accepted diagnostic DTO', total_spent_microusd: 112000, unused_microusd: 0,
    held_out_checks: { total: 2, passed: 2, failed: 0, not_in_population_denominator: true, cases: [
      { case: 'duplicate_reconciliation_grants_once', passed: true, account_id: 'test-only-replay-account', order_id: 'test-only-owned-order', method: 'owned HTTP diagnostic', diagnostic: true },
      { case: 'unpaid_access_denied', passed: true, account_id: 'test-only-unpaid-account', download_http: 403, orders: 0 },
    ] },
  });
  assert.equal(comparison.requested, 4);
  assert.equal(comparison.held_out_checks.cases[0].revision, undefined);
  assert.equal(comparison.held_out_checks.cases[1].provider, undefined);
  assert.equal(comparison.held_out_checks.not_in_population_denominator, true);
  assert.equal(runSchema.safeParse({ ...acceptedInput, phase: 'completed', comparison }).success, true);
});

test('after-only 96000 forecast retains all four personas and exact repair/source lineage', () => {
  assert.equal(quoteSchema.parse(quoteInput).kind, 'after_retry');
  assert.equal(quote.before_microusd + quote.overhead_microusd, 0);
  assert.equal(quote.reserved_microusd, 96000);
  assert.deepEqual(accepted.personas, original.personas);
  assert.deepEqual(accepted.after_retry, lineage);
  for (const bad of [
    { ...quoteInput, before_microusd: 20000 }, { ...quoteInput, overhead_microusd: 60000 },
    { ...quoteInput, persona_ids: quote.persona_ids.slice(0, 3) },
    { ...quoteInput, persona_ids: [people[0].id, people[0].id, people[2].id, people[3].id] },
    { ...quoteInput, retry_budget: undefined }, { ...quoteInput, head_revision: undefined },
    { ...quoteInput, retry_budget: { ...retryBudget, new_reserved_microusd: 120000 } },
  ]) assert.equal(quoteSchema.safeParse(bad).success, false);
});

test('accepted after DTO rejects missing lineage, changed cohort, before parent and repaired source', () => {
  for (const bad of [
    { ...acceptedInput, after_retry: undefined }, { ...acceptedInput, after_retry_of_run_id: undefined },
    { ...acceptedInput, parent_run_id: 'test-only-other-before' }, { ...acceptedInput, pass: 'before' },
    { ...acceptedInput, personas: people.slice(0, 3) }, { ...acceptedInput, personas: [...people].reverse() },
    { ...acceptedInput, source_commit: 'e'.repeat(40) }, { ...acceptedInput, source_digest: 'e'.repeat(64) },
    { ...acceptedInput, revision: 'test-only-other-target' }, { ...acceptedInput, repair: { ...repair, id: 'test-only-other-repair' } },
    { ...acceptedInput, budget: { ...acceptedInput.budget, reserved_microusd: 120000 } },
  ]) assert.equal(runSchema.safeParse(bad).success, false);
});

test('response loss and reload recover the exact approved after request without creating another run', async () => {
  const tab = storage(), journal = new RequestJournal(tab);
  journal.prepare(pending);
  const created = new Map(), sends = [];
  const send = async request => {
    sends.push(structuredClone(request));
    if (!created.has(request.key)) { created.set(request.key, result); throw new Error('Test-only response loss after acceptance'); }
    return created.get(request.key);
  };
  await assert.rejects(recoverRequest(journal, send, () => true), /response loss/);
  const reloaded = new RequestJournal(tab);
  assert.equal(reloaded.prepare({ ...pending, key: 'test-only-replacement-key' }).key, pending.key);
  await recoverRequest(reloaded, send, () => true);
  assert.equal(created.size, 1); assert.deepEqual(sends[0], sends[1]); assert.equal(reloaded.read(), null);
});

test('recovery rejects another parent, population, ordered customers, source or quote', async () => {
  for (const changed of [
    { ...accepted, id: original.id }, { ...accepted, parent_run_id: 'test-only-other-before' },
    { ...accepted, population_id: 'test-only-other-population' }, { ...accepted, personas: [...people].reverse() },
    { ...accepted, source_commit: 'e'.repeat(40) }, { ...accepted, source_digest: 'e'.repeat(64) },
    { ...accepted, revision: 'test-only-other-source' },
    { ...accepted, after_retry: { ...lineage, population_sha256: 'e'.repeat(64) } },
    { ...accepted, budget_contract: { ...quote, id: 'test-only-other-quote' } },
  ]) {
    const journal = new RequestJournal(storage()); journal.prepare(pending); let saved = false;
    await assert.rejects(recoverRequest(journal, async () => ({ ...result, id: changed.id, status: changed }), () => { saved = true; return true; }), /does not match/);
    assert.equal(saved, false); assert.deepEqual(journal.read().pending, pending);
  }
});

test('schema-valid substitute repair or diagnostic source cannot satisfy original journal approval', async () => {
  const otherRepair = runSchema.parse({
    ...acceptedInput, repair: { ...repair, id: 'test-only-other-repair' },
    after_retry: { ...lineage, repair_id: 'test-only-other-repair' },
    budget_contract: { ...quote, repair_id: 'test-only-other-repair' },
  });
  const otherDiagnostic = runSchema.parse({ ...acceptedInput, after_retry: { ...lineage, known_replay_source_run_id: 'test-only-unrelated-diagnostic' }, budget_contract: { ...quote, known_replay_source_run_id: 'test-only-unrelated-diagnostic' } });
  for (const other of [otherRepair, otherDiagnostic]) {
    const journal = new RequestJournal(storage()); journal.prepare(pending); let saved = false;
    await assert.rejects(recoverRequest(journal, async () => ({ ...result, status: other }), () => { saved = true; return true; }), /does not match/);
    assert.equal(saved, false); assert.deepEqual(journal.read().pending, pending);
  }
});

test('access is saved before journal removal; ordinary create cannot use after retry quote', async () => {
  const journal = new RequestJournal(storage()); journal.prepare(pending);
  await recoverRequest(journal, async () => result, () => false);
  assert.deepEqual(journal.read().pending, pending);
  assert.throws(() => new RequestJournal(storage()).prepare({ kind: 'create', key: 'test-only-wrong-create', approvedAt: pending.approvedAt, request: pending.request, quote }), /owner baseline retry endpoint/);
  assert.throws(() => new RequestJournal(storage()).prepare({ ...pending, runId: 'test-only-other-source-attempt' }), /blocked source attempt/);
  assert.throws(() => journal.prepare({ ...pending, request: { quote_id: 'test-only-another-quote' } }), /original pending request/);
});

test('a retry chain retains the canonical earlier diagnostic instead of inventing a new replay', async () => {
  const source = 'test-only-original-completed-diagnostic';
  const approved = afterRetryQuoteSchema.parse({ ...quoteInput, known_replay_source_run_id: source });
  const current = runSchema.parse({ ...acceptedInput, budget_contract: approved, after_retry: { ...lineage, known_replay_source_run_id: source } });
  const journal = new RequestJournal(storage()); journal.prepare({ ...pending, quote: approved });
  await recoverRequest(journal, async () => ({ ...result, status: current }), () => true);
  assert.equal(journal.read(), null);
  assert.equal(runSchema.safeParse({ ...acceptedInput, budget_contract: approved }).success, false);
});

test('completed Dodo comparison retains the actual structured duplicate reconciliation diagnostic', () => {
  const comparison = comparisonSchema.parse({
    before_run_id: 'test-only-before', after_run_id: 'test-only-after', population_id: 'test-only-population',
    requested: 4, before_counts: { ...counts, queued: 0, failed: 4 },
    after_counts: { ...counts, queued: 0, completed: 4 }, same_personas: true,
    known_failure_replay: 'reused_recorded', total_spent_microusd: 160000, unused_microusd: 16000,
    held_out_checks: { total: 2, passed: 2, failed: 0, not_in_population_denominator: true, cases: [{
      case: 'duplicate_reconciliation_grants_once', passed: true, account_id: 'test-only-diagnostic-account',
      diagnostic: { http_status: 200, state: 'already_granted', grant_applied: false, same_account_order: true,
        order_id: 'test-only-order', payment_id: 'test-only-payment', method: 'owned HTTP diagnostic',
        not_in_population_denominator: true, additional_checkout_or_pay: false },
    }, { case: 'unpaid_access_denied', passed: true, account_id: 'test-only-unpaid-account', download_http: 403, orders: 0 }] },
  });
  assert.equal(comparison.held_out_checks.cases[0].diagnostic.grant_applied, false);
  assert.equal(runSchema.safeParse({ ...acceptedInput, phase: 'completed', comparison,
    counts: { ...counts, queued: 0, completed: 4 } }).success, true);
});
