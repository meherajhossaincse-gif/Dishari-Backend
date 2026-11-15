import io

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.deps import STAFF, audit, require_roles
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.db.session import get_db
from app.models import Paper, PaperItem, Question, User
from app.schemas import Msg, Page, PaperIn, PaperItemIn, PaperOut, PaperReorder, PaperUpdate
from app.services.analysis import paper_analysis
from app.services.excel import read_rows, row_to_item
from app.services.paper_export import set_order, to_board_html, to_docx
from app.services.profile import paper_header_defaults
from app.services.serialize import question_out
from app.services.tagging import preview
from app.services.taxonomy import taxonomy
from app.api.v1.bulk import XLSX, read_upload

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
router = APIRouter(prefix="/papers", tags=["papers (teacher)"])
staff = require_roles(*STAFF)


def _paper(db, pid, user) -> Paper:
    p = db.scalar(select(Paper).options(selectinload(Paper.items).selectinload(PaperItem.question)
                                        .selectinload(Question.predictions)).where(Paper.id == pid))
    if not p:
        raise not_found("Paper")
    if p.owner_id != user.id and "admin" not in user.roles:
        raise forbidden()
    return p


def _out(p: Paper, with_items=True) -> dict:
    d = {"id": p.id, "title": p.title, "language": p.language, "exam_type": p.exam_type,
         "duration_min": p.duration_min, "target": p.target, "meta": p.meta or {}, "status": p.status, "created_at": p.created_at,
         "updated_at": p.updated_at, "item_count": len(p.items), "items": []}
    if with_items:
        d["items"] = [{"position": i.position, "marks": i.marks, "question": question_out(i.question)}
                      for i in p.items]
    return d


@router.post("", response_model=PaperOut, status_code=201)
def create(body: PaperIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    data = body.model_dump()
    data["meta"] = {**paper_header_defaults(user, body.language), "exam_name": body.title,
                    **(body.meta.model_dump(exclude_none=True) if body.meta else {})}
    p = Paper(owner_id=user.id, **data)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _out(p)


@router.get("", response_model=Page[PaperOut])
def list_papers(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user: User = Depends(staff),
                db: Session = Depends(get_db)):
    stmt = select(Paper).where(Paper.owner_id == user.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.options(selectinload(Paper.items)).order_by(Paper.updated_at.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_out(p, False) for p in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/{pid}", response_model=PaperOut)
def get_paper(pid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    taxonomy.ensure(db)
    return _out(_paper(db, pid, user))


@router.patch("/{pid}", response_model=PaperOut)
def update(pid: int, body: PaperUpdate, user: User = Depends(staff), db: Session = Depends(get_db)):
    p = _paper(db, pid, user)
    data = body.model_dump(exclude_none=True)
    if "meta" in data:
        data["meta"] = {**(p.meta or {}), **data["meta"]}
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    taxonomy.ensure(db)
    return _out(p)


@router.delete("/{pid}", response_model=Msg)
def delete(pid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    db.delete(_paper(db, pid, user))
    db.commit()
    return {"message": "Deleted"}


@router.post("/{pid}/items", response_model=PaperOut)
def add_items(pid: int, body: PaperItemIn, user: User = Depends(staff), db: Session = Depends(get_db)):
    p = _paper(db, pid, user)
    if p.status == "final":
        raise ApiError(409, "PAPER_FINAL", "Paper is final; set status to draft to edit")
    have = {i.question_id for i in p.items}
    qs = db.scalars(select(Question).where(Question.id.in_(body.question_ids), Question.deleted.is_(False))).all()
    found = {q.id for q in qs}
    missing = [i for i in body.question_ids if i not in found]
    if missing:
        raise bad_request("QUESTION_NOT_FOUND", f"Unknown question ids: {missing}")
    pos = max([i.position for i in p.items], default=0)
    for qid in body.question_ids:
        if qid not in have:
            pos += 1
            p.items.append(PaperItem(question_id=qid, position=pos, marks=body.marks))
    db.commit()
    db.expire(p)
    taxonomy.ensure(db)
    return _out(_paper(db, pid, user))


@router.delete("/{pid}/items/{qid}", response_model=PaperOut)
def remove_item(pid: int, qid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    p = _paper(db, pid, user)
    p.items = [i for i in p.items if i.question_id != qid]
    for n, i in enumerate(p.items, 1):
        i.position = n
    db.commit()
    taxonomy.ensure(db)
    return _out(_paper(db, pid, user))


@router.put("/{pid}/items/order", response_model=PaperOut)
def reorder(pid: int, body: PaperReorder, user: User = Depends(staff), db: Session = Depends(get_db)):
    p = _paper(db, pid, user)
    order = {qid: n for n, qid in enumerate(body.question_ids, 1)}
    for i in p.items:
        i.position = order.get(i.question_id, 10_000 + i.position)
    db.commit()
    db.expire(p)
    taxonomy.ensure(db)
    return _out(_paper(db, pid, user))


@router.get("/{pid}/analysis")
def analysis(pid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    """Balance of the paper vs its K1-K4 target (W06 analysis panel)."""
    p = _paper(db, pid, user)
    taxonomy.ensure(db)
    items = [{"cognitive_level": i.question.cognitive_level, "difficulty": i.question.difficulty,
              "chapter_id": i.question.chapter_id, "marks": i.marks} for i in p.items]
    return paper_analysis(items, p.target)


@router.post("/analyse-upload")
async def analyse_upload(file: UploadFile = File(...), target: str = Query("K1:30,K2:30,K3:25,K4:15"),
                         user: User = Depends(staff), db: Session = Depends(get_db)):
    """Upload an existing question paper (Excel/CSV); get its K1-K4 balance. Nothing is saved."""
    data = await read_upload(file)
    rows = read_rows(file.filename or "x.xlsx", data, settings.MAX_BATCH)
    items = [row_to_item(r) for r in rows]
    outs = preview(items)
    try:
        tgt = {k: float(v) for k, v in (x.split(":") for x in target.split(",") if x)}
    except ValueError:
        raise bad_request("BAD_TARGET", "target must look like K1:30,K2:30,K3:25,K4:15")
    taxonomy.ensure(db)
    rows_a = [{"cognitive_level": (o.get("cognitive_level") or {}).get("label"),
               "difficulty": (o.get("difficulty") or {}).get("label"),
               "chapter_id": (o.get("chapter") or {}).get("id"), "marks": 1} for o in outs]
    return {"analysis": paper_analysis(rows_a, tgt),
            "items": [{"row": r["_row"], "stem": it["stem"], "prediction": o} for r, it, o in zip(rows, items, outs)]}


@router.get("/{pid}/export")
def export(pid: int, format: str = Query("xlsx", pattern="^(xlsx|docx|html)$"), include_answers: bool = True,
           set_code: str = Query("ক", alias="set", description="ক|খ|গ|ঘ (or A-D): each set has its own question order"),
           embed: bool = Query(False, description="html only: hide the toolbar (in-app preview)"),
           institution: str | None = Query(None, description="header line; default = teacher's institution"),
           user: User = Depends(staff), db: Session = Depends(get_db)):
    """xlsx = data sheet + analysis; docx = printable Word paper (+ answer key page);
    html = print-ready page -> browser 'Save as PDF' (best Bangla rendering)."""
    p = _paper(db, pid, user)
    taxonomy.ensure(db)
    safe = "".join(ch for ch in p.title if ch.isascii() and (ch.isalnum() or ch in " -_"))[:40].strip() or f"paper_{p.id}"
    if format in ("docx", "html"):
        items = set_order([{"question": i.question, "marks": i.marks} for i in p.items], p.id, set_code)
        inst = institution if institution is not None else (user.institution or "")
        if not p.meta:  # papers made before headers existed: fill from the teacher's profile
            p.meta = {**paper_header_defaults(user, p.language), "exam_name": p.title}
        if format == "docx":
            return Response(to_docx(p, items, p.language, inst, include_answers), media_type=DOCX,
                            headers={"Content-Disposition": f'attachment; filename="{safe}.docx"'})
        return HTMLResponse(to_board_html(p, items, p.language, inst, include_answers, set_code, embed))
    wb = Workbook()
    ws = wb.active
    ws.title = "Paper"
    ws.append([p.title])
    ws["A1"].font = Font(bold=True, size=14)
    head = ["#", "Question", "A", "B", "C", "D"] + (["Answer"] if include_answers else []) + \
           ["Level", "Difficulty", "Chapter", "Marks"]
    ws.append(head)
    for c in ws[2]:
        c.font = Font(bold=True)
    for i in p.items:
        q = i.question
        ch = taxonomy.chapters.get(q.chapter_id or "", {})
        ws.append([i.position, q.stem, q.options.get("A"), q.options.get("B"), q.options.get("C"), q.options.get("D")]
                  + ([q.answer] if include_answers else [])
                  + [q.cognitive_level, q.difficulty, ch.get("name_bn" if p.language == "bn" else "name_en"), i.marks])
    an = paper_analysis([{"cognitive_level": i.question.cognitive_level, "difficulty": i.question.difficulty,
                          "chapter_id": i.question.chapter_id, "marks": i.marks} for i in p.items], p.target)
    ws2 = wb.create_sheet("Analysis")
    ws2.append(["Level", "Count", "Percent", "Target", "Gap"])
    for r in an["levels"]:
        ws2.append([r["level"], r["count"], r["percent"], r["target"], r["gap"]])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{safe}.xlsx"'})
