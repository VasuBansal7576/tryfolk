import assert from 'node:assert/strict';
import test from 'node:test';
import { z } from 'zod';
import { ApiError, createTransport } from '../src/transport.ts';

const responseSchema = z.object({ ok: z.literal(true) });
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });

test('owner GET uses the protected cookie; deliberate mutation uses only the current in-memory CSRF token', async () => {
  const calls = [];
  let csrf = null;
  const request = createTransport('', () => csrf, async (url, options) => {
    calls.push({ url, options });
    return json({ ok: true });
  });
  await request('/connections', responseSchema);
  assert.equal(calls[0].options.credentials, 'include');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[0].options.body, undefined);
  assert.equal(calls[0].options.headers.has('X-CSRF-Token'), false);
  assert.equal(calls[0].options.headers.has('Authorization'), false);
  csrf = 'test-only-csrf';
  await request('/connections', responseSchema, { body: { app_url: 'http://127.0.0.1:3301' } });
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.method, 'POST');
  assert.equal(calls[1].options.headers.get('X-CSRF-Token'), csrf);
  assert.deepEqual(JSON.parse(calls[1].options.body), { app_url: 'http://127.0.0.1:3301' });
  assert.ok(calls[1].options.signal instanceof AbortSignal);
});

test('auth refusal and unsupported registration remain errors with the actual prerequisite action', async () => {
  const denied = createTransport('', () => null, async () => json({ detail: { code: 'owner_required', message: 'Authorize the owner.' } }, 403));
  await assert.rejects(denied('/connections', responseSchema), error => error instanceof ApiError && error.status === 403 && error.code === 'owner_required');
  const blocked = createTransport('', () => 'test-only-csrf', async () => json({ detail: {
    code: 'unsupported_target', message: 'This target is unsupported.', gates: [{ message: 'Owned source is absent.', action: 'Register a supported owned repository.' }],
  } }, 422));
  await assert.rejects(blocked('/connections', responseSchema, { body: { app_url: 'https://test-only.invalid' } }), error => error instanceof ApiError && error.status === 422 && error.message.includes('Register a supported owned repository.'));
});

test('partial successful response, bad JSON and network loss never become connection success', async () => {
  for (const response of [json({}), new Response('Test-only non-JSON', { status: 200 })]) {
    const request = createTransport('', () => null, async () => response);
    await assert.rejects(request('/connections', responseSchema), error => error instanceof ApiError && error.code === 'contract_mismatch');
  }
  let calls = 0;
  const unavailable = createTransport('', () => null, async () => { calls += 1; throw new Error('Test-only response loss'); });
  await assert.rejects(unavailable('/connections', responseSchema), error => error instanceof ApiError && error.status === 0);
  assert.equal(calls, 1); // No automatic retry or registration occurs.
});
