"""Actor authorization exports for the Self Dev v3 three-role model."""

from src.api.dependencies.auth import require_coordinator, require_resident, require_technician

__all__ = ["require_coordinator", "require_resident", "require_technician"]
