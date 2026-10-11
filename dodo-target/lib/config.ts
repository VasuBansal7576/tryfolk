import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { specialDownloadsDisposition } from './fulfillment-policy';

export type Env = Record<string, string | undefined>;
export const PROVIDER_ORIGIN = 'https://test.dodopayments.com';
export const CHECKOUT_ORIGIN = 'https://test.checkout.dodopayments.com';
export const UPSTREAM_SHA = 'ef7cbd980d8f1e57b036dc2af71ff05521b6175a';
export const Mode = z.enum(['healthy', 'broken']);
export type Mode = z.infer<typeof Mode>;
export type SetupError = { code: string; action: string };

export function sourceFingerprint(root: string): string {
  const hash = createHash('sha256');
  function walk(relative: string) {
    const path = join(root, relative);
    if (!existsSync(path)) return;
    const entries = readdirSync(path, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
    for (const entry of entries) {
      const name = `${relative}/${entry.name}`;
      if (entry.isDirectory()) walk(name);
      else if (entry.isFile()) hash.update(name).update('\0').update(readFileSync(join(root, name)));
    }
  }
  for (const dir of ['app', 'lib', 'scripts']) walk(dir);
  for (const file of ['package.json', 'package-lock.json', 'next.config.mjs', 'tsconfig.json']) {
    if (existsSync(join(root, file))) hash.update(file).update('\0').update(readFileSync(join(root, file)));
  }
  return hash.digest('hex');
}

export function configuration(env: Env, root: string) {
  const errors: SetupError[] = [];
  const warnings: SetupError[] = [];
  function requireValue(name: string, code: string, action: string) {
    const value = env[name]?.trim() ?? '';
    if (!value) errors.push({ code, action });
    return value;
  }
  const sandbox = env.TARGET_SANDBOX === 'true' && env.TARGET_ENV === 'local';
  if (!sandbox) errors.push({ code: 'sandbox_disabled', action: 'Set TARGET_SANDBOX=true and TARGET_ENV=local in this owned loopback sandbox. Production fixture auth is forbidden.' });
  const origin = env.TARGET_ORIGIN || 'http://127.0.0.1:3300';
  let safeOrigin = false;
  try {
    const url = new URL(origin);
    safeOrigin = url.origin === origin && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) && url.protocol === 'http:' && !url.username && !url.password;
  } catch { /* Invalid origin is an explicit setup gate. */ }
  if (!safeOrigin) errors.push({ code: 'unsafe_origin', action: 'Set TARGET_ORIGIN to an exact HTTP loopback origin, e.g. http://127.0.0.1:3300.' });
  const controlToken = requireValue('TARGET_CONTROL_TOKEN', 'missing_control_token', 'Generate a private random TARGET_CONTROL_TOKEN of at least 32 characters; supply it only to the backend control client.');
  if (controlToken && controlToken.length < 32) errors.push({code: 'weak_control_token', action: 'Replace TARGET_CONTROL_TOKEN with at least 32 random characters.'});
  const writeKey = requireValue('DODO_TEST_WRITE_KEY', 'missing_write_key', 'Primary must supply a server-only Dodo TEST MODE key with Customers Write and Checkout Sessions Write; every other resource None.');
  const readKey = requireValue('DODO_TEST_READ_KEY', 'missing_read_key', 'Primary must supply a DISTINCT TEST MODE key with Checkout Sessions Read and Payments Read; every other resource None and no Write grants.');
  if (writeKey && writeKey === readKey) errors.push({code:'keys_not_distinct', action:'Use distinct test write and read-only keys.'});
  if (env.DODO_TEST_KEYS_CONFIRMED !== 'true' || env.DODO_READ_KEY_READ_ONLY !== 'true') errors.push({code:'key_scope_unconfirmed', action:'Primary must confirm both keys are TEST MODE with only their documented granular resource grants and no verifier Write grants, then set DODO_TEST_KEYS_CONFIRMED=true and DODO_READ_KEY_READ_ONLY=true. These are owner attestations, not provider validation; the app cannot infer key privilege from a token.'});
  const webhookKey = env.DODO_TEST_WEBHOOK_KEY?.trim() || '';
  if (!webhookKey) warnings.push({code:'webhook_not_configured',action:'Signed webhook delivery is unavailable until primary approves an endpoint and privately supplies its secret. Session-owned reconciliation can recover a fully matched settled payment through the independent test read key; it is not signed webhook evidence.'});
  const productId = requireValue('DODO_TEST_PRODUCT_ID', 'missing_product', 'Primary must create and supply one one-time Special Downloads TEST MODE product.');
  const amountResult = z.coerce.number().int().positive().safeParse(env.DODO_EXPECTED_AMOUNT_MINOR);
  const amountMinor = amountResult.success ? amountResult.data : null;
  if (amountMinor === null) errors.push({code:'missing_amount', action:'Set DODO_EXPECTED_AMOUNT_MINOR to the exact expected tax-inclusive checkout total in minor units, e.g. 1200 for a USD $12 tax-inclusive product.'});
  const currencyResult = z.enum(['USD', 'INR', 'EUR', 'GBP']).safeParse(env.DODO_EXPECTED_CURRENCY);
  const currency = currencyResult.success ? currencyResult.data : null;
  if (currency === null) errors.push({code:'missing_currency', action:'Set DODO_EXPECTED_CURRENCY to the configured product currency USD, INR, EUR or GBP. Disable adaptive currency selection.'});
  const modeResult = Mode.safeParse(env.TARGET_MODE || 'healthy');
  const mode = modeResult.success ? modeResult.data : 'healthy';
  if (!modeResult.success) errors.push({code:'invalid_mode', action:'Set TARGET_MODE to healthy or broken; restart as a separately pinned source revision. A repaired preview must use actual changed source, not a repaired mode flag.'});
  if (env.DODO_PAYMENTS_BASE_URL || env.DODO_ENVIRONMENT && env.DODO_ENVIRONMENT !== 'test_mode') errors.push({code:'provider_override_forbidden', action:'Remove provider URL/environment overrides. Only https://test.dodopayments.com is permitted.'});
  const currentFingerprint = sourceFingerprint(root);
  let fingerprint=currentFingerprint;
  let sourceCommit:string|null=null;
  let sourceClean:boolean|null=null;
  if (env.NODE_ENV === 'production') {
    try {
      const built=z.object({source_sha256:z.string().regex(/^[a-f0-9]{64}$/),source_commit:z.string().nullable(),source_clean:z.boolean()}).parse(JSON.parse(readFileSync(join(root,'.target-manifest.json'),'utf8')));
      fingerprint=built.source_sha256;
      sourceCommit=built.source_commit;sourceClean=built.source_clean;
      if (fingerprint !== currentFingerprint) errors.push({code:'build_revision_mismatch',action:'Source/config has changed since the local build. Rebuild and restart, then create new run fixtures pinned to the new manifest.'});
    } catch {
      errors.push({code:'build_manifest_missing',action:'Run npm run build in dodo-target/ before starting the production-optimized local server.'});
    }
  }
  const revision = `sha256:${createHash('sha256').update(JSON.stringify({fingerprint, mode, productId, amountMinor, currency})).digest('hex')}`;
  const manifest = {
    contract_version: '1', target_id: 'tryfolk-owned-dodo-special-downloads',
    target_revision: revision, source_sha256: fingerprint, upstream_commit: UPSTREAM_SHA,
    source_commit:sourceCommit, source_clean:sourceClean,
    mode, fault: specialDownloadsDisposition(mode) === 'suppressed' ? 'payment-settled-fulfillment-suppressed' : null,
    repair: {source_file:'lib/fulfillment-policy.ts',disposition:specialDownloadsDisposition(mode)},
    adapter: 'persistent-local-sqlite', app_origin: origin,
    safe_origins: [origin], checkout_origins: [CHECKOUT_ORIGIN], provider_environment: 'test_mode',
    fixture_auth_enabled: sandbox && safeOrigin && controlToken.length >= 32,
    product: {name:'Special Downloads', product_id:productId || null, amount_minor:amountMinor, currency, type:'one-time', artwork_count:12},
  };
  return {env, sandbox, safeOrigin, origin, controlToken, writeKey, readKey, webhookKey, productId, amountMinor, currency, mode, manifest, errors, warnings};
}
export type Config = ReturnType<typeof configuration>;
