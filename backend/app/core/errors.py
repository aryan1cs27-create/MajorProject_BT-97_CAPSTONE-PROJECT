"""Centralised API error type.

Routers raise ``APIError``; the FastAPI exception handler in ``app.main``
renders it as a consistent JSON body:

    {"error": {"code": "...", "status": 404, "message": "..."}}

Keeping errors in one place makes it straightforward to add authentication or
authorization checks later without touching every route.
"""

from __future__ import annotations

from fastapi import HTTPException


class APIError(HTTPException):
    """HTTP error with a machine-readable code."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})
        self.code = code


def not_found(resource: str, identifier: int | str) -> APIError:
    return APIError(404, "not_found", f"{resource} {identifier} was not found.")
