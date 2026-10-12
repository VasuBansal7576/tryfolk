'use client';
import { useEffect, useState } from 'react';
import { z } from 'zod';
import { ARTWORKS } from '../../lib/artworks';
const Ready=z.object({ready:z.boolean(),errors:z.array(z.object({code:z.string(),action:z.string()})),manifest:z.object({product:z.object({amount_minor:z.number().nullable(),currency:z.string().nullable()})})});
const Me=z.object({has_access:z.boolean(),ownership:z.object({account_id:z.string()}),orders:z.array(z.object({order_id:z.string()}))});
const Checkout=z.object({checkout_url:z.url(),order_id:z.string(),checkout_session_id:z.string()});
export default function SpecialDownloads() {
  const [readiness,setReadiness]=useState<z.infer<typeof Ready>|null>(null);
  const [account,setAccount]=useState<z.infer<typeof Me>|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  useEffect(()=>{let current=true;
    Promise.all([fetch('/api/target/readiness',{cache:'no-store'}),fetch('/api/target/me',{cache:'no-store'})]).then(async ([ready,me])=>{
      const readyData=Ready.parse(await ready.json());
      const meData=me.ok ? Me.parse(await me.json()):null;
      if(current){setReadiness(readyData);setAccount(meData);}
    }).catch(()=>{if(current)setError('The target could not load. Check the local server and database.');});
    return ()=>{current=false;};
  },[]);
  async function buy() {
    setBusy(true);setError('');
    try {
      const response=await fetch('/api/target/checkouts',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(!response.ok)throw new Error('Checkout is unavailable. Check sandbox setup or retry deliberately after inspecting the last order.');
      const data=Checkout.parse(await response.json());
      if(new URL(data.checkout_url).origin !== 'https://test.checkout.dodopayments.com')throw new Error('The checkout URL is outside the approved test provider.');
      window.location.assign(data.checkout_url);
    }catch(err){setError(err instanceof Error ? err.message:'Checkout failed.');setBusy(false);}
  }
  const product=readiness?.manifest.product;
  return <><p className="eyebrow">One-time art pack</p><h1>Special Downloads</h1><p>Twelve sample artworks, ready to download after your test purchase. Your pack stays available to this isolated test account. No credits or subscription.</p>
    <p className="price">{product?.amount_minor && product.currency ? new Intl.NumberFormat('en',{style:'currency',currency:product.currency}).format(product.amount_minor/100):'Price awaiting sandbox configuration'}</p>
    {!readiness ? <p role="status">Loading target setup…</p>:!readiness.ready ? <section className="note" aria-label="Sandbox setup required"><h2>Sandbox setup required</h2><p>Checkout is waiting for the owner’s Dodo test configuration.</p><ul>{readiness.errors.map(item=><li key={item.code}><code>{item.code}</code> · {item.action}</li>)}</ul></section>:null}
    {!account ? <section className="note"><h2>Test account required</h2><p>Open the isolated login link supplied for this customer’s run. No Google account or email inbox is needed.</p></section>:<p>Signed in to an isolated test account. <code>{account.ownership.account_id}</code></p>}
    {error && <p role="alert" className="error">{error}</p>}
    {account?.has_access ? <><section className="note"><h2>Your art pack is available</h2><p>Download a file below to exercise your purchased access.</p></section><div className="artworks">{ARTWORKS.map((title,index)=><article className="artwork" key={title}><h3>{title}</h3><small>Local vector sample · 600 × 600</small><a className="button" href={`/api/target/download?artwork=${index}`}>Download {title}</a></article>)}</div></>:<button id="buy-art-pack" disabled={!readiness?.ready || !account || busy} onClick={buy}>{busy ? 'Opening test checkout…':'Get Art Pack'}</button>}
    <p><a href="/special-downloads">Refresh access</a></p>
  </>;
}
