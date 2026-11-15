import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.deps import current_user, require_roles
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.db.session import get_db
from app.models import ParentLink, PracticeAnswer, PracticeSession, QStatus, Question, User
from fastapi.responses import HTMLResponse
from app.schemas import LinkChildIn, PracticeCreate, PracticeSubmit
from app.services.weekly import report_html, weekly_report
from app.services.analysis import mastery, score_practice
from app.services.serialize import student_question
from app.services.taxonomy import taxonomy

router = APIRouter(tags=["practice (student)"])
student = require_roles("student")
USABLE = (QStatus.verified.value, QStatus.tagged.value)


def _session(db, sid, user) -> PracticeSession:
    s = db.scalar(select(PracticeSession).options(selectinload(PracticeSession.answers)
                                                  .selectinload(PracticeAnswer.question))
                  .where(PracticeSession.id == sid))
    if not s:
        raise not_found("Practice session")
    if s.student_id != user.id:
        raise forbidden()
    return s


def _result(s: PracticeSession) -> dict:
    return {"id": s.id, "status": s.status, "language": s.language, "filters": s.filters, "total": s.total,
            "score": s.score, "breakdown": s.breakdown, "started_at": s.started_at, "submitted_at": s.submitted_at,
            "review": [{"position": a.position, "question": student_question(a.question), "chosen": a.chosen,
                        "correct_answer": a.question.answer, "is_correct": a.is_correct,
                        "explanation": a.question.explanation} for a in s.answers] if s.status == "submitted" else []}


@router.post("/practice/sessions", status_code=201)
def start(body: PracticeCreate, user: User = Depends(student), db: Session = Depends(get_db)):
    """Picks random tagged/verified questions (with an answer key) for the chosen chapters/levels."""
    usable = USABLE + ((QStatus.needs_review.value,) if settings.PRACTICE_ALLOW_NEEDS_REVIEW else ())
    stmt = select(Question.id).where(Question.deleted.is_(False), Question.status.in_(usable),
                                     Question.answer.is_not(None), Question.language == body.language)
    if body.chapter_ids:
        stmt = stmt.where(Question.chapter_id.in_(body.chapter_ids))
    if body.concept_ids:
        stmt = stmt.where(Question.concept_id.in_(body.concept_ids))
    if body.levels:
        stmt = stmt.where(Question.cognitive_level.in_(body.levels))
    ids = list(db.scalars(stmt))
    if not ids:
        raise ApiError(404, "NO_QUESTIONS", "No questions available for these filters yet")
    pick = random.sample(ids, min(body.size, len(ids)))
    s = PracticeSession(student_id=user.id, language=body.language, total=len(pick),
                        filters={"chapter_ids": body.chapter_ids, "concept_ids": body.concept_ids,
                                 "levels": body.levels, "requested": body.size, "mode": body.mode,
                                 "time_limit_min": body.time_limit_min if body.mode == "exam" else None})
    for n, qid in enumerate(pick, 1):
        s.answers.append(PracticeAnswer(question_id=qid, position=n))
    db.add(s)
    db.commit()
    taxonomy.ensure(db)
    s = _session(db, s.id, user)
    return {"id": s.id, "total": s.total, "status": s.status, "started_at": s.started_at, "mode": s.filters.get("mode", "practice"),
            "time_limit_min": s.filters.get("time_limit_min"),
            "questions": [{"position": a.position, **student_question(a.question)} for a in s.answers]}


@router.get("/practice/sessions/{sid}")
def get_session(sid: int, user: User = Depends(student), db: Session = Depends(get_db)):
    s = _session(db, sid, user)
    taxonomy.ensure(db)
    if s.status == "in_progress":
        return {"id": s.id, "total": s.total, "status": s.status, "started_at": s.started_at,
                "mode": (s.filters or {}).get("mode", "practice"), "time_limit_min": (s.filters or {}).get("time_limit_min"),
                "questions": [{"position": a.position, "chosen": a.chosen, **student_question(a.question)}
                              for a in s.answers]}
    return _result(s)


@router.post("/practice/sessions/{sid}/submit")
def submit(sid: int, body: PracticeSubmit, user: User = Depends(student), db: Session = Depends(get_db)):
    s = _session(db, sid, user)
    if s.status == "submitted":
        raise ApiError(409, "ALREADY_SUBMITTED", "This practice was already submitted")
    valid = {a.question_id for a in s.answers}
    extra = [k for k in body.answers if k not in valid]
    if extra:
        raise bad_request("NOT_IN_SESSION", f"Questions not in this practice: {extra}")
    rows = []
    for a in s.answers:
        a.chosen = body.answers.get(a.question_id)
        a.is_correct = a.chosen is not None and a.chosen == a.question.answer
        a.time_ms = body.time_ms.get(a.question_id)
        rows.append({"cognitive_level": a.question.cognitive_level, "chapter_id": a.question.chapter_id,
                     "is_correct": a.is_correct})
    s.score = sum(1 for r in rows if r["is_correct"])
    s.breakdown = score_practice(rows)
    s.status, s.submitted_at = "submitted", datetime.now(timezone.utc)
    db.commit()
    taxonomy.ensure(db)
    return _result(s)


@router.get("/practice/sessions")
def my_history(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user: User = Depends(student),
               db: Session = Depends(get_db)):
    stmt = select(PracticeSession).where(PracticeSession.student_id == user.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(PracticeSession.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": s.id, "status": s.status, "total": s.total, "score": s.score, "filters": s.filters,
                       "started_at": s.started_at, "submitted_at": s.submitted_at} for s in rows],
            "total": total, "page": page, "page_size": page_size}


# ----------------------------------------------------------------- progress / diagnosis
def _can_view_student(db, viewer: User, student_id: int):
    if viewer.id == student_id or {"admin", "teacher"} & set(viewer.roles):
        return
    if "parent" in viewer.roles and db.scalar(select(ParentLink).where(ParentLink.parent_id == viewer.id,
                                                                      ParentLink.student_id == student_id)):
        return
    raise forbidden("Not allowed to view this student")


def _answers(db, student_id: int):
    return db.execute(select(PracticeAnswer.is_correct, Question.cognitive_level, Question.chapter_id,
                             Question.concept_id, Question.difficulty, PracticeSession.submitted_at,
                             PracticeSession.id)
                      .join(PracticeSession, PracticeSession.id == PracticeAnswer.session_id)
                      .join(Question, Question.id == PracticeAnswer.question_id)
                      .where(PracticeSession.student_id == student_id, PracticeSession.status == "submitted")).all()


@router.get("/students/{student_id}/progress")
def progress(student_id: int, viewer: User = Depends(current_user), db: Session = Depends(get_db)):
    """W09 progress chart: score per session over time + accuracy per level and chapter."""
    _can_view_student(db, viewer, student_id)
    sessions = db.scalars(select(PracticeSession).where(PracticeSession.student_id == student_id,
                                                        PracticeSession.status == "submitted")
                          .order_by(PracticeSession.submitted_at)).all()
    rows = [{"cognitive_level": r[1], "chapter_id": r[2], "is_correct": r[0]} for r in _answers(db, student_id)]
    taxonomy.ensure(db)
    agg = score_practice(rows)
    agg["by_chapter"] = {k: {**v, "name_en": taxonomy.chapters.get(k, {}).get("name_en"),
                             "name_bn": taxonomy.chapters.get(k, {}).get("name_bn")}
                         for k, v in agg["by_chapter"].items()}
    return {"student_id": student_id, "sessions": [
        {"id": s.id, "submitted_at": s.submitted_at, "score": s.score, "total": s.total,
         "percent": round(100 * (s.score or 0) / s.total, 1) if s.total else 0,
         "by_level": (s.breakdown or {}).get("by_level")} for s in sessions], **agg}


@router.get("/students/{student_id}/mastery")
def student_mastery(student_id: int, viewer: User = Depends(current_user), db: Session = Depends(get_db)):
    """(R2) Concept mastery using (Σw·correct+1)/(Σw+2); Easy 1, Medium 1.5, Hard 2."""
    _can_view_student(db, viewer, student_id)
    taxonomy.ensure(db)
    rows = [{"is_correct": r[0], "cognitive_level": r[1], "concept_id": r[3], "difficulty": r[4]}
            for r in _answers(db, student_id)]
    m = mastery(rows)
    return {"student_id": student_id, "concepts": m,
            "weak": [x for x in m if x["label"] == "weak"],
            "formula": "(sum(w*correct)+1)/(sum(w)+2); w: Easy 1, Medium 1.5, Hard 2; <3 answers = insufficient"}


@router.get("/parents/me/children")
def my_children(user: User = Depends(require_roles("parent")), db: Session = Depends(get_db)):
    kids = db.scalars(select(User).join(ParentLink, ParentLink.student_id == User.id).where(ParentLink.parent_id == user.id)).all()
    return [{"id": k.id, "name": k.name, "class_level": k.class_level, "institution": k.institution,
             "avatar_url": k.avatar_url, "roll": (k.profile or {}).get("roll"), "section": (k.profile or {}).get("section")}
            for k in kids]


@router.delete("/parents/me/children/{sid}", status_code=204)
def unlink_child(sid: int, user: User = Depends(require_roles("parent")), db: Session = Depends(get_db)):
    """Remove a linked child from this parent account (the child's account is not touched)."""
    link = db.scalar(select(ParentLink).where(ParentLink.parent_id == user.id, ParentLink.student_id == sid))
    if link:
        db.delete(link)
        db.commit()


@router.post("/parents/me/children", status_code=201)
def link_child(body: LinkChildIn, user: User = Depends(require_roles("parent")), db: Session = Depends(get_db)):
    """Link a child with the code shown on the child's profile page."""
    kid = db.scalar(select(User).where(User.link_code == body.link_code.strip().upper()))
    if not kid or "student" not in kid.roles:
        raise ApiError(400, "BAD_LINK_CODE", "No student has this link code", {"link_code": "invalid"})
    if not db.scalar(select(ParentLink).where(ParentLink.parent_id == user.id, ParentLink.student_id == kid.id)):
        db.add(ParentLink(parent_id=user.id, student_id=kid.id))
        db.commit()
    return {"id": kid.id, "name": kid.name}


@router.get("/students/{student_id}/weekly-report")
def weekly(student_id: int, offset: int = Query(0, ge=0, le=52), format: str = Query("json", pattern="^(json|html)$"),
           lang: str = Query("bn", pattern="^(bn|en)$"), viewer: User = Depends(current_user), db: Session = Depends(get_db)):
    """Saturday-to-Friday report: activity, accuracy vs last week, levels, chapters, weak concepts, suggestions.
    offset=1 is last week. format=html gives a printable page."""
    _can_view_student(db, viewer, student_id)
    st = db.get(User, student_id)
    if not st or "student" not in st.roles:
        raise not_found("Student")
    r = weekly_report(db, st, offset)
    if format == "html":
        return HTMLResponse(report_html(r, lang))
    return r
