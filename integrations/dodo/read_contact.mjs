// Server-only, one existing Payments Read operation. No redirects or raw output.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {privateContactExpectation} from './contact_binding.mjs';
const require=createRequire(new URL('../../dodo-target/package.json',import.meta.url));
const DodoPayments=require('dodopayments').default;
try {
  const expected=JSON.parse(readFileSync(0,'utf8'));
  if (!/^pay_[A-Za-z0-9_-]+$/.test(expected.payment_id) || !process.env.DODO_TEST_READ_KEY) throw Error();
  const client=new DodoPayments({bearerToken:process.env.DODO_TEST_READ_KEY, environment:'test_mode',baseURL:null,
    maxRetries:0,timeout:10000,logLevel:'off',fetch:async(input,init)=>{
      const url=new URL(typeof input==='string'?input:input instanceof URL?input.href:input.url);
      if(url.origin!=='https://test.dodopayments.com' || url.pathname!=='/payments/'+expected.payment_id ||
         url.search || url.username || url.password || (init?.method || 'GET')!=='GET')throw Error();
      return fetch(input,{...init,redirect:'error'});
    }});
  const payment=await client.payments.retrieve(expected.payment_id);
  const result=privateContactExpectation(payment,expected);
  result.facts={...result.facts,test_payments_read:true,redirects_followed:0};
  // stdout is a private controller pipe, never inherited/logged or an API.
  process.stdout.write(JSON.stringify(result));
} catch {
  process.stdout.write(JSON.stringify({facts:{contact_binding_passed:false,test_payments_read_unestablished:true},expected_email:null}));
  process.exitCode=1;
}
