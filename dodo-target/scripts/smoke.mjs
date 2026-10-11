import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';
import assert from 'node:assert/strict';

// Real built Next HTTP server, no provider fixtures or credentials and no external requests.
const directory=mkdtempSync(join(tmpdir(),'tryfolk-dodo-http-'));
const token=randomBytes(32).toString('hex');
const port='3339',origin=`http://127.0.0.1:${port}`;
const environment={...process.env,NODE_ENV:'production',TARGET_ENV:'local',TARGET_SANDBOX:'true',TARGET_MODE:'healthy',TARGET_ORIGIN:origin,TARGET_DATABASE_PATH:join(directory,'target.sqlite'),TARGET_CONTROL_TOKEN:token,
  DODO_TEST_WRITE_KEY:'',DODO_TEST_READ_KEY:'',DODO_TEST_WEBHOOK_KEY:'',DODO_TEST_PRODUCT_ID:'',DODO_EXPECTED_AMOUNT_MINOR:'',DODO_EXPECTED_CURRENCY:'',DODO_TEST_KEYS_CONFIRMED:'false',DODO_READ_KEY_READ_ONLY:'false'};
const server=spawn(process.execPath,['node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port',port],{env:environment,stdio:['ignore','pipe','pipe']});
let output='';
server.stdout.on('data',chunk=>{output+=chunk.toString();});
server.stderr.on('data',chunk=>{output+=chunk.toString();});
try {
  const deadline=Date.now()+15_000;
  let ready;
  while(Date.now()<deadline){
    try{ready=await fetch(`${origin}/api/target/readiness`);if(ready.ok)break;}catch{}
    if(server.exitCode!==null)throw new Error(`Local Next server exited ${server.exitCode}`);
    await new Promise(resolve=>setTimeout(resolve,150));
  }
  assert.ok(ready?.ok,'real HTTP server readiness endpoint');
  const setup=await ready.json();
  assert.equal(setup.ready,false);
  assert.equal(setup.provider_baseline_verified,false);
  assert.ok(setup.errors.some(error=>error.code==='missing_write_key'));
  assert.ok(!setup.errors.some(error=>error.code==='build_revision_mismatch' || error.code==='build_manifest_missing'));
  const page=await fetch(`${origin}/special-downloads`);
  assert.equal(page.status,200);
  assert.match(await page.text(),/Special Downloads/);
  const headers={'Content-Type':'application/json',Authorization:`Bearer ${token}`,Origin:origin};
  const fixtureResponse=await fetch(`${origin}/api/target/fixtures`,{method:'POST',headers,body:JSON.stringify({run_id:'http_smoke_run',persona_id:'http_smoke_persona',target_revision:setup.manifest.target_revision})});
  assert.equal(fixtureResponse.status,201);
  const fixture=await fixtureResponse.json();
  const login=await fetch(`${origin}/api/target/login`,{method:'POST',headers,body:JSON.stringify({login_token:fixture.login_token})});
  assert.equal(login.status,200);
  const cookie=login.headers.get('set-cookie').split(';')[0];
  const checkout=await fetch(`${origin}/api/target/checkouts`,{method:'POST',headers:{...headers,Cookie:cookie},body:'{}'});
  assert.equal(checkout.status,503);
  assert.equal((await checkout.json()).error,'setup_required');
  const returned=await fetch(`${origin}/api/target/return?status=succeeded&payment_id=pay_forged`,{headers:{Cookie:cookie}});
  assert.equal((await returned.json()).has_access,false);
  const reset=await fetch(`${origin}/api/target/reset`,{method:'POST',headers,body:JSON.stringify({run_id:fixture.run_id,target_revision:fixture.target_revision})});
  assert.equal(reset.status,200);
  const revoked=await fetch(`${origin}/api/target/me`,{headers:{Cookie:cookie}});
  assert.equal(revoked.status,401);
  assert.ok(!output.includes(token) && !output.includes(fixture.login_token),'server log does not contain control/login credentials');
  process.stdout.write('PASS real local Next HTTP smoke: setup gate, fixture login, checkout 503, forged return denied, scoped reset. No provider/model requests.\n');
} finally {
  server.kill('SIGTERM');
  if(server.exitCode===null)await new Promise(resolve=>server.once('exit',resolve));
  rmSync(directory,{recursive:true,force:true});
}
