"""Class-level analysis for teachers: one place that turns online-test attempts and OMR sheets into
per-student, per-question rows, then into a class report (average, weakest topics, score by level,
students who need attention)."""
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import LEVELS, GroupMember, OmrResult, Paper, Question, StudentGroup, Test, TestAttempt, User
from app.services.paper_export import SET_CODES, set_order
from app.services.taxonomy import taxonomy

ATTENTION_BELOW = 0.5


def _omr_questions(p: Paper, set_letter: str) -> list[Question]:
    code = SET_CODES["ABCD".index(set_letter)] if set_letter in ("A", "B", "C", "D") else "ক"
    items = set_order([{"question": i.question, "marks": i.marks} for i in sorted(p.items, key=lambda i: i.position)],
                      p.id, code)
    return [it["question"] for it in items]


def _row(sid: int, src: str, q: Question, ok: bool) -> dict:
    return {"student_id": sid, "src": src, "question_id": q.id, "topic_id": q.topic_id, "concept_id": q.concept_id,
            "chapter_id": q.chapter_id, "level": q.cognitive_level, "difficulty": q.difficulty, "is_correct": ok}


def rows_for_test(db: Session, t: Test) -> list[dict]:
    out = []
    for a in db.scalars(select(TestAttempt).where(TestAttempt.test_id == t.id, TestAttempt.submitted_at.is_not(None))):
        qs = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(a.question_order or [0])))}
        for qid in a.question_order or []:
            q = qs.get(qid)
            if q:
                out.append(_row(a.student_id, f"test:{t.id}", q, (a.answers or {}).get(str(qid)) == q.answer))
    return out


def rows_for_paper_omr(db: Session, p: Paper) -> list[dict]:
    out, cache = [], {}
    for r in db.scalars(select(OmrResult).where(OmrResult.paper_id == p.id, OmrResult.student_id.is_not(None),
                                                OmrResult.set_code.is_not(None))):
        qs = cache.setdefault(r.set_code, _omr_questions(p, r.set_code))
        for n, q in enumerate(qs, 1):
            out.append(_row(r.student_id, f"omr:{p.id}", q, (r.answers or {}).get(str(n)) == q.answer))
    return out


def owned_tests(db: Session, user: User):
    q = select(Test).order_by(Test.id.desc())
    return db.scalars(q if "admin" in user.roles else q.where(Test.created_by == user.id)).all()


def owned_papers(db: Session, user: User):
    q = select(Paper).options(selectinload(Paper.items)).order_by(Paper.id.desc())
    return db.scalars(q if "admin" in user.roles else q.where(Paper.owner_id == user.id)).all()


def group_student_ids(db: Session, gid: int) -> set[int]:
    return set(db.scalars(select(GroupMember.student_id).where(GroupMember.group_id == gid)))


def collect(db: Session, user: User, test_id: int | None = None, paper_id: int | None = None,
            group_id: int | None = None) -> tuple[str, list[dict]]:
    """Rows for one test, one paper (its OMR sheets + its online tests) or one group (everything of the
    teacher's that its members took). Returns (title, rows)."""
    if test_id:
        t = db.get(Test, test_id)
        return (t.title if t else "?"), (rows_for_test(db, t) if t else [])
    if paper_id:
        p = db.scalar(select(Paper).options(selectinload(Paper.items)).where(Paper.id == paper_id))
        if not p:
            return "?", []
        rows = rows_for_paper_omr(db, p)
        for t in db.scalars(select(Test).where(Test.paper_id == p.id)):
            rows += rows_for_test(db, t)
        return p.title, rows
    rows = []
    for t in owned_tests(db, user):
        rows += rows_for_test(db, t)
    for p in owned_papers(db, user):
        rows += rows_for_paper_omr(db, p)
    if group_id:
        g = db.get(StudentGroup, group_id)
        ids = group_student_ids(db, group_id)
        return (g.name if g else "?"), [r for r in rows if r["student_id"] in ids]
    return "all", rows


def _name(kind: str, id_: str | None, lang: str) -> str | None:
    src = {"topic": taxonomy.topics, "concept": taxonomy.concepts, "chapter": taxonomy.chapters}[kind]
    d = src.get(id_ or "", {})
    return d.get("name_bn" if lang == "bn" else "name_en") or d.get("name_en")


def weak_topics(rows: list[dict], limit: int = 5, min_n: int = 2) -> list[dict]:
    agg: dict = defaultdict(lambda: [0, 0])
    for r in rows:
        key = ("topic", r["topic_id"]) if r["topic_id"] else ("concept", r["concept_id"]) if r["concept_id"] else None
        if key:
            agg[key][0] += int(r["is_correct"])
            agg[key][1] += 1
    out = [{"kind": k, "id": i, "name_en": _name(k, i, "en"), "name_bn": _name(k, i, "bn"), "correct": c, "total": n,
            "accuracy": round(c / n, 3)} for (k, i), (c, n) in agg.items() if n >= min_n]
    return sorted(out, key=lambda x: (x["accuracy"], -x["total"]))[:limit]


def report(db: Session, title: str, rows: list[dict]) -> dict:
    taxonomy.ensure(db)
    per_student: dict = defaultdict(list)
    for r in rows:
        per_student[r["student_id"]].append(r)
    names = {u.id: u for u in db.scalars(select(User).where(User.id.in_(list(per_student) or [0])))}
    students = []
    for sid, rs in per_student.items():
        u = names.get(sid)
        pct = sum(r["is_correct"] for r in rs) / len(rs)
        wt = weak_topics(rs, 1, 1)
        students.append({"student_id": sid, "name": u.name if u else "?", "roll": ((u.profile or {}).get("roll") if u else None),
                         "class_level": u.class_level if u else None, "answered": len(rs),
                         "correct": sum(r["is_correct"] for r in rs), "score": round(pct, 3),
                         "weakest": wt[0] if wt else None})
    students.sort(key=lambda s: s["score"])
    by_level = {k: {"correct": 0, "total": 0} for k in LEVELS}
    for r in rows:
        if r["level"] in by_level:
            by_level[r["level"]]["total"] += 1
            by_level[r["level"]]["correct"] += int(r["is_correct"])
    avg = round(sum(s["score"] for s in students) / len(students), 3) if students else None
    return {"title": title, "students_count": len(students), "average": avg,
            "need_attention": sum(1 for s in students if s["score"] < ATTENTION_BELOW),
            "attention_below": ATTENTION_BELOW, "by_level": by_level,
            "level_accuracy": {k: (round(v["correct"] / v["total"], 3) if v["total"] else None) for k, v in by_level.items()},
            "weak_topics": weak_topics(rows), "students": students}


def report_html(r: dict, lang: str) -> str:
    import html as h
    bn = lang == "bn"
    T = (lambda en, b: b if bn else en)
    pct = lambda v: "–" if v is None else f"{round(v * 100)}%"
    nm = lambda d: h.escape((d.get("name_bn") if bn else d.get("name_en")) or d.get("name_en") or "–") if d else "–"
    lv = "".join(f"<span class='lv'>{k} {pct(v)}</span>" for k, v in r["level_accuracy"].items())
    wt = "".join(f"<tr><td>{nm(t)}</td><td>{t['correct']}/{t['total']}</td><td>{pct(t['accuracy'])}</td></tr>" for t in r["weak_topics"])
    st = "".join(f"<tr class='{'att' if s['score'] < r['attention_below'] else ''}'><td>{h.escape(s['name'])}</td><td>{h.escape(str(s['roll'] or ''))}</td>"
                 f"<td>{pct(s['score'])}</td><td>{nm(s['weakest'])}</td></tr>" for s in r["students"])
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>Class report</title>
<link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@400;600&display=swap" rel="stylesheet">
<style>body{{font-family:'Hind Siliguri','Nirmala UI',sans-serif;max-width:760px;margin:24px auto;color:#1F1F1F;padding:0 16px}}
h1{{color:#7A0F1E;margin:0}}.sub{{color:#555}}.k{{display:flex;gap:12px;margin:16px 0}}.k div{{flex:1;border:1px solid #ddd;border-radius:10px;padding:10px}}
.k b{{display:block;font-size:22px;color:#7A0F1E}}table{{width:100%;border-collapse:collapse;margin:8px 0 16px}}td,th{{border-bottom:1px solid #eee;padding:6px;text-align:left}}
th{{background:#F3E9DF}}.att td{{color:#9B1C1C}}.lv{{display:inline-block;margin:0 8px 6px 0;padding:3px 9px;border-radius:8px;background:#F3E9DF}}
@media print{{button{{display:none}}}}</style></head><body>
<button onclick="print()" style="float:right">Print / PDF</button>
<h1>{T('Class report', 'ক্লাস রিপোর্ট')}</h1><div class="sub">{h.escape(r['title'])}</div>
<div class="k"><div>{T('Average', 'গড়')}<b>{pct(r['average'])}</b></div><div>{T('Students', 'শিক্ষার্থী')}<b>{r['students_count']}</b></div>
<div>{T('Need attention', 'মনোযোগ দরকার')}<b>{r['need_attention']}</b></div></div>
<h3>{T('Score by level', 'স্তর অনুযায়ী')}</h3>{lv}
<h3>{T('Weakest topics', 'দুর্বল টপিক')}</h3><table><tr><th>{T('Topic', 'টপিক')}</th><th></th><th>{T('Accuracy', 'সঠিকতা')}</th></tr>{wt or '<tr><td>–</td></tr>'}</table>
<h3>{T('Students', 'শিক্ষার্থী')}</h3><table><tr><th>{T('Name', 'নাম')}</th><th>{T('Roll', 'রোল')}</th><th>{T('Score', 'নম্বর')}</th><th>{T('Weakest', 'দুর্বল')}</th></tr>{st or '<tr><td>–</td></tr>'}</table>
</body></html>"""
