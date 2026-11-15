import time
from collections import defaultdict, deque

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import decode_token
from app.db.session import get_db
from app.models import AuditLog, User

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)) -> User:
    if not creds:
        raise ApiError(401, "UNAUTHORIZED", "Login required")
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "TOKEN_EXPIRED", "Access token expired, call /v1/auth/refresh")
    except jwt.PyJWTError:
        raise ApiError(401, "TOKEN_INVALID", "Invalid token")
    if payload.get("type") != "access":
        raise ApiError(401, "TOKEN_INVALID", "Not an access token")
    user = db.get(User, int(payload["sub"]))
    if not user or user.status != "active" or payload.get("tv") != user.token_version:
        raise ApiError(401, "TOKEN_INVALID", "User disabled or token revoked")
    return user


def require_roles(*roles: str):
    def dep(user: User = Depends(current_user)) -> User:
        if not set(roles) & set(user.roles):
            raise ApiError(403, "FORBIDDEN", f"Requires role: {' or '.join(roles)}")
        return user
    return dep


# role groups used across routers
STAFF = ("admin", "teacher", "moderator")
REVIEWERS = ("admin", "moderator")


# ----------------------------------------------------------- simple in-memory rate limiter (login)
_hits: dict[str, deque] = defaultdict(deque)


def login_rate_limit(request: Request):
    if settings.ENV == "test" or not settings.LOGIN_RATE_LIMIT:
        return
    key = request.client.host if request.client else "?"
    q, now = _hits[key], time.time()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.LOGIN_RATE_LIMIT_PER_MINUTE:
        raise ApiError(429, "RATE_LIMITED", "Too many login attempts, wait a minute")
    q.append(now)


def audit(db: Session, actor: User | None, action: str, entity: str, entity_id=None, details: dict | None = None,
          request: Request | None = None):
    db.add(AuditLog(actor_id=actor.id if actor else None, action=action, entity=entity,
                    entity_id=str(entity_id) if entity_id is not None else None, details=details or {},
                    ip=(request.client.host if request and request.client else None)))
