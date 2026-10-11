import assert from 'node:assert/strict';
import test from 'node:test';
import { runSchema, quoteSchema } from '../src/contracts.ts';
import { RequestJournal, recoverRequest, replayBlocker, repairTargetRevision } from '../src/operations.ts';

// Invented boundary inputs only; never served as live results and no API/browser/model/payment work.
const quote = quoteSchema.parse({
  id: 'test-only-quote', app_url: 'http://127.0.0.1:3101', persona_count: 2,
  expires_at: '2020-01-01T00:00:00Z', affordable: true, ready: true,
  before_microusd: 40000, after_microusd: 40000, overhead_microusd: 50000,
  reserved_microusd: 130000, available_microusd: 450000, currency: 'USD', cost_basis: 'estimate',
  cfo_plan_status: 'missing', gates: [],
});
const baseline = runSchema.parse({
  id: 'test-only-before', app_url: quote.app_url, persona_count: 2, population_id: 'test-only-population',
  parent_run_id: null, pass: 'before', phase: 'completed', revision: 'test-only-base', fault: 'test-only',
  preview_url: quote.app_url, created_at: '2020-01-01', updated_at: '2020-01-01',
  counts: { requested: 2, generated: 2, queued: 0, executed: 2, completed: 1, failed: 1, abandoned: 0, pending: 0, blocked: 0 },
  budget: { reserved_microusd: 130000, spent_microusd: 60000, unused_microusd: 70000, released_microusd: 0, cost_basis: 'estimate' },
  gates: [], events: [], comparison: null,
  personas: [0, 1].map(index => ({ id: 'test-only-person-' + index, index, name: 'Test-only person', profile: {}, conditions: {}, memory: [] })),
  repair: { id: 'test-only-repair', status: 'completed', base_revision: 'test-only-base', head_revision: 'test-only-head', preview_url: 'http://127.0.0.1:3102', checkpoint_id: 'test-only-checkpoint', checkpoint_url: null, gates: [] },
});
const replay = {
  kind: 'replay', key: 'test-only-original-replay-key', approvedAt: '2020-01-01T00:00:00Z',
  runId: baseline.id, request: { repair_id: baseline.repair.id }, populationId: baseline.population_id,
  personaCount: baseline.persona_count, repairRevision: baseline.repair.head_revision,
};
const create = { kind: 'create', key: 'test-only-original-create-key', approvedAt: replay.approvedAt, request: { quote_id: quote.id }, quote };
function memoryStorage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
function resultFor(pending) {
  const status = pending.kind === 'create' ? { ...baseline, phase: 'queued' } : {
    ...baseline, id: 'test-only-after', parent_run_id: baseline.id, pass: 'after', phase: 'queued',
    app_url: baseline.repair.preview_url, preview_url: baseline.repair.preview_url, revision: baseline.repair.head_revision,
  };
  return { id: status.id, access_token: 'test-only-access-value', status: runSchema.parse(status) };
}

test('Dodo replay recovery binds both registered preview revision and repaired source commit', async () => {
  const sourceCommit = 'b'.repeat(40);
  const targetRevision = 'sha256:' + 'c'.repeat(64);
  const dodo = runSchema.parse({ ...baseline, provider: 'dodo', source_commit: 'a'.repeat(40), source_digest: 'd'.repeat(64), target_mode: 'broken', repair: { ...baseline.repair, head_revision: sourceCommit, target_revision: targetRevision, source_digest: 'e'.repeat(64) } });
  assert.equal(repairTargetRevision(dodo), targetRevision);
  assert.equal(replayBlocker(dodo), null);
  assert.match(replayBlocker({ ...dodo, repair: { ...dodo.repair, target_revision: null } }), /preview revision/);
  const pending = { ...replay, repairRevision: repairTargetRevision(dodo), repairSourceCommit: sourceCommit };
  const journal = new RequestJournal(memoryStorage());
  journal.prepare(pending);
  const response = resultFor(pending);
  await assert.rejects(recoverRequest(journal, async () => response, () => true), /does not match/);
  const correct = { ...response, status: { ...response.status, provider: 'dodo', revision: targetRevision, source_commit: sourceCommit } };
  await assert.rejects(recoverRequest(journal, async () => ({ ...correct, status: { ...correct.status, source_commit: 'f'.repeat(40) } }), () => true), /does not match/);
  assert.deepEqual(journal.read().pending, pending);
  await recoverRequest(journal, async () => correct, () => true);
  assert.equal(journal.read(), null);
  assert.equal(repairTargetRevision(baseline), baseline.repair.head_revision);
});

test('legacy fixture runs retain absent source pins as null', () => {
  const legacy = runSchema.parse({ ...baseline, source_commit: null, source_digest: null, target_mode: null });
  assert.equal(legacy.source_commit, null);
  assert.equal(repairTargetRevision(legacy), baseline.repair.head_revision);
});

test('completed baseline needs every original unique saved profile, complete repair and held allowance', () => {
  assert.equal(replayBlocker(baseline), null);
  for (const phase of ['cancelled', 'failed', 'blocked', 'running', 'verifying']) {
    assert.match(replayBlocker({ ...baseline, phase }), /must be completed/);
  }
  assert.match(replayBlocker({ ...baseline, pass: 'after' }), /original initial run/);
  assert.match(replayBlocker({ ...baseline, counts: { ...baseline.counts, generated: 1 } }), /All original/);
  assert.match(replayBlocker({ ...baseline, counts: { ...baseline.counts, requested: 3 } }), /All original/);
  assert.match(replayBlocker({ ...baseline, personas: baseline.personas.slice(1) }), /All original/);
  assert.match(replayBlocker({ ...baseline, personas: [baseline.personas[0], baseline.personas[0]] }), /All original/);
  assert.match(replayBlocker({ ...baseline, repair: { ...baseline.repair, head_revision: null } }), /completed repair/);
});

test('released/cancelled/empty reservations never offer a fresh retest, including partially released funds', () => {
  assert.match(replayBlocker({ ...baseline, phase: 'cancelled' }), /must be completed/);
  assert.match(replayBlocker({ ...baseline, budget: { ...baseline.budget, released_microusd: 1 } }), /released/);
  assert.match(replayBlocker({ ...baseline, budget: { ...baseline.budget, unused_microusd: 0 } }), /no longer held/);
  assert.match(replayBlocker(baseline, true), /already has an after run/);
});

for (const pending of [create, replay]) {
  test(`${pending.kind}: response loss then dialog close/reload recovers the exact key/body and only one accepted run`, async () => {
    const storage = memoryStorage();
    const firstJournal = new RequestJournal(storage);
    firstJournal.prepare(pending);
    const accepted = new Map();
    const requests = [];
    let jobs = 0;
    const endpoint = async request => {
      // This adapter models the published idempotency boundary, not browser/customer execution.
      requests.push(structuredClone(request));
      if (!accepted.has(request.key)) { accepted.set(request.key, resultFor(request)); jobs += 1; throw new Error('Test-only lost response after commit'); }
      return accepted.get(request.key);
    };
    await assert.rejects(recoverRequest(firstJournal, endpoint, () => true), /lost response/);
    // No dialog/view state is retained; a fresh journal uses only persisted tab data.
    const reloaded = new RequestJournal(storage);
    assert.deepEqual(reloaded.read().pending, pending);
    assert.equal(reloaded.prepare({ ...pending, key: 'test-only-new-modal-key' }).key, pending.key);
    let saved = null;
    const recovered = await recoverRequest(reloaded, endpoint, result => { saved = result; return true; });
    assert.equal(jobs, 1);
    assert.deepEqual(requests[0], requests[1]);
    assert.equal(recovered.id, saved.id);
    assert.equal(reloaded.read(), null);
    assert.equal(recovered.status.persona_count, baseline.persona_count);
    assert.equal(recovered.status.population_id, baseline.population_id);
  });
}

test('pending replay cannot acquire a different repair, population, count or new initial-run request', () => {
  const journal = new RequestJournal(memoryStorage());
  journal.prepare(replay);
  const reused = journal.prepare({ ...replay, key: 'different-key', personaCount: 1, populationId: 'different-population', repairRevision: 'different-head' });
  assert.deepEqual(reused, replay);
  assert.throws(() => journal.prepare({ ...replay, request: { repair_id: 'different-repair' } }), /original pending request/);
  assert.throws(() => journal.prepare(create), /original pending request/);
  assert.deepEqual(journal.read().pending, replay);
});

test('no request is sent unless its complete recovery record was persisted first', async () => {
  let sent = 0;
  const storage = { ...memoryStorage(), setItem: () => { throw new Error('Test-only storage denied'); } };
  const journal = new RequestJournal(storage);
  await assert.rejects(async () => { journal.prepare(replay); await recoverRequest(journal, async pending => { sent += 1; return resultFor(pending); }, () => true); }, /could not be saved/);
  assert.equal(sent, 0);
  assert.throws(() => new RequestJournal({ ...memoryStorage(), getItem: () => '{invalid' }).prepare(create), /could not be read/);
});

test('failed access persistence retains the request across reload, then resolves only after access is saved', async () => {
  const storage = memoryStorage();
  const journal = new RequestJournal(storage);
  journal.prepare(replay);
  await recoverRequest(journal, async pending => resultFor(pending), () => false);
  assert.deepEqual(new RequestJournal(storage).read().pending, replay);
  await recoverRequest(new RequestJournal(storage), async pending => resultFor(pending), () => true);
  assert.equal(journal.read(), null);
});

test('a mismatched result keeps its original recovery record and is never saved', async () => {
  const journal = new RequestJournal(memoryStorage());
  journal.prepare(replay);
  let saved = false;
  const wrong = resultFor(replay);
  wrong.status.population_id = 'test-only-wrong-population';
  await assert.rejects(recoverRequest(journal, async () => wrong, () => { saved = true; return true; }), /does not match/);
  assert.equal(saved, false);
  assert.deepEqual(journal.read().pending, replay);
});

test('ambiguous errors and key conflicts cannot dismiss recovery; definitive declines need explicit dismissal', () => {
  const journal = new RequestJournal(memoryStorage());
  journal.prepare(create);
  for (const [status, code] of [[0, 'unreachable'], [500, 'request_failed'], [201, 'contract_mismatch'], [409, 'idempotency_conflict'], [403, 'owner_required']]) {
    journal.decline(status, code, 'Test-only error');
    assert.equal(journal.read().declined, null);
    assert.throws(() => journal.dismissDeclined(), /lost response/);
  }
  journal.decline(409, 'quote_expired', 'Test-only expired quote');
  assert.deepEqual(journal.read().pending, create);
  journal.dismissDeclined();
  assert.equal(journal.read(), null);
});

test('replay request boundary rejects population overrides rather than adding them to recovery', () => {
  const journal = new RequestJournal(memoryStorage());
  assert.throws(() => journal.prepare({ ...replay, request: { ...replay.request, persona_count: 1 } }));
  assert.equal(journal.read(), null);
  assert.throws(() => journal.prepare({ ...create, request: { quote_id: 'different-quote' } }), /original approved quote/);
});

test('retrying a declined request cannot use its earlier decline to dismiss a later lost response', async () => {
  const journal = new RequestJournal(memoryStorage());
  journal.prepare(create);
  journal.decline(402, 'budget', 'Test-only unavailable budget');
  await assert.rejects(recoverRequest(journal, async () => { throw new Error('Test-only lost response'); }, () => true), /lost response/);
  assert.equal(journal.read().declined, null);
  assert.throws(() => journal.dismissDeclined(), /lost response/);
  assert.deepEqual(journal.read().pending, create);
});

test('an accepted original replay remains recoverable after cancellation without approving another retest', async () => {
  const journal = new RequestJournal(memoryStorage());
  journal.prepare(replay);
  const endedBaseline = { ...baseline, phase: 'cancelled', budget: { ...baseline.budget, unused_microusd: 0, released_microusd: 70000 } };
  assert.notEqual(replayBlocker(endedBaseline), null);
  const cached = resultFor(replay);
  cached.status.phase = 'cancelled';
  let requests = 0;
  const result = await recoverRequest(journal, async original => {
    assert.deepEqual(original, replay);
    requests += 1;
    return cached; // Contract returns its previously accepted result before checking fresh-replay eligibility.
  }, () => true);
  assert.equal(requests, 1);
  assert.equal(result.status.phase, 'cancelled');
  assert.equal(result.id, cached.id);
  assert.equal(journal.read(), null);
});
