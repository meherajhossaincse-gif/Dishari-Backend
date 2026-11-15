"""(Re)load taxonomy names from a JSON file in seeds/taxonomy.json format. Inserts missing rows only.
    python scripts/import_taxonomy.py seeds/taxonomy.json"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db.session import SessionLocal  # noqa: E402
from app.services.seed import seed_taxonomy  # noqa: E402

db = SessionLocal()
print(seed_taxonomy(db, Path(sys.argv[1]) if len(sys.argv) > 1 else None))
