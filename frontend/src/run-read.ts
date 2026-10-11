import type { api } from './api';
import type { Finding, Run } from './contracts';

type FindingsRead = { kind: 'ready'; findings: Finding[] } | { kind: 'unavailable'; error: unknown };

// Both GETs use the same access context. A findings failure must not replace the run error
// or create an unhandled rejection while the run request is still pending.
export function readRunEvidence(client: Pick<typeof api, 'run' | 'findings'>, options: { id: string; token: string; initial?: Run }): {
  run: Promise<Run>;
  findings: Promise<FindingsRead>;
} {
  if (options.initial && options.initial.id !== options.id) throw new Error('The received run does not match this saved run. Refresh its recorded results.');
  const run = options.initial ? Promise.resolve(options.initial) : client.run(options.id, options.token);
  const findings = client.findings(options.id, options.token).then<FindingsRead, FindingsRead>(
    result => ({ kind: 'ready', findings: result.findings }),
    error => ({ kind: 'unavailable', error }),
  );
  return { run, findings };
}
