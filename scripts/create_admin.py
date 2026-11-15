"""Create (or reset) an admin account.
    python scripts/create_admin.py admin@school.edu "Full Name" "StrongPass123" """
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sqlalchemy import select  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

email, name, pw = sys.argv[1].lower(), sys.argv[2], sys.argv[3]
db = SessionLocal()
u = db.scalar(select(User).where(User.email == email))
if u:
    u.password_hash, u.roles, u.status = hash_password(pw), sorted(set(u.roles) | {"admin"}), "active"
    u.token_version += 1
else:
    db.add(User(name=name, email=email, password_hash=hash_password(pw), roles=["admin"]))
db.commit()
print("admin ready:", email)
