"""Turns DB rows into the exact JSON the frontend renders."""
from app.core.config import settings
from app.models import Question
from app.services.media import image_url
from app.services.taxonomy import taxonomy


def question_out(q: Question, hide_answer: bool = False) -> dict:
    p = q.predictions[0] if q.predictions else None
    conf = {}
    if p is not None and q.tag_source == "model":
        conf = {"chapter": p.chapter_conf, "concept": p.concept_conf, "topic": p.topic_conf,
                "cognitive_level": p.cognitive_conf, "difficulty": p.difficulty_conf}
    low = [f for f, c in conf.items() if c is not None and c < settings.REVIEW_THRESHOLD]
    raw = (p.raw if p is not None else {}) or {}
    return {
        "id": q.id, "external_id": q.external_id, "language": q.language, "stem": q.stem, "options": q.options,
        "answer": None if hide_answer else q.answer, "explanation": None if hide_answer else q.explanation,
        "source": q.source, "figure_dependent": q.figure_dependent, "image_url": image_url(q.image_path),
        "status": q.status, "tag_source": q.tag_source,
        "chapter": taxonomy.view("chapter", q.chapter_id, conf.get("chapter")),
        "concept": taxonomy.view("concept", q.concept_id, conf.get("concept")),
        "topic": taxonomy.view("topic", q.topic_id, conf.get("topic")),
        "cognitive_level": taxonomy.view("cognitive_level", q.cognitive_level, conf.get("cognitive_level")),
        "difficulty": taxonomy.view("difficulty", q.difficulty, conf.get("difficulty")),
        "min_confidence": q.min_confidence, "low_confidence_fields": low,
        "board_match": raw.get("board_match"), "model_version": p.model_version if p is not None else None,
        "warnings": raw.get("warnings", []) + ([raw["error"]["code"]] if raw.get("error") else []),
        "created_by": q.created_by, "created_at": q.created_at, "updated_at": q.updated_at,
    }


def student_question(q: Question) -> dict:
    """What a student sees during practice: no answer, no confidence, only level + chapter names."""
    return {"id": q.id, "language": q.language, "stem": q.stem, "options": q.options,
            "figure_dependent": q.figure_dependent, "image_url": image_url(q.image_path),
            "chapter": taxonomy.view("chapter", q.chapter_id),
            "cognitive_level": taxonomy.view("cognitive_level", q.cognitive_level)}
