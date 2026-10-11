// Test-only inputs. Never imported by the shipped app or used as live results.
import { runSchema } from '../src/contracts.ts';
export const person = {
  id: 'test-only-person', index: 0, name: 'Test-only customer',
  profile: { occupation: 'Test-only artist', preferences: ['Test-only preference'], goal: 'Test-only goal' },
  conditions: { device: 'desktop', locale: 'en-US' }, memory: [],
  initial_semantic_memory: [{ type: 'information', content: 'Test-only original memory' }],
};
export const savedRun = input => runSchema.parse({
  id: 'test-only-before', app_url: 'http://127.0.0.1:3101', persona_count: 1,
  population_id: 'test-only-population', parent_run_id: null, pass: 'before', phase: 'running',
  revision: 'test-only-source', fault: 'test-only', preview_url: 'http://127.0.0.1:3101',
  created_at: '2020-01-01', updated_at: '2020-01-01',
  counts: { requested: 1, generated: 1, queued: 1, executed: 0, completed: 0, failed: 0, abandoned: 0, pending: 0, blocked: 0 },
  budget: { reserved_microusd: 120000, spent_microusd: 0, unused_microusd: 120000, released_microusd: 0, cost_basis: 'estimate' },
  gates: [], events: [], comparison: null, repair: null, personas: [person], ...input,
});
