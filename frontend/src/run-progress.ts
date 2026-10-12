import type { Persona, Run, RunEvent } from './contracts.ts';
import { escape, gates, humanize, number, timestamp } from './presentation.ts';

const outcomeKinds = ['completed', 'failed', 'abandoned', 'pending', 'blocked'];
const browserKinds = ['observation', 'browser_action'];

// A successful browser action, decision or run phase is not a customer outcome.
export function recordedPersonaOutcome(events: RunEvent[]): string | null {
  for (const event of [...events].reverse()) {
    if (event.kind === 'verification' && typeof event.data.outcome === 'string' && outcomeKinds.includes(event.data.outcome)) return humanize(event.data.outcome);
    if (outcomeKinds.includes(event.kind)) return humanize(event.kind);
  }
  return null;
}

export function customerStatus(person: Persona, current: Run): string {
  const events = current.events.filter(event => event.persona_id === person.id);
  return recordedPersonaOutcome(events) ?? (events.some(event => browserKinds.includes(event.kind))
    ? 'No outcome recorded · browser steps saved' : 'No outcome recorded · profile saved');
}

export function phaseCopy(current: Run): string {
  switch (current.phase) {
    case 'queued': return 'Waiting for a browser session.';
    case 'discovering': return 'Looking at what your product promises.';
    case 'generating': return 'Saving the customers who will try your product.';
    case 'running': return 'Customers are trying the product. Recorded steps and outcomes appear below.';
    case 'verifying': return 'Checking the recorded outcomes.';
    case 'completed': return 'The recorded journeys are ready to inspect.';
    case 'blocked': return 'This run stopped at a reported blocker. Saved profiles and steps remain inspectable.';
    case 'failed': return 'The run stopped. Its recorded evidence is still available.';
    case 'cancelled': return 'The run was cancelled. Partial results are saved.';
  }
}

export function recordedProgress(current: Run) {
  const saved = new Set(current.personas.map(person => person.id));
  const customerEvents = current.events.filter(event => event.persona_id !== null && saved.has(event.persona_id));
  const activity = customerEvents.filter(event => browserKinds.includes(event.kind));
  return {
    savedProfiles: current.counts.generated,
    discoveryRecords: current.events.filter(event => event.kind === 'discovery').length,
    customersWithBrowserActivity: new Set(activity.map(event => event.persona_id)).size,
    observationRecords: customerEvents.filter(event => event.kind === 'observation').length,
    decisionRecords: customerEvents.filter(event => event.kind === 'decision').length,
    browserActionRecords: customerEvents.filter(event => event.kind === 'browser_action').length,
    // The actual API defines executed as persisted outcomes, including pending/blocked.
    recordedOutcomes: current.counts.executed,
    withoutRecordedOutcome: Math.max(0, current.persona_count - current.counts.executed),
    latestEvent: current.events.at(-1) ?? null,
  };
}

export function renderRunProgress(current: Run): string {
  const progress = recordedProgress(current);
  return `<section class="run-progress" aria-label="Recorded run progress">
    <h2>Recorded progress</h2>
    <p class="small muted">Service-reported stage: ${escape(humanize(current.phase))} · Updated ${escape(timestamp(current.updated_at))}</p>
    ${gates(current.gates)}
    <dl class="progress-facts">
      <div><dt>Product discovery</dt><dd>${number(progress.discoveryRecords)} saved records</dd></div>
      <div><dt>Saved customers</dt><dd>${number(progress.savedProfiles)} / ${number(current.persona_count)} profiles</dd></div>
      <div><dt>Browser activity</dt><dd>${number(progress.customersWithBrowserActivity)} / ${number(current.persona_count)} saved customers</dd></div>
      <div><dt>Recorded customer outcomes</dt><dd>${number(progress.recordedOutcomes)} / ${number(current.persona_count)}</dd></div>
    </dl>
    <p class="small muted">${number(progress.observationRecords)} observation records · ${number(progress.decisionRecords)} decision records · ${number(progress.browserActionRecords)} browser-action records. These counts come from saved events; they do not establish a completed journey or successful payment.</p>
    ${progress.withoutRecordedOutcome ? `<p class="progress-boundary">${number(progress.withoutRecordedOutcome)} / ${number(current.persona_count)} customers have no recorded outcome. Saved profiles and browser steps are not passes or failures.${current.phase === 'blocked' ? ' The service includes customers stopped by this run’s gate in its blocked count; that is separate from a recorded customer result.' : ''}</p>` : ''}
    ${progress.latestEvent ? `<p class="small muted">Latest saved event: ${escape(humanize(progress.latestEvent.kind))} · event ${number(progress.latestEvent.id)} · ${escape(timestamp(progress.latestEvent.created_at))}. This event is not a diagnosis of the blocker.</p>` : '<p class="small muted">No activity events were included in this saved record.</p>'}
    <button class="secondary" data-action="trace">Inspect recorded activity</button>
  </section>`;
}
