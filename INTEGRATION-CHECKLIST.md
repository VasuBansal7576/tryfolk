# Sandbox integration checklist

Source review: 11 October 2026. Firstmate reports the owned TryFolk account profile and Test Mode dashboard are prepared. Firstmate steering 005–006 supplied approved distinct granular Test Mode keys and genuine UI-created product `pdt_0NpSJqlzKkRpa8IfLD3gU`, TryFolk Sandbox Art Pack, USD 100 minor units, tax-inclusive, one-time Digital products. No payment or webhook secret was supplied. Exact hosted checkout authorization is now given; relay/new grants remain unauthorized. This checklist establishes the calls the code needs; it does not claim provider permission enforcement, a working relay or a genuine payment baseline.

## App keys: minimal call mapping

The installed official TypeScript SDK is `dodopayments@2.54.2`. The target's `lib/provider.ts` uses exactly these resource operations:

| Server credential | SDK operation | Actual HTTP call | Prepared granular grant |
| --- | --- | --- | --- |
| `DODO_TEST_WRITE_KEY` | `customers.create` | `POST /customers` | Customers Write |
| `DODO_TEST_WRITE_KEY` | `checkoutSessions.create` | `POST /checkouts` | Checkout Sessions Write |
| Distinct `DODO_TEST_READ_KEY` | `checkoutSessions.retrieve` | `GET /checkouts/{id}` | Checkout Sessions Read |
| Distinct `DODO_TEST_READ_KEY` | `payments.retrieve` | `GET /payments/{payment_id}` | Payments Read |
| `DODO_TEST_WEBHOOK_KEY` | `webhooks.unwrap` | None: local Standard Webhooks signature verification, then JSON parsing | Signing secret only; no Webhooks API grant |

Key 1 can use Firstmate's prepared name, `TryFolk local sandbox checkout`. Keep the other resource categories None. Key 2 needs only the two Read grants and no Write grants. The target does not list products/customers, call `POST /payments`, register webhooks, fetch signing secrets or read Dodo entitlements. The product ID is owner configuration; the entitlement oracle reads this app's SQLite state.

The call mapping supports the proposed scope split. Whether Dodo additionally enforces dependencies between granular grants remains unverified until the scoped genuine SDK diagnostics and primary checkout exercise succeed. Do not broaden grants silently on a 401/403. Preserve the error and request scope review through Firstmate. The [API introduction](https://docs.dodopayments.com/api-reference/introduction#authentication) still describes a legacy write checkbox; Firstmate's observed granular dashboard controls take precedence for this setup. Local transport tests alone do not validate actual privileges. Consult the private report for genuine diagnostic results; no extra resource grant is requested implicitly.

Installed source: `node_modules/dodopayments/src/resources/customers/customers.ts` lines 75–81, `checkout-sessions.ts` lines 18–37, `payments.ts` lines 46–53, and `webhooks/webhooks.ts` lines 114–125. Official source repository: [dodopayments-typescript](https://github.com/dodopayments/dodopayments-typescript). The existing SDK transport test proves key selection and endpoint paths with labelled external response fixtures only.

## Exact authorized origins and remaining delivery boundaries

| Purpose | Exact origin | Current state |
| --- | --- | --- |
| Server API requests | `https://test.dodopayments.com` | Allowed; fixed SDK `test_mode`, no overrides or redirects |
| Hosted browser checkout | `https://test.checkout.dodopayments.com` | Explicitly authorized by Firstmate steering 005; sole hosted origin allowed |
| Healthy app and return | `http://127.0.0.1:3300` | Allowed loopback only |
| Broken/changed-source repair previews | `http://127.0.0.1:3301`, `http://127.0.0.1:3302` or registered3310–3399 | Separate process/database; repair retains original broken mode/product/price |
| Optional CLI relay | `https://wsserver.dodopayments.tech/`, `wss://wsserver.dodopayments.tech/connect` | Official CLI source; separate relay/key disclosure and provider endpoint authorization required |

The [official session guide](https://docs.dodopayments.com/developer-resources/checkout-session#api-response) returns `https://test.checkout.dodopayments.com/session/{session_id}`. The exact hosted origin is now authorized and implemented in both the server and browser guard; register the resulting manifest with the backend. The server API origin stays unchanged. Any other checkout origin fails `502 provider_checkout_origin_forbidden`; it is an origin error, not a paid-without-access diagnosis. No wildcard, live domain, custom checkout domain, short link or relay override is approved. The browser's genuine form resources and any further navigation domains have not been observed; do not expand navigation permissions speculatively.

## Local webhook delivery with least exposure

The [official CLI documentation](https://docs.dodopayments.com/developer-resources/sdks/cli#listen-for-webhooks) provides the following direct command for a future approved Test Mode session:

```sh
dodo wh listen http://127.0.0.1:3300/api/webhooks/payment
```

This is a candidate outbound relay to a loopback server. It does not require publishing this app or opening an inbound tunnel. Nothing was installed, logged in, registered or started in this review. Do not launch the CLI TUI or AI commands; ordinary text in the TUI invokes its model assistant.

Review pinned official CLI source at [92416c7b706c36348b0edf43bb2f202788be41d9](https://github.com/dodopayments/dodopayments-cli/tree/92416c7b706c36348b0edf43bb2f202788be41d9), package version 3.1.0:

| CLI step | Source behavior | Permission or boundary |
| --- | --- | --- |
| Login | [`src/commands/login/index.ts`](https://github.com/dodopayments/dodopayments-cli/blob/92416c7b706c36348b0edf43bb2f202788be41d9/src/commands/login/index.ts) verifies via `products.list({page_size:1})` | Products Read; neither app key has it |
| Listener setup | [`src/commands/webhook/listen.ts`](https://github.com/dodopayments/dodopayments-cli/blob/92416c7b706c36348b0edf43bb2f202788be41d9/src/commands/webhook/listen.ts) lists the first 100 webhook endpoints | Webhooks Read |
| Missing relay endpoint | Creates a provider endpoint at `https://wsserver.dodopayments.tech/`, without an event filter | Webhooks Write and account mutation; can receive unrelated events |
| Relay connection | Sends its API key as `api-key` to the relay over WSS | Explicit credential disclosure to the official relay; not authorized; no relay connection is made |
| Local forwarding | Preserves headers, sends `JSON.stringify(data.payload)` to loopback, reports local response to relay | Body is reserialized; byte changes can invalidate the original signature |

Least scope candidate: primary precreates a **Test Mode** relay endpoint restricted to `payment.succeeded` in the dashboard, privately supplies that endpoint's signing secret as `DODO_TEST_WEBHOOK_KEY`, and uses a separate temporary listener key with Products Read + Webhooks Read only, all other categories None. Source inspection predicts no endpoint write if that exact URL appears in the returned list. This is not provider-validated; fail rather than grant Webhooks Write automatically if it does not work. Never reuse or broaden the app checkout/verifier keys. The CLI does not fetch or print the signing secret in this path; primary must supply the correct relay endpoint secret privately. Do not expose keys as literal command arguments, logs or process captures. Primary owns secure authentication/storage outside this worktree.

The relay server source/authorization checks were not inspected. Its acceptance of a restricted listener key, event delivery, and exact body preservation remain unverified. Leave `DODO_WH_TEST_SERVER_URL` unset. Subscribe only to the necessary event; an empty selection means all events in the [webhook setup guide](https://docs.dodopayments.com/developer-resources/webhooks#configuring-subscribed-events).

Keep SDK raw-body signature verification enabled. If reserialization yields `401 invalid_signature`, report a delivery failure and resolve a byte-preserving transport through Firstmate. Do not use `unsafeUnwrap`, re-sign a provider event locally, or label CLI trigger output genuine. `dodo wh trigger` is unsigned mock generation. A dashboard example can prove signed transport but has sample IDs; it does not prove this order was paid. No alternative tunnel/deployment is authorized here.

## Configure before the first genuine attempt

1. The owner must approve and privately supply distinct Test Mode keys with the granular grants above. Private `.env.local` is mode0600 and ignored by Git; no values appear in logs or client code. Scope flags record owner review, not provider enforcement.
2. Configure a one-time Test Mode product with an exact tax-inclusive amount/currency and quantity one. No trial/subscription or remote entitlement integration. Its genuine ID/expected total are configured server-side. `billing_currency` alone cannot force a currency when adaptive pricing is disabled; configure product/billing conditions consistently and preserve mismatches. [Create Checkout Session API](https://docs.dodopayments.com/api-reference/checkout-sessions/create#body-billing-currency)
3. Exact hosted sandbox origin is authorized. Signed provider delivery is separately unconfigured and returns 503. Leave its secret empty; no fake signing key, new CLI key, relay disclosure, tunnel or publication. Checkout and session-owned reconciliation require existing keys/product/controller, not a webhook secret. Readiness exposes `webhook_ready:false` and a warning.
4. Use the ignored `.env.local` or approved secret store for provider values and a private random controller token. Select an explicit local sandbox, loopback origin, mode and separate persistent database. Rebuild/restart after source or configuration changes and read the exact manifest.
5. Backend pins that manifest, creates a new run/persona-owned fixture, and gives the single-use login URL only to its isolated test browser. Keep the generated `@tryfolk.invalid` customer identity; do not substitute a genuine email or customer. No email login or receipt inbox is needed. Test Mode can send actual emails, so avoid deliverable addresses. [Test Mode boundaries](https://docs.dodopayments.com/miscellaneous/test-mode-vs-live-mode#emails-in-test-mode)

## Synthetic card entry and acceptance evidence

Only after setup and primary's test-session authorization, use the [official card guide](https://docs.dodopayments.com/miscellaneous/testing-process#test-cards):

| Scenario | US Visa test number | Expiry | CVV |
| --- | --- | --- | --- |
| Success | `4242424242424242` | `06/32` | `123` |
| Generic decline | `4000000000000002` | `06/32` | `123` |
| Insufficient funds | `4000000000009995` | `06/32` | `123` |

Use synthetic US billing details consistent with the configured product total for this US-card example. Each deliberate attempt gets a fresh order/customer/checkout, not a reused link. Accounts are fresh per run/persona fixture; retries retain that fixture's account ownership. Choose manual card entry only; no wallet, real card, bank, subscription or trial flow. This review made no payment attempt.

For the first healthy baseline, retain all of these separately:

1. Exact manifest and fixture/run/persona/account/product/revision ownership, fresh checkout and server-created customer IDs.
2. Actual read-key `GET /checkouts/{id}` followed by `GET /payments/{payment_id}`. Both status checks must succeed; match customer ID/email, metadata, checkout/payment linkage, cart quantity one, exact amount/currency and no subscription. Keep `pending`, `failed`, `mismatch`, and `provider_error` distinct. [Checkout read API](https://docs.dodopayments.com/api-reference/checkout-sessions/get-checkouts), [payment read API](https://docs.dodopayments.com/api-reference/payments/get-payments-1)
3. Explicit app fulfillment: click “Confirm purchase” on the return page, which uses an order from its authenticated app response and calls `POST /api/target/reconcile`. Fresh matched provider success must precede an atomic active-run/current-revision/account/order grant with immutable `dodo_test_api_reconciliation` evidence and `authenticated_raw_body:false`. Receipt/entitlement GETs cannot cause it. Signature bypass is never involved. Genuine provider-generated signed `payment.succeeded` delivery is a separate, still unverified acceptance gate; neither recovery nor a signed sample may be presented as that event.
4. Separate read-only app entitlement result for that account/order: actual aggregate `has_access`, order-specific grant count and payment ID. Read oracles and return GET/query parameters cannot grant; only the explicitly separate session mutation can recover access.
5. An authenticated successful protected-file download as capability evidence. A receipt alone does not prove access.

Repeat on the disclosed broken and exact repaired manifests using the same saved persona lineage and fresh run-owned identities. A broken-mode comparison is a configured fault demonstration. There is no `repaired` mode; real changed source and its exact revision are required. The added API recovery fixes the prior app’s missing recovery path for delayed/absent webhooks, with fixture-labelled failing/passing tests. Genuine paid-no-access reproduction, Codex/Entire evidence and same-persona provider replay must be captured separately. Backend owns both upfront N-persona budgets. This checklist adds no orchestration product or simulated provider success.


## Approved source repair boundary

Only `lib/fulfillment-policy.ts` is editable by the Codex repair adapter. Capture the clean machine source contract with `npm run --silent repair:contract`, export only its tracked file list into the backend-owned isolated source repository, and preserve its protected-file digests outside the editable source. Run `repair:contract -- --verify <original-contract.json>`, `test:repair`, typecheck, build and the credential-free HTTP smoke on the committed candidate. The baseline repair suite has two expected failures; a real source change must make it green without edits to tests, provider reads, auth, oracles, config, card-only identity or secrets. Keep original broken mode/product/amount/currency. See [REPAIR-CONTRACT.md](REPAIR-CONTRACT.md) for exact commands and source-head versus target-revision provenance.

Healthy CUA control: the backend creates a fixture with its private controller token outside browser/model prompts, opens only the single-use login_url in an isolated session, clicks Continue as test customer, opens Special Downloads, clicks Get Art Pack, and observes the genuine hosted Test Mode checkout. Keep the generated non-deliverable identity and use only documented synthetic card fields. Card-only configuration hides saved methods and disallows email/name editing, currency/discount changes and short links. Do not solve CAPTCHA automatically or move to a real card, bank, wallet, live host, OAuth, inbox or extra scope. A challenge/unsupported field is an explicit journey blocker. On return, Confirm purchase executes the authenticated reconciliation command; receipt and entitlement reads remain separate. Exercise the protected download and retain exact manifest/run/persona/account/order/checkout/payment/product identities. The controller capability, transient login token/cookie and provider keys must never appear in traces or model prompts.
