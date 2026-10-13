import { z } from 'zod';
import type { createTransport } from './transport.ts';
import { escape, evidence, humanize, link } from './presentation.ts';

const traceId = z.string().regex(/^[a-f0-9]{32}$/);
export const traceIncidentSchema = z.object({
  run_id: traceId, incident_id: z.string().uuid(), source: z.literal('official_neatlogs_ui'),
  status: z.string(), scope: z.enum(['current_run', 'prior_supporting_incident']),
  viewer_url: z.string().url(), trace_ids: z.array(traceId), observed_at: z.string(),
  provider: z.enum(['local-fixture', 'dodo']), title: z.string().optional(),
  evidence: z.string().optional(), resolution_evidence: z.string().optional(),
});
export const traceReceiptSchema = z.object({
  trace_id: traceId, persona_id: z.string().nullable(), viewer_url: z.string(),
  delivery_flushed: z.boolean(), finalized_readback: z.enum(['verified', 'not_verified']),
  readback_url: z.string(), payload_gates: z.array(z.string()),
});
export const traceReceiptsSchema = z.object({ run_id: traceId, traces: z.array(traceReceiptSchema), incidents: z.array(traceIncidentSchema).optional().default([]) });
const readbackFields = {
  trace_id: traceId, run_id: traceId, persona_id: z.string().nullable(), pass: z.enum(['before', 'after']),
  viewer_url: z.string(), source: z.literal('official_neatlogs_trace_api'),
  spans: z.array(z.object({ span_id: z.string(), parent_span_id: z.string().nullable().optional(), name: z.string(), data: z.record(z.string(), z.unknown()) })),
  reason: z.string(), http_status: z.number().int().optional(),
  hosted_status: z.enum(['success', 'error']).optional(),
  incidents: z.array(traceIncidentSchema).optional().default([]),
};
export const traceReadbackSchema = z.discriminatedUnion('finalized_readback', [
  z.object({ ...readbackFields, finalized_readback: z.literal('verified'), delivery_flushed: z.literal(true) }),
  z.object({ ...readbackFields, run_id: traceId.optional(), persona_id: z.string().nullable().optional(), pass: z.enum(['before', 'after']).optional(), viewer_url: z.string().optional(), source: z.literal('official_neatlogs_trace_api').optional(), spans: readbackFields.spans.optional(), finalized_readback: z.literal('not_verified'), delivery_flushed: z.boolean().optional() }),
]);
export type TraceReceipts = z.infer<typeof traceReceiptsSchema>;
export type TraceReadback = z.infer<typeof traceReadbackSchema>;
export type TraceState = { kind: 'loading' } | { kind: 'ready'; data: TraceReceipts } | { kind: 'unavailable'; message: string };

function renderIncidents(incidents: z.infer<typeof traceIncidentSchema>[]): string {
  return incidents.map(incident => `<article class="persona-section"><h3>${escape(incident.title ?? 'Neatlogs incident')}</h3><p>${escape(humanize(incident.status))} · ${incident.scope === 'prior_supporting_incident' ? 'Prior supporting incident' : 'Incident for this run'} · ${incident.provider === 'local-fixture' ? 'Local fixture' : 'Dodo sandbox'}</p><p class="small muted">Observed ${escape(incident.observed_at)}</p>${incident.evidence ? `<p>${escape(incident.evidence)}</p>` : ''}${incident.resolution_evidence ? `<p>Resolution evidence: ${escape(incident.resolution_evidence)}</p>` : ''}${link(incident.viewer_url, 'Open actual Neatlogs incident')}</article>`).join('');
}

export function createTraceApi(request: ReturnType<typeof createTransport>) {
  return {
    traces: (runId: string, token: string) => request(`/runs/${encodeURIComponent(runId)}/traces`, traceReceiptsSchema, { token }),
    inspectTrace: (runId: string, id: string, token: string) => request(`/runs/${encodeURIComponent(runId)}/traces/${encodeURIComponent(id)}`, traceReadbackSchema, { token }),
  };
}

export function renderTraceReceipts(runId: string, state: TraceState | undefined, personaId?: string): string {
  if (!state || state.kind === 'loading') return '<p class="small muted">Loading recorded trace delivery receipts…</p>';
  if (state.kind === 'unavailable') return `<p class="small status-bad">${escape(state.message)}</p>`;
  if (state.data.run_id !== runId) return '<p class="status-bad">Trace receipts belong to a different run.</p>';
  const receipts = state.data.traces.filter(item => personaId === undefined || item.persona_id === personaId);
  const selectedTraceIds = new Set(receipts.map(receipt => receipt.trace_id));
  const incidents = state.data.incidents.filter(incident => personaId === undefined || incident.trace_ids.some(id => selectedTraceIds.has(id)));
  return `<section class="trace-receipts"><h3>Neatlogs evidence</h3>${receipts.length ? receipts.map(receipt => `<article class="persona-section"><p><strong>${receipt.delivery_flushed ? 'Delivery flushed' : 'Delivery not verified'}</strong> · ${receipt.finalized_readback === 'verified' ? 'Finalized readback verified' : 'Finalized readback not verified'}</p><p class="small muted">${escape(receipt.trace_id)} · ${escape(receipt.persona_id ?? 'Run trace')}</p>${receipt.payload_gates.length ? `<p class="status-bad">Payload gates: ${escape(receipt.payload_gates.join(', '))}</p>` : ''}<div class="actions">${link(receipt.viewer_url, 'Open Neatlogs viewer')}<button class="secondary" data-action="remote-trace" data-id="${escape(runId + ':' + receipt.trace_id)}">Read actual hosted trace</button></div></article>`).join('') : '<p class="small muted">No Neatlogs receipt was recorded for this customer or run. Local steps remain inspectable.</p>'}${renderIncidents(incidents)}</section>`;
}

export function renderTraceReadback(result: TraceReadback): string {
  const spans = result.spans?.length ? `<h3>Actual hosted spans</h3>${result.spans.map(span => `<article class="persona-section"><h3>${escape(span.name)}</h3><p class="small muted">Span ${escape(span.span_id)}</p>${evidence(span.data)}</article>`).join('')}`
    : '<p class="small muted">No verified hosted spans are available. An unverified trace does not become successful evidence.</p>';
  return `<p class="sample-label">Trace ${escape(result.trace_id)}</p><p class="${result.finalized_readback === 'verified' ? 'status-good' : 'status-bad'}">${result.finalized_readback === 'verified' ? 'Delivered and finalized hosted spans matched the saved trace.' : 'Hosted trace has not been verified.'}</p>${result.hosted_status ? `<p>Hosted result: ${escape(humanize(result.hosted_status))}. Trace verification does not change the customer's outcome.</p>` : ''}<p>${escape(humanize(result.reason))}${result.http_status === undefined ? '' : ` · HTTP ${result.http_status}`}</p>${link(result.viewer_url, 'Open Neatlogs viewer')}${renderIncidents(result.incidents)}${spans}`;
}
