"""Check an ML tagger against the backend contract WITHOUT running the server.

    python scripts/check_ml_contract.py mock
    python scripts/check_ml_contract.py dishari_ml.tagger:Tagger --path ml_models/current
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "ml_package")]

from app.ml.contract import MLOutput  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("impl")
p.add_argument("--path", default=str(ROOT / "ml_models" / "current"))
a = p.parse_args()

tax = json.loads((ROOT / "seeds" / "taxonomy.json").read_text(encoding="utf-8"))
ch_ids = {c["id"] for c in tax["chapters"]}
co_ids = {k["id"]: c["id"] for c in tax["chapters"] for k in c["concepts"]}
tp_ids = {t["id"]: k["id"] for c in tax["chapters"] for k in c["concepts"] for t in k["topics"]}

if a.impl == "mock":
    from app.ml.mock_tagger import MockTagger as cls
else:
    import importlib
    m, _, c = a.impl.partition(":")
    cls = getattr(importlib.import_module(m), c or "Tagger")

t0 = time.time()
tagger = cls.load(a.path)
print(f"loaded in {time.time() - t0:.2f}s, version={getattr(tagger, 'version', '??')}")
items = json.loads((ROOT / "seeds" / "demo_questions.json").read_text(encoding="utf-8"))
items = [{"id": q["external_id"], "language": q["language"], "stem": q["stem"], "options": q["options"],
          "answer": q.get("answer")} for q in items]
items.append({"id": "bad-1", "language": "bn", "stem": "", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}})
t0 = time.time()
outs = tagger.predict_batch(items)
dt = time.time() - t0
ok = True
if len(outs) != len(items):
    print("FAIL: predict_batch returned", len(outs), "items for", len(items)); sys.exit(1)
for it, o in zip(items, outs):
    try:
        out = MLOutput.model_validate(o)
    except Exception as e:
        ok = False
        print(f"FAIL {it['id']}: {e}")
        continue
    if out.id != it["id"]:
        ok = False; print(f"FAIL {it['id']}: id not echoed (got {out.id})")
    if out.error:
        print(f"  {it['id']}: error {out.error.code} (ok if input was bad)")
        continue
    probs = []
    if out.chapter and out.chapter.id not in ch_ids: probs.append(f"unknown chapter {out.chapter.id}")
    if out.concept and out.concept.id not in co_ids: probs.append(f"unknown concept {out.concept.id}")
    if out.topic and out.topic.id not in tp_ids: probs.append(f"unknown topic {out.topic.id}")
    if out.concept and out.chapter and co_ids.get(out.concept.id) != out.chapter.id: probs.append("concept not in chapter")
    if out.topic and out.concept and tp_ids.get(out.topic.id) != out.concept.id: probs.append("topic not in concept")
    if probs:
        ok = False; print(f"FAIL {it['id']}: {'; '.join(probs)}")
print(f"{len(items)} items in {dt*1000:.0f} ms ({dt*1000/len(items):.1f} ms/item)")
print("CONTRACT OK" if ok else "CONTRACT FAILED")
sys.exit(0 if ok else 1)
