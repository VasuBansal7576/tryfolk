import { z } from 'zod';
import { quoteSchema } from './contracts.ts';
import type { CreatedRun, Run } from './contracts.ts';

// Approval is recorded before dispatch. Closing a dialog or reloading never replaces its key/body.
const pendingSchema = z.discriminatedUnion('kind', [
  z.object({
    kind: z.literal('create'), key: z.string().min(1), approvedAt: z.string(),
    request: z.object({ quote_id: z.string().min(1) }).strict(), quote: quoteSchema,
  }),
  z.object({
    kind: z.literal('replay'), key: z.string().min(1), approvedAt: z.string(), runId: z.string().min(1),
    request: z.object({ repair_id: z.string().min(1) }).strict(),
    populationId: z.string().min(1), personaCount: z.number().int().positive(), repairRevision: z.string().min(1),
    repairSourceCommit: z.string().min(1).optional(),
  }),
]);
const declinedSchema = z.object({ status: z.number().int(), code: z.string(), message: z.string() });
const journalSchema = z.object({ pending: pendingSchema, declined: declinedSchema.nullable() });
export type PendingRequest = z.infer<typeof pendingSchema>;
type JournalEntry = z.infer<typeof journalSchema>;
type TabStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
const journalKey = 'tryfolk.pending.v1';

function sameRequest(first: PendingRequest, second: PendingRequest): boolean {
  return first.kind === 'create' && second.kind === 'create'
    ? first.request.quote_id === second.request.quote_id
    : first.kind === 'replay' && second.kind === 'replay' && first.runId === second.runId && first.request.repair_id === second.request.repair_id;
}
function definitivelyDeclined(status: number, code: string): boolean {
  return [400, 402, 404, 409, 422].includes(status) && !['idempotency_conflict', 'contract_mismatch'].includes(code);
}

export class RequestJournal {
  private readonly storage: TabStorage;
  constructor(storage: TabStorage) { this.storage = storage; }
  read(): JournalEntry | null {
    let raw: string | null;
    try { raw = this.storage.getItem(journalKey); }
    catch { throw new Error('Tab storage is unavailable. Enable it before approving a run so a lost response can be recovered.'); }
    if (raw === null) return null;
    try { return journalSchema.parse(JSON.parse(raw)); }
    catch { throw new Error('The pending request record could not be read. Preserve this tab and resolve its saved request before starting another run.'); }
  }
  prepare(candidate: PendingRequest): PendingRequest {
    const existing = this.read();
    if (existing) {
      if (!sameRequest(existing.pending, candidate)) throw new Error('Recover or resolve the original pending request before approving another run.');
      return existing.pending;
    }
    const pending = pendingSchema.parse(candidate);
    if (pending.kind === 'create' && pending.quote.id !== pending.request.quote_id) throw new Error('The saved request must use its original approved quote.');
    this.write({ pending, declined: null });
    return pending;
  }
  decline(status: number, code: string, message: string): void {
    const entry = this.read();
    if (entry && definitivelyDeclined(status, code)) this.write({ ...entry, declined: { status, code, message } });
  }
  beginAttempt(key: string): void {
    const entry = this.read();
    if (!entry || entry.pending.key !== key) throw new Error('The original pending request is unavailable.');
    // A previous definitive decline cannot resolve a later response-loss attempt.
    this.write({ ...entry, declined: null });
  }
  dismissDeclined(): void {
    const entry = this.read();
    if (!entry?.declined || !definitivelyDeclined(entry.declined.status, entry.declined.code)) throw new Error('A lost response must be recovered with its original request.');
    this.storage.removeItem(journalKey);
  }
  complete(key: string): void {
    if (this.read()?.pending.key !== key) throw new Error('The pending request changed before its result was saved.');
    this.storage.removeItem(journalKey);
  }
  private write(entry: JournalEntry): void {
    try { this.storage.setItem(journalKey, JSON.stringify(entry)); }
    catch { throw new Error('The request recovery record could not be saved in this tab. Keep this tab open and enable storage before retrying its original request.'); }
  }
}

export async function recoverRequest(
  journal: RequestJournal,
  send: (pending: PendingRequest) => Promise<CreatedRun>,
  saveResult: (result: CreatedRun) => boolean,
): Promise<CreatedRun> {
  const entry = journal.read();
  if (!entry) throw new Error('There is no pending request to recover.');
  const pending = entry.pending;
  journal.beginAttempt(pending.key);
  const result = await send(pending);
  const current = result.status;
  const matches = result.id === current.id && (pending.kind === 'create'
    ? current.pass === 'before' && current.persona_count === pending.quote.persona_count && current.app_url === pending.quote.app_url
    : current.pass === 'after' && current.parent_run_id === pending.runId && current.population_id === pending.populationId && current.persona_count === pending.personaCount && current.revision === pending.repairRevision && (!pending.repairSourceCommit || current.source_commit === pending.repairSourceCommit));
  if (!matches) throw new Error('The returned run does not match the original approved request. Its recovery record has been preserved.');
  // Persist access before deleting recovery information. If access cannot be saved, reload can retry the same key.
  if (saveResult(result)) journal.complete(pending.key);
  return result;
}

export function replayBlocker(current: Run, afterAlreadySaved = false): string | null {
  if (current.pass !== 'before') return 'Retesting starts from the original initial run.';
  if (current.phase !== 'completed') return 'The initial run must be completed before its saved customers can be retested.';
  if (current.budget.released_microusd > 0) return 'The retest reservation was released. This ended run cannot start a retest.';
  if (afterAlreadySaved || current.comparison?.after_run_id) return 'This original population already has an after run. Open its saved results.';
  const n = current.persona_count;
  if (n < 1 || current.counts.requested !== n || current.counts.generated !== n || current.personas.length !== n || new Set(current.personas.map(person => person.id)).size !== n) return 'All original requested profiles must be saved before retesting. The population cannot be replaced or reduced.';
  if (current.budget.unused_microusd <= 0) return 'The original retest allowance is no longer held.';
  if (!current.repair || current.repair.status !== 'completed' || !current.repair.head_revision || !current.repair.preview_url) return 'A completed repair with its exact revision and registered preview is required.';
  if (!repairTargetRevision(current)) return 'The Dodo repair needs its registered preview revision in addition to the source commit.';
  return null;
}

export function repairTargetRevision(current: Run): string | null {
  return current.provider === 'dodo' ? current.repair?.target_revision ?? null : current.repair?.head_revision ?? null;
}
