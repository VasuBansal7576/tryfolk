import assert from 'node:assert/strict';
import test from 'node:test';
import { createTraceApi, renderTraceReadback, renderTraceReceipts, traceReadbackSchema, traceReceiptsSchema } from '../src/traces.ts';
import { createTransport } from '../src/transport.ts';

globalThis.window = { location: { origin: 'http://127.0.0.1:8000' } };
const run_id = 'a'.repeat(32), trace_id = 'b'.repeat(32), persona_id = 'c'.repeat(32);
const receipt = { trace_id, persona_id, viewer_url: `https://app.neatlogs.com/traces/${trace_id}`, delivery_flushed: true, finalized_readback: 'not_verified', readback_url: `/runs/${run_id}/traces/${trace_id}`, payload_gates: [] };
const readback = { trace_id, run_id, persona_id, pass: 'before', viewer_url: receipt.viewer_url, delivery_flushed: true, finalized_readback: 'verified', source: 'official_neatlogs_trace_api', spans: [{ span_id: '1', name: 'tryfolk.verification', data: { output_value: { outcome: 'failed' } } }], reason: 'trace_finalized_and_matched' };

test('trace receipts preserve delivery and finalization as distinct actual states per persona', () => {
 const data = traceReceiptsSchema.parse({ run_id, traces: [receipt, { ...receipt, trace_id: 'd'.repeat(32), persona_id: null }] });
 const html = renderTraceReceipts(run_id, { kind: 'ready', data }, persona_id);
 assert.ok(html.includes('Delivery flushed'));
 assert.ok(html.includes('Finalized readback not verified'));
 assert.ok(!html.includes('d'.repeat(32)));
 assert.ok(renderTraceReceipts(run_id, { kind: 'ready', data }, 'e'.repeat(32)).includes('No Neatlogs receipt'));
});

test('actual hosted readback preserves failed customer evidence without manufacturing a pass', () => {
 const html = renderTraceReadback(traceReadbackSchema.parse(readback));
 assert.ok(html.includes('finalized hosted spans matched'));
 assert.ok(html.includes('failed'));
 assert.ok(!html.includes('customer passed'));
 assert.equal(traceReadbackSchema.safeParse({ ...readback, delivery_flushed: false }).success, false);
 const pending = renderTraceReadback(traceReadbackSchema.parse({ trace_id, finalized_readback: 'not_verified', reason: 'local_receipt_pending' }));
 assert.ok(pending.includes('Hosted trace has not been verified'));
 assert.ok(pending.includes('No verified hosted spans'));
});

test('trace API requests use exact selected run and trace with same access token', async () => {
 const calls = [];
 const fetcher = async (url, options) => { calls.push({ url, options }); return Response.json(url.endsWith(trace_id) ? readback : { run_id, traces: [receipt] }); };
 const client = createTraceApi(createTransport('', () => null, fetcher));
 await client.traces(run_id, 'test-only-token');
 await client.inspectTrace(run_id, trace_id, 'test-only-token');
 assert.equal(calls[0].url, `/runs/${run_id}/traces`);
 assert.equal(calls[1].url, `/runs/${run_id}/traces/${trace_id}`);
 for (const { options } of calls) { assert.equal(options.method, 'GET'); assert.equal(options.headers.get('Authorization'), 'Bearer test-only-token'); }
});

test('actual incident links stay scoped to associated customer traces and retain observed status', () => {
 const incident = { run_id, incident_id: 'e98f18f3-857c-430f-a2f2-e6b44d45b2c9', source: 'official_neatlogs_ui', status: 'open', scope: 'current_run', viewer_url: 'https://app.neatlogs.com/incidents/e98f18f3-857c-430f-a2f2-e6b44d45b2c9', trace_ids: [trace_id], observed_at: '2026-10-12T20:00:00Z', provider: 'local-fixture', title: 'Actual failure investigation' };
 const data = traceReceiptsSchema.parse({ run_id, traces: [receipt, { ...receipt, trace_id: 'd'.repeat(32), persona_id: 'e'.repeat(32) }], incidents: [incident] });
 const html = renderTraceReceipts(run_id, { kind: 'ready', data }, persona_id);
 assert.ok(html.includes('Actual failure investigation'));
 assert.ok(html.includes('Open actual Neatlogs incident'));
 assert.ok(html.includes('Local fixture'));
 assert.ok(!html.includes('Resolution evidence'));
 assert.ok(!renderTraceReceipts(run_id, { kind: 'ready', data }, 'e'.repeat(32)).includes(incident.incident_id));
 const supporting = renderTraceReceipts(run_id, { kind: 'ready', data: { ...data, incidents: [{ ...incident, scope: 'prior_supporting_incident' }] } });
 assert.ok(supporting.includes('Prior supporting incident'));
 assert.ok(renderTraceReadback(traceReadbackSchema.parse({ ...readback, incidents: [incident] })).includes(incident.incident_id));
});
