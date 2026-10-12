import { baselineRetryQuoteSchema, createdRunSchema } from './contracts.ts';
import type { BaselineRetryQuote, RetryBudget, Run } from './contracts.ts';
import { escape, money, number } from './presentation.ts';
import { ApiError } from './transport.ts';
import type { createTransport } from './transport.ts';

function paymentAction(event: Run['events'][number]): boolean {
  const action = event.data.action;
  if (event.kind !== 'browser_action' || typeof action !== 'object' || action === null || !('_tryfolk' in action)) return false;
  const metadata = action._tryfolk;
  return typeof metadata === 'object' && metadata !== null && 'kind' in metadata && ['pay', 'confirm'].includes(String(metadata.kind));
}

export function baselineRetryBlocker(current: Run, owner: boolean, findingsAvailable: boolean, hasFindings: boolean): string | null {
  if (!owner) return 'Connect the owner session to review a saved-customer baseline retry.';
  if (current.provider !== 'dodo' || current.pass !== 'before' || current.phase !== 'blocked') return 'A baseline retry starts from a blocked initial Dodo attempt.';
  const n = current.persona_count;
  if (n < 1 || current.counts.requested !== n || current.counts.generated !== n || current.personas.length !== n
    || new Set(current.personas.map(person => person.id)).size !== n) return 'All original customers must be saved. A baseline retry cannot generate a replacement population.';
  if (current.baseline_retry || current.retry_of_run_id) return 'A saved baseline retry already exists. Inspect its attempt history instead of approving another.';
  if (current.counts.executed > 0 || [current.counts.completed, current.counts.failed, current.counts.abandoned, current.counts.pending].some(value => value > 0)) return 'Recorded customer outcomes require their own evidence review before any baseline retry.';
  if (!findingsAvailable) return 'Refresh the recorded findings before reviewing a baseline retry.';
  if (hasFindings || current.repair || current.comparison) return 'This attempt already has customer findings, a repair or a comparison. Inspect that evidence before continuing.';
  if (current.budget.released_microusd > 0) return 'The original hold was released. Inspect its saved attempt history before another request.';
  if (!current.source_commit || !current.source_digest) return 'The original source binding was not reported. Inspect its registered source before a retry.';
  if (current.events.some(event => ['verification', 'capability', 'scenario_payment_condition'].includes(event.kind) || paymentAction(event))) return 'Payment-stage evidence needs independent reconciliation before a baseline retry.';
  return null;
}

export function retryQuoteMatchesOriginal(quote: BaselineRetryQuote, original: Run): boolean {
  const ids = original.personas.map(person => person.id);
  const used = original.budget.estimated_consumed_microusd ?? original.budget.spent_microusd;
  return quote.retry_of_run_id === original.id && quote.population_id === original.population_id
    && quote.persona_count === original.persona_count && quote.app_url === original.app_url
    && ids.length === quote.persona_ids.length && ids.every((id, index) => quote.persona_ids[index] === id)
    && quote.revision === original.revision
    && quote.retry_budget.original_reserved_microusd === original.budget.reserved_microusd
    && quote.retry_budget.historical_spent_microusd === used
    && quote.retry_budget.release_microusd === original.budget.unused_microusd;
}

export function retryQuoteApprovalBlocker(quote: BaselineRetryQuote, original: Run, owner: boolean, now = Date.now()): string | null {
  if (!owner) return 'An owner session is required to approve this new reservation.';
  if (!retryQuoteMatchesOriginal(quote, original)) return 'Refresh the saved attempt and its exact population budget before approving.';
  const expires = Date.parse(quote.expires_at);
  if (!Number.isFinite(expires) || expires <= now) return 'This retry quote expired. Review a fresh budget.';
  if (!quote.ready || quote.gates.length) return 'Resolve the reported setup blockers and review a fresh quote. A baseline retry cannot start while setup is incomplete.';
  if (!quote.affordable || quote.retry_budget.available_after_reserve_microusd < 0) return 'The full new before-and-after reservation must fit both budget limits. All saved customers remain selected.';
  return null;
}

const retryErrors: Record<string, string> = {
  baseline_retry_ineligible: 'This saved attempt is not eligible for a baseline retry. Inspect its actual evidence and status.',
  baseline_retry_approval: 'The service could not verify pre-submission safety evidence for this exact saved attempt. New attempts need a valid server receipt; legacy attempts need the owner’s reviewed evidence. Missing outcomes alone do not prove retry safety.',
  population_integrity: 'The saved population binding could not be verified. Keep the original customers and inspect their source records.',
  baseline_retry_stale: 'The original attempt changed after this quote. Refresh its evidence and review a new budget.',
  baseline_retry_exists: 'A baseline retry already exists for this original attempt. Open its saved history instead of creating another.',
  quote_expired: 'This retry quote expired. Review a fresh budget before approving it.',
  quote_consumed: 'This retry quote was already accepted. Recover the original saved request or open its existing run.',
  revision_changed: 'The target revision changed after this quote. Review a new quote for the registered source.',
  scenario_support_changed: 'Supported test conditions changed. Refresh setup and the saved-customer retry quote.',
  setup_not_ready: 'Resolve the reported setup blockers before approving a saved-customer baseline retry.',
  quote_budget: 'The complete new before-and-after reservation exceeds the quote cap. The customer count will not be reduced.',
  budget: 'The complete new reservation does not fit the lifetime allowance after releasing only this attempt’s unused hold.',
};

export async function retryRequest<T>(send: () => Promise<T>): Promise<T> {
  try { return await send(); }
  catch (value) {
    if (value instanceof ApiError && retryErrors[value.code]) throw new ApiError(`${retryErrors[value.code]} (${value.code})`, value.status, value.code);
    throw value;
  }
}

export function createBaselineRetryApi(request: ReturnType<typeof createTransport>) {
  return {
    baselineRetry: (runId: string, quoteId: string, key: string) => retryRequest(() => request(`/runs/${encodeURIComponent(runId)}/baseline-retry`, createdRunSchema, { body: { quote_id: quoteId }, key })),
    baselineRetryQuote: async (original: Run): Promise<BaselineRetryQuote> => {
      const quote = await retryRequest(() => request(`/runs/${encodeURIComponent(original.id)}/baseline-retry/quote`, baselineRetryQuoteSchema, { body: {} }));
      if (!retryQuoteMatchesOriginal(quote, original)) throw new ApiError('The retry quote differs from the selected saved population or original budget. Refresh its evidence before approving anything.', 200, 'baseline_retry_response_mismatch');
      return quote;
    },
  };
}

export function renderRetryBudget(budget: RetryBudget, accepted = false): string {
  const rows: [string, number][] = [
    ['Original attempt reservation', budget.original_reserved_microusd],
    ['Original estimated allowance used, retained', budget.historical_spent_microusd],
    [accepted ? 'Original unused hold released on acceptance' : 'Only this attempt’s unused hold will be released', budget.release_microusd],
    ['Lifetime available before that release', budget.available_before_release_microusd],
    ['Lifetime available after that release', budget.available_after_release_microusd],
    ['New reservation for the retry and its same-customer after pass', budget.new_reserved_microusd],
    [budget.available_after_reserve_microusd < 0 ? 'Additional allowance needed for the full new reservation' : 'Lifetime available after the new reservation', Math.abs(budget.available_after_reserve_microusd)],
  ];
  return `<section class="retry-budget"><h2>${accepted ? 'Saved baseline retry budget' : 'The old attempt and the new budget'}</h2><div class="budget-rows">${rows.map(([label, value]) => `<div><span>${escape(label)}</span><strong>${money(value)}</strong></div>`).join('')}</div><p class="small muted">Original use remains in the ledger. Other attempts’ holds are retained. ${accepted ? 'These are the budget snapshots recorded on acceptance.' : 'Approval releases only the original unused hold and reserves the new budget in one transaction. A rejected request changes neither hold.'}</p><p class="small muted">Incremental cash recorded: ${money(budget.measured_cash_microusd)}. These amounts are estimated allowance, not measured customer outcomes.</p></section>`;
}

export function renderRetryPopulation(quote: BaselineRetryQuote): string {
  return `<p class="same-population">This distinct initial attempt reuses all ${number(quote.persona_count)} saved customers from population ${escape(quote.population_id)}. Profiles, scenarios, starting conditions and initial memory are preserved. No new crowd is generated.</p><details class="record" data-record="retry-population"><summary>Saved population binding</summary><div class="evidence-row"><strong>Original blocked attempt</strong><span>${escape(quote.retry_of_run_id)}</span></div><div class="evidence-row"><strong>Population digest</strong><span>${escape(quote.population_sha256)}</span></div><div class="evidence-row"><strong>Saved customer IDs</strong><span>${quote.persona_ids.map(escape).join(', ')}</span></div></details>`;
}

export function renderBaselineRetryHistory(current: Run, owner: boolean): string {
  const lineage = current.baseline_retry;
  if (!lineage) return '';
  const historical = current.id === lineage.original_run_id;
  const related = historical ? lineage.retry_run_id : lineage.original_run_id;
  const safety = lineage.payment_safety_basis === 'server_recorded_pre_submission_evidence_not_provider_receipt'
    ? 'Server-recorded pre-submission evidence permits this retry' : 'Owner-reviewed pre-submission evidence permits this legacy retry';
  const pass = current.pass === 'after'
    ? 'This after pass belongs to the new initial attempt shown above, following its completed measured baseline and genuine repair.'
    : 'The new baseline is a distinct initial pass. Its completed measured finding and genuine completed repair must precede its same-customer after pass.';
  return `<section class="baseline-retry-history"><h2>${historical ? 'Preserved blocked attempt' : 'A distinct attempt with the saved crowd'}</h2><p>${historical ? 'This original attempt remains in history. Its setup blocker does not count as a merchant failure or a result in the new attempt.' : 'The original blocked attempt remains inspectable. Its events and outcomes are excluded from this attempt’s customer denominator.'}</p><div class="evidence-row"><strong>Original blocked attempt</strong><span>${escape(lineage.original_run_id)}</span></div><div class="evidence-row"><strong>New initial attempt</strong><span>${escape(lineage.retry_run_id)}</span></div><button class="secondary" data-action="related-run" data-id="${escape(related)}" ${!owner ? 'disabled' : ''}>${historical ? 'Open accepted baseline retry' : 'Inspect original blocked attempt'}</button>${!owner ? '<p class="small muted">Connect the owner to inspect the related attempt.</p>' : ''}<p class="same-population">All ${number(current.persona_count)} original saved customers remain in population ${escape(lineage.population_id)}. ${pass}</p><p class="small muted">Accepted ${escape(lineage.accepted_at)}. ${safety}; it is not a provider payment receipt.</p><details class="record" data-record="baseline-retry-lineage"><summary>Saved retry binding</summary><div class="evidence-row"><strong>Population SHA256</strong><span>${escape(lineage.population_sha256)}</span></div><div class="evidence-row"><strong>Safety evidence receipt SHA256</strong><span>${escape(lineage.approval_sha256)}</span></div></details>${renderRetryBudget(lineage.retry_budget, true)}</section>`;
}
