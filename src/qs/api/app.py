"""FastAPI application factory.

``create_app(services)`` wires the routers under ``/api`` and stores the injected services on
``app.state``. Authentication failures return 401/403 JSON; every other error inside a route
is reported as ``success=false`` by the route itself, so an unexpected exception here is a bug
and surfaces as a 500 with a JSON body rather than touching the engine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from qs import __version__
from qs.api.deps import Services
from qs.api.routers import auth, engine, misc, qs_devices, queue, resources, status, ws

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    services: Services, *, allow_origins: list[str] | None = None, smoke_page: bool = True
) -> FastAPI:
    app = FastAPI(
        title="qs — Bluesky queue service (bluesky-httpserver compatible)",
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.services = services

    if allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    for r in (
        status.router,
        queue.router,
        engine.router,
        resources.router,
        misc.router,
        auth.router,
        ws.router,
        qs_devices.router,
    ):
        app.include_router(r, prefix="/api")

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error in %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"success": False, "msg": f"Internal error: {type(exc).__name__}: {exc}"},
        )

    if smoke_page:

        @app.get("/", include_in_schema=False)
        async def smoke() -> Any:
            return FileResponse(STATIC_DIR / "smoke.html")

    return app
