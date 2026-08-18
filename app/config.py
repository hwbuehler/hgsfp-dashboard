"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = PROJECT_ROOT / "app"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HGSFP_",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    secret_key: str = "dev-only-insecure-key-change-me"
    base_url: str = "http://127.0.0.1:8000"
    cookie_secure: bool = False

    database_path: Path = Path("data/hgsfp.db")
    model_path: Path = Path("models/all-MiniLM-L6-v2")

    # Clustering parameters, mirrored from the original survey_analyzer defaults.
    cluster_distance_threshold: float = 0.5
    topic_split_similarity_threshold: float = 0.2

    @property
    def database_file(self) -> Path:
        path = self.database_path
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database_file}"

    @property
    def model_dir(self) -> Path:
        path = self.model_path
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def templates_dir(self) -> Path:
        return APP_DIR / "templates"

    @property
    def static_dir(self) -> Path:
        return APP_DIR / "static"

    @property
    def reference_dir(self) -> Path:
        return PROJECT_ROOT / "reference"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
