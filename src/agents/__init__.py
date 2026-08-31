"""The FixIt ticket-analysis agent -- one LangGraph pipeline, no versions.

`src.models.agent_schemas` is the authoritative Backend <-> Agent contract; this
package produces one `AgentAnalysisResult` per round and hands it to
`AgentResultService.finalize()`, never mutating Priority, score or audit rows
itself. `run_case_grouping` is the background follow-up that looks for a
spreading incident once the resident already has their answer.
"""

from src.agents.service import (
    resume_after_emergency_downgrade,
    resume_analysis,
    run_analysis,
    run_case_grouping,
)

__all__ = ["resume_after_emergency_downgrade", "resume_analysis", "run_analysis", "run_case_grouping"]
