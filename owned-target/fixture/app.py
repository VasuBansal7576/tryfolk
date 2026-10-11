"""Local fixture counterpart to the licensed checkout demo. NO Dodo payment claim.
Provider and entitlement databases are separate; verification GETs never grant access.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

ROOT = Path(os.environ.get("TARGET_STATE", "runtime/target"))
ROOT.mkdir(parents=True, exist_ok=True)
REVISION = os.environ.get("TARGET_REVISION", "unregistered")
SOURCE = Path(os.environ.get("TARGET_FULFILLMENT", "owned-target/fixture/fulfillment.py"))
PROVIDER = os.environ.get("TARGET_PROVIDER", "http://127.0.0.1:3200")
KEY = os.environ.get("TARGET_FIXTURE_SECRET", "")
ROLE = os.environ.get("TARGET_ROLE", "app")
FAULT = os.environ.get("TARGET_FAULT", "paid-no-access")
app = FastAPI(title="Owned checkout local fixture")


def db():
    connection = sqlite3.connect(ROOT / ("provider.sqlite" if ROLE == "provider" else "accounts.sqlite"))
    connection.row_factory = sqlite3.Row
    connection.execute("CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,payload TEXT NOT NULL)")
    return connection


def save(value):
    with db() as connection:
        connection.execute("INSERT OR REPLACE INTO records VALUES (?,?)", (value["id"], json.dumps(value)))


def read(identifier):
    with db() as connection:
        row = connection.execute("SELECT payload FROM records WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise HTTPException(404)
    return json.loads(row[0])


def trusted(request):
    if not KEY or not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + KEY):
        raise HTTPException(403, "Fixture owner credential required")


def account(request):
    return read(request.cookies.get("account", ""))


@app.get("/health")
def health():
    return {"revision": REVISION, "source_digest": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "fault": FAULT, "provider": "local-fixture", "role": ROLE}


class Seed(BaseModel):
    run_id: str
    persona_id: str
    conditions: dict


@app.post("/fixtures/accounts")
def provision(body: Seed, request: Request):
    trusted(request)
    value = {"id": uuid4().hex, "run_id": body.run_id, "persona_id": body.persona_id,
             "conditions": body.conditions, "units": 0, "payment_id": None, "checkout_id": uuid4().hex,
             "product_id": "credits", "amount": 500, "currency": "USD"}
    save(value)
    return {"account_id": value["id"], "session_url": "/session?account=" + value["id"], "checkout_id": value["checkout_id"], "product_id": "credits", "amount": 500, "currency": "USD"}


@app.get("/session")
def login(account: str):
    read(account)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("account", account, httponly=True, samesite="strict")
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    try:
        value = account(request)
    except HTTPException:
        value = {"units": 0, "payment_id": None}
    status = "Payment confirmed." if value["payment_id"] else "10 credits for $5 in the local fixture. No money charged."
    return f'''<!doctype html><html><head><title>Dodo checkout — owned local fixture</title><style>body{{font:18px system-ui;background:#f4f1e8;color:#242c2b;max-width:700px;margin:80px auto}}button{{padding:16px 22px;background:#246c60;color:white;border:0;border-radius:8px;margin:10px 0}}aside{{padding:12px;border:1px solid #c8c9bb}}p{{line-height:1.7}}</style></head><body><aside>LOCAL FIXTURE PROVIDER · No Dodo transaction · No charges</aside><h1>Image credits</h1><p>Based on the licensed Dodo checkout demo. Buy credits, then use your purchased capability.</p><p>{status}</p><p>Available credits: {value["units"]}</p><form action="/checkout" method="post"><button id="buy" type="submit">Buy 10 credits — fixture payment</button></form><form action="/capability" method="post"><button id="use" type="submit">Use purchased image capability</button></form></body></html>'''


@app.post("/payments")
def provider_pay(body: dict, request: Request):
    trusted(request)
    if ROLE != "provider":
        raise HTTPException(404)
    # One result per checkout ID. Safe duplicate submissions never double fulfill.
    try:
        return read(body["checkout_id"])
    except HTTPException:
        value = {**body, "id": body["checkout_id"], "payment_id": "fixture-" + uuid4().hex,
                 "status": "succeeded", "provider": "local-fixture"}
        save(value)
        return value


@app.get("/payments/{checkout_id}")
def provider_read(checkout_id: str, request: Request):
    trusted(request)
    if ROLE != "provider":
        raise HTTPException(404)
    return read(checkout_id)


@app.post("/checkout")
def checkout(request: Request):
    value = account(request)
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        response = client.post(PROVIDER + "/payments", json={k:value[k] for k in ("checkout_id", "product_id", "amount", "currency")} | {"account_id": value["id"]}, headers={"Authorization": "Bearer " + KEY})
        response.raise_for_status()
        payment = response.json()
    value["payment_id"] = payment["payment_id"]
    spec = importlib.util.spec_from_file_location("fulfillment", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.should_grant(payment) and not value.get("fulfilled"):
        value["units"] += 10
        value["fulfilled"] = True
    save(value)
    return RedirectResponse("/", status_code=303)


@app.get("/entitlements/{account_id}")
def access(account_id: str, request: Request):
    trusted(request)
    value = read(account_id)
    return {"account_id": value["id"], "product_id": value["product_id"], "granted": bool(value.get("fulfilled")),
            "payment_id": value["payment_id"] if value.get("fulfilled") else None, "units": value["units"]}


@app.post("/capability", response_class=HTMLResponse)
def capability(request: Request):
    value = account(request)
    if value["units"] < 1:
        return HTMLResponse("<h1>No purchased access</h1><p>Buy credits to unlock this capability.</p><a href='/'>Return</a>", status_code=403)
    value["units"] -= 1  # intentional mutation, measured separately from entitlement reads
    save(value)
    return "<h1>Purchased capability available</h1><p>One credit consumed.</p><a href='/'>Return</a>"
