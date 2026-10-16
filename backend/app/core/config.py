"""Application configuration. All secrets come from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class Settings:
    app_name: str = "Forecast Platform"
    repo_root: Path = REPO_ROOT
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")

    # Application database (meta, layer values, audit).
    app_db_url: str = os.getenv("APP_DB_URL", "")
    # Analytic store (actuals, raw scores, snapshots, rule effects).
    store_backend: str = os.getenv("STORE_BACKEND", "local")
    store_path: str = os.getenv("STORE_PATH", "")

    sector: str = os.getenv("SECTOR", "demo")
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-not-a-production-secret")
    jwt_ttl_minutes: int = int(os.getenv("JWT_TTL_MINUTES", "720"))
    draft_ttl_days: int = int(os.getenv("DRAFT_TTL_DAYS", "7"))
    display_tz: str = "Europe/Istanbul"
    demo_password: str = os.getenv("DEMO_PASSWORD", "demo")

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.app_db_url:
            self.app_db_url = f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"
        if not self.store_path:
            self.store_path = (self.data_dir / "store.db").as_posix()

    @property
    def sector_config_path(self) -> Path:
        return self.repo_root / "configs" / "sectors" / f"{self.sector}.yaml"

    @property
    def model_packages_dir(self) -> Path:
        return self.repo_root / "models"

    @property
    def frontend_dir(self) -> Path:
        return self.repo_root / "frontend"


settings = Settings()
