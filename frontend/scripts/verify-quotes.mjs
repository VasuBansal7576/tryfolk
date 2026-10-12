import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { quoteSchema, readinessSchema } from '../src/contracts.ts';
import { hasBudgetDetails, affordabilityMessage, quoteBudget, forecastSource } from '../src/budget.ts';
import { redact } from '../src/presentation.ts';

// Quote-only verification. No run, repair, replay, payment or model job is created.
const base = process.env.TRYFOLK_API_URL || 'http://127.0.0.1:8000';
assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(new URL(base).hostname), 'Verification is local-only.');
async function call(path, options = {}) {
  const response = await fetch(base + path, { ...options, signal: AbortSignal.timeout(20_000) });
  const body = await response.json();
  assert.equal(response.status, 200, `${path} must succeed`);
  return body;
}
const readiness = readinessSchema.parse(await call('/readiness'));
const target = process.env.TRYFOLK_TEST_TARGET || readiness.target_url;
assert.ok(target, 'Register an owned local target before verifying quotes.');
const quotes = [];
for (const count of [4]) {
  const quote = quoteSchema.parse(await call('/quotes', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ app_url: target, persona_count: count }),
  }));
  assert.equal(quote.persona_count, count, 'The requested population must not be resized.');
  const subtotal = quote.before_microusd + quote.after_microusd + quote.overhead_microusd;
  assert.equal(quote.reserved_microusd, subtotal + (quote.contingency_microusd ?? 0));
  if (hasBudgetDetails(quote)) {
    assert.equal(quote.subtotal_microusd, subtotal);
    assert.equal(quote.affordable, quote.reserved_microusd <= quote.quote_limit_microusd && quote.reserved_microusd <= quote.lifetime_available_microusd);
    assert.equal(quote.available_microusd, Math.min(quote.quote_limit_microusd, quote.lifetime_available_microusd));
    assert.equal(quote.contingency_microusd, Math.ceil(subtotal * quote.contingency_basis_points / 10_000));
    assert.ok(quoteBudget(quote).includes('Two independent budget checks'));
  }
  if (quote.cfo_plan_status === 'missing') assert.ok(forecastSource(quote).includes('No validated native forecast is active'));
  if (!quote.affordable) assert.ok(affordabilityMessage(quote).includes('budget limit'));
  quotes.push(quote);
}
const proof = { verified_at: new Date().toISOString(), scope: 'Actual local API quotes only; no run/model/payment jobs', target, quotes, passed: true };
if (process.env.TRYFOLK_PROOF_PATH) await fs.writeFile(process.env.TRYFOLK_PROOF_PATH, JSON.stringify(redact(proof), null, 2) + '\n');
console.log(JSON.stringify({ verified: true, scope: proof.scope, counts: quotes.map(q => q.persona_count), additive_budget_fields: quotes.every(hasBudgetDetails), imported_plan: quotes.every(q => q.cfo_plan_status === 'imported') }));
