import type { Readiness } from './contracts.ts';
import { connectionSnapshot, executionReportedReady, registrationAvailable, sourceVerified } from './connections.ts';
import type { ConnectionState } from './connections.ts';
import { escape, gates, humanize, link } from './presentation.ts';

const serviceLabels: Record<string, string> = {
  browser_use: 'Product discovery', tinytroupe: 'Saved customer decisions',
  stagehand: 'Browser journeys', dodo: 'Payment verification',
  cfo: 'Cost forecast', entire: 'Repair history', neatlogs: 'Execution traces',
};

// Readiness describes service setup. Only a target-specific quote can admit a run.
export function readinessFacts(readiness: Readiness | null, error: string) {
  const freshness = error ? readiness ? 'stale' : 'unavailable' : readiness ? 'current' : 'loading';
  return {
    freshness,
    appStatus: !readiness ? 'Unknown' : readiness.target_url ? 'Service-reported app URL' : 'Not registered',
    appUrl: readiness?.target_url ?? null,
    canSelectApp: freshness === 'current' && !!readiness?.target_url,
    sourceStatus: 'Not reported',
    sourceDetail: 'The general setup check does not report repository ownership or a verified source revision. An app URL and owner session do not establish source access.',
    services: Object.entries(readiness?.integrations ?? {}).map(([name, item]) => ({
      name, label: serviceLabels[name] ?? name.replaceAll('_', ' '), ...item,
    })),
    gates: readiness?.gates ?? [],
  };
}

export function renderConnections(state: ConnectionState, selectedUrl: string, busy: boolean): string {
  const snapshot = connectionSnapshot(state);
  const available = registrationAvailable(state);
  const status = state.kind === 'guest' || state.kind === 'unsupported' || state.kind === 'unavailable'
    ? `<p class="${state.kind === 'unavailable' ? 'status-bad' : 'muted'}" role="status">${escape(state.message)}</p>`
    : state.kind === 'loading' ? '<p class="muted" role="status">Checking approved apps and source…</p>' : '';
  const stale = snapshot && state.kind !== 'ready' ? '<p class="setup-stale">Last reported connections · refresh before registering or selecting an app.</p>' : '';
  const targets = snapshot?.targets.map(target => `<article class="connection-target"><div class="connection-heading"><h3>${escape(target.app_url)}</h3><span>${target.registration_status === 'registered' ? 'Registered' : 'Awaiting owner registration'}</span></div><dl class="connection-facts"><div><dt>Repository</dt><dd><strong>${escape(target.source.repository_label || 'Not reported')}</strong>${link(target.source.repository_url, 'Licensed source repository')}</dd></div><div><dt>Source verification</dt><dd><strong>${sourceVerified(target) ? 'Verified source pins' : target.source.status === 'verified' ? 'Verification incomplete' : escape(humanize(target.source.status))}</strong><span>${escape(target.source.detail)}</span></dd></div><div><dt>Execution</dt><dd><strong>${executionReportedReady(target) ? 'Service reports execution ready' : 'Execution blocked'}</strong><span>Source verification describes this build. It is not proof of a completed payment or customer journey. Budget review checks this app before launch.</span></dd></div></dl>${gates(target.gates)}<details class="connection-pins"><summary>Recorded source and build</summary><div class="evidence-row"><strong>Source commit</strong><span>${escape(target.source.commit || 'Not reported')}</span></div><div class="evidence-row"><strong>Source digest</strong><span>${escape(target.source.digest || 'Not reported')}</span></div><div class="evidence-row"><strong>Target revision</strong><span>${escape(target.revision || 'Not reported')}</span></div></details>${state.kind === 'ready' && target.registration_status === 'registered' && sourceVerified(target) ? `<button class="secondary" data-action="use-connection" data-id="${escape(target.id)}" ${busy ? 'disabled' : ''}>Use this app URL</button>` : ''}</article>`).join('') ?? '';
  const selected = snapshot?.targets.find(target => target.app_url === selectedUrl);
  const form = available && snapshot?.targets.length ? `<form id="connection-form" class="connection-form"><label class="label" for="connection-url">Approved app</label><select id="connection-url" class="field" name="app_url" required ${busy ? 'disabled' : ''}>${snapshot.targets.map(target => `<option value="${escape(target.app_url)}" ${target.app_url === selectedUrl ? 'selected' : ''}>${escape(target.app_url)} · ${target.registration_status === 'registered' ? 'registered' : 'candidate'}</option>`).join('')}</select><p class="small muted">Verify the pinned licensed source and matching approved app build. This records the connection; it starts no run, checkout or repair and does not clear payment or browser prerequisites.</p><button class="primary" type="submit" ${busy ? 'disabled' : ''}>${busy ? 'Checking app…' : selected?.registration_status === 'registered' ? 'Recheck registered source' : 'Verify & register app'}</button></form>` : snapshot && state.kind === 'ready' ? `<p class="small muted">${!snapshot.targets.length ? 'No approved compatible apps were reported. Prepare the licensed demo source and matching local build, approve its URL on the service, and configure its sandbox payment product and adapter checks. Then refresh setup.' : 'Registration capability for this supported adapter was not reported. Connection details are read-only.'}</p>` : '';
  return `<section class="setup-connections"><h2>App and source connection</h2><p class="small muted">This connection supports the owned, licensed local payment demo in Test Mode. General customer apps and private repositories need an ownership, source-access and adapter connection that this service does not provide yet.</p>${status}${stale}${form}${targets}</section>`;
}
