import { z } from 'zod';
import { createdRunSchema, findingsSchema, ownerLogoutSchema, ownerSessionSchema, quoteSchema, readinessSchema, repairSchema, runSchema } from './contracts';

const base = String(import.meta.env.VITE_API_BASE || '').replace(/\/$/, '');
let csrfToken: string | null = null;
const errorSchema = z.object({ detail: z.union([
  z.string(),
  z.object({ code: z.string(), message: z.string(), gates: z.array(z.object({ message: z.string(), action: z.string() })).optional() }),
  z.array(z.object({ msg: z.string(), loc: z.array(z.union([z.string(), z.number()])) })),
]) });

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: string) { super(message); }
}

async function request<T>(path: string, schema: z.ZodType<T>, options: { body?: unknown; token?: string; key?: string; method?: string } = {}): Promise<T> {
  const headers = new Headers({ Accept: 'application/json' });
  if (options.body !== undefined) headers.set('Content-Type', 'application/json');
  if (options.token) headers.set('Authorization', `Bearer ${options.token}`);
  if (options.key) headers.set('Idempotency-Key', options.key);
  const method = options.method || (options.body !== undefined ? 'POST' : 'GET');
  if (csrfToken && method !== 'GET') headers.set('X-CSRF-Token', csrfToken);
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      method,
      headers, credentials: 'include', body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: AbortSignal.timeout(20_000),
    });
  } catch {
    throw new ApiError('The local service could not be reached. Check that it is running, then try again. Saved results remain available.', 0, 'unreachable');
  }
  const raw: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = errorSchema.safeParse(raw);
    let message = `The service returned an error (${response.status}).`;
    let code = 'request_failed';
    if (error.success) {
      const detail = error.data.detail;
      if (typeof detail === 'string') message = detail;
      else if (Array.isArray(detail)) message = detail.map(item => `${item.loc.join('.')}: ${item.msg}`).join(' ');
      else {
        message = detail.message;
        code = detail.code;
        if (detail.gates?.length) message += ' ' + detail.gates.map(gate => `${gate.message} ${gate.action}`).join(' ');
      }
    }
    throw new ApiError(message, response.status, code);
  }
  const parsed = schema.safeParse(raw);
  if (!parsed.success) throw new ApiError('The service response does not match the app contract. Refresh after the local service and frontend are updated together.', response.status, 'contract_mismatch');
  return parsed.data;
}

export const api = {
  owner: async () => { const session = await request('/owner/session', ownerSessionSchema); csrfToken = session.csrf_token; return session; },
  connectOwner: async (secret: string) => { const session = await request('/owner/session', ownerSessionSchema, { body: { secret } }); csrfToken = session.csrf_token; return session; },
  disconnectOwner: async () => { const session = await request('/owner/session', ownerLogoutSchema, { method: 'DELETE' }); csrfToken = null; return session; },
  readiness: () => request('/readiness', readinessSchema),
  quote: (app_url: string, persona_count: number) => request('/quotes', quoteSchema, { body: { app_url, persona_count } }),
  createRun: (quote_id: string, key: string) => request('/runs', createdRunSchema, { body: { quote_id }, key }),
  run: (id: string, token: string) => request(`/runs/${encodeURIComponent(id)}`, runSchema, { token }),
  findings: (id: string, token: string) => request(`/runs/${encodeURIComponent(id)}/findings`, findingsSchema, { token }),
  cancel: (id: string, token: string) => request(`/runs/${encodeURIComponent(id)}/cancel`, runSchema, { body: {}, token }),
  repair: (id: string, finding_id: string) => request(`/runs/${encodeURIComponent(id)}/repairs`, repairSchema, { body: { finding_id } }),
  recoverRepair: (id: string) => request(`/runs/${encodeURIComponent(id)}/repairs/recover`, repairSchema, { body: {} }),
  replay: (id: string, repair_id: string, key: string) => request(`/runs/${encodeURIComponent(id)}/replay`, createdRunSchema, { body: { repair_id }, key }),
};
