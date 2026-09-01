"""The Automatic Assignment switch (§2).

One boolean, and the asymmetry the previous version had is gone. Turning it on
used to require confirming a proposal batch first; §9 removes that rule, and §2
replaces it with a confirmation modal in the UI. So both directions go through
the same method here, and the guard that used to refuse `enabled=True` is
deleted rather than relaxed -- a guard that no longer expresses a rule is worse
than no guard, because the next reader assumes it does.

What the two directions still do differently is the **side effect**:

* **On** records who authorised autonomy, and enqueues the backlog -- every
  ticket that became eligible while the switch was off and is still waiting.
  Without that, turning the switch on would only affect tickets submitted
  afterwards, and the queue a manager just decided to automate would sit there.
* **Off** clears the provenance and stops future dispatch. §2 is explicit that
  it does **not** undo existing assignments, and nothing here touches one.
  Events already queued are not deleted either: the batch re-checks the switch
  and escalates them to Building Management, so they surface in the manager
  queue rather than vanishing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.orm import Session

from src.database.models.auto_assignment_setting import AutoAssignmentSetting
from src.models.api.errors import CONFLICT_VERSION, DomainError
from src.services.assignment_support import AssignmentSideEffects

#: `audit_logs.entity_id` is a non-null UUID and this is a singleton with an
#: integer key, so it gets one stable derived id rather than a magic literal.
SETTING_ENTITY_ID = uuid5(NAMESPACE_URL, "fixit:auto-assignment-setting")


class AutoAssignmentSettingsService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.side_effects = AssignmentSideEffects(db)

    def get(self) -> AutoAssignmentSetting:
        row = self.db.get(AutoAssignmentSetting, 1)
        if row is not None:
            return row
        row = AutoAssignmentSetting(id=1, enabled=False, version=1)
        self.db.add(row)
        self.db.commit()
        return row

    def set_enabled(
        self,
        actor_user_id: UUID,
        *,
        enabled: bool,
        expected_version: int | None = None,
    ) -> AutoAssignmentSetting:
        """Flip the switch, audit it, and enqueue the backlog when turning on.

        `expected_version` is optional optimistic concurrency. The UI sends the
        version it rendered the toggle from, so two managers on the screen at
        once cannot silently undo each other -- the second gets a conflict and
        sees the current state instead of believing they set it.
        """
        try:
            row = self.db.get(AutoAssignmentSetting, 1, with_for_update=True)
            if row is None:
                row = AutoAssignmentSetting(id=1, enabled=False, version=1)
                self.db.add(row)
                self.db.flush()
            if expected_version is not None and row.version != expected_version:
                raise DomainError(
                    CONFLICT_VERSION,
                    "Cấu hình phân việc tự động vừa được thay đổi. Hãy tải lại và thử lại.",
                    409,
                )

            was_enabled = bool(row.enabled)
            now = datetime.now(UTC)
            row.enabled = enabled
            row.updated_by_user_id = actor_user_id
            row.updated_at = now
            if enabled:
                # Only stamped on a real transition, so the record keeps saying
                # who first authorised the current ON state rather than who most
                # recently re-confirmed it.
                if not was_enabled:
                    row.enabled_by_user_id = actor_user_id
                    row.enabled_at = now
            else:
                # The provenance explained why autonomy was on. With it off,
                # leaving it would make the next reader think something still
                # authorises assignment.
                row.enabled_by_user_id = None
                row.enabled_at = None
            row.version += 1

            if was_enabled != enabled:
                self.side_effects.audit(
                    actor_user_id,
                    "ENABLE_AUTO_ASSIGNMENT" if enabled else "DISABLE_AUTO_ASSIGNMENT",
                    "AUTO_ASSIGNMENT_SETTING",
                    SETTING_ENTITY_ID,
                    {"enabled": was_enabled},
                    {"enabled": enabled},
                    None,
                    "COORDINATOR",
                )
            self.db.commit()

            if enabled and not was_enabled:
                self._enqueue_backlog()
            return row
        except Exception:
            self.db.rollback()
            raise

    def _enqueue_backlog(self) -> None:
        """Queue everything that was waiting while the switch was off.

        Imported here rather than at module scope so the settings endpoint does
        not pull the dispatch package into every request that reads the toggle.
        Failures are swallowed on purpose: the switch is already on and
        committed, the worker's own backlog sweep will catch anything missed,
        and raising now would report a failure for an action that succeeded.
        """
        from src.dispatch.enqueue import enqueue_backlog

        try:
            enqueue_backlog(self.db)
        except Exception:  # noqa: BLE001
            self.db.rollback()


__all__ = ["SETTING_ENTITY_ID", "AutoAssignmentSettingsService"]
