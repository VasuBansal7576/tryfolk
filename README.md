# Owned Dodo Special Downloads target

This is the owned local sandbox adaptation of [Dodo’s official MIT checkout demo](https://github.com/dodopayments/dodo-checkout-demo/tree/ef7cbd980d8f1e57b036dc2af71ff05521b6175a). It runs the Special Downloads one-time purchase and protected twelve-file download journey. The complete pinned source is preserved in `upstream/` for reference. Only the adapted `app/` is routed. Read [SOURCE.md](SOURCE.md) for license, archive digest and changes.

Configure privately supplied distinct Test Mode keys and an exact one-time product.
Credentials and account-specific evidence are excluded from this repository. The automated
saved-persona Dodo repair/retest cycle and signed provider delivery remain unverified.
Local tests use explicitly labelled external provider fixtures; they are not Dodo receipts.

## Run locally without credentials

Requires Node 22.13 or later. Validation used Node 26.8.2 and npm 11.19.1. The persistent local SQLite adapter uses Node’s built-in `node:sqlite`; it needs no MongoDB service, OAuth, paid image generator or model API.

```sh
cd dodo-target
npm ci --ignore-scripts
npm test
npm run typecheck
npm run build
npm start
```

Open `http://127.0.0.1:3300/special-downloads`. Without configuration the app displays an honest sandbox setup gate. Checkout cannot succeed. `npm run smoke` starts and stops its own built HTTP server on port 3339, verifies login and scoped reset, and proves checkout returns `503 setup_required`. It does not contact Dodo or a model.

For explicit local fixture access, copy `.env.example` to the ignored `.env.local` and privately set a random `TARGET_CONTROL_TOKEN` with at least 32 characters. Generate it directly into the private file or secret store without logging it. Do not commit, echo or put credentials in browser/model prompts. The controller token is used only by the backend fixture/oracle client. Provider secrets remain server-only.

`TARGET_ENV=local` and `TARGET_SANDBOX=true` explicitly enable fixture auth on an exact HTTP loopback origin. `TARGET_ENV=production`, missing sandbox opt-in, or a remote origin disables it. Next’s `NODE_ENV=production` means a production-optimized local build here, not a deployed production environment. The server binds to loopback. No remote deployment is supported or authorized by this target.

## Primary-owned Dodo setup

After primary's credential approval, supply these values in `.env.local` or an approved server secret store. The current dashboard uses granular resource grants. The legacy Enable write access checkbox is not the setup contract. Read [INTEGRATION-CHECKLIST.md](INTEGRATION-CHECKLIST.md) for the SDK call mapping, approved origins and remaining delivery boundaries.

| Variable | Required value |
| --- | --- |
| `DODO_TEST_WRITE_KEY` | Test Mode key: Customers Write + Checkout Sessions Write; all other resources None |
| `DODO_TEST_READ_KEY` | Distinct Test Mode key: Checkout Sessions Read + Payments Read; all others None, no Write grants |
| `DODO_TEST_WEBHOOK_KEY` | Optional genuine test endpoint signing secret, server-only; absent until separately approved. Its absence disables signed webhook handling and produces a readiness warning, while settled-payment reconciliation remains available |
| `DODO_TEST_PRODUCT_ID` | One-time Special Downloads Test Mode product ID, quantity one, no subscriptions/trials |
| `DODO_EXPECTED_AMOUNT_MINOR` | Exact tax-inclusive total in minor units, e.g. 1200 for a fixed USD $12 product |
| `DODO_EXPECTED_CURRENCY` | Configured USD, INR, EUR or GBP product currency |
| `DODO_TEST_KEYS_CONFIRMED` | `true` only after confirming both keys are Test Mode |
| `DODO_READ_KEY_READ_ONLY` | `true` only after confirming verifier has the two Read grants above and no Write grants |
| `TARGET_CONTROL_TOKEN` | Private random controller token, at least 32 characters |
| `TARGET_ORIGIN` | Exact loopback origin matching the chosen local port |
| `TARGET_DATABASE_PATH` | Owned writable persistent local SQLite file, separate for each preview |

Use a fixed tax-inclusive product and test billing country consistent with the expected total. Checkout offers credit/debit card entry only, hides saved methods, forbids short links, and disables editing the generated customer email/name. Currency selection, discount codes and subscriptions are disabled. Any amount/currency mismatch stays a mismatch; the target never adjusts expected values to force success. The app cannot infer key privileges from an opaque token. The two dashboard scope confirmations are explicit owner attestations, not independently verified key permissions.

The server API uses only `https://test.dodopayments.com`, official SDK `environment:'test_mode'`, no endpoint overrides and no followed provider redirects. Firstmate steering 005 explicitly authorizes the [official hosted sandbox origin](https://docs.dodopayments.com/developer-resources/checkout-session#api-response) **`https://test.checkout.dodopayments.com`**. The server, browser guard and manifest now permit only that exact checkout origin. No API-host short link, alternate port, embedded credential, wildcard or live fallback is enabled. SDK logging is explicitly disabled.

Signed delivery to `/api/webhooks/payment` remains separately gated; missing configuration returns 503, never an unsigned fallback. Session-owned settled-payment recovery does not need a new webhook secret. Primary must approve any genuine signed delivery path. The [official CLI](https://docs.dodopayments.com/developer-resources/sdks/cli#listen-for-webhooks) offers an outbound Test Mode relay to loopback. It can create a provider webhook endpoint, needs additional setup scopes, sends its key to `wsserver.dodopayments.tech`, and reserializes JSON before forwarding. Those effects require primary's separate authorization. Its body transformation can invalidate signatures; verification stays enabled and such a failure remains a delivery error. The checklist documents a route with a precreated endpoint and separate limited listener key. Nothing was installed, registered or started here. Local signed fixtures and the CLI's unsigned trigger do not establish genuine payment fulfillment.

Test cards are in the [official testing guide](https://docs.dodopayments.com/miscellaneous/testing-process). Use only documented synthetic card entry in Test Mode. Keep the server-owned `@tryfolk.invalid` customer identity; no inbox, wallet, genuine card, bank/KYC, subscription or paid trial is part of this journey.

## Backend integration and browser journey

The full HTTP contract is [TARGET-CONTRACT.md](TARGET-CONTRACT.md). Register the manifest’s exact origin and revision once, then create each fresh fixture with the backend controller token. Keep token responses private. The login token is opaque, single-use and expires in five minutes. Its URL fragment is removed immediately by the login page, so it is not sent in the HTTP URL or referrer. Sessions are HttpOnly and SameSite=Strict, expire in one hour, and persist only hashed tokens. Fixtures cannot select an existing account or email.

Stagehand opens the per-customer `login_url` in an isolated browser, clicks “Continue as test customer”, visits `/special-downloads`, and clicks “Get Art Pack”. The target creates a fresh server-owned order, synthetic Dodo test customer and checkout each attempt. Its metadata binds run, persona, account, fixture, product, order and target revision. The browser goes to the approved test checkout, then returns to `/checkout/return`. The page reads the current session’s own order list and offers “Confirm purchase” when access is absent. That button calls a separate authenticated app reconciliation command. It never selects an order or payment ID from return query parameters. The confirmation can be retried deliberately if payment is still pending or the provider read fails.

After the browser attempt, the backend reads `/api/target/orders?account_id=...` to obtain exact order/checkout IDs. Read `/api/target/receipts?order_id=...` and `/api/target/entitlements?account_id=...&order_id=...` separately. Compare returned ownership against the journey before classifying an outcome. The provider receipt requires actual `GET /checkouts/{id}` followed by `GET /payments/{payment_id}`, status succeeded on both resources, server-bound customer ID/email, all metadata IDs, one expected product/quantity, amount and currency. Null/processing states remain pending. Errors, failed states and mismatches stay distinct.

Receipt reads append sanitized immutable evidence only; they never change orders, paid status or access. The entitlement oracle reads local app state without any writes. There are two explicit grant paths. The strict signed webhook path verifies the original raw body with the official SDK and current event envelope before independent provider reads. The app recovery command `POST /api/target/reconcile` requires the active session, exact Origin and its own order ID, then independently re-reads the actual checkout and payment. It does not accept browser payment claims. Both paths recheck the active run/account/order/current revision inside an atomic transaction and use a shared immutable fulfillment ledger. Duplicate events, concurrent retries, restarted servers and later delivery cannot grant twice. Bad signatures, wrong identities, inactive/reset orders and unsettled provider state are rejected. Evidence records have SQL guards against update/delete and omit raw customer email, billing/card details, credentials and checkout URLs. The original signed body is retained as a SHA256 digest plus sanitized fields, not as sensitive raw bytes. Runtime API observations are `dodo_test_api`; app recovery fulfillment is `dodo_test_api_reconciliation` with `authenticated_raw_body:false`; actual verified signed events are `dodo_signed_webhook`. Recovery cannot claim signature evidence. Injected external responses keep the explicit `external_provider_fixture` label.

Exercise a file download separately in the browser. The server guards `/api/target/download?artwork=0` with app entitlement; the twelve SVG files are local authored samples that replace the original demo’s external Picsum placeholders. No artwork generation or paid service is involved.

## Healthy source and disclosed broken control

The approved repair seam and fixed local regression/export gates are [REPAIR-CONTRACT.md](REPAIR-CONTRACT.md). Only `lib/fulfillment-policy.ts` is editable by the repair adapter; verification, auth, oracles, tests, configuration and secrets remain protected. A source repair retains the original broken configuration and gets a new source head/revision. `npm run test:repair` is deliberately red on the shipped fault source and must pass after an actual source repair.

Use separate local processes and databases. Configuration is frozen at process startup. Restart after mode/product changes and create fresh accounts and checkout IDs for the new run. The revision hash includes source content, mode, product, amount and currency. The build records its source commit and whether the owned target was clean. A changed runnable source/config after build produces `build_revision_mismatch` and gates payment. Rebuild before using that source.

| Preview | Origin | Mode | Database |
| --- | --- | --- | --- |
| Healthy | `http://127.0.0.1:3300` | `healthy` | `.data/healthy.sqlite` |
| Disclosed fault | `http://127.0.0.1:3301` | `broken` | `.data/broken.sqlite` |
| Changed-source repair preview | `http://127.0.0.1:3302` or registered3310–3399 | Original `broken` configuration, actual changed source | `.data/repaired.sqlite` |

For an alternate preview, supply `TARGET_ORIGIN`, `TARGET_MODE` and `TARGET_DATABASE_PATH` to the process and use `npm start -- --port 3301` or `--port 3302`. Each process needs its private controller/provider configuration. A separately registered repaired preview can use3310–3399 and must retain the original broken mode/product/price. Mode changes are never an HTTP toggle.

The disclosed broken branch of fulfillment records independently settled payment and an immutable `suppressed` event, while withholding the Special Downloads entitlement. Healthy mode grants once. `repaired` is rejected as a mode; a source repair must be a real code change on a new pinned revision. This is a controlled shared-path failure for the configured fixture, not an observed commercial defect. The reconciliation source change repairs the app’s inability to recover a fully settled owned purchase when webhook delivery is delayed or absent. Its public test failed on the prior source and passes on the changed source. This is source repair evidence from labelled provider fixtures; a genuine paid-but-no-access reproduction and same-persona provider retest remain outstanding. The explicit broken suppression is only a disclosed control defect and must not be presented as an organically observed SaaS bug. A mode comparison cannot claim a Codex repair, genuine baseline, Entire checkpoint or same-population retest.

Reset requires explicit run ID and revision and revokes only that run’s accounts/orders. It keeps receipt evidence and the event ledger, and never resets Dodo customers/payments or genuine user accounts. A closed run cannot provision new fixtures. Retest uses a new run with the same saved persona lineage and equivalent fresh starting state. Tryfolk’s backend owns the paired N-persona budget, queue and orchestration.

## Validation and limits

`npm test` covers setup gating, production fixture rejection, single-use login, fresh checkouts, signatures, idempotency, independent oracles, forged return parameters, wrong ownership/product/customer/amount/currency, pending provider states, persistent ledger, isolated reset, restricted domains, separate SDK key roles, session-owned reconciliation, concurrent/restarted/cross-path idempotency, reset races, stale revisions and rejected preconfigured repair flags. External API responses are explicitly test fixtures; no genuine Dodo API call is made by those tests. `npm run smoke` verifies a real built local Next HTTP server without credentials.

The local adapter is single-host SQLite, not a multi-region deployment. Actual permission enforcement, provider transport/schema compatibility, checkout form behavior, genuine settlement and complete payment/access/browser evidence require scoped diagnostics and primary’s CUA checkout. Signed webhook delivery requires separate approved setup. The owner supplied keys/product, but their presence alone is not validation. Readiness reports configuration readiness; `provider_baseline_verified:false` remains explicit. No OAuth login, original credit/subscription routes, Google accounts, paid image generation or remote publication are enabled.
