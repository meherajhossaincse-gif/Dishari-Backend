"""Teacher approvals, system settings, student groups, teacher feedback, class reports, OMR overview and the
student/parent home summary."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import STAFF, audit, current_user, require_roles
from app.core.errors import ApiError, forbidden, not_found
from app.core.security import new_reset_token
from app.db.session import get_db
from app.models import (Feedback, GroupMember, OmrResult, Paper, ParentLink, PasswordReset, PracticeSession,
                        StudentGroup, Test, TestAttempt, User)
from app.schemas import UserOut
from app.services import classroom, system_settings
from app.services.analysis import mastery
from app.services.taxonomy import taxonomy

router = APIRouter(tags=["school"])
staff = require_roles(*STAFF)
admin_only = require_roles("admin")


# ================================================================== teacher sign-up approval (admin)
@router.get("/admin/pending-teachers", response_model=list[UserOut])
def pending_teachers(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    return db.scalars(select(User).where(User.status == "pending").order_by(User.created_at)).all()


class RejectIn(BaseModel):
    reason: str | None = Field(None, max_length=300)


def _pending(db, uid: int) -> User:
    u = db.get(User, uid)
    if not u:
        raise not_found("User")
    if u.status not in ("pending", "rejected"):
        raise ApiError(409, "NOT_PENDING", "This account is not waiting for approval")
    return u


@router.post("/users/{uid}/approve", response_model=UserOut)
def approve_user(uid: int, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    u = _pending(db, uid)
    u.status = "active"
    u.profile = {k: v for k, v in (u.profile or {}).items() if k != "reject_reason"}
    audit(db, admin, "user.approve", "user", u.id, {"email": u.email}, request=request)
    db.commit()
    return u


@router.post("/users/{uid}/reject", response_model=UserOut)
def reject_user(uid: int, body: RejectIn, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    u = _pending(db, uid)
    u.status = "rejected"
    u.profile = {**(u.profile or {}), **({"reject_reason": body.reason.strip()} if body.reason and body.reason.strip() else {})}
    audit(db, admin, "user.reject", "user", u.id, {"email": u.email, "reason": body.reason}, request=request)
    db.commit()
    return u


@router.post("/users/{uid}/send-reset")
def admin_send_reset(uid: int, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Admin starts a password reset for a user (same link as 'forgot password')."""
    u = db.get(User, uid)
    if not u:
        raise not_found("User")
    raw, h = new_reset_token()
    db.add(PasswordReset(user_id=u.id, token_hash=h,
                         expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.RESET_TOKEN_MINUTES)))
    audit(db, admin, "user.send_reset", "user", u.id, request=request)
    db.commit()
    out = {"message": "Reset link created"}
    if settings.DEBUG:
        out["debug_reset_token"] = raw
    return out


@router.post("/users/{uid}/unlock", response_model=UserOut)
def unlock_user(uid: int, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    u = db.get(User, uid)
    if not u:
        raise not_found("User")
    u.locked_until, u.failed_logins = None, 0
    db.commit()
    return u


# ================================================================== system settings (admin)
@router.get("/admin/settings")
def get_settings(_: User = Depends(admin_only)):
    return system_settings.current()


@router.put("/admin/settings")
def put_settings(body: dict, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    """Thresholds, upload limits and security. Keys: board_match_threshold, review_threshold, max_upload_rows,
    max_upload_mb, access_token_minutes, refresh_token_days, lock_after_failed, login_rate_limit."""
    out = system_settings.save(db, body)
    audit(db, admin, "settings.update", "settings", "system", body, request=request)
    db.commit()
    return out


# ================================================================== groups (teacher)
class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    class_level: str | None = Field(None, max_length=20)
    section: str | None = Field(None, max_length=20)
    note: str | None = None
    student_ids: list[int] | None = None
    add_matching: bool = False  # add every student of class_level (+ section) automatically


class GroupPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    class_level: str | None = None
    section: str | None = None
    note: str | None = None


class MembersIn(BaseModel):
    student_ids: list[int]


def _students(db) -> list[User]:
    return [u for u in db.scalars(select(User).where(User.status == "active").order_by(User.name)) if "student" in (u.roles or [])]


def _matching(db, cls: str | None, sec: str | None) -> list[int]:
    return [u.id for u in _students(db) if (not cls or (u.class_level or "").strip() == cls.strip())
            and (not sec or str((u.profile or {}).get("section") or "").strip().lower() == sec.strip().lower())]


def _gout(db, g: StudentGroup, members: bool = False) -> dict:
    ids = classroom.group_student_ids(db, g.id)
    out = {"id": g.id, "name": g.name, "class_level": g.class_level, "section": g.section, "note": g.note,
           "members_count": len(ids), "created_at": g.created_at}
    if members:
        us = db.scalars(select(User).where(User.id.in_(list(ids) or [0])).order_by(User.name)).all()
        out["members"] = [{"id": u.id, "name": u.name, "email": u.email, "class_level": u.class_level,
                           "section": (u.profile or {}).get("section"), "roll": (u.profile or {}).get("roll"),
                           "avatar_url": u.avatar_url} for u in us]
    return out


def _own_group(db, gid: int, user: User) -> StudentGroup:
    g = db.get(StudentGroup, gid)
    if not g or (g.owner_id != user.id and "admin" not in user.roles):
        raise not_found("Group")
    return g


@router.get("/groups")
def list_groups(user: User = Depends(staff), db: Session = Depends(get_db)):
    q = select(StudentGroup).order_by(StudentGroup.name)
    gs = db.scalars(q if "admin" in user.roles else q.where(StudentGroup.owner_id == user.id)).all()
    return [_gout(db, g) for g in gs]


@router.post("/groups", status_code=201)
def create_group(body: GroupIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    g = StudentGroup(owner_id=user.id, name=body.name.strip(), class_level=body.class_level or None,
                     section=body.section or None, note=body.note)
    db.add(g)
    db.flush()
    ids = set(body.student_ids or []) | (set(_matching(db, body.class_level, body.section)) if body.add_matching else set())
    valid = {u.id for u in _students(db)}
    for sid in ids & valid:
        db.add(GroupMember(group_id=g.id, student_id=sid))
    db.commit()
    return _gout(db, g, True)


@router.get("/groups/{gid}")
def get_group(gid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    return _gout(db, _own_group(db, gid, user), True)


@router.patch("/groups/{gid}")
def update_group(gid: int, body: GroupPatch, user: User = Depends(staff), db: Session = Depends(get_db)):
    g = _own_group(db, gid, user)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(g, k, v)
    db.commit()
    return _gout(db, g, True)


@router.put("/groups/{gid}/members")
def set_members(gid: int, body: MembersIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    g = _own_group(db, gid, user)
    valid = {u.id for u in _students(db)}
    want = set(body.student_ids) & valid
    have = classroom.group_student_ids(db, g.id)
    for m in db.scalars(select(GroupMember).where(GroupMember.group_id == g.id, GroupMember.student_id.in_(list(have - want) or [0]))):
        db.delete(m)
    for sid in want - have:
        db.add(GroupMember(group_id=g.id, student_id=sid))
    db.commit()
    return _gout(db, g, True)


@router.delete("/groups/{gid}", status_code=204)
def delete_group(gid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    db.delete(_own_group(db, gid, user))
    db.commit()


@router.get("/students/search")
def search_students(q: str | None = None, class_level: str | None = None, section: str | None = None,
                    _: User = Depends(staff), db: Session = Depends(get_db)):
    """Students a teacher can add to a group (name / email / roll search, optional class + section)."""
    out = []
    ql = (q or "").strip().lower()
    for u in _students(db):
        p = u.profile or {}
        if class_level and (u.class_level or "").strip() != class_level.strip():
            continue
        if section and str(p.get("section") or "").strip().lower() != section.strip().lower():
            continue
        if ql and ql not in f"{u.name} {u.email} {p.get('roll') or ''}".lower():
            continue
        out.append({"id": u.id, "name": u.name, "email": u.email, "class_level": u.class_level,
                    "section": p.get("section"), "roll": p.get("roll"), "institution": u.institution})
    return out[:200]


# ================================================================== feedback
class FeedbackIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    student_ids: list[int] | None = None
    group_id: int | None = None
    context: str | None = Field(None, max_length=200)


def _fout(f: Feedback, names: dict) -> dict:
    return {"id": f.id, "from_id": f.from_id, "from_name": names.get(f.from_id), "student_id": f.student_id,
            "student_name": names.get(f.student_id), "context": f.context, "message": f.message,
            "read": f.read_at is not None, "created_at": f.created_at}


def _names(db, ids) -> dict:
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(list(set(ids)) or [0])))}


@router.post("/feedback", status_code=201)
def send_feedback(body: FeedbackIn, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    ids = set(body.student_ids or [])
    if body.group_id:
        _own_group(db, body.group_id, user)
        ids |= classroom.group_student_ids(db, body.group_id)
    valid = {u.id for u in _students(db)}
    ids &= valid
    if not ids:
        raise ApiError(422, "NO_STUDENTS", "Choose at least one student or a group", {"student_ids": "required"})
    for sid in ids:
        db.add(Feedback(from_id=user.id, student_id=sid, message=body.message.strip(), context=body.context))
    audit(db, user, "feedback.send", "feedback", None, {"count": len(ids)}, request=request)
    db.commit()
    return {"sent": len(ids)}


@router.get("/feedback/sent")
def sent_feedback(limit: int = Query(50, le=200), user: User = Depends(staff), db: Session = Depends(get_db)):
    fs = db.scalars(select(Feedback).where(Feedback.from_id == user.id).order_by(Feedback.id.desc()).limit(limit)).all()
    names = _names(db, [f.student_id for f in fs] + [user.id])
    return [_fout(f, names) for f in fs]


def _can_view(db, viewer: User, sid: int):
    if viewer.id == sid or {"admin", "teacher", "moderator"} & set(viewer.roles):
        return
    if "parent" in viewer.roles and db.scalar(select(ParentLink).where(ParentLink.parent_id == viewer.id, ParentLink.student_id == sid)):
        return
    raise forbidden("Not allowed to view this student")


@router.get("/students/{sid}/feedback")
def student_feedback(sid: int, limit: int = Query(20, le=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _can_view(db, user, sid)
    fs = db.scalars(select(Feedback).where(Feedback.student_id == sid).order_by(Feedback.id.desc()).limit(limit)).all()
    if user.id == sid:
        now = datetime.now(timezone.utc)
        for f in fs:
            f.read_at = f.read_at or now
        db.commit()
    names = _names(db, [f.from_id for f in fs] + [sid])
    return [_fout(f, names) for f in fs]


# ================================================================== class report
@router.get("/class-report/sources")
def report_sources(user: User = Depends(staff), db: Session = Depends(get_db)):
    """What a teacher can make a class report for: their online tests, their papers with OMR sheets, their groups."""
    tests = [{"id": t.id, "title": t.title, "class_level": t.class_level, "section": t.section,
              "submitted": db.scalar(select(func.count(TestAttempt.id)).where(TestAttempt.test_id == t.id, TestAttempt.submitted_at.is_not(None)))}
             for t in classroom.owned_tests(db, user)]
    papers = []
    for p in classroom.owned_papers(db, user):
        n = db.scalar(select(func.count(OmrResult.id)).where(OmrResult.paper_id == p.id))
        if n:
            papers.append({"id": p.id, "title": p.title, "omr_sheets": n})
    groups = list_groups(user, db)
    return {"tests": tests, "papers": papers, "groups": groups}


@router.get("/class-report")
def class_report(test_id: int | None = None, paper_id: int | None = None, group_id: int | None = None,
                 format: str = Query("json", pattern="^(json|html)$"), lang: str = Query("bn", pattern="^(bn|en)$"),
                 user: User = Depends(staff), db: Session = Depends(get_db)):
    """Average, students, students who need attention (< 50 %), weakest topics, score by level.
    Pick one of test_id / paper_id (its OMR sheets + its online tests) / group_id; none = all of my classes.
    `format=html` gives a printable page (browser → Save as PDF)."""
    if test_id:
        t = db.get(Test, test_id)
        if not t or (t.created_by != user.id and "admin" not in user.roles):
            raise not_found("Test")
    if paper_id:
        p = db.get(Paper, paper_id)
        if not p or (p.owner_id != user.id and "admin" not in user.roles):
            raise not_found("Paper")
    if group_id:
        _own_group(db, group_id, user)
    title, rows = classroom.collect(db, user, test_id, paper_id, group_id)
    r = classroom.report(db, "" if title == "all" else title, rows)
    if format == "html":
        return HTMLResponse(classroom.report_html(r, lang))
    return r


# ================================================================== OMR overview (all papers)
@router.get("/omr/summary")
def omr_summary(user: User = Depends(staff), db: Session = Depends(get_db)):
    out = []
    for p in classroom.owned_papers(db, user):
        rs = db.scalars(select(OmrResult).where(OmrResult.paper_id == p.id)).all()
        graded = [r for r in rs if r.score is not None and r.total]
        out.append({"paper_id": p.id, "title": p.title, "questions": len(p.items), "sheets": len(rs),
                    "average": round(sum(r.score / r.total for r in graded) / len(graded), 3) if graded else None,
                    "needs_check": sum(1 for r in rs if r.flags or not r.student_id),
                    "last_upload": max((r.created_at for r in rs), default=None)})
    out.sort(key=lambda x: (x["last_upload"] is None, -(x["last_upload"].timestamp() if x["last_upload"] else 0)))
    return out


# ================================================================== student / parent home summary
@router.get("/students/{sid}/summary")
def student_summary(sid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Everything the student (and a linked parent) home screen needs in one call."""
    _can_view(db, user, sid)
    st = db.get(User, sid)
    if not st or "student" not in (st.roles or []):
        raise not_found("Student")
    taxonomy.ensure(db)
    results = []
    for a in db.scalars(select(TestAttempt).where(TestAttempt.student_id == sid, TestAttempt.submitted_at.is_not(None))):
        t = db.get(Test, a.test_id)
        peers = db.scalars(select(TestAttempt).where(TestAttempt.test_id == a.test_id, TestAttempt.submitted_at.is_not(None),
                                                     TestAttempt.total > 0)).all()
        results.append({"kind": "test", "title": t.title if t else "?", "score": a.score, "total": a.total, "when": a.submitted_at,
                        "class_average": round(sum((x.score or 0) / x.total for x in peers) / len(peers), 3) if peers else None})
    for r in db.scalars(select(OmrResult).where(OmrResult.student_id == sid, OmrResult.score.is_not(None))):
        p = db.get(Paper, r.paper_id)
        peers = db.scalars(select(OmrResult).where(OmrResult.paper_id == r.paper_id, OmrResult.score.is_not(None),
                                                   OmrResult.total > 0)).all()
        results.append({"kind": "omr", "title": p.title if p else "?", "score": r.score, "total": r.total, "when": r.created_at,
                        "class_average": round(sum(x.score / x.total for x in peers) / len(peers), 3) if peers else None})
    for s in db.scalars(select(PracticeSession).where(PracticeSession.student_id == sid, PracticeSession.status == "submitted")):
        results.append({"kind": "practice", "title": None, "score": s.score, "total": s.total, "when": s.submitted_at,
                        "class_average": None})
    norm = lambda w: (w if w is None or w.tzinfo else w.replace(tzinfo=timezone.utc))
    results = [r for r in results if r["when"] and r["total"]]
    results.sort(key=lambda r: norm(r["when"]))
    exams = [r for r in results if r["kind"] != "practice"]
    latest = (exams or results or [None])[-1]
    # weak concepts (all sources)
    from app.services.weekly import _answers
    rows = _answers(db, sid, datetime(2000, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc) + timedelta(days=1))
    for p in db.scalars(select(Paper).where(Paper.id.in_(select(OmrResult.paper_id).where(OmrResult.student_id == sid)))):
        rows += [{**x, "is_correct": x["is_correct"]} for x in classroom.rows_for_paper_omr(db, p) if x["student_id"] == sid]
    weak = [m for m in mastery(rows) if m["label"] in ("weak", "developing")][:4]
    lv = {k: [0, 0] for k in ("K1", "K2", "K3", "K4")}
    for x in rows:
        k = x.get("cognitive_level") or x.get("level")
        if k in lv:
            lv[k][0] += int(bool(x["is_correct"]))
            lv[k][1] += 1
    weak_levels = [k for k, (c, n) in lv.items() if n >= 3 and c / n < 0.6]
    now = datetime.now(timezone.utc)
    upcoming = []
    from app.api.v1.tests import _visible
    for t in db.scalars(select(Test).where(Test.end_at > now).order_by(Test.start_at)):
        if _visible(t, st) and not db.scalar(select(TestAttempt.id).where(TestAttempt.test_id == t.id, TestAttempt.student_id == sid,
                                                                          TestAttempt.submitted_at.is_not(None))):
            upcoming.append({"id": t.id, "title": t.title, "start_at": t.start_at, "end_at": t.end_at, "duration_min": t.duration_min})
    fb = db.scalars(select(Feedback).where(Feedback.student_id == sid).order_by(Feedback.id.desc()).limit(3)).all()
    names = _names(db, [f.from_id for f in fb])
    return {"student": {"id": st.id, "name": st.name, "class_level": st.class_level, "institution": st.institution,
                        "section": (st.profile or {}).get("section"), "avatar_url": st.avatar_url},
            "latest": latest, "progress": [{"title": r["title"], "kind": r["kind"], "pct": round(r["score"] / r["total"], 3), "when": r["when"]}
                                            for r in results[-8:]],
            "weak_topics": weak, "weak_levels": weak_levels, "upcoming": upcoming[:3],
            "feedback": [_fout(f, names) for f in fb],
            "unread_feedback": db.scalar(select(func.count(Feedback.id)).where(Feedback.student_id == sid, Feedback.read_at.is_(None)))}
