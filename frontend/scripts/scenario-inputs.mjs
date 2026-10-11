// TEST ONLY invented scenario and seed inputs. No provider or model execution.
import { person, savedRun } from './persona-inputs.mjs';

export const scenario = {
  id: 'test-only-core204', area: 'test-only-payment', title: 'TEST ONLY saved case',
  customer_context: { goal_context: 'TEST ONLY artwork purchase' }, hypothetical_problem: 'TEST ONLY hypothetical access gap',
  customer_goal: 'TEST ONLY purchase goal', expected_behavior: ['TEST ONLY fulfilled'],
  check_kind: 'customer_journey', availability: 'runnable', exercise_status: 'not_exercised',
  required_capabilities: { target: ['TEST ONLY download'], provider: ['TEST ONLY payment'], conditions: { billing_country: 'TEST ONLY explicit fixture country' } },
  source_refs: [], evidence_requirements: [], verification_boundary: { scope: 'TEST ONLY condition binding' }, expected_outcome: 'fulfilled', risk_priority: 3,
};
export const seed = {
  id: 'test-only-seed', bank_version: 'test-only-bank-v1', bank_sha256: 'test-only-bank-hash', record_sha256: 'test-only-record-hash',
  record_snapshot: { id: 'test-only-seed', profile: { arts: 'TEST ONLY seed art context' }, source_record_id: 'test-only-row',
    supported_context: { billing_country: ['TEST ONLY fixture eligibility'] }, eligibility: { adult: true, commercial_use: true, quality_passed: true },
    source_row_sha256: 'test-only-source-row-hash', normalized_sha256: 'test-only-normalized-hash',
    selection_rationale: 'TEST ONLY seed relevance reason', changes: ['TEST ONLY normalization'], provider_conditions_established: false },
  source: { dataset: 'TEST ONLY synthetic dataset', url: 'https://example.invalid/test-only-dataset', revision: 'test-only-source-revision', license: 'CC-BY-4.0', attribution: 'TEST ONLY attribution' },
};
export const assignment = { persona_index: 0, catalog_version: 'test-only-catalog-v1', catalog_sha256: 'test-only-catalog-hash',
  scenario_id: scenario.id, scenario_sha256: 'test-only-case-hash', scenario_snapshot: scenario,
  scenario_snapshot_json: JSON.stringify(scenario), source_refs_snapshot: [{ source: 'TEST ONLY accepted source reference' }],
  condition_fixture_snapshot: { billing_country: 'TEST ONLY explicit fixture country' }, condition_fixture_sha256: 'test-only-condition-hash', condition_fixture_verified: false, seed,
  saved_persona_sha256: 'test-only-saved-person-hash', profile_sha256: 'test-only-profile-hash', initial_memory_sha256: 'test-only-memory-hash',
  profile_relevance_rationale: { basis: 'constrained_TinyTroupe_generation', customer_goal: 'TEST ONLY generated purchase goal', customer_context: { relevance: 'TEST ONLY constrained profile context' }, seed_id: seed.id, measured_relevance: false } };
export const rationale = { mode: 'automatic_capability_risk', policy_version: 'supported-risk-seed-v1', selected_case_ids: [scenario.id],
  omitted_cases: [{ scenario_id: 'test-only-omitted', reason: 'requires_fixture' }], why_selected: [{ scenario_id: scenario.id,
    risk_priority: 3, capability_basis: scenario.required_capabilities, context: scenario.customer_context, assignment_count: 1 }],
  library_seed_count: 2001, personality_variation_is_case_coverage: false, whole_catalog_coverage_claimed: false };
export const observed = { classification: 'not_exercised', condition_fixture_verified: false, journey_outcome: 'failed', journey_classification: 'paid_without_access',
  measured: false, payment_verified: true, entitlement_verified: false, capability_checked: true, capability_available: false, evidence_event_ids: [1], exercise_gate: 'TEST ONLY scenario fixture binding not implemented' };
export const makeRun = (extra = {}) => savedRun({ personas: [{ ...person, profile: { goal: 'TEST ONLY actual generated profile goal' }, assigned_scenario: assignment }],
  scenario_plan: { catalog_version: 'test-only-catalog-v1', catalog_sha256: 'test-only-catalog-hash', catalog_source_sha256: 'test-only-catalog-source-hash', catalog_case_count: 2, requested_personas: 1,
    assignments: [assignment], does_not_imply_exercised_coverage: true, selection_rationale: rationale },
  scenario_coverage: { catalog_case_count: 2, requested_personas: 1, assigned_case_ids: [scenario.id], exercised_case_ids: [],
    counting_basis: 'actual_saved_persona_journeys', whole_catalog_coverage_claimed: false,
    cases: [{ persona_id: person.id, assigned_scenario: assignment, observed_scenario_result: observed }] },
  events: [{ id: 1, persona_id: person.id, kind: 'verification', created_at: '2020-01-01', data: { outcome: 'failed', payment_verified: true, entitlement_verified: false }, trace_url: null }], ...extra });
