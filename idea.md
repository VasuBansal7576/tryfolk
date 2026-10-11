# TryFolk — product idea

Saved: 10 October 2026
Status: working idea and architecture handoff, not a formal specification.
TryFolk is the accepted product name.

## The problem

People are building apps, agents, and SaaS products quickly. Even when customers want to pay, broken or confusing checkout and access flows can lose the sale or leave a paying customer unable to use the product.

The failure can affect only a particular customer, account state, device, geography, or sequence of actions. A successful checkout for the developer does not establish that the flow works for everyone.

## The idea

TryFolk gives a product its first varied synthetic customers. Generate relevant customers automatically, let them make their own decisions while using the app, execute those decisions in real browsers, and collect evidence of what happened. Find who cannot complete payment and receive the purchased capability, investigate the evidence, and prepare an evidence-backed pull request on the connected target repository. Before owner merge review, the saved customers test the exact PR preview under equivalent starting conditions. A report or generated patch alone is not the end-to-end outcome.

The experience is: enter an app link and a number of personas → watch the population work → inspect verified results → review a draft PR → retest its exact preview → owner merge review.

Every new run has exactly two inputs:

1. App URL.
2. Number of personas.

Users do not choose individual personas, supply demographic filters, write test cases, or select every edge case. The system discovers the app and builds the population.

## What a persona represents

One persona is one synthetic customer with a goal, background, preferences, patience, confidence, memory, and isolated test identity. A journey can include retries, revisits, confusion, abandonment, and recovery.

Variation can include geography, device, locale, account history, accessibility needs, technical familiarity, price sensitivity, frustration with alternatives, and willingness to wait. Younger and older customers can be included without treating age as a deterministic behavior rule.

Important cases include:

- Payment succeeds but the same customer does not receive access.
- Payment or access remains pending and the customer refreshes, leaves, or returns.
- A retry causes duplicate submission or inconsistent state.
- A mobile return path or checkout redirect fails.
- An existing account, expired session, or unexpected starting state changes the outcome.
- A supported location, billing country, currency, or payment method behaves differently.
- A declined payment receives confusing feedback or prevents a valid retry.
- An adversarial customer attempts scoped abuse within an owned test environment.

The system must implement test conditions through fixtures. A persona description alone cannot create a slow connection, a delayed webhook, a different IP location, or a failed payment.

## How population generation works

Browser Use discovers the app once and produces an AppBrief describing observed product promises, signup, checkout, paid access, navigation, and blockers.

A population planner combines that AppBrief with checkout-risk dimensions and the conditions supported by the target connection. TinyTroupe generates relevant customer profiles and supplies their decisions during execution.

Generate in batches, deduplicate, and expand distinct supported combinations as the requested count increases. Preserve population lineage for retesting. Thousands of profiles are a scale target; the initial implementation should prove one actual journey and then a bounded queue.

Reddit discussions and persona datasets can optionally provide research about real problems and motivations. They are not required for the MVP, and they do not execute browser tests. No Tencent dataset, MiroFish, or alternative persona platform has been selected as an additional dependency.

Additional customers should explore underrepresented supported conditions and paths rather than merely new biographies. Record assigned conditions separately from conditions actually exercised. Show requested, generated, queued, executed, completed, and inconclusive customer counts, distinct conditions exercised, and verified findings. More customers cannot guarantee a new edge case or defect for every additional customer or statistically representative real conversion rates.

Failures also guide bounded follow-up exploration. Generate nearby variations of a failing customer or condition, link them to the original finding, and execute only supported fixtures. Keep these diagnostic attempts separate from the requested population and its before/after comparison. Use a reserved investigation allowance; a request for 100 customers must not silently become a larger paid run.

## Finding who is affected

One failed customer is a lead, not proof that everyone is affected. Repeat the failing steps under equivalent conditions, try fresh accounts with the same relevant history, and vary one condition at a time. Compare browser evidence, payment/access records, traces, and the connected code path.

Report observed scope as account-specific, group-specific, shared-path, intermittent, or unresolved. Include attempts, reproduced failures, passing controls, target version, changed conditions, and untested cases. Even a shared-path finding does not establish that every real customer is affected.

For example: an existing-trial upgrade fails in five fresh-account tests, while five new-account controls pass. That supports a trial-upgrade finding in the tested configuration. It does not establish a real-world failure rate.

## Agreed stack and partner roles

- Frontend: the two-input landing page, live run experience, and concise evidence/results.
- FastAPI and a Python worker: run lifecycle, scheduling, limits, persistence, and adapters.
- Browser Use: app discovery.
- TinyTroupe: synthetic customer profiles, reasoning, and memory.
- Stagehand: actual browser observations and execution through isolated sessions.
- Dodo Payments sandbox: demo payments. Stripe is excluded from this plan following the user's constraint.
- Outcome verifier: correlate read-only provider payment truth with the same app account's read-only entitlement lookup; exercise usable access separately and identify intentional state mutations.
- neatlogs: actual persona decisions, tool calls, browser results, failures, retries, verification references, investigation, and latency/cost visibility. Correlate run/persona/account/payment identifiers and relevant target-app evidence without exposing secrets. Link evidence rather than rebuilding its general dashboard.
- cfo.ai: the business and unit-cost model for discovery, generation, customer/browser execution, verification, diagnosis, repair, and replay, plus pricing, monthly revenue scenarios, margins, burn, and runway. This informs affordable population sizes. Save the resulting business plan for the submission. Our scheduler enforces actual spending caps; cfo.ai is not assumed to run as an API for each count selection or execute the personas.
- Coding agent: investigate findings, implement and validate a fix on an isolated branch, prepare a draft PR on the authorized connected target repository, and link the exact PR preview and retest evidence for owner merge review.
- Entire: capture coding-agent context and code checkpoints for development and repairs. It does not itself perform the repair.
- SQLite with a persistent volume: initial run/job/evidence metadata store. The proposed Dodo demo's existing MongoDB is a separate target-app database, not a requirement for TryFolk.
- Executor: explicitly skipped to keep the hackathon implementation smaller.

## What the URL-only experience requires

For the public judge demo, enter the URL of our deployed Dodo checkout demo into the same App URL field. Its repository, Dodo sandbox credentials, test identities, read-only payment/entitlement verifier, and preview connection are configured once behind that URL. Visitors still provide only App URL and Number of customers/personas. A small repository-connected status may explain readiness without changing the accepted UI design. A payment link alone cannot establish post-purchase product access, and a repository URL alone is not a running application.

For another owner's app, connect their app and authorized repository once using a saved AppConnection containing those integrations. An arbitrary URL does not reveal private payment records or grant repository access. Discovery-only results for unconnected apps must not promise full verification or repair.

The chosen demo repository is [dodopayments/dodo-checkout-demo](https://github.com/dodopayments/dodo-checkout-demo). Use our own copy as the target app, deployed with Dodo sandbox. Dodo sandbox supplies test-payment behavior; the checkout demo supplies the running product and source code that TryFolk can investigate and repair. We do not need to find a different deployed commercial app or build another SaaS from scratch. Deployment, sandbox login fixtures, webhook signature verification, account reset, and authoritative credit/access checks remain implementation work. See the Demo target and setup gates section of `architecture.md` for the setup gates. The deployment host and tested runtime versions remain undecided. The target is not established as deployed or verified.

## Evidence and before/after

For each journey save observations, actual actions, screenshots, trace references, timing, customer/account identity, checkout/payment identity, and entitlement verification.

Separate verified product defects from customer abandonment, automation errors, blocked cases, and unsettled outcomes. A synthetic customer's decision to leave is not a measured real-world churn rate.

Reproduce a verified finding on a pinned target revision, prepare and validate the fix on an isolated branch, and create a draft PR on the connected target repository. Deploy a preview of that exact head commit. First replay the known failing actions, then adaptively retest saved customer profiles and supported conditions under equivalent starting conditions. Run a small held-out set of nearby regression cases separately from the original population denominator. Known-failure replay proves that case; held-out cases check nearby regressions. Track action replay and adaptive retesting separately because new model decisions may differ.

Attach before/after evidence to the PR for owner merge review. Link its URL, base/head commits, preview deployment revision, validation, Entire checkpoint, and neatlogs evidence. A changed PR head invalidates prior preview acceptance until retested. This planned feature does not mean a real PR has already been created or that publication is currently authorized. Do not automatically merge, deploy to production, or modify upstream Dodo's repository.

Payment and entitlement lookups must be read-only; a status endpoint that also grants access can erase the defect. Exercise the purchased capability separately and identify intentional state mutations. Freeze target revision and fault configuration for each run. Keep healthy, deliberately broken, and repaired previews separate, isolate test accounts, and reset only run-owned state so concurrent visitors cannot contaminate comparisons.

Compare before and after with the same population lineage, clear denominators, and incomplete counts. Report actual verified changes rather than invented improvement percentages.

## User experience requirements to retain

- Landing page: app URL and persona count, with concise copy and a clear start action.
- Working page: show customers actually trying the product, with concise explanatory text. Adapt density and detail to the requested population and viewport. The user accepted the human crowd direction, with a product snapshot and one inspectable customer story.
- Results page: understandable outcomes and evidence, followed by the repair/retest flow.
- Transparency: reveal actual progress, actions, blockers, findings, and run provenance without filling the UI with implementation details.
- Keep Browser Use, TinyTroupe, Stagehand, and other stack names out of the product header/footer.
- Remove unnecessary “interactive demo” and “before/after” header decoration.
- Users do not select personas.
- The accepted visual direction uses warm paper, ink typography, illustrated human crowds, and concise customer stories. The literal galaxy, dot-swarm iterations, and three later swarm concepts were rejected. The current requirement is adaptive population presentation for counts such as 50, 100, and 1,000, rather than an agent-swarm aesthetic.

## Current implementation state

The earlier React/Vite frontend prototype was removed from the working tree; its history remains in Git. The current `designs/tryfolk.html` is a simulated UI reference. It does not establish that real discovery, TinyTroupe decisions, Stagehand execution, Dodo verification, neatlogs instrumentation, draft PR creation, or PR-preview retesting is implemented. Deployment, baseline verification, and real integration evidence remain planned until actually completed and recorded.

The accepted interactive product UI is `designs/tryfolk.html`. Earlier prototypes and rejected explorations have been removed.
The three newly generated swarm references were also rejected. Build from the accepted human crowd reference and `designs/tryfolk.html`; the Agreed designs section of `idea.md` defines the current experience contract. Detailed customers, activity, traces, and evidence remain inspectable. Population size affects the presentation; it does not mean every browser runs concurrently. Set execution allowances from measured cost, and keep any visual detail cap separate from the number of customers actually tested.

## Hackathon handoff

See `hackathon.md` for current event requirements and the project evidence checklist. Keep the code's actual implementation status, dated experiments, trace links, repair checkpoints, and measured before/after results visible in the repository. Proposed partner roles are not evidence of integrations.

## First implementation milestone

Connect one owned Dodo sandbox target. Make URL + 1 persona produce one real observed browser journey with authoritative payment/access verification and linked traces. Then add persisted populations, execution limits, and URL + N scheduling. After the first loop works, culminate in a verified finding → draft PR → exact PR preview → failing-action replay → saved-population and held-out retests → owner merge review demonstration. Keep this within the existing stack; no further framework expansion is required.

See `architecture.md` for the detailed proposed modules, data records, pipeline, API, deployment, and repair flow.

## Agreed designs

All three accepted compositions form one product story:

- `designs/first-arrivals.png`: the URL/count start surrounded by arriving customers.
- `designs/bigger-crowd.png`: a bounded representative human crowd for a larger population.
- `designs/moment-of-friction.png`: a highlighted customer, product snapshot and inspectable journey.
- `designs/tryfolk.html`: the complete interactive UI: arrival, discovery, crowd, customer journeys, findings, payment/access evidence, fix review, retest, comparison and run history. This single file embeds its artwork, fonts, styles and script. Its testing, payments and repair data are simulated.

Use the human crowd composition, warm paper, ink, Geist UI typography, restrained teal and serif customer quotes. Keep every requested customer individually inspectable while bounding the artwork at larger counts. UI count validation currently allows 1–1,000; actual execution allowance and concurrency must come from measured backend cost. Keep the run inputs to app URL and count, and product copy concise. Do not add stack names or judge instructions to the header or footer. Earlier agent swarms, galaxy/dot designs and dashboard versions were rejected.

## Executable paired-run constraint
N is selected once, within an upfront budget covering both complete passes and diagnostic/repair overhead. Save all N initial personas and cognition; replay the same population against the exact repaired revision under equivalent fresh test conditions. Report completed, failed, abandoned, pending and blocked outcomes separately. Local fixture provider evidence, economic estimates and genuinely measured sandbox evidence have explicit provenance. No charges, paid trial, publication or submission are authorized in this delivery.
