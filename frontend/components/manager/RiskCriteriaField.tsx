"use client";

import {
  BLOCKER_CODES,
  BLOCKER_FLOORS,
  BLOCKER_LABELS,
  CRITERIA,
  CRITERION_ANCHORS,
  CRITERION_LABELS,
  CRITERION_WEIGHTS,
  MAX_CRITERION_SCORE,
} from "@/lib/risk";
import type { BlockerCode, RiskAssessment, RiskCriteriaInput, RiskCriterion } from "@/types/api";
import { RiskBreakdown } from "@/components/manager/RiskBreakdown";

type Props = {
  /** True when the analysis produced no assessment and the backend needs one. */
  missing: boolean;
  stored: RiskAssessment | null;
  value: Partial<RiskCriteriaInput>;
  blockers: BlockerCode[];
  disabled?: boolean;
  onChange: (next: Partial<RiskCriteriaInput>) => void;
  onBlockersChange: (next: BlockerCode[]) => void;
};

/** Scoring a report by hand, when the analysis never scored it.
 *
 *  Five selects, one per criterion, each showing its own 0–4 anchors. That is
 *  more clicks than the single LOW/MEDIUM/HIGH dropdown it replaces, and the
 *  extra clicks are the point: "how serious is it" has no answer two
 *  coordinators would give the same way, while "how many apartments are
 *  affected" has exactly one.
 *
 *  No total is shown while the form is being filled in. The backend computes
 *  the score, and a number rendered here would be a second implementation of
 *  the formula that could disagree with the one that matters.
 *
 *  When the report already carries an assessment this is read-only context:
 *  manual review settles the Category, and changing an existing score is the
 *  classification override, not this form.
 */
export function RiskCriteriaField({ missing, stored, value, blockers, disabled, onChange, onBlockersChange }: Props) {
  if (!missing) {
    return (
      <div className="managerManualRiskRead">
        <span className="managerManualRiskReadLabel">Điểm rủi ro đã có</span>
        {stored ? <RiskBreakdown assessment={stored} /> : <strong>Chưa chấm điểm</strong>}
      </div>
    );
  }

  const setCriterion = (criterion: RiskCriterion, next: string) => {
    onChange({ ...value, [criterion]: next === "" ? undefined : Number(next) });
  };
  const toggleBlocker = (code: BlockerCode) => {
    onBlockersChange(blockers.includes(code) ? blockers.filter((item) => item !== code) : [...blockers, code]);
  };

  return (
    <div className="managerManualRiskForm">
      <p className="managerManualRiskIntro">
        AI chưa chấm điểm phản ánh này. Chấm năm tiêu chí theo đúng những gì quan sát được — hệ thống tự
        tính điểm và mức ưu tiên.
      </p>
      {CRITERIA.map((criterion) => (
        <div className="field managerManualCriterion" key={criterion}>
          <label htmlFor={`risk-${criterion}`}>
            {CRITERION_LABELS[criterion]} <small>trọng số {CRITERION_WEIGHTS[criterion]}</small>
          </label>
          <select
            id={`risk-${criterion}`}
            value={value[criterion] === undefined ? "" : String(value[criterion])}
            required
            aria-required="true"
            disabled={disabled}
            onChange={(event) => setCriterion(criterion, event.target.value)}
          >
            <option value="">— Chọn mức —</option>
            {Array.from({ length: MAX_CRITERION_SCORE + 1 }, (_unused, score) => (
              <option value={score} key={score}>
                {score} · {CRITERION_ANCHORS[criterion][score]}
              </option>
            ))}
          </select>
        </div>
      ))}

      <fieldset className="managerManualBlockers" disabled={disabled}>
        <legend>
          Sự kiện khẩn cấp <small>nâng sàn mức ưu tiên, không cộng điểm</small>
        </legend>
        {BLOCKER_CODES.map((code) => (
          <label key={code}>
            <input type="checkbox" checked={blockers.includes(code)} onChange={() => toggleBlocker(code)} />
            <span>
              {BLOCKER_LABELS[code]} <small>→ {BLOCKER_FLOORS[code]}</small>
            </span>
          </label>
        ))}
      </fieldset>
    </div>
  );
}

/** Whether every criterion has an answer. No default anywhere: an unanswered
 *  control keeps the confirm button disabled rather than scoring a zero
 *  somebody did not choose. */
export function criteriaComplete(value: Partial<RiskCriteriaInput>): value is RiskCriteriaInput {
  return CRITERIA.every((criterion) => typeof value[criterion] === "number");
}
