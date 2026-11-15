from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import STAFF, audit, require_roles
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.db.session import get_db
from app.models import BulkJob, User
from app.schemas import BulkJobOut, Page
from app.services.bulk import run_question_job
from app.services.excel import make_template

router = APIRouter(prefix="/bulk", tags=["bulk upload"])
staff = require_roles(*STAFF)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def job_out(j: BulkJob) -> dict:
    d = BulkJobOut.model_validate(j).model_dump()
    d["progress"] = round(j.processed / j.total, 3) if j.total else (1.0 if j.status == "done" else 0.0)
    d["result_url"] = f"{settings.API_PREFIX}/bulk/jobs/{j.id}/results" if j.result_path else None
    return d


async def read_upload(file: UploadFile) -> bytes:
    data = await file.read()
    if len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise bad_request("FILE_TOO_LARGE", f"Max {settings.MAX_UPLOAD_MB} MB")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm", ".csv")):
        raise bad_request("BAD_FILE_TYPE", "Upload a .xlsx or .csv file")
    return data


@router.get("/template")
def template(_: User = Depends(staff)):
    return Response(make_template("questions"), media_type=XLSX,
                    headers={"Content-Disposition": 'attachment; filename="dishari_question_template.xlsx"'})


@router.post("/upload", response_model=BulkJobOut, status_code=202)
async def upload(background: BackgroundTasks, request: Request, file: UploadFile = File(...),
                 user: User = Depends(staff), db: Session = Depends(get_db)):
    """Starts a background job. Poll GET /bulk/jobs/{id} every 1-2 s until status is done/failed."""
    data = await read_upload(file)
    job = BulkJob(owner_id=user.id, kind="questions", filename=file.filename or "upload.xlsx")
    db.add(job)
    db.flush()
    audit(db, user, "bulk.upload", "bulk_job", job.id, {"filename": job.filename}, request)
    db.commit()
    background.add_task(run_question_job, job.id, job.filename, data)
    return job_out(job)


@router.get("/jobs", response_model=Page[BulkJobOut])
def jobs(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), user: User = Depends(staff),
         db: Session = Depends(get_db)):
    stmt = select(BulkJob)
    if "admin" not in user.roles:
        stmt = stmt.where(BulkJob.owner_id == user.id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(BulkJob.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [job_out(j) for j in rows], "total": total, "page": page, "page_size": page_size}


def _job(db, job_id, user) -> BulkJob:
    j = db.get(BulkJob, job_id)
    if not j:
        raise not_found("Job")
    if j.owner_id != user.id and "admin" not in user.roles:
        raise forbidden()
    return j


@router.get("/jobs/{job_id}", response_model=BulkJobOut)
def job(job_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    return job_out(_job(db, job_id, user))


@router.get("/jobs/{job_id}/results")
def results(job_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    j = _job(db, job_id, user)
    if not j.result_path or not Path(j.result_path).exists():
        raise ApiError(409, "NOT_READY", "Results are not ready yet")
    return FileResponse(j.result_path, media_type=XLSX, filename=f"dishari_job_{j.id}_results.xlsx")
