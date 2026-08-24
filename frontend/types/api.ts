import type { TicketSeverity } from "@/lib/severity";

export type OtpSession = { access_token: string; refresh_token: string | null; token_type: string; expires_in: number | null };
export type CurrentUser = { user_id: string; role: string; full_name: string | null; phone_e164: string | null; unit: { id: string; unit_code: string; building_code: string; floor_code: string } | null };
export type LocationItem = { id: string; building_code: string; floor_code: string; floor_display_name: string; location_type_code: string; location_type_name: string; unit_code: string | null; label: string };
export type SignedUpload = { upload_id: string; signed_upload_url: string | null; required_headers: Record<string, string> };
export type TicketAttachment = { id: string; mime_type: string | null; size_bytes: number | null; download_url_endpoint: string };
export type AttachmentDownload = { attachment_id: string; signed_download_url: string; expires_in: number; mime_type: string | null; size_bytes: number | null };
export type ResidentLifecycleGroup = "ACTIVE" | "FINISHED";
export type ResidentTicket = { id: string; display_code: string; description: string | null; display_status: string; category_display_name: string | null; priority_description: string | null; estimated_resolution_text: string; expected_resolution_at: string | null; location_label: string; reporter_name: string | null; is_reporter: boolean; lifecycle_group: ResidentLifecycleGroup; invalid_reason_text: string | null; created_at: string; updated_at: string; available_actions: string[]; duplicate_of_ticket_id?: string | null; duplicate_master_display_code?: string | null; technician?: { id: string; full_name: string | null } | null; attachments: TicketAttachment[]; timeline: Array<{ display_status: string; reason: string | null; created_at: string }> };
export type ResidentCategory = { id: string; code: string; display_name: string };
export type ResidentAgentQuestion = { id: string; question_type: "MULTIPLE_CHOICE" | "FREE_TEXT"; question_text: string; options: string[] | null; allow_free_text_fallback: boolean; round_number: number; expires_at: string | null };
export type TicketList<T> = { items: T[]; page: number; page_size: number; total: number };
export type CreatedTicket = { ticket_id: string; status: string; classification_status: string; display_status: string };
export type CoordinatorAnalysisSummary = { run_number: number; exit_reason: string | null; text_categories: string[]; image_categories: string[] | null; red_flag_text: boolean; red_flag_signal: boolean; severity: TicketSeverity | null; severity_source: string | null; is_confident: boolean | null; confidence_notes: string | null; text_model_version: string | null; vision_model_version: string | null; error_code: string | null };
export type CoordinatorAgentQuestionSummary = { id: string; question_type: string; question_text: string; options: string[] | null; allow_free_text_fallback: boolean; round_number: number; status: string; answer_type: string | null; answer_text: string | null; answer_upload_id: string | null; asked_at: string; answered_at: string | null; expires_at: string | null };
export type CoordinatorTicketReporter = { user_id: string; full_name: string | null; phone_e164: string | null; unit_code: string | null; building_code: string | null; floor_label: string | null };
export type CoordinatorTimelineItem = { from_status: string | null; to_status: string; reason: string | null; created_at: string };
export type CoordinatorTicket = { id: string; reporter_user_id: string; reporter: CoordinatorTicketReporter | null; source_unit_id: string; location_label: string | null; description: string | null; status: string; classification_status: string; display_code: "P0" | null; category_id: string | null; category: string | null; priority: "P1" | "P2" | "P3" | null; severity: TicketSeverity | null; red_flag_detected: boolean; score_total: number | null; sla_started_at?: string | null; sla_due_at: string | null; created_at: string; updated_at: string; version?: number; available_actions: string[]; duplicate_of_ticket_id?: string | null; duplicate_master_display_code?: string | null; invalid_reason?: string | null; reassignment_count?: number; auto_assignment_paused?: boolean; auto_assignment_pause_reason?: string | null; active_assignment_id: string | null; active_assignment_status: string | null; active_assignment_source?: string | null; active_technician_id: string | null; active_technician_name: string | null; latest_analysis: CoordinatorAnalysisSummary | null; agent_questions: CoordinatorAgentQuestionSummary[]; attachments: TicketAttachment[]; timeline?: CoordinatorTimelineItem[] };
export type CoordinatorClusterTicket = { id: string; display_code: string; description: string | null; status: string; priority: "P1" | "P2" | "P3" | null; location_label: string | null; unit_code: string | null; floor_label: string | null; created_at: string; active_assignment_id: string | null; active_assignment_status: string | null; active_technician_id: string | null; active_technician_name: string | null };
export type CoordinatorCluster = { id: string; category_id: string; category: string; building_id: string; building: string; floor_label: string; density: number; status: string; closed: boolean; window_start: string; window_end: string; created_at: string; tickets: CoordinatorClusterTicket[] };
export type CoordinatorClusterApproveResult = { case_id: string; approved_ticket_ids: string[]; skipped_ticket_ids: string[] };
export type CoordinatorClusterAssignResult = { case_id: string; technician_id: string; assigned_ticket_ids: string[]; skipped_ticket_ids: string[]; assignment_ids: string[] };
export type TechnicianSummary = { user_id: string; full_name: string | null; phone_e164: string | null; is_active: boolean; is_available: boolean; skill_category_ids: string[] };
export type CoordinatorResidentSummary = { user_id: string; full_name: string | null; phone_e164: string | null; is_active: boolean; unit_id: string | null; unit_code: string | null; building_code: string | null; floor_code: string | null; is_primary: boolean | null };
export type ManagerAccount = { user_id: string; role: "RESIDENT" | "TECHNICIAN" | "COORDINATOR"; full_name: string | null; phone_e164: string | null; email: string | null; temporary_password: string | null; unit_id: string | null; unit_code: string | null; is_active: boolean; is_available: boolean | null; skill_category_ids: string[] };
export type TechnicianAssignment = { id: string; status: "ASSIGNED" | "ACCEPTED" | "IN_PROGRESS" | "COMPLETED" | "REJECTED" | "REASSIGNED" | "UNABLE_TO_HANDLE"; assigned_at: string; accepted_at?: string | null; started_at?: string | null; completed_at?: string | null; ended_at?: string | null; unable_reason?: string | null; reject_reason?: string | null; ticket: { id: string; description: string | null; category_display_name: string | null; location_label: string | null; priority: "P1" | "P2" | "P3" | null; sla_due_at: string | null; attachments: TicketAttachment[] } };
export type TechnicianAvailability = { is_available: boolean };
export type AutoAssignmentDelay = "IMMEDIATE" | "2H" | "5H" | "1D" | "3D";
/** The DIRECT switch. Stoppable at any time; startable only by confirming a
 *  proposal, which is what the three `activated_*` fields record. */
export type AutoAssignmentSettings = { enabled: boolean; activation_delay: AutoAssignmentDelay | string; version: number; updated_at: string; activated_by_batch_id: string | null; activated_by_user_id: string | null; activated_at: string | null };
/** One ticket inside a proposal row. A case row carries up to five. */
export type AssignmentProposalItemMember = {
  ticket_id: string;
  display_code: string | null;
  location_label: string | null;
  category: string | null;
  priority: "P1" | "P2" | "P3" | null;
  /** Submission time and resolution deadline, so the draft board row carries
   *  the same facts as a dashboard row. */
  created_at: string | null;
  sla_due_at: string | null;
};
export type AssignmentProposalItemStatus = "PENDING" | "PROPOSED" | "EMPTY" | "DESELECTED" | "ASSIGNED" | "SKIPPED_MANUAL_WON";
export type AssignmentProposalItem = {
  id: string;
  decision_id: string;
  status: AssignmentProposalItemStatus | string;
  work_item_type: string;
  work_item_id: string;
  ticket_id: string | null;
  ticket_display_code: string | null;
  ticket_description: string | null;
  ticket_location_label: string | null;
  ticket_category: string | null;
  ticket_priority: "P1" | "P2" | "P3" | null;
  /** What the model suggested; kept next to what the coordinator settled on. */
  proposed_technician_id: string | null;
  proposed_technician_name: string | null;
  final_technician_id: string | null;
  final_technician_name: string | null;
  selected_technician_id: string | null;
  selected_technician_name: string | null;
  completed_model: string | null;
  decided_at: string | null;
  ticket_ids: string[];
  /** Every member with its own code/location/priority; never just the first. */
  members: AssignmentProposalItemMember[];
  reason: string | null;
  created_at: string;
  updated_at: string;
};
export type AssignmentProposalBatch = {
  id: string;
  status: string;
  /** Null while the batch is still BUILDING (contract 4.6 item 3). */
  ready_at: string | null;
  expires_at: string | null;
  continue_auto_assignment: boolean | null;
  activation_delay: AutoAssignmentDelay | string | null;
  version: number;
  created_at: string | null;
  confirmed_at: string | null;
  cancelled_at: string | null;
  /** Who confirmed it. Always a coordinator; null until confirmed. */
  confirmed_by_user_id: string | null;
  confirmed_by_name: string | null;
  items: AssignmentProposalItem[];
};
/** How often the system opens a new *draft* proposal for review.
 *
 *  Not `AutoAssignmentDelay`. That one says how long an approved ticket waits
 *  before the system assigns it by itself; this one says how often a new table
 *  is built for a human to confirm. The two are different features with
 *  different backing tables, and conflating them is what the old UI did. */
export type ProposalScheduleInterval = "2_HOURS" | "1_DAY" | "3_DAYS";
/** What the result modal offers. `NONE` is a recorded answer, not an absence. */
export type ProposalScheduleChoice = ProposalScheduleInterval | "NONE";
export type AssignmentSchedule = {
  enabled: boolean;
  /** Null when the schedule is off. */
  interval: ProposalScheduleInterval | null;
  next_run_at: string | null;
  last_run_at: string | null;
  version: number;
  updated_at: string;
};

/** One ticket as it read at confirmation time — never re-read since. */
export type AssignmentHistoryMember = {
  ticket_id: string | null;
  display_code: string | null;
  category: string | null;
  location_label: string | null;
  priority: string | null;
  created_at: string | null;
  sla_due_at: string | null;
};
export type AssignmentHistoryItem = {
  item_id: string | null;
  status: string | null;
  work_item_type: string | null;
  proposed_technician_id: string | null;
  proposed_technician_name: string | null;
  final_technician_id: string | null;
  final_technician_name: string | null;
  /** Frozen: still true after the roster changes underneath it. */
  coordinator_override: boolean;
  reason: string | null;
  members: AssignmentHistoryMember[];
};
export type AssignmentHistoryRecord = {
  batch_id: string;
  confirmed_at: string | null;
  confirmed_by_user_id: string | null;
  confirmed_by_name: string | null;
  /** SYSTEM when the recurring schedule opened the batch. */
  created_by_type: "COORDINATOR" | "SYSTEM" | string;
  ticket_count: number;
  technician_count: number;
  items: AssignmentHistoryItem[];
  /** The repeat chosen after confirming; null when never asked. */
  followup_schedule: ProposalScheduleChoice | null;
  /** False for batches confirmed before snapshots existed. */
  has_snapshot: boolean;
};
export type AssignmentJobStatus = "SCHEDULED_GRACE" | "PRIMARY_RUNNING" | "FALLBACK_RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED_BY_COORDINATOR" | "CANCELLED_MANUAL_WON" | "MANUAL_REQUIRED";
export type AssignmentJobTrigger = "INITIAL_AUTO" | "REASSIGN_REJECTED" | "REASSIGN_SILENT" | "COORDINATOR_PROPOSAL";
/** Read-only view of one AI assignment round (contract 7.4). Sanitized: a code
 *  and a one-line reason, never a prompt, raw model output or a stack trace. */
export type AssignmentJob = {
  id: string;
  mode: "DIRECT" | "PROPOSAL" | string;
  status: AssignmentJobStatus | string;
  trigger: AssignmentJobTrigger | string | null;
  work_item_type: "TICKET" | "INCIDENT_CASE" | string | null;
  work_item_id: string | null;
  ticket_ids: string[];
  execute_after: string | null;
  selected_technician_id: string | null;
  selected_technician_name: string | null;
  completed_model: string | null;
  decision_reason: string | null;
  error_code: string | null;
  created_at: string;
  completed_at: string | null;
  /** Contract 6.2: true only inside the P1/P2 window after a rejection. */
  cancellable: boolean;
};
export type OperationalTimeoutSweep = { resident_question_timeouts: number; technician_acceptance_warnings: number; technician_acceptance_reassignments: number };
export type BackendNotification = { id: string; ticket_id: string | null; notification_type: string; channel: string; title: string; body: string; status: string; created_at: string; sent_at: string | null };
export type CoordinatorCategory = { id: string; code: string; display_name: string; base_score: number | null; priority_ceiling: "P1" | "P2" | "P3" | null; is_active: boolean };
export type BackendAuditLog = { id: number; actor_user_id: string | null; actor_role: string; action: string; entity_type: string; entity_id: string; before_data: Record<string, unknown> | null; after_data: Record<string, unknown> | null; reason: string | null; created_at: string };
export type TicketSummaryReport = { total: number; by_status: Record<string, number>; by_priority: Record<string, number>; by_category: Record<string, number> };
export type SlaPerformanceReport = { completed_total: number; completed_on_time: number; compliance_rate: number | null };
export type TechnicianProductivityRow = { technician_id: string; full_name: string | null; is_active: boolean; active_days: number; completed_tickets: number; sla_late_tickets: number; reassigned_from_other_tickets: number };
export type TechnicianProductivityReport = { period: "week" | "month" | string; period_start: string; period_end: string; rows: TechnicianProductivityRow[] };
