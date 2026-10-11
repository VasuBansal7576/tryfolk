import type { Run, Scenario, ScenarioCatalog, ScenarioResult } from './contracts';
import { escape, humanize, number } from './presentation.ts';
import { readable } from './persona-detail.ts';

export type CatalogState = { kind: 'loading' } | { kind: 'ready'; catalog: ScenarioCatalog } | { kind: 'unavailable'; message: string };

function support(scenario: Scenario): string {
  switch (scenario.availability) {
    case 'runnable': return 'Available to attempt · readiness required';
    case 'requires_fixture': return 'Needs a condition fixture';
    case 'provider_owned': return 'Provider-owned';
    case 'unsupported_by_target': return 'Unsupported by this target';
  }
}

export function scenarioExercised(result: ScenarioResult): boolean {
  return result.exercised === true;
}

function resultCopy(result: ScenarioResult, matchesAssignment: boolean): string {
  const classification = humanize(result.classification);
  const exercise = result.exercised === undefined ? 'Unknown · exercise not reported'
    : !matchesAssignment ? 'Not exercised for this assignment'
    : scenarioExercised(result) ? 'Exercised · service reported'
    : 'Not exercised';
  const journey = 'journey_outcome' in result ? ` · ${result.journey_outcome ? `journey ${result.journey_outcome}` : 'no journey outcome'}` : '';
  const gate = 'exercise_gate' in result && result.exercise_gate ? ` · ${result.exercise_gate}` : '';
  return `${exercise}${journey} · ${classification}${gate}`;
}

function verification(result: ScenarioResult): string {
  const checked = (value: boolean | null) => value === null ? 'Unknown' : value ? 'Verified' : 'Not verified';
  return `<p>${result.measured ? 'Measured' : 'Fixture / unmeasured'} · service-reported checks</p><p>Payment: ${checked(result.payment_verified)}</p><p>Access: ${checked(result.entitlement_verified)}</p><p>Capability: ${!result.capability_checked ? 'Not checked' : result.capability_available === null ? 'Unknown' : result.capability_available ? 'Available' : 'Unavailable'}</p>${readable({ provider: 'provider' in result ? result.provider : undefined, finding_ids: 'finding_ids' in result ? result.finding_ids : undefined, evidence_event_ids: result.evidence_event_ids })}`;
}

export function renderSelectionRationale(plan: Run['scenario_plan']): string {
  const rationale = plan?.selection_rationale;
  if (!rationale) return '<p class="small muted">Selection rationale: Unknown · not reported for this saved plan.</p>';
  if ('selected_case_ids' in rationale) return `<details class="record selection-rationale" data-record="selection-rationale"><summary>Why these cases were selected · ${escape(rationale.mode === 'automatic_capability_risk' ? 'Automatic selection' : 'Owner-supplied cases')}</summary><p class="small muted">Saved policy ${escape(rationale.policy_version)} · ${number(plan.requested_personas)} requested customers. ${number(rationale.library_seed_count)} source seeds are context inputs, not tested customers or case coverage.</p>${rationale.why_selected.map(item => `<article class="persona-section" data-focus-scope="rationale-${escape(item.scenario_id)}"><h3>${escape(item.scenario_id)} · ${number(item.assignment_count)} assignments</h3>${readable({ risk_priority: item.risk_priority, capability_basis: item.capability_basis, relevant_customer_context: item.context })}</article>`).join('')}<h3>Cases left outside the selected N</h3>${rationale.omitted_cases.length ? `<ul class="saved-values">${rationale.omitted_cases.map(item => `<li><strong>${escape(item.scenario_id)}</strong> · ${escape(humanize(item.reason))}<details class="record" data-record="omitted-${escape(item.scenario_id)}"><summary>Required capabilities</summary>${readable(item.required_capabilities)}</details></li>`).join('')}</ul>` : '<p class="small muted">No omitted-case reasons were recorded. This does not establish whole-catalog exercise.</p>'}<p class="small muted">Profile variety does not add case coverage. Fixture readiness at selection time is not proof of execution.</p></details>`;
  return `<details class="record selection-rationale" data-record="selection-rationale"><summary>Why these cases were selected · ${escape(rationale.mode === 'automatic_capability_risk' ? 'Automatic selection' : 'Owner-supplied cases')}</summary><p class="small muted">Saved policy ${escape(rationale.policy_version)} · ${number(rationale.requested_personas)} requested customers. Profile variety does not add case coverage.</p>${rationale.why_selected.map(item => `<article class="persona-section"><h3>${escape(item.scenario_id)} · ${number(item.assignment_count)} assignments</h3><p>${escape(item.risk_basis)}</p>${readable({ capability_basis: item.capability_basis, relevant_customer_context: item.relevant_customer_context, fixture_readiness_at_selection: item.required_fixture_ready ? 'Reported ready · not proof of execution' : 'Not ready' })}</article>`).join('')}<h3>Cases left outside the selected N</h3>${rationale.omitted_cases.length ? `<ul class="saved-values">${rationale.omitted_cases.map(item => `<li><strong>${escape(item.scenario_id)}</strong> · ${escape(humanize(item.reason))}<details class="record"><summary>Required capabilities</summary>${readable(item.required_capabilities)}</details></li>`).join('')}</ul>` : '<p class="small muted">No omitted-case reasons were recorded. This does not establish whole-catalog exercise.</p>'}</details>`;
}

export function renderScenarioCoverage(current: Run, state: CatalogState): string {
  // Frozen assignments take precedence over the current catalog. The catalog never
  // enriches an old persona or supplies an observation to either pass.
  const frozen = [...(current.scenario_plan?.assignments ?? []), ...current.personas.flatMap(person => person.assigned_scenario ? [person.assigned_scenario] : []), ...(current.scenario_coverage?.cases.flatMap(item => item.assigned_scenario ? [item.assigned_scenario] : []) ?? [])];
  const rows: { scenario: Scenario; frozen: boolean }[] = [];
  for (const assignment of frozen) if (!rows.some(row => row.scenario.id === assignment.scenario_id)) rows.push({ scenario: assignment.scenario_snapshot, frozen: true });
  if (state.kind === 'ready') for (const scenario of state.catalog.scenarios) if (!rows.some(row => row.scenario.id === scenario.id)) rows.push({ scenario, frozen: false });
  const coverage = current.scenario_coverage;
  const completeScope = coverage && coverage.requested_personas === current.persona_count && coverage.counting_basis === 'actual_saved_persona_journeys';
  const assignmentFor = (person: Run['personas'][number]) => person.assigned_scenario ?? (completeScope ? coverage.cases.find(item => item.persona_id === person.id)?.assigned_scenario : null);
  const matches = (result: ScenarioResult, scenarioId: string) => 'scenario_id' in result && result.scenario_id === scenarioId;
  const unexercised = completeScope ? rows.filter(row => !coverage.cases.some(item => current.personas.some(person => person.id === item.persona_id && assignmentFor(person)?.scenario_id === row.scenario.id) && item.observed_scenario_result && matches(item.observed_scenario_result, row.scenario.id) && scenarioExercised(item.observed_scenario_result))) : null;
  const notice = state.kind === 'ready'
    ? `Source catalog ${escape(state.catalog.catalog_version)} · ${number(state.catalog.scenarios.length)} hypothetical cases. Saved assignments below retain their original catalog snapshot.`
    : state.kind === 'loading' ? 'Loading the read-only scenario catalog. This starts no customer journey.' : escape(state.message);
  return `<section class="coverage-section"><h2>Which cases did this crowd cover?</h2><p>Only the original ${number(current.persona_count)} requested customers belong to this run. A catalog case or assignment is not a completed test.</p><p class="small muted">${notice}</p>${renderSelectionRationale(current.scenario_plan)}${!completeScope ? '<p class="small muted">Per-scenario coverage was not reported for this saved run. Existing steps remain inspectable; no category coverage is inferred from them.</p>' : ''}<p class="coverage-boundary">Support, assignment, exercise and verification are separate. Browser journeys do not certify tax or payment compliance, fraud protection or dispute handling. Merchant events and provider controls stay outside the customer denominator.</p>${rows.length ? `<div class="table-scroll"><table class="report-table coverage-table"><caption>Scenario scope for this selected ${current.pass === 'before' ? 'initial' : 'after'} run</caption><thead><tr><th scope="col">Case and category</th><th scope="col">Support scope</th><th scope="col">Assigned</th><th scope="col">Exercised</th><th scope="col">Verified evidence</th></tr></thead><tbody>${rows.map(row => {
    const scenario = row.scenario;
    const assigned = current.personas.filter(person => assignmentFor(person)?.scenario_id === scenario.id);
    const planned = current.scenario_plan?.assignments.filter(item => item.scenario_id === scenario.id).length ?? 0;
    const cases = completeScope ? coverage.cases.filter(item => assigned.some(person => person.id === item.persona_id) && item.observed_scenario_result) : [];
    return `<tr><th scope="row"><strong>${escape(scenario.title)}</strong><small>${escape(humanize(scenario.area))} · ${escape(humanize(scenario.check_kind))}</small><details class="record" data-record="case-${escape(scenario.id)}"><summary>${row.frozen ? 'Saved hypothetical assignment' : 'Hypothetical catalog case · not tested'}</summary><p class="hypothetical-label">Hypothetical problem · not an observed failure</p><p>${escape(scenario.hypothetical_problem)}</p><h3>Expected behavior</h3>${readable(scenario.expected_behavior)}<h3>Evidence needed and limits</h3>${readable({ customer_context: scenario.customer_context, required_capabilities: scenario.required_capabilities, provider_support: scenario.provider_support, evidence_requirements: scenario.evidence_requirements, verification_boundary: scenario.verification_boundary, source_refs: scenario.source_refs })}</details></th><td>${escape(support(scenario))}${scenario.selection_ready === false ? `<p>${escape(scenario.selection_gate ?? 'Not ready for selection')}</p>` : ''}</td><td>${assigned.length ? assigned.map(person => `<button class="text-button" data-action="person" data-id="${escape(person.id)}">${escape(person.name)}</button>`).join('') : planned ? `${number(planned)} planned · no saved profile yet` : 'Not assigned'}</td><td>${cases.length ? cases.map(item => item.observed_scenario_result ? `<p>${escape(resultCopy(item.observed_scenario_result, matches(item.observed_scenario_result, scenario.id)))}</p>` : '').join('') : completeScope ? 'Not exercised' : 'Unknown · not reported'}</td><td>${cases.length ? cases.map(item => {
      const result = item.observed_scenario_result;
      if (!result) return '';
      if (matches(result, scenario.id) && scenarioExercised(result)) return verification(result);
      const linked = result.evidence_event_ids.some(id => current.events.some(event => event.id === id && event.persona_id === item.persona_id));
      return `<p>Journey checks only · ${result.condition_fixture_verified ? 'no reported exercise for this assigned case' : 'scenario conditions not verified'}</p>${!linked ? '<p class="small muted">The reported evidence event is not present in this run view.</p>' : ''}${verification(result)}`;
    }).join('') : 'Unknown · no verified result'}</td></tr>`;
  }).join('')}</tbody></table></div>` : '<p class="empty">No scenario catalog or frozen assignment has been supplied. No cases are invented here.</p>'}${unexercised?.length ? `<details class="record coverage-gaps" data-record="coverage-gaps"><summary>${number(unexercised.length)} cases without recorded exercised coverage in this run</summary><p>The service has not reported these assigned cases exercised by the saved customers in this selected run. Assigned or attempted cases can still be unverified; provider-owned cases need separate controls.</p><ul class="saved-values">${unexercised.map(row => `<li>${escape(row.scenario.title)} · ${escape(humanize(row.scenario.area))}</li>`).join('')}</ul></details>` : ''}<p class="same-population">Retesting keeps these saved assignments and all ${number(current.persona_count)} original profiles and initial memories. It does not expand the crowd to cover the catalog.</p></section>`;
}
