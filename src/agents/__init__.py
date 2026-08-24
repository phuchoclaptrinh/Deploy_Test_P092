"""FixIt Agent v3 runtime — LangGraph pipeline implementing Logic_xử_lý_chính_v3.

`src.models.agent_schemas` remains the authoritative Backend <-> Agent
contract; this package produces `AgentAnalysisResultV3` and hands it to
`AgentResultService.finalize()`, never mutating Priority/score/audit itself.
"""

from src.agents.service import resume_ticket_analysis, run_ticket_analysis

__all__ = ["run_ticket_analysis", "resume_ticket_analysis"]
