# ML contract (backend ⇄ model)

Source of truth in code: `app/ml/contract.py`. Check any model offline with
`python scripts/check_ml_contract.py <module:Class> --path <weights folder>`.

## Python interface

```python
class Tagger:
    version: str                                  # e.g. "xlmr-v1-2026-11-20"
    @classmethod
    def load(cls, path: str) -> "Tagger": ...     # path = MODEL_PATH (ml_models/current)
    def predict(self, item: dict) -> dict: ...
    def predict_batch(self, items: list[dict]) -> list[dict]: ...   # same length, same order, ≤ 500 items
```

## Input (one item)

```json
{"id": "123", "language": "bn", "stem": "নিচের কোনটি ভেক্টর রাশি?",
 "options": {"A": "ভর", "B": "দ্রুতি", "C": "সরণ", "D": "কাজ"}, "answer": "C"}
```

`language` is `bn` or `en`; all four options are present; `answer` may be `null`.
The backend validates input before calling the model, so the model normally only sees valid items.

## Output (one item)

```json
{
  "id": "123",
  "chapter":  {"id": "2",     "name": "গতি",            "confidence": 0.91},
  "concept":  {"id": "C02.1", "name": "...",            "confidence": 0.78},
  "topic":    {"id": "2.1",   "name": "...",            "confidence": 0.66},
  "cognitive_level": {"label": "K1", "name": "জ্ঞান",   "confidence": 0.83},
  "difficulty": {"label": "Easy", "confidence": 0.70, "source": "model"},
  "model_version": "xlmr-v1-2026-11-20",
  "warnings": []
}
```

Rules:

- `chapter.id`, `concept.id`, `topic.id` **must exist in `seeds/taxonomy.json`** (13 chapters, 91 concepts, 193 topics),
  and the topic must belong to the concept, and the concept to the chapter. Otherwise the backend keeps the item in
  *Needs review* and records a warning.
- If you only predict the topic, you may send `concept`/`chapter` as `null`; the backend fills the parents.
- `confidence` is 0–1. If **any** confidence is below `REVIEW_THRESHOLD` (0.60), the question goes to the moderator queue.
- `difficulty.source`: `model` | `heuristic` | `none`.
- `board_match` is filled by the backend; the model does not need to send it.
- On a bad item return `{"id": ..., "model_version": ..., "error": {"code": "...", "message": "..."}}`.
  Codes: `EMPTY_STEM`, `TOO_FEW_OPTIONS`, `UNSUPPORTED_LANGUAGE`, `BAD_ANSWER`, `MODEL_NOT_READY`, `MODEL_ERROR`.
- Never raise from `predict_batch` for one bad item; return an error object for that item instead.
  (If the whole call raises, the backend marks those questions `pending` and retries later.)

## Lifecycle in the backend

```
save question → validate → predict_batch → store raw output (model_predictions)
   → board match → status: tagged | needs_review | pending (model down)
   → moderator review → verified | rejected
```

Moderator-verified tags are never overwritten by a later model; new predictions are stored for comparison only.

## Deploying a new model

1. Package in `ml_package/<name>/`, weights in `ml_models/current/`, extra pip packages in `requirements-ml.txt`.
2. `.env`: `TAGGER_IMPL=<name>.tagger:Tagger`, then `docker compose up --build`
   **or** hot-swap without restart: `POST /v1/admin/models` `{"version": "...", "impl": "<name>.tagger:Tagger", "path": "/app/ml_models/current", "metrics": {...}}`.
3. `POST /v1/admin/retag-pending` to tag questions saved while no model was loaded.
4. `GET /v1/ml/status` shows what is loaded. `POST /v1/ml/predict` runs the model on raw items without saving.

## Test-set rule

Never upload Gold **Test** items into the running system (bulk upload, demo seed, board bank). The seed only uses
taxonomy *names* from the gold files and 16 handmade demo questions.
