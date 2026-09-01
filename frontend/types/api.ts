
export type OtpSession = { access_token: string; refresh_token: string | null; token_type: string; expires_in: number | null };
export type CurrentUser = { user_id: string; role: string; full_name: string | null; phone_e164: string | null; unit: { id: string; unit_code: string; floor_code: string } | null };
export type LocationItem = { id: string; floor_code: string; floor_display_name: string; location_type_code: string; location_type_name: string; unit_code: string | null; label: string };
export type SignedUpload = { upload_id: string; signed_upload_url: string | null; required_headers: Record<string, string> };
export type TicketAttachment = { id: string; mime_type: string | null; size_bytes: number | null; attachment_type: "ISSUE_ORIGINAL" | "RESIDENT_SUPPLEMENT" | "TECHNICIAN_COMPLETION" | string; download_url_endpoint: string };
export type AttachmentDownload = { attachment_id: string; signed_download_url: string; expires_in: number; mime_type: string | null; size_bytes: number | null };
export type ResidentLifecycleGroup = "ACTIVE" | "FINISHED";
export type ResidentTicket = { id: string; display_code: string; description: string | null; display_status: string; category_display_name: string | null; priority_description: string | null; progress_text: string; expected_start_at: string | null; location_label: string; reporter_name: string | null; is_reporter: boolean; lifecycle_group: ResidentLifecycleGroup; invalid_reason_text: string | null; created_at: string; updated_at: string; available_actions: string[]; duplicate_of_ticket_id?: string | null; duplicate_master_display_code?: string | null; technician?: { id: string; full_name: string | null } | null; completion_note?: string | null; attachments: TicketAttachment[]; timeline: Array<{ display_status: string; reason: string | null; created_at: string }> };
export type ResidentCategory = { id: string; code: string; display_name: string };
/** The four things the agent may ask a resident. A location confirmation is the
 *  only one whose answer is structured: it carries an explicit `location_id`
 *  picked from the same fixed selector the report was created with. */
export type ResidentAgentQuestionKind = "CATEGORY_CONFIRMATION" | "SEVERITY_CONFIRMATION" | "LOCATION_CONFIRMATION" | "RECENT_COMPLETION";
export type ResidentAgentQuestion = { id: string; question_kind: ResidentAgentQuestionKind | null; question_type: "MULTIPLE_CHOICE" | "FREE_TEXT"; question_text: string; options: string[] | null; allow_free_text_fallback: boolean; round_number: number; expires_at: string | null; current_location_id: string | null; current_location_label: string | null };
export type TicketList<T> = { items: T[]; page: number; page_size: number; total: number };
export type CreatedTicket = { ticket_id: string; status: string; classification_status: string; display_status: string };
/** One duplicate candidate exactly as the agent was shown it. Management reviews
 *  this snapshot rather than a fresh query, so the reason and the list it is
 *  about can never disagree. */
export type CoordinatorDuplicateCandidate = { ticket_id: string; display_code: string; category_name: string; location_label: string; floor_label: string; status: string; summary: string; created_at: string | null; completed_at: string | null; recently_completed: boolean };
/** `final_category_id` is the answer; the two evidence categories explain how it
 *  was reached and are never merged into it. `ai_reason` says why the ticket was
 *  classified this way, `duplicate_reason` why the duplicate verdict is or is not
 *  certain — two different questions. `error_code` is set only when the run
 *  failed technically instead of concluding anything. */
/** P5 is the emergency Priority (five-minute SLA, handled by hand). PENDING is
 *  the only status where the review buttons apply.
 *
 *  The scale inverted with risk scoring v2: P1 is the routine band and P5 the
 *  emergency, where P3 used to be. Anything in this file written before that
 *  reads the opposite way. */
export type EmergencyReviewStatus = "NOT_REQUIRED" | "PENDING" | "CONFIRMED" | "DOWNGRADED";
export type EmergencyDecision = "CONFIRM_P5" | "DOWNGRADE_PRIORITY";
export type TicketPriority = "P1" | "P2" | "P3" | "P4" | "P5";

/** The five judgements the Agent makes. Backend turns them into a score; the
 *  frontend only ever displays them. */
export type RiskCriterion =
  | "human_safety"
  | "property_spread"
  | "essential_function"
  | "affected_scope"
  | "deterioration_speed";

export type BlockerCode =
  | "FIRE_OR_SMOKE"
  | "ELECTRIC_SHOCK_OR_LIVE_WIRE"
  | "GAS_LEAK_OR_ASPHYXIATION"
  | "SERIOUS_INJURY"
  | "PERSON_TRAPPED_IN_ELEVATOR"
  | "SOLE_ESCAPE_ROUTE_BLOCKED"
  | "ONGOING_VIOLENCE"
  | "SEWAGE_OVERFLOW"
  | "HEAVY_WATER_FLOW_SPREAD_RISK"
  | "TOTAL_UNPLANNED_UTILITY_LOSS"
  | "SOLE_TOILET_UNUSABLE";

export type RiskAssessmentSource =
  | "AI_ANALYSIS"
  | "GROUPING_RESCORE"
  | "HUMAN_REVIEW"
  | "DUPLICATE_ESCALATION";

/** One scoring revision, append-only on the backend.
 *
 *  Three scope numbers rather than one, because they answer three questions:
 *  what the Agent estimated from a single report, what a case actually counted,
 *  and which of the two the formula used. An estimate that was overruled is the
 *  most interesting one on the screen, so it is shown rather than replaced.
 *
 *  `score_priority` and `final_priority` differ exactly when a blocker floored
 *  the outcome — which is the case where the number on screen does not explain
 *  the band on screen, and a coordinator needs to be told why. */
export type RiskEvidence = Partial<Record<RiskCriterion, string[]>> & {
  blockers?: Record<string, string[]> | string[];
};

export type RiskAssessment = {
  id: string;
  revision_no: number;
  source: RiskAssessmentSource | string;
  human_safety_score: number;
  property_spread_score: number;
  essential_function_score: number;
  deterioration_speed_score: number;
  ai_scope_score: number;
  backend_scope_score: number | null;
  effective_scope_score: number;
  confirmed_affected_unit_count: number | null;
  blocker_codes: string[];
  /** The five criterion keys hold lines; `blockers` holds one list per blocker
   *  code. Keyed rather than pooled because each code sets a different floor,
   *  so "which line justified which floor" is a question with an answer. */
  evidence: RiskEvidence;
  unknown_facts: string[];
  risk_score: number;
  score_priority: TicketPriority;
  blocker_floor: TicketPriority | null;
  final_priority: TicketPriority;
  rubric_version: string;
  case_id_snapshot: string | null;
  case_density_snapshot: number | null;
  override_reason: string | null;
  reviewed_by: string | null;
  created_at: string;
};

/** The five 0–4 judgements, as a coordinator supplies them by hand when the
 *  analysis produced none. */
export type RiskCriteriaInput = Record<RiskCriterion, number>;

export type CoordinatorAnalysisSummary = { run_number: number; exit_reason: string | null; final_category_id: string | null; text_category_id: string | null; image_category_id: string | null; risk_assessment: RiskAssessment | null; ai_reason: string | null; duplicate_verdict: string | null; duplicate_reason: string | null; duplicate_candidates: CoordinatorDuplicateCandidate[]; grouping_status: string | null; emergency_review_status: EmergencyReviewStatus | null; emergency_decision: EmergencyDecision | null; emergency_decision_reason: string | null; emergency_reviewed_by: string | null; emergency_reviewed_at: string | null; ai_priority_before_review: TicketPriority | null; effective_priority: TicketPriority | null; model_version: string | null; error_code: string | null };
export type CoordinatorAgentQuestionSummary = { id: string; question_kind: string | null; question_type: string; question_text: string; options: string[] | null; allow_free_text_fallback: boolean; round_number: number; status: string; answer_type: string | null; answer_text: string | null; answer_payload: Record<string, unknown> | null; answer_upload_id: string | null; asked_at: string; answered_at: string | null; expires_at: string | null };
export type CoordinatorTicketReporter = { user_id: string; full_name: string | null; phone_e164: string | null; unit_code: string | null; floor_label: string | null };
export type CoordinatorTimelineItem = { from_status: string | null; to_status: string; reason: string | null; created_at: string };
export type CoordinatorTicket = { id: string; reporter_user_id: string; reporter: CoordinatorTicketReporter | null; source_unit_id: string; location_label: string | null; description: string | null; status: string; classification_status: string; display_code: "P0" | null; category_id: string | null; category: string | null; priority: TicketPriority | null; risk_score: number | null; risk_assessment: RiskAssessment | null; case_unit_count: number | null; sla_started_at?: string | null; sla_due_at: string | null; created_at: string; updated_at: string; version?: number; available_actions: string[]; duplicate_of_ticket_id?: string | null; duplicate_master_display_code?: string | null; invalid_reason?: string | null; reassignment_count?: number; auto_assignment_paused?: boolean; auto_assignment_pause_reason?: string | null; active_assignment_id: string | null; active_assignment_status: string | null; active_assignment_source?: string | null; active_technician_id: string | null; active_technician_name: string | null; active_assignment_updated_at?: string | null; planned_start_at?: string | null; planned_finish_at?: string | null; planned_order?: number | null; assignment_risk_state?: string | null; slack_seconds?: number | null; completion_note?: string | null; completed_technician_name?: string | null; latest_analysis: CoordinatorAnalysisSummary | null; agent_questions: CoordinatorAgentQuestionSummary[]; attachments: TicketAttachment[]; timeline?: CoordinatorTimelineItem[] };
export type CoordinatorClusterTicket = { id: string; display_code: string; description: string | null; status: string; priority: TicketPriority | null; location_label: string | null; unit_code: string | null; floor_label: string | null; created_at: string; active_assignment_id: string | null; active_assignment_status: string | null; active_technician_id: string | null; active_technician_name: string | null };
export type CoordinatorCluster = { id: string; category_id: string; category: string; floor_label: string; density: number; status: string; closed: boolean; window_start: string; window_end: string; created_at: string; tickets: CoordinatorClusterTicket[] };
export type CoordinatorClusterApproveResult = { case_id: string; approved_ticket_ids: string[]; skipped_ticket_ids: string[] };
export type CoordinatorClusterAssignResult = { case_id: string; technician_id: string; assigned_ticket_ids: string[]; skipped_ticket_ids: string[]; assignment_ids: string[] };
export type TechnicianSummary = { user_id: string; full_name: string | null; phone_e164: string | null; is_active: boolean; is_available: boolean; skill_category_ids: string[] };
export type CoordinatorResidentSummary = { user_id: string; full_name: string | null; phone_e164: string | null; is_active: boolean; unit_id: string | null; unit_code: string | null; floor_code: string | null; is_primary: boolean | null };
export type ManagerAccount = { user_id: string; role: "RESIDENT" | "TECHNICIAN" | "COORDINATOR"; full_name: string | null; phone_e164: string | null; email: string | null; temporary_password: string | null; unit_id: string | null; unit_code: string | null; is_active: boolean; is_available: boolean | null; skill_category_ids: string[] };
export type TechnicianAssignment = { id: string; status: "ASSIGNED" | "IN_PROGRESS" | "COMPLETED" | "REJECTED" | "REASSIGNED" | "UNABLE_TO_HANDLE"; assigned_at: string; started_at?: string | null; completed_at?: string | null; ended_at?: string | null; unable_reason?: string | null; reject_reason?: string | null; planned_start_at?: string | null; planned_finish_at?: string | null; planned_order?: number | null; risk_state?: string | null; slack_seconds?: number | null; ticket: { id: string; description: string | null; category_display_name: string | null; location_label: string | null; priority: TicketPriority | null; sla_due_at: string | null; attachments: TicketAttachment[] } };
export type TechnicianAvailability = { is_available: boolean };
/** §4: the technician's ordered work queue. Item 0 is "Làm ngay", item 1 is
 *  "Tiếp theo". The split is a rendering decision, so the payload stays one
 *  ordered list. */
export type TechnicianQueue = { generated_at: string; within_working_shift: boolean; items: TechnicianAssignment[] };

/** §2: the Automatic Assignment switch. One boolean and its provenance — no
 *  activation delay, no proposal batch, no schedule. All three belonged to the
 *  architecture §9 removed. */
export type AutoAssignmentToggle = {
  enabled: boolean;
  version: number;
  enabled_at: string | null;
  enabled_by_user_id: string | null;
  enabled_by_name: string | null;
  updated_at: string | null;
  /** How many dispatch events are waiting right now, so turning the switch off
   *  is an informed act rather than a blind one. */
  open_event_count: number;
};

/** What the board flags about one pairing. The first three are §3 hard
 *  constraints and block the drop; the last two are advisory and do not. */
export type PlacementWarning =
  | "MISSING_SKILL"
  | "TECHNICIAN_UNAVAILABLE"
  | "OUT_OF_SHIFT"
  | "OVERLOADED"
  | "SCHEDULE_RISK";

export type BoardPlacementPreview = {
  technician_id: string;
  /** True when a §3 hard constraint fails. The board must refuse the drop —
   *  `confirmVisualAssignment` would reject the whole board otherwise. */
  blocked: boolean;
  warnings: PlacementWarning[] | string[];
  planned_start_at: string | null;
  /** Internal scheduling value. Shown to Building Management, never to a
   *  resident, and never described as a completion promise. */
  planned_finish_at: string | null;
  worst_slack_seconds: number | null;
};

/** One draggable item. A `GROUP` unit covers several tickets and is
 *  indivisible (§1): its members are listed for display only and there is no
 *  per-member placement. */
export type BoardUnit = {
  unit_id: string;
  unit_type: "TICKET" | "GROUP" | string;
  ticket_ids: string[];
  display_codes: string[];
  category_id: string | null;
  category_code: string | null;
  category_display_name: string | null;
  priority: TicketPriority | null;
  score: number;
  submitted_at: string;
  location_labels: string[];
  /** Internal P80 estimate for the whole unit, in seconds (§5). */
  p80_seconds: number;
  member_count: number;
  eligible_technician_ids: string[];
  previews: BoardPlacementPreview[];
};

export type BoardPlannedSlot = {
  assignment_id: string | null;
  ticket_id: string | null;
  order: number;
  planned_start_at: string;
  planned_finish_at: string;
  slack_seconds: number | null;
  in_progress: boolean;
};

export type BoardTechnician = {
  technician_id: string;
  display_name: string;
  is_active: boolean;
  is_available: boolean;
  skill_category_ids: string[];
  active_assignment_count: number;
  in_progress_count: number;
  planned_slots: BoardPlannedSlot[];
  day_ends_at: string | null;
};

export type VisualBoard = {
  generated_at: string;
  /** False outside 08:00–18:00 Vietnam time. Every placement is blocked while
   *  it is false, so the board says so once at the top. */
  within_working_shift: boolean;
  units: BoardUnit[];
  technicians: BoardTechnician[];
};

export type VisualPlacement = { unit_id: string; technician_id: string };
export type VisualConfirmResult = { assigned_unit_count: number; assigned_ticket_count: number; assignment_ids: string[] };
/** What a rejected confirm carries back, so the board can mark the offending
 *  cards instead of making anyone hunt for them. */
export type VisualPlacementFailure = { unit_id: string; technician_id: string; codes: string[] };

export type DispatchEventStatus = "PENDING" | "CLAIMED" | "ASSIGNED" | "ESCALATED" | "SUPERSEDED" | "FAILED";
export type DispatchEvent = {
  id: string;
  ticket_id: string;
  ticket_display_code: string | null;
  status: DispatchEventStatus | string;
  priority: string;
  risk_state: "SAFE" | "AT_RISK" | string | null;
  decision_source: "SCHEDULER" | "AGENT" | "SCHEDULER_FALLBACK" | string | null;
  selected_technician_id: string | null;
  selected_technician_name: string | null;
  assignment_id: string | null;
  batch_id: string | null;
  attempt_count: number;
  planned_start_at: string | null;
  planned_finish_at: string | null;
  slack_seconds: number | null;
  escalation_reason: string | null;
  error_code: string | null;
  enqueued_at: string;
  available_at: string;
  decided_at: string | null;
};

/** §7. `decision_source` is the field that matters in review: `AGENT` means a
 *  model weighed the trade-off, `SCHEDULER_FALLBACK` means it did not answer in
 *  time and the least-negative-slack candidate was taken instead. */
export type AtRiskDecision = {
  id: string;
  dispatch_event_id: string;
  ticket_id: string;
  ticket_display_code: string | null;
  batch_id: string;
  technician_id: string | null;
  technician_name: string | null;
  decision_source: "AGENT" | "SCHEDULER_FALLBACK" | string;
  reason: string | null;
  model_name: string | null;
  latency_ms: number | null;
  candidate_technician_ids: string[];
  slack_seconds: number | null;
  error_code: string | null;
  created_at: string;
};

/** The sweep no longer touches assignments: the acceptance deadline is gone and
 *  no start deadline has replaced it. See docs/assignment_lifecycle.md. */
export type OperationalTimeoutSweep = { resident_question_timeouts: number };
/** One manually triggered micro-batch, for operations screens. */
export type DispatchWorkerRun = { batch_id: string | null; claimed: number; reclaimed: number; assigned_safe: number; assigned_by_agent: number; assigned_by_fallback: number; at_risk: number; escalated: number; out_of_shift: boolean; query_count: number; agent_calls: number; agent_error: string | null; duration_ms: number; errors: string[] };

export type BackendNotification = { id: string; ticket_id: string | null; notification_type: string; channel: string; title: string; body: string; status: string; created_at: string; sent_at: string | null };
/** A routing and reporting label. No base score and no priority ceiling: a
 *  category takes no part in scoring under the v2 rubric. */
export type CoordinatorCategory = { id: string; code: string; display_name: string; is_active: boolean };
export type BackendAuditLog = { id: number; actor_user_id: string | null; actor_role: string; action: string; entity_type: string; entity_id: string; before_data: Record<string, unknown> | null; after_data: Record<string, unknown> | null; reason: string | null; created_at: string };
export type TicketSummaryReport = { total: number; by_status: Record<string, number>; by_priority: Record<string, number>; by_category: Record<string, number> };
/** Measured at the moment work *started*, over P1–P4 only. P5 is reported
 *  beside the rate rather than inside it: an emergency is never dispatched,
 *  so it is neither a technician's success nor their failure. */
export type SlaPerformanceReport = { measured_total: number; started_on_time: number; overdue_not_started: number; compliance_rate: number | null; emergency_manual_total: number };
export type TechnicianProductivityRow = { technician_id: string; full_name: string | null; is_active: boolean; active_days: number; completed_tickets: number; sla_late_tickets: number; reassigned_from_other_tickets: number };
export type TechnicianProductivityReport = { period: "week" | "month" | string; period_start: string; period_end: string; rows: TechnicianProductivityRow[] };

/** Capacity & SLA simulation — a read-only what-if run by Building Management.
 *
 *  Two flows, and **neither of them is production**. `OLD_APP` is the manual
 *  baseline; `NEW_APP` is a *hypothetical* policy — P2 before every unstarted
 *  P1, technicians chosen by when they can start, and a conservative fallback
 *  when nobody can make the deadline. The deployed dispatcher does not work
 *  that way, so there is no parity flag on this payload and no badge for the
 *  screen to render.
 *
 *  **SLA is measured at `work_started_at`**, the moment the technician arrives
 *  and begins. `completed_at` exists only for capacity and rostering, and is
 *  never written back to a ticket.
 *
 *  Timestamps arrive in Vietnam local time (+07:00) — the one exception to this
 *  API — see `src/models/api/simulation.py`.
 */
export type SimulationScenarioKey = "OLD_APP" | "NEW_APP";

export type SimulationSlaPolicy = "WALL_CLOCK_V1" | "SERVICE_HOURS_DRAFT_V1" | "SERVICE_HOURS_RISK_V2";

/** `ASSIGNED` means a technician was chosen — not that the work has begun. */
export type SimulationOutcome =
  | "ASSIGNED"
  | "REQUIRES_MANUAL_P3_REVIEW"
  | "REQUIRES_MANUAL_P5_REVIEW"
  | "NO_ELIGIBLE_TECHNICIAN";

/** Measured at the moment work *starts*. `OPEN_OVERDUE` is a breach and is in
 *  the denominator; `OPEN_NOT_DUE` is neither a success nor a breach and is
 *  outside it. */
export type SimulationSlaStatus = "ON_TIME" | "LATE_STARTED" | "OPEN_OVERDUE" | "OPEN_NOT_DUE" | "NOT_EVALUABLE";

export type SimulationReason =
  | "P3_MANUAL_REVIEW"
  | "P5_MANUAL_REVIEW"
  | "MISSING_SKILL"
  | "TECHNICIAN_UNAVAILABLE"
  | "TECHNICIAN_EXCLUDED";

/** Nobody eligible could start before the deadline. */
export type SimulationRiskReason = "START_SLA_RISK";

/** Who chose the technician. None of these claims a model actually decided:
 *  `SCHEDULER_FALLBACK_SIMULATED` is the deterministic conservative branch the
 *  simulator takes where the real system would consult an agent. */
export type SimulationDecisionSource = "SCHEDULER_SIMULATED" | "SCHEDULER_FALLBACK_SIMULATED" | "MANUAL_SIMULATED";

/** Where a ticket's SLA duration came from. `INPUT_OVERRIDE` means the scenario
 *  pinned a duration the policy disagrees with, so that row is not measured
 *  against the policy the run is nominally under. */
export type SimulationSlaDurationSource = "POLICY" | "INPUT_OVERRIDE";

export type SimulationTicketOutcome = {
  ticket_id: string;
  scenario: SimulationScenarioKey;
  outcome: SimulationOutcome;
  sla_status: SimulationSlaStatus;
  reason: SimulationReason | null;
  priority: TicketPriority;
  floor: number;
  unit: string;
  required_skill: string;
  sla_due_at: string;
  /** The duration behind `sla_due_at`, and who decided it. */
  sla_minutes: number;
  sla_duration_source: SimulationSlaDurationSource;
  ready_at: string;
  assigned_technician_id: string | null;
  decision_source: SimulationDecisionSource | null;
  risk_state: "SAFE" | "AT_RISK" | null;
  risk_reason: SimulationRiskReason | null;
  /** What the run projected when it made the assignment. Can differ from what
   *  happened, because a later P2 may overtake an unstarted P1. */
  projected_start_at: string | null;
  projected_start_late_minutes: number;
  /** What the real system would do here. The simulator sends and writes nothing. */
  would_notify_bql: boolean;
  would_write_audit: boolean;
  /** The technician leaves their previous job. */
  departed_at: string | null;
  /** They arrive and begin. **This is the SLA moment.** Null when nothing had
   *  started by the end of the simulated window. */
  work_started_at: string | null;
  /** Simulated completion. Capacity and rostering only — never an SLA moment,
   *  and never written back to a ticket. */
  completed_at: string | null;
  wait_minutes: number;
  response_minutes: number;
  /** Late *starting*, on the clock the running policy uses. For `OPEN_OVERDUE`
   *  it is the overdue time up to the end of the simulated window. */
  start_late_minutes: number;
  travel_minutes: number;
  repair_minutes: number;
  bql_minutes: number;
};

export type SimulationTechnicianLoad = {
  technician_id: string;
  work_minutes: number;
  travel_minutes: number;
  busy_minutes: number;
  assigned_ticket_count: number;
  /** The working minutes the run spans — the denominator of the percentage. */
  capacity_minutes: number;
  utilization_percent: number;
};

export type SimulationScenarioSummary = {
  total_tickets: number;
  /** Given a technician. Not the same as started. */
  assigned_tickets: number;
  started_tickets: number;
  /** ON_TIME + LATE_STARTED + OPEN_OVERDUE. Rendered next to the rate, always. */
  sla_evaluable_tickets: number;
  sla_on_time_tickets: number;
  sla_late_started_tickets: number;
  sla_open_overdue_tickets: number;
  sla_open_not_due_tickets: number;
  sla_not_evaluable_tickets: number;
  /** Null when the denominator is empty, rather than a misleading 0% or 100%. */
  compliance_rate: number | null;
  total_start_late_minutes: number;
  average_start_late_minutes: number;
  average_wait_minutes: number;
  p95_wait_minutes: number;
  average_response_minutes: number;
  p95_response_minutes: number;
  total_travel_minutes: number;
  bql_effort_minutes: number;
  at_risk_tickets: number;
  last_completed_at: string | null;
  technician_utilization: SimulationTechnicianLoad[];
};

export type SimulationScenarioResult = {
  scenario: SimulationScenarioKey;
  summary: SimulationScenarioSummary;
  tickets: SimulationTicketOutcome[];
};

/** The new app measured against the old one.
 *
 *  **Positive always means the new app is better.** Every `_saved` / `_avoided`
 *  field is `OLD_APP − NEW_APP`; `compliance_rate_gain` is `NEW_APP − OLD_APP`,
 *  because there more is better. The subtraction happens once, on the backend,
 *  so nothing on the screen flips a sign. */
export type SimulationComparison = {
  bql_minutes_saved: number;
  bql_hours_saved: number;
  late_starts_avoided: number;
  start_late_minutes_avoided: number;
  average_response_minutes_saved: number;
  p95_response_minutes_saved: number;
  travel_minutes_saved: number;
  compliance_rate_gain: number | null;
};

export type SimulationSettings = {
  travel_base_minutes: number;
  travel_per_floor_minutes: number;
  /** The batch cadence, defaulted from the deployed dispatcher's config. */
  micro_batch_interval_ms: number;
  micro_batch_size: number;
  simulation_horizon_days: number;
  old_app: { manual_category_minutes: number; manual_dispatch_minutes: number };
  new_app: { ai_classification_minutes: number; manual_review_minutes: number };
};

export type SimulationRun = {
  generated_at: string;
  scenario_name: string;
  sla_policy: SimulationSlaPolicy;
  building: { floor_count: number; units_per_floor: number };
  settings: SimulationSettings;
  /** The instant every unstarted ticket was judged against. */
  horizon_end: string;
  old_app: SimulationScenarioResult;
  new_app: SimulationScenarioResult;
  comparison: SimulationComparison;
  warnings: string[];
};

/** The request body: one scenario document, not three separate sources. */
export type SimulationRunRequest = { scenario: Record<string, unknown> };
