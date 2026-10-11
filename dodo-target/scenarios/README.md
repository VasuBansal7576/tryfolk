# Payment scenario catalog

[payment-catalog.json](payment-catalog.json) is the backend-owned input contract for 24 hypothetical payment cases. It contains customer context, concrete problem and goal, expected behavior, risk, condition fixture, target/provider requirements, official support references and observable verification limits. Every entry is `not_exercised`. This file is data, not an executor, simulator or run result.

The current owned target supports one-time credit/debit checkout for Special Downloads. Its current driver uses one public successful test card, US billing, desktop Chromium and `en-US`. Two catalog entries can use that implementation after all readiness and run-authority gates pass. Other entries disclose missing fixtures, unsupported target operations or provider-owned controls. Neither seed text nor a story about a locale/device creates an executable condition.

Core can read this JSON without a new service. Keep the catalog version and selected entry bytes in persistence; UI renders those backend fields and actual saved persona details. Users still supply the target URL and N. Do not require users to design profiles, conditions or a second planner.

## Select and freeze

Select cases from actual registered target capabilities and conditions first. Prefer relevant risks such as settled payment without access and incorrect account binding. Save a plain selection reason with each assignment. Use useful purchase context and constraints when pairing a profile; do not infer fraud, payment method, wealth or reliability from demographics.

Before quote acceptance, reject assignments whose required capability or fixture is unavailable. Quote both fixed N-persona passes upfront. Acceptance freezes the selected scenario, profile, initial memory, condition and quote snapshots with their hashes. The after pass restores the exact saved scenario/profile/initial memory/conditions and uses fresh isolated account, cookie, order and checkout identities. Pin before/after source manifests separately. Never regenerate a profile, inherit before-pass memory, expand N or update an accepted quote from a newer catalog.

Catalog IDs are stable; `catalog_version` identifies changed input content. Store exact UTF-8 snapshot bytes and SHA-256 together. A later catalog release can affect a new quote, not an accepted one.

## Report only exercised coverage

Keep assigned, attempted and independently verified case IDs separate. Selected N covers selected actually exercised cases; it does not prove all 24 entries, all methods or all available seeds. Merchant lifecycle/event checks and provider-owned controls have separate denominators from browser personas. Pending, setup and provider-read errors remain explicit. An expected decline is not an app defect because payment did not succeed.

Provider payment evidence requires actual independent checkout then payment reads with succeeded status, matching ownership identifiers and exact amount/currency. Read-only app entitlement and actual protected file evidence establish access separately. A return page, email match, signed example, mock receipt or fixture-labelled unit payload never substitutes for real provider evidence. The current target applies only authenticated, independently verified `payment.succeeded` events; other authenticated event types are ignored. A webhook acknowledgment does not prove a refund, subscription or dispute policy was applied.

Official provider documentation was checked on 2026-10-11. SDK support is grounded in the installed official `dodopayments` 2.54.2 declarations, with package/resource/symbol references in the catalog. Support must be rechecked for future quotes. The current payment-method guide marks PayPal unavailable for new transactions even though checkout parameters include it. PCI-related entries check owned-app card-data handling only; no certification, tax-law or fraud-scoring claim is made.

## Verify locally

From the repository root, with the existing target dependencies installed:

```sh
dodo-target/node_modules/.bin/tsx --test dodo-target/tests/scenario-catalog.test.ts
```

This focused catalog check needs no credentials, browser, provider, model or running target. The catalog and test live outside the target source fingerprint; they change no checkout/fulfillment behavior or current run controller. Catalog presence authorizes no external operation.
