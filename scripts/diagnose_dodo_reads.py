"""Private official SDK GET-only diagnosis for exact existing Test orders."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
NODE = r"""
import DodoPayments from 'dodopayments';
import {AsyncLocalStorage} from 'node:async_hooks';
import {readFileSync} from 'node:fs';
import {CheckoutStatus,Payment,paymentMismatches,providerReadDiagnostic} from './lib/provider.ts';
const input=JSON.parse(readFileSync(0,'utf8'));
const local=new AsyncLocalStorage();
const client=new DodoPayments({bearerToken:process.env.DODO_TEST_READ_KEY,
 environment:'test_mode',baseURL:null,maxRetries:0,timeout:10000,logLevel:'off',
 fetch:async(input,init)=>{
  const url=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url);
  const method=String(init?.method || (input instanceof Request?input.method:'GET')).toUpperCase();
  if(url.origin!=='https://test.dodopayments.com'||method!=='GET')throw new Error('read_scope_denied');
  const lane=local.getStore();const start=Date.now();
  const response=await fetch(input,{...init,redirect:'error'});
  lane.http.push({stage:lane.stage,method,start:new Date(start).toISOString(),
   end:new Date().toISOString(),elapsed_ms:Date.now()-start,status:response.status});
  return response;
 }});
const results=[];
for(let round=1;round<=input.rounds;round++){
 const wave=await Promise.all(input.orders.map(async order=>{
 const lane={round,stage:'checkout',http:[],order_id:order.order_id,persona_id:order.persona_id,
  started_at:new Date().toISOString()};
 return await local.run(lane,async()=>{
  try{
   const checkout=CheckoutStatus.parse(await client.checkoutSessions.retrieve(order.checkout_session_id));
   if(checkout.id!==order.checkout_session_id||!checkout.payment_id)throw new Error('checkout_binding_unavailable');
   lane.stage='payment';
   const payment=Payment.parse(await client.payments.retrieve(checkout.payment_id));
   const mismatches=paymentMismatches(order,payment);
   const checks={exact_checkout:checkout.id===order.checkout_session_id,
    exact_payment:payment.payment_id===checkout.payment_id,
    exact_order_metadata:mismatches.length===0,
    checkout_payment_status_matches:checkout.payment_status===payment.status,
    previous_payment_unchanged:!order.payment_id||payment.payment_id===order.payment_id};
   return {...lane,finished_at:new Date().toISOString(),outcome:'SDK_reads_completed',
    payment_status:payment.status,checkout_status:checkout.payment_status,checks,
    diagnostic_only:true,no_fulfillment_or_acceptance_claim:true};
  }catch(error){return {...lane,finished_at:new Date().toISOString(),outcome:'SDK_read_failed',
   error:providerReadDiagnostic(error,lane.stage),diagnostic_only:true};}
 });
 }));
 results.push(...wave);
}
process.stdout.write(JSON.stringify({source:'official_dodopayments_SDK_Test_Mode_GETs',
 maxRetries:0,shared_stateless_read_client:true,results}));
"""


def main():
    run_id = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    if rounds not in (1, 3):
        raise ValueError("Only one diagnostic wave or the authorized finite three-wave burst")
    if len(run_id) != 32 or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("Exact existing run identity required")
    connection = sqlite3.connect("file:" + str(ROOT / "runtime/dodo-broken.sqlite") + "?mode=ro", uri=True)
    records = [json.loads(row[0]) for row in connection.execute(
        "SELECT data FROM records WHERE collection='orders'")]
    orders = [record for record in records if record.get("run_id") == run_id]
    if len(orders) != 4 or len({order["persona_id"] for order in orders}) != 4:
        raise ValueError("Exactly four existing run-owned Test orders required")
    key = dotenv_values(ROOT / "dodo-target/.env.local").get("DODO_TEST_READ_KEY")
    if not key:
        raise ValueError("Existing approved read-only Test key required")
    started = datetime.now(timezone.utc).isoformat()
    environment = {"PATH": os.environ["PATH"], "DODO_TEST_READ_KEY": key}
    completed = subprocess.run(["node", "--import", "tsx", "--input-type=module", "-e", NODE],
        input=json.dumps({"orders": orders, "rounds": rounds}), text=True, capture_output=True,
        cwd=ROOT / "dodo-target", env=environment, timeout=45)
    if completed.returncode:
        raise RuntimeError("Private SDK diagnostic subprocess failed; raw output withheld")
    result = json.loads(completed.stdout)
    result.update(run_id=run_id, started_at=started, finished_at=datetime.now(timezone.utc).isoformat(),
        diagnostic_only=True, no_provider_writes=True, not_an_acceptance_outcome=True,
        original_target_revisions=list({order["target_revision"] for order in orders}))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    output = ROOT / "runtime/evidence" / f"{run_id}-official-SDK-GET-diagnostic-{stamp}.json"
    output.write_text(json.dumps(result, indent=2))
    output.chmod(0o600)
    print(json.dumps({"evidence": str(output), "reads": [{"outcome": lane["outcome"],
        "error": lane.get("error"), "statuses": [entry["status"] for entry in lane["http"]],
        "checks": lane.get("checks")} for lane in result["results"]]}))


if __name__ == "__main__":
    main()
