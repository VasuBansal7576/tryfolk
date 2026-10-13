import './styles.css';
import './app.css';
import { api, ApiError } from './api';
import { fixedPopulationBlocker, LAUNCH_PERSONA_COUNT } from './launch';
import type { AfterRetryQuote, BaselineRetryQuote, Finding, Quote, Readiness, Run, RunEvent } from './contracts';
import { comparisonTable, escape, eventList, eventSummary, evidence, gates, humanize, link, money, number, outcomes, providerNotice, redact, safeUrl, timestamp, findingEvidenceLabel, repairableFinding } from './presentation';
import { loadRuns, saveRun } from './session';
import { affordabilityMessage, forecastSource, quoteBudget } from './budget';
import { RequestJournal, recoverRequest, replayBlocker, repairTargetRevision } from './operations';
import type { PendingRequest } from './operations';
import type { SavedRun } from './session';
import { readinessFacts, renderConnections } from './setup';
import { applyRegistration, connectionSnapshot, registrationAvailable, registrationMessage, sourceVerified } from './connections';
import type { ConnectionState } from './connections';
import { readRunEvidence } from './run-read';
import { renderPersonaDetail, renderSeedProvenance, selectedPersona } from './persona-detail';
import type { PersonaSelection } from './persona-detail';
import { renderScenarioCoverage, renderSelectionRationale } from './scenario-coverage';
import type { CatalogState } from './scenario-coverage';
import { captureRefreshState, restoreRefreshState } from './refresh-state';
import type { RefreshState } from './refresh-state';
import { customerStatus, phaseCopy, renderRunProgress } from './run-progress';
import { canVerifyCheckpoint, renderCheckpointAccess } from './checkpoint-access';
import { baselineRetryBlocker, renderBaselineRetryHistory, renderRetryBudget, renderRetryPopulation, retryQuoteApprovalBlocker } from './baseline-retry';
import { personaTraceLinks, renderPersonaComparison } from './persona-comparison';
import { renderTraceReceipts, renderTraceReadback } from './traces';
import type { TraceReadback, TraceState } from './traces';

type View = { kind: 'start' } | { kind: 'setup' } | { kind: 'quote'; quote: Quote } | { kind: 'baseline-retry-quote'; quote: BaselineRetryQuote; original: Run } | { kind: 'run'; id: string };
type Modal = { kind: 'people'; query: string; page: number } | { kind: 'person'; selection: PersonaSelection } | { kind: 'person-trace'; selection: PersonaSelection } | { kind: 'before-person-trace'; afterRunId: string; personaId: string } | { kind: 'remote-trace'; runId: string; traceId: string; result: TraceReadback | null; message: string } | { kind: 'finding'; id: string } | { kind: 'trace' } | { kind: 'history' } | { kind: 'cancel' } | { kind: 'repair'; findingId: string } | { kind: 'replay' } | { kind: 'after-retry-quote'; quote: AfterRetryQuote } | { kind: 'after-recovery' } | { kind: 'checkpoint-access'; runId: string; repairId: string } | { kind: 'pending' } | { kind: 'screenshot'; eventId: number } | { kind: 'about' };
const main = document.querySelector('#main');
const context = document.querySelector('#context');
const dialog = document.querySelector('#dialog');
const toast = document.querySelector('#toast');
if (!(main instanceof HTMLElement) || !(context instanceof HTMLElement) || !(dialog instanceof HTMLDialogElement) || !(toast instanceof HTMLElement)) throw new Error('Missing app elements');
const elements = { main, context, dialog, toast };
let view: View = { kind: 'start' };
let setupReturn: Exclude<View, { kind: 'setup' }> | null = null;
let modal: Modal | null = null;
let modalReturnFocus: HTMLElement | null = null;
let modalReturnState: RefreshState | null = null;
let readiness: Readiness | null = null;
let readinessError = '';
let connections: ConnectionState = { kind: 'guest', message: 'Authorize the owner to inspect approved app and source connections.' };
let connectionUrl = '';
let connectionEpoch = 0;
let run: Run | null = null;
let comparisonBefore: Run | null = null;
let comparisonError = '';
let comparisonEpoch = -1;
const traceStates = new Map<string, TraceState>();
let findings: Finding[] = [];
let findingsError = '';
let findingsRunId: string | null = null;
let savedRuns = loadRuns();
let appUrl = '';
let error = '';
let busy = false;
let pollError = '';
let pollTimer: ReturnType<typeof setTimeout> | undefined;
let pollEpoch = 0;
let catalogEpoch = -1;
let catalogState: CatalogState = { kind: 'loading' };
let toastTimer: ReturnType<typeof setTimeout> | undefined;
let owner = false;
let ownerSupported = false;
let ownerError = '';
// Accessing storage can itself fail in private/restricted contexts; the journal reports that before dispatch.
const requestJournal = new RequestJournal({
  getItem: key => sessionStorage.getItem(key),
  setItem: (key, value) => sessionStorage.setItem(key, value),
  removeItem: key => sessionStorage.removeItem(key),
});

const isActive = (value: Run) => ['queued', 'discovering', 'generating', 'running', 'verifying'].includes(value.phase);
const canCancel = (value: Run) => value.phase !== 'cancelled' && (isActive(value) || value.budget.unused_microusd > 0);
const host = (value: string): string => { try { return new URL(value).host; } catch { return value; } };
const errorMessage = (value: unknown): string => value instanceof Error ? value.message : 'The request failed. Try again.';

function notify(message: string): void {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { elements.toast.hidden = true; }, 5000);
}
function stopPolling(): void { clearTimeout(pollTimer); pollEpoch += 1; }
function restoreModalFocus(): void {
  const previous = modalReturnFocus;
  const saved = modalReturnState;
  modalReturnFocus = null;
  modalReturnState = null;
  if (previous?.isConnected) { previous.focus({ preventScroll: true }); return; }
  if (saved) restoreRefreshState(elements.main, { ...saved, opened: [] });
}
function closeModal(): void { modal = null; if (elements.dialog.open) elements.dialog.close(); restoreModalFocus(); }
function go(next: View): void {
  if (next.kind === 'setup' && view.kind !== 'setup') setupReturn = view;
  stopPolling(); closeModal(); error = ''; pollError = ''; view = next;
  if (next.kind !== 'run' && next.kind !== 'setup') location.hash = '';
  render();
  window.scrollTo({ top: 0 });
}
function banner(message: string, action = ''): string {
  return message ? `<div class="error-banner" role="alert"><p>${escape(message)}</p>${action}</div>` : '';
}
function pendingRequest(): PendingRequest | null {
  try { return requestJournal.read()?.pending || null; }
  catch { return null; } // The visible banner and approval path report storage failures.
}
function pendingBanner(): string {
  try {
    const entry = requestJournal.read();
    if (!entry) return '';
    return `<section class="pending-request" role="status"><strong>${entry.declined ? 'The service declined the original request.' : 'The original request needs its saved response.'}</strong><p>${entry.pending.kind === 'replay' ? 'The saved retest request still uses the original population, repair and request key.' : entry.pending.kind === 'baseline-retry' ? 'The saved baseline retry request keeps the blocked attempt, exact saved population, approved new budget and original request key.' : 'The saved initial-run request still uses the original approved quote and request key.'} Recover it before approving another run.</p><button class="secondary" data-action="pending-review">Review saved request</button></section>`;
  } catch (value) { return banner(errorMessage(value)); }
}
function currentReplayBlocker(current: Run): string | null {
  return fixedPopulationBlocker(current.persona_count) ?? replayBlocker(current, savedRuns.some(item => item.pass === 'after' && item.parentRunId === current.id));
}
function render(): void {
  const sameRun = view.kind === 'run' && elements.main.dataset.runId === view.id;
  const savedView = captureRefreshState(elements.main);
  if (!sameRun) savedView.opened = [];
  const ownerInput = elements.main.querySelector('#owner-secret');
  elements.context.innerHTML = view.kind === 'run' && run ? `<div class="context"><span>${escape(host(run.app_url))}</span><span>${number(run.persona_count)} people</span></div>` : '';
  switch (view.kind) {
    case 'start': renderStart(); break;
    case 'setup': renderSetup(); break;
    case 'quote': renderQuote(view.quote); break;
    case 'baseline-retry-quote': renderBaselineRetryQuote(view.quote, view.original); break;
    case 'run': renderRun(); break;
  }
  elements.main.insertAdjacentHTML('afterbegin', pendingBanner());
  elements.main.dataset.runId = view.kind === 'run' ? view.id : '';
  // Retain an unfinished password only in its input node across unrelated setup refreshes.
  const replacement = elements.main.querySelector('#owner-secret');
  if (ownerInput instanceof HTMLInputElement && replacement instanceof HTMLInputElement && !owner) replacement.replaceWith(ownerInput);
  restoreRefreshState(elements.main, savedView);
}
function renderStart(): void {
  elements.main.innerHTML = `<section class="world landing"><img class="world-art" src="/assets/arrival.png" alt=""><div class="landing-content"><h1>Before the<br>world gets here.</h1><form class="start-form" id="start-form"><label class="url-field"><span class="sr-only">App URL</span><input id="url" name="url" type="url" inputmode="url" value="${escape(appUrl)}" placeholder="https://your-app.example" autocomplete="url" required></label><div class="count-field"><span>${LAUNCH_PERSONA_COUNT} people</span></div><button class="primary" type="submit" ${busy ? 'disabled' : ''}>${busy ? 'Getting budget…' : 'Review budget'}</button></form>${banner(error)}<p class="start-note">Four customers together, then the same saved four after a fix. Review both passes upfront.</p><div class="landing-links">${readinessFacts(readiness, readinessError).canSelectApp ? '<button class="text-button" data-action="demo">Use test app URL</button>' : ''}<button class="text-button" data-action="about">How it works</button><span>Local test · No charges</span></div>${readiness ? `<p class="allowance">Each new test uses four customers. Setup and both passes are checked at budget review.</p>` : readinessError ? `<p class="allowance status-bad">Connection status is unavailable. <button class="text-button" data-action="setup">Review app connection</button></p>` : '<p class="allowance">Checking the local test connection…</p>'}</div></section>`;
}
function renderQuote(quote: Quote): void {
  const expired = Date.parse(quote.expires_at) <= Date.now();
  const nativePage = readiness?.integrations.cfo?.evidence || null;
  elements.main.innerHTML = `<section class="page quote-page"><button class="back" data-action="home">Back to your app URL</button><h1>One crowd.<br>Both runs covered.</h1><p class="lead">${escape(host(quote.app_url))} · ${number(quote.persona_count)} requested customers</p>${providerNotice({ provider: quote.provider, provenance: quote.provenance, targetProvenance: quote.target_provenance, estimate: quote.cost_basis === 'estimate' && quote.cfo_plan_status === 'missing' })}<div class="quote-layout"><div>${quoteBudget(quote)}<p class="same-population">All ${number(quote.persona_count)} requested customers will be saved during the initial pass. The after pass reuses those exact profiles and starting conditions.</p>${quote.scenario_plan ? renderSelectionRationale(quote.scenario_plan) : ''}${quote.scenario_plan ? `<details class="record"><summary>Planned hypothetical cases · ${number(quote.scenario_plan.assignments.length)} assignments</summary><p class="small muted">These are test inputs, not generated customers or observed failures. They are saved with this quote for both passes.</p>${quote.scenario_plan.assignments.map(item => `<article class="persona-section"><h3>Customer #${number(item.persona_index + 1)} · ${escape(item.scenario_snapshot.title)}</h3><p>${escape(item.scenario_snapshot.hypothetical_problem)}</p>${evidence({ expected_behavior: item.scenario_snapshot.expected_behavior, availability: item.scenario_snapshot.availability, accepted_condition_fixture: item.condition_fixture_snapshot })}${item.seed ? `<details class="record" data-record="quote-seed-${escape(String(item.persona_index))}"><summary>Accepted synthetic seed source · not a generated customer</summary>${renderSeedProvenance(item.seed)}</details>` : ''}</article>`).join('')}</details>` : ''}${banner(error)}${expired ? banner('This quote expired. Refresh the budget before starting.') : ''}<div class="actions"><button class="primary" data-action="confirm-quote" ${busy || expired || !quote.affordable ? 'disabled' : ''}>${busy ? 'Saving run…' : !quote.ready ? 'Confirm budget & save blocked run' : 'Confirm budget & start'}</button><button class="text-button" data-action="requote" ${busy ? 'disabled' : ''}>Refresh budget</button></div>${!quote.affordable ? `<p class="small status-bad">${escape(affordabilityMessage(quote))}</p>` : ''}<p class="small muted quote-expiry">Quote expires ${escape(timestamp(quote.expires_at))}.</p>${forecastSource(quote, nativePage)}</div><aside class="quote-aside"><img src="/assets/aditi.png" alt="" class="quote-person"><h2>${quote.ready ? 'Ready to try it.' : 'Setup comes first.'}</h2>${!quote.ready ? '<p>Saving this run records the blockers. Browser execution will not start while setup is incomplete.</p>' : '<p>Customers and their steps will appear after they are generated and executed.</p>'}${gates(quote.gates)}<button class="text-button" data-action="setup">Review app connection</button></aside></div></section>`;
}
function renderBaselineRetryQuote(quote: BaselineRetryQuote, original: Run): void {
  const reason = fixedPopulationBlocker(quote.persona_count) ?? retryQuoteApprovalBlocker(quote, original, owner);
  elements.main.innerHTML = `<section class="page quote-page"><button class="back" data-action="related-run" data-id="${escape(original.id)}">Back to the preserved attempt</button><h1>Same saved crowd.<br>A new initial attempt.</h1><p class="lead">${escape(host(quote.app_url))} · all ${number(quote.persona_count)} original saved customers</p>${providerNotice({ provider: quote.provider, provenance: quote.provenance, targetProvenance: quote.target_provenance, estimate: quote.cost_basis === 'estimate' && quote.cfo_plan_status === 'missing' })}<div class="quote-layout"><div><p>The original attempt stopped at a setup gate. Its steps and estimated use remain in history. This is a separate initial test with a new budget covering this retry and the same customers after a genuine repair.</p>${renderRetryBudget(quote.retry_budget)}${quoteBudget(quote)}${renderRetryPopulation(quote)}${banner(error)}${reason ? banner(reason) : ''}<div class="actions"><button class="primary" data-action="confirm-baseline-retry" ${busy || !!reason || !!pendingRequest() ? 'disabled' : ''}>${busy ? 'Saving retry…' : 'Approve hold release & new both-pass budget'}</button><button class="text-button" data-action="requote-baseline-retry" ${busy || !!pendingRequest() || !owner ? 'disabled' : ''}>Refresh retry budget</button></div><p class="small muted">Quote expires ${escape(timestamp(quote.expires_at))}. The service rechecks the exact saved population, source, verified pre-submission safety evidence, setup and ledger on acceptance. A rejected request leaves the original hold intact.</p>${forecastSource(quote, readiness?.integrations.cfo?.evidence || null)}</div><aside class="quote-aside"><img src="/assets/aditi.png" alt="" class="quote-person"><h2>${quote.ready ? 'Review the new attempt.' : 'Setup comes first.'}</h2><p>No personas are regenerated. Isolated accounts use the saved initial customer conditions and memory.</p><p>The original blocked setup is excluded from customer success and failure comparisons. A completed measured baseline and genuine repair must precede the after pass.</p>${gates(quote.gates)}<button class="text-button" data-action="setup">Review app connection</button></aside></div></section>`;
}
function renderBaselineRetryEntry(current: Run): string {
  if (current.provider !== 'dodo' || current.pass !== 'before' || current.phase !== 'blocked' || current.baseline_retry || current.retry_of_run_id) return '';
  const reason = fixedPopulationBlocker(current.persona_count) ?? baselineRetryBlocker(current, owner, findingsRunId === current.id && !findingsError, findings.length > 0);
  return `<section class="baseline-retry-entry"><h2>Try the saved customers again</h2><p>A baseline retry creates a distinct initial attempt with all ${number(current.persona_count)} saved customers. The original blocker, steps and estimated use remain in history.</p><p>Review a new budget for both this retry and its same-customer after pass. Approval releases only the old unused hold and reserves the new allowance together.</p><button class="secondary" data-action="baseline-retry-quote" ${busy || !!reason || !!pendingRequest() ? 'disabled' : ''}>Review saved-customer retry budget</button>${reason ? `<p class="small muted">${escape(reason)}</p>` : '<p class="small muted">The service must verify this attempt’s pre-submission safety receipt. Legacy attempts require the owner’s reviewed evidence. An absent outcome does not prove payment safety.</p>'}</section>`;
}
function renderSetup(): void {
  const facts = readinessFacts(readiness, readinessError);
  const backLabel = setupReturn?.kind === 'run' ? 'Return to saved run' : (setupReturn?.kind === 'quote' || setupReturn?.kind === 'baseline-retry-quote') ? 'Return to budget review' : 'Back to your app URL';
  const freshness = facts.freshness === 'stale' ? '<p class="setup-stale" role="status">Last reported setup · the refresh failed. These details may have changed.</p>' : facts.freshness === 'loading' ? '<p class="muted" role="status">Checking the local service…</p>' : '';
  elements.main.innerHTML = `<section class="page setup-page"><button class="back" data-action="setup-back">${backLabel}</button><h1>Connect once.<br>Run with a link.</h1><p class="lead">Connect a supported owned test app. A URL does not grant access to a private repository or payment records.</p>${banner(error)}${banner(readinessError, '<button class="secondary" data-action="refresh-readiness">Retry setup check</button>')}${freshness}${connectionSnapshot(connections) ? '' : `<section class="setup-target"><h2>Your app connection</h2><dl class="connection-facts"><div><dt>App</dt><dd><strong>${facts.appStatus}</strong>${facts.appUrl ? `<span>${escape(facts.appUrl)}</span>` : '<span>The service has not reported a registered app URL.</span>'}</dd></div><div><dt>Repository and source</dt><dd><strong>${facts.sourceStatus}</strong><span>${facts.sourceDetail}</span></dd></div><div><dt>Run readiness</dt><dd><strong>Checked at budget review</strong><span>Registration and owner authorization are separate from browser, payment and repair readiness. The selected app’s quote reports its remaining prerequisites before launch.</span></dd></div></dl><p class="small muted">The general setup check cannot verify source access. Owner-only connection details below report the supported registration capability.</p></section>`}${renderOwner()}${renderConnections(connections, connectionUrl, busy)}${readiness ? `<section class="setup-prerequisites"><h2>Remaining setup</h2>${facts.gates.length ? gates(facts.gates) : '<p>No general setup blockers were reported. This is not a successful journey or approval to execute a target.</p>'}<p class="small muted">New tests use four customers. Both passes are quoted before launch; the retest keeps the same saved four.</p><details class="integration-details"><summary>Service-reported prerequisites</summary>${facts.services.map(item => `<div class="evidence-row"><div><strong>${escape(item.label)}</strong><small>${escape(item.detail)}</small>${link(item.evidence, 'Setup evidence')}</div><span>${escape(humanize(item.status))}</span></div>`).join('')}</details></section>` : '<p class="muted">Setup prerequisites are unavailable until the local service responds.</p>'}<div class="actions"><button class="secondary" data-action="refresh-readiness" ${busy ? 'disabled' : ''}>Refresh setup</button>${facts.canSelectApp ? '<button class="primary" data-action="demo">Use test app URL</button>' : ''}</div><p class="same-population">The initial pass saves all requested customers. The retest reuses that exact population and its original allowance.</p></section>`;
}
function renderOwner(): string {
  return `<section class="owner-panel"><h2>Owner authorization</h2><p>Authorize app setup, repair and retest with the local service’s owner secret. Signing in does not register a repository or prove a working payment flow.</p>${banner(ownerError)}${owner ? '<p class="status-good">Owner session authorized.</p><button class="secondary" data-action="owner-logout">Disconnect owner</button>' : ownerSupported ? `<form id="owner-form" class="owner-form"><label class="label" for="owner-secret">Owner secret</label><input class="field" id="owner-secret" name="secret" type="password" autocomplete="off" required><button class="primary" type="submit" ${busy ? 'disabled' : ''}>Authorize owner</button></form><p class="small muted">The secret is sent only to the local service. It is not saved in this app. The service sets a protected, expiring session cookie.</p>` : '<p class="muted">Owner authorization is unavailable. Check the local service, then refresh.</p>'}</section>`;
}
function lastSnapshot(events: RunEvent[]): { url: string; event: RunEvent } | null {
  for (const event of [...events].reverse()) for (const key of ['screenshot', 'screenshot_url', 'snapshot_url', 'image_url']) {
    const url = safeUrl(event.data[key]); if (url) return { url, event };
  }
  return null;
}
function renderRun(): void {
  const current = run;
  if (!current || view.kind !== 'run' || current.id !== view.id) {
    elements.main.innerHTML = `<section class="page"><h1>Opening your run.</h1><p class="lead">Loading saved results from the local service.</p>${banner(error, '<button class="secondary" data-action="retry-run">Retry</button>')}</section>`;
    return;
  }
  const selected = current.personas.find(person => current.events.some(event => event.persona_id === person.id)) || current.personas[0];
  const storyEvents = selected ? current.events.filter(event => event.persona_id === selected.id) : [];
  const snapshot = lastSnapshot(selected ? storyEvents : current.events.filter(event => event.persona_id === null));
  const storyEvent = [...storyEvents].reverse().find(event => event.kind === 'decision') || storyEvents.at(-1);
  const intent = storyEvent?.data.intent;
  const thought = intent && typeof intent === 'object' && 'content' in intent && typeof intent.content === 'string' ? intent.content : null;
  const goal = selected?.profile.goal;
  const storyLabel = storyEvent ? 'Recorded story excerpt' : typeof goal === 'string' ? 'Saved goal · input only' : 'No recorded story yet';
  const storyText = thought || (storyEvent ? eventSummary(storyEvent) || humanize(storyEvent.kind) : typeof goal === 'string' ? goal : 'This customer has no recorded browser steps yet.');
  const crowd = current.persona_count > 100 ? 'dense' : 'crowd';
  const active = isActive(current);
  elements.main.innerHTML = `<section class="world run-world"><img class="world-art" src="/assets/${crowd}.png" alt=""><div class="run-heading"><h1>${current.pass === 'after' ? 'The same people. A new try.' : 'Your first customers.'}</h1><p class="run-status">${escape(phaseCopy(current))}</p><p class="run-count">${number(current.counts.executed)} / ${number(current.persona_count)} outcomes recorded · ${number(current.counts.generated)} saved · ${escape(humanize(current.phase))}</p></div><div class="session">${snapshot ? `<button class="snapshot-button" data-action="screenshot" data-id="${snapshot.event.id}" aria-label="Inspect full recorded screenshot"><img src="${escape(snapshot.url)}" alt="Full recorded browser screenshot" referrerpolicy="no-referrer"></button>` : `<div class="empty-snapshot"><span>${escape(host(current.app_url))}</span><h2>${selected ? 'No screenshot saved for this customer.' : current.counts.executed ? 'No browser screenshot saved.' : 'Waiting for browser evidence.'}</h2><p>Only recorded screenshots appear here.</p></div>`}<div class="session-caption"><span>${snapshot ? `Recorded for ${escape(selected?.name || 'this run')} · event ${snapshot.event.id}` : 'No screenshot available'}</span>${snapshot ? `<button data-action="screenshot" data-id="${snapshot.event.id}">Inspect full screenshot</button>` : ''}<button data-action="trace">Inspect activity</button></div></div><div class="customer-moment"><img class="person-art" src="/assets/aditi.png" alt=""><div class="moment-copy">${selected ? `<p class="story-excerpt-label">${escape(storyLabel)}</p><p class="moment-quote">${escape(storyText)}</p><p class="moment-name">${escape(selected.name)} · ${escape(customerStatus(selected, current))}</p><button class="secondary" data-action="person" data-id="${escape(selected.id)}">Read full story</button>` : '<p class="moment-quote">Your crowd is<br>not here yet.</p><p class="moment-name">No customer profiles have been generated.</p>'}</div></div><div class="run-bottom"><button class="primary" data-action="people">All ${number(current.persona_count)} customers</button><p class="crowd-note">Representative artwork. ${number(current.personas.length)} saved profiles are individually inspectable.</p>${canCancel(current) ? '<button class="text-button" data-action="cancel">End this run</button>' : '<button class="text-button" data-action="home">Start another run</button>'}</div></section><section class="page run-details">${providerNotice({ provider: current.provider, provenance: current.provenance, targetProvenance: current.target_provenance, estimate: current.budget.cost_basis.includes("estimate") })}${banner(error)}${banner(pollError, '<button class="secondary" data-action="retry-run">Reconnect</button>')}${renderRunProgress(current)}${renderAfterRecovery(current)}${renderAfterRetry(current)}${renderBaselineRetryHistory(current, owner)}<div class="section-head"><h2>${active ? 'Customer status so far' : 'Service-reported customer status'}</h2><p>Every outcome uses the original ${number(current.persona_count)} customers as its denominator.</p></div>${outcomes(current.counts, current.persona_count)}<p class="small muted incomplete-note">${number(current.counts.queued)} queued · ${number(current.counts.requested - current.counts.generated)} not generated. Pending and blocked customers are not passes. Abandonment is a synthetic customer's decision, not real customer churn.</p><section class="findings-section"><h2>Findings</h2>${banner(findingsError, '<button class="text-button" data-action="retry-findings">Retry findings</button>')}${findings.length ? findings.map(finding => `<button class="finding-row" data-action="finding" data-id="${escape(finding.id)}"><span><strong>${escape(finding.summary)}</strong><small>${escape(humanize(finding.classification))} · ${escape(finding.scope)}</small></span><span>${findingEvidenceLabel(finding)} →</span></button>`).join('') : !findingsError ? `<p class="empty">No customer finding has been recorded. Saved steps alone do not establish that a journey passed or failed.</p>` : ''}</section>${renderScenarioCoverage(current, catalogState)}${renderRepair(current)}${renderComparison(current)}${renderBudget(current)}${renderBaselineRetryEntry(current)}<details class="provenance"><summary>Run and population provenance</summary><div class="evidence-row"><strong>Run</strong><span>${escape(current.id)} · ${current.pass === 'before' ? 'Initial pass' : 'After pass'}</span></div><div class="evidence-row"><strong>Saved population</strong><span>${escape(current.population_id)}</span></div><div class="evidence-row"><strong>Target revision</strong><span>${escape(current.revision)}</span></div><div class="evidence-row"><strong>Recorded source commit</strong><span>${escape(current.source_commit || 'Not reported for this saved run')}</span></div><div class="evidence-row"><strong>Recorded source digest</strong><span>${escape(current.source_digest || 'Not reported for this saved run')}</span></div><div class="evidence-row"><strong>Recorded target mode</strong><span>${escape(current.target_mode || 'Not reported for this saved run')}</span></div><div class="evidence-row"><strong>Fault configuration</strong><span>${escape(current.fault)}</span></div><div class="evidence-row"><strong>Last recorded update</strong><span>${escape(timestamp(current.updated_at))}</span></div>${link(current.preview_url, 'Registered target preview')}<p class="same-population">Retesting uses all ${number(current.persona_count)} of these saved personas and their supported starting conditions. Separate diagnostic attempts never change this denominator.</p></details><div class="actions"><button class="secondary" data-action="trace">Execution trace</button><button class="text-button" data-action="download">Download recorded report</button><button class="text-button" data-action="refresh-run">Refresh results</button></div></section>`;
}
function renderAfterRetry(current: Run): string {
  const history = current.after_retry ? `<section class="repair-section"><h2>Saved after retry</h2><p>This distinct attempt keeps all ${number(current.persona_count)} original customers and the same repaired source. The recorded diagnostic is separate from these new journeys.</p><button class="secondary" data-action="open-saved-after-retry" data-id="${escape(current.after_retry.original_run_id)}">Open previous after attempt</button></section>` : '';
  if (current.after_retry_child_run_id) return `${history}<section class="repair-section"><h2>Saved after retry</h2><button class="secondary" data-action="open-saved-after-retry" data-id="${escape(current.after_retry_child_run_id)}">Open the saved after retry</button><p>The blocked evidence stays in this run.</p></section>`;
  const eligible = current.phase === 'blocked' && current.counts.executed === current.counts.completed
    || current.phase === 'completed' && current.counts.completed === current.persona_count;
  if (current.provider !== 'dodo' || current.pass !== 'after' || !eligible || !current.repair?.head_revision) return history;
  return `${history}<section class="repair-section"><h2>A fresh after attempt</h2><p>A new attempt keeps all ${number(current.persona_count)} saved customers and exact repaired source, using fresh accounts. Existing paid or ambiguous sessions remain untouched. Review the complete new forecast before approval.</p><button class="secondary" data-action="after-retry-quote" ${!owner || busy || !!pendingRequest() ? 'disabled' : ''}>Review a fresh same-customer after attempt</button></section>`;
}
function renderAfterRetryQuote(quote: AfterRetryQuote): string {
  const b = quote.retry_budget;
  const allocation = quote.cfo_plan?.schema_version === 2 ? quote.cfo_plan.operator_allocation : undefined;
  const allocationDisclosure = allocation ? `<p><strong>Local operator retry forecast.</strong> The inspected native CFO rates and history are preserved. An additional ${money(allocation.additional_microusd)} complete-cohort forecast sets the local scheduler ceiling to ${money(allocation.lifetime_budget_microusd)}. This is not native CFO approval or funded credit and authorizes no cash charges.</p>` : '';
  return `<p>All ${number(quote.persona_count)} saved customers from population ${escape(quote.population_id)} return on source ${escape(quote.head_revision)}. No profiles or initial memories are regenerated.</p><p>The genuine completed diagnostic and its held-out checks are reused from attempt ${escape(quote.known_replay_source_run_id || quote.retry_of_run_id)} on this exact source. Each customer still needs a new real payment and matching access check.</p>${allocationDisclosure}<div class="budget-rows"><div><span>Prior estimated use, retained</span><strong>${money(b.historical_spent_microusd)}</strong></div><div><span>Prior unused hold released on acceptance</span><strong>${money(b.release_microusd)}</strong></div><div><span>All saved customers after allowance</span><strong>${money(quote.after_microusd)}</strong></div><div><span>Forecast contingency</span><strong>${money(quote.contingency_microusd || 0)}</strong></div><div><span>New after-only reservation</span><strong>${money(quote.reserved_microusd)}</strong></div></div><p class="small muted">Estimated allowance only; incremental cash recorded is ${money(b.measured_cash_microusd)}. Rejected approval changes no reservation.</p>${gates(quote.gates)}`;
}
function afterRecoveryAvailable(current: Run): boolean {
  return current.provider === 'dodo' && current.pass === 'after' && current.phase === 'blocked'
    && current.counts.executed === 0
    && current.gates.some(gate => gate.message === 'Recorded intent has no unique freshly observed control; diagnostic stops without navigation substitution.' || gate.message === 'stagehand: Document changed after observation; no browser action was executed or retried.');
}
function renderAfterRecovery(current: Run): string {
  if (!afterRecoveryAvailable(current)) return '';
  return `<section class="repair-section"><h2>Recover the stopped diagnostic</h2><p>The four saved customers have not started. Review a fresh diagnostic on the same repaired source before their after pass. Earlier payment sessions and blocked evidence stay saved.</p><button class="secondary" data-action="after-recovery-review" ${!owner || busy ? 'disabled' : ''}>Review after-pass recovery</button></section>`;
}
function renderBudget(current: Run): string {
  const allowanceUsed = current.budget.estimated_consumed_microusd ?? current.budget.spent_microusd;
  const cash = current.budget.measured_cash_microusd ?? current.provenance?.cash_charged_microusd;
  const cashBasis = current.budget.cash_basis || (current.provenance?.model_billing === 'existing_codex_allowance' ? 'Existing model allowance and local browsers. Token quota use is separate from cash cost.' : 'Incremental cash cost has not been measured.');
  const original = current.budget_contract
    ? `<details class="accepted-budget"><summary>Original accepted budget · all ${number(current.budget_contract.persona_count)} customers</summary>${quoteBudget(current.budget_contract, true)}${forecastSource(current.budget_contract)}</details>`
    : '<p class="small muted">The original itemized quote was not included in this saved record. Its recorded reservation remains unchanged.</p>';
  return `<section class="run-budget"><h2>The budget stays with the crowd.</h2><div class="budget-summary"><p><strong>${money(current.budget.reserved_microusd)}</strong>${current.after_retry ? 'Reserved for this after retry' : 'Originally reserved for both passes'}</p><p><strong>${money(allowanceUsed)}</strong>Estimated allowance used</p><p><strong>${money(current.budget.unused_microusd)}</strong>Unused and still held</p><p><strong>${money(current.budget.released_microusd)}</strong>Released</p></div>${original}<div class="cash-cost"><strong>Incremental cash recorded: ${cash === undefined ? 'Unknown' : money(cash)}</strong><p>${escape(cashBasis)}</p></div><p class="small muted">${current.after_retry ? 'This after retry uses its separately approved forecast; the original attempt retains its spent allowance and evidence.' : 'The saved after pass uses its original reservation.'} Plan updates cannot reprice this run or replace its saved population. No charges are made by this app.</p></section>`;
}
function renderRepair(current: Run): string {
  const repair = current.repair;
  const repairable = findings.find(finding => repairableFinding(finding, current));
  if (!repair) return `<section class="repair-section"><h2>A repair starts with evidence.</h2>${current.phase === 'blocked' ? '<p>This run’s blocker needs review. A stopped browser action is not yet a recorded customer defect.</p>' : ''}<p>${repairable ? 'A recorded defect is ready for owner review. Local fixture observations remain separate from measured provider payments.' : 'A completed initial run with a measured or disclosed local fixture defect and an authorized source connection is needed before repair.'}</p><button class="secondary" data-action="repair-review" data-id="${escape(repairable?.id || '')}" ${!repairable ? 'disabled' : ''}>Review repair approval</button><p class="small muted">${owner ? 'Owner session connected.' : 'Guests can inspect evidence. An owner session is required to approve a repair or retest.'}</p><h3>Before same-customer retest</h3><p>${escape(currentReplayBlocker(current) || 'Review the exact registered repair and original reservation before approving a retest.')}</p><p class="small muted">The retest keeps all ${number(current.persona_count)} original customers and their starting conditions. It cannot use a smaller or newly generated crowd.</p></section>`;
  const replayReason = currentReplayBlocker(current);
  const replayable = !replayReason && !pendingRequest();
  return `<section class="repair-section"><h2>The repair</h2><p>${escape(humanize(repair.status))}. ${repair.status === 'completed' ? 'A prepared repair is not proof of recovery. Retest its exact preview.' : 'The existing run evidence remains available.'}</p>${gates(repair.gates)}${owner && ['local-fixture', 'dodo'].includes(current.provider || '') && current.pass === 'before' && current.phase === 'completed' && repair.status === 'blocked' ? '<p class="small muted">Verify the saved repair checkpoint and source. Recovery preserves this population and reservation and starts no new code repair or checkout.</p><button class="secondary" data-action="recover-repair">Recover saved repair checkpoint</button>' : ''}<div class="evidence-row"><strong>Base revision</strong><span>${escape(repair.base_revision)}</span></div><div class="evidence-row"><strong>Repair head</strong><span>${escape(repair.head_revision || 'Not available yet')}</span></div><div class="evidence-row"><strong>Repair checkpoint</strong><span>${escape(repair.checkpoint_id || 'Not recorded')}</span></div>${renderCheckpointAccess(repair)}${!repair.checkpoint_access ? `<button class="secondary" data-action="checkpoint-access-review" ${!canVerifyCheckpoint(current, owner) || busy ? 'disabled' : ''}>Review public checkpoint verification</button><p class="small muted">${!owner ? 'Owner authorization is required.' : 'Requires a completed registered repair and server-approved repository and capture. Publication is reviewed separately.'}</p>` : ''}<div class="evidence-links">${link(repair.preview_url, 'Registered repair preview')}</div><button class="primary" data-action="replay-review" ${!replayable ? 'disabled' : ''}>Review same-customer retest</button>${replayReason ? `<p class="small muted">${escape(replayReason)}</p>` : pendingRequest() ? '<p class="small muted">Recover the original saved request before approving a retest.</p>' : ''}<p class="same-population">All ${number(current.persona_count)} saved personas will return under equivalent starting conditions. A changed preview revision must be retested.</p></section>`;
}
function renderComparison(current: Run): string {
  const comparison = current.comparison;
  const paired = current.pass === 'after' ? `<div>${comparisonBefore ? renderPersonaComparison(current, comparisonBefore) : `<p class="small muted">${comparisonError ? escape(comparisonError) : 'Loading the saved initial evidence for each customer…'}</p><button class="secondary" data-action="refresh-comparison">Load initial evidence</button>`}</div>` : '';
  if (!comparison) return paired ? `<section class="comparison-section"><h2>The same customers, both attempts</h2><p>Aggregate comparison is recorded after the after pass finishes. Unfinished journeys remain unresolved.</p>${paired}</section>` : '';
  const comparable = comparison.same_personas && comparison.population_id === current.population_id && comparison.requested === current.persona_count;
  return `<section class="comparison-section"><h2>What changed for the same crowd?</h2><p>${comparable ? 'The service reports the same saved personas in both passes.' : 'Population equivalence has not been established. These runs cannot be accepted as a same-population comparison.'}</p><p class="small muted">${escape(comparison.population_id)} · ${number(comparison.requested)} original customers in each pass</p>${comparisonTable(comparison.before_counts, comparison.after_counts, comparison.requested)}${paired}<p class="evidence-row"><strong>Known-failure action replay</strong><span>${escape(humanize(comparison.known_failure_replay))}</span></p>${renderHeldOut(current)}<p>Estimated allowance used ${money(comparison.total_spent_microusd)} · Unused ${money(comparison.unused_microusd)}</p><p class="small muted">Initial run ${escape(comparison.before_run_id)} · After run ${escape(comparison.after_run_id)}</p></section>`;
}
function renderHeldOut(current: Run): string {
  const checks = current.comparison?.held_out_checks;
  if (!checks) return '<p class="small muted">No separate diagnostic checks are reported in this saved record.</p>';
  const origin = current.after_retry
    ? `These recorded controls were reused from source attempt ${escape(current.after_retry.known_replay_source_run_id)} on the same repaired source. They were not run again for this retry.`
    : 'These deterministic controls are separate from the adaptive customer journeys.';
  return `<details class="held-out record"><summary>Separate diagnostic checks · ${number(checks.passed)} passed / ${number(checks.total)} recorded assertions</summary><p class="small muted">${number(checks.failed)} failed. ${origin} They never enter the ${number(current.persona_count)}-customer denominator and do not establish adaptive customer outcomes.</p>${checks.cases.length ? checks.cases.map(item => `<article class="diagnostic-case"><h3>${escape(humanize(item.case))} · ${item.passed ? 'Passed' : 'Failed'}</h3><p class="small muted">${escape(item.provider || current.provider || 'Provider not recorded')} · ${item.diagnostic ? 'Recorded diagnostic control' : item.measured ? 'Measured diagnostic evidence' : 'Measurement metadata not recorded'}</p><p class="small muted">Revision ${escape(item.revision || current.revision)} · separate account ${escape(item.account_id)}</p>${link(item.preview_url, 'Diagnostic preview')}${evidence(item.evidence || item)}</article>`).join('') : '<p class="empty">No individual diagnostic evidence was included.</p>'}</details>`;
}
function openModal(next: Modal): void {
  if (!elements.dialog.open && document.activeElement instanceof HTMLElement) {
    modalReturnFocus = document.activeElement;
    modalReturnState = captureRefreshState(elements.main);
  }
  // Deliberate drawer navigation starts a new inspection. Only a refresh of the
  // same open drawer retains its control/disclosure state.
  elements.dialog.replaceChildren();
  error = ''; modal = next; renderModal();
  if (!elements.dialog.open) elements.dialog.showModal();
  const heading = elements.dialog.querySelector('#dialog-heading');
  if (heading instanceof HTMLElement) heading.focus({ preventScroll: true });
}
function modalFrame(title: string, body: string, side = false): void {
  const savedView = captureRefreshState(elements.dialog);
  const scroll = elements.dialog.querySelector('.dialog-body')?.scrollTop ?? 0;
  elements.dialog.className = side ? 'sidepanel' : '';
  elements.dialog.innerHTML = `<div class="dialog-header"><h2 id="dialog-heading" tabindex="-1">${escape(title)}</h2><button class="text-button" data-action="close">Close</button></div><div class="dialog-body">${banner(error)}${body}</div>`;
  const restored = restoreRefreshState(elements.dialog, savedView);
  if (savedView.focus && !restored) elements.dialog.querySelector<HTMLElement>('#dialog-heading')?.focus({ preventScroll: true });
  const updatedBody = elements.dialog.querySelector('.dialog-body');
  if (updatedBody instanceof HTMLElement) updatedBody.scrollTop = scroll;
}
function renderModal(): void {
  const state = modal;
  if (!state) return;
  const current = run;
  switch (state.kind) {
    case 'screenshot': {
      const event = current?.events.find(item => item.id === state.eventId);
      const snapshot = event ? lastSnapshot([event]) : null;
      if (!snapshot || !current) { modalFrame('Screenshot unavailable', '<p>No recorded image is available for this event.</p>'); break; }
      const person = current.personas.find(item => item.id === event?.persona_id);
      modalFrame('Recorded browser screenshot', `<p class="small muted">${escape(person?.name || (event?.persona_id ? event.persona_id : 'Run-wide evidence'))} · event ${snapshot.event.id} · ${escape(timestamp(snapshot.event.created_at))}</p><p class="small muted">The complete saved image is shown below. Scroll to inspect long screenshots.</p><figure class="full-screenshot"><img src="${escape(snapshot.url)}" alt="Complete recorded browser screenshot" referrerpolicy="no-referrer"></figure>${link(snapshot.url, 'Open original full-size image')}`);
      elements.dialog.className = 'screenshot-dialog';
      break;
    }
    case 'pending': {
      try {
        const entry = requestJournal.read();
        if (!entry) { modalFrame('Request resolved', '<p>The saved request has already been resolved.</p>'); break; }
        const pending = entry.pending;
        const details = pending.kind === 'create'
          ? `<p class="lead">Original approved initial run · ${number(pending.quote.persona_count)} requested customers</p>${quoteBudget(pending.quote, true)}`
          : pending.kind === 'after-retry' ? renderAfterRetryQuote(pending.quote)
          : pending.kind === 'baseline-retry'
            ? `<p class="lead">Original approved baseline retry · all ${number(pending.quote.persona_count)} saved customers</p><p>Acceptance has not been established in this tab. Recover this request before approving another reservation.</p>${renderRetryBudget(pending.quote.retry_budget)}${renderRetryPopulation(pending.quote)}${quoteBudget(pending.quote)}`
            : `<p class="lead">Original approved retest · all ${number(pending.personaCount)} saved customers</p><div class="evidence-row"><strong>Initial run</strong><span>${escape(pending.runId)}</span></div><div class="evidence-row"><strong>Saved population</strong><span>${escape(pending.populationId)}</span></div><div class="evidence-row"><strong>Exact repair</strong><span>${escape(pending.repairRevision)}</span></div>`;
        modalFrame('Recover the original request', `${details}<p>The service receives the exact original request and key. An already accepted request returns its existing run. If its response was lost, this does not generate a replacement crowd or reserve another budget.</p><p class="small muted">Approved ${escape(timestamp(pending.approvedAt))}. Recovery is deliberate; reload does not resend the request.</p>${entry.declined ? banner(entry.declined.message) : ''}<div class="actions dialog-actions"><button class="primary" data-action="recover-request" ${busy || pending.kind !== 'create' && !owner ? 'disabled' : ''}>Recover original ${pending.kind === 'create' ? 'run' : pending.kind === 'baseline-retry' ? 'baseline retry' : 'retest'}</button>${pending.kind !== 'create' && !owner ? '<button class="secondary" data-action="setup">Connect owner</button>' : ''}${entry.declined ? '<button class="secondary" data-action="dismiss-declined">Dismiss declined request</button>' : ''}</div>`);
      } catch (value) { modalFrame('Request recovery unavailable', `<p>${escape(errorMessage(value))}</p>`); }
      break;
    }
    case 'about': modalFrame('How it works', '<div class="about"><p>Enter your app URL. Review an upfront allowance for four customers and the same saved four after a fix.</p><p>The service generates the customers, executes their decisions in isolated browsers, and records what happened. You can inspect each story, browser evidence, payment truth and access checks.</p><p>Owner-authorized repair targets the connected source. Retesting reuses the saved population on the exact registered repair preview.</p><p>This local app displays backend records. Missing setup is shown as a blocker; it does not create fictional results.</p></div>'); break;
    case 'history': modalFrame('Runs in this tab', `<p class="small muted">Run access is saved for reloads in this browser tab. A closed or cleared session may remove access.</p>${savedRuns.length ? savedRuns.map(item => `<button class="run-entry" data-action="open-run" data-id="${escape(item.id)}"><strong>${escape(host(item.appUrl))}</strong><span>${number(item.count)} customers · ${item.pass === 'before' ? 'Initial pass' : 'After pass'}</span><span>${escape(item.id)}</span></button>`).join('') : '<p class="empty">Your first crowd is waiting. Start by reviewing a budget.</p>'}`, true); break;
    case 'people': if (current) renderPeople(state, current); break;
    case 'person': {
      const person = selectedPersona({ run: current, viewRunId: view.kind === 'run' ? view.id : null, epoch: pollEpoch, selection: state.selection });
      if (!current || !person) { modalFrame('Customer unavailable', '<p>This profile has not been generated or is no longer in the current run.</p>'); break; }
      modalFrame(person.name, renderPersonaDetail(current, person), true); break;
    }
    case 'person-trace': {
      const person = selectedPersona({ run: current, viewRunId: view.kind === 'run' ? view.id : null, epoch: pollEpoch, selection: state.selection });
      if (!current || !person) { modalFrame('Customer trace unavailable', '<p>The selected run changed. Open a customer from the current saved run.</p>'); break; }
      modalFrame(`${person.name} · recorded trace`, `<p class="sample-label">${escape(current.id)} · ${escape(person.id)}</p>${renderTraceReceipts(current.id, traceStates.get(current.id), person.id)}<p>${personaTraceLinks(current, person.id)}</p><button class="secondary" data-action="person" data-id="${escape(person.id)}">Back to customer details</button><button class="text-button" data-action="refresh-trace-receipts" data-id="${escape(current.id)}">Refresh trace receipts</button>${eventList(current.events.filter(event => event.persona_id === person.id))}`, true); break;
    }
    case 'before-person-trace': {
      const before = comparisonBefore;
      const person = before?.personas.find(item => item.id === state.personaId);
      if (!current || current.id !== state.afterRunId || current.parent_run_id !== before?.id || !before || !person) { modalFrame('Initial evidence unavailable', '<p>Reload both-pass evidence from the after run.</p>'); break; }
      modalFrame(`${person.name} · initial trace`, `<p class="sample-label">Initial run ${escape(before.id)} · ${escape(person.id)}</p>${renderTraceReceipts(before.id, traceStates.get(before.id), person.id)}<p>${personaTraceLinks(before, person.id)}</p><button class="text-button" data-action="refresh-trace-receipts" data-id="${escape(before.id)}">Refresh trace receipts</button>${eventList(before.events.filter(event => event.persona_id === person.id))}`, true); break;
    }
    case 'remote-trace': modalFrame('Actual Neatlogs readback', `${state.result ? renderTraceReadback(state.result) : `<p>${escape(state.message || 'Reading the recorded trace from Neatlogs…')}</p>`}<button class="secondary" data-action="remote-trace" data-id="${escape(state.runId + ':' + state.traceId)}">Refresh hosted readback</button>`, true); break;
    case 'finding': {
      const finding = findings.find(item => item.id === state.id);
      if (!finding) { modalFrame('Finding unavailable', '<p>Refresh the run to reload its findings.</p>'); break; }
      modalFrame(finding.summary, `<p class="sample-label">${findingEvidenceLabel(finding)} · ${escape(humanize(finding.classification))}</p><p class="lead">Observed scope: ${escape(finding.scope)}</p>${evidence(finding.evidence)}<p class="small muted">Observed scope applies to the tested conditions. This does not establish a real customer failure rate.</p><div class="actions"><button class="secondary" data-action="person" data-id="${escape(finding.persona_id)}">Customer story</button><button class="primary" data-action="repair-review" data-id="${escape(finding.id)}" ${!current || !repairableFinding(finding, current) ? 'disabled' : ''}>Review repair approval</button></div>`); break;
    }
    case 'trace': if (current) modalFrame('Recorded execution', `<p class="sample-label">${escape(current.id)} · ${number(current.events.length)} saved events</p>${renderTraceReceipts(current.id, traceStates.get(current.id))}<p class="small muted">These are the saved run events. A linked trace does not establish server finalization, and browser steps alone are not customer outcomes.</p><button class="secondary" data-action="refresh-trace">Refresh activity</button>${eventList(current.events)}`, true); break;
    case 'cancel': modalFrame('End this run?', '<p>The service will stop further work and release the unused reservation, including the saved retest allowance. Recorded evidence remains available.</p><div class="actions dialog-actions"><button class="primary" data-action="confirm-cancel">End run</button><button class="secondary" data-action="close">Keep watching</button></div>'); break;
    case 'repair': {
      const finding = findings.find(item => item.id === state.findingId);
      modalFrame('Approve a local repair', `<p>${escape(finding?.summary || 'No eligible recorded defect selected.')}</p><p class="lead">This approves investigation and a repair in the authorized connected source. Review the prepared patch and exact preview before accepting its outcome.</p><p>No code is pushed, published, merged or deployed to production.</p>${finding && !finding.measured ? '<p class="provider-notice">This approval uses a disclosed local fixture defect. It does not verify a Dodo payment.</p>' : ''}<p class="small muted">${owner ? 'Owner session connected.' : 'An owner must connect a session before approval is available.'}</p><div class="actions dialog-actions"><button class="primary" data-action="confirm-repair" ${busy || !owner || !finding || !current || !repairableFinding(finding, current) ? 'disabled' : ''}>Approve local repair</button><button class="secondary" data-action="setup">Owner connection</button></div>`); break;
    }
    case 'checkpoint-access': {
      const matches = current?.id === state.runId && current.repair?.id === state.repairId;
      modalFrame('Verify public checkpoint access', `<p>The local service will anonymously read the approved repository’s immutable checkpoint, transcript and repair source, then retain an access receipt.</p><p>Publication and privacy review happen separately. This action does not publish, repair, retest or reserve another budget.</p><p class="small muted">Hosted Entire UI access remains unverified. A repeated successful request returns the original historical receipt.</p>${current?.repair ? `<div class="evidence-row"><strong>Saved checkpoint</strong><span>${escape(current.repair.checkpoint_id)}</span></div>` : ''}${banner(error)}<div class="actions dialog-actions"><button class="primary" data-action="confirm-checkpoint-access" ${busy || !matches || !current || !canVerifyCheckpoint(current, owner) ? 'disabled' : ''}>Verify approved public checkpoint</button><button class="secondary" data-action="setup">Owner connection</button></div>`);
      break;
    }
    case 'after-retry-quote': {
      const quote = state.quote;
      const enabled = owner && current?.id === quote.retry_of_run_id && quote.ready && quote.affordable && !quote.gates.length && Date.parse(quote.expires_at) > Date.now();
      const budgetBlocker = !quote.affordable ? `The full reservation exceeds available forecast allowance after release (${money(quote.retry_budget.available_after_release_microusd)}). Release an unused historical hold or review the plan, then request a new quote.` : '';
      modalFrame('Review a fresh after-only forecast', `${renderAfterRetryQuote(quote)}${banner(budgetBlocker)}${banner(error)}<div class="actions dialog-actions"><button class="primary" data-action="confirm-after-retry" ${busy || !enabled ? 'disabled' : ''}>Run all ${number(quote.persona_count)} saved customers with fresh accounts</button></div>`);
    } break;
    case 'after-recovery': if (current) {
      modalFrame('Recover the same after pass', `<p>Run a new isolated diagnostic account, then release all ${number(current.persona_count)} original saved customers on repair ${escape(current.repair?.head_revision || '')}.</p><p>Old payment sessions are not resubmitted. The original blocked attempt and trace remain recorded. The service checks that the remaining original forecast covers this diagnostic and the full crowd; no new reservation or charge is created.</p>${banner(error)}<div class="actions dialog-actions"><button class="primary" data-action="confirm-after-recovery" ${busy || !owner || !afterRecoveryAvailable(current) ? 'disabled' : ''}>Recover all ${number(current.persona_count)} saved customers</button></div>`);
    } break;
    case 'replay': if (current) {
      const reason = currentReplayBlocker(current);
      modalFrame('Retest the same saved customers', `<p class="lead">All ${number(current.persona_count)} personas from population ${escape(current.population_id)} will retry the registered repair preview.</p><p>Profiles and supported starting conditions are preserved. Accounts are isolated and decision memory is reset. Known-failure actions are checked separately before adaptive retesting.</p><p class="same-population">The after pass reuses the original reservation. No new crowd and no second reservation.</p><div class="evidence-row"><strong>Exact repair revision</strong><span>${escape(current.repair?.head_revision || 'Unavailable')}</span></div>${reason ? banner(reason) : ''}<p class="small muted">${owner ? 'Owner session connected.' : 'Owner authorization is required to start the retest.'}</p><div class="actions dialog-actions"><button class="primary" data-action="confirm-replay" ${busy || !owner || !!reason || !!pendingRequest() ? 'disabled' : ''}>Retest all ${number(current.persona_count)} saved customers</button><button class="secondary" data-action="setup">Owner connection</button></div>`);
    } break;
  }
}
function renderPeople(state: Extract<Modal, { kind: 'people' }>, current: Run): void {
  const query = state.query.toLocaleLowerCase().trim();
  const people = current.personas.filter(person => `${person.name} ${person.id} ${person.index + 1}`.toLocaleLowerCase().includes(query));
  const pages = Math.max(1, Math.ceil(people.length / 20));
  state.page = Math.max(0, Math.min(state.page, pages - 1));
  const rows = people.slice(state.page * 20, state.page * 20 + 20);
  modalFrame('Your saved customers', `<p class="small muted">${number(current.personas.length)} saved / ${number(current.persona_count)} requested. Inspection never changes the population.</p><label class="label people-search-label" for="person-search">Search saved customers</label><input class="field" id="person-search" type="search" placeholder="Name or customer number" value="${escape(state.query)}"><div class="person-list">${rows.length ? rows.map(person => `<button class="person-row" data-action="person" data-id="${escape(person.id)}"><span><strong>${escape(person.name)}</strong><small>#${number(person.index + 1)} · ${escape(person.id)}</small></span><span class="person-status">${escape(customerStatus(person, current))}</span></button>`).join('') : `<p class="empty">${current.personas.length ? 'No saved customers match this search.' : 'No profiles have been generated. Setup blockers and queued work do not create fictional customers.'}</p>`}</div><div class="pager"><button class="text-button" data-action="people-prev" ${state.page === 0 ? 'disabled' : ''}>Previous</button><span>${people.length ? `${state.page * 20 + 1}–${Math.min((state.page + 1) * 20, people.length)} of ${number(people.length)}` : '0 saved customers'}</span><button class="text-button" data-action="people-next" ${state.page >= pages - 1 ? 'disabled' : ''}>Next</button></div>`, true);
}
async function refreshReadiness(): Promise<void> {
  try {
    readiness = await api.readiness(); readinessError = '';
    if (!appUrl && readiness.target_url) appUrl = readiness.target_url;
  } catch (value) { readinessError = value instanceof ApiError && value.status === 404 ? 'This local service does not expose connection readiness yet. Start it with the current API routes.' : errorMessage(value); }
  if (view.kind === 'start' || view.kind === 'setup') render();
}
async function refreshOwner(): Promise<void> {
  try { const session = await api.owner(); owner = session.owner; ownerSupported = true; ownerError = ''; }
  catch (value) { owner = false; ownerSupported = false; ownerError = errorMessage(value); }
  if (!owner) {
    connectionEpoch += 1;
    connections = { kind: 'guest', message: 'Authorize the owner to inspect approved app and source connections.' };
    connectionUrl = '';
  }
  if (view.kind === 'setup') render();
}
async function refreshConnections(): Promise<void> {
  if (!owner) return;
  const epoch = ++connectionEpoch;
  const previous = connectionSnapshot(connections);
  connections = { kind: 'loading', previous };
  if (view.kind === 'setup') render();
  try {
    const data = await api.connections();
    if (epoch !== connectionEpoch || !owner) return;
    connections = { kind: 'ready', data };
    if (!data.targets.some(target => target.app_url === connectionUrl)) connectionUrl = data.targets.find(target => target.app_url === appUrl)?.app_url ?? data.targets[0]?.app_url ?? '';
  } catch (value) {
    if (epoch !== connectionEpoch) return;
    if (value instanceof ApiError && [401, 403].includes(value.status)) {
      owner = false;
      connections = { kind: 'guest', message: 'The owner session was declined. Authorize the owner again to inspect connections.' };
      connectionUrl = '';
      await refreshOwner();
    } else if (value instanceof ApiError && value.status === 404) {
      connections = { kind: 'unsupported', message: 'This service does not expose app registration. Update the local service to use supported owner connections; the general setup check remains available.' };
    } else connections = { kind: 'unavailable', message: errorMessage(value), previous };
  }
  if (view.kind === 'setup') render();
}
async function refreshSetup(): Promise<void> {
  await refreshOwner();
  await Promise.allSettled([refreshReadiness(), refreshConnections()]);
}
async function registerApp(): Promise<void> {
  if (!owner || connections.kind !== 'ready' || !registrationAvailable(connections)) throw new Error('Refresh owner connection capabilities before registering an app.');
  const data = connections.data;
  const url = connectionUrl;
  if (!data.targets.some(target => target.app_url === url)) throw new Error('Choose an app approved by this service. General customer URLs need a supported source and adapter connection.');
  const epoch = ++connectionEpoch;
  connections = { kind: 'loading', previous: data };
  if (view.kind === 'setup') render();
  try {
    const registered = await api.registerConnection(url);
    const updated = applyRegistration(data, url, registered);
    if (epoch !== connectionEpoch || !owner) return;
    connections = { kind: 'ready', data: updated };
    notify(registrationMessage(registered));
    await refreshReadiness();
  } catch (value) {
    if (epoch === connectionEpoch) connections = { kind: 'unavailable', message: errorMessage(value), previous: data };
    throw value;
  }
}
async function getQuote(): Promise<void> {
  busy = true; error = ''; render();
  try { const quote = await api.quote(appUrl); go({ kind: 'quote', quote }); }
  catch (value) { error = errorMessage(value); }
  finally { busy = false; render(); }
}
function remember(current: Run, token: string): boolean {
  const saved = saveRun(current, token, savedRuns); savedRuns = saved.runs;
  if (!saved.persisted) notify('This browser blocked session storage. Keep this tab open to retain access to the run.');
  return saved.persisted;
}
async function submitSavedRequest(): Promise<void> {
  const pending = requestJournal.read()?.pending;
  if (!pending) return;
  if (pending.kind !== 'create' && !owner) throw new Error('Connect the owner session to recover the original approved owner request.');
  try {
    const result = await recoverRequest(requestJournal, saved => {
      switch (saved.kind) {
        case 'create': return api.createRun(saved.request.quote_id, saved.key);
        case 'replay': return api.replay(saved.runId, saved.request.repair_id, saved.key);
        case 'after-retry': return api.afterRetry(saved.runId, saved.request.quote_id, saved.key);
        case 'baseline-retry': return api.baselineRetry(saved.runId, saved.request.quote_id, saved.key);
      }
    },
    result => remember(result.status, result.access_token));
    run = result.status;
    const entry = savedRuns.find(item => item.id === result.id);
    if (entry) await openRun(entry, result.status);
  } catch (value) {
    if (value instanceof ApiError) requestJournal.decline(value.status, value.code, value.message);
    throw value;
  }
}
async function openRun(entry: SavedRun, initial?: Run): Promise<void> {
  stopPolling(); closeModal(); error = ''; pollError = ''; findings = []; findingsError = ''; findingsRunId = null;
  catalogState = { kind: 'loading' };
  comparisonBefore = null; comparisonError = ''; comparisonEpoch = -1;
  view = { kind: 'run', id: entry.id };
  location.hash = `run=${encodeURIComponent(entry.id)}`;
  render();
  await pollRun(pollEpoch, initial);
}
async function refreshFindings(id: string, token: string): Promise<void> {
  const epoch = pollEpoch;
  try { const result = await api.findings(id, token); if (epoch === pollEpoch && view.kind === 'run' && view.id === id) { findings = result.findings; findingsError = ''; findingsRunId = id; } }
  catch (value) { if (epoch === pollEpoch && view.kind === 'run' && view.id === id) findingsError = errorMessage(value); }
}
async function refreshScenarioCatalog(current: Run, epoch: number): Promise<void> {
  catalogEpoch = epoch;
  try {
    const catalog = await api.scenarios(current.app_url);
    if (epoch !== pollEpoch || view.kind !== 'run' || view.id !== current.id) return;
    catalogState = { kind: 'ready', catalog };
  } catch (value) {
    if (epoch !== pollEpoch || view.kind !== 'run' || view.id !== current.id) return;
    catalogState = { kind: 'unavailable', message: value instanceof ApiError && value.status === 404 ? 'This service does not expose the scenario catalog. Historical profiles and observations remain available.' : `Scenario catalog unavailable: ${errorMessage(value)}` };
  }
  render();
}
async function refreshComparisonBefore(current: Run, epoch: number): Promise<void> {
  if (current.pass !== 'after' || !current.parent_run_id) return;
  comparisonEpoch = epoch;
  const originalId = current.parent_run_id;
  const entry = savedRuns.find(item => item.id === originalId);
  try {
    if (!entry && !owner) throw new Error('Authorize the owner or open the initial run in this tab to load its evidence.');
    const before = await api.run(originalId, entry?.token ?? '');
    if (epoch !== pollEpoch || view.kind !== 'run' || view.id !== current.id) return;
    if (before.id !== originalId) throw new Error('The service returned a different initial run.');
    comparisonBefore = before; comparisonError = '';
  } catch (value) {
    if (epoch !== pollEpoch || view.kind !== 'run' || view.id !== current.id) return;
    comparisonError = `Initial evidence unavailable: ${errorMessage(value)}`;
  }
  render();
}
async function refreshTraceReceipts(runId: string): Promise<void> {
  const entry = savedRuns.find(item => item.id === runId);
  traceStates.set(runId, { kind: 'loading' });
  if (modal) renderModal();
  try {
    if (!entry && !owner) throw new Error('Owner authorization or saved run access is required to read its trace receipts.');
    const data = await api.traces(runId, entry?.token ?? '');
    if (data.run_id !== runId) throw new Error('The trace receipts belong to another run.');
    traceStates.set(runId, { kind: 'ready', data });
  } catch (value) { traceStates.set(runId, { kind: 'unavailable', message: errorMessage(value) }); }
  if (modal) renderModal();
}
async function inspectRemoteTrace(runId: string, traceId: string): Promise<void> {
  const state = traceStates.get(runId);
  if (state?.kind !== 'ready' || !state.data.traces.some(item => item.trace_id === traceId)) return;
  const entry = savedRuns.find(item => item.id === runId);
  if (!entry && !owner) return;
  const inspection: Extract<Modal, { kind: 'remote-trace' }> = { kind: 'remote-trace', runId, traceId, result: null, message: '' };
  openModal(inspection);
  try {
    const result = await api.inspectTrace(runId, traceId, entry?.token ?? '');
    if (result.trace_id !== traceId || result.run_id && result.run_id !== runId) throw new Error('The hosted trace response differs from the selected run and trace.');
    if (modal !== inspection) return;
    inspection.result = result;
    if (result.finalized_readback === 'verified') {
      const receipt = state.data.traces.find(item => item.trace_id === traceId);
      if (receipt) receipt.finalized_readback = 'verified';
    }
  } catch (value) { if (modal === inspection) inspection.message = errorMessage(value); }
  if (modal === inspection) renderModal();
}
async function pollRun(epoch: number, initial?: Run): Promise<void> {
  if (view.kind !== 'run' || epoch !== pollEpoch) return;
  const id = view.id;
  const entry = savedRuns.find(item => item.id === id);
  if (!entry) { error = 'This tab has no access token for the run. Open one from Runs, or start a new test.'; render(); return; }
  try {
    const reads = readRunEvidence(api, { id, token: entry.token, initial });
    const next = await reads.run;
    if (epoch !== pollEpoch || view.kind !== 'run' || view.id !== id) return;
    const nextFindings = await reads.findings;
    if (epoch !== pollEpoch || view.kind !== 'run' || view.id !== id) return;
    const changed = JSON.stringify(run) !== JSON.stringify(next) || !!pollError;
    run = next; pollError = ''; error = '';
    if (entry.parentRunId !== next.parent_run_id) remember(next, entry.token);
    const previousFindings = JSON.stringify(findings);
    const previousError = findingsError;
    const previousFindingsRunId = findingsRunId;
    if (nextFindings.kind === 'ready') { findings = nextFindings.findings; findingsError = ''; findingsRunId = id; }
    else findingsError = errorMessage(nextFindings.error);
    if (changed || previousFindings !== JSON.stringify(findings) || previousError !== findingsError || previousFindingsRunId !== findingsRunId) { render(); if (modal?.kind === 'person' || modal?.kind === 'person-trace') renderModal(); }
    if (catalogEpoch !== epoch) void refreshScenarioCatalog(next, epoch);
    if (next.pass === 'after' && comparisonEpoch !== epoch) void refreshComparisonBefore(next, epoch);
    if (isActive(next) || next.repair?.status === 'queued' || next.repair?.status === 'running') pollTimer = setTimeout(() => void pollRun(epoch), 1000);
  } catch (value) {
    if (epoch !== pollEpoch) return;
    pollError = errorMessage(value);
    if (!run || run.id !== id) error = pollError;
    render();
    if (!(value instanceof ApiError) || ![401, 403, 404].includes(value.status)) pollTimer = setTimeout(() => void pollRun(epoch), 5000);
  }
}
async function mutate(operation: () => Promise<void>): Promise<void> {
  if (busy) return;
  busy = true; error = '';
  const activeElement = document.activeElement;
  if (activeElement instanceof HTMLButtonElement) activeElement.disabled = true;
  try { await operation(); }
  catch (value) { error = errorMessage(value); notify(error); if (value instanceof ApiError && [401, 403].includes(value.status)) await refreshOwner();  }
  finally { busy = false; render(); if (modal) renderModal(); }
}
async function action(name: string, id: string): Promise<void> {
  switch (name) {
    case 'home': go({ kind: 'start' }); break;
    case 'setup': if (!busy) { go({ kind: 'setup' }); await refreshSetup(); } break;
    case 'setup-back': {
      const previous = setupReturn;
      if (previous?.kind === 'run') {
        let entry = savedRuns.find(item => item.id === previous.id);
        if (!entry && owner) {
          try { const requested = await api.run(previous.id, ''); remember(requested, ''); entry = savedRuns.find(item => item.id === previous.id); }
          catch (value) { error = errorMessage(value); render(); break; }
        }
        if (entry) await openRun(entry);
        else { error = 'Authorize the owner to open this run, or choose a run saved in this tab.'; render(); }
      } else go(previous ?? { kind: 'start' });
      break;
    }
    case 'about': openModal({ kind: 'about' }); break;
    case 'history': openModal({ kind: 'history' }); break;
    case 'close': closeModal(); break;
    case 'demo': if (readiness?.target_url && readinessFacts(readiness, readinessError).canSelectApp) { appUrl = readiness.target_url; go({ kind: 'start' }); } break;
    case 'refresh-readiness': if (!busy) await refreshSetup(); break;
    case 'use-connection': {
      const target = connections.kind === 'ready' ? connections.data.targets.find(item => item.id === id) : null;
      if (target?.registration_status === 'registered' && sourceVerified(target) && !busy) { appUrl = target.app_url; go({ kind: 'start' }); }
      break;
    }
    case 'owner-logout': await mutate(async () => { await api.disconnectOwner(); owner = false; connectionEpoch += 1; connections = { kind: 'guest', message: 'Authorize the owner to inspect approved app and source connections.' }; connectionUrl = ''; notify('Owner disconnected. You can still inspect your saved runs.'); }); break;
    case 'requote': await getQuote(); break;
    case 'confirm-quote': {
      if (view.kind !== 'quote') break;
      const quoteView = view;
      const countBlocker = fixedPopulationBlocker(quoteView.quote.persona_count);
      if (countBlocker) { error = countBlocker; render(); break; }
      if (quoteView.quote.kind === 'baseline_retry') { error = 'A baseline retry must be reviewed from its preserved original attempt.'; render(); break; }
      if (pendingRequest()) { openModal({ kind: 'pending' }); break; }
      if (!quoteView.quote.affordable || Date.parse(quoteView.quote.expires_at) <= Date.now()) { error = 'Refresh the budget before starting.'; render(); break; }
      await mutate(async () => {
        requestJournal.prepare({ kind: 'create', key: crypto.randomUUID(), approvedAt: new Date().toISOString(), request: { quote_id: quoteView.quote.id }, quote: quoteView.quote });
        await submitSavedRequest();
      });
      break;
    }
    case 'pending-review': openModal({ kind: 'pending' }); break;
    case 'recover-request': await mutate(submitSavedRequest); break;
    case 'dismiss-declined': await mutate(async () => { requestJournal.dismissDeclined(); closeModal(); notify('Declined request resolved. You can review a new budget.'); }); break;
    case 'open-run': { const entry = savedRuns.find(item => item.id === id); if (entry) await openRun(entry); break; }
    case 'related-run': {
      const existing = savedRuns.find(item => item.id === id);
      if (existing) { await openRun(existing); break; }
      if (!owner) { error = 'Connect the owner session to inspect the related attempt.'; render(); break; }
      await mutate(async () => {
        const related = await api.run(id, '');
        if (related.id !== id) throw new Error('The response differs from the selected related attempt. Refresh its saved history.');
        remember(related, '');
        const entry = savedRuns.find(item => item.id === id);
        if (entry) await openRun(entry, related);
      });
      break;
    }
    case 'baseline-retry-quote': case 'requote-baseline-retry': {
      const original = view.kind === 'baseline-retry-quote' ? view.original : view.kind === 'run' && run?.id === view.id ? run : null;
      if (!original) break;
      if (pendingRequest()) { openModal({ kind: 'pending' }); break; }
      const reason = fixedPopulationBlocker(original.persona_count) ?? baselineRetryBlocker(original, owner, findingsRunId === original.id && !findingsError, findings.length > 0);
      if (reason) { error = reason; render(); break; }
      const sourceView = view;
      await mutate(async () => {
        const quote = await api.baselineRetryQuote(original);
        if (view !== sourceView || !owner) return;
        go({ kind: 'baseline-retry-quote', quote, original });
      });
      break;
    }
    case 'confirm-baseline-retry': {
      if (view.kind !== 'baseline-retry-quote') break;
      const selected = view;
      if (pendingRequest()) { openModal({ kind: 'pending' }); break; }
      const reason = fixedPopulationBlocker(selected.quote.persona_count) ?? retryQuoteApprovalBlocker(selected.quote, selected.original, owner);
      if (reason) { error = reason; render(); break; }
      const original = selected.original;
      if (!original.source_commit || !original.source_digest) { error = 'The original source binding is unavailable.'; render(); break; }
      const sourceCommit = original.source_commit, sourceDigest = original.source_digest;
      await mutate(async () => {
        requestJournal.prepare({ kind: 'baseline-retry', key: crypto.randomUUID(), approvedAt: new Date().toISOString(), runId: original.id,
          request: { quote_id: selected.quote.id }, quote: selected.quote, originalRevision: original.revision,
          originalSourceCommit: sourceCommit, originalSourceDigest: sourceDigest });
        await submitSavedRequest();
      });
      break;
    }
    case 'retry-run': case 'refresh-run': { stopPolling(); await pollRun(pollEpoch); break; }
    case 'refresh-comparison': if (run) await refreshComparisonBefore(run, pollEpoch); break;
    case 'before-person-trace': if (run?.pass === 'after' && comparisonBefore?.personas.some(person => person.id === id)) { openModal({ kind: 'before-person-trace', afterRunId: run.id, personaId: id }); await refreshTraceReceipts(comparisonBefore.id); } break;
    case 'refresh-trace-receipts': if (id === run?.id || id === comparisonBefore?.id) await refreshTraceReceipts(id); break;
    case 'remote-trace': { const [runId, traceId] = id.split(':'); if (runId && traceId) await inspectRemoteTrace(runId, traceId); break; }
    case 'retry-findings': { if (run) { const entry = savedRuns.find(item => item.id === run?.id); if (entry) { await refreshFindings(entry.id, entry.token); render(); } } break; }
    case 'people': openModal({ kind: 'people', query: '', page: 0 }); break;
    case 'people-prev': case 'people-next': if (modal?.kind === 'people') { modal.page += name === 'people-prev' ? -1 : 1; renderModal(); } break;
    case 'person': case 'person-trace': {
      if (view.kind !== 'run' || !run || run.id !== view.id || !run.personas.some(person => person.id === id)) break;
      openModal({ kind: name, selection: { runId: run.id, populationId: run.population_id, epoch: pollEpoch, personaId: id } }); if (name === 'person-trace') await refreshTraceReceipts(run.id); break;
    }
    case 'screenshot': openModal({ kind: 'screenshot', eventId: Number(id) }); break;
    case 'finding': openModal({ kind: 'finding', id }); break;
    case 'trace': openModal({ kind: 'trace' }); if (run) await refreshTraceReceipts(run.id); break;
    case 'refresh-trace': stopPolling(); await pollRun(pollEpoch); if (run) await refreshTraceReceipts(run.id); if (modal?.kind === 'trace') renderModal(); break;
    case 'cancel': if (run && canCancel(run)) openModal({ kind: 'cancel' }); break;
    case 'confirm-cancel': {
      const entry = savedRuns.find(item => item.id === run?.id);
      if (!entry) break;
      await mutate(async () => { run = await api.cancel(entry.id, entry.token); stopPolling(); closeModal(); notify('Run ended. Recorded evidence is saved.'); }); break;
    }
    case 'repair-review': openModal({ kind: 'repair', findingId: id }); break;
    case 'confirm-repair': {
      if (!owner || !run || modal?.kind !== 'repair') break;
      const current = run; const findingId = modal.findingId;
      if (!findings.some(finding => finding.id === findingId && repairableFinding(finding, current))) break;
      await mutate(async () => { current.repair = await api.repair(current.id, findingId); closeModal(); stopPolling(); await pollRun(pollEpoch); }); break;
    }
    case 'open-saved-after-retry': { const entry = savedRuns.find(item => item.id === id); if (entry) await openRun(entry); break; }
    case 'after-retry-quote': {
      if (!run || !owner || pendingRequest()) break;
      const current = run;
      await mutate(async () => { const quote = await api.afterRetryQuote(current.id); if (run?.id !== current.id || quote.retry_of_run_id !== current.id || quote.population_id !== current.population_id || quote.head_revision !== current.source_commit) throw new Error('The forecast differs from this saved cohort or exact source. Refresh before approval.'); openModal({ kind: 'after-retry-quote', quote }); }); break;
    }
    case 'confirm-after-retry': {
      if (!run || !owner || modal?.kind !== 'after-retry-quote' || pendingRequest()) break;
      const quote = modal.quote;
      if (run.id !== quote.retry_of_run_id || !quote.ready || !quote.affordable || quote.gates.length || Date.parse(quote.expires_at) <= Date.now()) break;
      requestJournal.prepare({ kind: 'after-retry', key: crypto.randomUUID(), approvedAt: new Date().toISOString(), runId: run.id, request: { quote_id: quote.id }, quote });
      await mutate(submitSavedRequest); break;
    }
    case 'after-recovery-review': if (run && owner && afterRecoveryAvailable(run)) openModal({ kind: 'after-recovery' }); break;
    case 'confirm-after-recovery': {
      if (!run || !owner || modal?.kind !== 'after-recovery' || !afterRecoveryAvailable(run)) break;
      const current = run;
      await mutate(async () => { run = await api.recoverAfter(current.id); closeModal(); stopPolling(); await pollRun(pollEpoch); }); break;
    }
    case 'recover-repair': {
      if (!owner || !run || run.repair?.status !== 'blocked') break;
      const current = run;
      await mutate(async () => { current.repair = await api.recoverRepair(current.id); stopPolling(); await pollRun(pollEpoch); }); break;
    }
    case 'checkpoint-access-review': {
      if (!run?.repair || !canVerifyCheckpoint(run, owner)) break;
      error = ''; openModal({ kind: 'checkpoint-access', runId: run.id, repairId: run.repair.id }); break;
    }
    case 'confirm-checkpoint-access': {
      if (!run?.repair || modal?.kind !== 'checkpoint-access' || run.id !== modal.runId
        || run.repair.id !== modal.repairId || !canVerifyCheckpoint(run, owner)) break;
      const current = run; const repair = run.repair;
      await mutate(async () => {
        const updated = await api.verifyCheckpointAccess(current.id, repair);
        current.repair = updated;
        closeModal();
        if (view.kind === 'run' && view.id === current.id) { stopPolling(); await pollRun(pollEpoch); }
      }); break;
    }
    case 'replay-review': {
      if (pendingRequest()) { openModal({ kind: 'pending' }); break; }
      if (!run) break;
      const reason = currentReplayBlocker(run);
      if (reason) { error = reason; render(); break; }
      openModal({ kind: 'replay' }); break;
    }
    case 'confirm-replay': {
      if (!owner || !run?.repair || modal?.kind !== 'replay') break;
      if (pendingRequest()) { openModal({ kind: 'pending' }); break; }
      const current = run; const repair = run.repair;
      const reason = currentReplayBlocker(current);
      if (reason || !repair.head_revision) { error = reason || 'The exact repair revision is unavailable.'; renderModal(); break; }
      const repairRevision = repairTargetRevision(current);
      if (!repairRevision) { error = 'The registered repair preview revision is unavailable.'; renderModal(); break; }
      const repairSourceCommit = repair.head_revision;
      await mutate(async () => {
        requestJournal.prepare({ kind: 'replay', key: crypto.randomUUID(), approvedAt: new Date().toISOString(), runId: current.id, request: { repair_id: repair.id }, populationId: current.population_id, personaCount: current.persona_count, repairRevision, ...(current.provider === 'dodo' ? { repairSourceCommit } : {}) });
        await submitSavedRequest();
      }); break;
    }
    case 'download': if (run) {
      const blob = new Blob([JSON.stringify(redact({ product: 'TryFolk', source: 'Recorded local service results', run, findings,
        before_run: run.pass === 'after' ? comparisonBefore : null,
        trace_receipts: [traceStates.get(run.id), ...(comparisonBefore ? [traceStates.get(comparisonBefore.id)] : [])]
          .flatMap(state => state?.kind === 'ready' ? [state.data] : []),
      }), null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `tryfolk-${run.id}.json`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } break;
  }
}
document.addEventListener('click', event => {
  if (!(event.target instanceof Element)) return;
  const target = event.target.closest('[data-action]');
  if (!(target instanceof HTMLElement) || target instanceof HTMLButtonElement && target.disabled) return;
  if (busy && ['confirm-quote', 'confirm-cancel', 'confirm-repair', 'confirm-replay', 'confirm-checkpoint-access', 'confirm-baseline-retry', 'confirm-after-recovery', 'confirm-after-retry', 'recover-request', 'dismiss-declined'].includes(target.dataset.action || '')) return;
  void action(target.dataset.action || '', target.dataset.id || '');
});
document.addEventListener('submit', event => {
  if (!(event.target instanceof HTMLFormElement)) return;
  if (event.target.id === 'owner-form') {
    event.preventDefault();
    const data = new FormData(event.target);
    const secret = String(data.get('secret') || '');
    event.target.reset();
    if (secret && !busy) void mutate(async () => { const session = await api.connectOwner(secret); if (!session.owner) throw new Error('Owner authorization was not granted.'); owner = true; ownerSupported = true; ownerError = ''; await refreshConnections(); notify(owner ? 'Owner session authorized.' : 'Owner authorization needs review.'); });
    return;
  }
  if (event.target.id === 'connection-form') {
    event.preventDefault();
    const data = new FormData(event.target); connectionUrl = String(data.get('app_url') || '');
    if (!busy) void mutate(registerApp);
    return;
  }
  if (event.target.id !== 'start-form') return;
  event.preventDefault();
  const data = new FormData(event.target); appUrl = String(data.get('url') || '').trim();
  try { const url = new URL(appUrl); if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error(); }
  catch { error = 'Enter a full http or https app URL without credentials.'; render(); return; }
  if (!busy) void getQuote();
});
document.addEventListener('input', event => {
  if (event.target instanceof HTMLSelectElement && event.target.id === 'connection-url') { connectionUrl = event.target.value; return; }
  if (!(event.target instanceof HTMLInputElement)) return;
  if (event.target.id === 'url') appUrl = event.target.value;
  if (event.target.id === 'person-search' && modal?.kind === 'people') {
    const start = event.target.selectionStart; modal.query = event.target.value; modal.page = 0; renderModal();
    const input = elements.dialog.querySelector('#person-search'); if (input instanceof HTMLInputElement) { input.focus(); if (start !== null) input.setSelectionRange(start, start); }
  }
});
elements.dialog.addEventListener('close', () => { if (!elements.dialog.open) { modal = null; restoreModalFocus(); } });
elements.dialog.addEventListener('keydown', event => {
  if (event.key !== 'Tab' || !elements.dialog.open) return;
  const focusable = Array.from(elements.dialog.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]')).filter(element => element.getClientRects().length > 0);
  const first = focusable[0], last = focusable.at(-1);
  if (!first || !last) return;
  if (event.shiftKey && (document.activeElement === first || document.activeElement?.id === 'dialog-heading')) {
    event.preventDefault(); last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault(); first.focus();
  }
});
window.addEventListener('pagehide', stopPolling);
render();
void refreshReadiness();
void (async () => {
  await refreshOwner();
  const requestedId = location.hash.startsWith('#run=') ? decodeURIComponent(location.hash.slice(5)) : null;
  if (!requestedId) return;
  let existing = savedRuns.find(item => item.id === requestedId);
  let initial: Run | undefined;
  if (!existing && owner) {
    try {
      const requested = await api.run(requestedId, '');
      remember(requested, '');
      existing = savedRuns.find(item => item.id === requestedId);
      initial = requested;
    } catch (value) { error = errorMessage(value); render(); }
  }
  if (existing) await openRun(existing, initial);
  else if (!owner) { ownerError = 'Authorize the local owner to inspect this run link, or open a run saved in this tab.'; go({ kind: 'setup' }); setupReturn = { kind: 'run', id: requestedId }; render(); }
})();
