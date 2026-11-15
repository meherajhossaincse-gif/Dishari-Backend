from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.deps import audit, current_user, require_roles
from app.core.errors import not_found
from app.db.session import get_db
from app.models import Chapter, CognitiveLevel, Concept, Topic, User, DIFFICULTIES
from app.schemas import ChapterOut, LevelOutS, RenameIn, TaxonomyOut
from app.services.taxonomy import taxonomy

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])


@router.get("", response_model=TaxonomyOut)
def full_taxonomy(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """The whole tree in one call (13 chapters / 91 concepts / 193 topics) - cache it on the frontend."""
    chapters = db.scalars(select(Chapter).options(selectinload(Chapter.concepts).selectinload(Concept.topics))
                          .order_by(Chapter.sort_order)).all()
    return {
        "version": "2026.1",
        "chapters": chapters,
        "cognitive_levels": db.scalars(select(CognitiveLevel).order_by(CognitiveLevel.code)).all(),
        "difficulties": DIFFICULTIES,
        "counts": {"chapters": len(chapters), "concepts": db.scalar(select(func.count(Concept.id))),
                   "topics": db.scalar(select(func.count(Topic.id)))},
    }


@router.get("/chapters", response_model=list[ChapterOut])
def chapters(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = db.scalars(select(Chapter).order_by(Chapter.sort_order)).all()
    return [{"id": c.id, "name_en": c.name_en, "name_bn": c.name_bn, "concepts": []} for c in rows]


@router.get("/levels", response_model=list[LevelOutS])
def levels(db: Session = Depends(get_db)):
    """Public: K1-K4 definitions (used on the landing/help page too)."""
    return db.scalars(select(CognitiveLevel).order_by(CognitiveLevel.code)).all()


@router.patch("/{kind}/{item_id}")
def rename(kind: str, item_id: str, body: RenameIn, request: Request, admin: User = Depends(require_roles("admin")),
           db: Session = Depends(get_db)):
    """Admin can rename EN/BN names. IDs never change (the ML model depends on them)."""
    model = {"chapters": Chapter, "concepts": Concept, "topics": Topic}.get(kind)
    if not model:
        raise not_found("Taxonomy kind")
    row = db.get(model, item_id)
    if not row:
        raise not_found("Taxonomy item")
    before = {"name_en": row.name_en, "name_bn": row.name_bn}
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v.strip())
    if kind == "topics":
        row.needs_translation = False
    audit(db, admin, "taxonomy.rename", kind, item_id, {"before": before, "after": body.model_dump(exclude_none=True)},
          request)
    db.commit()
    taxonomy.reload(db)
    return {"id": row.id, "name_en": row.name_en, "name_bn": row.name_bn}
