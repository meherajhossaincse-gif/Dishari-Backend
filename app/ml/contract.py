"""THE ML CONTRACT - the single agreement between backend and the ML model.

Razzaque's model package must accept `MLInput`-shaped dicts and return `MLOutput`-shaped
dicts. The backend validates both directions with these schemas, so if either side changes
shape, it fails loudly here instead of silently corrupting data. Keep docs/ML_CONTRACT.md in
sync with this file.
"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator

LEVEL_NAMES = {
    "K1": {"en": "Knowledge", "bn": "জ্ঞান"},
    "K2": {"en": "Comprehension", "bn": "অনুধাবন"},
    "K3": {"en": "Application", "bn": "প্রয়োগ"},
    "K4": {"en": "Higher ability", "bn": "উচ্চতর দক্ষতা"},
}
ERROR_CODES = ("EMPTY_STEM", "TOO_FEW_OPTIONS", "UNSUPPORTED_LANGUAGE", "BAD_ANSWER", "MODEL_NOT_READY", "MODEL_ERROR")


# ------------------------------------------------------------------ input
class MLInput(BaseModel):
    id: str = Field(..., description="Caller's id for the item; echoed back unchanged")
    language: str = Field(..., description="bn | en")
    stem: str
    options: dict[str, str] = Field(..., description='{"A": "...", "B": "...", "C": "...", "D": "..."}')
    answer: str | None = Field(None, description="A|B|C|D, optional")


class ContractError(Exception):
    def __init__(self, code: str, message: str):
        assert code in ERROR_CODES
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def validate_input(item: dict) -> MLInput:
    """Same validation the model is expected to do; backend runs it first so bad rows never reach ML."""
    lang = (item.get("language") or "").strip().lower()
    if lang not in ("bn", "en"):
        raise ContractError("UNSUPPORTED_LANGUAGE", f"language must be 'bn' or 'en', got {item.get('language')!r}")
    stem = (item.get("stem") or "").strip()
    if not stem:
        raise ContractError("EMPTY_STEM", "Question stem is empty")
    opts = {k.upper(): (str(v).strip() if v is not None else "") for k, v in (item.get("options") or {}).items()}
    opts = {k: opts.get(k, "") for k in "ABCD"}
    if sum(1 for v in opts.values() if v) < 4:
        raise ContractError("TOO_FEW_OPTIONS", "All four options A-D are required")
    ans = item.get("answer")
    ans = str(ans).strip().upper() if ans not in (None, "") else None
    if ans is not None and ans not in "ABCD":
        raise ContractError("BAD_ANSWER", "answer must be A, B, C or D")
    return MLInput(id=str(item.get("id", "")), language=lang, stem=stem, options=opts, answer=ans)


# ------------------------------------------------------------------ output
class LabelConf(BaseModel):
    id: str | None
    name: str | None = None
    confidence: float = Field(ge=0, le=1)


class LevelOut(BaseModel):
    label: Literal["K1", "K2", "K3", "K4"] | None
    name: str | None = None
    confidence: float = Field(ge=0, le=1)


class DifficultyOut(BaseModel):
    label: Literal["Easy", "Medium", "Hard"] | None
    confidence: float = Field(ge=0, le=1)
    source: Literal["model", "heuristic", "none"] = "model"


class BoardMatchOut(BaseModel):
    matched: bool = False
    board_question_id: int | None = None
    board: str | None = None
    year: int | None = None
    score: float | None = None


class MLError(BaseModel):
    code: str
    message: str


class MLOutput(BaseModel):
    id: str
    chapter: LabelConf | None = None
    concept: LabelConf | None = None
    topic: LabelConf | None = None
    cognitive_level: LevelOut | None = None
    difficulty: DifficultyOut | None = None
    board_match: BoardMatchOut | None = None  # filled by backend, model may leave it out
    model_version: str
    warnings: list[str] = []
    error: MLError | None = None

    @field_validator("warnings", mode="before")
    @classmethod
    def _w(cls, v):
        return v or []

    def min_confidence(self) -> float | None:
        vals = [x.confidence for x in (self.chapter, self.concept, self.topic, self.cognitive_level, self.difficulty)
                if x is not None and getattr(x, "id", getattr(x, "label", None)) is not None]
        return min(vals) if vals else None
