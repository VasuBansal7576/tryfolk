import { repairSchema } from './contracts.ts';
import type { Repair, Run } from './contracts.ts';
import { escape, evidence, link, timestamp } from './presentation.ts';
import { ApiError } from './transport.ts';
import type { createTransport } from './transport.ts';

export function canVerifyCheckpoint(current: Run, owner: boolean): boolean {
  const repair = current.repair;
  return owner && current.provider === 'dodo' && current.pass === 'before' && current.phase === 'completed'
    && repair?.status === 'completed' && !!repair.checkpoint_id && !!repair.head_revision
    && !repair.checkpoint_access;
}

const checkpointErrors: Record<string, string> = {
  checkpoint_not_registered: 'Complete a genuine registered repair before verifying its checkpoint.',
  checkpoint_capture_unavailable: 'This repair has no authenticated capture binding. A legacy checkpoint cannot be backfilled here.',
  checkpoint_capture_mismatch: 'The saved capture does not match the registered repair. Inspect its source and checkpoint evidence.',
  checkpoint_repository_unapproved: 'The local service needs an owner-approved public repository configuration.',
  checkpoint_public_access_unverified: 'Anonymous checkpoint reads could not be verified. Review the approved publication and access prerequisites.',
  checkpoint_public_bytes_mismatch: 'Public resource bytes differ from the saved capture. This checkpoint remains unverified.',
  checkpoint_capture_changed: 'The registered source changed during verification. Refresh the saved repair before continuing.',
  checkpoint_evidence_unavailable: 'The service could not retain its private access receipt. This checkpoint remains unverified.',
};

export function createCheckpointApi(request: ReturnType<typeof createTransport>) {
  return {
    verifyCheckpointAccess: async (runId: string, original: Repair): Promise<Repair> => {
      let result: Repair;
      try {
        // Server owns repository/capture selection. Anonymous reads have a 25s aggregate deadline.
        result = await request(`/runs/${encodeURIComponent(runId)}/repairs/${encodeURIComponent(original.id)}/checkpoint-access`, repairSchema, { body: {}, timeoutMs: 35_000 });
      } catch (value) {
        if (value instanceof ApiError && checkpointErrors[value.code]) {
          throw new ApiError(`${checkpointErrors[value.code]} (${value.code})`, value.status, value.code);
        }
        throw value;
      }
      if (result.id !== original.id || result.status !== original.status || result.base_revision !== original.base_revision
        || result.head_revision !== original.head_revision
        || result.checkpoint_id !== original.checkpoint_id || result.preview_url !== original.preview_url
        || result.target_revision !== original.target_revision || result.source_digest !== original.source_digest
        || !result.checkpoint_access) {
        throw new ApiError('The access response differs from the selected registered repair. Refresh its saved evidence.', 200, 'checkpoint_response_mismatch');
      }
      return result;
    },
  };
}

export function renderCheckpointAccess(repair: Repair): string {
  const access = repair.checkpoint_access;
  const checkpoint = access
    ? `<h3>Public Git checkpoint</h3>${link(access.url, `Public Git checkpoint · ${access.checkpoint_id}`)}<p class="small muted">Service-verified anonymous reads at ${escape(timestamp(access.verified_at))}. This is a historical receipt; current availability has not been rechecked.</p><div class="evidence-links">${link(access.transcript_url, access.privacy_export ? 'Verified privacy-filtered transcript' : 'Verified raw transcript')}${link(access.source_url, 'Verified repair source')}</div>${access.privacy_export ? `<p class="small muted">This is a disclosed privacy-filtered copy of the genuine session. The private original is preserved; account identifiers and financial metadata were removed. The repair prompt, actions, tool results and source commit are retained.</p>${link(access.privacy_export_url, 'Verified privacy export disclosure')}` : ''}<details class="record" data-record="checkpoint-access"><summary>Saved access receipt</summary>${evidence({ repository: access.repository, checkpoint_id: access.checkpoint_id, checkpoint_ref: access.checkpoint_ref, checkpoint_object: access.checkpoint_object, source_commit: access.source_commit, binding_sha256: access.binding_sha256, receipt_sha256: access.receipt_sha256, resource_digests: access.resource_digests, privacy_export: access.privacy_export })}</details>`
    : `<p class="small muted">${repair.checkpoint_id ? 'Saved checkpoint · public Git access unverified.' : 'Checkpoint evidence has not been reported.'}</p>${link(repair.checkpoint_url, 'Saved checkpoint link · access unverified')}`;
  return `<section class="checkpoint-access">${checkpoint}<h3>Hosted Entire UI</h3>${repair.entire_viewer_url ? `${link(repair.entire_viewer_url, 'Open actual Entire repair session')}<p class="small muted">Recorded public Entire page for this exact repair commit. This link is separate from the service's public Git access receipt.</p>` : '<p class="small muted">Unverified. Public Git readback does not verify hosted Entire UI access or login.</p>'}<p class="small muted">Checkpoint access does not establish a successful payment, customer access or retest.</p></section>`;
}
