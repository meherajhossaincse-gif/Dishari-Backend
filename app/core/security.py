import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


def _token(sub: int, kind: str, ttl: timedelta, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(sub), "type": kind, "iat": now, "exp": now + ttl, "jti": secrets.token_hex(8)}
    payload.update(extra or {})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user) -> str:
    return _token(user.id, "access", timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
                  {"roles": user.roles, "tv": user.token_version})


def create_refresh_token(user) -> str:
    return _token(user.id, "refresh", timedelta(days=settings.REFRESH_TOKEN_DAYS), {"tv": user.token_version})


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def new_reset_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
