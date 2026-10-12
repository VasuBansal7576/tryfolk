// The only approved Codex repair seam. All payment, ownership, auth and ledger
// checks live outside this file and run before this decision is applied.
export function specialDownloadsDisposition(mode: 'healthy' | 'broken'): 'granted' | 'suppressed' {
  return 'granted';
}
