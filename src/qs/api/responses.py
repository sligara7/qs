"""bluesky-httpserver response conventions.

Operation outcomes are 2xx with ``{"success": bool, "msg": str, ...}``; a refused or
unsupported operation is ``success=false`` with a message, never a 4xx. Authentication
failures are 401/403 and malformed requests 422.
"""

from __future__ import annotations

from typing import Any

from qs.errors import ErrorCode

_DEFAULT_CODES = {
    "QueueError": ErrorCode.ITEM_INVALID,
    "RegistryError": ErrorCode.ITEM_INVALID,
    "SequencerError": ErrorCode.QUEUE_REFUSED,
    "EngineHostError": ErrorCode.ENGINE_BUSY,
    "DeviceDefinitionError": ErrorCode.ITEM_INVALID,
}


def ok(msg: str = "", **fields: Any) -> dict[str, Any]:
    return {"success": True, "msg": msg, **fields}


def fail(msg: str, *, code: ErrorCode | str | None = None, **fields: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"success": False, "msg": msg, **fields}
    if code:
        body["code"] = str(code)
    return body


def fail_from(exc: Exception, default: ErrorCode | None = None) -> dict[str, Any]:
    """A failure body for a service exception; the code comes from the exception when it has one."""
    code = getattr(exc, "code", None) or default or _DEFAULT_CODES.get(type(exc).__name__, ErrorCode.INTERNAL)
    return fail(str(exc), code=code)


def unsupported(what: str, why: str) -> dict[str, Any]:
    """The documented 'tolerated' or 'not supported' answer for a route qs does not implement."""
    msg = f"{what} is not supported by this service: {why}"
    return fail(msg, code=ErrorCode.UNSUPPORTED, supported=False)
