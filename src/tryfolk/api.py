from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime, timedelta, timezone
import secrets
import os
from pathlib import Path

from .config import Settings
from .service import Service, DomainError
from .connection_api import connection_router


class QuoteRequest(BaseModel):
    app_url: str
    persona_count: int = Field(ge=1, le=1000, strict=True)


class RunRequest(BaseModel):
    quote_id: str


class OwnerLogin(BaseModel):
    secret: str


class RepairRequest(BaseModel):
    finding_id: str


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repair_id: str


def bearer(value):
    return value[7:] if value and value.startswith("Bearer ") else None


def create_app(settings=None):
    app = FastAPI(title="Tryfolk", version="0.1.0")
    service = Service(settings or Settings.from_env())
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
                "status": "processing",
                "detail": "Real doctor trace visible, backend processing; finalization/readback not yet verified.",
            },
        }
        manifest = Path("runtime/integration-readiness.json")
        if manifest.exists():
            integrations.update(__import__("json").loads(manifest.read_text())["integrations"])
        return {
            "integrations": integrations,
            "gates": list(service.settings.gates),
            "target_url": next(
                (t.url for t in service.settings.targets if t.provider == "dodo" and t.mode == "broken"),
                next((t.url for t in service.settings.targets if t.fault == "paid-no-access"), None),
            ),
            "max_personas": service.settings.max_personas,
            "cfo_plan_status": "imported" if service.settings.cfo_plan_path else "missing",
        }

    @app.post("/quotes")
    def quote(body: QuoteRequest):
        return service.quote(body.app_url, body.persona_count)

    @app.post("/runs", status_code=201)
    def create_run(body: RunRequest, idempotency_key: str = Header(min_length=1, max_length=200)):
        return service.create_run(body.quote_id, idempotency_key)

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
