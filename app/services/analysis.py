"""Paper balance analysis, practice scoring and (R2) mastery diagnosis."""
from collections import Counter, defaultdict

from app.models import LEVELS, DIFFICULTIES
from app.services.taxonomy import taxonomy

DIFF_WEIGHT = {"Easy": 1.0, "Medium": 1.5, "Hard": 2.0}


def paper_analysis(items: list[dict], target: dict | None) -> dict:
    """items: [{"cognitive_level","difficulty","chapter_id","marks"}]. Returns distribution vs target."""
    n = len(items)
    total_marks = sum(float(i.get("marks") or 1) for i in items) or 0
    lvl = Counter(i.get("cognitive_level") or "untagged" for i in items)
    dif = Counter(i.get("difficulty") or "untagged" for i in items)
    chap = Counter(i.get("chapter_id") or "untagged" for i in items)
    target = target or {}
    levels = []
    for k in LEVELS:
        pct = round(100 * lvl.get(k, 0) / n, 1) if n else 0.0
        t = target.get(k)
        levels.append({"level": k, "count": lvl.get(k, 0), "percent": pct, "target": t,
                       "gap": round(pct - t, 1) if t is not None else None})
    suggestions = []
    for row in levels:
        if row["gap"] is not None and n:
            need = round(abs(row["gap"]) * n / 100)
            if row["gap"] <= -10 and need:
                suggestions.append({"type": "add", "level": row["level"], "count": need,
                                    "message_en": f"Add about {need} {row['level']} question(s)",
                                    "message_bn": f"আরও প্রায় {need}টি {row['level']} প্রশ্ন যোগ করুন"})
            elif row["gap"] >= 10 and need:
                suggestions.append({"type": "remove", "level": row["level"], "count": need,
                                    "message_en": f"Remove about {need} {row['level']} question(s)",
                                    "message_bn": f"প্রায় {need}টি {row['level']} প্রশ্ন কমান"})
    taxonomy_names = taxonomy.chapters
    return {
        "question_count": n, "total_marks": total_marks,
        "levels": levels,
        "untagged": lvl.get("untagged", 0),
        "difficulty": [{"label": d, "count": dif.get(d, 0),
                        "percent": round(100 * dif.get(d, 0) / n, 1) if n else 0.0} for d in DIFFICULTIES],
        "chapters": [{"chapter_id": c, "name_en": taxonomy_names.get(c, {}).get("name_en"),
                      "name_bn": taxonomy_names.get(c, {}).get("name_bn"), "count": k}
                     for c, k in sorted(chap.items(), key=lambda x: (x[0] == "untagged", x[0].zfill(3)))],
        "balanced": all(r["gap"] is None or abs(r["gap"]) < 10 for r in levels),
        "suggestions": suggestions,
    }


def score_practice(rows: list[dict]) -> dict:
    """rows: [{"cognitive_level","chapter_id","is_correct"}]"""
    by_level = {k: {"correct": 0, "total": 0} for k in LEVELS}
    by_chapter: dict = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in rows:
        lv = r.get("cognitive_level")
        if lv in by_level:
            by_level[lv]["total"] += 1
            by_level[lv]["correct"] += int(bool(r["is_correct"]))
        ch = r.get("chapter_id") or "untagged"
        by_chapter[ch]["total"] += 1
        by_chapter[ch]["correct"] += int(bool(r["is_correct"]))
    weak = [k for k, v in by_level.items() if v["total"] and v["correct"] / v["total"] < 0.5]
    return {"by_level": by_level, "by_chapter": dict(by_chapter), "weak_levels": weak}


def mastery(answers: list[dict]) -> list[dict]:
    """R2 diagnosis. answers: [{"concept_id","difficulty","is_correct","cognitive_level"}].
    mastery = (sum w*correct + 1) / (sum w + 2), w by difficulty; <3 answers -> insufficient evidence."""
    groups: dict = defaultdict(list)
    for a in answers:
        if a.get("concept_id"):
            groups[a["concept_id"]].append(a)
    out = []
    for cid, rows in groups.items():
        w = [DIFF_WEIGHT.get(r.get("difficulty") or "Medium", 1.5) for r in rows]
        num = sum(wi * int(bool(r["is_correct"])) for wi, r in zip(w, rows)) + 1
        den = sum(w) + 2
        m = round(num / den, 3)
        if len(rows) < 3:
            label = "insufficient_evidence"
        elif m < 0.50:
            label = "weak"
        elif m <= 0.75:
            label = "developing"
        else:
            label = "strong"
        c = taxonomy.concepts.get(cid, {})
        out.append({"concept_id": cid, "chapter_id": c.get("chapter_id"), "name_en": c.get("name_en"),
                    "name_bn": c.get("name_bn"), "answered": len(rows),
                    "correct": sum(int(bool(r["is_correct"])) for r in rows), "mastery": m, "label": label})
    return sorted(out, key=lambda x: x["mastery"])
