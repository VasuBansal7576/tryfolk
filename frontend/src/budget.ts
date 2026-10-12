import type { Quote, QuoteV2 } from './contracts.ts';
import { escape, link, money, number, timestamp } from './presentation.ts';

export function hasBudgetDetails(quote: Quote): quote is QuoteV2 {
  return quote.subtotal_microusd !== undefined;
}
const row = (label: string, value: number, className = ''): string =>
  `<div class="${className}"><span>${escape(label)}</span><strong>${money(value)}</strong></div>`;
const percent = (basisPoints: number): string => `${(basisPoints / 100).toLocaleString('en-US', { maximumFractionDigits: 2 })}%`;
const allowanceNames: Record<string, string> = {
  before_per_persona_microusd: 'Initial test per person', after_per_persona_microusd: 'After test per person',
  discovery_microusd: 'Discovery', generation_microusd: 'Customer generation',
  investigation_microusd: 'Investigation', repair_microusd: 'Repair', known_replay_microusd: 'Known-failure replay',
  contingency_basis_points: 'Contingency rate', quote_budget_microusd: 'Customer quote cap', lifetime_budget_microusd: 'Lifetime ceiling',
};
const assumptionName = (key: string): string => allowanceNames[key] || key.replace(/_microusd$/, '').replaceAll('_', ' ');

export function quoteBudget(quote: Quote, accepted = false): string {
  const details = hasBudgetDetails(quote);
  const retry = quote.kind === 'baseline_retry';
  const base = row(`Initial test base · ${number(quote.persona_count)} people`, quote.before_microusd)
    + row(`After test base · the same ${number(quote.persona_count)} people`, quote.after_microusd)
    + row('Discovery, generation, investigation, repair and replay overhead', quote.overhead_microusd);
  const totals = details
    ? row('Base subtotal', quote.subtotal_microusd)
      + row(`Explicit contingency · ${percent(quote.contingency_basis_points)} of subtotal`, quote.contingency_microusd)
    : '<p class="small muted">This saved contract does not disclose a separate contingency. Its original total is shown unchanged.</p>';
  const caps = details
    ? `<section class="budget-limits"><h3>${accepted ? 'Limits recorded with this quote' : 'Two independent budget checks'}</h3>
      <div class="budget-rows">${row('Customer cap for this quote', quote.quote_limit_microusd)}${row('Lifetime budget ceiling', quote.lifetime_limit_microusd)}${row('Lifetime estimated allowance already used', quote.ledger.estimated_consumed_microusd)}${row('Lifetime allowance held by existing reservations', quote.ledger.held_microusd)}${row(retry ? 'Lifetime remaining before original hold release, at quote time' : accepted ? 'Lifetime remaining at quote time' : 'Lifetime remaining for new reservations', quote.lifetime_available_microusd)}</div>
      <p class="small muted">${retry ? 'This is the ledger snapshot before releasing the original attempt’s unused hold. The separate retry budget shows that release and the complete new reservation. The service rechecks both the quote cap and lifetime allowance on acceptance.' : accepted ? 'These are the original quote’s ledger snapshot, not today’s balance. This run keeps its accepted prices and customer count.' : 'The full reservation must fit both the customer cap and lifetime remaining. Existing consumption and holds are already deducted from the lifetime remaining shown here. The service checks the ledger again when you confirm.'}</p></section>`
    : `<div class="budget-rows">${row('Available allowance at quote time', quote.available_microusd)}</div><p class="small muted">Independent quote and lifetime limits were not included in this contract.</p>`;
  const protection = details
    ? `<section class="after-protection"><h3>${accepted ? 'Originally protected for the retest' : 'Protected for the same-customer retest'}</h3><p><strong>${money(quote.after_reserve_microusd)}</strong> for all ${number(quote.persona_count)} ${accepted ? 'original customers' : 'requested customers'}${quote.after_reserve_includes_known_replay ? ', known-failure replay' : ''}, and ${money(quote.after_contingency_microusd)} after-pass contingency.</p><p class="small muted">This amount is part of the total reservation, not an extra charge. Before-pass work cannot use it. Unused contingency is released after the complete matched retest or cancellation.</p></section>`
    : '<p class="small muted">The exact protected after-pass amount was not included in this saved contract.</p>';
  const allowances = details ? `<details class="record"><summary>Original execution allowances</summary><div class="budget-rows">${Object.entries(quote.execution_allowances).map(([key, amount]) => row(assumptionName(key), amount)).join('')}</div><p class="small muted">Reported category caps. The quoted overhead and total above remain authoritative.</p></details>` : '';
  return `<div class="budget-rows">${base}${totals}${row(accepted ? 'Original total reserved for both passes' : 'Total reserved upfront for both passes', quote.reserved_microusd, 'budget-total')}</div>${protection}${caps}${allowances}<p class="small muted">Forecast allowance and contingent reserve are separate from measured cash. No billing or charge is performed by this app.</p>`;
}

export function affordabilityMessage(quote: Quote): string {
  if (quote.affordable) return '';
  if (quote.kind === 'baseline_retry') return `The complete ${money(quote.reserved_microusd)} new reservation must fit the quote cap and ${money(quote.retry_budget.available_after_release_microusd)} lifetime allowance after releasing only the original unused hold. All ${number(quote.persona_count)} saved customers remain selected. Resolve the reported budget limit before reviewing another quote.`;
  if (!hasBudgetDetails(quote)) return 'This request exceeds the available allowance. Resolve the reported budget limit before reviewing another quote. The requested population has not changed.';
  const reasons = [];
  if (quote.reserved_microusd > quote.quote_limit_microusd) reasons.push(`the ${money(quote.quote_limit_microusd)} customer quote cap`);
  if (quote.reserved_microusd > quote.lifetime_available_microusd) reasons.push(`the ${money(quote.lifetime_available_microusd)} lifetime remaining`);
  return `The ${money(quote.reserved_microusd)} full reservation exceeds ${reasons.join(' and ') || 'the service’s available budget'}. All ${number(quote.persona_count)} requested customers remain selected. Resolve the reported budget limit before reviewing another quote.`;
}

export function forecastSource(quote: Quote, pendingPage: string | null = null): string {
  const plan = quote.cfo_plan;
  if (quote.cfo_plan_status !== 'imported') return `<section class="forecast-source"><h3>Cost forecast pending</h3><p>No validated native forecast is active for this quote. Account access and a draft Page do not activate pricing.</p>${link(pendingPage, 'Open native CFO Page under review')}<p class="small muted">The amounts above are the service’s local estimates. Draft assumptions do not change this quote or its customer count.</p></section>`;
  if (!plan) return `<section class="forecast-source"><h3>Original imported forecast</h3><p>The service reports an imported forecast for this saved contract. Row assumptions and inspection metadata were not included.</p>${link(quote.provenance?.cfo_plan_url, 'Open original native CFO Page')}</section>`;
  const comparison = hasBudgetDetails(quote) ? quote.forecast_comparison : null;
  const difference = comparison && comparison.native_reserved_microusd !== quote.reserved_microusd
    ? `<p class="forecast-difference">Native forecast total ${money(comparison.native_reserved_microusd)}. Scheduler reservation ${money(quote.reserved_microusd)}. The scheduler’s stricter category allowances are included in the quote you approve.</p>` : '';
  const inspection = plan.schema_version === 2
    ? `<p class="small muted">Service reports a finalized, inspected forecast. Inspected ${escape(timestamp(plan.inspected_at))}.</p><p class="small muted">Inspection evidence SHA256: ${escape(plan.inspection_evidence_sha256)}</p>`
    : '<p class="small muted">This is the legacy forecast retained by the original contract. It has not been repriced using a newer plan.</p>';
  const rows = Object.entries(plan.assumptions).map(([key, value]) => {
    const provenance = plan.schema_version === 2 ? plan.assumption_provenance[key] : undefined;
    return `<tr><th scope="row">${escape(assumptionName(key))}</th><td>${key === 'contingency_basis_points' ? percent(value) : money(value)}</td><td>${provenance ? `${escape(provenance.reference)}<small>Forecast · evidence ${escape(provenance.evidence_sha256)}</small>` : 'Original forecast assumption'}</td></tr>`;
  }).join('');
  const native = comparison ? `<h4>Native ledger snapshot</h4><div class="budget-rows">${row('Estimated allowance used in native snapshot', comparison.native_ledger_snapshot.estimated_consumed_microusd)}${row('Held in native snapshot', comparison.native_ledger_snapshot.held_microusd)}${row('Available in native snapshot', comparison.native_ledger_snapshot.available_microusd)}</div><p class="small muted">Observed ${escape(timestamp(comparison.native_ledger_snapshot.observed_at))}. ${escape(comparison.basis)} The native available balance is a snapshot, not another ceiling or deduction.</p><p class="small muted">Native ledger evidence SHA256: ${escape(comparison.native_ledger_snapshot.evidence_sha256)}</p><h4>Overhead assumptions and scheduler caps</h4><table class="report-table"><thead><tr><th scope="col">Category</th><th scope="col">Native forecast</th><th scope="col">Scheduler cap</th></tr></thead><tbody>${Object.entries(comparison.scheduler_overhead_caps).map(([key, amount]) => `<tr><th scope="row">${escape(assumptionName(key))}</th><td>${comparison.native_overhead_caps[key] === undefined ? 'Not reported' : money(comparison.native_overhead_caps[key])}</td><td>${money(amount)}</td></tr>`).join('')}</tbody></table>` : '';
  return `<section class="forecast-source"><h3>Original native cost forecast</h3>${link(plan.source_url, 'Open native CFO Page')}<p class="small muted">Plan ${escape(plan.plan_id)} · Snapshot SHA256: ${escape(plan.snapshot_sha256)}</p>${inspection}${difference}<details class="record"><summary>Forecast assumptions and inspection evidence</summary><div class="table-scroll"><table class="report-table forecast-assumptions"><thead><tr><th scope="col">Assumption</th><th scope="col">Forecast value</th><th scope="col">Source row</th></tr></thead><tbody>${rows}</tbody></table></div>${native}</details><p class="small muted">These are planning assumptions, not measured cash or observed customer outcomes.</p></section>`;
}
