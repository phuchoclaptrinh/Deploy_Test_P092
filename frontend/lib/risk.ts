/** The five-criterion rubric, on the screen.
 *
 *  Everything here mirrors `docs/risk_scoring_v2.md`, and nothing here computes
 *  anything. The backend owns the arithmetic; this module owns the words, the
 *  colours and the ordering — so a screen showing 72.5 next to "P4" and a
 *  report saying the same are reading one vocabulary.
 *
 *  Replaces `severity.ts`. LOW/MEDIUM/HIGH is gone, and with it the question a
 *  coordinator could never answer consistently ("is a blocked stairwell MEDIUM
 *  or HIGH?"). What replaced it is five questions that each have an observable
 *  answer.
 */

import type { BlockerCode, RiskAssessment, RiskCriterion, TicketPriority } from "@/types/api";

/** Heaviest first. Every table and every breakdown renders in this order, so
 *  two screens never disagree about which number is which.
 *
 *  This is display order only. The contract's field order -- the one the
 *  payload and the assessment columns use -- is the declaration order in
 *  `src/domain/risk_scoring.py`, and the two are deliberately allowed to
 *  differ: retuning a weight should not rewrite a database column order. */
export const CRITERIA: RiskCriterion[] = [
  "essential_function",
  "human_safety",
  "affected_scope",
  "property_spread",
  "deterioration_speed",
];

export const CRITERION_LABELS: Record<RiskCriterion, string> = {
  human_safety: "An toàn con người",
  property_spread: "Mức lan thiệt hại",
  essential_function: "Chức năng thiết yếu",
  affected_scope: "Phạm vi căn hộ",
  deterioration_speed: "Tốc độ xấu đi",
};

/** Shown beside each score, because "3" on its own is not a finding. */
export const CRITERION_WEIGHTS: Record<RiskCriterion, number> = {
  human_safety: 35,
  property_spread: 5,
  essential_function: 35,
  affected_scope: 20,
  deterioration_speed: 5,
};

/** The 0–4 anchors, abbreviated for a tooltip. The full wording lives in the
 *  contract; these are what fits next to a number. */
export const CRITERION_ANCHORS: Record<RiskCriterion, [string, string, string, string, string]> = {
  human_safety: ["Không có yếu tố an toàn", "Rủi ro gián tiếp", "Rủi ro tránh được", "Nguy hiểm cao", "Đe doạ tính mạng"],
  property_spread: ["Không lan", "Lan chậm trong căn", "Lan rõ trong căn", "Đang lan sang căn khác", "Lan nhanh diện rộng"],
  essential_function: ["Không ảnh hưởng", "Suy giảm nhẹ", "Mất chức năng phụ", "Mất chức năng thiết yếu", "Căn hộ không ở được"],
  affected_scope: ["Một căn", "Hai căn", "Ba căn", "Bốn căn", "Từ năm căn"],
  deterioration_speed: ["Ổn định", "Theo tuần", "Theo ngày", "Theo giờ", "Theo phút"],
};

export const MAX_CRITERION_SCORE = 4;

/** Contribution of one criterion to the total, for the breakdown bar. */
export function criterionPoints(criterion: RiskCriterion, score: number): number {
  return (score / MAX_CRITERION_SCORE) * CRITERION_WEIGHTS[criterion];
}

// ---------------------------------------------------------------------------
// Priority
// ---------------------------------------------------------------------------

/** Lowest urgency first, which is also the order of the score bands. Note the
 *  direction: P5 is the emergency. Anything written before risk scoring v2 read
 *  the opposite way. */
export const PRIORITIES: TicketPriority[] = ["P1", "P2", "P3", "P4", "P5"];

export const PRIORITY_LABELS: Record<TicketPriority, string> = {
  P1: "P1 · Thông thường",
  P2: "P2 · Theo lịch",
  P3: "P3 · Cần sớm",
  P4: "P4 · Trong ca",
  P5: "P5 · Khẩn cấp",
};

/** The score band each priority covers, shown so a manager can see why a
 *  ticket landed where it did without opening the contract. */
export const PRIORITY_BANDS: Record<TicketPriority, string> = {
  P1: "0–20",
  P2: "20–40",
  P3: "40–60",
  P4: "60–80",
  P5: "80–100",
};

/** Four tones, not five: P1 and P2 are both "this is fine, it is queued", and
 *  giving them separate colours would spend a signal on a distinction nobody
 *  acts on. P5 is alone at the top because it is the one that means "stop what
 *  you are doing". */
export const PRIORITY_TONES: Record<TicketPriority, "neutral" | "info" | "warn" | "bad" | "critical"> = {
  P1: "neutral",
  P2: "neutral",
  P3: "info",
  P4: "warn",
  P5: "critical",
};

/** The promise attached to each band, in minutes.
 *
 *  Mirrors `POLICY_SLA_MINUTES[SERVICE_HOURS_RISK_V2]` in
 *  `src/domain/sla_clock.py`, which is the table the deadlines are actually
 *  written from. Duplicated rather than fetched because a report has to render
 *  its own axis before any ticket arrives; `frontend/tests/prioritySla.test.ts`
 *  reads the Python and fails if the two drift.
 *
 *  P1-P4 are *service* minutes: the clock pauses outside 08:00-18:00, so 1800
 *  is three working days rather than thirty hours of wall time. P5 is the
 *  exception in both directions -- five wall-clock minutes, and not a
 *  technician's deadline at all but Building Management's own.
 */
export const PRIORITY_SLA_MINUTES: Record<TicketPriority, number> = {
  P1: 1800,
  P2: 1200,
  P3: 600,
  P4: 180,
  P5: 5,
};

export const PRIORITY_SLA_TEXT: Record<TicketPriority, string> = {
  P1: "3 ngày làm việc",
  P2: "2 ngày làm việc",
  P3: "1 ngày làm việc",
  P4: "3 giờ làm việc",
  P5: "5 phút (24/7)",
};

/** The bands a technician's SLA compliance is measured over, highest first.
 *
 *  P5 is absent, and absent rather than counted as a pass: an emergency nobody
 *  was dispatched to is not a technician's success or failure. Mirrors
 *  `COMPLIANCE_PRIORITIES` in `src/domain/sla_clock.py`.
 */
export const COMPLIANCE_PRIORITIES: TicketPriority[] = ["P4", "P3", "P2", "P1"];

export const EMERGENCY_PRIORITY: TicketPriority = "P5";

export function isEmergency(priority: TicketPriority | null | undefined): boolean {
  return priority === EMERGENCY_PRIORITY;
}

/** The bands a human may set on a form. P5 is absent on purpose.
 *
 *  An emergency is not something a coordinator types into a dropdown. It is
 *  reached one of two ways -- the rubric scores it there, or a blocker floors
 *  it there -- and it leaves through the emergency gate, which records a
 *  decision and a reason. A generic override that could write P5 would be a
 *  third way in, with no gate behind it and nothing recorded about why.
 *
 *  The same list serves the downgrade select for the same reason read from the
 *  other side: staying at P5 is *confirming*, not downgrading.
 */
export const OVERRIDE_PRIORITIES: TicketPriority[] = ["P1", "P2", "P3", "P4"];

/** @see OVERRIDE_PRIORITIES -- the emergency gate's downgrade targets. */
export const DOWNGRADE_PRIORITIES = OVERRIDE_PRIORITIES;

// ---------------------------------------------------------------------------
// Blockers
// ---------------------------------------------------------------------------

export const BLOCKER_LABELS: Record<BlockerCode, string> = {
  FIRE_OR_SMOKE: "Cháy hoặc khói",
  ELECTRIC_SHOCK_OR_LIVE_WIRE: "Điện giật / dây điện sống",
  GAS_LEAK_OR_ASPHYXIATION: "Rò gas / ngạt khí",
  SERIOUS_INJURY: "Chấn thương nghiêm trọng",
  PERSON_TRAPPED_IN_ELEVATOR: "Người kẹt trong thang máy",
  SOLE_ESCAPE_ROUTE_BLOCKED: "Lối thoát duy nhất bị chặn",
  ONGOING_VIOLENCE: "Bạo lực đang diễn ra",
  SEWAGE_OVERFLOW: "Nước thải trào ngược",
  HEAVY_WATER_FLOW_SPREAD_RISK: "Nước chảy mạnh, nguy cơ lan",
  TOTAL_UNPLANNED_UTILITY_LOSS: "Mất điện/nước ngoài kế hoạch",
  SOLE_TOILET_UNUSABLE: "Toilet duy nhất không dùng được",
};

/** The floor each blocker imposes. Shown next to the code so a manager reading
 *  "P5" on a ticket that scored 13 can see which fact put it there. */
export const BLOCKER_FLOORS: Record<BlockerCode, TicketPriority> = {
  FIRE_OR_SMOKE: "P5",
  ELECTRIC_SHOCK_OR_LIVE_WIRE: "P5",
  GAS_LEAK_OR_ASPHYXIATION: "P5",
  SERIOUS_INJURY: "P5",
  PERSON_TRAPPED_IN_ELEVATOR: "P5",
  SOLE_ESCAPE_ROUTE_BLOCKED: "P5",
  ONGOING_VIOLENCE: "P5",
  SEWAGE_OVERFLOW: "P4",
  HEAVY_WATER_FLOW_SPREAD_RISK: "P4",
  TOTAL_UNPLANNED_UTILITY_LOSS: "P4",
  SOLE_TOILET_UNUSABLE: "P4",
};

export const BLOCKER_CODES = Object.keys(BLOCKER_LABELS) as BlockerCode[];

export function formatBlocker(code: string): string {
  return BLOCKER_LABELS[code as BlockerCode] ?? code;
}

// ---------------------------------------------------------------------------
// Reading one assessment
// ---------------------------------------------------------------------------

export const RISK_SOURCE_LABELS: Record<string, string> = {
  AI_ANALYSIS: "AI phân tích",
  GROUPING_RESCORE: "Chấm lại sau gộp cụm",
  HUMAN_REVIEW: "Ban quản lý quyết định",
  DUPLICATE_ESCALATION: "Nâng theo phản ánh trùng",
};

export function formatRiskScore(score: number | null | undefined): string {
  return score === null || score === undefined ? "—" : score.toFixed(2);
}

/** The score of one criterion on an assessment, using the *effective* scope.
 *
 *  `affected_scope` is the one criterion with three stored values, and the
 *  effective one is what the formula used. A breakdown that showed the Agent's
 *  estimate would not add up to the total printed beside it.
 */
export function criterionScore(assessment: RiskAssessment, criterion: RiskCriterion): number {
  if (criterion === "affected_scope") return assessment.effective_scope_score;
  return assessment[`${criterion}_score` as const];
}

/** True when a blocker, rather than the score, decided the outcome. The single
 *  most useful thing to put in front of a coordinator: it means the number on
 *  screen does not explain the band on screen. */
export function blockerRaisedPriority(assessment: RiskAssessment | null | undefined): boolean {
  if (!assessment) return false;
  return assessment.final_priority !== assessment.score_priority;
}

/** True when a case counted more apartments than the Agent estimated, or
 *  fewer. Worth flagging because it is the one place the backend overrules the
 *  model, and a manager comparing the two should see that it happened. */
export function scopeWasOverruled(assessment: RiskAssessment | null | undefined): boolean {
  if (!assessment || assessment.backend_scope_score === null) return false;
  return assessment.backend_scope_score !== assessment.ai_scope_score;
}
