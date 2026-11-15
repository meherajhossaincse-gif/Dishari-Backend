"""Paper exams with OMR sheets: print the matching sheet, upload filled sheets, get marks per student."""
import io

import anyio
from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import HTMLResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import STAFF, audit, require_roles
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.db.session import get_db
from app.models import OmrResult, Paper, PaperItem, Question, User
from app.services import omr
from app.services.analysis import score_practice
from app.services.paper_export import SET_CODES, set_order
from app.services.profile import paper_header_defaults

router = APIRouter(tags=["OMR (paper exams)"])
staff = require_roles(*STAFF)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _paper(db, pid, user) -> Paper:
    p = db.scalar(select(Paper).options(selectinload(Paper.items).selectinload(PaperItem.question)).where(Paper.id == pid))
    if not p:
        raise not_found("Paper")
    if p.owner_id != user.id and "admin" not in user.roles:
        raise forbidden()
    return p


def _key(p: Paper, set_letter: str | None) -> tuple[dict, list]:
    """Answer key {question number: letter} for a set, plus the questions in that set's order."""
    code = SET_CODES["ABCD".index(set_letter)] if set_letter in ("A", "B", "C", "D") else "ক"
    items = set_order([{"question": i.question, "marks": i.marks} for i in sorted(p.items, key=lambda i: i.position)], p.id, code)
    return {n: it["question"].answer for n, it in enumerate(items, 1)}, [it["question"] for it in items]


def _grade(r: OmrResult, p: Paper):
    key, qs = _key(p, r.set_code)
    rows = []
    for n, q in enumerate(qs, 1):
        a = (r.answers or {}).get(str(n))
        rows.append({"cognitive_level": q.cognitive_level, "chapter_id": q.chapter_id, "is_correct": a is not None and a == key[n]})
    r.total = len(qs)
    r.score = sum(1 for x in rows if x["is_correct"]) if r.set_code else None
    r.breakdown = score_practice(rows) if r.set_code else {}


def _match_student(db, p: Paper, roll: str | None) -> int | None:
    if not roll or "?" in roll:
        return None
    cls = str((p.meta or {}).get("class_level") or "").strip()
    found = [u for u in db.scalars(select(User)) if "student" in (u.roles or [])
             and str((u.profile or {}).get("roll") or "").lstrip("0") == roll.lstrip("0")
             and (not cls or (u.class_level or "").strip() == cls)]
    return found[0].id if len(found) == 1 else None


def _out(r: OmrResult, names: dict) -> dict:
    return {"id": r.id, "filename": r.filename, "set": r.set_code, "set_bn": SET_CODES["ABCD".index(r.set_code)] if r.set_code else None,
            "roll": r.roll, "student_id": r.student_id, "student_name": names.get(r.student_id), "answers": r.answers,
            "score": r.score, "total": r.total, "breakdown": r.breakdown, "flags": r.flags, "image_url": r.image_url,
            "edited": r.edited, "created_at": r.created_at}


@router.get("/papers/{pid}/omr-sheet", response_class=HTMLResponse)
def omr_sheet(pid: int, set_code: str | None = Query(None, alias="set"), embed: bool = False, user: User = Depends(staff),
              db: Session = Depends(get_db)):
    """Printable OMR answer sheet that matches this paper (number of questions, header)."""
    p = _paper(db, pid, user)
    header = {**paper_header_defaults(user, p.language), **(p.meta or {}), "exam_name": (p.meta or {}).get("exam_name") or p.title}
    return HTMLResponse(omr.sheet_html(len(p.items), p.language, header, set_code, embed))


@router.post("/papers/{pid}/omr")
async def upload_sheets(pid: int, files: list[UploadFile] = File(...), user: User = Depends(staff), db: Session = Depends(get_db)):
    """Upload 1–60 photos/scans of filled OMR sheets. Each is read, graded with its set's key and matched to a student by roll."""
    p = _paper(db, pid, user)
    if not p.items:
        raise bad_request("EMPTY_PAPER", "This paper has no questions")
    if len(files) > 60:
        raise bad_request("TOO_MANY_FILES", "Upload at most 60 sheets at a time")
    n = len(p.items)
    out, errors = [], []
    for f in files:
        data = await f.read()
        try:
            res = await anyio.to_thread.run_sync(omr.read_sheet, data, n)
        except ApiError as e:
            errors.append({"filename": f.filename, "code": e.code, "message": e.message})
            continue
        r = OmrResult(paper_id=p.id, uploaded_by=user.id, filename=f.filename or "", set_code=res["set"], roll=res["roll"],
                      answers={str(k): v for k, v in res["answers"].items()}, flags=res["flags"])
        _grade(r, p)
        key, _ = _key(p, r.set_code)
        r.image_url = omr.save_overlay(omr.overlay_png(res["warped"], n, res["answers"], key if r.set_code else {}, r.set_code, r.roll))
        r.student_id = _match_student(db, p, r.roll)
        db.add(r)
        out.append(r)
    audit(db, user, "omr.upload", "paper", p.id, {"read": len(out), "failed": len(errors)})
    db.commit()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_([r.student_id for r in out if r.student_id] or [0])))}
    return {"results": [_out(r, names) for r in out], "errors": errors}


@router.get("/papers/{pid}/omr")
def list_results(pid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    p = _paper(db, pid, user)
    rows = db.scalars(select(OmrResult).where(OmrResult.paper_id == p.id).order_by(OmrResult.roll, OmrResult.id)).all()
    names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_([r.student_id for r in rows if r.student_id] or [0])))}
    scored = [r.score / r.total for r in rows if r.score is not None and r.total]
    return {"results": [_out(r, names) for r in rows], "count": len(rows),
            "average": round(sum(scored) / len(scored), 3) if scored else None, "highest": max(scored) if scored else None,
            "needs_check": sum(1 for r in rows if r.flags or not r.set_code or not r.student_id)}


class OmrFix(BaseModel):
    set: str | None = None
    roll: str | None = None
    student_id: int | None = None
    answers: dict[str, str | None] | None = None


@router.patch("/omr-results/{rid}")
def fix_result(rid: int, body: OmrFix, user: User = Depends(staff), db: Session = Depends(get_db)):
    """Teacher corrects what the reader saw (set, roll, a bubble, or the student)."""
    r = db.get(OmrResult, rid)
    if not r:
        raise not_found("Result")
    p = _paper(db, r.paper_id, user)
    if body.set is not None:
        s = {"ক": "A", "খ": "B", "গ": "C", "ঘ": "D"}.get(body.set, body.set.upper())
        if s not in "ABCD" or len(s) != 1:
            raise bad_request("BAD_SET", "Set must be ক/খ/গ/ঘ or A-D")
        r.set_code = s
    if body.roll is not None:
        r.roll = body.roll.strip() or None
        r.student_id = _match_student(db, p, r.roll)
    if body.student_id is not None:
        st = db.get(User, body.student_id)
        if not st or "student" not in st.roles:
            raise bad_request("BAD_STUDENT", "Not a student")
        r.student_id = st.id
    if body.answers is not None:
        r.answers = {**(r.answers or {}), **{k: (v.upper() if v else None) for k, v in body.answers.items()}}
    r.flags = [f for f in (r.flags or []) if not (body.set and f.startswith("set_")) and not (body.answers and f.startswith("q"))]
    r.edited = True
    _grade(r, p)
    db.commit()
    names = {r.student_id: db.get(User, r.student_id).name} if r.student_id else {}
    return _out(r, names)


@router.delete("/omr-results/{rid}")
def delete_result(rid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    r = db.get(OmrResult, rid)
    if not r:
        raise not_found("Result")
    _paper(db, r.paper_id, user)
    db.delete(r)
    db.commit()
    return {"message": "Deleted"}


@router.get("/papers/{pid}/omr/export")
def export_results(pid: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    p = _paper(db, pid, user)
    data = list_results(pid, user, db)
    wb = Workbook()
    ws = wb.active
    ws.title = "Results"
    ws.append([p.title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Roll", "Student", "Set", "Score", "Total", "%", "K1", "K2", "K3", "K4", "Flags", "File"])
    for c in ws[2]:
        c.font = Font(bold=True)
    for r in data["results"]:
        lv = (r["breakdown"] or {}).get("by_level", {})
        ws.append([r["roll"], r["student_name"], r["set_bn"], r["score"], r["total"],
                   round(100 * r["score"] / r["total"], 1) if r["score"] is not None and r["total"] else None,
                   *[f'{lv.get(k, {}).get("correct", 0)}/{lv.get(k, {}).get("total", 0)}' for k in ("K1", "K2", "K3", "K4")],
                   ", ".join(r["flags"] or []), r["filename"]])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="omr_results_{p.id}.xlsx"'})
