from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    if url.startswith("sqlite"):
        kw = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kw["poolclass"] = StaticPool
        return create_engine(url, **kw)
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)


engine = make_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
