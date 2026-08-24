"""FixIt Analysis Agent v4 runtime.

Implements the analysis half of `agent_backend_contract_v4.md`: six exit
reasons, duplicate detection, Category kept independent per source, and Density
left to Backend. It runs beside the v3 package in `src.agents` rather than
replacing it, so sessions already in flight finish on the contract they
started under.

The v4 graph produces `AgentAnalysisResultV4` and returns it. It never writes
the ticket: validating and persisting the result is Backend `finalize_v4()`,
which is outside this package.
"""

from src.agents.v4.graph import build_analysis_graph_v4
from src.agents.v4.service import (
    MODEL_VERSION_V4,
    AnalysisOutcomeV4,
    resume_analysis_v4,
    start_analysis_v4,
)

__all__ = [
    "MODEL_VERSION_V4",
    "AnalysisOutcomeV4",
    "build_analysis_graph_v4",
    "start_analysis_v4",
    "resume_analysis_v4",
]
