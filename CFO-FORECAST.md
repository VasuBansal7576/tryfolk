# CFO forecast import contract v2, preparation only

The importer supports an owner-inspected, finalized CFO business plan and paired-budget
forecast with per-row evidence hashes. Runtime activation and product quote acceptance are
separate gates. Illustrative draft quotes and population proposals are not accepted quotes
or selected populations. Keep workspace/Page IDs, inspection artifacts and account billing
outside source control.

The importer accepts JSON with schema_version2, source cfo.ai-inspected-plan, real source_url and plan_id, status finalized, inspected_at, inspection_evidence_sha256, and assumptions. Each assumption has provenance with basis forecast or scheduler_ledger_snapshot, actual Page/table/row reference and evidence SHA256. Numeric assumptions use integer USD microdollars, except contingency_basis_points, integer basis points. snapshot_sha256 is computed from the exact input bytes. Local snapshot copying is not a native CFO export or OAuth readback.

Required assumptions: before_per_persona_microusd, after_per_persona_microusd, discovery_microusd, generation_microusd, investigation_microusd, repair_microusd, known_replay_microusd, contingency_basis_points, quote_budget_microusd, lifetime_budget_microusd. Required native_ledger_snapshot: estimated_consumed_microusd, held_microusd, available_microusd, observed_at, evidence_sha256. Native balance must equal lifetime ceiling minus consumed and held. The snapshot balance is disclosed for comparison; it never replaces the lifetime ceiling with consumed subtracted again.

Quotes disclose base before/after/overhead, subtotal, contingency and basis, protected after reserve including known replay and their contingency, total, independent quote/customer cap, lifetime cap, current scheduler consumed/held/headroom, and the model's native snapshot. Affordability requires total <= quote cap AND total <= current lifetime headroom. No automatic count change.

The scheduler keeps its conservative category floors: discovery10000, generation10000, investigation0, repair20000, known replay10000 microUSD, before/after20000 per persona. A finalized plan can raise a category floor. For an illustrative native forecast with five10000 overhead caps, the scheduler would reserve60000 overhead because repair's existing floor is20000. N8 would total456000 with20percent contingency, above450000 headroom, while the native aggregate overhead50000 yields444000. This is a review discrepancy, not a reason to silently lower repair allowance or select fewer people.

Contingency rounds upward once on the complete subtotal to integer microUSD. After protection rounds upward on after persona costs plus known replay. Before work cannot debit that protected amount. Estimates and contingent reserve are distinct; unused contingency is released after the complete matched retest or cancellation. Estimates are not cash charges.

Accepted quotes/reservations keep their immutable cost contract and exact N after a new plan or configuration. No migration resizes populations or recomputes previous prices. Current owner lifetime caps and available ledger headroom still apply atomically before a new reservation is accepted. Unaffordable historical quotes cannot bypass their original quote cap. Existing reservations/replay keep their original allowance.

New additive Quote fields: subtotal_microusd, contingency_microusd, contingency_basis_points, after_contingency_microusd, after_reserve_microusd, quote_limit_microusd, lifetime_limit_microusd, lifetime_available_microusd, ledger:{estimated_consumed_microusd,held_microusd}, forecast_comparison. available_microusd remains the smaller of quote cap and lifetime headroom. Run.budget_contract retains original quote costs/provenance; after_reserve_microusd is the protected remainder. Old quotes are not retroactively given contingency.

A fresh clone has no private finalized snapshot or active owner plan. Import requires genuine inspected evidence; preparation and unit fixtures do not establish CFO model acceptance.

## Import authorized finalized evidence

```sh
uv run python scripts/cfo_export.py /path/to/finalized-inspected-snapshot.json --source-url https://app.cfo.ai/WORKSPACE/pages/PAGE_ID --plan-id PAGE_ID --evidence /path/to/actual-inspection.txt --output runtime/cfo-plan.json
```

Repeat --evidence for each distinct evidence file. Source URL/ID must exactly match the snapshot; all inspection, per-assumption and ledger evidence SHA256 hashes must match supplied file bytes. Output is mode0600 and atomically replaced only after validation. The command does not reload the runtime or change quotes/reservations. Then set server-only TRYFOLK_CFO_PLAN=runtime/cfo-plan.json and perform a quiet supervisor/worker reload after the owner approves the validated plan integration. The placeholders above are instructions, not an existing plan or executed import.

Legacy v1 imported contracts remain readable for compatibility. New imports require v2; the old eight-row CSV importer no longer accepts incomplete forecasts. Frontend must display contingency and both caps when a validated v2 plan is activated. A fresh installation remains gated until its own validated plan is configured.

Contract metadata keys are exact: schema_version:2, source:'cfo.ai-inspected-plan', source_url:string, plan_id:string, status:'finalized', currency:'USD', inspected_at:timezone-aware-ISO8601, inspection_evidence_sha256:64-lowercase-hex, assumptions:required-integer-map, assumption_provenance:matching-key-map, native_ledger_snapshot:reconciled-balances. Each assumption provenance has basis:'forecast', reference:nonempty-string, evidence_sha256:64-lowercase-hex. Ledger snapshot has the three balances, observed_at:timezone-aware-ISO8601 and evidence_sha256. The importer supplies snapshot_sha256 from the exact JSON input bytes.
