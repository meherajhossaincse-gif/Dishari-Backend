"""Question create + tag pipeline (FR: validate -> ML -> board match -> store -> status)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.board_match import board_index
from app.ml.contract import MLOutput, ContractError, validate_input
from app.ml.normalize import stem_hash
from app.ml.tagger import tagger
from app.models import BoardMatch, ModelPrediction, QStatus, Question
from app.services.taxonomy import taxonomy

TAG_FIELDS = ("chapter", "concept", "topic", "cognitive_level", "difficulty")


def to_ml_input(q: Question) -> dict:
    return {"id": str(q.id), "language": q.language, "stem": q.stem, "options": q.options, "answer": q.answer}


def _conf(out: MLOutput, f: str):
    v = getattr(out, f)
    return v.confidence if v is not None else None


def apply_prediction(db: Session, q: Question, out: MLOutput, latency_ms: int = 0) -> Question:
    """Store the raw output, copy tags onto the question, set status. Moderator tags are never overwritten."""
    taxonomy.ensure(db)
    warnings = list(out.warnings)

    if out.error:
        q.status = QStatus.pending.value if out.error.code in ("MODEL_NOT_READY", "MODEL_ERROR") else QStatus.needs_review.value
        q.predictions.insert(0, ModelPrediction(model_version=out.model_version, raw=out.model_dump(),
                                                latency_ms=latency_ms))
        return q

    ch = out.chapter.id if out.chapter else None
    co = out.concept.id if out.concept else None
    tp = out.topic.id if out.topic else None
    problems = taxonomy.check_consistency(ch, co, tp)
    # derive missing parents from child IDs when the model only gives the child
    if not problems:
        if tp and not co:
            co = taxonomy.topics[tp]["concept_id"]
        if co and not ch:
            ch = taxonomy.concepts[co]["chapter_id"]
    warnings += problems

    pred = ModelPrediction(
        model_version=out.model_version,
        chapter_id=ch, chapter_conf=_conf(out, "chapter"),
        concept_id=co, concept_conf=_conf(out, "concept"),
        topic_id=tp, topic_conf=_conf(out, "topic"),
        cognitive_level=out.cognitive_level.label if out.cognitive_level else None,
        cognitive_conf=_conf(out, "cognitive_level"),
        difficulty=out.difficulty.label if out.difficulty else None,
        difficulty_conf=_conf(out, "difficulty"),
        raw={**out.model_dump(), "warnings": warnings}, latency_ms=latency_ms,
    )
    q.predictions.insert(0, pred)

    if q.tag_source == "moderator" and q.status == QStatus.verified.value:
        return q  # a human-verified question keeps its tags; prediction is stored for comparison only

    if not problems:
        q.chapter_id, q.concept_id, q.topic_id = ch, co, tp
    q.cognitive_level = pred.cognitive_level
    q.difficulty = pred.difficulty
    q.tag_source = "model"
    q.min_confidence = out.min_confidence()
    low = q.min_confidence is None or q.min_confidence < settings.REVIEW_THRESHOLD
    q.status = QStatus.needs_review.value if (low or problems) else QStatus.tagged.value

    # board match (backend's job, not the model's)
    bm = out.board_match.model_dump() if out.board_match else board_index.match(db, q.language, q.stem)
    pred.raw = {**pred.raw, "board_match": bm}
    if bm.get("matched") and bm.get("board_question_id"):
        q.is_board = True
        q.board_question_id = bm["board_question_id"]
        exists = db.scalar(select(BoardMatch).where(BoardMatch.question_id == q.id,
                                                    BoardMatch.board_question_id == bm["board_question_id"]))
        if not exists:
            db.add(BoardMatch(question_id=q.id, board_question_id=bm["board_question_id"], score=bm["score"]))
    return q


def create_questions(db: Session, items: list[dict], user_id: int | None, bulk_job_id: int | None = None,
                     run_model: bool = True) -> list[tuple[Question | None, dict | None]]:
    """Validate + insert + tag a list of raw dicts. Returns [(question, error)] in input order."""
    results: list[tuple[Question | None, dict | None]] = []
    created: list[Question] = []
    for it in items:
        try:
            x = validate_input(it)
        except ContractError as e:
            results.append((None, {"code": e.code, "message": e.message}))
            continue
        q = Question(language=x.language, stem=x.stem, options=x.options, answer=x.answer,
                     explanation=it.get("explanation"), source=it.get("source"),
                     figure_dependent=bool(it.get("figure_dependent", False)), external_id=it.get("external_id"),
                     stem_hash=stem_hash(x.stem), created_by=user_id, bulk_job_id=bulk_job_id,
                     status=QStatus.pending.value)
        db.add(q)
        created.append(q)
        results.append((q, None))
    db.flush()  # assign ids
    if run_model and created:
        outs, ms = tagger.predict_batch([to_ml_input(q) for q in created])
        per = ms // max(1, len(created))
        for q, out in zip(created, outs):
            apply_prediction(db, q, out, per)
    return results


def preview(items: list[dict]) -> list[dict]:
    """Tag without saving (used by paper 'analyse upload' and the tag preview)."""
    good, res = [], []
    for i, it in enumerate(items):
        try:
            x = validate_input({**it, "id": str(i)})
            good.append(x.model_dump())
            res.append(None)
        except ContractError as e:
            res.append({"id": str(i), "error": {"code": e.code, "message": e.message}, "model_version": tagger.version})
    outs, _ = tagger.predict_batch(good)
    it_out = iter(outs)
    return [r if r is not None else next(it_out).model_dump() for r in res]


def retag(db: Session, questions: list[Question]) -> int:
    if not questions:
        return 0
    outs, ms = tagger.predict_batch([to_ml_input(q) for q in questions])
    for q, out in zip(questions, outs):
        apply_prediction(db, q, out, ms // len(questions))
    return len(questions)
