"""Loads whichever tagger TAGGER_IMPL points to, and guards every call with the contract.

TAGGER_IMPL=mock                          -> app.ml.mock_tagger.MockTagger
TAGGER_IMPL=dishari_ml.tagger:Tagger      -> Razzaque's real package (put it in ./ml_package)

The real class needs only:  Tagger.load(path) -> instance,  .predict(dict) -> dict,
.predict_batch(list[dict]) -> list[dict],  and a `.version` string attribute.
"""
import importlib
import logging
import threading
import time

from pydantic import ValidationError

from app.core.config import settings
from app.ml.contract import MLOutput

log = logging.getLogger("dishari.ml")


class TaggerService:
    def __init__(self):
        self._impl = None
        self._lock = threading.Lock()
        self.status = "not_loaded"  # not_loaded | ready | failed
        self.error: str | None = None
        self.impl_name = settings.TAGGER_IMPL
        self.loaded_at: float | None = None

    # -------------------------------------------------------------- loading
    def load(self, impl: str | None = None, path: str | None = None):
        impl = impl or settings.TAGGER_IMPL
        path = path or settings.MODEL_PATH
        with self._lock:
            try:
                if impl == "mock":
                    from app.ml.mock_tagger import MockTagger
                    cls = MockTagger
                else:
                    mod_name, _, cls_name = impl.partition(":")
                    cls = getattr(importlib.import_module(mod_name), cls_name or "Tagger")
                inst = cls.load(path)
                for m in ("predict", "predict_batch"):
                    if not callable(getattr(inst, m, None)):
                        raise TypeError(f"{impl} has no {m}() method")
                self._impl, self.status, self.error, self.impl_name = inst, "ready", None, impl
                self.loaded_at = time.time()
                log.info("Tagger loaded: %s (%s)", impl, self.version)
            except Exception as e:  # model missing must never crash the API
                self._impl, self.status, self.error, self.impl_name = None, "failed", f"{type(e).__name__}: {e}", impl
                log.warning("Tagger failed to load (%s): %s", impl, self.error)
        return self.status

    @property
    def ready(self) -> bool:
        return self._impl is not None

    @property
    def version(self) -> str:
        if settings.MODEL_VERSION_OVERRIDE:
            return settings.MODEL_VERSION_OVERRIDE
        return str(getattr(self._impl, "version", "unknown")) if self._impl else "none"

    def info(self) -> dict:
        return {"status": self.status, "impl": self.impl_name, "version": self.version, "error": self.error,
                "model_path": settings.MODEL_PATH, "review_threshold": settings.REVIEW_THRESHOLD,
                "board_match_threshold": settings.BOARD_MATCH_THRESHOLD, "max_batch": settings.MAX_BATCH}

    # -------------------------------------------------------------- prediction
    def _check(self, raw: dict, item_id: str) -> MLOutput:
        if not isinstance(raw, dict):
            return MLOutput(id=item_id, model_version=self.version,
                            error={"code": "MODEL_ERROR", "message": "model returned non-dict"})
        raw.setdefault("model_version", self.version)
        raw.setdefault("id", item_id)
        try:
            return MLOutput.model_validate(raw)
        except ValidationError as e:
            return MLOutput(id=item_id, model_version=self.version,
                            error={"code": "MODEL_ERROR", "message": f"output broke contract: {e.errors()[:2]}"})

    def predict_batch(self, items: list[dict]) -> tuple[list[MLOutput], int]:
        """Returns (outputs in the same order, latency_ms). Never raises."""
        if not self.ready:
            return [MLOutput(id=str(i.get("id", "")), model_version="none",
                             error={"code": "MODEL_NOT_READY", "message": self.error or "model not loaded"})
                    for i in items], 0
        outs: list[MLOutput] = []
        t0 = time.perf_counter()
        for start in range(0, len(items), settings.MAX_BATCH):
            chunk = items[start:start + settings.MAX_BATCH]
            try:
                raws = self._impl.predict_batch(chunk)
                if len(raws) != len(chunk):
                    raise ValueError("predict_batch returned a different number of items")
            except Exception as e:
                log.exception("predict_batch failed")
                raws = [{"id": str(i.get("id", "")), "error": {"code": "MODEL_ERROR", "message": str(e)[:300]}}
                        for i in chunk]
            outs.extend(self._check(r, str(i.get("id", ""))) for r, i in zip(raws, chunk))
        return outs, int((time.perf_counter() - t0) * 1000)


tagger = TaggerService()
