import type { Counts, Finding, Gate, Provenance, Run, RunEvent, TargetProvenance } from './contracts';

export const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, character => {
  switch (character) {
    case '&': return '&amp;'; case '<': return '&lt;'; case '>': return '&gt;';
    case '"': return '&quot;'; case "'": return '&#39;'; default: return character;
  }
});
export const number = (value: number): string => value.toLocaleString('en-US');
export const money = (value: number): string => (value / 1_000_000).toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 6 });
export const humanize = (value: string): string => value.replace(/[_-]/g, ' ').replace(/^\w/, character => character.toUpperCase());
export function providerNotice(source: { provider: string | undefined; provenance: Provenance | undefined; estimate: boolean; targetProvenance?: TargetProvenance | null }): string {
  const provider = source.provenance?.payment_truth || source.provider;
  const costBasis = source.provenance ? source.provenance.cost_basis : source.estimate ? 'non_cfo_estimate' : null;
  const costLabel = costBasis === 'non_cfo_estimate' ? ' · Non-CFO estimate' : costBasis === 'cfo_forecast' ? ' · Imported cost forecast' : '';
  const planLink = source.provenance ? link(source.provenance.cfo_plan_url, 'Imported cost plan') : '';
  if (provider === 'local-fixture' || provider === 'local_fixture') return `<div class="provider-notice"><strong>Local fixture${costLabel}</strong><p>Test payment receipts come from the owned local fixture. They are not verified Dodo payments.</p>${planLink}</div>`;
  if (!provider) return '<p class="provider-notice">Payment provider provenance has not been reported. Do not treat these records as provider verification.</p>';
  const target = source.targetProvenance;
  const disclosure = target
    ? `<p><strong>Owned licensed sandbox · ${escape(target.license)}</strong>. ${target.controlled_fulfillment_defect ? 'The original baseline includes an intentionally controlled fulfillment defect.' : 'The original baseline is a healthy sandbox control.'} Synthetic personas exercise Dodo Test Mode checkout. Each provider receipt and its matching account access are checked separately.</p>${link(target.upstream_url, 'Licensed target source')}`
    : provider === 'dodo' || provider === 'dodo_sandbox' ? '<p>Target source and controlled-fault provenance were not recorded for this saved contract.</p>' : '';
  return `<div class="provider-notice"><strong>Payment source: ${escape(humanize(provider))}${costLabel}</strong>${disclosure}<p>Verify each receipt and its account evidence.</p>${planLink}</div>`;
}
export function findingEvidenceLabel(finding: Finding): string {
  return finding.measured ? 'Measured evidence' : finding.evidence.provider === 'local-fixture' ? 'Recorded fixture defect · provider not measured' : 'Unverified lead';
}
export function repairableFinding(finding: Finding, current: Pick<Run, 'pass' | 'phase' | 'provider' | 'provenance'>): boolean {
  return current.pass === 'before' && current.phase === 'completed' && (finding.measured || finding.evidence.provider === 'local-fixture' && (current.provider === 'local-fixture' || current.provenance?.provider_mode === 'local_fixture'));
}
export const timestamp = (value: string): string => {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
};
export function safeUrl(value: unknown): string | null {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const url = new URL(value, window.location.origin);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
export const link = (value: unknown, label: string): string => {
  const url = safeUrl(value);
  return url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>` : '';
};
export const gates = (items: Gate[]): string => items.length ? `<div class="gates">${items.map(gate => `<article class="gate"><h3>${escape(gate.message)}</h3><p>${escape(gate.action)}</p></article>`).join('')}</div>` : '';

// Defense in depth for sanitized backend evidence; never download or display credential fields.
export function redact(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
      /password|secret|authorization|cookie|access_token|api_key|credential/i.test(key) ? '[redacted]' : redact(item),
    ]));
  }
  return value;
}
export const json = (value: unknown): string => escape(JSON.stringify(redact(value), null, 2));
export function evidence(value: Record<string, unknown>): string {
  if (!Object.keys(value).length) return '<p class="muted">No evidence has been recorded.</p>';
  return Object.entries(value).map(([key, item]) => {
    if (/password|secret|authorization|cookie|access_token|api_key|credential/i.test(key)) return '';
    const url = safeUrl(item);
    if (url && /screenshot|snapshot|image/i.test(key)) return `<figure class="actual-snapshot"><a href="${escape(url)}" target="_blank" rel="noopener noreferrer"><img src="${escape(url)}" alt="Recorded browser evidence" loading="lazy" referrerpolicy="no-referrer"></a><figcaption>${escape(humanize(key))} · ${link(url, 'Open full-size screenshot')}</figcaption></figure>`;
    if (url && /url|link|trace/i.test(key)) return `<p class="evidence-link">${link(item, humanize(key))}</p>`;
    if (item !== null && typeof item === 'object') return `<details class="record"><summary>${escape(humanize(key))}</summary><pre>${json(item)}</pre></details>`;
    return `<div class="evidence-row"><strong>${escape(humanize(key))}</strong><span>${escape(item)}</span></div>`;
  }).join('');
}
export function eventSummary(event: RunEvent): string {
  for (const key of ['message', 'summary', 'description', 'action', 'reason']) {
    const value = event.data[key];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return '';
}
export function eventList(events: RunEvent[]): string {
  return events.length ? `<ol class="timeline">${events.map(event => `<li><strong>${escape(humanize(event.kind))}</strong><p>${escape(eventSummary(event))}</p><small>${escape(timestamp(event.created_at))}</small><details class="record"><summary>Recorded details</summary>${evidence(event.data)}</details>${link(event.trace_url, 'Open full trace')}</li>`).join('')}</ol>` : '<p class="empty">No steps have been recorded yet. A queued customer has not started a browser session.</p>';
}
export const outcomeRows: { key: keyof Counts; label: string }[] = [
  { key: 'completed', label: 'Passed' }, { key: 'failed', label: 'Failed' },
  { key: 'abandoned', label: 'Abandoned' }, { key: 'pending', label: 'Pending' }, { key: 'blocked', label: 'Blocked' },
];
export function outcomes(counts: Counts, requested: number): string {
  return `<div class="outcomes">${outcomeRows.map(({ key, label }) => `<div class="outcome"><strong>${number(counts[key])}<small> / ${number(requested)}</small></strong>${label}</div>`).join('')}</div>`;
}
export function comparisonTable(before: Counts, after: Counts, requested: number): string {
  return `<table class="report-table comparison-table"><thead><tr><th scope="col">Outcome</th><th scope="col">Initial run</th><th scope="col">After repair</th></tr></thead><tbody>${outcomeRows.map(({ key, label }) => `<tr><th scope="row">${label}</th><td>${number(before[key])} / ${number(requested)}</td><td>${number(after[key])} / ${number(requested)}</td></tr>`).join('')}<tr><th scope="row">Executed</th><td>${number(before.executed)} / ${number(requested)}</td><td>${number(after.executed)} / ${number(requested)}</td></tr></tbody></table>`;
}
