# TryFolk frontend

Small Vite + TypeScript app for the local TryFolk API. All project setup is contained here. The owner-provided design's artwork, palette and self-hosted fonts are preserved. See [asset provenance](ASSET-PROVENANCE.md).

```sh
cd frontend
npm ci
npm run dev
```

Development proxies the API to `http://127.0.0.1:8000`. Set `TRYFOLK_API_PROXY` for another local port. `VITE_API_BASE` optionally sets an API prefix or origin; it contains no credentials. See `.env.example`. The service must allow the frontend origin when serving the API separately.

```sh
npm run typecheck
npm test
npm run build
npm run preview
```

With the owned local backend running, `npm run verify:api` uses its registered target to check a real N=1 quote, combined reserve, idempotent creation, run authorization, guest repair/replay denial, findings retrieval and cancellation. It always cancels the test run to release unused budget. This uses Node 22.18+ TypeScript support. `TRYFOLK_API_URL` and `TRYFOLK_TEST_TARGET` can select another owned loopback runtime. `TRYFOLK_PROOF_PATH` optionally writes a sanitized proof outside the repository. A blocked result proves lifecycle behavior; it does not establish an executed customer outcome.

`npm run verify:quotes` checks actual N=1, N=8 and N=10 quotes without creating a run, model job, repair or payment. It validates additive budget fields when the service supplies them, and keeps old service quotes readable. Use this command during forecast preparation when runtime jobs are not authorized. `npm test` checks backward compatibility, immutable prices/counts, independent caps, protected after allowance, complete boundary validation, pending versus finalized provenance, and microdollar precision. Its invented contract inputs stay inside the test process and are never live app results.

The backend can serve `frontend/dist` at `/`, with API routes registered before its static fallback. The production app uses same-origin API calls by default. `preview` is static-only; use the backend to serve the build for API verification.

The frontend validates incoming JSON with schemas in `src/contracts.ts`. `src/api.ts` owns requests and errors; `src/presentation.ts` escapes text and renders evidence. The app has no fixture or simulated progress mode. Decorative illustrations do not represent actual persona identities or browser evidence.

URL and count lead to a combined initial/after quote. An explicit confirmation creates the run with a stable idempotency key. A gated quote can create a durable blocked run, clearly labeled before confirmation. Active work polls once a second; errors preserve saved results. Expired access and service errors have recovery controls. Every displayed outcome retains the original requested denominator. Retesting sends only the repair ID and reuses the entire saved population.

Each poll starts its run and findings GETs together with the same access context, then applies one update after checking that navigation is still current. Findings failures stay visible without replacing a valid run; authorization failures stop normal polling. When navigation or deliberate request recovery has just received a validated run response, the initial display reuses it and fetches findings rather than requesting that run twice. Later active polls still fetch fresh run data. No outcome, trace, population or accepted budget is synthesized or omitted.

Saved customers open with native buttons into a keyboard-accessible detail drawer. It separates the saved profile, preferences, goal, hypothetical scenario and expected behavior from this selected run's observed steps and independent payment/access/download evidence. Absent fields remain Unknown. Detail and trace selections bind to the current run, population and navigation epoch; before and after may share persona IDs without sharing observations. Background updates preserve focus, scroll and expanded records.

Optional scenario API additions use frozen `scenario_plan`, `personas[].assigned_scenario` and `scenario_coverage` records. The read-only `GET /scenarios?app_url=…` catalog adds clearly hypothetical, unassigned cases to the coverage view; it never enriches historical profiles or supplies outcomes. Missing routes or fields keep old records inspectable. Selection rationale, fixture readiness at selection time and omitted-case reasons come from the service. Assignment, verified conditions, exercised evidence and reported verification remain separate. Profile variety and provider-owned controls do not increase the original N-persona denominator. Inspection introduces no case-selection inputs, new reservations or extra execution.

The implemented core204 records add frozen seed bindings, source attribution/revision/checksums and structured profile/selection rationale. Accepted seed context is separate from the actual generated profile. Historical seed provenance remains Unknown. Initial semantic memory never falls back to later episodic memory. Source-bank size is an input count; assigned cases stay unexercised while the service lacks scenario-specific fixture binding. Actual linked journey payment/access/capability checks remain visible separately. Neither demographics nor source country establishes a supported payment condition. No seed bank or real seed sample is bundled in this frontend.

Run-scoped access tokens are saved in this tab's `sessionStorage` for reloads. They never appear in URLs, reports, logs or source. The owner authorization panel sends a runtime-entered secret to `POST /owner/session` and clears the input immediately. It never persists the secret. The service sets an HttpOnly session cookie; the app keeps its CSRF token only in memory and includes it on mutations. `GET /owner/session` restores capabilities after reload; `DELETE /owner/session` disconnects. Owner-only controls remain unavailable until the session is supported and connected. No remote publication or billing is initiated.

Actual screenshots and trace links are rendered only when supplied by the service. Evidence objects are escaped and credential fields are redacted in the UI and downloaded reports. Missing evidence remains explicitly missing. The report contains recorded results, without a run access token.

Quotes and results prominently label local fixture receipts and non-CFO estimates using the API's source provenance. Fixture receipts never become Dodo verification. Scheduling allowance used is an estimate; recorded incremental cash is a separate counter. Older service records without cash measurement display `Unknown`. The app preserves imported cost-plan links when the service supplies them.

The CFO v2 quote view shows the original before/after bases, overhead, subtotal, explicit contingency, both-pass reservation, protected original-N after allowance, customer quote cap, lifetime ceiling and current consumed/held/remaining ledger. The native Page's source rows, inspection evidence, native ledger snapshot and any stricter scheduler allowance discrepancy are available in the forecast details. A draft Page or account access does not activate a plan. Accepted runs use their own immutable `budget_contract`; historical quotes without v2 fields do not gain invented zero contingency or new limits. The service remains authoritative for acceptance and checks current headroom atomically.

Local fixture findings are labelled as fixture observations and may reach owner repair review after their completed initial run, matching the backend contract. They are not measured Dodo payments. Separate recorded diagnostic controls are rendered outside the original-N adaptive outcome table. The main evidence panel accepts the service's authorized `screenshot` URL; absent screenshots remain absent. Repair/replay still require explicit owner approval, and no recovery or payment is automatically retried.

Development proxy behavior follows the [Vite server documentation](https://vite.dev/config/server-options). JSON parsing follows the [Zod boundary-validation API](https://zod.dev/basics).

Approved initial-run and retest requests are saved in this tab before dispatch. If a response is lost, closing the approval dialog or reloading retains the exact request and idempotency key. Review the pending-request banner to deliberately recover through the original endpoint; reload never resends a job automatically. Run access must be saved before the recovery record is removed. Ambiguous errors cannot dismiss that record; a definitive service decline can be explicitly dismissed. Tab storage must be available before approval. Clearing or closing the entire tab session removes this local recovery capability.

Fresh retest approval requires a completed initial run, all original unique saved profiles, completed repair revision/preview and a retained reservation with no released funds. Known saved after runs disable another retest. The service remains authoritative for reservations and any after run this tab has not observed. Recovering an already approved request uses the original key even when the displayed baseline has subsequently ended.

The hero screenshot belongs to the shown persona, includes event attribution, and fits the entire image into its preview frame. **Inspect full screenshot** opens the complete unchanged image in a scrollable app dialog with an original-size image link. Actual whitespace within an image is preserved. The hero story is explicitly an excerpt; **Read full story** retains all saved steps and evidence. Repair summaries show recorded checkpoint IDs even without a public link.

The app setup view separates a registered app URL, repository/source verification, owner authorization and execution readiness. General `/readiness` results cannot prove source access or a successful journey. Failed refreshes retain visibly stale details and disable selection from them. Missing connection interfaces remain read-only; registration is available only after the owner-only capability response confirms the supported adapter. Exact service blockers/actions are shown in plain product terms. Returning from owner setup restores the saved run or original quote for deliberate review, without launching a job. Run provenance shows recorded source commit/digest separately from the preview revision.

Owner app connections follow the supplied v1 contract. `GET /connections` lists only server-approved local candidates and persisted registrations. The setup panel shows each app’s registered/unregistered status, service-verified repository/source pins, separate preview revision, execution flag and exact remaining gates. `POST /connections` sends only `{app_url}` for a deliberately selected approved app using the existing HttpOnly session and CSRF. This verifies and persists registration; it starts no run, checkout, account or model job. It neither clones arbitrary repositories nor clears payment/adapter checks. A registered-but-blocked response stays blocked. Unsupported/empty capabilities,401/403,404,503, partial payloads and stale reads have explicit states; submission remains unavailable until a fresh compatible capability is reported. A failed registration marks the previous snapshot stale and requires deliberate refresh rather than automatic retry. Logout clears private connection details from memory. No test contract input is bundled as live results.

`src/transport.ts` owns validated requests and error parsing, while `src/connections.ts` owns the connection contract client and state rules. Boundary tests exercise these real client functions with test-only responses because browser/account/payment/model calls are outside this worker’s current authorization. Integrated endpoint and browser acceptance are primary/core checks after the backend feature is supplied and served.
