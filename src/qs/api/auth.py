"""Caller identification (``cap:http-auth``, ``dec:http-auth-single-key``).

An :class:`Authenticator` resolves a request's credential to a :class:`Principal` or
refuses. The first implementation is the single-user API key from
``QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY`` with ``QSERVER_HTTP_SERVER_ALLOW_ANONYMOUS_ACCESS``
to bypass on trusted networks — bluesky-httpserver's convention, so finch's
``Authorization: Apikey <key>`` header, ``?api_key=`` query and ``Bearer`` token all work.
Per-client keys with roles are the planned successor behind the same protocol.
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

ALL_SCOPES = (
    "read:status",
    "read:queue",
    "write:queue",
    "read:history",
    "write:history",
    "read:resources",
    "read:config",
    "read:monitor",
    "read:console",
    "read:lock",
    "write:lock",
    "write:permissions",
    "read:testing",
    "write:testing",
    "user:apikeys",
    "admin:apikeys",
    "admin:read:principals",
    "admin:metrics",
    "write:plans",
    "write:execute",
    "write:scripts",
    "write:manager",
    "write:unlock",
    "write:apikeys",
)


@dataclass(frozen=True)
class Principal:
    name: str
    user_group: str = "primary"
    scopes: frozenset[str] = field(default_factory=lambda: frozenset(ALL_SCOPES))
    identities: tuple[dict[str, str], ...] = ()

    @property
    def is_anonymous(self) -> bool:
        return self.name == "anonymous"


@dataclass(frozen=True)
class Credential:
    """What a request presented, in whichever form finch's client sends it."""

    scheme: str  # "apikey" | "bearer" | "query" | "message" | ""
    value: str

    @classmethod
    def none(cls) -> Credential:
        return cls("", "")

    @classmethod
    def from_headers_and_query(cls, headers: Mapping[str, str], query: Mapping[str, str]) -> Credential:
        auth = headers.get("authorization") or headers.get("Authorization") or ""
        if auth:
            scheme, _, value = auth.partition(" ")
            scheme = scheme.strip().lower()
            value = value.strip()
            if scheme == "apikey":
                return cls("apikey", value)
            if scheme == "bearer":
                return cls("bearer", value)
        if query.get("api_key"):
            return cls("query", str(query["api_key"]))
        if query.get("access_token"):
            return cls("bearer", str(query["access_token"]))
        return cls.none()


class AuthenticationError(Exception):
    """The credential was missing or wrong; carries the HTTP status to return."""

    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


@runtime_checkable
class Authenticator(Protocol):
    """Resolve a credential to a principal, or raise :class:`AuthenticationError`."""

    @property
    def mode(self) -> str:
        """Human-readable mode for /api/config/get and logs."""
        ...

    def authenticate(self, credential: Credential) -> Principal: ...


class SingleKeyAuthenticator:
    """One shared API key. With ``allow_anonymous`` every request is the anonymous principal."""

    def __init__(
        self, api_key: str | None, *, allow_anonymous: bool = False, generated: bool = False
    ) -> None:
        if not allow_anonymous and not api_key:
            raise ValueError("An API key is required unless anonymous access is allowed")
        self._key = api_key or ""
        self._allow_anonymous = allow_anonymous
        self.generated = generated
        self._principal = Principal(name="single_user")
        self._anonymous = Principal(name="anonymous")

    @property
    def mode(self) -> str:
        return "UNAUTHENTICATED_SINGLE_USER" if self._allow_anonymous else "SINGLE_USER_API_KEY"

    @property
    def api_key(self) -> str:
        return self._key

    def authenticate(self, credential: Credential) -> Principal:
        if self._allow_anonymous:
            return self._anonymous
        if not credential.value:
            raise AuthenticationError(
                "Authentication required: pass 'Authorization: Apikey <key>' or ?api_key="
            )
        if hmac.compare_digest(credential.value, self._key):
            return self._principal
        raise AuthenticationError("Invalid API key", status_code=401)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> SingleKeyAuthenticator:
        allow = environ.get("QSERVER_HTTP_SERVER_ALLOW_ANONYMOUS_ACCESS", "0").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        key = environ.get("QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY")
        generated = False
        if not key and not allow:
            key, generated = secrets.token_hex(32), True
        return cls(key, allow_anonymous=allow, generated=generated)
