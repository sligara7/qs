"""The HTTP API: a bluesky-httpserver-compatible FastAPI application (``cmp:http-api``).

Thin by design: every route translates HTTP into calls on the services it receives through
:class:`qs.api.deps.Services`, and formats the answer the way bluesky-httpserver does
(``{"success": bool, "msg": str, ...}``), so finch and blop work unchanged.
"""

from qs.api.app import create_app
from qs.api.deps import Services

__all__ = ["Services", "create_app"]
