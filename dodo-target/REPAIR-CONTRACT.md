# Fulfillment source repair contract

The sole approved editable file is **`lib/fulfillment-policy.ts`**. Its exported `specialDownloadsDisposition(mode)` receives only the existing healthy/broken configuration and returns `granted` or `suppressed`. It controls the final Special Downloads entitlement decision after independent settlement and ownership checks. It has no credentials, customer data, provider transport, HTTP input or database handle. Both signed webhook fulfillment and session-owned reconciliation call this seam inside the existing atomic transaction. The manifest reports `repair.source_file`, its effective `disposition`, and a fault only while the decision actually suppresses fulfillment.

The shipped source deliberately suppresses in `TARGET_MODE=broken`. A repair must change this source on a new commit while keeping **the original broken mode, product, amount and currency unchanged**. Merely starting healthy mode, adding a repaired mode, changing an oracle/test or accepting an unpaid provider response is not a repair. Fresh isolated accounts/orders/databases are required after a revision change; never migrate an existing pending/paid order into the new source revision.

The backend owns source copying and its isolated Git repository under its authorized `runtime/repairs/<id>/source`. This package does not create or administer Git worktrees, preview processes or repair orchestration. From a clean committed target, capture the original contract outside the editable source:

```sh
npm run --silent repair:contract > /private/owned-evidence/original-source-contract.json
```

The JSON includes the exact source head/digest, one-file allowlist, each export file's SHA256, the seam hash, protected-file digest and fixed regression commands. Copy only `export_files`, preserving relative paths and the MIT license/provenance. They are tracked files and omit upstream reference material, credentials, all `.env*`, databases, generated builds/manifests, dependency installations and caches. Reinitialize only the backend-owned copied source Git repository, commit its initial source, and capture its own initial contract there. Preserve the original target manifest/head plus this copied source's initial head as distinct provenance. Install dependencies with `npm ci --ignore-scripts`; do not copy installed dependencies or private configuration into model-visible source.

Keep the original contract, protected-file digests, quote and saved population outside Codex's editable directory. The repair adapter must enforce the one-file allowlist. Config, provider verification, auth/session/ownership code, SQL store/ledgers, read-only oracles, browser origins, card-only customer protections, tests, scripts, package/lock and documentation are all protected. The candidate must be committed and clean before this gate:

```sh
npm run --silent repair:contract -- --verify /private/owned-evidence/original-source-contract.json
npm run test:repair
npm run typecheck
npm run build
npm run smoke
```

Verification rejects a dirty/unchanged source, altered allowlist/commands and any protected file addition, removal or content change. It requires a changed seam and source head. The owner supplies the immutable original contract; this digest comparison does not authenticate a substituted baseline. The backend retains control of the gate and actual process configuration. Secrets remain in its approved server secret store outside the copied model-visible source; do not broaden model/tool access to them.

`npm test` characterizes the shipped baseline, including two explicitly named `fault control:` tests. It passes on this source. **`npm run test:repair` is intentionally red on the disclosed broken source**: two fixed purchase/access regressions fail while the unpaid guard and all other safety tests pass. It omits only the two immutable fault-characterization tests, which assert the original deliberate suppression. It never changes a test according to a candidate result. After an actual seam repair, the same command must be fully green: matched paid reconciliation and verified signed settlement grant once, protected download succeeds, and pending payment/return claims still fail. These tests use labelled external provider fixtures; red/green here is local coding evidence, never genuine Dodo settlement or a persona retest.

Build and start only after the candidate passes. Primary/backend privately configure the same original product/price/currency/mode and a new persistent local database/controller capability. A separately registered loopback origin on ports3310–3399 may host the changed-source preview; the process owner starts/stops only its own process. Read its actual readiness/manifest and pin `source_commit`, `source_sha256`, `target_revision`, original `mode`, effective `repair.disposition`, exact origin and product. A changed source after build is gated. A clean source head and target revision are different identifiers; retain both. Runtime receipt/reconciliation/oracle/download behavior is unchanged from [TARGET-CONTRACT.md](TARGET-CONTRACT.md).

Use a fresh diagnostic account and saved supported action lineage for known-failure replay before the original saved N-persona population. Diagnostic controls belong to their explicitly labelled run and must not become hidden population denominators. Independently read actual Dodo checkout then payment, compare all ownership/price identifiers, separately read app entitlement and exercise download. API reconciliation evidence stays `dodo_test_api_reconciliation`; signed event evidence requires actual authenticated raw provider delivery. No fixture, changed mode or local test substitutes for this provider evidence or an Entire checkpoint. Primary/backend own budget approval, checkpoint capture, browser actions and paired population replay.
