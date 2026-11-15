import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.core import errors
from app.core.config import settings
from app.db.session import Base, SessionLocal, engine
from app.ml.tagger import tagger
from app.api.v1 import admin, auth, board, bulk, omr, papers, practice, questions, reviews, school, taxonomy, tests, users

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dishari")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app import models as _models  # noqa: F401  (register tables)
    if settings.AUTO_CREATE_TABLES:
        Base.metadata.create_all(engine)
    tagger.load()
    try:
        from app.services import system_settings
        _db = SessionLocal()
        system_settings.load(_db)
        _db.close()
    except Exception:
        log.warning("Could not load admin settings (run 'alembic upgrade head')")
    if settings.SEED_ON_STARTUP:
        from app.services import seed
        db = SessionLocal()
        try:
            seed.run(db)
        except Exception:
            log.exception("Seeding failed (did you run 'alembic upgrade head'?)")
        finally:
            db.close()
    yield


app = FastAPI(
    title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan,
    description="Dishari - bilingual SSC Physics MCQ concept & cognitive-level tagging API. "
                "All errors use {\"error\": {\"code\", \"message\", \"fields\"}}. Auth: Bearer JWT from /v1/auth/login.",
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Content-Disposition"])
errors.install(app)

_media = Path(settings.STORAGE_DIR) / "media"
_media.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=_media), name="media")

for r in (auth, users, taxonomy, questions, bulk, board, reviews, papers, omr, practice, tests, school, admin):
    app.include_router(r.router, prefix=settings.API_PREFIX)


@app.get("/health", tags=["health"])
def health():
    db_ok = True
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "version": settings.APP_VERSION, "database": db_ok,
            "model": {"status": tagger.status, "version": tagger.version}}


@app.get("/", include_in_schema=False)
def root():
    return {"name": settings.APP_NAME, "docs": "/docs", "openapi": "/openapi.json", "health": "/health"}
