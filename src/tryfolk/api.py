from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime, timedelta, timezone
import secrets
import os
import json
from pathlib import Path

from .config import Settings
from .service import Service, DomainError
from .connection_api import connection_router


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_url: str
    persona_count: int = Field(ge=1, le=1000, strict=True)
    scenario_ids: list[str] | None = Field(default=None, max_length=1000)


class RunRequest(BaseModel):
    quote_id: str


class BaselineRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quote_id: str


class OwnerLogin(BaseModel):
    secret: str


class RepairRequest(BaseModel):
    finding_id: str


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repair_id: str


class CheckpointAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


def bearer(value):
    return value[7:] if value and value.startswith("Bearer ") else None


def create_app(settings=None, *, checkpoint_transport=None):
    app = FastAPI(title="Tryfolk", version="0.1.0")
    service = Service(settings or Settings.from_env(), checkpoint_transport=checkpoint_transport)
    app.state.service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Idempotency-Key", "Content-Type", "X-CSRF-Token"],
    )

    origins = {"http://127.0.0.1:8000", "http://127.0.0.1:5173", "http://localhost:5173"}

    def session(request):
        cookie = request.cookies.get("tryfolk_owner")
        with service.store.transaction() as db:
            row = db.execute("SELECT * FROM owner_sessions WHERE id=?", (cookie,)).fetchone()
        return (
            dict(row)
            if row and datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
            else None
        )

    def csrf_check(request, row):
        if (
            request.headers.get("origin") not in origins
            or not row
            or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf"])
        ):
            raise DomainError(403, "csrf", "Owner mutation requires a trusted Origin and valid CSRF token.")

    def credential(request):
        token = bearer(request.headers.get("authorization"))
        if token:
            return token
        row = session(request)
        if row:
            if request.method not in ("GET", "HEAD"):
                csrf_check(request, row)
            return service.settings.owner_token
        return None

    def connection_owner(request):
        row = session(request)
        if not row or not service.settings.owner_token:
            raise DomainError(401, "owner_session_required", "Sign in with the configured owner session.")
        if request.method not in ("GET", "HEAD"):
            csrf_check(request, row)

    app.include_router(connection_router(service.settings, service.store, connection_owner))

    @app.post("/owner/session")
    def owner_login(body: OwnerLogin, request: Request, response: Response):
        if request.headers.get("origin") not in origins:
            raise DomainError(403, "origin", "Owner login requires a trusted local Origin.")
        if not service.settings.owner_token or not secrets.compare_digest(
            body.secret, service.settings.owner_token
        ):
            raise DomainError(401, "owner_secret", "Owner secret is invalid or not configured.")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with service.store.transaction() as db:
            db.execute(
                "INSERT INTO owner_sessions VALUES (?,?,?)",
                (token, csrf, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
            )
        response.set_cookie("tryfolk_owner", token, httponly=True, samesite="strict", max_age=3600, path="/")
        return {"owner": True, "csrf_token": csrf}

    @app.get("/owner/session")
    def owner_status(request: Request):
        row = session(request)
        return {"owner": bool(row), "csrf_token": row["csrf"] if row else None}

    @app.delete("/owner/session")
    def owner_logout(request: Request, response: Response):
        row = session(request)
        csrf_check(request, row)
        with service.store.transaction() as db:
            db.execute("DELETE FROM owner_sessions WHERE id=?", (row["id"],))
        response.delete_cookie("tryfolk_owner", path="/")
        return {"owner": False}

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, error: DomainError):
        return JSONResponse(status_code=error.status, content={"detail": error.detail})

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "tryfolk"}

    @app.get("/readiness")
    def readiness():
        sdk_status = (
            "pending" if any(g["code"] == "sdk_validation" for g in service.settings.gates) else "live_probe"
        )
        integrations = {
            "browser_use": {
                "status": sdk_status,
                "detail": "Real 0.13.11 Agent discovery with Codex BaseChatModel and local Chromium.",
            },
            "tinytroupe": {
                "status": sdk_status,
                "detail": "Pinned official 0.7.0; TinyPerson decisions and episodic memory through registered Codex client.",
            },
            "stagehand": {
                "status": sdk_status,
                "detail": "Real 4.2.0 local browser and structured Codex callback; no hosted session.",
            },
            "dodo": {
                "status": "blocked",
                "detail": "Sandbox credentials and merchant products not configured. Local fixture is not Dodo evidence.",
            },
            "cfo": {
                "status": "blocked",
                "detail": "Official business plan not imported. Account access and OAuth grants are verified separately; local quotes are non-CFO estimates.",
            },
            "entire": {
                "status": "pending",
                "detail": "CLI 0.11.4 enabled locally; protected hooks retained. Real transcript attach/checkpoint verification required.",
            },
            "neatlogs": {
                "status": "pending",
                "detail": "Real workflow trace delivery and finalized readback have not been verified.",
            },
        }
        manifest = Path("runtime/integration-readiness.json")
        if manifest.exists():
            integrations.update(__import__("json").loads(manifest.read_text())["integrations"])
        from .budget import load_plan

        try:
            cfo_plan = load_plan(service.settings.cfo_plan_path)
        except (ValueError, TypeError, KeyError, OSError):
            cfo_plan = None
        if cfo_plan:
            integrations["cfo"] = {
                "status": "configured",
                "detail": "Inspected native CFO forecast imported. The local quote reserves both passes; this does not accept or fund the native draft.",
                "source_url": cfo_plan["source_url"],
            }
        dodo_targets = [target for target in service.settings.targets if target.provider == "dodo"]
        if dodo_targets:
            provider_ready = any(target.execution_ready and not target.setup_gates for target in dodo_targets)
            integrations["dodo"] = {
                "status": "configured" if provider_ready else "pending",
                "detail": "Registered owned Test Mode target. Checkout, payment, entitlement and file access require bound live validation."
                if not provider_ready else "Owned Test Mode target has credential-backed runtime validation; inspect each journey's independent payment and access evidence.",
            }
        from .telemetry import neatlogs_key

        configured = bool(neatlogs_key())
        integrations["neatlogs"] = {
            "status": "pending" if configured else "blocked",
            "configured": configured,
            "detail": "Project credential configured. Inspect each run's trace receipts and authenticated readback."
            if configured else "Configure NEATLOGS_API_KEY or TRYFOLK_NEATLOGS_ENV before sending real traces.",
        }
        return {
            "integrations": integrations,
            "gates": list(service.settings.gates),
            "target_url": next(
                (t.url for t in service.settings.targets if t.provider == "dodo" and t.mode == "broken"),
                next((t.url for t in service.settings.targets if t.fault == "paid-no-access"), None),
            ),
            "max_personas": service.settings.max_personas,
            "fixed_persona_count": service.settings.fixed_persona_count,
            "cfo_plan_status": "imported" if service.settings.cfo_plan_path else "missing",
        }

    @app.get("/scenarios")
    def scenarios(app_url: str):
        return service.scenarios(app_url)

    @app.post("/quotes")
    def quote(body: QuoteRequest):
        return service.quote(body.app_url, body.persona_count, body.scenario_ids)

    @app.post("/runs", status_code=201)
    def create_run(body: RunRequest, idempotency_key: str = Header(min_length=1, max_length=200)):
        return service.create_run(body.quote_id, idempotency_key)

    @app.post("/runs/{run_id}/baseline-retry/quote")
    def baseline_retry_quote(run_id: str, body: CheckpointAccessRequest, request: Request):
        return service.baseline_retry_quote(run_id, credential(request))

    @app.post("/runs/{run_id}/after-retry/quote")
    def after_retry_quote(run_id: str, body: CheckpointAccessRequest, request: Request):
        return service.after_retry_quote(run_id, credential(request))

    @app.post("/runs/{run_id}/after-retry", status_code=201)
    def after_retry(run_id: str, body: BaselineRetryRequest, request: Request,
                    idempotency_key: str = Header(min_length=1, max_length=200)):
        return service.after_retry(run_id, body.quote_id, idempotency_key, credential(request))

    @app.post("/runs/{run_id}/baseline-retry", status_code=201)
    def baseline_retry(
        run_id: str,
        body: BaselineRetryRequest,
        request: Request,
        idempotency_key: str = Header(min_length=1, max_length=200),
    ):
        return service.baseline_retry(run_id, body.quote_id, idempotency_key, credential(request))

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, request: Request, response: Response):
        token = credential(request)
        result = service.get_run(run_id, token)
        with service.store.transaction() as db:
            run_token = db.execute("SELECT token FROM runs WHERE id=?", (run_id,)).fetchone()[0]
        if token and secrets.compare_digest(token, run_token):
            response.set_cookie(
                "tryfolk_run_" + run_id,
                run_token,
                httponly=True,
                samesite="strict",
                max_age=3600,
                path=f"/runs/{run_id}/evidence",
            )
        return result

    def owned_traces(run_id, token):
        from .trace_policy import HEX_ID
        from .telemetry import viewer_url
        from urllib.parse import urlparse

        with service.store.transaction() as db:
            service.authorize(db, run_id, token)
            events = db.execute(
                "SELECT persona_id,trace_url FROM events WHERE run_id=? AND trace_url IS NOT NULL ORDER BY id",
                (run_id,),
            ).fetchall()
        root = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
        traces = {}
        for persona_id, url in events:
            trace_id = urlparse(url).path.rsplit("/", 1)[-1]
            if not HEX_ID.fullmatch(trace_id) or trace_id in traces:
                continue
            receipt_path = root / f"{run_id}-{trace_id}-trace.json"
            receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
            traces[trace_id] = {
                "trace_id": trace_id, "persona_id": receipt.get("persona_id", persona_id),
                "viewer_url": viewer_url(trace_id),
                "delivery_flushed": receipt.get("delivery_flushed") is True,
                "finalized_readback": receipt.get("finalized_readback", "not_verified"),
                "readback_url": f"/runs/{run_id}/traces/{trace_id}",
                "payload_gates": receipt.get("payload_gates", []),
            }
        return traces

    def recorded_incidents(run_id, traces):
        """Expose only explicit, bounded official incident observations for this run."""
        from .trace_policy import HEX_ID
        from urllib.parse import urlparse
        from uuid import UUID

        if not HEX_ID.fullmatch(run_id):
            return []
        path = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")) / (
            run_id + "-neatlogs-incident.json"
        )
        try:
            if not path.is_file() or path.stat().st_size > 100_000:
                return []
            item = json.loads(path.read_text())
            if not isinstance(item, dict) or item.get("run_id") != run_id:
                return []
            incident_id = str(UUID(item.get("incident_id", "")))
            url = item.get("viewer_url", "")
            if not isinstance(url, str) or len(url) > 2000:
                return []
            parsed = urlparse(url)
            if (parsed.scheme != "https" or parsed.netloc != "app.neatlogs.com"
                    or incident_id not in (parsed.path + "?" + parsed.query)):
                return []
            if item.get("source") != "official_neatlogs_ui":
                return []
            scope = item.get("scope")
            if scope not in {"current_run", "prior_supporting_incident"}:
                return []
            trace_ids = item.get("trace_ids")
            if (not isinstance(trace_ids, list) or not 1 <= len(trace_ids) <= 64
                    or any(not isinstance(value, str) or not HEX_ID.fullmatch(value) for value in trace_ids)):
                return []
            if scope == "current_run" and not set(trace_ids).issubset(traces):
                return []
            if any(not isinstance(item.get(key), str) or not 0 < len(item[key]) <= 100
                   for key in ("status", "observed_at")):
                return []
        except (OSError, ValueError, TypeError, AttributeError):
            return []
        result = {key: item[key] for key in (
            "run_id", "source", "incident_id", "status", "scope", "viewer_url", "trace_ids", "observed_at",
        )}
        if scope == "current_run":
            with service.store.transaction() as db:
                result["provider"] = service.store.load(db, "runs", run_id)["provider"]
        elif item.get("provider") in {"local-fixture", "dodo"}:
            result["provider"] = item["provider"]
        else:
            return []
        for key in ("title", "evidence"):
            if isinstance(item.get(key), str) and len(item[key]) <= 2000:
                result[key] = item[key]
        # This is observed vendor state, never an inference from a local failure or repair.
        evidence = item.get("resolution_evidence")
        if isinstance(evidence, str) and len(evidence) <= 2000:
            result["resolution_evidence"] = evidence
        return [result]

    @app.get("/runs/{run_id}/traces")
    def trace_receipts(run_id: str, request: Request):
        traces = owned_traces(run_id, credential(request))
        return JSONResponse({
            "run_id": run_id, "traces": list(traces.values()),
            "incidents": recorded_incidents(run_id, traces),
        }, headers={"Cache-Control": "private, no-store"})

    @app.get("/runs/{run_id}/traces/{trace_id}")
    async def inspect_trace(run_id: str, trace_id: str, request: Request):
        traces = owned_traces(run_id, credential(request))
        if trace_id not in traces:
            raise DomainError(404, "trace_missing", "No recorded Neatlogs trace for this run.")
        from .telemetry import read_trace

        result = await read_trace(run_id, trace_id)
        result["incidents"] = [
            item for item in recorded_incidents(run_id, traces)
            if item["scope"] == "current_run" and trace_id in item["trace_ids"]
        ]
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})

    @app.get("/runs/{run_id}/evidence/{filename}")
    def screenshot(run_id: str, filename: str, request: Request):
        token = credential(request) or request.cookies.get("tryfolk_run_" + run_id)
        with service.store.transaction() as db:
            service.authorize(db, run_id, token)
            sources = [
                __import__("json").loads(row[0]).get("screenshot")
                for row in db.execute(
                    "SELECT data FROM events WHERE run_id=? AND kind='observation'", (run_id,)
                )
            ]
        root = Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence")).resolve()
        source = (root / filename).resolve()
        if (
            source.parent != root
            or not filename.startswith(run_id + "-")
            or source.suffix != ".png"
            or not source.is_file()
            or str(source) not in sources
        ):
            raise DomainError(404, "evidence_missing", "No recorded screenshot for this run.")
        return FileResponse(
            source,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, request: Request):
        return service.cancel(run_id, credential(request))

    @app.post("/runs/{run_id}/recover", status_code=202)
    def recover(run_id: str, request: Request):
        return service.recover_connection(run_id, credential(request))

    @app.get("/runs/{run_id}/findings")
    def findings(run_id: str, request: Request):
        return service.findings(run_id, credential(request))

    @app.post("/runs/{run_id}/repairs", status_code=202)
    def repair(run_id: str, body: RepairRequest, request: Request):
        return service.repair(run_id, body.finding_id, credential(request))

    @app.post("/runs/{run_id}/repairs/recover", status_code=202)
    def recover_repair(run_id: str, request: Request):
        return service.recover_repair(run_id, credential(request))

    @app.post("/runs/{run_id}/repairs/{repair_id}/checkpoint-access")
    def checkpoint_access(run_id: str, repair_id: str, body: CheckpointAccessRequest, request: Request):
        return service.finalize_checkpoint_access(run_id, repair_id, credential(request))

    @app.post("/runs/{run_id}/replay", status_code=201)
    def replay(
        run_id: str,
        body: ReplayRequest,
        request: Request,
        idempotency_key: str = Header(min_length=1, max_length=200),
    ):
        return service.replay(run_id, body.repair_id, idempotency_key, credential(request))

    frontend = Path(__file__).resolve().parents[2] / "frontend/dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
