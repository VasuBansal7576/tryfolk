import { createdRunSchema, findingsSchema, ownerLogoutSchema, ownerSessionSchema, readinessSchema, repairSchema, runSchema, scenarioCatalogSchema } from './contracts';

import { createTransport } from './transport';
import { createConnectionApi } from './connections';
import { createCheckpointApi } from './checkpoint-access';
import { createBaselineRetryApi } from './baseline-retry';
import { createLaunchApi } from './launch';
export { ApiError } from './transport';

const base = String(import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
let csrfToken: string | null = null;
const request = createTransport(base, () => csrfToken);

export const api = {
  ...createConnectionApi(request),
  ...createCheckpointApi(request),
  ...createBaselineRetryApi(request),
  ...createLaunchApi(request),
  owner: async () => { const session = await request('/owner/session', ownerSessionSchema); csrfToken = session.csrf_token; return session; },
  connectOwner: async (secret: string) => { const session = await request('/owner/session', ownerSessionSchema, { body: { secret } }); csrfToken = session.csrf_token; return session; },
  disconnectOwner: async () => { const session = await request('/owner/session', ownerLogoutSchema, { method: 'DELETE' }); csrfToken = null; return session; },
  readiness: () => request('/readiness', readinessSchema),
  scenarios: (app_url: string) => request(`/scenarios?${new URLSearchParams({ app_url })}`, scenarioCatalogSchema),
  createRun: (quote_id: string, key: string) => request('/runs', createdRunSchema, { body: { quote_id }, key }),
  run: (id: string, token: string) => request(`/runs/${encodeURIComponent(id)}`, runSchema, { token }),
  findings: (id: string, token: string) => request(`/runs/${encodeURIComponent(id)}/findings`, findingsSchema, { token }),
  cancel: (id: string, token: string) => request(`/runs/${encodeURIComponent(id)}/cancel`, runSchema, { body: {}, token }),
  repair: (id: string, finding_id: string) => request(`/runs/${encodeURIComponent(id)}/repairs`, repairSchema, { body: { finding_id } }),
  recoverRepair: (id: string) => request(`/runs/${encodeURIComponent(id)}/repairs/recover`, repairSchema, { body: {} }),
  replay: (id: string, repair_id: string, key: string) => request(`/runs/${encodeURIComponent(id)}/replay`, createdRunSchema, { body: { repair_id }, key }),
};
