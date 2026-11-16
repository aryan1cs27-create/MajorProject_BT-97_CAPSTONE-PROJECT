"""Lightweight local API-token authentication (Phase 5.1).

One operator, one token: there are no accounts, sessions, cookies or roles.
The token is read from ``API_TOKEN`` (never hardcoded) and may be sent as
either header:

    Authorization: Bearer <token>
    X-API-Token: <token>

Behaviour
---------
* ``API_TOKEN`` **set**  -> every protected route requires a matching token;
  a missing header returns ``401 unauthorized`` and a wrong one returns
  ``401 invalid_token``.
* ``API_TOKEN`` **unset** -> authentication is disabled (local development
  mode). ``app.main`` logs a warning at startup and the README documents it.

The comparison uses :func:`hmac.compare_digest` so a wrong token cannot be
recovered through timing differences, and error messages never echo the
configured value.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, Header

from app.core.config import get_settings
from app.core.errors import APIError

logger = logging.getLogger("matchwise.security")

#: Name of the environment variable that carries the token (documented only).
API_TOKEN_ENV = "API_TOKEN"


def _provided_token(
    authorization: str | None,
    x_api_token: str | None,
) -> str | None:
    if x_api_token and x_api_token.strip():
        return x_api_token.strip()
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    return None


def require_api_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_token: Annotated[str | None, Header()] = None,
) -> None:
    """FastAPI dependency guarding the data routes.

    Routers declare it once (``dependencies=[Depends(require_api_token)]``), so
    every current and future route on them is covered.
    """
    expected = get_settings().api_token
    if not expected:
        return  # authentication disabled — local development mode

    provided = _provided_token(authorization, x_api_token)
    if not provided:
        raise APIError(
            401,
            "unauthorized",
            "Missing API token. Send 'Authorization: Bearer <token>' or "
            "'X-API-Token: <token>'.",
        )
    if not hmac.compare_digest(provided, expected):
        raise APIError(401, "invalid_token", "The API token is not valid.")

    return None


#: Convenience alias used by routers.
ApiAuth = Depends(require_api_token)
