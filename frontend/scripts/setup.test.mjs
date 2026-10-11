import assert from 'node:assert/strict';
import test from 'node:test';
import { readinessFacts } from '../src/setup.ts';

const readiness = {
  target_url: 'http://127.0.0.1:3301', max_personas: 20, cfo_plan_status: 'imported',
  gates: [{ code: 'adapter_check', message: 'Execution is blocked.', action: 'Verify the healthy adapter.' }],
  integrations: { dodo: { status: 'configured', detail: 'Credentials configured; execution is not verified.' } },
};

test('registered URL and imported plan never imply verified source or successful execution', () => {
  const facts = readinessFacts(readiness, '');
  assert.equal(facts.appStatus, 'Registered URL');
  assert.equal(facts.sourceStatus, 'Not reported');
  assert.equal(facts.services[0].status, 'configured');
  assert.deepEqual(facts.gates, readiness.gates);
  assert.equal(facts.canSelectApp, true); // Selects a URL only; the quote remains authoritative.
});

test('failed refresh retains last reported facts as stale and cannot select that app', () => {
  const facts = readinessFacts(readiness, 'Test-only unavailable service');
  assert.equal(facts.freshness, 'stale');
  assert.equal(facts.appUrl, readiness.target_url);
  assert.equal(facts.canSelectApp, false);
  assert.deepEqual(facts.gates, readiness.gates);
});

test('missing service data stays unknown, while an actual empty target list is unregistered', () => {
  assert.equal(readinessFacts(null, '').freshness, 'loading');
  assert.equal(readinessFacts(null, 'Test-only network error').appStatus, 'Unknown');
  assert.equal(readinessFacts({ ...readiness, target_url: null }, '').appStatus, 'Not registered');
  assert.equal(readinessFacts({ ...readiness, gates: [] }, '').sourceStatus, 'Not reported');
});
