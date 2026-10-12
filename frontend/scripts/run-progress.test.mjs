import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { runSchema } from '../src/contracts.ts';
import { replayBlocker } from '../src/operations.ts';
import { personaOutcome, renderPersonaDetail, selectedPersona } from '../src/persona-detail.ts';
import { eventList } from '../src/presentation.ts';
import { customerStatus, phaseCopy, recordedPersonaOutcome, recordedProgress, renderRunProgress } from '../src/run-progress.ts';
import { person, savedRun } from './persona-inputs.mjs';

globalThis.window = { location: { origin: 'http://127.0.0.1:5173' } };
const event = (id, kind, data = {}, personaId = person.id) => ({
  id, kind, data, persona_id: personaId, created_at: '2020-01-01', trace_url: null,
});

test('progress counts saved browser activity independently from recorded outcomes and original N', () => {
  const run = savedRun({
    persona_count: 3,
    counts: { requested: 3, generated: 1, queued: 2, executed: 0, completed: 0, failed: 0, abandoned: 0, pending: 0, blocked: 0 },
    events: [event(1, 'discovery', {}, null), event(2, 'observation'),
      event(3, 'decision', { outcome: 'completed' }), event(4, 'browser_action', { success: false }),
      event(5, 'browser_action', {}, 'test-only-unsaved'), event(6, 'observation', {}, null)],
  });
  const before = structuredClone(run);
  assert.deepEqual(recordedProgress(run), {
    savedProfiles: 1, discoveryRecords: 1, customersWithBrowserActivity: 1,
    observationRecords: 1, decisionRecords: 1, browserActionRecords: 1,
    recordedOutcomes: 0, withoutRecordedOutcome: 3, latestEvent: run.events[5],
  });
  assert.equal(customerStatus(run.personas[0], run), 'No outcome recorded · browser steps saved');
  assert.deepEqual(run, before);
});

test('action, decision and run gates cannot supply a customer verdict', () => {
  const run = savedRun({ phase: 'blocked', gates: [{ code: 'test-only', message: 'Test-only gate', action: 'Review test-only evidence' }],
    events: [event(1, 'decision', { outcome: 'completed' }), event(2, 'browser_action', { outcome: 'failed' }),
      event(3, 'verification', { outcome: 'test-only-unrecognized' })],
  });
  assert.equal(recordedPersonaOutcome(run.events), null);
  assert.equal(personaOutcome(run.events), 'Unknown · no outcome recorded');
  assert.equal(customerStatus(run.personas[0], run), 'No outcome recorded · browser steps saved');
  assert.match(phaseCopy(run), /stopped at a reported blocker/);
  assert.match(phaseCopy(savedRun({ phase: 'running' })), /Recorded steps and outcomes/);
  assert.equal(customerStatus(person, savedRun()), 'No outcome recorded · profile saved');
});

test('explicit verification outcomes and legacy customer terminal records remain readable without promoting later actions', () => {
  for (const [value, label] of [['completed', 'Completed'], ['failed', 'Failed'], ['abandoned', 'Abandoned'], ['pending', 'Pending'], ['blocked', 'Blocked']]) {
    const recorded = [event(1, 'verification', { outcome: value }), event(2, 'browser_action', { outcome: 'completed' })];
    assert.equal(recordedPersonaOutcome(recorded), label);
    assert.equal(recordedPersonaOutcome([event(1, value)]), label);
  }
  const run = savedRun({ phase: 'completed', events: [event(1, 'verification', { outcome: 'pending' }, 'test-only-other')] });
  assert.equal(customerStatus(person, run), 'No outcome recorded · profile saved');
});

const privateInput = process.env.TRYFOLK_PRIVATE_BASELINE_DTO;
test('actual sealed234 projection preserves activity, Unknown outcomes, persona inspection and retest prerequisites', {
  skip: privateInput ? false : 'Set TRYFOLK_PRIVATE_BASELINE_DTO to the private artifact-derived input; no real seed data is bundled.',
}, async () => {
  const input = JSON.parse(await readFile(privateInput, 'utf8'));
  assert.equal(input.schema, 'tryfolk.ui039.sealed234-input.v1');
  assert.match(input.label, /PRIVATE OFFLINE ARTIFACT-DERIVED UI TEST INPUT/);
  assert.equal(input.backend_source_commit, '64370d570b6e7f2a3980d0897c81e769d7b469a6');
  assert.equal(input.sealed_result_sha256, '446765916ea8119a9ba82328444040b1d9829f9f0d8cdecd42a988128e98c0cb');
  assert.equal(input.projection.population_validation_error, null);
  const run = runSchema.parse(input.run);
  const original = structuredClone(run);
  assert.equal(run.phase, 'blocked');
  assert.equal(run.persona_count, 1);
  assert.equal(run.personas.length, 1);
  assert.equal(run.counts.generated, 1);
  assert.equal(run.counts.executed, 0);
  assert.equal(run.counts.blocked, 1);
  assert.deepEqual(input.projection.counts_raw_persisted_outcomes, { completed: 0, failed: 0, abandoned: 0, pending: 0, blocked: 0 });
  assert.equal(run.events.length, 18);
  const progress = recordedProgress(run);
  assert.deepEqual({ ...progress, latestEvent: undefined }, {
    savedProfiles: 1, discoveryRecords: 1, customersWithBrowserActivity: 1,
    observationRecords: 5, decisionRecords: 5, browserActionRecords: 4,
    recordedOutcomes: 0, withoutRecordedOutcome: 1, latestEvent: undefined,
  });
  assert.equal(progress.latestEvent.id, 143);
  assert.equal(progress.latestEvent.kind, 'persona_memory');
  assert.equal(customerStatus(run.personas[0], run), 'No outcome recorded · browser steps saved');
  assert.deepEqual(input.findings, []);
  assert.deepEqual(run.scenario_coverage.exercised_case_ids, []);
  assert.equal(run.scenario_coverage.cases[0].observed_scenario_result.exercised, false);
  assert.equal(run.scenario_coverage.cases[0].observed_scenario_result.classification, 'not_exercised');

  // Exercise the actual detail-selection boundary without using an alternate browser controller.
  const selection = { runId: run.id, populationId: run.population_id, epoch: 7, personaId: run.personas[0].id };
  const selected = selectedPersona({ run, viewRunId: run.id, epoch: 7, selection });
  assert.equal(selected, run.personas[0]);
  const detail = renderPersonaDetail(run, selected);
  assert.match(detail, /Hypothetical input · not an observed failure/);
  assert.match(detail, /Unknown · no outcome recorded/);
  assert.match(detail, /Only an explicit exercised flag establishes reported case exercise/);
  const selectedEvents = run.events.filter(item => item.persona_id === selected.id);
  assert.equal(selectedEvents.length, 17);
  assert.equal(personaOutcome(selectedEvents), 'Unknown · no outcome recorded');
  assert.equal(selectedPersona({ run, viewRunId: run.id, epoch: 8, selection }), null);
  const after = runSchema.parse({ ...run, id: 'test-only-after-navigation', pass: 'after', parent_run_id: run.id });
  assert.equal(selectedPersona({ run: after, viewRunId: after.id, epoch: 7, selection }), null);

  const progressHtml = renderRunProgress(run);
  assert.match(progressHtml, /customers have no recorded outcome/);
  assert.match(progressHtml, /separate from a recorded customer result/);
  assert.match(progressHtml, /Previously executed checkout field no longer verifies/);
  assert.match(eventList(selectedEvents), /Open full trace/);
  assert.equal(replayBlocker(run), 'The initial run must be completed before its saved customers can be retested.');
  assert.equal(run.repair, null);
  assert.equal(run.comparison, null);
  assert.deepEqual(run, original);
});
