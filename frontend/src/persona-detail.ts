import type { Persona, Run, RunEvent, SavedScenario } from './contracts';
import { escape, eventList, evidence, humanize, json, link, number, redact } from './presentation.ts';
import { recordedPersonaOutcome } from './run-progress.ts';

export type PersonaSelection = { runId: string; populationId: string; epoch: number; personaId: string };

export function selectedPersona(input: { run: Run | null; viewRunId: string | null; epoch: number; selection: PersonaSelection }): Persona | null {
  const { run, selection } = input;
  if (!run || run.id !== input.viewRunId || run.id !== selection.runId || run.population_id !== selection.populationId || input.epoch !== selection.epoch) return null;
  return run.personas.find(person => person.id === selection.personaId) ?? null;
}

const unknown = '<p class="small muted">Unknown · not reported in this saved record.</p>';

// Profile values are data, including nested objects and strings supplied by people or providers.
// Keep them readable without interpreting them as HTML or inferring behavior from demographics.
export function readable(value: unknown, depth = 0): string {
  const clean = redact(value);
  if (clean === null || clean === undefined || clean === '') return '<span class="muted">Unknown · not reported</span>';
  if (depth > 5) return `<pre>${json(clean)}</pre>`;
  if (Array.isArray(clean)) return clean.length ? `<ul class="saved-values">${clean.map(item => `<li>${readable(item, depth + 1)}</li>`).join('')}</ul>` : '<span class="muted">No entries saved</span>';
  if (typeof clean === 'object') return Object.keys(clean).length ? `<dl class="persona-facts">${Object.entries(clean).map(([key, item]) => `<div><dt>${escape(humanize(key))}</dt><dd>${readable(item, depth + 1)}</dd></div>`).join('')}</dl>` : '<span class="muted">No entries saved</span>';
  return escape(clean);
}

function section(title: string, content: string): string {
  return `<section class="persona-section" data-focus-scope="${escape(title)}"><h3>${escape(title)}</h3>${content}</section>`;
}

export function renderSeedProvenance(seed: SavedScenario['seed']): string {
  if (!seed || seed.id !== seed.record_snapshot.id) return unknown;
  return `<p class="hypothetical-label">Synthetic seed context · not a generated customer or observed result</p>
    <p class="small muted">The source informs profile context. Saved scenario conditions and actual fixture evidence determine payment eligibility. Source country and demographics do not supply payment, risk, currency, device or locale evidence.</p>
    ${readable({ seed_id: seed.id, source: seed.source, source_record_id: seed.record_snapshot.source_record_id,
      bank_version: seed.bank_version, bank_sha256: seed.bank_sha256, record_sha256: seed.record_sha256,
      selection_rationale: seed.record_snapshot.selection_rationale,
      source_row_sha256: seed.record_snapshot.source_row_sha256, normalized_sha256: seed.record_snapshot.normalized_sha256,
      changes: seed.record_snapshot.changes })}
    ${link(seed.source.url, 'Open seed dataset source')}
    <details class="record" data-record="accepted-seed"><summary>Accepted synthetic seed context and admission declarations</summary>${readable(seed.record_snapshot)}<p class="small muted">Declared admission checks do not establish human realism, payment behavior or exercised case coverage.</p></details>`;
}

function canonicalScenario(value: string | undefined): string {
  if (value === undefined) return '';
  try {
    const parsed: unknown = JSON.parse(value);
    return `<details class="record" data-record="canonical-scenario"><summary>Saved canonical scenario record</summary>${readable(parsed)}</details>`;
  } catch { return '<p class="small muted">The saved canonical scenario record is unavailable. Its readable snapshot remains above.</p>'; }
}

export function personaOutcome(events: RunEvent[]): string {
  return recordedPersonaOutcome(events) ?? 'Unknown · no outcome recorded';
}

function records(events: RunEvent[], keys: RegExp): string {
  const rows = events.flatMap(event => {
    const data = Object.fromEntries(Object.entries(event.data).filter(([key]) => keys.test(key)));
    return Object.keys(data).length ? [`<article class="persona-evidence" data-focus-scope="event-${number(event.id)}"><p class="small muted">Event ${number(event.id)} · ${escape(humanize(event.kind))}</p>${evidence(data)}${link(event.trace_url, 'Open full trace')}</article>`] : [];
  });
  return rows.length ? rows.join('') : unknown;
}

export function renderPersonaDetail(current: Run, person: Persona): string {
  const events = current.events.filter(event => event.persona_id === person.id);
  const coverage = current.scenario_coverage?.cases.find(item => item.persona_id === person.id);
  const saved = person.assigned_scenario ?? coverage?.assigned_scenario;
  const assignment = saved?.persona_index === person.index && saved.scenario_id === saved.scenario_snapshot.id ? saved : null;
  const scenario = assignment?.scenario_snapshot;
  const challenge = assignment?.buyer_challenge ?? person.buyer_challenge;
  const observed = coverage?.observed_scenario_result;
  const result = observed ?? null;
  const preferences = Object.fromEntries(Object.entries(person.profile).filter(([key]) => /prefer|patience|constraint/i.test(key)));
  const payment = Object.fromEntries([...Object.entries(scenario?.customer_context ?? {}), ...Object.entries(person.profile), ...Object.entries(person.conditions)].filter(([key]) => /payment|billing|currency|budget|price/i.test(key)));
  const goal = person.profile.goal ?? person.profile.customer_goal ?? scenario?.customer_goal;
  const rationale = current.scenario_plan?.selection_rationale?.why_selected.find(item => item.scenario_id === assignment?.scenario_id);
  const choice = rationale ? section('Saved case-selection rationale', readable('risk_priority' in rationale ? {
    risk_priority: rationale.risk_priority, capability_basis: rationale.capability_basis,
    relevant_customer_context: rationale.context, assignment_count: rationale.assignment_count,
    policy_version: current.scenario_plan?.selection_rationale?.policy_version,
  } : {
    risk_basis: rationale.risk_basis, capability_basis: rationale.capability_basis,
    relevant_customer_context: rationale.relevant_customer_context,
    condition_fixture_at_selection: rationale.required_fixture_ready
      ? 'Reported ready at selection time · not proof of execution' : 'Not ready at selection time',
    assignment_count: rationale.assignment_count,
    policy_version: current.scenario_plan?.selection_rationale?.policy_version,
  })) : '';
  const savedProvenance = assignment ? `<details class="record" data-record="scenario-provenance"><summary>Saved scenario, support and replay provenance</summary>${readable({
    title: scenario?.title, scenario_id: assignment.scenario_id, catalog_version: assignment.catalog_version,
    availability: scenario?.availability, required_capabilities: scenario?.required_capabilities,
    provider_support: scenario?.provider_support, verification_boundary: scenario?.verification_boundary,
    evidence_requirements: scenario?.evidence_requirements, source_refs: scenario?.source_refs,
    accepted_source_refs: assignment.source_refs_snapshot,
    catalog_source_sha256: current.scenario_plan?.catalog_source_sha256,
    catalog_sha256: assignment.catalog_sha256, scenario_sha256: assignment.scenario_sha256,
    profile_sha256: assignment.profile_sha256, initial_memory_sha256: assignment.initial_memory_sha256,
    condition_fixture_sha256: assignment.condition_fixture_sha256,
    condition_fixture_snapshot: assignment.condition_fixture_snapshot,
    saved_persona_sha256: assignment.saved_persona_sha256,
    buyer_challenge: challenge, buyer_challenge_sha256: assignment.buyer_challenge_sha256 ?? person.buyer_challenge_sha256,
  })}${canonicalScenario(assignment.scenario_snapshot_json)}</details>` : '';
  const observedVerification = result ? section('Reported journey and scenario verification', `
    <p class="small muted">${result.measured ? 'Measured provider evidence' : 'Fixture or unmeasured evidence'} · ${escape(humanize(result.classification))}</p>
    ${readable({ reported_scenario_id: 'scenario_id' in result ? result.scenario_id : undefined,
      provider: 'provider' in result ? result.provider : undefined,
      finding_ids: 'finding_ids' in result ? result.finding_ids : undefined,
      scenario_exercised: result.exercised,
      payment_verified: result.payment_verified, entitlement_verified: result.entitlement_verified,
      capability_checked: result.capability_checked, capability_available: result.capability_available,
      condition_fixture_verified: result.condition_fixture_verified,
      evidence_event_ids: result.evidence_event_ids,
      ...('journey_outcome' in result ? { journey_outcome: result.journey_outcome, journey_classification: result.journey_classification, scenario_exercise_gate: result.exercise_gate } : {}) })}
    ${'scenario_id' in result && result.scenario_id !== assignment?.scenario_id ? '<p class="small muted">This observation does not identify the saved assigned case. Its journey checks and findings remain inspectable; it adds no exercised coverage for that assignment.</p>' : ''}
    <p class="small muted">These are separate service-reported checks. Only an explicit exercised flag establishes reported case exercise. An expected decline or provider challenge is not an app defect merely because payment did not succeed.</p>
  `) : '';
  return `
    <p class="sample-label">Saved customer #${number(person.index + 1)} · ${current.pass === 'before' ? 'Initial pass' : 'After pass'}</p>
    <div class="person-summary"><img src="/assets/aditi.png" alt=""><div><h2>${escape(person.name)}</h2><p>Representative illustration. Details below come from this saved customer.</p></div></div>
    <p class="small muted persona-identity">Run ${escape(current.id)} · population ${escape(current.population_id)} · customer ${escape(person.id)}</p>
    <div class="persona-inputs">
      <h2>Saved profile and assigned test</h2>
      <p class="small muted">These describe the synthetic customer and test inputs. They do not establish what happened in the browser.</p>
      ${challenge ? section('Buyer challenge', `<p class="hypothetical-label">Hypothetical test input · not proof of exercise or an observed failure</p>${readable({ title: challenge.title, buyer_constraint: challenge.buyer_constraint, check_emphasis: challenge.check_emphasis })}`) : ''}
      ${section('Why this customer and case were selected', assignment?.profile_relevance_rationale ? readable(assignment.profile_relevance_rationale) : unknown)}
      ${choice}
      ${section('Seed source and provenance', renderSeedProvenance(assignment?.seed))}
      ${section('Background and profile', Object.keys(person.profile).length ? readable(person.profile) : unknown)}
      ${section('Payment context', Object.keys(payment).length ? readable(payment) : unknown)}
      ${section('Buyer preferences and constraints', Object.keys(preferences).length ? readable(preferences) : unknown)}
      ${section('Buyer goal', goal === undefined ? unknown : readable(goal))}
      ${section('Hypothetical payment risk', scenario ? `<p class="hypothetical-label">Hypothetical input · not an observed failure</p><p>${escape(scenario.hypothetical_problem)}</p>` : unknown)}
      ${section('Measurable check', scenario ? readable(scenario.expected_behavior) : unknown)}
      ${scenario ? section('Buyer context and constraints', readable(scenario.customer_context)) : ''}
      ${section('Assigned starting conditions', Object.keys(person.conditions).length ? readable(person.conditions) : unknown)}
      ${section('Saved initial memory', person.initial_semantic_memory === undefined ? unknown : readable(person.initial_semantic_memory))}
      ${person.memory.length ? section('Other saved memory records', `<p class="small muted">The service did not identify these as the original initial memory.</p>${readable(person.memory)}`) : ''}
      <details class="record" data-record="original-profile"><summary>Original profile and memory records</summary><pre>${json({ profile: person.profile, conditions: person.conditions, memory: person.memory, initial_semantic_memory: person.initial_semantic_memory, initial_snapshot: person.initial_snapshot })}</pre></details>
      ${savedProvenance}
    </div>
    <p class="same-population">The after pass must restore this original saved profile, scenario, starting conditions and initial memory. New isolated accounts and payment identifiers are separate from the customer identity.</p>
    <div class="persona-observed">
      <h2>Observed in this run</h2>
      ${section('Recorded outcome', `<p>${escape(personaOutcome(events))}</p>${recordedPersonaOutcome(events) === null ? '<p class="small muted">This saved customer has no recorded result. A run blocker or a saved browser action does not establish that the customer passed or failed.</p>' : ''}`)}
      ${observedVerification}
      ${section('Payment evidence', records(events, /^(payment|receipt|checkout|provider|measured|payment_status|payment_id|payment_truth|payment_verified)$/i))}
      ${section('Account access evidence', records(events, /^(entitlement|app_entitlement|entitlement_verified|access|account_id|classification)$/i))}
      ${section('Download and purchased capability evidence', `<p class="small muted">Capability availability alone does not establish a completed download. Inspect the recorded browser source and any saved download evidence.</p>${records(events, /download|^capability(_checked|_available)?$/i)}`)}
      ${section('Recorded steps and decisions', eventList(events))}
      <div class="actions"><button class="secondary" data-action="person-trace" data-id="${escape(person.id)}">Inspect this customer's trace</button><button class="text-button" data-action="people">All saved customers</button></div>
    </div>`;
}
