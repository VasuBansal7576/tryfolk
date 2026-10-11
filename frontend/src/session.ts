import { z } from 'zod';
import type { Run } from './contracts';

const savedRunSchema = z.object({ id: z.string(), token: z.string(), appUrl: z.string(), count: z.number().int().nonnegative(), pass: z.enum(['before', 'after']), parentRunId: z.string().nullable().optional() });
const savedRunsSchema = z.array(savedRunSchema);
export type SavedRun = z.infer<typeof savedRunSchema>;
const storageKey = 'tryfolk.runs.v1';

export function loadRuns(): SavedRun[] {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(storageKey) || '[]');
    const parsed = savedRunsSchema.safeParse(raw);
    return parsed.success ? parsed.data : [];
  } catch { return []; }
}
export function saveRun(run: Run, token: string, previous: SavedRun[]): { runs: SavedRun[]; persisted: boolean } {
  const entry: SavedRun = { id: run.id, token, appUrl: run.app_url, count: run.persona_count, pass: run.pass, parentRunId: run.parent_run_id };
  const runs = [entry, ...previous.filter(item => item.id !== run.id)].slice(0, 50);
  try { sessionStorage.setItem(storageKey, JSON.stringify(runs)); return { runs, persisted: true }; }
  catch { return { runs, persisted: false }; }
}
