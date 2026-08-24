"""Rule-based technician selection (RULE_ENGINE_V1).

The default engine behind the assignment side of the contract. It answers the
same question `src.assignment_agent` answered — which technician, out of the
snapshot Backend filtered, gets this work item — with a documented ordering
instead of a model call, because the model call cost up to 300 seconds per
request and the inputs are a handful of integers.

* `config` — the rule set, from `config/assignment_rules.yaml` plus
  `ASSIGNMENT_RULE_*` environment overrides.
* `engine` — the pure ranking: hard filter, work-item order, lexicographic key,
  projected load.
* `service` — `RuleBasedAssignmentService`, shaped exactly like
  `AssignmentAgentService` so the two sit behind one configuration switch.

Which engine actually runs is decided in
`src.services.assignment_decision_engine`, from `ASSIGNMENT_DECISION_ENGINE`.
"""

from src.assignment_rules.config import (
    RULE_ENGINE_V1,
    AssignmentRuleConfig,
    AssignmentRuleConfigError,
    get_rule_config,
    load_rule_config,
)
from src.assignment_rules.engine import ProjectedLoad, Selection, decide_items, rank_key, select
from src.assignment_rules.service import RuleBasedAssignmentService

__all__ = [
    "RULE_ENGINE_V1",
    "AssignmentRuleConfig",
    "AssignmentRuleConfigError",
    "ProjectedLoad",
    "RuleBasedAssignmentService",
    "Selection",
    "decide_items",
    "get_rule_config",
    "load_rule_config",
    "rank_key",
    "select",
]
