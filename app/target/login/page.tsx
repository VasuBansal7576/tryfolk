'use client';
import { useEffect, useState } from 'react';
export default function TestLogin() {
  const [token,setToken]=useState(''),[status,setStatus]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{
    const fragment=new URLSearchParams(window.location.hash.slice(1));
    setToken(fragment.get('token') || '');
    window.history.replaceState(null,'','/target/login');
  },[]);
  async function login() {
    setBusy(true);setStatus('');
    try {
      const response=await fetch('/api/target/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login_token:token})});
      setToken('');
      if(!response.ok){setStatus('This test login is disabled, expired, already used, or revoked. Request a fresh run fixture.');setBusy(false);return;}
      window.location.replace('/special-downloads');
    }catch{setStatus('The local target is unavailable.');setBusy(false);}
  }
  return <><p className="eyebrow">Isolated customer login</p><h1>Enter your test account</h1><p>This single-use link signs you into a fresh account owned by this sandbox run.</p><button id="test-login" disabled={!token || busy} onClick={login}>{busy ? 'Signing in…':'Continue as test customer'}</button>{!token && !busy && <p role="status">Open the login link provided by the run fixture.</p>}{status && <p role="alert" className="error">{status}</p>}</>;
}
