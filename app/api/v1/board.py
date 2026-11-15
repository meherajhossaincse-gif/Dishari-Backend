from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import STAFF, audit, require_roles
from app.core.errors import not_found
from app.db.session import get_db
from app.ml.board_match import board_index
from app.models import BoardMatch, BoardQuestion, Question, User
from app.schemas import BoardMatchDecision, BoardQuestionIn, BoardQuestionOut, BoardQuestionPatch, Page
from app.ml.normalize import normalize, stem_hash
from app.core.errors import ApiError
from app.services.bulk import import_board_rows
from app.services.excel import make_template, read_rows
from app.api.v1.bulk import XLSX, read_upload

router = APIRouter(prefix="/board-questions", tags=["board bank"])
staff = require_roles(*STAFF)
admin_only = require_roles("admin")


@router.get("/template")
def template(_: User = Depends(admin_only)):
    return Response(make_template("board"), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="dishari_board_bank_template.xlsx"'})


@router.post("/import")
async def import_board(request: Request, file: UploadFile = File(...), admin: User = Depends(admin_only),
                       db: Session = Depends(get_db)):
    data = await read_upload(file)
    rows = read_rows(file.filename or "x.xlsx", data, settings.MAX_UPLOAD_ROWS)
    res = import_board_rows(db, rows)
    audit(db, admin, "board.import", "board_questions", None, {k: v for k, v in res.items() if k != "errors"}, request)
    db.commit()
    return res


@router.get("", response_model=Page[BoardQuestionOut])
def list_board(q: str | None = None, board: str | None = None, year: int | None = None, language: str | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               _: User = Depends(staff), db: Session = Depends(get_db)):
    stmt = select(BoardQuestion)
    if q:
        stmt = stmt.where(BoardQuestion.stem.ilike(f"%{q}%"))
    if board:
        stmt = stmt.where(BoardQuestion.board == board)
    if year:
        stmt = stmt.where(BoardQuestion.year == year)
    if language:
        stmt = stmt.where(BoardQuestion.language == language)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(BoardQuestion.year.desc(), BoardQuestion.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/check")
def check(language: str, stem: str, _: User = Depends(staff), db: Session = Depends(get_db)):
    """Live check while a teacher types a question."""
    return board_index.match(db, language, stem)


@router.get("/matches")
def matches(status: str = "suggested", _: User = Depends(staff), db: Session = Depends(get_db)):
    rows = db.execute(select(BoardMatch, Question.stem, BoardQuestion.stem, BoardQuestion.board, BoardQuestion.year)
                      .join(Question, Question.id == BoardMatch.question_id)
                      .join(BoardQuestion, BoardQuestion.id == BoardMatch.board_question_id)
                      .where(BoardMatch.status == status).order_by(BoardMatch.id.desc()).limit(200)).all()
    return [{"id": m.id, "question_id": m.question_id, "question_stem": qs, "board_question_id": m.board_question_id,
             "board_stem": bs, "board": b, "year": y, "score": m.score, "status": m.status}
            for m, qs, bs, b, y in rows]


@router.post("/matches/{match_id}")
def decide(match_id: int, body: BoardMatchDecision, user: User = Depends(staff), db: Session = Depends(get_db)):
    m = db.get(BoardMatch, match_id)
    if not m:
        raise not_found("Match")
    m.status, m.decided_by = body.status, user.id
    q = db.get(Question, m.question_id)
    if body.status == "dismissed" and q.board_question_id == m.board_question_id:
        q.is_board, q.board_question_id = False, None
    elif body.status == "confirmed":
        q.is_board, q.board_question_id = True, m.board_question_id
    audit(db, user, f"board.match.{body.status}", "question", q.id, {"board_question_id": m.board_question_id})
    db.commit()
    return {"id": m.id, "status": m.status}


def _dup(db, h: str, not_id: int | None = None):
    other = db.scalar(select(BoardQuestion).where(BoardQuestion.stem_hash == h))
    if other and other.id != not_id:
        raise ApiError(409, "BOARD_DUPLICATE", "This question is already in the board bank", {"stem": "duplicate"})


@router.post("", response_model=BoardQuestionOut, status_code=201)
def add_board_question(body: BoardQuestionIn, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    h = stem_hash(body.stem)
    _dup(db, h)
    b = BoardQuestion(board=body.board.strip(), year=body.year, q_no=body.q_no, language=body.language, stem=body.stem.strip(),
                      stem_norm=normalize(body.stem), stem_hash=h, options=body.options, answer=body.answer, chapter_id=body.chapter_id)
    db.add(b)
    db.flush()
    audit(db, admin, "board.add", "board_question", b.id, request=request)
    db.commit()
    board_index.dirty = True
    return b


@router.patch("/{bid}", response_model=BoardQuestionOut)
def edit_board_question(bid: int, body: BoardQuestionPatch, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    b = db.get(BoardQuestion, bid)
    if not b:
        raise not_found("Board question")
    data = body.model_dump(exclude_unset=True)
    if data.get("stem"):
        h = stem_hash(data["stem"])
        _dup(db, h, b.id)
        b.stem_hash, b.stem_norm = h, normalize(data["stem"])
    for k, v in data.items():
        setattr(b, k, v)
    audit(db, admin, "board.edit", "board_question", b.id, {"fields": list(data)}, request=request)
    db.commit()
    board_index.dirty = True
    return b


@router.delete("/{bid}", status_code=204)
def remove_board_question(bid: int, request: Request, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    b = db.get(BoardQuestion, bid)
    if not b:
        raise not_found("Board question")
    for m in db.scalars(select(BoardMatch).where(BoardMatch.board_question_id == bid)):
        db.delete(m)
    for q in db.scalars(select(Question).where(Question.board_question_id == bid)):
        q.board_question_id = None
    db.flush()
    db.delete(b)
    audit(db, admin, "board.remove", "board_question", bid, request=request)
    db.commit()
    board_index.dirty = True
