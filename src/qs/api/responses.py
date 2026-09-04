"""bluesky-httpserver response conventions.

Operation outcomes are 2xx with ``{"success": bool, "msg": str, ...}``; a refused or
unsupported operation is ``success=false`` with a message, never a 4xx. Authentication
failures are 401/403 and malformed requests 422.
"""

from __future__ import annotations

from typing import Any


def ok(msg: str = "", **fields: Any) -> dict[str, Any]:
    return {"success": True, "msg": msg, **fields}


def fail(msg: str, **fields: Any) -> dict[str, Any]:
    return {"success": False, "msg": msg, **fields}


def unsupported(what: str, why: str) -> dict[str, Any]:
    """The documented 'tolerated' or 'not supported' answer for a route qs does not implement."""
    return fail(f"{what} is not supported by this service: {why}", supported=False)
