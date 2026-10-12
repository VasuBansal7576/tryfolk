import { z } from 'zod';

const errorSchema = z.object({ detail: z.union([
  z.string(),
  z.object({ code: z.string(), message: z.string(), gates: z.array(z.object({ message: z.string(), action: z.string() })).optional() }),
  z.array(z.object({ msg: z.string(), loc: z.array(z.union([z.string(), z.number()])) })),
]) });

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(message: string, status: number, code: string) {
    super(message); this.status = status; this.code = code;
  }
}

export function createTransport(base: string, csrf: () => string | null, fetcher: typeof fetch = fetch) {
  return async function request<T>(path: string, schema: z.ZodType<T>, options: { body?: unknown; token?: string; key?: string; method?: string; timeoutMs?: number } = {}): Promise<T> {
    const headers = new Headers({ Accept: 'application/json' });
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    if (options.token) headers.set('Authorization', `Bearer ${options.token}`);
    if (options.key) headers.set('Idempotency-Key', options.key);
    const method = options.method || (options.body !== undefined ? 'POST' : 'GET');
    const csrfToken = csrf();
    if (csrfToken && method !== 'GET') headers.set('X-CSRF-Token', csrfToken);
    let response: Response;
    try {
      response = await fetcher(`${base}${path}`, {
        method,
        headers, credentials: 'include', cache: 'no-store', body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
        signal: AbortSignal.timeout(options.timeoutMs ?? 20_000),
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
  };
}
