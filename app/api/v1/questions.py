from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.deps import STAFF, audit, require_roles
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.db.session import get_db
from app.ml.normalize import stem_hash
from app.models import QStatus, Question, User
from app.schemas import Msg, Page, QuestionIn, QuestionOut, QuestionUpdate, TagRequest, TagResult
from app.services.media import delete_question_image, save_question_image
from app.services.serialize import question_out
from app.services.tagging import create_questions, preview, retag
from app.services.taxonomy import taxonomy

router = APIRouter(prefix="/questions", tags=["questions"])
staff = require_roles(*STAFF)


def _load(db: Session, qid: int) -> Question:
    q = db.scalar(select(Question).options(selectinload(Question.predictions))
                  .where(Question.id == qid, Question.deleted.is_(False)))
    if not q:
        raise not_found("Question")
    return q


def _can_edit(user: User, q: Question):
    if "admin" in user.roles or "moderator" in user.roles or q.created_by == user.id:
        return
    raise forbidden("Only the owner, a moderator or an admin can change this question")


@router.post("/tag", response_model=list[TagResult])
def tag_questions(body: TagRequest, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    """Main entry: send 1..500 MCQs, get tags back. save=false -> preview only (nothing stored).
    A row with invalid input returns ok=false with an error code; other rows still succeed."""
    if len(body.items) > settings.MAX_BATCH:
        raise bad_request("BATCH_TOO_LARGE", f"Max {settings.MAX_BATCH} items per request; use /bulk for files")
    raw = [{**i.model_dump(), "options": i.options.model_dump()} for i in body.items]
    if not body.save:
        outs = preview(raw)
        return [{"index": n, "ok": not o.get("error"), "prediction": o, "error": o.get("error")}
                for n, o in enumerate(outs)]
    res = create_questions(db, raw, user.id)
    audit(db, user, "question.tag", "question", None, {"count": len(raw)}, request)
    db.commit()
    taxonomy.ensure(db)
    return [{"index": n, "ok": q is not None, "question": question_out(q) if q else None, "error": err}
            for n, (q, err) in enumerate(res)]


@router.post("", response_model=QuestionOut, status_code=201)
def create_one(body: QuestionIn, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    (q, err), = create_questions(db, [{**body.model_dump(), "options": body.options.model_dump()}], user.id)
    if err:
        raise bad_request(err["code"], err["message"])
    audit(db, user, "question.create", "question", q.id, request=request)
    db.commit()
    return question_out(q)


@router.get("", response_model=Page[QuestionOut])
def list_questions(
    q: str | None = Query(None, description="search text in stem"),
    status: str | None = Query(None, description="comma list: pending,tagged,needs_review,verified,rejected"),
    language: str | None = None, chapter_id: str | None = None, concept_id: str | None = None,
    topic_id: str | None = None, level: str | None = Query(None, description="comma list K1,K2"),
    difficulty: str | None = None, board: bool | None = None, mine: bool = False,
    min_conf_below: float | None = None, bulk_job_id: int | None = None,
    sort: str = Query("-created_at", description="created_at | -created_at | min_confidence | -min_confidence"),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(staff), db: Session = Depends(get_db),
):
    stmt = select(Question).where(Question.deleted.is_(False))
    if q:
        stmt = stmt.where(Question.stem.ilike(f"%{q}%"))
    if status:
        stmt = stmt.where(Question.status.in_(status.split(",")))
    if language:
        stmt = stmt.where(Question.language == language)
    if chapter_id:
        stmt = stmt.where(Question.chapter_id == chapter_id)
    if concept_id:
        stmt = stmt.where(Question.concept_id == concept_id)
    if topic_id:
        stmt = stmt.where(Question.topic_id == topic_id)
    if level:
        stmt = stmt.where(Question.cognitive_level.in_(level.split(",")))
    if difficulty:
        stmt = stmt.where(Question.difficulty.in_(difficulty.split(",")))
    if board is not None:
        stmt = stmt.where(Question.is_board.is_(board))
    if mine:
        stmt = stmt.where(Question.created_by == user.id)
    if min_conf_below is not None:
        stmt = stmt.where(or_(Question.min_confidence < min_conf_below, Question.min_confidence.is_(None)))
    if bulk_job_id:
        stmt = stmt.where(Question.bulk_job_id == bulk_job_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    col = {"created_at": Question.created_at, "min_confidence": Question.min_confidence, "id": Question.id}.get(
        sort.lstrip("-"), Question.created_at)
    stmt = stmt.order_by(col.desc() if sort.startswith("-") else col.asc(), Question.id.desc())
    rows = db.scalars(stmt.options(selectinload(Question.predictions))
                      .offset((page - 1) * page_size).limit(page_size)).all()
    taxonomy.ensure(db)
    return {"items": [question_out(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/stats")
def stats(user: User = Depends(staff), db: Session = Depends(get_db)):
    base = select(Question).where(Question.deleted.is_(False))
    by_status = dict(db.execute(select(Question.status, func.count()).where(Question.deleted.is_(False))
                                .group_by(Question.status)).all())
    by_level = dict(db.execute(select(Question.cognitive_level, func.count()).where(Question.deleted.is_(False))
                               .group_by(Question.cognitive_level)).all())
    return {"total": db.scalar(select(func.count()).select_from(base.subquery())), "by_status": by_status,
            "by_level": {k or "untagged": v for k, v in by_level.items()}}


@router.get("/export")
def export_questions(q: str | None = None, status: str | None = None, language: str | None = None, chapter_id: str | None = None,
                     level: str | None = None, difficulty: str | None = None, board: bool | None = None, mine: bool = False,
                     bulk_job_id: int | None = None, user: User = Depends(staff), db: Session = Depends(get_db)):
    """The question bank (with the same filters as the list) as an Excel file with tags."""
    import io
    from fastapi.responses import Response
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "questions"
    head = ["id", "language", "question", "A", "B", "C", "D", "answer", "chapter", "concept", "topic", "level", "difficulty", "status", "board"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    page = 1
    while page <= 50:
        res = list_questions(q=q, status=status, language=language, chapter_id=chapter_id, concept_id=None, topic_id=None, level=level,
                             difficulty=difficulty, board=board, mine=mine, min_conf_below=None, bulk_job_id=bulk_job_id,
                             sort="created_at", page=page, page_size=100, user=user, db=db)
        for x in res["items"]:
            g = (lambda k: x.get(k)) if isinstance(x, dict) else (lambda k: getattr(x, k))
            o = g("options") or {}
            lang = g("language")

            def nm(tv):
                if not tv:
                    return ""
                tv = tv if isinstance(tv, dict) else tv.model_dump()
                return (tv.get("name_bn") if lang == "bn" else tv.get("name_en")) or tv.get("id") or ""
            bm = g("board_match") or {}
            lv, df = g("cognitive_level"), g("difficulty")
            ws.append([g("id"), lang, g("stem"), o.get("A"), o.get("B"), o.get("C"), o.get("D"), g("answer"), nm(g("chapter")), nm(g("concept")),
                       nm(g("topic")), (lv.get("id") if isinstance(lv, dict) else getattr(lv, "id", "")) if lv else "",
                       (df.get("id") if isinstance(df, dict) else getattr(df, "id", "")) if df else "", g("status"),
                       f"{bm.get('board', '')} {bm.get('year', '')}".strip() if bm.get("matched") else ""])
        if page * 100 >= res["total"]:
            break
        page += 1
    ws.column_dimensions["C"].width = 70
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="dishari_questions.xlsx"'})


@router.get("/{qid}", response_model=QuestionOut)
def get_question(qid: int, _: User = Depends(staff), db: Session = Depends(get_db)):
    taxonomy.ensure(db)
    return question_out(_load(db, qid))


@router.get("/{qid}/history")
def history(qid: int, _: User = Depends(staff), db: Session = Depends(get_db)):
    """All model predictions + all moderator reviews for one question (audit trail on the detail page)."""
    from app.models import Review
    q = _load(db, qid)
    reviews = db.scalars(select(Review).where(Review.question_id == qid).order_by(Review.id.desc())).all()
    return {"predictions": [{"id": p.id, "model_version": p.model_version, "created_at": p.created_at, "raw": p.raw}
                            for p in q.predictions],
            "reviews": [{"id": r.id, "reviewer_id": r.reviewer_id, "action": r.action, "before": r.before,
                         "after": r.after, "reason": r.reason, "created_at": r.created_at} for r in reviews]}


@router.patch("/{qid}", response_model=QuestionOut)
def update_question(qid: int, body: QuestionUpdate, request: Request, user: User = Depends(staff),
                    db: Session = Depends(get_db)):
    """Edit text and/or tags. Text change -> re-tag by model. Manual tag change by a teacher -> needs_review;
    by a moderator/admin -> verified."""
    q = _load(db, qid)
    _can_edit(user, q)
    data = body.model_dump(exclude_unset=True)
    text_changed = False
    for k in ("stem", "options", "answer", "explanation", "source", "figure_dependent"):
        if k in data and data[k] is not None:
            v = data[k]
            if k == "options":
                v = dict(v)
            if k in ("stem", "options") and getattr(q, k) != v:
                text_changed = True
            setattr(q, k, v)
    if text_changed:
        q.stem_hash = stem_hash(q.stem)
    tag_keys = [k for k in ("chapter_id", "concept_id", "topic_id", "cognitive_level", "difficulty") if k in data]
    if tag_keys:
        taxonomy.ensure(db)
        new = {k: data.get(k, getattr(q, k)) for k in ("chapter_id", "concept_id", "topic_id")}
        probs = taxonomy.check_consistency(new["chapter_id"], new["concept_id"], new["topic_id"])
        if probs:
            raise bad_request("TAXONOMY_MISMATCH", "; ".join(probs))
        for k in tag_keys:
            setattr(q, k, data[k])
        staff_reviewer = bool({"admin", "moderator"} & set(user.roles))
        q.tag_source = "moderator" if staff_reviewer else "teacher"
        q.status = QStatus.verified.value if staff_reviewer else QStatus.needs_review.value
        q.min_confidence = None
    elif text_changed:
        retag(db, [q])
    audit(db, user, "question.update", "question", q.id, {"fields": list(data.keys())}, request)
    db.commit()
    db.refresh(q)
    return question_out(q)


class ConfirmIn(BaseModel):
    ids: list[int]


@router.post("/confirm")
def confirm_tags(body: ConfirmIn, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    """Tag review: accept the current tags of the chosen questions. A moderator/admin marks them `verified`;
    a teacher confirms their own questions (status `tagged`, tag source `teacher`) and a moderator can still check them."""
    reviewer = bool({"admin", "moderator"} & set(user.roles))
    done, skipped = [], []
    from app.models import Review
    for q in db.scalars(select(Question).where(Question.id.in_(body.ids[:500] or [0]), Question.deleted.is_(False))):
        if q.status not in (QStatus.tagged.value, QStatus.needs_review.value) or not q.cognitive_level or \
                (not reviewer and q.created_by != user.id):
            skipped.append(q.id)
            continue
        if reviewer:
            tags = {k: getattr(q, k) for k in ("chapter_id", "concept_id", "topic_id", "cognitive_level", "difficulty")}
            db.add(Review(question_id=q.id, reviewer_id=user.id, action="accept", before=tags, after=tags))
            q.status, q.tag_source = QStatus.verified.value, "moderator"
        else:
            q.status, q.tag_source = QStatus.tagged.value, "teacher"
        done.append(q.id)
    audit(db, user, "question.confirm", "question", None, {"count": len(done)}, request)
    db.commit()
    return {"confirmed": done, "skipped": skipped, "status": "verified" if reviewer else "tagged"}


@router.post("/{qid}/retag", response_model=QuestionOut)
def retag_one(qid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = _load(db, qid)
    _can_edit(user, q)
    if q.status == QStatus.verified.value and q.tag_source == "moderator":
        raise ApiError(409, "ALREADY_VERIFIED", "A moderator verified this question; change tags via review")
    retag(db, [q])
    db.commit()
    db.refresh(q)
    return question_out(q)


@router.delete("/{qid}", response_model=Msg)
def delete_question(qid: int, request: Request, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = _load(db, qid)
    _can_edit(user, q)
    q.deleted = True  # soft delete: keeps papers/practice history intact
    audit(db, user, "question.delete", "question", q.id, request=request)
    db.commit()
    return {"message": "Deleted"}


@router.post("/{qid}/image", response_model=QuestionOut)
async def upload_image(qid: int, request: Request, file: UploadFile = File(...), user: User = Depends(staff),
                       db: Session = Depends(get_db)):
    """Attach a figure (PNG/JPG/WEBP, max 3 MB). Replaces the old one. Shows as image_url."""
    q = _load(db, qid)
    _can_edit(user, q)
    name = save_question_image(await file.read())
    delete_question_image(q.image_path)
    q.image_path, q.figure_dependent = name, True
    audit(db, user, "question.image", "question", q.id, {"file": name}, request)
    db.commit()
    db.refresh(q)
    return question_out(q)


@router.delete("/{qid}/image", response_model=QuestionOut)
def remove_image(qid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    q = _load(db, qid)
    _can_edit(user, q)
    delete_question_image(q.image_path)
    q.image_path = None
    db.commit()
    db.refresh(q)
    return question_out(q)


@router.post("/extract")
async def extract_from_file(file: UploadFile = File(...), language: str = Query("auto", pattern="^(auto|bn|en)$"),
                            _: User = Depends(staff)):
    """Photo (JPG/PNG/WEBP) or PDF of a question paper -> draft MCQs (Bangla and English).
    Nothing is saved: the teacher checks/edits the draft, then sends it to POST /questions/tag."""
    from app.services.ocr import extract
    import anyio
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise bad_request("FILE_TOO_LARGE", "Max 15 MB")
    return await anyio.to_thread.run_sync(extract, file.filename or "upload.jpg", data, language)
