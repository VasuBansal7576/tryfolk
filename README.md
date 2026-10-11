# TryFolk

Local synthetic checkout journeys with saved TinyTroupe personas, Browser Use discovery,
Stagehand execution, independent payment/access checks, Codex repairs and matched retests.
Warm paper, ink, teal and the accepted illustrated crowd are preserved in `frontend/`.

## Run locally

Python 3.12, uv, Node 22.13+, Chrome/Chromium, an existing authenticated Codex CLI (`gpt-6.1-sol`),
and Entire CLI 0.11.4 are required. Model inference uses the existing authenticated Codex allowance and local browsers.

```sh
uv sync --locked
uv sync --locked --project integrations/browser-use
uv sync --locked --project integrations/stagehand
uv sync --locked --project integrations/tinytroupe
cd frontend
npm ci
npm run build
cd ..
# Optional licensed Dodo target, before starting the supervisor:
(cd dodo-target && npm ci --ignore-scripts && npm run build)
# After importing a genuine inspected CFO plan into the private runtime:
TRYFOLK_CFO_PLAN=runtime/cfo-plan.json uv run python scripts/local.py
```

Open **http://127.0.0.1:8000**. API documentation: http://127.0.0.1:8000/docs.
The supervisor launches only owned loopback fixture/provider/API processes and stops its own
children on Ctrl-C. In another terminal, prove one complete real SDK cycle:

```sh
uv run python scripts/live_cycle.py
```

This deliberate local probe writes durable results and a successful SDK validation record;
until validated, ordinary quotes expose a setup gate. Restart `scripts/local.py` to consume
that record, then run the durable worker in a second terminal:

```sh
TRYFOLK_CFO_PLAN=runtime/cfo-plan.json uv run python scripts/run_worker.py
```

Choose N=1–20 and reserve **both** complete passes before execution. Browser concurrency is one.
The population is generated once, saved to SQLite with initial cognition, and reused at identical
N against the repaired revision with fresh accounts, cookies and checkout identities.
Generation/cancellation never silently reduces N. A killed worker blocks ambiguous work rather
than repeating payment actions. Owner access is a server-created HttpOnly cookie with Origin
and CSRF checks; the local owner secret remains in ignored, mode-0600 `runtime/local-secrets.json`.
Run-scoped guest access tokens cannot repair or replay.

After a completed defect-producing baseline, the owner can repair/replay through the UI with
`run_worker.py` active. For CLI validation, use the printed run ID:

```sh
uv run python scripts/repair_retest.py RUN_ID
```

The repair changes only the approved owned fulfillment source in an isolated local git source
workspace. Dodo exports preserve the license and original protected-source contract; only
`lib/fulfillment-policy.ts` is writable, under the unchanged broken configuration. Private
keys and databases stay outside the coding workspace. The worker records failing-before/passing-after assertions, creates a real Codex transcript,
attaches it using Entire without changing protected launcher hooks, starts its exact source
revision at a registered local preview, replays the known failure, then reruns the entire same
saved population. Public checkpoint links remain a separate external setup gate.

Optional Neatlogs configuration is server-side only: set `TRYFOLK_NEATLOGS_ENV` to the private
env-file path before launching a worker/probe. Keys never enter SDK/model/browser subprocesses.
Actual OTLP delivery and finalized readback are separate states. `scripts/neatlogs_readback.py TRACE_ID --output PRIVATE_PATH` reads one existing trace through the official MCP; server errors stay unverified.

## Truth and integration limits

`owned-target/fixture/` is a separately authored **local fixture**. It has no Dodo transaction
and charges no money. The independent provider process on3200 and account entitlement processes
on3100/3101 have distinct ledgers. The licensed official Dodo source export and provenance are
retained in `owned-target/upstream/` and the full pinned `dodo-target/upstream/`; neither reference
export is the running fixture application. `dodo-target/` is the owned licensed Dodo Test Mode
target, with separate local databases on3300/3301. Configure distinct scoped checkout-write and
payment-read keys and an exact one-time product privately. Independent provider readback must
match the account entitlement; fixture receipts do not satisfy that requirement. Signed webhook
delivery remains unverified. Customer-triggered, independently server-verified API reconciliation
is a distinct fulfillment path, while receipt and entitlement GET remain read-only.

The native card guard preserves the original owned checkout creation URL separately from
the current payment parent. The existing authenticated receipt read can return the provider's
nullable `payment_link` through a private controller callback; ordinary receipts and stored
evidence exclude that URL. Full parent equality to that independently correlated link is
preferred. A session-to-payment URL difference requires the already occurring session-bound
billing POST response, the currently captured reviewed hosted bundle, one bound Continue
action, and an exact complete URL transformation with the original query and explicit
customization. Missing links, unknown source, unrelated checkouts and stale generations stay
gated. Confirmation rechecks the frozen parent, payment, checkout and ownership before any
native request is forwarded. Local routing fixtures do not prove provider settlement or access.

Guard events retain separate boolean `parent_equality_facts` for complete links, origin,
path, raw query, query pairs/order and document generations. These facts explain a denied
parent without exposing provider routes or query values. They never enter the admission
checks. Creation customization flags remain distinct from observed effective hosted settings.
`parent_query_facts` records presence and equality for `from`, `showOrderDetails` and
`showOnDemandTag`, plus unrelated pair/name counts and original pair order. Unrelated
names and all parameter values stay private.

A separately pinned, currently captured layout bundle can replace the payment query
with a single browser-local `session` key. That route is admitted only when a fresh
read of its bounded session-storage object equals the exact query object produced
by the reviewed billing transition, with the same payment path and original document
generation. The key is not payment authority. The full resolved URL, key, serialized
map and generation are frozen at arm and rechecked at confirmation. Unknown source,
missing storage, extra query keys and changed maps stay gated. Events retain only
boolean `normalization_checks`; raw keys and maps never enter evidence or model input.

The sole repair source seam is `lib/fulfillment-policy.ts`; immutable public regressions must turn red to green under the original broken configuration. The worker verifies the protected export contract, actual Codex/Entire source association, fixed checks and an exact commit/digest production preview. Known replay uses the actual failed saved profile, fresh identity and freshly observed equivalent controls, then runs all original N profiles. Separate unpaid access and duplicate confirmation controls create no extra provider checkout. Source ownership, registration, replay binding and cancellation have unit checks. A genuine Stagehand local iframe probe confirms its supported cross-frame snapshot/input APIs without model or provider calls. The Dodo browser/persona repair cycle remains unverified until executed and independently correlated. Retain each fixture result's separate provenance.

Allowance consumption is an economic scheduling estimate. Authenticated Codex allowance,
local browser costs and token/quota use are recorded separately from cash charges. No
Claude/Nova sponsor credit is used. CFO integration requires an owner-authorized workspace
and an inspected, finalized business plan covering both passes. Import its source references
and evidence hashes server-side; keep account billing, private Page IDs and inspection
artifacts outside the repository. Native XLSX export, OAuth and public sharing require
separate verification and are not established by a local JSON import.

`scripts/cfo_mcp.py` implements the official OAuth MCP transport for authorized workspace access. `scripts/cfo_export.py` imports finalized inspected JSON forecasts. The scheduler preserves rounded20percent contingency, separate450000-microUSD quote and1000000-microUSD lifetime caps, original saved N and each accepted quote. Its conservative repair floor is20000 rather than the native10000, so N1 reserves120000, N8 reserves456000 and is rejected within the450000 envelope. The native N8=444000 proposal is not silently selected. See [CFO-FORECAST.md](CFO-FORECAST.md).

The runtime does not push, publish, deploy, initiate charges or trials, submit or send messages.

## Checks

```sh
uv run pytest -q
uv run ruff check src scripts integrations tests
cd frontend && npm run build && npm run verify:api
```

Tests replace external I/O only and are explicitly distinct from live SDK evidence. Keep exact run IDs, revision/checkpoint IDs, traces and unresolved account/readback gates
in a private local report. These artifacts are excluded from source publication. Team: solo NewGen Labs.

If Entire captured a repair before a capture timeout, the connected owner can choose
**Recover saved repair checkpoint**. `POST /runs/{id}/repairs/recover` queues verification
of the existing Codex session, checkpoint transcript and exact clean source commit. It
adds no budget debit and starts no code repair, persona generation or payment action.
The previous blocked job and error remain recorded. Missing or mismatched context
stays blocked; canceled runs cannot recover or publish a repair.

Entire 0.11.4 probes the controlling terminal independently of stdin. Capture uses a
detached process group and the supported CI sentinel, without `--force`. A command
timeout triggers read-only reconciliation of its exact saved session, never another
automatic attach. The captured trailer is linked before registering the immutable
preview revision, then verified through `entire checkpoint explain --commit`.
