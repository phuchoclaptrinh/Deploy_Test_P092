from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "FixIt Agent API"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str | list[str] = "http://localhost:3000"

    # LLM
    openai_api_key: str = ""
    model_name: str = ""
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    # Classification is latency-sensitive.  OpenAI models that support it are
    # explicitly run without reasoning tokens; non-reasoning models (such as
    # gpt-4o-mini) simply omit this provider parameter.
    llm_reasoning_effort: Literal["none"] = "none"
    # Anthropic is the fallback provider for src/agents. Both must be set to
    # enable it; leaving either blank keeps the agent on OpenAI alone.
    anthropic_api_key: str = ""
    anthropic_fallback_model: str = ""

    # Database
    database_url: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_verification_mode: Literal["jwks", "auth_server", "auto"] = "auto"
    supabase_storage_bucket: str = "ticket-attachments"
    supabase_signed_download_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    max_ticket_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1)
    allowed_ticket_image_mime_types: str | list[str] = "image/jpeg,image/png,image/webp"
    allow_live_migration: bool = False
    run_supabase_integration_tests: bool = False


    # --- Assignment and dispatch ------------------------------------------
    # Technical configuration, version-controlled centrally. Deliberately not
    # coordinator-editable: the working shift and the safety buffer are policy,
    # and a UI that let them drift per building would make the schedule
    # unverifiable.
    #
    # The acceptance windows are gone with the acceptance step. No start
    # deadline has replaced them: `start_due_at` and its grace period are an
    # open business decision, and a default invented here would quietly become
    # the policy. See `src.services.operational_timeout_service`.
    assignment_reassignment_cap: int = Field(default=3, ge=0, le=10)
    incident_case_max_ticket_count: int = Field(default=5, ge=1, le=5)
    incident_case_sla_extension_per_extra_ticket: float = Field(default=0.25, ge=0.0, le=1.0)

    # §6: the scheduler's safety buffer, in seconds. See
    # `src.dispatch.scheduler.DEFAULT_SAFETY_BUFFER` for why thirty minutes.
    dispatch_safety_buffer_seconds: int = Field(default=1800, ge=0, le=14400)

    # §8: micro-batch shape. The 20-ticket ceiling is the contract's, not a
    # tuning knob -- `le=20` is what stops a deployment widening it by config.
    dispatch_micro_batch_size: int = Field(default=20, ge=1, le=20)
    dispatch_micro_batch_interval_ms: int = Field(default=750, ge=500, le=1000)
    #: How long a claimed dispatch event may sit before another worker may take
    #: it back. Comfortably longer than one batch including a full agent
    #: timeout, so a slow pass is never mistaken for a dead one.
    dispatch_claim_timeout_seconds: int = Field(default=120, ge=10, le=3600)
    dispatch_max_attempts: int = Field(default=3, ge=1, le=10)

    # §7-§8: the at-risk agent. One call per micro-batch, bounded, and with a
    # hard ceiling on how many run at once so a burst cannot open more
    # concurrent work than the database session budget below allows.
    at_risk_agent_enabled: bool = True
    at_risk_agent_model: str = ""
    at_risk_agent_timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    at_risk_agent_max_concurrency: int = Field(default=2, ge=1, le=10)
    at_risk_agent_history_windows_days: str | list[int] = "30,60,90"

    # §8: the Supabase session ceiling this deployment must stay under, and how
    # the budget is split. These are not decoration -- `validate_runtime_safety`
    # refuses to boot on a pool configuration whose worst case exceeds the
    # ceiling, because discovering that at peak load is discovering it as an
    # outage.
    supabase_max_sessions: int = Field(default=15, ge=1, le=200)
    api_db_pool_size: int = Field(default=5, ge=1, le=100)
    api_db_max_overflow: int = Field(default=2, ge=0, le=100)
    dispatch_worker_db_pool_size: int = Field(default=2, ge=1, le=50)
    dispatch_worker_db_max_overflow: int = Field(default=1, ge=0, le=50)
    dispatch_worker_poll_seconds: float = Field(default=1.0, ge=0.1, le=60.0)

    # Agent tracing (see src/agents/trace.py). One JSONL file per analysis
    # session; content is sanitized the same way as the sanitized_* audit
    # columns, so signed storage URLs never reach disk.
    agent_trace_enabled: bool = True
    agent_trace_dir: str = ".ai-log/agent"
    agent_trace_text_limit: int = Field(default=500, ge=0)

    @field_validator("cors_origins", "allowed_ticket_image_mime_types", mode="before")
    @classmethod
    def _split_csv(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return [item.strip() for item in value if item.strip()]
        return [item.strip() for item in value.split(",") if item.strip()]

    @field_validator("at_risk_agent_history_windows_days", mode="before")
    @classmethod
    def _split_int_csv(cls, value: str | list[int]) -> list[int]:
        """§7 asks for 30/60/90-day windows; the value is configurable so an
        operator can widen them without a code change, and normalized here so
        every reader sees a list of ints rather than whichever of the two shapes
        the environment happened to supply."""
        if isinstance(value, list):
            return [int(item) for item in value]
        return [int(item.strip()) for item in str(value).split(",") if item.strip()]

    @computed_field
    @property
    def parsed_cors_origins(self) -> list[str]:
        return list(self.cors_origins)

    @computed_field
    @property
    def parsed_allowed_ticket_image_mime_types(self) -> set[str]:
        return set(self.allowed_ticket_image_mime_types)

    def require_database_url(self) -> str:
        """Return DATABASE_URL or raise a safe configuration error."""
        if self.database_url:
            return self.database_url
        if self.app_env == "test":
            return "sqlite:///:memory:"
        raise RuntimeError("DATABASE_URL is required for FixIt Agent API.")

    @computed_field
    @property
    def parsed_at_risk_history_windows(self) -> list[int]:
        return sorted(set(self.at_risk_agent_history_windows_days))

    @property
    def peak_db_session_budget(self) -> int:
        """Worst-case simultaneous database sessions across API and worker.

        §8's connection-exhaustion requirement expressed as one number. Both
        processes size their own pool, so neither can see the total; this is the
        only place the sum exists, and `validate_runtime_safety` is the only
        caller that acts on it.
        """
        return (
            self.api_db_pool_size
            + self.api_db_max_overflow
            + self.dispatch_worker_db_pool_size
            + self.dispatch_worker_db_max_overflow
        )

    def validate_runtime_safety(self) -> None:
        """Validate startup settings without exposing secret values."""
        if self.app_env == "production" and "*" in self.parsed_cors_origins:
            raise RuntimeError("Production CORS origins must be explicit.")
        if self.app_env in {"development", "production"} and not self.database_url:
            raise RuntimeError("DATABASE_URL is required for development and production.")
        # §8: refuse to boot on a pool configuration that could exhaust the
        # Supabase session limit. Checked at startup rather than on the first
        # timeout, because the first timeout happens at peak load with
        # residents waiting -- which is the one moment nobody is reading logs.
        if self.peak_db_session_budget > self.supabase_max_sessions:
            raise RuntimeError(
                "Database pool configuration allows up to "
                f"{self.peak_db_session_budget} concurrent sessions, above the "
                f"configured Supabase limit of {self.supabase_max_sessions}. "
                "Lower API_DB_POOL_SIZE / DISPATCH_WORKER_DB_POOL_SIZE or raise "
                "SUPABASE_MAX_SESSIONS to match the real plan."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
