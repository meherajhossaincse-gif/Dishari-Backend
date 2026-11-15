"""Online tests: a teacher turns a paper into a timed test; students take it inside a time window."""
import random
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import STAFF, audit, current_user, require_roles
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.db.session import get_db
from app.models import Paper, PaperItem, Question, Test, TestAttempt, User
from app.schemas import TestAnswers, TestCreate
from app.services.analysis import score_practice
from app.services.serialize import student_question
from app.services.taxonomy import taxonomy

router = APIRouter(prefix="/tests", tags=["online tests"])
staff = require_roles(*STAFF)
student = require_roles("student")
GRACE = timedelta(seconds=60)


def now():
    return datetime.now(timezone.utc)


def aware(d: datetime | None):
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def state(t: Test, a: TestAttempt | None = None) -> str:
    n = now()
    if a and a.submitted_at:
        return "submitted"
    if a and not a.submitted_at and n <= aware(a.deadline_at) + GRACE:
        return "in_progress"
    if n < aware(t.start_at):
        return "upcoming"
    if n <= aware(t.end_at):
        return "live"
    return "missed" if not a else "submitted"


def test_out(t: Test, a: TestAttempt | None = None, extra: dict | None = None) -> dict:
    d = {"id": t.id, "title": t.title, "instructions": t.instructions, "class_level": t.class_level, "section": t.section,
         "paper_id": t.paper_id, "start_at": aware(t.start_at), "end_at": aware(t.end_at),
         "duration_min": t.duration_min, "question_count": len(t.paper.items) if t.paper else None,
         "show_answers": t.show_answers, "state": state(t, a), "created_at": t.created_at}
    if a:
        d["attempt"] = {"started_at": aware(a.started_at), "deadline_at": aware(a.deadline_at),
                        "submitted_at": aware(a.submitted_at), "score": a.score if a.submitted_at else None,
                        "total": a.total, "late": a.late}
    return d | (extra or {})


def _load(db: Session, tid: int) -> Test:
    t = db.scalar(select(Test).options(selectinload(Test.paper).selectinload(Paper.items).selectinload(PaperItem.question))
                  .where(Test.id == tid))
    if not t:
        raise not_found("Test")
    return t


def _visible(t: Test, u: User) -> bool:
    if t.class_level and (u.class_level or "").strip() != t.class_level.strip():
        return False
    sec = ((u.profile or {}).get("section") or "").strip().lower()
    return not t.section or sec == t.section.strip().lower()


# ------------------------------------------------------------------ teacher
@router.post("", status_code=201)
def create_test(body: TestCreate, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    p = db.scalar(select(Paper).options(selectinload(Paper.items)).where(Paper.id == body.paper_id))
    if not p or (p.owner_id != user.id and "admin" not in user.roles):
        raise not_found("Paper")
    if not p.items:
        raise bad_request("EMPTY_PAPER", "Add questions to the paper first")
    if any(i.question.answer is None for i in p.items):
        raise bad_request("MISSING_ANSWERS", "Every question in an online test needs a correct answer")
    if aware(body.end_at) <= aware(body.start_at):
        raise bad_request("BAD_WINDOW", "End time must be after start time", {"end_at": "before start"})
    t = Test(paper_id=p.id, created_by=user.id, title=body.title or p.title, instructions=body.instructions,
             class_level=(body.class_level or None), section=(body.section or None), start_at=aware(body.start_at), end_at=aware(body.end_at),
             duration_min=body.duration_min, shuffle=body.shuffle, show_answers=body.show_answers)
    db.add(t)
    db.flush()
    audit(db, user, "test.create", "test", t.id, {"paper_id": p.id}, request)
    db.commit()
    return test_out(_load(db, t.id))


@router.get("")
def list_tests(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Staff: tests they created (with attempt counts). Student: tests open to their class, with their state."""
    q = select(Test).options(selectinload(Test.paper).selectinload(Paper.items)).order_by(Test.start_at.desc())
    if {"admin", "teacher", "moderator"} & set(user.roles):
        tests = db.scalars(q if "admin" in user.roles else q.where(Test.created_by == user.id)).all()
        counts = dict(db.execute(select(TestAttempt.test_id, func.count()).where(TestAttempt.submitted_at.is_not(None))
                                 .group_by(TestAttempt.test_id)).all())
        return [test_out(t, extra={"submitted": counts.get(t.id, 0)}) for t in tests]
    if "student" not in user.roles:
        raise forbidden()
    tests = [t for t in db.scalars(q).all() if _visible(t, user)]
    mine = {a.test_id: a for a in db.scalars(select(TestAttempt).where(TestAttempt.student_id == user.id))}
    return [test_out(t, mine.get(t.id)) for t in tests]


@router.get("/{tid}/results")
def results(tid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    t = _load(db, tid)
    if t.created_by != user.id and "admin" not in user.roles:
        raise forbidden()
    rows = db.execute(select(TestAttempt, User.name, User.class_level).join(User, User.id == TestAttempt.student_id)
                      .where(TestAttempt.test_id == tid).order_by(TestAttempt.score.desc().nulls_last())).all()
    sub = [a for a, _, _ in rows if a.submitted_at]
    lvl = {k: {"correct": 0, "total": 0} for k in ("K1", "K2", "K3", "K4")}
    for a in sub:
        for k, v in (a.breakdown or {}).get("by_level", {}).items():
            lvl[k]["correct"] += v["correct"]
            lvl[k]["total"] += v["total"]
    scores = [a.score / a.total for a in sub if a.total]
    return {"test": test_out(t), "submitted": len(sub), "started": len(rows),
            "average": round(sum(scores) / len(scores), 3) if scores else None,
            "highest": max(scores) if scores else None, "by_level": lvl,
            "students": [{"student_id": a.student_id, "name": n, "class_level": c, "score": a.score, "total": a.total,
                          "submitted_at": aware(a.submitted_at), "late": a.late} for a, n, c in rows]}


@router.delete("/{tid}")
def delete_test(tid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    t = _load(db, tid)
    if t.created_by != user.id and "admin" not in user.roles:
        raise forbidden()
    db.delete(t)
    db.commit()
    return {"message": "Deleted"}


# ------------------------------------------------------------------ student
def _attempt(db, tid, uid) -> TestAttempt | None:
    return db.scalar(select(TestAttempt).where(TestAttempt.test_id == tid, TestAttempt.student_id == uid))


def _questions(t: Test, a: TestAttempt) -> list[dict]:
    by_id = {i.question_id: i.question for i in t.paper.items}
    return [{"position": n, **student_question(by_id[q])} for n, q in enumerate(a.question_order, 1) if q in by_id]


@router.post("/{tid}/start")
def start(tid: int, user: User = Depends(student), db: Session = Depends(get_db)):
    t = _load(db, tid)
    if not _visible(t, user):
        raise forbidden("This test is not for your class")
    a = _attempt(db, tid, user.id)
    if a and a.submitted_at:
        raise ApiError(409, "ALREADY_SUBMITTED", "You already submitted this test")
    n = now()
    if not a:
        if n < aware(t.start_at):
            raise ApiError(409, "NOT_STARTED", "The test has not started yet")
        if n > aware(t.end_at):
            raise ApiError(409, "TEST_CLOSED", "The test window is closed")
        order = [i.question_id for i in sorted(t.paper.items, key=lambda i: i.position)]
        if t.shuffle:
            random.shuffle(order)
        a = TestAttempt(test_id=t.id, student_id=user.id, question_order=order, answers={},
                        deadline_at=min(n + timedelta(minutes=t.duration_min), aware(t.end_at)), total=len(order))
        db.add(a)
        db.commit()
    taxonomy.ensure(db)
    return test_out(t, a, {"questions": _questions(t, a), "answers": a.answers, "server_time": n})


@router.put("/{tid}/answers")
def autosave(tid: int, body: TestAnswers, user: User = Depends(student), db: Session = Depends(get_db)):
    a = _attempt(db, tid, user.id)
    if not a or a.submitted_at:
        raise ApiError(409, "NOT_IN_PROGRESS", "No open attempt")
    if now() > aware(a.deadline_at) + GRACE:
        raise ApiError(409, "TIME_UP", "Time is up, submit now")
    a.answers = {str(k): v for k, v in body.answers.items() if v}
    db.commit()
    return {"saved": len(a.answers), "server_time": now()}


def _grade(db, t: Test, a: TestAttempt):
    by_id = {i.question_id: i.question for i in t.paper.items}
    rows = []
    for qid in a.question_order:
        q = by_id.get(qid)
        if not q:
            continue
        ch = (a.answers or {}).get(str(qid))
        rows.append({"cognitive_level": q.cognitive_level, "chapter_id": q.chapter_id, "is_correct": ch == q.answer})
    a.score = sum(1 for r in rows if r["is_correct"])
    a.total = len(rows)
    a.breakdown = score_practice(rows)


@router.post("/{tid}/submit")
def submit(tid: int, body: TestAnswers, user: User = Depends(student), db: Session = Depends(get_db)):
    t = _load(db, tid)
    a = _attempt(db, tid, user.id)
    if not a:
        raise ApiError(409, "NOT_STARTED", "Start the test first")
    if a.submitted_at:
        raise ApiError(409, "ALREADY_SUBMITTED", "You already submitted this test")
    n = now()
    if n <= aware(a.deadline_at) + GRACE:  # answers sent after the deadline are ignored; autosaved ones count
        a.answers = {str(k): v for k, v in body.answers.items() if v}
    else:
        a.late = True
    a.submitted_at = n
    _grade(db, t, a)
    db.commit()
    return my_result(tid, user, db)


@router.get("/{tid}/my-result")
def my_result(tid: int, user: User = Depends(student), db: Session = Depends(get_db)):
    t = _load(db, tid)
    a = _attempt(db, tid, user.id)
    if not a or not a.submitted_at:
        raise ApiError(409, "NOT_SUBMITTED", "Not submitted yet")
    taxonomy.ensure(db)
    show = t.show_answers == "after_submit" or (t.show_answers == "after_end" and now() > aware(t.end_at))
    by_id = {i.question_id: i.question for i in t.paper.items}
    review = []
    if show:
        for n, qid in enumerate(a.question_order, 1):
            q = by_id.get(qid)
            if q:
                ch = (a.answers or {}).get(str(qid))
                review.append({"position": n, "question": student_question(q), "chosen": ch, "correct_answer": q.answer,
                               "is_correct": ch == q.answer, "explanation": q.explanation})
    return test_out(t, a, {"breakdown": a.breakdown, "answers_visible": show, "review": review,
                           "answers_visible_from": aware(t.end_at) if t.show_answers == "after_end" else None})
