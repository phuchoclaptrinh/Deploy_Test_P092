"""Configuration for the rule-based technician selection (RULE_ENGINE_V1).

Deliberately *not* in `src.config`: these are the knobs a coordinator-facing
policy discussion turns into numbers, and they are expected to move on their
own schedule. Keeping them in one small YAML file means changing a load cap is
a config change with a diff, not a code change with a deploy.

Resolution order, lowest first:

1. the dataclass defaults below - no caps at all, so switching the LLM off
   cannot change who gets work on the first day;
2. `config/assignment_rules.yaml`, or whatever `ASSIGNMENT_RULES_FILE` points
   at;
3. `ASSIGNMENT_RULE_<FIELD>` environment variables, for a hotfix inside a
   container image that ships the YAML read-only.

A malformed file is a configuration error and raises. Silently falling back to
"no limits" would be the one failure mode nobody notices: every technician
keeps getting work and the caps simply stop existing.

The next step for this module, once the caps have proven themselves in
production, is an `assignment_rule_settings` singleton table plus a coordinator
endpoint. `load_rule_config()` is the seam that swap goes behind: nothing
outside this file reads the YAML.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

RULE_ENGINE_V1 = "RULE_ENGINE_V1"

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "assignment_rules.yaml"

ENV_PREFIX = "ASSIGNMENT_RULE_"


class AssignmentRuleConfigError(RuntimeError):
    """The rule file or its environment overrides cannot be read.

    Its own type for the same reason `AssignmentConfigurationError` has one:
    it must reach an operator instead of being absorbed into a per-ticket
    outcome that looks like the engine considered the ticket and gave up.
    """


@dataclass(frozen=True)
class AssignmentRuleConfig:
    """One immutable rule set. `None` on a cap means no limit."""

    rule_version: str = RULE_ENGINE_V1
    max_active_assignments: int | None = None
    max_active_p1_assignments: int | None = None
    max_active_p2_assignments: int | None = None
    max_active_p3_assignments: int | None = None
    allow_p3_overload_when_all_capped: bool = True
    tie_break_on_last_assigned_at: bool = True

    def cap_for(self, priority_value: str) -> int | None:
        """The per-priority cap that applies when placing a `priority_value` item.

        Only one priority is ever checked - the one being placed. A technician
        holding four P1 tickets is not thereby barred from a P3 emergency;
        that is what `max_active_assignments` is for.
        """
        return {
            "P1": self.max_active_p1_assignments,
            "P2": self.max_active_p2_assignments,
            "P3": self.max_active_p3_assignments,
        }.get(priority_value)

    @property
    def has_any_cap(self) -> bool:
        return any(
            value is not None
            for value in (
                self.max_active_assignments,
                self.max_active_p1_assignments,
                self.max_active_p2_assignments,
                self.max_active_p3_assignments,
            )
        )


def _coerce(name: str, raw: object, annotation: str) -> object:
    if raw is None:
        if annotation.startswith("int |"):
            return None
        raise AssignmentRuleConfigError(f"{name} must not be null.")
    if annotation == "str":
        text = str(raw).strip()
        if not text or len(text) > 100:
            raise AssignmentRuleConfigError(f"{name} must be 1-100 characters.")
        return text
    if annotation == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
        raise AssignmentRuleConfigError(f"{name} must be a boolean; got {raw!r}.")
    # int | None
    text = str(raw).strip()
    if text.lower() in {"", "null", "none"}:
        return None
    try:
        value = int(text)
    except (TypeError, ValueError) as exc:
        raise AssignmentRuleConfigError(f"{name} must be an integer or null; got {raw!r}.") from exc
    if value < 1:
        # Zero would mean "nobody may ever be assigned", which is a way to stop
        # the system that should be spelled `enabled: false` on the switch, not
        # smuggled in as a cap.
        raise AssignmentRuleConfigError(f"{name} must be at least 1, or null for no limit; got {value}.")
    return value


def _read_yaml(path: Path) -> dict[str, object]:
    if not path.exists():
        logger.info("No assignment rule file at %s; using built-in defaults (no caps).", path)
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AssignmentRuleConfigError(f"Cannot read assignment rules from {path}: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise AssignmentRuleConfigError(f"{path} must contain a YAML mapping.")
    return loaded


def load_rule_config(path: str | Path | None = None) -> AssignmentRuleConfig:
    """Build the rule set from file plus environment. Never cached here.

    `get_rule_config()` is the cached production entry point; this one stays
    pure so a test can point it at a fixture file without clearing a cache.
    """
    source = Path(path) if path is not None else Path(os.environ.get("ASSIGNMENT_RULES_FILE") or DEFAULT_RULES_PATH)
    raw = _read_yaml(source)

    known = {field.name: field.type for field in fields(AssignmentRuleConfig)}
    unknown = sorted(set(raw) - set(known))
    if unknown:
        # A typo'd key is the quiet failure this whole module exists to avoid:
        # `max_active_assignment` would parse fine and cap nothing.
        raise AssignmentRuleConfigError(f"Unknown assignment rule key(s) in {source}: {', '.join(unknown)}.")

    values: dict[str, object] = {}
    for name, annotation in known.items():
        env_key = f"{ENV_PREFIX}{name.upper()}"
        if env_key in os.environ:
            values[name] = _coerce(name, os.environ[env_key], str(annotation))
        elif name in raw:
            values[name] = _coerce(name, raw[name], str(annotation))

    config = AssignmentRuleConfig(**values)  # type: ignore[arg-type]
    logger.info(
        "Assignment rules loaded from %s: version=%s total_cap=%s p3_cap=%s p3_overload=%s",
        source,
        config.rule_version,
        config.max_active_assignments,
        config.max_active_p3_assignments,
        config.allow_p3_overload_when_all_capped,
    )
    return config


@lru_cache
def get_rule_config() -> AssignmentRuleConfig:
    return load_rule_config()


__all__ = [
    "DEFAULT_RULES_PATH",
    "RULE_ENGINE_V1",
    "AssignmentRuleConfig",
    "AssignmentRuleConfigError",
    "get_rule_config",
    "load_rule_config",
]
