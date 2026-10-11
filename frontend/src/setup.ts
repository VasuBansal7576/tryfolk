import type { Readiness } from './contracts.ts';

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
    appStatus: !readiness ? 'Unknown' : readiness.target_url ? 'Registered URL' : 'Not registered',
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
