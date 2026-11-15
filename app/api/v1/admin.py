from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import STAFF, audit, current_user, require_roles
from app.core.errors import ApiError, not_found
from app.db.session import SessionLocal, get_db
from app.ml.tagger import tagger
from app.models import (AuditLog, Review, BoardQuestion, ModelVersion, PracticeSession, QStatus, Question, TagReport, User)
from app.schemas import ModelDeployIn
from app.services.tagging import retag
from app.services import agreement

router = APIRouter(tags=["admin, ML & dashboard"])
admin_only = require_roles("admin")


# ----------------------------------------------------------------- ML endpoints
@router.get("/ml/status")
def ml_status(_: User = Depends(current_user)):
    """Frontend shows a banner if status != ready (questions will stay 'pending')."""
    return tagger.info()


@router.post("/ml/predict")
def ml_predict(items: list[dict], _: User = Depends(require_roles(*STAFF))):
    """Raw passthrough to the model using the ML contract (for Razzaque to test the deployed model).
    Nothing is saved."""
    outs, ms = tagger.predict_batch(items[:500])
    return {"latency_ms": ms, "outputs": [o.model_dump() for o in outs]}


@router.get("/ml/agreement")
def ml_agreement(model_version: str | None = None, format: str = Query("json", pattern="^(json|xlsx)$"),
                 _: User = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Model vs moderator agreement (per field, K1-K4 confusion matrix, precision/recall/F1, by confidence,
    by model version). format=xlsx for the capstone report."""
    rep = agreement.compute(db.scalars(select(Review)).all(), model_version)
    if format == "xlsx":
        from fastapi.responses import Response
        return Response(agreement.to_xlsx(rep),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="dishari_model_agreement.xlsx"'})
    return rep


@router.get("/ml/models")
def ml_models(_: User = Depends(require_roles(*STAFF)), db: Session = Depends(get_db)):
    """Model versions (read-only, for the AI model page)."""
    return [{"id": m.id, "version": m.version, "impl": m.impl, "metrics": m.metrics, "notes": m.notes,
             "is_active": m.is_active, "deployed_at": m.deployed_at}
            for m in db.scalars(select(ModelVersion).order_by(ModelVersion.id.desc()))]


@router.get("/admin/models")
def models(_: User = Depends(admin_only), db: Session = Depends(get_db)):
    return [{"id": m.id, "version": m.version, "impl": m.impl, "path": m.path, "metrics": m.metrics,
             "notes": m.notes, "is_active": m.is_active, "deployed_by": m.deployed_by, "deployed_at": m.deployed_at}
            for m in db.scalars(select(ModelVersion).order_by(ModelVersion.id.desc()))]


@router.post("/admin/models")
def deploy_model(body: ModelDeployIn, request: Request, admin: User = Depends(admin_only),
                 db: Session = Depends(get_db)):
    """Register a model version and (optionally) hot-load it without restarting the server."""
    mv = db.scalar(select(ModelVersion).where(ModelVersion.version == body.version))
    if not mv:
        mv = ModelVersion(version=body.version, impl=body.impl, path=body.path, metrics=body.metrics,
                          notes=body.notes, deployed_by=admin.id)
        db.add(mv)
    else:
        mv.impl, mv.path, mv.metrics, mv.notes = body.impl, body.path, body.metrics, body.notes
    if body.activate:
        status = tagger.load(body.impl, body.path or None)
        if status != "ready":
            db.rollback()
            raise ApiError(400, "MODEL_LOAD_FAILED", tagger.error or "load failed")
        for other in db.scalars(select(ModelVersion).where(ModelVersion.is_active.is_(True))):
            other.is_active = False
        mv.is_active = True
    audit(db, admin, "model.deploy", "model_version", body.version, {"impl": body.impl, "activate": body.activate},
          request)
    db.commit()
    return {"model": tagger.info(), "version_id": mv.id}


@router.post("/admin/models/reload")
def reload_model(admin: User = Depends(admin_only)):
    return {"status": tagger.load(), **tagger.info()}


def _retag_pending():
    db = SessionLocal()
    try:
        while True:
            qs = db.scalars(select(Question).where(Question.status == QStatus.pending.value,
                                                   Question.deleted.is_(False)).limit(200)).all()
            if not qs or not tagger.ready:
                break
            retag(db, qs)
            db.commit()
            if all(q.status == QStatus.pending.value for q in qs):
                break  # model still failing; avoid endless loop
    finally:
        db.close()


@router.post("/admin/retag-pending", status_code=202)
def retag_pending(background: BackgroundTasks, _: User = Depends(admin_only), db: Session = Depends(get_db)):
    """After the real model is deployed, tag every question that was saved while the model was down."""
    n = db.scalar(select(func.count(Question.id)).where(Question.status == QStatus.pending.value))
    if not tagger.ready:
        raise ApiError(409, "MODEL_NOT_READY", "Load a model first")
    background.add_task(_retag_pending)
    return {"queued": n}


@router.get("/admin/audit-logs")
def audit_logs(action: str | None = None, actor_id: int | None = None, page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=200), _: User = Depends(admin_only), db: Session = Depends(get_db)):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"id": r.id, "actor_id": r.actor_id, "action": r.action, "entity": r.entity,
                       "entity_id": r.entity_id, "details": r.details, "ip": r.ip, "created_at": r.created_at}
                      for r in rows], "total": total, "page": page, "page_size": page_size}


# ----------------------------------------------------------------- dashboard (one endpoint, role-aware)
@router.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out: dict = {"roles": user.roles, "ml": {"status": tagger.status, "version": tagger.version}}
    q_live = Question.deleted.is_(False)
    if {"admin", "teacher", "moderator"} & set(user.roles):
        by_status = dict(db.execute(select(Question.status, func.count()).where(q_live)
                                    .group_by(Question.status)).all())
        by_level = dict(db.execute(select(Question.cognitive_level, func.count()).where(q_live)
                                   .group_by(Question.cognitive_level)).all())
        out["questions"] = {"total": sum(by_status.values()), "by_status": by_status,
                            "by_level": {k or "untagged": v for k, v in by_level.items()},
                            "mine": db.scalar(select(func.count(Question.id))
                                              .where(q_live, Question.created_by == user.id))}
    if {"admin", "moderator"} & set(user.roles):
        out["review"] = {"queue": db.scalar(select(func.count(Question.id)).where(
            q_live, Question.status == QStatus.needs_review.value)),
            "open_reports": db.scalar(select(func.count(TagReport.id)).where(TagReport.status == "open"))}
    if {"admin", "teacher", "moderator"} & set(user.roles):
        from app.models import BulkJob, OmrResult, Paper, Test, TestAttempt
        from app.services import classroom
        tests = classroom.owned_tests(db, user) if "admin" not in user.roles else [t for t in classroom.owned_tests(db, user) if t.created_by == user.id]
        papers = [p for p in classroom.owned_papers(db, user) if p.owner_id == user.id]
        scores = [a.score / a.total for t in tests for a in db.scalars(select(TestAttempt).where(
            TestAttempt.test_id == t.id, TestAttempt.submitted_at.is_not(None), TestAttempt.total > 0))]
        scores += [r.score / r.total for p in papers for r in db.scalars(select(OmrResult).where(
            OmrResult.paper_id == p.id, OmrResult.score.is_not(None), OmrResult.total > 0))]
        rows = []
        for t in tests:
            rows += classroom.rows_for_test(db, t)
        for p in papers:
            rows += classroom.rows_for_paper_omr(db, p)
        jobs = db.scalars(select(BulkJob).where(BulkJob.owner_id == user.id, BulkJob.kind == "questions")
                          .order_by(BulkJob.id.desc()).limit(5)).all()
        out["teacher"] = {
            "pending_review": db.scalar(select(func.count(Question.id)).where(q_live, Question.status == QStatus.needs_review.value)),
            "bank": db.scalar(select(func.count(Question.id)).where(q_live, Question.status.in_([QStatus.verified.value, QStatus.tagged.value]))),
            "exams": len(tests) + len(papers), "tests": len(tests), "papers": len(papers),
            "class_average": round(sum(scores) / len(scores), 3) if scores else None,
            "weak_topics": classroom.weak_topics(rows, 3),
            "recent_uploads": [{"id": j.id, "filename": j.filename, "status": j.status, "total": j.total,
                                "processed": j.processed, "needs_review": j.needs_review, "created_at": j.created_at} for j in jobs]}
    if "admin" in user.roles:
        from app.models import Review
        out["admin"] = {"users": db.scalar(select(func.count(User.id))),
                        "pending_teachers": db.scalar(select(func.count(User.id)).where(User.status == "pending")),
                        "questions_tagged": db.scalar(select(func.count(Question.id)).where(q_live, Question.cognitive_level.is_not(None))),
                        "teacher_corrections": db.scalar(select(func.count(Review.id)).where(Review.action == "change")),
                        "board_questions": db.scalar(select(func.count(BoardQuestion.id))),
                        "pending_untagged": db.scalar(select(func.count(Question.id)).where(
                            q_live, Question.status == QStatus.pending.value))}
    if "student" in user.roles:
        ss = db.scalars(select(PracticeSession).where(PracticeSession.student_id == user.id,
                                                      PracticeSession.status == "submitted")
                        .order_by(PracticeSession.id.desc()).limit(5)).all()
        out["student"] = {"recent": [{"id": s.id, "score": s.score, "total": s.total, "submitted_at": s.submitted_at}
                                     for s in ss],
                          "sessions_done": db.scalar(select(func.count(PracticeSession.id)).where(
                              PracticeSession.student_id == user.id, PracticeSession.status == "submitted"))}
    return out
