"""Weekly report for a student (week = Saturday to Friday, Bangladesh school week)."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LEVELS, PracticeAnswer, PracticeSession, Question, Test, TestAttempt, User
from app.services.analysis import mastery
from app.services.profile import _streak, student_activity_days
from app.services.taxonomy import taxonomy

BD = timezone(timedelta(hours=6))
LEVEL_NAME = {"K1": ("Knowledge", "জ্ঞান"), "K2": ("Comprehension", "অনুধাবন"), "K3": ("Application", "প্রয়োগ"),
              "K4": ("Higher ability", "উচ্চতর দক্ষতা")}


def week_bounds(offset: int = 0):
    today = datetime.now(BD).date()
    start = today - timedelta(days=(today.weekday() - 5) % 7) - timedelta(weeks=offset)  # Saturday
    s = datetime.combine(start, time.min, BD)
    return s, s + timedelta(days=7)


def _answers(db: Session, sid: int, s, e):
    """Every answered question (practice + tests) between s and e."""
    out = []
    for is_corr, lvl, ch, co, diff, when, tms in db.execute(
            select(PracticeAnswer.is_correct, Question.cognitive_level, Question.chapter_id, Question.concept_id,
                   Question.difficulty, PracticeSession.submitted_at, PracticeAnswer.time_ms)
            .join(PracticeSession, PracticeSession.id == PracticeAnswer.session_id)
            .join(Question, Question.id == PracticeAnswer.question_id)
            .where(PracticeSession.student_id == sid, PracticeSession.submitted_at >= s, PracticeSession.submitted_at < e)):
        out.append({"is_correct": bool(is_corr), "cognitive_level": lvl, "chapter_id": ch, "concept_id": co,
                    "difficulty": diff, "when": when, "time_ms": tms or 0, "src": "practice"})
    for a in db.scalars(select(TestAttempt).where(TestAttempt.student_id == sid, TestAttempt.submitted_at >= s,
                                                  TestAttempt.submitted_at < e)):
        qs = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(a.question_order or [0])))}
        for qid in a.question_order or []:
            q = qs.get(qid)
            if q:
                out.append({"is_correct": (a.answers or {}).get(str(qid)) == q.answer, "cognitive_level": q.cognitive_level,
                            "chapter_id": q.chapter_id, "concept_id": q.concept_id, "difficulty": q.difficulty,
                            "when": a.submitted_at, "time_ms": 0, "src": "test"})
    return out


def _acc(rows):
    return round(sum(r["is_correct"] for r in rows) / len(rows), 3) if rows else None


def weekly_report(db: Session, student: User, offset: int = 0) -> dict:
    taxonomy.ensure(db)
    s, e = week_bounds(offset)
    s, e = s.astimezone(timezone.utc), e.astimezone(timezone.utc)
    rows = _answers(db, student.id, s, e)
    prev = _answers(db, student.id, s - timedelta(days=7), s)
    by_level = {k: {"correct": 0, "total": 0} for k in LEVELS}
    by_ch: dict = defaultdict(lambda: {"correct": 0, "total": 0})
    days = [{"date": (s.astimezone(BD) + timedelta(days=i)).date().isoformat(), "answered": 0, "correct": 0} for i in range(7)]
    for r in rows:
        if r["cognitive_level"] in by_level:
            by_level[r["cognitive_level"]]["total"] += 1
            by_level[r["cognitive_level"]]["correct"] += int(r["is_correct"])
        c = r["chapter_id"] or "untagged"
        by_ch[c]["total"] += 1
        by_ch[c]["correct"] += int(r["is_correct"])
        w = r["when"] if r["when"].tzinfo else r["when"].replace(tzinfo=timezone.utc)
        i = (w.astimezone(BD).date() - s.astimezone(BD).date()).days
        if 0 <= i < 7:
            days[i]["answered"] += 1
            days[i]["correct"] += int(r["is_correct"])
    acc, pacc = _acc(rows), _acc(prev)
    lv_acc = {k: (v["correct"] / v["total"] if v["total"] else None) for k, v in by_level.items()}
    tried = [k for k, v in lv_acc.items() if v is not None]
    weakest = min(tried, key=lambda k: lv_acc[k]) if tried else None
    strongest = max(tried, key=lambda k: lv_acc[k]) if tried else None
    ch_list = sorted(({"chapter_id": c, "name_en": taxonomy.chapters.get(c, {}).get("name_en"),
                       "name_bn": taxonomy.chapters.get(c, {}).get("name_bn"), **v,
                       "accuracy": round(v["correct"] / v["total"], 3)} for c, v in by_ch.items()),
                     key=lambda x: x["accuracy"])
    # all-time weak concepts (mastery) for recommendations
    all_rows = _answers(db, student.id, datetime(2000, 1, 1, tzinfo=timezone.utc), e)
    weak_concepts = [m for m in mastery(all_rows) if m["label"] == "weak"][:5]
    tests = [{"title": a.test.title, "score": a.score, "total": a.total, "submitted_at": a.submitted_at, "kind": "online"}
             for a in db.scalars(select(TestAttempt).join(Test, Test.id == TestAttempt.test_id)
                                 .where(TestAttempt.student_id == student.id, TestAttempt.submitted_at >= s,
                                        TestAttempt.submitted_at < e))]
    from app.models import OmrResult, Paper
    tests += [{"title": f"{p.title} (OMR)", "score": r.score, "total": r.total, "submitted_at": r.created_at, "kind": "omr"}
              for r, p in db.execute(select(OmrResult, Paper).join(Paper, Paper.id == OmrResult.paper_id)
                                     .where(OmrResult.student_id == student.id, OmrResult.created_at >= s,
                                            OmrResult.created_at < e, OmrResult.score.is_not(None)))]
    rec_en, rec_bn = [], []
    active = sum(1 for d in days if d["answered"])
    if not rows:
        rec_en.append("No practice this week. Start with a short set of 10 questions.")
        rec_bn.append("এই সপ্তাহে কোনো অনুশীলন হয়নি। ১০টি প্রশ্নের একটি ছোট সেট দিয়ে শুরু করো।")
    if weakest and lv_acc[weakest] is not None and lv_acc[weakest] < 0.6:
        en, bn = LEVEL_NAME[weakest]
        rec_en.append(f"Focus on {weakest} ({en}) questions — accuracy {round(lv_acc[weakest] * 100)}%.")
        rec_bn.append(f"{weakest} ({bn}) প্রশ্নে বেশি অনুশীলন করো — সঠিকতা {round(lv_acc[weakest] * 100)}%।")
    if ch_list and ch_list[0]["accuracy"] < 0.6 and ch_list[0]["chapter_id"] != "untagged":
        rec_en.append(f"Revise chapter: {ch_list[0]['name_en']}.")
        rec_bn.append(f"অধ্যায়টি আবার পড়ো: {ch_list[0]['name_bn']}।")
    if rows and active < 4:
        rec_en.append("Practise on at least 4 days a week to build a habit.")
        rec_bn.append("অভ্যাস গড়তে সপ্তাহে অন্তত ৪ দিন অনুশীলন করো।")
    if acc is not None and pacc is not None and acc > pacc:
        rec_en.append("Great — you improved on last week. Keep it up!")
        rec_bn.append("চমৎকার — গত সপ্তাহের চেয়ে ভালো করেছ। এভাবেই চালিয়ে যাও!")
    return {
        "student": {"id": student.id, "name": student.name, "class_level": student.class_level},
        "week_start": s.astimezone(BD).date().isoformat(), "week_end": (e.astimezone(BD) - timedelta(days=1)).date().isoformat(), "offset": offset,
        "answered": len(rows), "correct": sum(r["is_correct"] for r in rows), "accuracy": acc,
        "previous_accuracy": pacc, "change": round(acc - pacc, 3) if acc is not None and pacc is not None else None,
        "practice_sets": len({(r["when"]) for r in rows if r["src"] == "practice"}), "tests": tests,
        "minutes": round(sum(r["time_ms"] for r in rows) / 60000),
        "active_days": active, "streak_days": _streak(student_activity_days(db, student.id)), "days": days,
        "by_level": by_level, "strongest_level": strongest, "weakest_level": weakest,
        "by_chapter": ch_list, "weak_concepts": weak_concepts,
        "recommendations": {"en": rec_en, "bn": rec_bn},
    }


def report_html(r: dict, lang: str) -> str:
    import html as h
    bn = lang == "bn"
    T = (lambda en, b: b if bn else en)
    pct = lambda v: "–" if v is None else f"{round(v * 100)}%"
    rows = "".join(f"<tr><td>{k} · {LEVEL_NAME[k][1 if bn else 0]}</td><td>{v['correct']}/{v['total']}</td>"
                   f"<td>{pct(v['correct'] / v['total'] if v['total'] else None)}</td></tr>" for k, v in r["by_level"].items())
    chs = "".join(f"<tr><td>{h.escape((c['name_bn'] if bn else c['name_en']) or c['chapter_id'])}</td><td>{c['correct']}/{c['total']}</td><td>{pct(c['accuracy'])}</td></tr>" for c in r["by_chapter"])
    recs = "".join(f"<li>{h.escape(x)}</li>" for x in r["recommendations"]["bn" if bn else "en"])
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>Weekly report</title>
<link href="https://fonts.googleapis.com/css2?family=Hind+Siliguri:wght@400;600&display=swap" rel="stylesheet">
<style>body{{font-family:'Hind Siliguri','Nirmala UI',sans-serif;max-width:720px;margin:24px auto;color:#1F1F1F;padding:0 16px}}
h1{{color:#7A0F1E;margin:0}}.sub{{color:#4A4A4A}}.k{{display:flex;gap:12px;margin:16px 0}}.k div{{flex:1;border:1px solid #ddd;border-radius:10px;padding:10px}}
.k b{{display:block;font-size:22px;color:#7A0F1E}}table{{width:100%;border-collapse:collapse;margin:8px 0 16px}}td,th{{border-bottom:1px solid #eee;padding:6px;text-align:left}}
@media print{{button{{display:none}}}}</style></head><body>
<button onclick="print()" style="float:right">Print / PDF</button>
<h1>{T('Weekly report', 'সাপ্তাহিক রিপোর্ট')}</h1><div class="sub">{h.escape(r['student']['name'])} · {r['week_start']} → {r['week_end']}</div>
<div class="k"><div>{T('Answered', 'উত্তর দিয়েছে')}<b>{r['answered']}</b></div><div>{T('Accuracy', 'সঠিকতা')}<b>{pct(r['accuracy'])}</b></div>
<div>{T('Active days', 'সক্রিয় দিন')}<b>{r['active_days']}/7</b></div><div>{T('Streak', 'ধারাবাহিকতা')}<b>{r['streak_days']}</b></div></div>
<h3>{T('By level', 'স্তর অনুযায়ী')}</h3><table>{rows}</table>
<h3>{T('By chapter', 'অধ্যায় অনুযায়ী')}</h3><table>{chs or '<tr><td>–</td></tr>'}</table>
<h3>{T('Suggestions', 'পরামর্শ')}</h3><ul>{recs}</ul></body></html>"""
