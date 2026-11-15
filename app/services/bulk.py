"""Bulk upload jobs run in the background; the frontend polls GET /v1/bulk/jobs/{id}."""
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db import session as dbs
from app.ml.board_match import board_index
from app.ml.normalize import normalize, stem_hash
from app.models import BoardQuestion, BulkJob, QStatus
from app.services.excel import read_rows, results_workbook, row_to_item
from app.services.tagging import create_questions

log = logging.getLogger("dishari.bulk")
CHUNK = 100


def _storage() -> Path:
    p = Path(settings.STORAGE_DIR) / "bulk"
    p.mkdir(parents=True, exist_ok=True)
    return p


def run_question_job(job_id: int, filename: str, data: bytes):
    db = dbs.SessionLocal()
    job = db.get(BulkJob, job_id)
    try:
        job.status = "running"
        rows = read_rows(filename, data, settings.MAX_UPLOAD_ROWS)
        job.total = len(rows)
        db.commit()
        report, errors = [], []
        for start in range(0, len(rows), CHUNK):
            chunk = rows[start:start + CHUNK]
            items = [row_to_item(r) for r in chunk]
            res = create_questions(db, items, job.owner_id, bulk_job_id=job.id)
            for r, it, (q, err) in zip(chunk, items, res):
                line = {"row": r["_row"], "external_id": it.get("external_id")}
                if err:
                    job.failed += 1
                    errors.append({"row": r["_row"], **err})
                    line.update(status="error", error_code=err["code"], error_message=err["message"])
                else:
                    job.succeeded += 1
                    if q.status == QStatus.needs_review.value:
                        job.needs_review += 1
                    bm = (q.predictions[0].raw or {}).get("board_match") if q.predictions else None
                    line.update(question_id=q.id, status=q.status, chapter_id=q.chapter_id, concept_id=q.concept_id,
                                topic_id=q.topic_id, cognitive_level=q.cognitive_level, difficulty=q.difficulty,
                                min_confidence=q.min_confidence,
                                board_match=(f'{bm.get("board")} {bm.get("year")}' if bm and bm.get("matched") else ""))
                report.append(line)
            job.processed = min(job.total, start + len(chunk))
            job.errors = errors[:500]
            db.commit()
        out = _storage() / f"job_{job.id}_results.xlsx"
        out.write_bytes(results_workbook(report))
        job.result_path = str(out)
        job.status = "done"
    except Exception as e:
        log.exception("bulk job failed")
        db.rollback()
        job = db.get(BulkJob, job_id)
        job.status = "failed"
        job.errors = (job.errors or []) + [{"row": None, "code": getattr(e, "code", "JOB_FAILED"),
                                             "message": getattr(e, "message", str(e))[:300]}]
    job.finished_at = datetime.now(timezone.utc)
    db.commit()
    db.close()


def import_board_rows(db, rows: list[dict]) -> dict:
    """Synchronous board-bank import with duplicate skipping."""
    added = dup = bad = 0
    errors = []
    seen = set(db.scalars(select(BoardQuestion.stem_hash)))
    for r in rows:
        stem = str(r.get("question") or "").strip()
        lang = str(r.get("language") or "").strip().lower()
        try:
            year = int(str(r.get("year") or "").strip())
        except ValueError:
            year = None
        if not stem or lang not in ("bn", "en") or not r.get("board") or not year:
            bad += 1
            errors.append({"row": r["_row"], "code": "BAD_ROW", "message": "board, year, language, question required"})
            continue
        h = stem_hash(stem)
        if h in seen:
            dup += 1
            continue
        seen.add(h)
        it = row_to_item(r)
        try:
            qno = int(str(r.get("q_no") or r.get("question_no") or "").strip())
        except ValueError:
            qno = None
        db.add(BoardQuestion(board=str(r["board"]).strip(), year=year, q_no=qno, language=lang, stem=stem,
                             stem_norm=normalize(stem), stem_hash=h, options=it["options"], answer=it["answer"],
                             chapter_id=(str(r.get("chapter_id")).strip() if r.get("chapter_id") else None)))
        added += 1
    db.commit()
    board_index.dirty = True
    return {"added": added, "duplicates_skipped": dup, "invalid": bad, "errors": errors[:200]}
