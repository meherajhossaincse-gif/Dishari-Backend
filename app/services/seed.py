"""Idempotent seed: taxonomy (from seeds/taxonomy.json), K1-K4 levels, demo users, demo questions.
Safe to run on every startup - it only inserts what is missing."""
import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.models import Chapter, CognitiveLevel, Concept, ModelVersion, ParentLink, Question, Topic, User
from app.services.tagging import create_questions
from app.services.taxonomy import taxonomy

log = logging.getLogger("dishari.seed")
SEEDS = Path(__file__).resolve().parents[2] / "seeds"

LEVELS = [
    ("K1", "Knowledge", "জ্ঞান", "Recall facts, terms, definitions, units.", "তথ্য, সংজ্ঞা, একক ইত্যাদি মনে করা।"),
    ("K2", "Comprehension", "অনুধাবন", "Explain, compare, interpret a concept.", "ধারণা ব্যাখ্যা, তুলনা ও বোঝা।"),
    ("K3", "Application", "প্রয়োগ", "Use a rule or formula in a new situation, calculate.",
     "নতুন পরিস্থিতিতে সূত্র বা নিয়ম প্রয়োগ, গাণিতিক সমাধান।"),
    ("K4", "Higher ability", "উচ্চতর দক্ষতা", "Analyse, evaluate, multi-statement reasoning.",
     "বিশ্লেষণ, মূল্যায়ন, বহুপদী যুক্তি।"),
]

DEMO_USERS = [
    ("Admin", "admin@dishari.com", ["admin"]),
    ("Teacher Demo", "teacher@dishari.com", ["teacher"]),
    ("Moderator Demo", "moderator@dishari.com", ["teacher", "moderator"]),
    ("Student Demo", "student@dishari.com", ["student"]),
    ("Parent Demo", "parent@dishari.com", ["parent"]),
]


def seed_taxonomy(db: Session, path: Path | None = None) -> dict:
    data = json.loads((path or SEEDS / "taxonomy.json").read_text(encoding="utf-8"))
    n = {"chapters": 0, "concepts": 0, "topics": 0}
    for ci, ch in enumerate(data["chapters"]):
        row = db.get(Chapter, ch["id"])
        if not row:
            db.add(Chapter(id=ch["id"], name_en=ch["name_en"], name_bn=ch["name_bn"], sort_order=ci))
            n["chapters"] += 1
        for ki, co in enumerate(ch["concepts"]):
            if not db.get(Concept, co["id"]):
                db.add(Concept(id=co["id"], chapter_id=ch["id"], name_en=co["name_en"], name_bn=co["name_bn"],
                               sort_order=ki))
                n["concepts"] += 1
            for ti, tp in enumerate(co["topics"]):
                trow = db.get(Topic, tp["id"])
                if not trow:
                    db.add(Topic(id=tp["id"], concept_id=co["id"], chapter_id=ch["id"], name_en=tp["name_en"],
                                 name_bn=tp["name_bn"], sort_order=ti,
                                 needs_translation=tp.get("needs_translation", False)))
                    n["topics"] += 1
                elif trow.needs_translation and not tp.get("needs_translation", False):
                    # seed file now has the missing translation; admin-renamed rows are never touched
                    trow.name_en, trow.name_bn, trow.needs_translation = tp["name_en"], tp["name_bn"], False
                    n["translated"] = n.get("translated", 0) + 1
        db.flush()
    for code, en, bn, den, dbn in LEVELS:
        if not db.get(CognitiveLevel, code):
            db.add(CognitiveLevel(code=code, name_en=en, name_bn=bn, description_en=den, description_bn=dbn))
    db.commit()
    taxonomy.reload(db)
    return n


def seed_users(db: Session):
    ids = {}
    for name, email, roles in DEMO_USERS:
        u = db.scalar(select(User).where(User.email == email))
        if not u:
            u = User(name=name, email=email, password_hash=hash_password(settings.DEMO_PASSWORD), roles=roles)
            db.add(u)
            db.flush()
        ids[email] = u.id
    from app.services.profile import new_link_code
    for u in db.scalars(select(User)):
        if "student" in u.roles and not u.link_code:
            u.link_code = new_link_code(db)
        if u.profile is None:
            u.profile = {}
    tc = db.get(User, ids["teacher@dishari.com"])
    if not tc.institution:
        tc.institution = "Dishari Demo School"
        tc.profile = {**(tc.profile or {}), "subjects": "পদার্থবিজ্ঞান", "classes_taught": "10", "designation": "সহকারী শিক্ষক"}
    st = db.get(User, ids["student@dishari.com"])
    if not st.class_level:
        st.class_level, st.institution = "10", "Dishari Model School"
        st.profile = {"section": "A", "roll": "12", "group": "Science", "board": "Dhaka", "ssc_year": "2027"}
    if not db.scalar(select(ParentLink)):
        db.add(ParentLink(parent_id=ids["parent@dishari.com"], student_id=ids["student@dishari.com"]))
    db.commit()
    return ids


def seed_demo_questions(db: Session, teacher_id: int):
    if db.scalar(select(Question.id).limit(1)):
        return 0
    items = json.loads((SEEDS / "demo_questions.json").read_text(encoding="utf-8"))
    res = create_questions(db, items, teacher_id)
    db.commit()
    return sum(1 for q, e in res if q)


def run(db: Session):
    t = seed_taxonomy(db)
    from app.ml.tagger import tagger
    if tagger.ready and not db.scalar(select(ModelVersion).where(ModelVersion.version == tagger.version)):
        db.add(ModelVersion(version=tagger.version, impl=tagger.impl_name, path=settings.MODEL_PATH,
                            is_active=True, notes="registered automatically on startup"))
        db.commit()
    if settings.SEED_DEMO_USERS:
        ids = seed_users(db)
        nq = seed_demo_questions(db, ids["teacher@dishari.com"])
        log.info("Seeded taxonomy %s, demo questions %s", t, nq)
