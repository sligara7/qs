"""Auth group under the single-user key: whoami, scopes, and the API key routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from qs.api.auth import ALL_SCOPES, Principal, SingleKeyAuthenticator
from qs.api.deps import Services, get_principal, get_services
from qs.api.responses import fail

router = APIRouter(tags=["auth"])


def _principal_dict(principal: Principal, services: Services) -> dict[str, Any]:
    auth = services.authenticator
    api_keys: list[dict[str, Any]] = []
    if isinstance(auth, SingleKeyAuthenticator) and auth.api_key:
        api_keys.append(
            {
                "first_eight": auth.api_key[:8],
                "expiration_time": None,
                "note": "single-user key",
                "scopes": list(ALL_SCOPES),
            }
        )
    return {
        "uuid": principal.name,
        "type": "service" if principal.is_anonymous else "user",
        "identities": [{"id": principal.name, "provider": auth.mode}],
        "api_keys": api_keys,
        "sessions": [],
        "roles": [{"name": "admin" if not principal.is_anonymous else "user"}],
    }


@router.get("/auth/whoami")
async def whoami(
    principal: Principal = Depends(get_principal), services: Services = Depends(get_services)
) -> dict[str, Any]:
    return _principal_dict(principal, services)


@router.get("/auth/scopes")
async def scopes(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return {"scopes": sorted(principal.scopes)}


@router.post("/auth/apikey")
async def apikey_create(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return fail(
        "Creating API keys is not supported under the single-user key; set QSERVER_HTTP_SERVER_SINGLE_USER_API_KEY"
    )


@router.get("/auth/apikey")
async def apikey_get(
    principal: Principal = Depends(get_principal), services: Services = Depends(get_services)
) -> Any:
    auth = services.authenticator
    if isinstance(auth, SingleKeyAuthenticator) and auth.api_key:
        return {
            "first_eight": auth.api_key[:8],
            "expiration_time": None,
            "note": "single-user key",
            "scopes": list(ALL_SCOPES),
        }
    return fail("No API key is configured")


@router.delete("/auth/apikey")
async def apikey_delete(principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return fail("Deleting API keys is not supported under the single-user key")


@router.post("/auth/logout")
async def logout() -> dict[str, Any]:
    return {}


@router.get("/auth/principal")
async def principal_list(
    principal: Principal = Depends(get_principal), services: Services = Depends(get_services)
) -> Any:
    return [_principal_dict(principal, services)]


@router.get("/auth/principal/{uuid}")
async def principal_get(
    uuid: str, principal: Principal = Depends(get_principal), services: Services = Depends(get_services)
) -> Any:
    return _principal_dict(principal, services)


@router.post("/auth/principal/{uuid}/apikey")
async def principal_apikey(uuid: str, principal: Principal = Depends(get_principal)) -> dict[str, Any]:
    return fail("Per-principal API keys are not supported under the single-user key")


@router.post("/auth/session/refresh")
async def session_refresh() -> dict[str, Any]:
    return fail("Sessions are not supported under the single-user key")


@router.delete("/auth/session/revoke/{session_id}")
async def session_revoke(session_id: str) -> dict[str, Any]:
    return fail("Sessions are not supported under the single-user key")
