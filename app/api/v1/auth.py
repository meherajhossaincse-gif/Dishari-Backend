from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import audit, current_user, login_rate_limit, require_roles
from app.core.errors import ApiError
from app.core.security import (create_access_token, create_refresh_token, decode_token, hash_password,
                               hash_reset_token, new_reset_token, verify_password)
from app.db.session import get_db
from app.models import ParentLink, PasswordReset, User
from app.services.media import delete_avatar, save_avatar
from app.services.profile import PROFILE_FIELDS, new_link_code, user_stats
from app.schemas import (ChangePwIn, ForgotIn, LoginIn, MeUpdate, Msg, PendingOut, RefreshIn, RegisterIn, ResetIn, TokenOut,
                         UserOut)

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(u: User) -> dict:
    return {"access_token": create_access_token(u), "refresh_token": create_refresh_token(u), "token_type": "bearer",
            "expires_in": settings.ACCESS_TOKEN_MINUTES * 60, "user": UserOut.model_validate(u)}


STATUS_ERRORS = {
    "pending": ("ACCOUNT_PENDING", "Your teacher account is waiting for admin approval",
                "আপনার শিক্ষক অ্যাকাউন্ট এখনো admin-এর অনুমোদনের অপেক্ষায়"),
    "rejected": ("ACCOUNT_REJECTED", "Your account request was not approved",
                 "আপনার অ্যাকাউন্টের আবেদন অনুমোদিত হয়নি"),
    "disabled": ("ACCOUNT_DISABLED", "This account is disabled", "এই অ্যাকাউন্টটি বন্ধ করা আছে"),
}
PORTALS = {"staff": {"admin", "teacher", "moderator"}, "admin": {"admin"}, "student": {"student"}, "parent": {"parent"}}


def _portal_of(roles) -> str:
    r = set(roles)
    return "admin" if "admin" in r else "staff" if {"teacher", "moderator"} & r else (list(r) or ["student"])[0]


@router.post("/login", response_model=TokenOut, dependencies=[Depends(login_rate_limit)])
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    lu = u.locked_until if u else None
    if lu is not None and (lu if lu.tzinfo else lu.replace(tzinfo=timezone.utc)) > now:
        mins = max(1, int(((lu if lu.tzinfo else lu.replace(tzinfo=timezone.utc)) - now).total_seconds() // 60) + 1)
        raise ApiError(423, "ACCOUNT_LOCKED", f"Too many wrong passwords. Try again in {mins} minutes",
                       {"minutes": str(mins)})
    if not u or not verify_password(body.password, u.password_hash):
        if u:
            u.failed_logins = (u.failed_logins or 0) + 1
            if settings.LOCK_AFTER_FAILED and u.failed_logins >= settings.LOCK_AFTER_FAILED:
                u.locked_until, u.failed_logins = now + timedelta(minutes=settings.LOCK_MINUTES), 0
            db.commit()
        raise ApiError(401, "INVALID_CREDENTIALS", "Email or password is wrong")
    if u.status != "active":
        code, en, bn = STATUS_ERRORS.get(u.status, STATUS_ERRORS["disabled"])
        reason = (u.profile or {}).get("reject_reason") if u.status == "rejected" else None
        raise ApiError(403, code, en, {"message_bn": bn, **({"reason": reason} if reason else {})})
    if body.portal and not PORTALS[body.portal] & set(u.roles):
        right = _portal_of(u.roles)
        raise ApiError(403, "WRONG_PORTAL", f"This account belongs to the {right} panel", {"portal": right})
    u.failed_logins, u.locked_until, u.last_login_at = 0, None, now
    audit(db, u, "auth.login", "user", u.id, request=request)
    db.commit()
    return _tokens(u)


@router.post("/register", response_model=TokenOut | PendingOut, status_code=201)
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    """Self sign-up. Students and parents can log in at once (a parent can link a child with the link code).
    A TEACHER account is created as `pending` and can log in only after an admin approves it
    (`POST /users/{id}/approve`); the response is then `{pending: true, message}` instead of tokens."""
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise ApiError(409, "EMAIL_TAKEN", "An account with this email already exists", {"email": "taken"})
    child = None
    if body.role == "parent" and body.child_link_code:
        child = db.scalar(select(User).where(User.link_code == body.child_link_code.strip().upper()))
        if not child:
            raise ApiError(400, "BAD_LINK_CODE", "No student has this link code", {"child_link_code": "invalid"})
    teacher = body.role == "teacher"
    if teacher and not (body.institution or "").strip():
        raise ApiError(422, "VALIDATION_ERROR", "Institution is required for a teacher", {"institution": "required"})
    prof = {k: v.strip() for k, v in {"subjects": body.subjects, "classes_taught": body.classes_taught,
                                       "designation": body.designation}.items() if teacher and v and v.strip()}
    u = User(name=body.name, email=body.email.lower(), password_hash=hash_password(body.password), roles=[body.role],
             language_pref=body.language_pref, institution=body.institution, class_level=body.class_level,
             phone=body.phone, profile=prof, status="pending" if teacher else "active",
             link_code=new_link_code(db) if body.role == "student" else None)
    db.add(u)
    db.flush()
    if child:
        db.add(ParentLink(parent_id=u.id, student_id=child.id))
    audit(db, u, "auth.register", "user", u.id, {"role": body.role}, request=request)
    db.commit()
    if teacher:
        return PendingOut(message="Your request has been sent. You can log in after an admin approves it.",
                          message_bn="আপনার আবেদন পাঠানো হয়েছে। admin অনুমোদন দিলে লগইন করতে পারবেন।")
    return _tokens(u)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    try:
        p = decode_token(body.refresh_token)
    except jwt.PyJWTError:
        raise ApiError(401, "TOKEN_INVALID", "Refresh token invalid or expired, login again")
    if p.get("type") != "refresh":
        raise ApiError(401, "TOKEN_INVALID", "Not a refresh token")
    u = db.get(User, int(p["sub"]))
    if not u or u.status != "active" or p.get("tv") != u.token_version:
        raise ApiError(401, "TOKEN_INVALID", "Session revoked, login again")
    return _tokens(u)


@router.post("/logout", response_model=Msg)
def logout(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Revokes every token of this user (all devices)."""
    user.token_version += 1
    db.commit()
    return {"message": "Logged out"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: MeUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = body.model_dump(exclude_none=True)
    prof = data.pop("profile", None)
    for k, v in data.items():
        setattr(user, k, v)
    if prof is not None:
        allowed = set().union(*(PROFILE_FIELDS.get(r, set()) for r in user.roles)) | PROFILE_FIELDS["common"]
        bad = [k for k in prof if k not in allowed]
        if bad:
            raise ApiError(400, "UNKNOWN_PROFILE_FIELD", f"Not allowed for your role: {', '.join(bad)}")
        merged = dict(user.profile or {})
        for k, v in prof.items():
            if isinstance(v, str) and len(v) > 1000:
                raise ApiError(400, "TOO_LONG", f"{k} is too long", {k: "too long"})
            merged[k] = v
        user.profile = merged
    db.commit()
    return user


@router.post("/me/avatar", response_model=UserOut)
async def upload_avatar(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Profile photo (PNG/JPG/WEBP, max 3 MB). Cropped to a square, 400 px."""
    name = save_avatar(await file.read())
    delete_avatar(user.avatar_path)
    user.avatar_path = name
    db.commit()
    return user


@router.post("/me/link-code", response_model=UserOut)
def regenerate_link_code(user: User = Depends(require_roles("student")), db: Session = Depends(get_db)):
    """New parent-link code (the old one stops working; already linked parents stay linked)."""
    user.link_code = new_link_code(db)
    db.commit()
    return user


@router.get("/me/stats")
def my_stats(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Numbers for the profile page, per role."""
    return user_stats(db, user)


@router.post("/change-password", response_model=Msg)
def change_password(body: ChangePwIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise ApiError(400, "WRONG_PASSWORD", "Current password is wrong", {"current_password": "wrong"})
    user.password_hash = hash_password(body.new_password)
    user.token_version += 1
    db.commit()
    return {"message": "Password changed, please login again"}


@router.post("/forgot-password", status_code=202)
def forgot_password(body: ForgotIn, db: Session = Depends(get_db)):
    """Always 202 (does not reveal whether the email exists). Email sending is a deployment task
    (Sumaiya); in DEBUG the reset token is returned so the frontend flow can be tested."""
    u = db.scalar(select(User).where(User.email == body.email.lower()))
    resp = {"message": "If the email exists, a reset link has been sent"}
    if u:
        raw, h = new_reset_token()
        db.add(PasswordReset(user_id=u.id, token_hash=h,
                             expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.RESET_TOKEN_MINUTES)))
        db.commit()
        # TODO(Sumaiya): send f"{FRONTEND_URL}/reset-password?token={raw}" by email (SMTP) here.
        if settings.DEBUG:
            resp["debug_reset_token"] = raw
    return resp


@router.post("/reset-password", response_model=Msg)
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    pr = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_reset_token(body.token),
                                               PasswordReset.used.is_(False)))
    exp = pr.expires_at if pr else None
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if not pr or exp < datetime.now(timezone.utc):
        raise ApiError(400, "RESET_TOKEN_INVALID", "Reset link is invalid or expired")
    u = db.get(User, pr.user_id)
    u.password_hash = hash_password(body.new_password)
    u.token_version += 1
    pr.used = True
    db.commit()
    return {"message": "Password updated, please login"}
