import { apiRequest, uploadToSignedUrl } from "@/api/client";
import type { AssignmentHistoryRecord, AssignmentJob, AssignmentProposalBatch, AssignmentSchedule, AttachmentDownload, AutoAssignmentDelay, AutoAssignmentSettings, BackendAuditLog, BackendNotification, CoordinatorCategory, CoordinatorCluster, CoordinatorClusterApproveResult, CoordinatorClusterAssignResult, CoordinatorResidentSummary, CoordinatorTicket, CreatedTicket, LocationItem, ManagerAccount, OperationalTimeoutSweep, ProposalScheduleInterval, ResidentAgentQuestion, ResidentCategory, ResidentLifecycleGroup, ResidentTicket, SignedUpload, SlaPerformanceReport, TechnicianAssignment, TechnicianAvailability, TechnicianProductivityReport, TechnicianSummary, TicketAttachment, TicketList, TicketSummaryReport } from "@/types/api";
import type { TicketImage } from "@/lib/types";
import type { TicketSeverity } from "@/lib/severity";
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
export const getTechnicianAvailability = () => apiRequest<TechnicianAvailability>("/technician/availability", { role: "technician", fresh: true });
export const updateTechnicianAvailability = (isAvailable: boolean) => apiRequest<TechnicianAvailability>("/technician/availability", { role: "technician", method: "PATCH", body: JSON.stringify({ is_available: isAvailable }), timeoutMs: 20_000 });
export const listNotifications = (role: "resident" | "manager" | "technician", fresh = false) => apiRequest<BackendNotification[]>("/notifications?limit=200", { role, fresh });
export const markBackendNotificationRead = (role: "resident" | "manager" | "technician", id: string) => apiRequest<BackendNotification>(`/notifications/${id}/read`, { role, method: "POST" });
export const getResidentTicket = (id: string, fresh = false) => apiRequest<ResidentTicket>(`/tickets/${id}`, { role: "resident", fresh });
export const getResidentAgentQuestion = (id: string, fresh = false) => apiRequest<ResidentAgentQuestion | null>(`/tickets/${id}/agent-question`, { role: "resident", fresh });
export const cancelResidentBackendTicket = (id: string) => apiRequest<ResidentTicket>(`/tickets/${id}/cancel`, { role: "resident", method: "POST" });
export const getCoordinatorTicket = (id: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}`, { role: "manager" });
export const approveCoordinatorTicket = (id: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/approve`, { role: "manager", method: "POST" });
// `severity` is sent only when the report never got one from the analysis: the
// backend keeps a stored severity and rejects a manual review without one when
// there is nothing to keep. The key is omitted rather than sent as null.
export const resolveCoordinatorManualReview = (id: string, categoryId: string, resolutionSource: "IMAGE" | "TEXT" | "OTHER", reason: string, severity?: TicketSeverity | null) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/manual-review/resolve`, { role: "manager", method: "POST", body: JSON.stringify({ category_id: categoryId, resolution_source: resolutionSource, reason, ...(severity ? { severity } : {}) }) });
export const rejectCoordinatorManualReview = (id: string, reason: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/manual-review/reject`, { role: "manager", method: "POST", body: JSON.stringify({ reason }) });
export const overrideCoordinatorClassification = (id: string, categoryId: string, priority: string, reason: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/classification`, { role: "manager", method: "PATCH", body: JSON.stringify({ category_id: categoryId, priority, reason }) });
export const linkCoordinatorDuplicateTicket = (id: string, masterTicketId: string, reason: string) => apiRequest<CoordinatorTicket>(`/coordinator/tickets/${id}/duplicate-link`, { role: "manager", method: "POST", body: JSON.stringify({ master_ticket_id: masterTicketId, reason }), timeoutMs: 20_000 });
/** The DIRECT switch: does Backend assign approved tickets on its own? */
export const getAutoAssignmentSettings = () => apiRequest<AutoAssignmentSettings>("/coordinator/auto-assignment-settings", { role: "manager", fresh: true });
/** Stop DIRECT. There is deliberately no counterpart that starts it.
 *
 *  `enabled: false` is hard-coded rather than taken as an argument, so this
 *  module contains no way to ask for activation at all — the backend refuses
 *  the transition anyway, but a client function that could *form* the request
 *  is a loaded gun sitting next to a rule about not firing it. DIRECT starts
 *  only as a consequence of `confirmCoordinatorAssignmentProposal` succeeding. */
export const disableDirectAutoAssignment = (activationDelay: AutoAssignmentDelay = "IMMEDIATE") => apiRequest<AutoAssignmentSettings>("/coordinator/auto-assignment-settings", { role: "manager", method: "PATCH", body: JSON.stringify({ enabled: false, activation_delay: activationDelay }), timeoutMs: 20_000 });
export const listCoordinatorAssignmentProposals = () => apiRequest<AssignmentProposalBatch[]>("/coordinator/assignment-proposals?limit=20", { role: "manager", fresh: true });
export const createCoordinatorAssignmentProposal = (limit = 20) => apiRequest<AssignmentProposalBatch>("/coordinator/assignment-proposals", { role: "manager", method: "POST", body: JSON.stringify({ limit }), timeoutMs: 30_000 });
export const getCoordinatorAssignmentProposal = (id: string) => apiRequest<AssignmentProposalBatch>(`/coordinator/assignment-proposals/${id}`, { role: "manager", fresh: true });
/** Contract 4.6 item 5: `expectedVersion` is sent whenever the coordinator edited
 *  the table, so a stale view is rejected instead of silently confirmed. */
/** Assign the placed rows. If DIRECT was off and this hands real work out, the
 *  backend turns it on in the same transaction — there is no flag here asking
 *  for that, because the decision is not the client's to make. */
export const confirmCoordinatorAssignmentProposal = (id: string, expectedVersion?: number) => apiRequest<AssignmentProposalBatch>(`/coordinator/assignment-proposals/${id}/confirm`, { role: "manager", method: "POST", body: JSON.stringify(expectedVersion == null ? {} : { expected_version: expectedVersion }), timeoutMs: 30_000 });
/** Contract 4.6 item 4: drop a row, or put a different technician on it. */
export const updateCoordinatorAssignmentProposalItem = (batchId: string, itemId: string, change: { selected?: boolean; technician_id?: string }) => apiRequest<AssignmentProposalBatch>(`/coordinator/assignment-proposals/${batchId}/items/${itemId}`, { role: "manager", method: "PATCH", body: JSON.stringify(change), timeoutMs: 20_000 });
export const cancelCoordinatorAssignmentProposal = (id: string, reason?: string) => apiRequest<AssignmentProposalBatch>(`/coordinator/assignment-proposals/${id}/cancel`, { role: "manager", method: "POST", body: JSON.stringify({ reason: reason || "BQL hủy bảng đề xuất." }), timeoutMs: 20_000 });
/** Contract 7.4: the read-only job view the assignment queues are built from.
 *  Passing several statuses keeps the three live states to one request. */
/** The recurring *draft* schedule: how often a new proposal table is built for
 *  review. Distinct from `getAutoAssignmentSettings`, which is the switch that
 *  assigns approved tickets without a human. Different endpoint, different
 *  table, different meaning. */
export const getAssignmentSchedule = () => apiRequest<AssignmentSchedule>("/coordinator/assignment-schedule", { role: "manager", fresh: true });
export const updateAssignmentSchedule = (interval: ProposalScheduleInterval | null, options: { expectedVersion?: number; afterBatchId?: string } = {}) => apiRequest<AssignmentSchedule>("/coordinator/assignment-schedule", { role: "manager", method: "PATCH", timeoutMs: 20_000, body: JSON.stringify({ enabled: interval !== null, interval, ...(options.expectedVersion == null ? {} : { expected_version: options.expectedVersion }), ...(options.afterBatchId ? { after_batch_id: options.afterBatchId } : {}) }) });
/** Confirmed rounds, rendered from the snapshot frozen at confirmation. */
export const listAssignmentHistory = (limit = 50) => apiRequest<AssignmentHistoryRecord[]>(`/coordinator/assignment-history?limit=${limit}`, { role: "manager", fresh: true });
export const listCoordinatorAssignmentJobs = (statuses: string[] = [], limit = 50) => apiRequest<AssignmentJob[]>(`/coordinator/assignment-jobs?limit=${limit}${statuses.length ? `&status=${encodeURIComponent(statuses.join(","))}` : ""}`, { role: "manager", fresh: true });
/** Contract 6.2: only a DIRECT job that a rejection put into the five-minute
 *  grace window may be cancelled; the backend refuses anything else. */
export const cancelCoordinatorAssignmentJob = (jobId: string) => apiRequest<AssignmentJob>(`/coordinator/assignment-jobs/${jobId}/cancel`, { role: "manager", method: "POST", timeoutMs: 20_000 });
export const runCoordinatorOperationalTimeouts = () => apiRequest<OperationalTimeoutSweep>("/coordinator/operational-timeouts/run", { role: "manager", method: "POST", timeoutMs: 20_000 });
export const listCoordinatorTechnicians = () => apiRequest<TechnicianSummary[]>("/coordinator/technicians", { role: "manager" });
export const createCoordinatorTechnician = (data: { full_name: string; phone_number?: string | null; skill_category_ids: string[]; is_available?: boolean }) => apiRequest<ManagerAccount>("/coordinator/accounts/technicians", { role: "manager", method: "POST", body: JSON.stringify(data), timeoutMs: 20_000 });
export const resetCoordinatorTechnicianPassword = (id: string) => apiRequest<ManagerAccount>(`/coordinator/accounts/technicians/${id}/reset-password`, { role: "manager", method: "POST", timeoutMs: 20_000 });
export const deleteCoordinatorTechnician = (id: string) => apiRequest<ManagerAccount>(`/coordinator/accounts/technicians/${id}`, { role: "manager", method: "DELETE", timeoutMs: 20_000 });
export const listCoordinatorResidents = () => apiRequest<CoordinatorResidentSummary[]>("/coordinator/accounts/residents", { role: "manager" });
export const createCoordinatorResident = (data: { full_name: string; phone?: string | null; unit_code: string; building_code?: string | null; is_primary?: boolean }) => apiRequest<ManagerAccount>("/coordinator/accounts/residents", { role: "manager", method: "POST", body: JSON.stringify(data), timeoutMs: 20_000 });
export const resetCoordinatorResidentPassword = (id: string) => apiRequest<ManagerAccount>(`/coordinator/accounts/residents/${id}/reset-password`, { role: "manager", method: "POST", timeoutMs: 20_000 });
export const setCoordinatorResidentActive = (id: string, isActive: boolean) => apiRequest<ManagerAccount>(`/coordinator/accounts/residents/${id}/status`, { role: "manager", method: "PATCH", body: JSON.stringify({ is_active: isActive }), timeoutMs: 20_000 });
export const listBackendAuditLogs = () => apiRequest<BackendAuditLog[]>("/coordinator/audit-logs?limit=500", { role: "manager" });
export const listBackendCategories = () => apiRequest<CoordinatorCategory[]>("/coordinator/categories", { role: "manager" });
export const createBackendCategory = (code: string, displayName: string, priorityCeiling: string | null) => apiRequest<CoordinatorCategory>("/coordinator/categories", { role: "manager", method: "POST", body: JSON.stringify({ code, display_name: displayName, base_score: 0, priority_ceiling: priorityCeiling }) });
export const updateBackendCategory = (id: string, data: Partial<{ display_name: string; base_score: number; priority_ceiling: string | null; is_active: boolean }>) => apiRequest<CoordinatorCategory>(`/coordinator/categories/${id}`, { role: "manager", method: "PATCH", body: JSON.stringify(data) });
export const getTicketSummaryReport = () => apiRequest<TicketSummaryReport>("/coordinator/reports/tickets-summary", { role: "manager" });
export const getSlaPerformanceReport = () => apiRequest<SlaPerformanceReport>("/coordinator/reports/sla-performance", { role: "manager" });
export const getTechnicianProductivityReport = (period: "week" | "month") => apiRequest<TechnicianProductivityReport>(`/coordinator/reports/technician-productivity?period=${period}`, { role: "manager", fresh: true });
export const assignCoordinatorTicket = (id: string, technicianId: string) => apiRequest<{ assignment_id: string; status: string }>(`/coordinator/tickets/${id}/assign`, { role: "manager", method: "POST", body: JSON.stringify({ technician_id: technicianId }), timeoutMs: 20_000 });
export const getTechnicianAssignment = (id: string) => apiRequest<TechnicianAssignment>(`/technician/assignments/${id}`, { role: "technician" });
export const acceptTechnicianAssignment = (id: string) => apiRequest<TechnicianAssignment>(`/technician/assignments/${id}/accept`, { role: "technician", method: "POST" });
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
export async function answerResidentAgentQuestion(ticketId: string, questionId: string, answer: { option?: string; text?: string; image?: TicketImage }) {
  const uploadId = answer.image ? await uploadImage(answer.image) : undefined;
  const answerType = uploadId ? "NEW_PHOTO" : answer.option ? "OPTION" : "FREE_TEXT";
  return apiRequest<ResidentAgentQuestion>(`/tickets/${ticketId}/agent-question/${questionId}/answer`, {
    role: "resident",
    method: "POST",
    body: JSON.stringify({
      answer_type: answerType,
      answer_text: answer.option || answer.text || null,
      upload_id: uploadId || null,
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
