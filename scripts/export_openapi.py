"""Writes docs/openapi.json (frontend generates TypeScript types from it:
   npx openapi-typescript docs/openapi.json -o src/api/schema.d.ts)"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_URL", "sqlite://")
from app.main import app  # noqa: E402

(ROOT / "docs" / "openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=1), encoding="utf-8")
print("wrote docs/openapi.json")
