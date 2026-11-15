"""MockTagger - a stand-in model with EXACTLY the same interface as the real one.

It lets Jibon and Arshi build and demo every screen before the real model is ready. It
matches the stem against taxonomy names (fuzzy) and guesses the cognitive level from
keywords. Its numbers are not meaningful; never report them as model results.
"""
import json
import re
from pathlib import Path

from rapidfuzz import fuzz

from app.ml.contract import LEVEL_NAMES, validate_input, ContractError
from app.ml.normalize import normalize

TAXONOMY_FILE = Path(__file__).resolve().parents[2] / "seeds" / "taxonomy.json"

_K4 = re.compile(r"(i\s*,?\s*ii|ii\s*ও\s*iii|i\s*ও\s*ii|কোনটি সঠিক|সঠিক উক্তি|which of the following (is|are) correct|statement)", re.I)
_K3 = re.compile(r"(\d|নির্ণয়|হিসাব|কত\b|কত হবে|মান কত|calculate|find the|how much|how many|what will be)", re.I)
_K2 = re.compile(r"(কেন|কারণ|ব্যাখ্যা|পার্থক্য|why|because|explain|differ|reason|means)", re.I)


class MockTagger:
    version = "mock-0.1"

    def __init__(self, taxonomy: dict):
        self.rows = []  # (chapter, concept, topic, norm_en, norm_bn)
        for ch in taxonomy["chapters"]:
            for co in ch["concepts"]:
                for tp in co["topics"]:
                    self.rows.append((ch, co, tp,
                                      normalize(f'{tp["name_en"]} {co["name_en"]} {ch["name_en"]}'),
                                      normalize(f'{tp["name_bn"]} {co["name_bn"]} {ch["name_bn"]}')))

    # ---- the 3 methods every tagger must have --------------------------------
    @classmethod
    def load(cls, path: str | None = None) -> "MockTagger":
        return cls(json.loads(TAXONOMY_FILE.read_text(encoding="utf-8")))

    def predict(self, item: dict) -> dict:
        try:
            x = validate_input(item)
        except ContractError as e:
            return {"id": str(item.get("id", "")), "model_version": self.version,
                    "error": {"code": e.code, "message": e.message}, "warnings": []}
        text = normalize(x.stem + " " + " ".join(x.options.values()))
        idx = 4 if x.language == "bn" else 3
        best, best_s = None, -1.0
        for row in self.rows:
            s = fuzz.token_set_ratio(text, row[idx]) * 0.6 + fuzz.partial_ratio(text, row[idx]) * 0.4
            if s > best_s:
                best, best_s = row, s
        ch, co, tp = best[0], best[1], best[2]
        conf = round(min(0.95, max(0.30, best_s / 100)), 3)
        nk = "name_bn" if x.language == "bn" else "name_en"

        raw = x.stem
        if _K4.search(raw):
            lvl, lc = "K4", 0.66
        elif _K3.search(raw):
            lvl, lc = "K3", 0.63
        elif _K2.search(raw):
            lvl, lc = "K2", 0.62
        else:
            lvl, lc = "K1", 0.70
        diff = {"K1": "Easy", "K2": "Medium", "K3": "Medium", "K4": "Hard"}[lvl]
        return {
            "id": x.id,
            "chapter": {"id": ch["id"], "name": ch[nk], "confidence": round(min(0.97, conf + 0.1), 3)},
            "concept": {"id": co["id"], "name": co[nk], "confidence": conf},
            "topic": {"id": tp["id"], "name": tp[nk], "confidence": round(max(0.2, conf - 0.08), 3)},
            "cognitive_level": {"label": lvl, "name": LEVEL_NAMES[lvl][x.language], "confidence": lc},
            "difficulty": {"label": diff, "confidence": 0.62, "source": "heuristic"},
            "model_version": self.version,
            "warnings": ["MOCK_MODEL: predictions are placeholders, not real model output"],
        }

    def predict_batch(self, items: list[dict]) -> list[dict]:
        return [self.predict(i) for i in items]
