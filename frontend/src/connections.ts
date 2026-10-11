import { connectionSchema, connectionsSchema } from './contracts.ts';
import type { Connection, Connections } from './contracts.ts';
import type { createTransport } from './transport.ts';

export type ConnectionState =
  | { kind: 'guest'; message: string }
  | { kind: 'loading'; previous: Connections | null }
  | { kind: 'ready'; data: Connections }
  | { kind: 'unavailable'; message: string; previous: Connections | null }
  | { kind: 'unsupported'; message: string };

export function connectionSnapshot(state: ConnectionState): Connections | null {
  switch (state.kind) {
    case 'ready': return state.data;
    case 'loading': case 'unavailable': return state.previous;
    case 'guest': case 'unsupported': return null;
  }
}

export function registrationAvailable(state: ConnectionState): boolean {
  return state.kind === 'ready' && state.data.registration_supported
    && state.data.ownership_scope === 'owner-approved-loopback-dodo-test'
    && state.data.supported_adapters.includes('owned-dodo-special-downloads-v1');
}

export function sourceVerified(connection: Connection): boolean {
  return connection.source.status === 'verified'
    && /^[a-f0-9]{40}$/.test(connection.source.commit ?? '')
    && /^[a-f0-9]{64}$/.test(connection.source.digest ?? '')
    && !!connection.revision;
}

export function executionReportedReady(connection: Connection): boolean {
  return connection.registration_status === 'registered' && sourceVerified(connection)
    && connection.execution_ready && connection.gates.length === 0;
}

export function registrationMessage(connection: Connection): string {
  if (connection.registration_status !== 'registered') return 'Registration is incomplete. Review the app prerequisites.';
  if (!sourceVerified(connection)) return 'App registration is recorded, but its source is not verified. Execution remains blocked.';
  return executionReportedReady(connection)
    ? 'App and source registered. The service reports execution ready; budget approval is still required.'
    : 'App and source registered. Execution remains blocked; review the remaining prerequisites.';
}

export function applyRegistration(data: Connections, appUrl: string, result: Connection): Connections {
  if (result.app_url !== appUrl || !data.targets.some(target => target.app_url === appUrl)) {
    throw new Error('The returned connection does not match the selected approved app. Refresh setup before continuing.');
  }
  return { ...data, targets: data.targets.map(target => target.app_url === appUrl ? result : target) };
}

export function createConnectionApi(request: ReturnType<typeof createTransport>) {
  return {
    connections: () => request('/connections', connectionsSchema),
    registerConnection: (app_url: string) => request('/connections', connectionSchema, { body: { app_url } }),
  };
}
