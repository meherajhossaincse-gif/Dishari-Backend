"""Import board questions from Excel/CSV (same format as GET /v1/board-questions/template).
    python scripts/import_board_bank.py board_2015_2025.xlsx"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.db.session import SessionLocal  # noqa: E402
from app.services.bulk import import_board_rows  # noqa: E402
from app.services.excel import read_rows  # noqa: E402

f = Path(sys.argv[1])
db = SessionLocal()
print(import_board_rows(db, read_rows(f.name, f.read_bytes(), 100000)))
