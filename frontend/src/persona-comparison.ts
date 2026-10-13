import type { Persona, Run } from './contracts.ts';
import { escape, link, number, safeUrl } from './presentation.ts';
import { recordedPersonaOutcome } from './run-progress.ts';

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`;
  }
  return JSON.stringify(value) ?? 'undefined';
}

export function sameSavedCustomer(before: Persona, after: Persona): boolean {
  return before.id === after.id && before.index === after.index
    && canonical(before.profile) === canonical(after.profile)
    && canonical(before.conditions) === canonical(after.conditions)
    && before.initial_semantic_memory !== undefined && after.initial_semantic_memory !== undefined
    && canonical(before.initial_semantic_memory) === canonical(after.initial_semantic_memory)
    && canonical(before.assigned_scenario) === canonical(after.assigned_scenario);
}

export function personaTraceLinks(current: Run, personaId: string): string {
  const urls = [...new Set(current.events.filter(event => event.persona_id === personaId)
    .map(event => safeUrl(event.trace_url)).filter((url): url is string => url !== null))];
  return urls.length ? urls.map((url, index) => link(url, urls.length === 1 ? 'Open Neatlogs trace' : `Open Neatlogs trace ${index + 1}`)).join(' · ')
    : '<span class="small muted">No remote trace link recorded</span>';
}

function outcome(current: Run, personaId: string): string {
  const value = recordedPersonaOutcome(current.events.filter(event => event.persona_id === personaId));
  return value === 'Completed' ? 'Passed' : value ?? 'No outcome recorded';
}

export function renderPersonaComparison(after: Run, before: Run): string {
  if (after.pass !== 'after' || before.pass !== 'before' || after.parent_run_id !== before.id
    || after.population_id !== before.population_id || after.persona_count !== before.persona_count
    || after.comparison && (after.comparison.before_run_id !== before.id || after.comparison.after_run_id !== after.id)) {
    return '<p class="error-banner">The loaded initial record does not match this after pass. No customer evidence has been paired.</p>';
  }
  const rows = before.personas.map(person => {
    const returned = after.personas.find(item => item.id === person.id);
    return `<tr><th scope="row">${escape(person.name)}<small>#${number(person.index + 1)} · ${escape(person.id)}</small></th>
      <td>${escape(outcome(before, person.id))}<div>${personaTraceLinks(before, person.id)}</div><button class="text-button" data-action="before-person-trace" data-id="${escape(person.id)}">Inspect initial evidence</button></td>
      <td>${returned ? escape(outcome(after, person.id)) : 'Saved customer missing'}<div>${returned ? personaTraceLinks(after, person.id) : ''}</div>${returned ? `<button class="text-button" data-action="person-trace" data-id="${escape(person.id)}">Inspect after evidence</button>` : ''}</td>
      <td>${returned && sameSavedCustomer(person, returned) ? 'Profile, conditions and initial memory match' : 'Saved input equivalence not verified'}</td></tr>`;
  });
  const extras = after.personas.filter(person => !before.personas.some(original => original.id === person.id));
  return `<section class="persona-comparison"><h3>Each customer's two attempts</h3><p class="small muted">These outcomes come from each customer's recorded verification events. Trace links open the recorded remote evidence; a URL alone does not prove trace delivery.</p>
    <div class="comparison-scroll"><table class="report-table"><thead><tr><th scope="col">Saved customer</th><th scope="col">Initial pass</th><th scope="col">After pass</th><th scope="col">Saved input check</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>
    ${before.personas.length !== before.persona_count ? `<p class="status-bad">Only ${number(before.personas.length)} / ${number(before.persona_count)} original profiles were supplied. Missing customers remain unresolved.</p>` : ''}
    ${extras.length ? `<p class="status-bad">${number(extras.length)} after profiles were absent from the initial population. They are not paired as the same customer.</p>` : ''}
    <div class="actions"><button class="secondary" data-action="related-run" data-id="${escape(before.id)}">Open initial run</button><button class="text-button" data-action="refresh-comparison">Refresh both-pass evidence</button></div></section>`;
}
