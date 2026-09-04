"""Read a request payload the way bluesky-httpserver clients send it.

httpserver routes take a JSON body on GET and POST alike (finch's client sends it), but a
browser GET may only carry query parameters; both are merged here.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request


async def read_payload(request: Request) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in request.query_params.items():
        if key in {"api_key", "access_token"}:
            continue
        payload[key] = _coerce(value)
    body = await request.body()
    if body:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            payload.update(data)
    return payload


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value
