"""Finds whether a question already appeared in a board exam (fuzzy text match)."""
import threading

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import BoardQuestion
from app.ml.normalize import normalize


class BoardIndex:
    def __init__(self):
        self._lock = threading.Lock()
        self._by_lang: dict[str, tuple[list[str], list[int]]] = {}
        self._meta: dict[int, tuple[str, int]] = {}
        self.dirty = True

    def rebuild(self, db: Session):
        with self._lock:
            by: dict[str, tuple[list, list]] = {"bn": ([], []), "en": ([], [])}
            meta = {}
            for bq in db.execute(select(BoardQuestion.id, BoardQuestion.language, BoardQuestion.stem_norm,
                                        BoardQuestion.board, BoardQuestion.year)):
                by.setdefault(bq.language, ([], []))
                by[bq.language][0].append(bq.stem_norm)
                by[bq.language][1].append(bq.id)
                meta[bq.id] = (bq.board, bq.year)
            self._by_lang, self._meta, self.dirty = by, meta, False

    def match(self, db: Session, language: str, stem: str) -> dict:
        if self.dirty:
            self.rebuild(db)
        stems, ids = self._by_lang.get(language, ([], []))
        if not stems:
            return {"matched": False}
        hit = process.extractOne(normalize(stem), stems, scorer=fuzz.token_sort_ratio)
        if not hit:
            return {"matched": False}
        _, score, idx = hit
        score = round(score / 100, 3)
        bid = ids[idx]
        board, year = self._meta[bid]
        return {"matched": score >= settings.BOARD_MATCH_THRESHOLD, "board_question_id": bid,
                "board": board, "year": year, "score": score}


board_index = BoardIndex()
