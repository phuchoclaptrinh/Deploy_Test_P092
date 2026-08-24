"""Compatibility facade for the Backend-owned AI Agent services.

One object exposes the v3 tool/question/session surface and both finalize
paths, so the v3 graph, the v4 graph and the routes all keep a single
import. `finalize()` applies a v3 result; `finalize_v4()` applies a v4 one
and refuses a session that did not start on v4.
"""

from src.services.agent_question_service import AgentQuestionService
from src.services.agent_result_service import AgentResultService
from src.services.agent_result_v4_service import AgentResultV4Service
from src.services.agent_session_service import AgentSessionService
from src.services.agent_tool_service import AgentToolService


class AgentBackendService(
    AgentResultService,
    AgentResultV4Service,
    AgentQuestionService,
    AgentToolService,
    AgentSessionService,
):
    """Preserves the existing import path while keeping the service areas split."""
