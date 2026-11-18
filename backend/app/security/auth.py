"""Demo authentication.

Bearer tokens are `<user_id>.<hmac>` signed with FLEETPILOT_AUTH_SECRET. The server resolves the
token to a stored `User`, and the tenant (`company_id`) comes from that row — never from the request
body, query string or the LLM. This stands in for a real IdP (OIDC/SAML); swapping it only changes
`resolve_token`.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.rls import bind_tenant
from app.db.session import get_db
from app.models import User
from app.security.context import TenantContext


def sign(user_id: str) -> str:
    mac = hmac.new(get_settings().auth_secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()[:40]
    return f"{user_id}.{mac}"


def resolve_token(session: Session, token: str) -> TenantContext:
    user_id, _, mac = token.rpartition(".")
    if not user_id or not hmac.compare_digest(sign(user_id), token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown user")
    return TenantContext(company_id=user.company_id, user_id=user.id, role=user.role,  # type: ignore[arg-type]
                         display_name=user.display_name)


def current_tenant(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> TenantContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    ctx = resolve_token(db, authorization.split(" ", 1)[1].strip())
    # Defence in depth: on PostgreSQL every later transaction of this request runs under row-level security
    # restricted to this tenant (no-op on SQLite). See app/db/rls.py.
    bind_tenant(db, ctx.company_id)
    return ctx


def require(permission: str):
    def dep(ctx: TenantContext = Depends(current_tenant)) -> TenantContext:
        if not ctx.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role '{ctx.role}' lacks '{permission}'")
        return ctx
    return dep


class RateLimiter:
    """In-process sliding window (per user). Adequate for a single API instance; a shared store
    would be needed for horizontal scaling."""

    def __init__(self) -> None:
        self.hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded; try again shortly")
        q.append(now)


rate_limiter = RateLimiter()
