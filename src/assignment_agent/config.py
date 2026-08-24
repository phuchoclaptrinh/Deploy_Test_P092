"""Agent-side configuration for assignment model calls (contract §5.2).

Declared here rather than in `src.config` on purpose: this scope covers the
Agent, and the surrounding operational config — durable job store, grace
windows, acceptance deadlines, proposal TTL, reassignment cap — belongs to the
Backend worker that does not exist yet. Only what the Agent itself needs to
issue a model call lives in this file.

    assignment_primary_model
    assignment_fallback_model
    assignment_model_timeout_seconds = 300

The fallback must be a genuinely different model. Pointing it at the primary
gives a failover that fails in exactly the same way, so `validate_failover()`
treats that as a misconfiguration rather than a preference.

A misconfiguration is not a model outage, so it raises its own exception type.
The orchestration catches model failures broadly - it has to, or one provider
incident stops the worker - and `AssignmentConfigurationError` is what lets it
tell the two apart, instead of writing MANUAL_REQUIRED rows that look like the
model considered the ticket and gave up on it.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# §5.2 / §11 item 1: two sequential requests, each with a hard 300-second
# deadline. No retry, no deadline extension.
ASSIGNMENT_MODEL_TIMEOUT_SECONDS = 300


class AssignmentConfigurationError(RuntimeError):
    """The configured model pair cannot fail over.

    Distinct from `AssignmentModelError`: nothing was asked of any model, and
    no amount of retrying or falling back will help. It must reach an operator
    rather than being absorbed into a per-ticket outcome.
    """


class AssignmentAgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    assignment_primary_model: str = ""
    assignment_fallback_model: str = ""
    assignment_model_timeout_seconds: int = Field(default=ASSIGNMENT_MODEL_TIMEOUT_SECONDS, ge=1, le=300)
    assignment_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)

    @property
    def failover_ready(self) -> bool:
        return bool(
            self.assignment_primary_model
            and self.assignment_fallback_model
            and self.assignment_primary_model != self.assignment_fallback_model
        )

    def validate_failover(self) -> None:
        """Raise when the configured pair cannot actually fail over.

        Model *names* only - never the API keys - so this is safe to surface in
        a startup log or a supervisor's crash report.
        """
        if not self.assignment_primary_model:
            raise AssignmentConfigurationError("ASSIGNMENT_PRIMARY_MODEL is required for AI assignment.")
        if not self.assignment_fallback_model:
            raise AssignmentConfigurationError(
                "ASSIGNMENT_FALLBACK_MODEL is required; there is no single-model mode."
            )
        if self.assignment_primary_model == self.assignment_fallback_model:
            raise AssignmentConfigurationError(
                "ASSIGNMENT_FALLBACK_MODEL must differ from ASSIGNMENT_PRIMARY_MODEL; "
                "an identical fallback is not a failover."
            )


@lru_cache
def get_assignment_settings() -> AssignmentAgentSettings:
    settings = AssignmentAgentSettings()
    if not settings.failover_ready:
        logger.warning(
            "Assignment models are not configured for failover "
            "(primary=%r, fallback=%r); a primary failure will go straight to manual.",
            settings.assignment_primary_model or None,
            settings.assignment_fallback_model or None,
        )
    return settings


def enforce_failover(
    *,
    strict: bool,
    settings: AssignmentAgentSettings | None = None,
) -> AssignmentAgentSettings:
    """The one place that decides what an unusable model pair costs.

    `strict` - production - refuses to start. Anywhere else the process starts
    and logs, because a developer running the API to look at tickets should not
    need two model names, and a scripted or injected agent never reaches this
    code at all.

    Called from three places that must agree: API startup, the standalone
    worker, and the lazy agent construction in the DIRECT and PROPOSAL
    services. Agreeing matters - a worker that starts on a configuration the
    API rejected would process jobs the API believes cannot be processed.
    """
    settings = settings or get_assignment_settings()
    if strict:
        settings.validate_failover()
    elif not settings.failover_ready:
        logger.warning(
            "Assignment failover is not configured (primary=%r, fallback=%r); "
            "a primary failure will go straight to the manual queue.",
            settings.assignment_primary_model or None,
            settings.assignment_fallback_model or None,
        )
    return settings
