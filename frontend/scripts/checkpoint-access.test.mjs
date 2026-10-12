import assert from 'node:assert/strict';
import test from 'node:test';
import { repairSchema } from '../src/contracts.ts';
import { canVerifyCheckpoint, createCheckpointApi, renderCheckpointAccess } from '../src/checkpoint-access.ts';
import { ApiError, createTransport } from '../src/transport.ts';
import { savedRun } from './persona-inputs.mjs';

// OFFLINE HTTP/DTO FIXTURES: no genuine capture, publication or live access receipt.
globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const object = 'a'.repeat(40), source = 'b'.repeat(40);
const repository = 'OfflineOwner/test-only-checkpoint-fixture';
const raw = (id, path) => `https://raw.githubusercontent.com/${repository}/${id}/${path}`;
const repair = {
  id: 'test-only-repair', status: 'completed', base_revision: 'test-only-base', head_revision: source,
  preview_url: 'http://127.0.0.1:3310', checkpoint_id: 'test-only-checkpoint', checkpoint_url: null,
  target_revision: 'test-only-revision', source_digest: 'c'.repeat(64), gates: [],
};
const access = {
  kind: 'github_git_checkpoint', status: 'anonymous_verified', repository,
  checkpoint_id: repair.checkpoint_id, checkpoint_ref: 'refs/entire/checkpoints/test-only-checkpoint',
  checkpoint_object: object, source_commit: source, url: raw(object, 'metadata.json'),
  transcript_url: raw(object, '0/full.jsonl'), source_url: raw(source, 'lib/fulfillment-policy.ts'),
  binding_sha256: 'd'.repeat(64), receipt_sha256: 'e'.repeat(64), verified_at: '2020-01-01T00:00:00+00:00',
  hosted_entire_url: null, hosted_entire_status: 'unverified',
  resource_digests: [
    [object, 'metadata.json'], [object, '0/metadata.json'], [object, '0/full.jsonl'], [source, 'lib/fulfillment-policy.ts'],
  ].map(([object, path]) => ({ object, path, bytes: 20, sha256: 'f'.repeat(64) })),
};
const verified = { ...repair, checkpoint_url: access.url, checkpoint_access: access };
const json = (data, status = 200) => new Response(JSON.stringify(data), { status });

test('legacy checkpoint links remain unverified, and hosted UI access is never inferred from Git readback', () => {
  const legacy = repairSchema.parse({ ...repair, checkpoint_url: 'https://test-only.invalid/checkpoint' });
  assert.equal(legacy.checkpoint_access, undefined);
  assert.match(renderCheckpointAccess(legacy), /Saved checkpoint link · access unverified/);
  assert.doesNotMatch(renderCheckpointAccess(legacy), /Public Git checkpoint ·/);
  const parsed = repairSchema.parse(verified);
  assert.deepEqual(parsed.checkpoint_access, access);
  const rendered = renderCheckpointAccess(parsed);
  assert.match(rendered, /Public Git checkpoint · test-only-checkpoint/);
  assert.match(rendered, /historical receipt; current availability has not been rechecked/);
  assert.match(rendered, /Hosted Entire UI/);
  assert.match(rendered, /Unverified/);
  assert.match(rendered, /does not establish a successful payment/);
});

test('partial receipts, wrong repair/source joins, mutable or credential-bearing URLs cannot become verified links', () => {
  for (const checkpoint_access of [
    {}, { ...access, status: 'unverified' }, { ...access, checkpoint_id: 'test-only-other' },
    { ...access, source_commit: 'f'.repeat(40) }, { ...access, url: raw('main', 'metadata.json') },
    { ...access, url: 'https://test:credential@raw.githubusercontent.com/data' },
    { ...access, repository: '../test-only-fixture' }, { ...access, transcript_url: 'javascript:alert(1)' },
    { ...access, hosted_entire_url: 'https://test-only.invalid/hosted', hosted_entire_status: 'verified' },
    { ...access, resource_digests: [] },
  ]) assert.equal(repairSchema.safeParse({ ...verified, checkpoint_access }).success, false);
});

test('owner confirmation requires a completed genuine registered repair, independently from replay eligibility', () => {
  const baseline = savedRun({ phase: 'completed', provider: 'dodo', repair });
  const original = structuredClone(baseline);
  assert.equal(canVerifyCheckpoint(baseline, true), true);
  assert.equal(canVerifyCheckpoint(baseline, false), false);
  for (const current of [
    { ...baseline, phase: 'blocked' }, { ...baseline, pass: 'after' }, { ...baseline, provider: 'local-fixture' },
    { ...baseline, repair: null }, { ...baseline, repair: { ...repair, status: 'blocked' } },
    { ...baseline, repair: { ...repair, checkpoint_id: null } }, { ...baseline, repair: verified },
  ]) assert.equal(canVerifyCheckpoint(current, true), false);
  assert.deepEqual(baseline, original); // Population, customer verdicts and budget stay untouched.
});

test('verification makes one strict-empty-body owner-session POST and retains the exact historical receipt', async () => {
  const calls = [];
  const request = createTransport('', () => 'test-only-csrf', async (url, options) => {
    calls.push({ url, options }); return json(verified);
  });
  const api = createCheckpointApi(request);
  const result = await api.verifyCheckpointAccess('test-only-run', repairSchema.parse(repair));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/runs/test-only-run/repairs/test-only-repair/checkpoint-access');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.body, '{}');
  assert.equal(calls[0].options.credentials, 'include');
  assert.equal(calls[0].options.headers.get('X-CSRF-Token'), 'test-only-csrf');
  assert.equal(calls[0].options.headers.has('Authorization'), false);
  assert.equal(result.checkpoint_access.verified_at, access.verified_at);
  assert.equal(result.checkpoint_access.receipt_sha256, access.receipt_sha256);
});

test('access response cannot replace the selected repair or preview, and missing receipts remain errors', async () => {
  for (const response of [
    { ...verified, id: 'test-only-other' }, { ...verified, status: 'blocked' },
    { ...verified, base_revision: 'test-only-other-base' }, { ...verified, preview_url: 'http://127.0.0.1:9999' },
    { ...verified, target_revision: 'test-only-other-revision' },
    { ...verified, source_digest: '0'.repeat(64) }, repair,
  ]) {
    const api = createCheckpointApi(createTransport('', () => null, async () => json(response)));
    await assert.rejects(api.verifyCheckpointAccess('test-only-run', repairSchema.parse(repair)),
      error => error instanceof ApiError && error.code === 'checkpoint_response_mismatch');
  }
});

test('every published 409 gate stays an actionable error, with no success result or automatic retry', async () => {
  for (const code of [
    'checkpoint_not_registered', 'checkpoint_capture_unavailable', 'checkpoint_capture_mismatch',
    'checkpoint_repository_unapproved', 'checkpoint_public_access_unverified',
    'checkpoint_public_bytes_mismatch', 'checkpoint_capture_changed', 'checkpoint_evidence_unavailable',
  ]) {
    let calls = 0;
    const api = createCheckpointApi(createTransport('', () => null, async () => {
      calls += 1; return json({ detail: { code, message: 'Test-only unavailable checkpoint.' } }, 409);
    }));
    await assert.rejects(api.verifyCheckpointAccess('test-only-run', repairSchema.parse(repair)),
      error => error instanceof ApiError && error.status === 409 && error.code === code && error.message.includes(code));
    assert.equal(calls, 1);
  }
});
