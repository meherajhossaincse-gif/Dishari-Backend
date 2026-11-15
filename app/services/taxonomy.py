"""In-memory taxonomy lookup (IDs -> names, parent links). Reloaded after admin renames."""
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ml.contract import LEVEL_NAMES
from app.models import Chapter, Concept, Topic


class TaxonomyCache:
    def __init__(self):
        self._lock = threading.Lock()
        self.chapters: dict[str, dict] = {}
        self.concepts: dict[str, dict] = {}
        self.topics: dict[str, dict] = {}
        self.loaded = False

    def reload(self, db: Session):
        with self._lock:
            self.chapters = {c.id: {"name_en": c.name_en, "name_bn": c.name_bn}
                             for c in db.scalars(select(Chapter))}
            self.concepts = {c.id: {"name_en": c.name_en, "name_bn": c.name_bn, "chapter_id": c.chapter_id}
                             for c in db.scalars(select(Concept))}
            self.topics = {t.id: {"name_en": t.name_en, "name_bn": t.name_bn, "chapter_id": t.chapter_id,
                                  "concept_id": t.concept_id} for t in db.scalars(select(Topic))}
            self.loaded = True

    def ensure(self, db: Session):
        if not self.loaded:
            self.reload(db)

    def view(self, kind: str, id_: str | None, conf: float | None = None) -> dict | None:
        if id_ is None:
            return None
        if kind == "cognitive_level":
            n = LEVEL_NAMES.get(id_, {})
            return {"id": id_, "name_en": n.get("en"), "name_bn": n.get("bn"), "confidence": conf}
        if kind == "difficulty":
            bn = {"Easy": "সহজ", "Medium": "মধ্যম", "Hard": "কঠিন"}.get(id_)
            return {"id": id_, "name_en": id_, "name_bn": bn, "confidence": conf}
        src = {"chapter": self.chapters, "concept": self.concepts, "topic": self.topics}[kind].get(id_, {})
        return {"id": id_, "name_en": src.get("name_en"), "name_bn": src.get("name_bn"), "confidence": conf}

    def check_consistency(self, chapter_id, concept_id, topic_id) -> list[str]:
        """Returns problems: unknown IDs or topic/concept that don't belong to the chapter."""
        errs = []
        if chapter_id and chapter_id not in self.chapters:
            errs.append(f"unknown chapter_id {chapter_id}")
        if concept_id:
            c = self.concepts.get(concept_id)
            if not c:
                errs.append(f"unknown concept_id {concept_id}")
            elif chapter_id and c["chapter_id"] != chapter_id:
                errs.append(f"concept {concept_id} is not in chapter {chapter_id}")
        if topic_id:
            t = self.topics.get(topic_id)
            if not t:
                errs.append(f"unknown topic_id {topic_id}")
            elif concept_id and t["concept_id"] != concept_id:
                errs.append(f"topic {topic_id} is not in concept {concept_id}")
        return errs


taxonomy = TaxonomyCache()
