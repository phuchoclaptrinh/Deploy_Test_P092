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

    # Which analysis contract a *new* ticket starts on. Sessions already in
    # flight are unaffected: they resume on the contract recorded in
    # `ai_analysis_sessions.model_version` (see src/agents/analysis_dispatch.py).
    # Kept as a setting so v4 can be rolled out, and rolled back, without a
    # deploy of new code. Defaulted to v4 now that finalize_v4, the purpose-aware
    # search and the end-to-end suite are all in place; set it back to v3 to stop
    # starting new v4 sessions without touching the ones already running.
    analysis_contract_version: Literal["v3", "v4"] = "v4"

    # --- Assignment orchestration (contract §5.2) ---------------------------
    # Technical configuration, version-controlled centrally. These are not
    # coordinator-editable options: the acceptance windows and the reassignment
    # cap are policy, and a UI that let them drift per building would make the
    # SLA promises unverifiable.
    # Which engine picks the technician. `RULE` is the default: a documented
    # ordering (src/assignment_rules) that answers in microseconds instead of
    # the two 300-second model windows §5.2 budgets for. `AI` restores the
    # original contract §4-§5 model path without a code change, for the case
    # where the ranking rule turns out to allocate work in a way the building
    # manager disagrees with. Read in exactly one place:
    # src/services/assignment_decision_engine.py.
    assignment_decision_engine: Literal["RULE", "AI"] = "RULE"
    assignment_grace_seconds: int = Field(default=300, ge=0, le=3600)
    assignment_reassignment_cap: int = Field(default=3, ge=0, le=10)
    direct_request_max_ticket_count: int = Field(default=20, ge=1, le=20)
    proposal_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    incident_case_max_ticket_count: int = Field(default=5, ge=1, le=5)
    incident_case_sla_extension_per_extra_ticket: float = Field(default=0.25, ge=0.0, le=1.0)
    acceptance_warning_p1_seconds: int = Field(default=172800, ge=0)
    acceptance_reassign_p1_seconds: int = Field(default=176400, ge=0)
    acceptance_warning_p2_seconds: int = Field(default=7200, ge=0)
    acceptance_reassign_p2_seconds: int = Field(default=9000, ge=0)
    acceptance_reassign_p3_seconds: int = Field(default=300, ge=0)
    # How long a claimed job may sit unfinished before another worker may take
    # it back. Longer than the two 300-second model windows plus slack.
    assignment_job_claim_timeout_seconds: int = Field(default=900, ge=60)
    assignment_worker_poll_seconds: int = Field(default=15, ge=1, le=300)
    assignment_worker_batch_size: int = Field(default=20, ge=1, le=100)

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

    @property
    def require_assignment_failover(self) -> bool:
        """Whether an unusable assignment model pair is fatal rather than a warning.

        Only production, and only while the AI engine is the one selected: on
        `RULE` there is no model pair to fail over, so demanding two model
        names would refuse to boot over configuration nothing reads.

        A plain property, not a `computed_field`: it is an operational
        predicate, not part of any serialized settings payload.
        """
        return self.app_env == "production" and self.assignment_decision_engine == "AI"

    def validate_runtime_safety(self) -> None:
        """Validate startup settings without exposing secret values."""
        if self.app_env == "production" and "*" in self.parsed_cors_origins:
            raise RuntimeError("Production CORS origins must be explicit.")
        if self.app_env in {"development", "production"} and not self.database_url:
            raise RuntimeError("DATABASE_URL is required for development and production.")
        # Imported here rather than at module scope: `src.config` is imported by
        # almost everything, and it should not drag either engine in with it.
        if self.assignment_decision_engine == "AI":
            from src.assignment_agent.config import enforce_failover

            # §5.2: production refuses to start without a primary and a
            # genuinely different fallback. Checked at startup rather than at
            # the first assignment, because the first assignment happens on a
            # real ticket with a resident waiting on it. Raises
            # `AssignmentConfigurationError`, which carries model names only
            # and never a key.
            enforce_failover(strict=self.require_assignment_failover)
        else:
            from src.assignment_rules.config import get_rule_config

            # The equivalent check for the rule engine: a malformed rule file
            # or a typo'd cap must stop the boot, not quietly leave every cap
            # unset while the process reports itself healthy.
            get_rule_config()


@lru_cache
def get_settings() -> Settings:
    return Settings()
