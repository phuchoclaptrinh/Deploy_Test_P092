"use client";

import { ShieldAlert, TrendingUp, Users } from "lucide-react";
import {
  CRITERIA,
  CRITERION_ANCHORS,
  CRITERION_LABELS,
  CRITERION_WEIGHTS,
  MAX_CRITERION_SCORE,
  PRIORITY_BANDS,
  PRIORITY_LABELS,
  RISK_SOURCE_LABELS,
  blockerRaisedPriority,
  criterionPoints,
  criterionScore,
  formatBlocker,
  formatRiskScore,
  scopeWasOverruled,
} from "@/lib/risk";
import type { RiskAssessment } from "@/types/api";

/** Why this ticket has the priority it has.
 *
 *  The whole point of the screen is that a manager can disagree with the number
 *  in a specific way. "This is a P4" invites either acceptance or a fight;
 *  "human safety 3 of 4, thirty points, because the report says an exposed
 *  socket at child height" invites a correction to one row.
 *
 *  Three things get called out rather than left to be worked out from the rows:
 *
 *  * a **blocker** that floored the priority, because then the score on screen
 *    does not explain the band on screen and that is confusing until it is said;
 *  * a **scope the backend overruled**, because that is the one place a case
 *    outvotes the model and a manager comparing the two should see it happened;
 *  * **unknown facts**, because a 0 the Agent chose and a 0 it fell back to are
 *    different findings that look identical in a table.
 */
export function RiskBreakdown({ assessment }: { assessment: RiskAssessment }) {
  const blockerDecided = blockerRaisedPriority(assessment);
  const overruled = scopeWasOverruled(assessment);

  return (
    <div className="riskBreakdown">
      <header className="riskBreakdownHead">
        <div>
          <span className="riskBreakdownScore">{formatRiskScore(assessment.risk_score)}</span>
          <small>/ 100</small>
        </div>
        <div className="riskBreakdownBands">
          <strong className={`badge priority-${assessment.final_priority}`}>
            {PRIORITY_LABELS[assessment.final_priority]}
          </strong>
          <small>
            {blockerDecided
              ? `Điểm rơi vào ${assessment.score_priority} (${PRIORITY_BANDS[assessment.score_priority]}), blocker nâng sàn lên ${assessment.blocker_floor}`
              : `Dải điểm ${PRIORITY_BANDS[assessment.final_priority]}`}
          </small>
        </div>
      </header>

      {blockerDecided && (
        <p className="riskBreakdownNotice bad">
          <ShieldAlert size={15} />
          Mức ưu tiên do sự kiện khẩn cấp quyết định, không phải do điểm số.
        </p>
      )}

      <table className="riskBreakdownTable">
        <thead>
          <tr>
            <th>Tiêu chí</th>
            <th>Điểm</th>
            <th>Đóng góp</th>
            <th>Diễn giải</th>
          </tr>
        </thead>
        <tbody>
          {CRITERIA.map((criterion) => {
            const score = criterionScore(assessment, criterion);
            const unknown = assessment.unknown_facts.includes(criterion);
            return (
              <tr key={criterion} className={unknown ? "riskUnknown" : undefined}>
                <td>{CRITERION_LABELS[criterion]}</td>
                <td>
                  <strong>{score}</strong>
                  <small>/{MAX_CRITERION_SCORE}</small>
                </td>
                <td>
                  {criterionPoints(criterion, score).toFixed(2)}
                  <small> / {CRITERION_WEIGHTS[criterion]}</small>
                </td>
                <td>
                  {CRITERION_ANCHORS[criterion][score]}
                  {unknown && <em> · AI không xác định được</em>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <div className="riskBreakdownScope">
        <Users size={15} />
        <span>Phạm vi</span>
        <strong>
          {assessment.confirmed_affected_unit_count
            ? `${assessment.confirmed_affected_unit_count} căn hộ đã xác nhận`
            : "Chưa có case xác nhận"}
        </strong>
        {overruled && (
          <em>
            AI ước lượng {assessment.ai_scope_score}, hệ thống đếm được {assessment.backend_scope_score} —
            dùng số đếm được.
          </em>
        )}
      </div>

      {assessment.blocker_codes.length > 0 && (
        <ul className="riskBreakdownBlockers">
          {assessment.blocker_codes.map((code) => (
            <li key={code}>
              <ShieldAlert size={14} />
              {formatBlocker(code)}
            </li>
          ))}
        </ul>
      )}

      <RiskEvidence assessment={assessment} />

      <footer className="riskBreakdownFoot">
        <TrendingUp size={14} />
        <span>
          Bản {assessment.revision_no} · {RISK_SOURCE_LABELS[assessment.source] ?? assessment.source} ·{" "}
          {assessment.rubric_version}
        </span>
        {assessment.override_reason && <em>Ghi đè: {assessment.override_reason}</em>}
      </footer>
    </div>
  );
}

/** What the Agent said it saw, per criterion. Empty sections are dropped rather
 *  than rendered blank: "no evidence for spread" is already said by the 0.
 *
 *  Blockers are their own sections, one per code. The payload keys them that
 *  way because each code sets a different floor, and a pooled list left a
 *  reviewer unable to tell which line justified which one. */
function RiskEvidence({ assessment }: { assessment: RiskAssessment }) {
  const evidence = assessment.evidence || {};
  const sections: { key: string; label: string; lines: string[] }[] = [];
  for (const criterion of CRITERIA) {
    const lines = evidence[criterion] || [];
    if (lines.length) sections.push({ key: criterion, label: CRITERION_LABELS[criterion], lines });
  }
  for (const [code, lines] of blockerEvidenceEntries(evidence.blockers)) {
    if (lines.length) sections.push({ key: `blocker:${code}`, label: formatBlocker(code), lines });
  }
  if (!sections.length) return null;
  return (
    <details className="riskBreakdownEvidence">
      <summary>Bằng chứng AI ghi nhận</summary>
      {sections.map((section) => (
        <div key={section.key}>
          <strong>{section.label}</strong>
          <ul>
            {section.lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      ))}
    </details>
  );
}

function blockerEvidenceEntries(blockers: RiskAssessment["evidence"]["blockers"]): [string, string[]][] {
  if (!blockers) return [];
  if (Array.isArray(blockers)) {
    return blockers.map((line, index) => [`UNATTRIBUTED_${index}`, [String(line)]]);
  }
  return Object.entries(blockers).map(([code, lines]) => [
    code,
    Array.isArray(lines) ? lines.map(String).filter((line) => line.trim()) : [],
  ]);
}
