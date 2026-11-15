"""Admin-editable runtime settings. Stored in the `settings` table and applied on top of .env at startup and
on every save, so the rest of the code keeps reading `settings.X`."""
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Setting

# key -> (settings attribute, type, min, max)
EDITABLE = {
    "board_match_threshold": ("BOARD_MATCH_THRESHOLD", float, 0.5, 1.0),
    "review_threshold": ("REVIEW_THRESHOLD", float, 0.1, 0.99),
    "max_upload_rows": ("MAX_UPLOAD_ROWS", int, 10, 20000),
    "max_upload_mb": ("MAX_UPLOAD_MB", int, 1, 50),
    "access_token_minutes": ("ACCESS_TOKEN_MINUTES", int, 5, 720),
    "refresh_token_days": ("REFRESH_TOKEN_DAYS", int, 1, 60),
    "lock_after_failed": ("LOCK_AFTER_FAILED", int, 0, 20),
    "login_rate_limit": ("LOGIN_RATE_LIMIT", bool, None, None),
}
ROW_KEY = "system"


def current() -> dict:
    return {k: getattr(settings, attr) for k, (attr, *_r) in EDITABLE.items()}


def validate(body: dict) -> dict:
    out, errors = {}, {}
    for k, v in body.items():
        if k not in EDITABLE or v is None:
            continue
        _attr, typ, lo, hi = EDITABLE[k]
        try:
            v = typ(v) if typ is not bool else bool(v)
        except (TypeError, ValueError):
            errors[k] = "invalid"
            continue
        if lo is not None and not (lo <= v <= hi):
            errors[k] = f"must be between {lo} and {hi}"
            continue
        out[k] = v
    if errors:
        from app.core.errors import ApiError
        raise ApiError(422, "VALIDATION_ERROR", "Some settings are out of range", errors)
    return out


def apply(values: dict):
    for k, v in values.items():
        if k in EDITABLE:
            setattr(settings, EDITABLE[k][0], v)


def load(db: Session):
    row = db.get(Setting, ROW_KEY)
    if row and isinstance(row.value, dict):
        apply({k: v for k, v in row.value.items() if k in EDITABLE})


def save(db: Session, body: dict) -> dict:
    vals = validate(body)
    row = db.get(Setting, ROW_KEY)
    merged = {**((row.value or {}) if row else {}), **vals}
    if row:
        row.value = merged
    else:
        db.add(Setting(key=ROW_KEY, value=merged))
    db.commit()
    apply(merged)
    return current()
