import { apiRequest, uploadToSignedUrl } from "@/api/client";
import type { SimulationRun, SimulationRunRequest, AtRiskDecision, AttachmentDownload, AutoAssignmentToggle, BackendAuditLog, BackendNotification, CoordinatorCategory, CoordinatorCluster, CoordinatorClusterApproveResult, CoordinatorClusterAssignResult, CoordinatorResidentSummary, CoordinatorTicket, CreatedTicket, DispatchEvent, DispatchWorkerRun, LocationItem, ManagerAccount, OperationalTimeoutSweep, EmergencyDecision, RiskCriteriaInput, ResidentAgentQuestion, ResidentCategory, ResidentLifecycleGroup, ResidentTicket, SignedUpload, SlaPerformanceReport, TechnicianAssignment, TechnicianAvailability, TechnicianProductivityReport, TechnicianQueue, TechnicianSummary, TicketAttachment, TicketList, TicketPriority, TicketSummaryReport, VisualBoard, VisualConfirmResult, VisualPlacement } from "@/types/api";
import type { TicketImage } from "@/lib/types";
export const listLocations = () => apiRequest<LocationItem[]>("/catalog/locations", { role: "resident" });
export const listManagerLocations = () => apiRequest<LocationItem[]>("/catalog/locations", { role: "manager" });
export type ResidentTicketQuery = {
  page?: number;
  pageSize?: number;
  statusGroup?: ResidentLifecycleGroup | null;
  categoryId?: string | null;
  /** Date-only "YYYY-MM-DD"; the backend widens it to the whole Vietnam day. */
  from?: string | null;
  to?: string | null;
  /** Matches the visible report code or the description, in the database. */
  search?: string | null;
};

/** One page of the apartment's reports. Filtering, counting and sorting all
 *  happen in the backend, so `total` describes the filtered set. */
export function listResidentTickets(query: ResidentTicketQuery = {}) {
  const params = new URLSearchParams({
    page: String(query.page ?? 1),
    page_size: String(query.pageSize ?? 20),
  });
  if (query.statusGroup) params.set("status_group", query.statusGroup);
  if (query.categoryId) params.set("category_id", query.categoryId);
  if (query.from) params.set("from", query.from);
  if (query.to) params.set("to", query.to);
  if (query.search) params.set("search", query.search);
  return apiRequest<TicketList<ResidentTicket>>(`/tickets?${params.toString()}`, { role: "resident" });
}

export const listResidentCategories = () => apiRequest<ResidentCategory[]>("/catalog/categories", { role: "resident" });
export const listCoordinatorTickets = () => apiRequest<TicketList<CoordinatorTicket>>("/coordinator/tickets?page=1&page_size=100", { role: "manager" });
export const listCoordinatorClusters = () => apiRequest<CoordinatorCluster[]>("/coordinator/clusters", { role: "manager", fresh: true });
export const approveCoordinatorCluster = (id: string) => apiRequest<CoordinatorClusterApproveResult>(`/coordinator/clusters/${id}/approve`, { role: "manager", method: "POST", timeoutMs: 30_000 });
export const assignCoordinatorCluster = (id: string, technicianId: string) => apiRequest<CoordinatorClusterAssignResult>(`/coordinator/clusters/${id}/assign`, { role: "manager", method: "POST", body: JSON.stringify({ technician_id: technicianId }), timeoutMs: 30_000 });
export const removeCoordinatorClusterTicket = (id: string, ticketId: string) => apiRequest<CoordinatorCluster>(`/coordinator/clusters/${id}/tickets/${ticketId}`, { role: "manager", method: "DELETE" });
export const listTechnicianAssignments = () => apiRequest<TechnicianAssignment[]>("/technician/assignments", { role: "technician" });
/** §4: the ordered work queue. Item 0 is "Làm ngay", item 1 is "Tiếp theo". */
export const getTechnicianQueue = () => apiRequest<TechnicianQueue>("/technician/queue", { role: "technician", fresh: true });
export const getTechnicianAvailability = () => apiRequest<TechnicianAvailability>("/technician/availability", { role: "technician", fresh: true });
export const updateTechnicianAvailability = (isAvailable: boolean) => apiRequest<TechnicianAvailability>("/technician/availability", { role: "technician", method: "PATCH", body: JSON.stringify({ is_available: isAvailable }), timeoutMs: 20_000 });
export const listNotifications = (role: "resident" | "manager" | "technician", fresh = false) => apiRequest<BackendNotification[]>("/notifications?limit=200", { role, fresh });
export const markBackendNotificationRead = (role: "resident" | "manager" | "technician", id: string) => apiRequest<BackendNotification>(`/notifications/${id}/read`, { role, method: "POST" });
export const getResidentTicket = (id: string, fresh = false) => apiRequest<ResidentTicket>(`/tickets/${id}`, { role: "resident", fresh });
export const getResidentAgentQuestion = (id: string, fresh = false) => apiRequest<ResidentAgentQuestion | null>(`/tickets/${id}/agent-question`, { role: "resident", fresh });
export const cancelResidentBackendTicket = (id: string) => apiRequest<ResidentTicket>(`/tickets/${id}/cancel`, { role: "resident", method: "POST" });
export const getCoordinatorTicket = (id: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}`, { role: "manager" });
export const approveCoordinatorTicket = (id: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/approve`, { role: "manager", method: "POST" });
// `criteria` is sent only when the report was never scored: the backend keeps a
// stored assessment and rejects a manual review without one when
// there is nothing to keep. The key is omitted rather than sent as null.
export const resolveCoordinatorManualReview = (id: string, categoryId: string, resolutionSource: "IMAGE" | "TEXT" | "OTHER", reason: string, criteria?: RiskCriteriaInput | null, blockers: string[] = []) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/manual-review/resolve`, { role: "manager", method: "POST", body: JSON.stringify({ category_id: categoryId, resolution_source: resolutionSource, reason, ...(criteria ? { criteria, blockers } : {}) }) });
export const rejectCoordinatorManualReview = (id: string, reason: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/manual-review/reject`, { role: "manager", method: "POST", body: JSON.stringify({ reason }) });
export const overrideCoordinatorClassification = (id: string, categoryId: string, priority: string, reason: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/classification`, { role: "manager", method: "PATCH", body: JSON.stringify({ category_id: categoryId, priority, reason }) });
export const linkCoordinatorDuplicateTicket = (id: string, masterTicketId: string, reason: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/duplicate-link`, { role: "manager", method: "POST", body: JSON.stringify({ master_ticket_id: masterTicketId, reason }), timeoutMs: 20_000 });
/** Settle a report the agent flagged as an uncertain duplicate. Confirming a
 *  duplicate links it and stops there; confirming it is independent publishes the
 *  report and lets the backend start looking for a spreading case. */
export const decideCoordinatorDuplicate = (id: string, isDuplicate: boolean, reason: string, masterTicketId?: string | null) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/duplicate-decision`, { role: "manager", method: "POST", body: JSON.stringify({ is_duplicate: isDuplicate, reason, ...(masterTicketId ? { master_ticket_id: masterTicketId } : {}) }), timeoutMs: 20_000 });
/** Re-run an analysis that stopped on a technical error rather than a verdict. */
/** The emergency gate. Confirming keeps P5 and deliberately stops the
 *  automation; downgrading needs a target below P5 and a written reason, and
 *  is what lets the pipeline continue into duplicate handling. */
export const reviewCoordinatorEmergency = (id: string, decision: EmergencyDecision, priority?: TicketPriority, reason = "") => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/emergency-review`, { role: "manager", method: "POST", body: JSON.stringify({ decision, reason, ...(decision === "DOWNGRADE_PRIORITY" && priority ? { priority } : {}) }), timeoutMs: 20_000 });

export const retryCoordinatorAnalysis = (id: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/analysis/retry`, { role: "manager", method: "POST", timeoutMs: 20_000 });
/** §2: the Automatic Assignment switch. */
export const getAutoAssignmentToggle = () => apiRequest<AutoAssignmentToggle>("/coordinator/auto-assignment", { role: "manager", fresh: true });

/** Turn Automatic Assignment on or off.
 *
 *  `acknowledged` is required by the backend to enable it, and corresponds to
 *  the manager having read the confirmation modal. It is a parameter rather
 *  than a hard-coded `true` on purpose: a client that could set it without
 *  showing the modal would be defeating the rule it exists to enforce, so the
 *  only caller that passes `true` is the modal's confirm button.
 *
 *  Turning it **off** always succeeds and never undoes an existing assignment. */
export const setAutoAssignment = (enabled: boolean, options: { acknowledged?: boolean; expectedVersion?: number } = {}) =>
  apiRequest<AutoAssignmentToggle>("/coordinator/auto-assignment", {
    role: "manager",
    method: "PUT",
    timeoutMs: 30_000,
    body: JSON.stringify({
      enabled,
      acknowledged: options.acknowledged ?? false,
      ...(options.expectedVersion == null ? {} : { expected_version: options.expectedVersion }),
    }),
  });

/** §1: everything the Visual Assignment board needs, in one request.
 *
 *  Deliberately one call rather than one per unit: the board has to know what a
 *  drop would do *before* it happens, and a per-drop round trip would either be
 *  slow or let a manager drop first and find out afterwards. */
export const getVisualAssignmentBoard = (limit = 100) =>
  apiRequest<VisualBoard>(`/coordinator/visual-assignment/board?limit=${limit}`, { role: "manager", fresh: true });

/** §1: all manual placements, confirmed in one action and one transaction.
 *
 *  Carries unit and technician ids only. Planned times and warnings are absent
 *  because none of them is the client's to assert — the server recomputes them
 *  under lock, and a field a client could lie about is a field it must not read.
 *
 *  A rejection is a 409 whose `details.failures` names the offending
 *  placements; nothing at all is written. */
export const confirmVisualAssignment = (placements: VisualPlacement[]) =>
  apiRequest<VisualConfirmResult>("/coordinator/visual-assignment/confirm", {
    role: "manager",
    method: "POST",
    body: JSON.stringify({ placements }),
    timeoutMs: 30_000,
  });

/** The automatic queue and what happened to it (§10). */
export const listDispatchEvents = (status?: string, limit = 50) =>
  apiRequest<DispatchEvent[]>(
    `/coordinator/dispatch/events?limit=${limit}${status ? `&status=${encodeURIComponent(status)}` : ""}`,
    { role: "manager", fresh: true },
  );

/** §7: the subset where a trade-off was made. This is the list a manager
 *  actually reviews, which is why it is its own endpoint rather than a filter. */
export const listAtRiskDecisions = (limit = 50) =>
  apiRequest<AtRiskDecision[]>(`/coordinator/dispatch/at-risk-decisions?limit=${limit}`, { role: "manager", fresh: true });

/** Operations only. In a real deployment `python -m src.workers.dispatch_worker`
 *  runs this continuously. */
export const runDispatchOnce = () =>
  apiRequest<DispatchWorkerRun>("/coordinator/dispatch/run-once", { role: "manager", method: "POST", timeoutMs: 30_000 });

export const runCoordinatorOperationalTimeouts = () => apiRequest<OperationalTimeoutSweep>("/coordinator/operational-timeouts/run", { role: "manager", method: "POST", timeoutMs: 20_000 });
export const listCoordinatorTechnicians = () => apiRequest<TechnicianSummary[]>("/coordinator/technicians", { role: "manager" });
export const createCoordinatorTechnician = (data: { full_name: string; phone_number?: string | null; skill_category_ids: string[]; is_available?: boolean }) => apiRequest<ManagerAccount>("/coordinator/accounts/technicians", { role: "manager", method: "POST", body: JSON.stringify(data), timeoutMs: 20_000 });
export const resetCoordinatorTechnicianPassword = (id: string) => apiRequest<ManagerAccount>(`/coordinator/accounts/technicians/${id}/reset-password`, { role: "manager", method: "POST", timeoutMs: 20_000 });
export const deleteCoordinatorTechnician = (id: string) => apiRequest<ManagerAccount>(`/coordinator/accounts/technicians/${id}`, { role: "manager", method: "DELETE", timeoutMs: 20_000 });
export const listCoordinatorResidents = () => apiRequest<CoordinatorResidentSummary[]>("/coordinator/accounts/residents", { role: "manager" });
export const createCoordinatorResident = (data: { full_name: string; phone?: string | null; unit_code: string; is_primary?: boolean }) => apiRequest<ManagerAccount>("/coordinator/accounts/residents", { role: "manager", method: "POST", body: JSON.stringify(data), timeoutMs: 20_000 });
export const resetCoordinatorResidentPassword = (id: string) => apiRequest<ManagerAccount>(`/coordinator/accounts/residents/${id}/reset-password`, { role: "manager", method: "POST", timeoutMs: 20_000 });
export const setCoordinatorResidentActive = (id: string, isActive: boolean) => apiRequest<ManagerAccount>(`/coordinator/accounts/residents/${id}/status`, { role: "manager", method: "PATCH", body: JSON.stringify({ is_active: isActive }), timeoutMs: 20_000 });
export const listBackendAuditLogs = () => apiRequest<BackendAuditLog[]>("/coordinator/audit-logs?limit=500", { role: "manager" });
export const listBackendCategories = () => apiRequest<CoordinatorCategory[]>("/coordinator/categories", { role: "manager" });
// A code and a name. A category carries no base score and no priority ceiling
// under the v2 rubric -- it takes no part in scoring at all.
export const createBackendCategory = (code: string, displayName: string) => apiRequest<CoordinatorCategory>("/coordinator/categories", { role: "manager", method: "POST", body: JSON.stringify({ code, display_name: displayName }) });
export const updateBackendCategory = (id: string, data: Partial<{ display_name: string; is_active: boolean }>) => apiRequest<CoordinatorCategory>(`/coordinator/categories/${id}`, { role: "manager", method: "PATCH", body: JSON.stringify(data) });
export const getTicketSummaryReport = () => apiRequest<TicketSummaryReport>("/coordinator/reports/tickets-summary", { role: "manager" });
export const getSlaPerformanceReport = () => apiRequest<SlaPerformanceReport>("/coordinator/reports/sla-performance", { role: "manager" });
export const getTechnicianProductivityReport = (period: "week" | "month") => apiRequest<TechnicianProductivityReport>(`/coordinator/reports/technician-productivity?period=${period}`, { role: "manager", fresh: true });
export const assignCoordinatorTicket = (id: string, technicianId: string) => apiRequest<{ assignment_id: string; status: string }>(`/coordinator/tickets/${id}/assign`, { role: "manager", method: "POST", body: JSON.stringify({ technician_id: technicianId }), timeoutMs: 20_000 });
export const getTechnicianAssignment = (id: string) => apiRequest<TechnicianAssignment>(`/technician/assignments/${id}`, { role: "technician" });
export const startTechnicianAssignment = (id: string) => apiRequest<TechnicianAssignment>(`/technician/assignments/${id}/start`, { role: "technician", method: "POST" });
export const unableTechnicianAssignment = (id: string, reason: string) => apiRequest<TechnicianAssignment>(`/technician/assignments/${id}/unable-to-handle`, { role: "technician", method: "POST", body: JSON.stringify({ reason }) });
export const rejectTechnicianAssignment = (id: string, reason: string) => apiRequest<TechnicianAssignment>(`/technician/assignments/${id}/reject`, { role: "technician", method: "POST", body: JSON.stringify({ reason }) });
export const completeTechnicianAssignment = (id: string, resolutionNote: string, evidenceUploadIds: string[]) => apiRequest<TechnicianAssignment>(`/technician/assignments/${id}/complete`, { role: "technician", method: "POST", body: JSON.stringify({ resolution_note: resolutionNote, evidence_upload_ids: evidenceUploadIds }) });

type CachedAttachmentImage = { image: TicketImage; expiresAt: number };
const attachmentImageCache = new Map<string, CachedAttachmentImage>();
const attachmentImageRequests = new Map<string, Promise<TicketImage>>();
const attachmentImageStoragePrefix = "attachment-image:";

function readCachedAttachmentImage(key: string) {
  const memoryCached = attachmentImageCache.get(key);
  if (memoryCached?.expiresAt && memoryCached.expiresAt > Date.now()) return memoryCached.image;
  if (memoryCached) attachmentImageCache.delete(key);
  if (typeof window === "undefined") return undefined;
  try {
    const value = window.sessionStorage.getItem(`${attachmentImageStoragePrefix}${key}`);
    if (!value) return undefined;
    const cached = JSON.parse(value) as CachedAttachmentImage;
    if (cached.expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(`${attachmentImageStoragePrefix}${key}`);
      return undefined;
    }
    attachmentImageCache.set(key, cached);
    return cached.image;
  } catch {
    return undefined;
  }
}

function cacheAttachmentImage(key: string, image: TicketImage, expiresIn: number) {
  const lifetimeMs = Math.max(1_000, expiresIn * 1_000);
  const safetyMarginMs = Math.min(60_000, Math.max(5_000, lifetimeMs * 0.1));
  const cached = { image, expiresAt: Date.now() + Math.max(1_000, lifetimeMs - safetyMarginMs) };
  attachmentImageCache.set(key, cached);
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(`${attachmentImageStoragePrefix}${key}`, JSON.stringify(cached));
  } catch {
    // Memory cache is enough when session storage is unavailable.
  }
}

export async function loadAttachmentImages(attachments: TicketAttachment[], role: "resident" | "manager" | "technician") {
  return Promise.all(attachments.map(async (attachment) => {
    const cacheKey = `${role}:${attachment.id}`;
    const cached = readCachedAttachmentImage(cacheKey);
    if (cached) return cached;
    const pending = attachmentImageRequests.get(cacheKey);
    if (pending) return pending;
    const endpoint = attachment.download_url_endpoint.replace(/^\/api\/v1/, "");
    const request = apiRequest<AttachmentDownload>(endpoint, { role }).then((download) => {
      const image = { name: attachment.id, dataUrl: download.signed_download_url, size: attachment.size_bytes || undefined };
      cacheAttachmentImage(cacheKey, image, download.expires_in);
      return image;
    }).finally(() => attachmentImageRequests.delete(cacheKey));
    attachmentImageRequests.set(cacheKey, request);
    return request;
  }));
}
/** Requests a private upload target and uploads one photo, returning its upload id. */
export async function uploadImage(image: TicketImage) {
  const blob = await (await fetch(image.dataUrl)).blob();
  const upload = await apiRequest<SignedUpload>("/storage/ticket-attachments/upload-url", { role: "resident", method: "POST", body: JSON.stringify({ original_filename: image.name, mime_type: blob.type || "image/jpeg", file_size: blob.size }) });
  if (!upload.signed_upload_url) throw new Error("Backend không trả về URL tải ảnh.");
  await uploadToSignedUrl(blob, upload.signed_upload_url, upload.required_headers);
  return upload.upload_id;
}
/** `locationId` is only ever sent for a LOCATION_CONFIRMATION where the resident
 *  picked a different place. It is an id from the fixed selector, never a name
 *  typed into `text`: the backend validates the id and moves the report, and it
 *  never infers a location from prose. */
export async function answerResidentAgentQuestion(ticketId: string, questionId: string, answer: { option?: string; text?: string; image?: TicketImage; locationId?: string }) {
  const uploadId = answer.image ? await uploadImage(answer.image) : undefined;
  const answerType = uploadId ? "NEW_PHOTO" : answer.option ? "OPTION" : "FREE_TEXT";
  return apiRequest<ResidentAgentQuestion>(`/tickets/${ticketId}/agent-question/${questionId}/answer`, {
    role: "resident",
    method: "POST",
    body: JSON.stringify({
      answer_type: answerType,
      answer_text: answer.option || answer.text || null,
      upload_id: uploadId || null,
      selected_location_id: answer.locationId || null,
    }),
    timeoutMs: 20_000,
  });
}
export async function uploadCompletionImage(image: TicketImage) {
  const blob = await (await fetch(image.dataUrl)).blob();
  const upload = await apiRequest<SignedUpload>("/storage/completion-evidence/upload-url", { role: "technician", method: "POST", body: JSON.stringify({ original_filename: image.name, mime_type: blob.type || "image/jpeg", file_size: blob.size }) });
  if (!upload.signed_upload_url) throw new Error("Backend không trả về URL tải ảnh.");
  await uploadToSignedUrl(blob, upload.signed_upload_url, upload.required_headers);
  return upload.upload_id;
}
/** Photos are uploaded before submit so a failed upload never costs the draft. */
export function createResidentTicket(locationId: string, description: string, attachmentUploadIds: string[]) {
  return apiRequest<CreatedTicket>("/tickets", { role: "resident", method: "POST", body: JSON.stringify({ location_id: locationId, description, attachment_upload_ids: attachmentUploadIds }), timeoutMs: 20_000 });
}

/** Run the capacity & SLA simulation over one pasted scenario document.
 *
 *  Read-only on the backend: it replays the scenario in memory and creates no
 *  ticket, assignment or dispatch event. Returns three results — the manual
 *  baseline, production as planned by production's own scheduler, and the
 *  proposed optimisation — plus the differences.
 *
 *  The timeout is generous because the run is synchronous and a 500-report
 *  scenario is a real amount of arithmetic; there is nothing to poll
 *  afterwards, the result is the response.
 */
export const runCapacitySimulation = (scenario: Record<string, unknown>) => apiRequest<SimulationRun>("/coordinator/simulation/run", { role: "manager", method: "POST", body: JSON.stringify({ scenario } satisfies SimulationRunRequest), timeoutMs: 60_000 });
