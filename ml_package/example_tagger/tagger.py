"""TEMPLATE for Razzaque: copy this folder to ml_package/dishari_ml/ and replace the body of
predict() with the real model. Keep the method names, argument types and the returned keys.

Test it without the server:
    python scripts/check_ml_contract.py dishari_ml.tagger:Tagger --path ml_models/current
Use it in the server (.env):
    TAGGER_IMPL=dishari_ml.tagger:Tagger
    MODEL_PATH=/app/ml_models/current
"""
from pathlib import Path

LEVEL_NAMES = {"K1": {"en": "Knowledge", "bn": "জ্ঞান"}, "K2": {"en": "Comprehension", "bn": "অনুধাবন"},
               "K3": {"en": "Application", "bn": "প্রয়োগ"}, "K4": {"en": "Higher ability", "bn": "উচ্চতর দক্ষতা"}}


class Tagger:
    version = "example-0.0"  # change on every new model, e.g. "xlmr-v1-2026-11-20"

    def __init__(self, path: Path):
        self.path = path
        # e.g. self.model = AutoModelForSequenceClassification.from_pretrained(path / "level")
        #      self.label_maps = json.loads((path / "labels.json").read_text())

    @classmethod
    def load(cls, path: str) -> "Tagger":
        return cls(Path(path))

    def predict(self, item: dict) -> dict:
        """item = {"id": str, "language": "bn"|"en", "stem": str, "options": {"A".."D": str}, "answer": "A".."D"|None}"""
        stem = (item.get("stem") or "").strip()
        if not stem:
            return {"id": item.get("id"), "model_version": self.version,
                    "error": {"code": "EMPTY_STEM", "message": "stem is empty"}}
        lang = item.get("language", "bn")
        # ---- replace below with real inference; IDs MUST come from seeds/taxonomy.json ----
        return {
            "id": item.get("id"),
            "chapter": {"id": "1", "name": None, "confidence": 0.50},
            "concept": {"id": "C01.1", "name": None, "confidence": 0.50},
            "topic": {"id": "1.1", "name": None, "confidence": 0.50},
            "cognitive_level": {"label": "K1", "name": LEVEL_NAMES["K1"][lang], "confidence": 0.50},
            "difficulty": {"label": "Easy", "confidence": 0.50, "source": "model"},
            "model_version": self.version,
            "warnings": [],
        }

    def predict_batch(self, items: list[dict]) -> list[dict]:
        """Must return the same number of items, in the same order. Batch the real model here for speed."""
        return [self.predict(i) for i in items]
