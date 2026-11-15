"""Model vs moderator agreement - evidence for the capstone report on how good the deployed model is
in real use (complements offline test-set metrics). Uses the FIRST moderator review of each question
whose tags came from the model."""
from collections import defaultdict

from app.models import LEVELS

FIELDS = ("chapter_id", "concept_id", "topic_id", "cognitive_level", "difficulty")


def _rate(a, t):
    return round(a / t, 3) if t else None


def compute(reviews: list, model_version: str | None = None) -> dict:
    first = {}
    for r in sorted(reviews, key=lambda r: r.id):
        if r.question_id not in first and (r.before or {}).get("tag_source") == "model":
            first[r.question_id] = r
    rows = [r for r in first.values() if not model_version or r.before.get("model_version") == model_version]

    def block(rs):
        acc = sum(r.action == "accept" for r in rs)
        chg = sum(r.action == "change" for r in rs)
        rej = sum(r.action == "reject" for r in rs)
        judged = [r for r in rs if r.action != "reject"]
        fields = {}
        for f in FIELDS:
            tot = sum(1 for r in judged if r.before.get(f) is not None)
            ag = sum(1 for r in judged if r.before.get(f) is not None and r.before.get(f) == r.after.get(f))
            fields[f] = {"agree": ag, "total": tot, "rate": _rate(ag, tot)}
        return {"reviewed": len(rs), "accepted": acc, "changed": chg, "rejected": rej,
                "acceptance_rate": _rate(acc, acc + chg), "fields": fields}

    out = block(rows)
    judged = [r for r in rows if r.action != "reject"]
    # confusion matrix: rows = model level, cols = moderator's final level
    cm = {m: {f: 0 for f in LEVELS} for m in LEVELS}
    for r in judged:
        m, f = r.before.get("cognitive_level"), r.after.get("cognitive_level")
        if m in cm and f in cm[m]:
            cm[m][f] += 1
    per_level = {}
    for k in LEVELS:
        pred = sum(cm[k].values())
        true = sum(cm[m][k] for m in LEVELS)
        tp = cm[k][k]
        p, rc = _rate(tp, pred), _rate(tp, true)
        f1 = round(2 * p * rc / (p + rc), 3) if p and rc else None
        per_level[k] = {"model_predicted": pred, "final": true, "precision": p, "recall": rc, "f1": f1}
    f1s = [v["f1"] for v in per_level.values() if v["f1"] is not None]
    out["level_confusion"] = cm
    out["level_metrics"] = per_level
    out["level_macro_f1"] = round(sum(f1s) / len(LEVELS), 3) if f1s else None
    # does confidence mean anything? agreement for low vs high confidence questions
    lo = [r for r in judged if (r.before.get("min_confidence") or 0) < 0.60]
    hi = [r for r in judged if (r.before.get("min_confidence") or 0) >= 0.60]
    out["by_confidence"] = {"below_0.60": block(lo)["acceptance_rate"], "at_or_above_0.60": block(hi)["acceptance_rate"],
                            "n_below": len(lo), "n_above": len(hi)}
    versions = defaultdict(list)
    for r in first.values():
        versions[r.before.get("model_version") or "unknown"].append(r)
    out["by_model_version"] = {v: {k: b[k] for k in ("reviewed", "accepted", "changed", "rejected", "acceptance_rate")}
                               for v, rs in versions.items() for b in [block(rs)]}
    out["model_version_filter"] = model_version
    out["note"] = ("Moderators mostly review low-confidence questions, so these numbers are biased low versus a "
                   "random sample. Report them next to the offline test-set metrics, not instead of them.")
    return out


def to_xlsx(rep: dict) -> bytes:
    import io
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    for k in ("reviewed", "accepted", "changed", "rejected", "acceptance_rate", "level_macro_f1"):
        ws.append([k, rep.get(k)])
    ws.append([])
    ws.append(["Field", "Agree", "Total", "Rate"])
    for f, v in rep["fields"].items():
        ws.append([f, v["agree"], v["total"], v["rate"]])
    cm = wb.create_sheet("Level confusion")
    cm.append(["model \\ moderator"] + LEVELS)
    for m in LEVELS:
        cm.append([m] + [rep["level_confusion"][m][f] for f in LEVELS])
    lm = wb.create_sheet("Level metrics")
    lm.append(["Level", "Model predicted", "Final", "Precision", "Recall", "F1"])
    for k, v in rep["level_metrics"].items():
        lm.append([k, v["model_predicted"], v["final"], v["precision"], v["recall"], v["f1"]])
    for sh in wb.worksheets:
        for c in sh[1]:
            c.font = Font(bold=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
