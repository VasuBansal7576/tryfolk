"""Owner session HTTP boundary for source registration, without execution."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .connections import Connections


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_url: str = Field(min_length=1, max_length=2048)


def connection_router(settings, store, authorize):
    router = APIRouter()
    connections = Connections(settings, store)

    @router.get("/connections")
    def list_connections(request: Request, response: Response):
        authorize(request)
        response.headers["Cache-Control"] = "private, no-store"
        return connections.list()

    @router.post("/connections")
    def register_connection(body: ConnectionRequest, request: Request, response: Response):
        authorize(request)
        response.headers["Cache-Control"] = "private, no-store"
        return connections.register(body.app_url)

    return router
