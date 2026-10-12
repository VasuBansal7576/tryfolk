'use client';
import { useEffect, useState } from 'react';
import { z } from 'zod';
const Access=z.object({has_access:z.boolean(),orders:z.array(z.object({order_id:z.string(),created_at:z.string(),state:z.string(),granted:z.boolean()}))});
const Recovery=z.object({state:z.enum(['granted','already_granted','suppressed','pending','failed','mismatch','provider_error','setup_required'])});
const messages={
  granted:'Your payment is confirmed and your art pack is available.',
  already_granted:'Your art pack is available.',
  suppressed:'Your payment is confirmed, but the art pack is unavailable. The sandbox owner needs to repair fulfillment.',
  pending:'Payment is still awaiting confirmation. You can check again after completing checkout.',
  failed:'Payment did not succeed. Return to the art pack to start a fresh checkout.',
  mismatch:'The payment could not be matched to this purchase. The sandbox owner needs to review it.',
  provider_error:'Payment confirmation is temporarily unavailable. You can check again.',
  setup_required:'Payment confirmation is waiting for the sandbox owner to finish setup.',
};
export default function CheckoutReturn() {
  const [status,setStatus]=useState('Checking your app access…');
  const [order,setOrder]=useState<string|null>(null);
  const [available,setAvailable]=useState(false);
  const [busy,setBusy]=useState(false);
  useEffect(()=>{
    let current=true;
    fetch('/api/target/return',{cache:'no-store'}).then(async response=>{
      if(!response.ok){if(current)setStatus('Sign in again to check your isolated account.');return;}
      const result=Access.parse(await response.json());
      const latest=result.orders.filter(item=>item.state === 'ready').sort((a,b)=>b.created_at.localeCompare(a.created_at))[0];
      if(current){
        setAvailable(result.has_access);setOrder(latest?.order_id || null);
        setStatus(result.has_access ? 'Your art pack is available.':latest ? 'Confirm your purchase to check payment and unlock your art pack.':'No checkout is available for this account. Return to the art pack to start one.');
      }
    }).catch(()=>{if(current)setStatus('Access could not be read. The payment result remains unverified.');});
    return ()=>{current=false;};
  },[]);
  async function confirm() {
    if(!order || busy)return;
    setBusy(true);setStatus('Confirming your purchase…');
    try {
      // The order comes from the authenticated app response, never URL claims.
      const response=await fetch('/api/target/reconcile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:order})});
      if(response.status === 401){setStatus('Sign in again to confirm this purchase.');return;}
      const result=Recovery.safeParse(await response.json());
      if(!result.success){setStatus('This purchase could not be confirmed for your account. The sandbox owner needs to review it.');return;}
      setStatus(messages[result.data.state]);
      setAvailable(result.data.state === 'granted' || result.data.state === 'already_granted');
    }catch{setStatus('Payment confirmation is temporarily unavailable. You can check again.');}
    finally{setBusy(false);}
  }
  return <><h1>Back from checkout</h1><p role="status">{status}</p>
    {!available && order && <p><button id="confirm-art-pack" disabled={busy} onClick={confirm}>{busy ? 'Confirming purchase…':'Confirm purchase'}</button></p>}
    <a className="button" href="/special-downloads">{available ? 'View your art pack':'Return to art pack'}</a></>;
}
