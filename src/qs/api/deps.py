"""What the API needs, handed in by the composition root (dependency injection)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Depends, HTTPException, Request

from qs.api.auth import Authenticator, Credential, Principal
from qs.api.console import ConsoleCapture
from qs.api.status import StatusReporter
from qs.api.streams import EventBroadcaster
from qs.devices.service import DeviceDefinitionService
from qs.engine.events import EventBus
from qs.engine.host import EngineHost
from qs.errors import ErrorCode
from qs.queue.service import QueueService
from qs.registry import Registry
from qs.sequencer import Sequencer

logger = logging.getLogger(__name__)


@dataclass
class Services:
    host: EngineHost
    registry: Registry
    queue: QueueService
    sequencer: Sequencer
    events: EventBus
    authenticator: Authenticator
    status: StatusReporter
    console: ConsoleCapture
    broadcaster: EventBroadcaster
    devices: DeviceDefinitionService | None = None
    config: dict[str, Any] = field(default_factory=dict)
    shutdown_callback: Any = None  # called by POST /api/manager/stop


def get_services(request: Request) -> Services:
    return request.app.state.services  # type: ignore[no-any-return]


def get_principal(request: Request, services: Services = Depends(get_services)) -> Principal:
    credential = Credential.from_headers_and_query(request.headers, request.query_params)
    try:
        return services.authenticator.authenticate(credential)
    except Exception as exc:  # AuthenticationError or anything else the authenticator raises
        status_code = getattr(exc, "status_code", 401)
        client = request.client.host if request.client else "?"
        logger.warning(
            "[%s] %s %s from %s refused: %s", ErrorCode.AUTH, request.method, request.url.path, client, exc
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


def require_scope(scope: str):  # type: ignore[no-untyped-def]
    def check(principal: Principal = Depends(get_principal)) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"Missing scope {scope!r}")
        return principal

    return check
