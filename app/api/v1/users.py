from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import audit, require_roles
from app.core.errors import ApiError, not_found
from app.core.security import hash_password
from app.db.session import get_db
from app.models import ParentLink, User
from app.schemas import Msg, Page, ParentLinkIn, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users (admin)"])
admin_only = require_roles("admin")


@router.get("", response_model=Page[UserOut])
def list_users(q: str | None = None, role: str | None = None, status: str | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               _: User = Depends(admin_only), db: Session = Depends(get_db)):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.name).like(like), func.lower(User.email).like(like)))
    if status:
        stmt = stmt.where(User.status == status)
    users = db.scalars(stmt.order_by(User.id)).all()
    if role:  # roles is JSON; filter in Python to stay DB-agnostic
        users = [u for u in users if role in (u.roles or [])]
    total = len(users)
    items = users[(page - 1) * page_size: page * page_size]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email.lower())):
        raise ApiError(409, "EMAIL_TAKEN", "Email already used", {"email": "taken"})
    u = User(name=body.name, email=body.email.lower(), password_hash=hash_password(body.password),
             roles=list(dict.fromkeys(body.roles)), language_pref=body.language_pref, institution=body.institution,
             class_level=body.class_level)
    db.add(u)
    db.flush()
    audit(db, admin, "user.create", "user", u.id, {"roles": u.roles}, request)
    db.commit()
    return u


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, _: User = Depends(admin_only), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise not_found("User")
    return u


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, admin: User = Depends(admin_only),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise not_found("User")
    data = body.model_dump(exclude_none=True)
    if u.id == admin.id and ("status" in data and data["status"] != "active" or
                             "roles" in data and "admin" not in data["roles"]):
        raise ApiError(400, "SELF_LOCKOUT", "You cannot disable yourself or remove your own admin role")
    if "email" in data:
        data["email"] = data["email"].lower()
        other = db.scalar(select(User).where(User.email == data["email"], User.id != u.id))
        if other:
            raise ApiError(409, "EMAIL_TAKEN", "Email already used", {"email": "taken"})
    if "password" in data:
        u.password_hash = hash_password(data.pop("password"))
        u.token_version += 1
    if data.get("status") == "disabled" or "roles" in data:
        u.token_version += 1  # force re-login so new roles/status apply immediately
    for k, v in data.items():
        setattr(u, k, v)
    audit(db, admin, "user.update", "user", u.id, {k: v for k, v in data.items()}, request)
    db.commit()
    return u


@router.post("/parent-links", response_model=Msg, status_code=201)
def link_parent(body: ParentLinkIn, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    p, s = db.get(User, body.parent_id), db.get(User, body.student_id)
    if not p or "parent" not in p.roles or not s or "student" not in s.roles:
        raise ApiError(400, "BAD_LINK", "parent_id must be a parent and student_id a student")
    if not db.scalar(select(ParentLink).where(ParentLink.parent_id == p.id, ParentLink.student_id == s.id)):
        db.add(ParentLink(parent_id=p.id, student_id=s.id))
        audit(db, admin, "user.parent_link", "user", s.id, {"parent_id": p.id})
        db.commit()
    return {"message": "Linked"}
