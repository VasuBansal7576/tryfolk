import assert from 'node:assert/strict';
import test from 'node:test';
import { targetProvenanceSchema } from '../src/contracts.ts';
import { providerNotice } from '../src/presentation.ts';

// Invented source pins for contract boundaries only; never shown as live product evidence.
const original = {
  target_id: 'tryfolk-owned-dodo-special-downloads', ownership: 'owned_sandbox', license: 'MIT',
  license_sha256: 'd'.repeat(64), upstream_commit: 'ef7cbd980d8f1e57b036dc2af71ff05521b6175a',
  upstream_url: 'https://github.com/dodopayments/dodo-checkout-demo/tree/ef7cbd980d8f1e57b036dc2af71ff05521b6175a',
  source_commit: 'a'.repeat(40), source_digest: 'b'.repeat(64), baseline_target_revision: 'sha256:'+'c'.repeat(64),
  baseline_mode: 'broken', baseline_fulfillment_disposition: 'suppressed', controlled_fulfillment_defect: true, sandbox: 'dodo_test_mode',
};

test('pinned controlled target disclosure survives after-pass effective fault removal', () => {
  const parsed = targetProvenanceSchema.parse(original);
  const text = providerNotice({provider:'dodo',provenance:undefined,estimate:true,targetProvenance:parsed});
  assert.match(text,/Owned licensed sandbox/);
  assert.match(text,/original baseline.*intentionally controlled fulfillment defect/i);
  assert.match(text,/Dodo Test Mode/);
  assert.match(text,/synthetic personas/i);
  assert.deepEqual(parsed, original);
  assert.equal(providerNotice({provider:'dodo',provenance:undefined,estimate:true,targetProvenance:structuredClone(parsed)}),text);
});

test('provider label alone cannot invent owned-source or controlled-fault provenance', () => {
  const unknown = providerNotice({provider:'dodo',provenance:undefined,estimate:true});
  assert.ok(!unknown.includes('Owned licensed sandbox'));
  assert.ok(!unknown.includes('intentionally controlled'));
  assert.match(unknown,/Target source and controlled-fault provenance were not recorded/);
  const healthy = targetProvenanceSchema.parse({...original,baseline_mode:'healthy',baseline_fulfillment_disposition:'granted',controlled_fulfillment_defect:false});
  assert.ok(!providerNotice({provider:'dodo',provenance:undefined,estimate:true,targetProvenance:healthy}).includes('intentionally controlled'));
});
