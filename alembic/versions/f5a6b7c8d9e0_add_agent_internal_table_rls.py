"""add agent internal table rls

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-08-11 14:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: str | Sequence[str] | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.execute("ALTER TABLE ai_analysis_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_analysis_sessions FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE ai_analysis_sessions FROM PUBLIC")
    op.execute("ALTER TABLE ai_agent_tool_calls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_agent_tool_calls FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE ai_agent_tool_calls FROM PUBLIC")
    op.execute("ALTER TABLE ai_agent_questions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ai_agent_questions FORCE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE ai_agent_questions FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError("f5a6b7c8d9e0 is a forward-only Agent internal table RLS migration.")
