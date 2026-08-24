"""Human-facing identifiers derived from internal ones.

A ticket's UUID is what the database joins on; `PA-5DBB6A` is what a resident
reads out over the phone and what a coordinator types into a search box. The
derivation lives here rather than in a route module because the confirmation
snapshot has to freeze the same code the API renders — two implementations that
drifted would make a history record disagree with the ticket it names.
"""

from __future__ import annotations

from uuid import UUID


def ticket_display_code(ticket_id: UUID | str | None) -> str | None:
    """The short code shown to people. Derived, never stored, never reused."""
    if ticket_id is None:
        return None
    return f"PA-{str(ticket_id).replace('-', '')[:6].upper()}"


__all__ = ["ticket_display_code"]
