from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import REVIEWERS, audit, current_user, require_roles
from app.core.errors import bad_request, not_found
from app.db.session import get_db
from app.models import QStatus, Question, Review, TagReport, User
from app.schemas import Page, QuestionOut, ReviewIn, ReviewOut, TagReportIn, TagReportOut, TagReportResolve
from app.services.serialize import question_out
from app.services.taxonomy import taxonomy

router = APIRouter(tags=["review"])
reviewer = require_roles(*REVIEWERS)
TAGS = ("chapter_id", "concept_id", "topic_id", "cognitive_level", "difficulty")


def _snap(q: Question) -> dict:
    return {k: getattr(q, k) for k in TAGS} | {
        "status": q.status, "tag_source": q.tag_source, "min_confidence": q.min_confidence,
        "model_version": q.predictions[0].model_version if q.predictions else None}


@router.get("/reviews/queue", response_model=Page[QuestionOut])
def queue(language: str | None = None, chapter_id: str | None = None, level: str | None = None,
          include_reported: bool = True, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
          _: User = Depends(reviewer), db: Session = Depends(get_db)):
    """Needs-review questions, lowest confidence first, plus questions with open tag reports."""
    cond = Question.status == QStatus.needs_review.value
    if include_reported:
        reported = select(TagReport.question_id).where(TagReport.status == "open")
        cond = cond | Question.id.in_(reported)
    stmt = select(Question).where(Question.deleted.is_(False), cond)
    if language:
        stmt = stmt.where(Question.language == language)
    if chapter_id:
        stmt = stmt.where(Question.chapter_id == chapter_id)
    if level:
        stmt = stmt.where(Question.cognitive_level == level)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.options(selectinload(Question.predictions))
                      .order_by(Question.min_confidence.asc().nulls_first(), Question.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    taxonomy.ensure(db)
    return {"items": [question_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.post("/reviews/{qid}", response_model=QuestionOut)
def submit_review(qid: int, body: ReviewIn, request: Request, user: User = Depends(reviewer),
                  db: Session = Depends(get_db)):
    q = db.scalar(select(Question).options(selectinload(Question.predictions))
                  .where(Question.id == qid, Question.deleted.is_(False)))
    if not q:
        raise not_found("Question")
    if body.action in ("change", "reject") and not (body.reason or "").strip():
        raise bad_request("REASON_REQUIRED", "A reason is required to change or reject", {"reason": "required"})
    before = _snap(q)
    if body.action == "change":
        if not body.tags:
            raise bad_request("TAGS_REQUIRED", "Send the corrected tags", {"tags": "required"})
        data = body.tags.model_dump(exclude_unset=True)
        taxonomy.ensure(db)
        new = {k: data.get(k, getattr(q, k)) for k in ("chapter_id", "concept_id", "topic_id")}
        probs = taxonomy.check_consistency(new["chapter_id"], new["concept_id"], new["topic_id"])
        if probs:
            raise bad_request("TAXONOMY_MISMATCH", "; ".join(probs))
        for k, v in data.items():
            setattr(q, k, v)
        q.status, q.tag_source = QStatus.verified.value, "moderator"
    elif body.action == "accept":
        q.status, q.tag_source = QStatus.verified.value, "moderator"
    else:
        q.status = QStatus.rejected.value
    db.add(Review(question_id=q.id, reviewer_id=user.id, action=body.action, before=before, after=_snap(q),
                  reason=body.reason))
    # resolve open reports for this question
    for r in db.scalars(select(TagReport).where(TagReport.question_id == q.id, TagReport.status == "open")):
        r.status, r.resolved_by, r.resolution_note = "resolved", user.id, f"review: {body.action}"
    audit(db, user, f"review.{body.action}", "question", q.id, {"reason": body.reason}, request)
    db.commit()
    db.refresh(q)
    taxonomy.ensure(db)
    return question_out(q)


@router.get("/reviews", response_model=Page[ReviewOut])
def review_history(question_id: int | None = None, reviewer_id: int | None = None, page: int = Query(1, ge=1),
                   page_size: int = Query(20, ge=1, le=100), _: User = Depends(reviewer),
                   db: Session = Depends(get_db)):
    stmt = select(Review)
    if question_id:
        stmt = stmt.where(Review.question_id == question_id)
    if reviewer_id:
        stmt = stmt.where(Review.reviewer_id == reviewer_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Review.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


# ----------------------------------------------------------------- tag reports (S11: anyone logged in)
@router.post("/tag-reports", response_model=TagReportOut, status_code=201)
def report(body: TagReportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.get(Question, body.question_id)
    if not q or q.deleted:
        raise not_found("Question")
    r = TagReport(question_id=q.id, reporter_id=user.id, field=body.field, comment=body.comment)
    db.add(r)
    db.commit()
    return r


@router.get("/tag-reports", response_model=Page[TagReportOut])
def reports(status: str = "open", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
            _: User = Depends(reviewer), db: Session = Depends(get_db)):
    stmt = select(TagReport).where(TagReport.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(TagReport.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.patch("/tag-reports/{rid}", response_model=TagReportOut)
def resolve(rid: int, body: TagReportResolve, user: User = Depends(reviewer), db: Session = Depends(get_db)):
    r = db.get(TagReport, rid)
    if not r:
        raise not_found("Report")
    r.status, r.resolved_by, r.resolution_note = body.status, user.id, body.note
    db.commit()
    return r
