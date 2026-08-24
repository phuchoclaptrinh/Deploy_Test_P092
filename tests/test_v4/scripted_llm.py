"""A scripted `AnalysisLLMClientV4` for end-to-end tests.

Real model calls would make these tests slow, flaky and network-dependent, and
none of what is being verified is about model quality: the assertions are all
about what Backend does with a decision. So the model is a script, and every
answer it gives is one a real model could legitimately produce.

The graph, the tool port, `finalize_v4()` and the persistence layer are all
real — only the four LLM calls are replaced.
"""

from __future__ import annotations

from src.agents.v4.llm_client import (
    ActionDecisionV4,
    DuplicateJudgementV4,
    ImageExtractionV4,
    TextExtractionV4,
)


class ScriptedAnalysisLLMV4:
    """Answers each of the four calls from a fixed script.

    `actions` is consumed in order; the last entry repeats, so a test only has
    to script the decisions it cares about.
    """

    def __init__(
        self,
        *,
        text_categories: list[str] | None = None,
        red_flag_text: bool = False,
        text_understandable: bool = True,
        severity: str | None = "MEDIUM",
        severity_unknown_reason: str | None = None,
        symptom_facts: list[str] | None = None,
        is_confident: bool = True,
        image_categories: list[str] | None = None,
        red_flag_signal: bool = False,
        is_relevant: bool = True,
        duplicate_verdicts: list[str] | None = None,
        duplicate_reason: str = "Cùng tài sản và cùng hiện tượng.",
        actions: list[ActionDecisionV4] | None = None,
        notes: str = "Xác định theo mô tả và bằng chứng đã thu thập.",
    ) -> None:
        self.text_categories = text_categories or []
        self.red_flag_text = red_flag_text
        self.text_understandable = text_understandable
        self.severity = severity
        self.severity_unknown_reason = severity_unknown_reason
        self.symptom_facts = symptom_facts or ["hiện tượng được mô tả rõ"]
        self.is_confident = is_confident
        self.image_categories = image_categories or []
        self.red_flag_signal = red_flag_signal
        self.is_relevant = is_relevant
        self.duplicate_verdicts = duplicate_verdicts or ["DIFFERENT_INCIDENT"]
        self.duplicate_reason = duplicate_reason
        self.actions = actions or [ActionDecisionV4(action="CONCLUDE", reason="Đã đủ dữ liệu.")]
        self.notes = notes

        self.text_calls = 0
        self.image_calls = 0
        self.judge_calls = 0
        self.action_calls = 0
        self.last_candidates: list[dict[str, object]] = []

    # -- extraction ---------------------------------------------------

    def extract_text(self, *, description, catalog_names, context_notes):
        self.text_calls += 1
        return TextExtractionV4(
            text_categories=[name for name in self.text_categories if name in catalog_names],
            red_flag_text=self.red_flag_text,
            text_understandable=self.text_understandable,
            symptom_facts=self.symptom_facts,
            severity=self.severity,
            severity_unknown_reason=self.severity_unknown_reason,
            is_confident=self.is_confident,
            notes=self.notes,
        )

    def extract_image(self, *, image_urls, catalog_names):
        self.image_calls += 1
        return ImageExtractionV4(
            image_categories=[name for name in self.image_categories if name in catalog_names],
            red_flag_signal=self.red_flag_signal,
            is_relevant=self.is_relevant,
            symptom_facts=self.symptom_facts,
            severity=self.severity,
            severity_unknown_reason=self.severity_unknown_reason,
            notes=self.notes,
        )

    # -- judgement ----------------------------------------------------

    def judge_duplicate(self, *, evidence, candidates):
        self.judge_calls += 1
        self.last_candidates = list(candidates)
        index = min(self.judge_calls - 1, len(self.duplicate_verdicts) - 1)
        verdict = self.duplicate_verdicts[index]
        master = None
        if verdict == "SAME_INCIDENT" and candidates:
            # Copied verbatim from the candidate list, exactly as the prompt
            # requires — an invented id would be dropped by the node.
            master = str(candidates[0]["ticket_id"])
        return DuplicateJudgementV4(verdict=verdict, master_ticket_id=master, reason=self.duplicate_reason)

    # -- action -------------------------------------------------------

    def decide_next_action(self, **kwargs):
        self.action_calls += 1
        index = min(self.action_calls - 1, len(self.actions) - 1)
        decision = self.actions[index]
        available = kwargs.get("available_actions") or []
        if decision.action not in available:
            # Keep the script honest: a scripted action the graph did not offer
            # would be testing a path that cannot happen.
            return ActionDecisionV4(action="CONCLUDE", reason="Hành động đã lên kịch bản không khả dụng.")
        return decision
