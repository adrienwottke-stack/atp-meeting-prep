"""Explicit configuration; secrets are never rendered or logged."""

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=APP_DIR / ".env", env_file_encoding="utf-8", extra="ignore")
    app_mode: str = "local"
    storage_backend: str = "auto"
    app_base_url: str = "http://127.0.0.1:8000"
    fixture_mode: bool = False
    database_url: str = Field(default="", repr=False)
    worker_database_url: str = Field(default="", repr=False)
    supabase_url: str = ""
    supabase_anon_key: str = Field(default="", repr=False)
    supabase_service_role_key: str = Field(default="", repr=False)
    supabase_secret_key: str = Field(default="", repr=False)
    supabase_jwt_secret: str = Field(default="", repr=False)
    supabase_jwt_issuer: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_algorithm: str = "ES256"
    auth_verify_session: bool = True
    storage_bucket: str = "briefings"
    openai_api_key: str = Field(default="", repr=False)
    model_extraction: str = "gpt-6-luna"
    model_research: str = "gpt-6.1-sol"
    model_review: str = "gpt-6.1-sol"
    model_synthesis: str = "gpt-6-luna"
    atp_profile_version: str = "ATP-2026-10-15.2"
    output_dir: Path = APP_DIR / "output"
    target_seconds: int = Field(default=180, ge=1, le=180)
    deadline_seconds: int = Field(default=480, ge=1, le=480)
    research_cutoff_seconds: int = Field(default=390, ge=1, le=390)
    max_model_requests: int = Field(default=16, ge=1, le=16)
    max_search_calls: int = Field(default=24, ge=1, le=24)
    max_evidence_fetches: int = Field(default=40, ge=1, le=40)
    org_concurrency: int = Field(default=1, ge=1, le=10)
    lease_seconds: int = Field(default=90, ge=15, le=600)
    retention_days: int | None = Field(default=None, ge=1)
    real_data_approved: bool = False
    integrations_enabled: bool = False
    memory_enabled: bool = False
    memory_user_ids: str = ""
    # A worker can only claim its own queue; acceptance never competes with live jobs.
    worker_queue: str = "live"

    def memory_available(self, actor):
        return self.memory_enabled and (
            not self.memory_user_ids or actor.user_id in self.memory_user_ids.split(",")
        )

    @field_validator("retention_days", mode="before")
    @classmethod
    def empty_retention(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def configuration(self):
        if self.integrations_enabled:
            raise ValueError(
                "Live-Integrationen sind in dieser Version nicht implementiert; INTEGRATIONS_ENABLED muss false bleiben."
            )
        if self.app_mode not in ("local", "hosted"):
            raise ValueError("APP_MODE muss local oder hosted sein.")
        if self.storage_backend not in ("auto", "local", "supabase"):
            raise ValueError("STORAGE_BACKEND muss auto, local oder supabase sein.")
        if self.app_mode == "hosted" and self.effective_storage_backend == "local":
            raise ValueError("Gehosteter Betrieb benötigt Supabase Storage.")
        if self.app_mode == "hosted" and self.fixture_mode:
            raise ValueError("FIXTURE_MODE ist im gehosteten Betrieb nicht zulässig.")
        if self.app_mode == "hosted" and not self.app_base_url.startswith("https://"):
            raise ValueError("Gehosteter Betrieb benötigt HTTPS.")
        if not self.supabase_jwt_issuer and self.supabase_url:
            self.supabase_jwt_issuer = self.supabase_url.rstrip("/") + "/auth/v1"
        return self

    @property
    def effective_storage_backend(self) -> str:
        if self.storage_backend == "auto":
            return "local" if self.app_mode == "local" else "supabase"
        return self.storage_backend

    @property
    def storage_api_key(self) -> str:
        return self.supabase_secret_key or self.supabase_service_role_key

    def setup_issues(self) -> list[str]:
        required = ["database_url", "worker_database_url", "supabase_url", "supabase_anon_key"]
        if not self.fixture_mode:
            required.append("openai_api_key")
        issues = [f"{name.upper()} fehlt." for name in required if not getattr(self, name)]
        if self.effective_storage_backend == "supabase" and not self.storage_api_key:
            issues.append("SUPABASE_SECRET_KEY oder SUPABASE_SERVICE_ROLE_KEY fehlt für Supabase Storage.")
        if not self.fixture_mode and (not self.real_data_approved or self.retention_days is None):
            issues.append(
                "Reale Daten: REAL_DATA_APPROVED und RETENTION_DAYS müssen nach Betriebsfreigabe gesetzt werden."
            )
        return issues
