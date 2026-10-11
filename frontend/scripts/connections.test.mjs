import assert from 'node:assert/strict';
import test from 'node:test';
import { connectionSchema, connectionsSchema } from '../src/contracts.ts';
import { applyRegistration, connectionSnapshot, createConnectionApi, executionReportedReady, registrationAvailable, registrationMessage, sourceVerified } from '../src/connections.ts';
import { ApiError, createTransport } from '../src/transport.ts';

// Contract inputs for the test process only. No fixture mode or success data enters the built app.
const candidate = {
  id: 'test-only-target', app_url: 'http://127.0.0.1:3301', registration_status: 'unregistered', execution_ready: false,
  provider: 'dodo', revision: 'sha256:' + 'c'.repeat(64),
  source: { status: 'pending', repository_label: 'Test-only licensed source', repository_url: null, commit: 'a'.repeat(40), digest: 'b'.repeat(64), detail: 'Test-only source awaiting verification.' },
  gates: [{ code: 'connection_required', message: 'Owner registration is required.', action: 'Verify the approved app.' }],
};
const registered = { ...candidate, registration_status: 'registered', source: { ...candidate.source, status: 'verified' }, gates: [
  { code: 'dodo_runtime_validation', message: 'Browser payment verification remains blocked.', action: 'Complete the independent adapter checks.' },
] };
const capability = { version: '1', registration_supported: true, ownership_scope: 'owner-approved-loopback-dodo-test', supported_adapters: ['owned-dodo-special-downloads-v1'], targets: [candidate] };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

test('actual connection client reads and deliberately posts only the approved URL with cookie/CSRF', async () => {
  const calls = [];
  const client = createConnectionApi(createTransport('', () => 'test-only-csrf', async (url, options) => {
    calls.push({ url, options });
    return json(options.method === 'GET' ? capability : registered);
  }));
  const data = await client.connections();
  assert.equal(calls.length, 1); // Reading capabilities never performs registration.
  assert.equal(registrationAvailable({ kind: 'ready', data }), true);
  const result = await client.registerConnection(data.targets[0].app_url);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(call => call.url === '/connections' && call.options.credentials === 'include'));
  assert.equal(calls[1].options.headers.get('X-CSRF-Token'), 'test-only-csrf');
  assert.deepEqual(JSON.parse(calls[1].options.body), { app_url: candidate.app_url });
  assert.equal(sourceVerified(result), true);
  assert.equal(executionReportedReady(result), false);
  assert.match(registrationMessage(result), /Execution remains blocked/);
  assert.deepEqual(result.gates, registered.gates);
});

test('source registration cannot establish runtime or payment success, even when fields conflict', () => {
  assert.equal(sourceVerified(connectionSchema.parse(candidate)), false);
  for (const change of [
    { status: 'stale' }, { status: 'unavailable' }, { status: 'pending' }, { commit: null }, { digest: 'invalid' },
  ]) {
    const incomplete = connectionSchema.parse({ ...registered, execution_ready: true, gates: [], source: { ...registered.source, ...change } });
    assert.equal(executionReportedReady(incomplete), false);
    assert.doesNotMatch(registrationMessage(incomplete), /reports execution ready/);
  }
  assert.equal(executionReportedReady({ ...registered, execution_ready: true }), false); // Gates still block.
  assert.equal(executionReportedReady({ ...registered, execution_ready: true, gates: [] }), true);
  assert.match(registrationMessage({ ...registered, execution_ready: true, gates: [] }), /budget approval is still required/);
});

test('missing or unsupported capability and stale snapshots remain read-only; guest has no private snapshot', () => {
  const { registration_supported, ...missing } = capability;
  assert.equal(connectionsSchema.safeParse(missing).success, false);
  for (const data of [
    { ...capability, registration_supported: false }, { ...capability, supported_adapters: [] }, { ...capability, ownership_scope: 'test-only-unknown-scope' },
  ]) assert.equal(registrationAvailable({ kind: 'ready', data }), false);
  for (const state of [
    { kind: 'loading', previous: capability }, { kind: 'unavailable', previous: capability, message: 'Test-only refresh failed' },
    { kind: 'unsupported', message: 'Test-only old API' }, { kind: 'guest', message: 'Owner session required' },
  ]) assert.equal(registrationAvailable(state), false);
  assert.deepEqual(connectionSnapshot({ kind: 'unavailable', previous: capability, message: 'Test-only refresh failed' }), capability);
  assert.equal(connectionSnapshot({ kind: 'guest', message: 'Owner disconnected' }), null);
});

test('registration updates only its matching candidate and never mutates the earlier snapshot', () => {
  const before = structuredClone(capability);
  const updated = applyRegistration(before, candidate.app_url, registered);
  assert.equal(updated.targets[0].registration_status, 'registered');
  assert.deepEqual(before, capability);
  assert.throws(() => applyRegistration(before, candidate.app_url, { ...registered, app_url: 'http://127.0.0.1:9999' }), /does not match/);
  assert.throws(() => applyRegistration({ ...before, targets: [] }, candidate.app_url, registered), /does not match/);
  assert.deepEqual(before, capability);
});

test('empty approved list is a valid read-only outcome, not an invented target', async () => {
  const client = createConnectionApi(createTransport('', () => null, async () => json({ ...capability, targets: [] })));
  const data = await client.connections();
  assert.deepEqual(data.targets, []);
  assert.throws(() => applyRegistration(data, candidate.app_url, registered), /does not match/);
});

test('real client preserves unsupported, auth, provenance and unreachable errors without retrying', async () => {
  for (const [status, code] of [[401, 'owner_session_required'], [403, 'csrf'], [422, 'connection_provenance'], [503, 'connection_target_unavailable']]) {
    let calls = 0;
    const client = createConnectionApi(createTransport('', () => 'test-only-csrf', async () => { calls += 1; return json({ detail: { code, message: 'Test-only prerequisite failure', gates: [{ code, message: 'Verification is missing.', action: 'Refresh supported app setup.' }] } }, status); }));
    await assert.rejects(client.registerConnection(candidate.app_url), error => error instanceof ApiError && error.status === status && error.code === code && error.message.includes('Refresh supported app setup.'));
    assert.equal(calls, 1);
  }
  const old = createConnectionApi(createTransport('', () => null, async () => json({ detail: 'Not Found' }, 404)));
  await assert.rejects(old.connections(), error => error instanceof ApiError && error.status === 404);
  const partial = createConnectionApi(createTransport('', () => null, async () => json({ version: '1' })));
  await assert.rejects(partial.connections(), error => error instanceof ApiError && error.code === 'contract_mismatch');
});
