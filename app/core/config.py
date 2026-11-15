"""Central settings. Every value can be overridden with an environment variable
(or a line in .env). Frontend, backend and ML all read their shared numbers
(thresholds, batch size, model location) from here, so nobody hard-codes them."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app ---
    APP_NAME: str = "Dishari API"
    APP_VERSION: str = "1.0.0"
    ENV: str = "development"  # development | test | production
    DEBUG: bool = True
    API_PREFIX: str = "/v1"

    # --- database ---
    DATABASE_URL: str = "postgresql+psycopg://dishari:dishari@localhost:5432/dishari"
    AUTO_CREATE_TABLES: bool = False  # True only for quick local runs / tests (Alembic is the normal path)

    # --- security ---
    SECRET_KEY: str = "change-me-in-production-please-32-chars-min"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_MINUTES: int = 30
    REFRESH_TOKEN_DAYS: int = 7
    RESET_TOKEN_MINUTES: int = 30
    LOGIN_RATE_LIMIT_PER_MINUTE: int = 10
    LOGIN_RATE_LIMIT: bool = True
    LOCK_AFTER_FAILED: int = 5  # 0 = never lock
    LOCK_MINUTES: int = 15

    # --- frontend ---
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"

    # --- ML integration ---
    TAGGER_IMPL: str = "mock"  # "mock" or "python.module:ClassName" (e.g. dishari_ml.tagger:Tagger)
    MODEL_PATH: str = str(BASE_DIR / "ml_models" / "current")
    MODEL_VERSION_OVERRIDE: str = ""
    REVIEW_THRESHOLD: float = 0.60  # any tag below this -> Needs review
    BOARD_MATCH_THRESHOLD: float = 0.85  # similarity >= this -> board match
    MAX_BATCH: int = 500  # max items per predict_batch / bulk file chunk
    MAX_UPLOAD_ROWS: int = 5000

    # practice uses verified + tagged questions; while only the mock model exists almost everything is
    # needs_review, so development also allows those. Set False in production.
    PRACTICE_ALLOW_NEEDS_REVIEW: bool = True

    # --- files ---
    STORAGE_DIR: str = str(BASE_DIR / "storage")
    MAX_UPLOAD_MB: int = 10

    # --- seed ---
    SEED_ON_STARTUP: bool = True
    SEED_DEMO_USERS: bool = True
    DEMO_PASSWORD: str = "Dishari@123"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
