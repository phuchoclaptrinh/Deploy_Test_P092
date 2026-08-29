"""One object exposing the whole Backend-owned Agent surface.

The service areas stay split across modules -- session/catalog, tool calls,
resident questions, result persistence -- but the graph and the routes reach
them through this single import. There is one `finalize()`, because there is
one contract.
"""

from src.services.agent_question_service import AgentQuestionService
from src.services.agent_result_service import AgentResultService
from src.services.agent_session_service import AgentSessionService
from src.services.agent_tool_service import AgentToolService


class AgentBackendService(
    AgentResultService,
    AgentQuestionService,
    AgentToolService,
    AgentSessionService,
):
    """Preserves the existing import path while keeping the service areas split."""
