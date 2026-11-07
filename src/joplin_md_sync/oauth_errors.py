"""Request-local OAuth errors, safe to expose without upstream response data."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthFault:
    status: int
    error: str
    description: str


request_faults: ContextVar[list[OAuthFault] | None] = ContextVar(
    "oauth_request_faults", default=None
)


def record_fault(status: int, error: str, description: str) -> None:
    faults = request_faults.get()
    if faults is not None and not faults:
        faults.append(OAuthFault(status, error, description))
