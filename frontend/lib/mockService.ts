"use client";

import type {
  AppNotification,
  AuditLogEntry,
  Category,
  CategoryConfig,
  CreateTicketPayload,
  CreateTicketResult,
  Priority,
  StatusUpdatePayload,
  Technician,
  TechnicianUpdatePayload,
  Ticket,
  TicketCluster,
  TicketFilters,
  TicketImage,
  TicketStatus,
  TimelineEntry,
  UserRole,
} from "./types";

const STORE_KEY = "apartment-triage-mvp-v3";
const STORE_EVENT = "triage-store-change";
const DEMO_IMAGE = "/demo/incident-triptych.png";

type Store = {
  version: 5;
  revision: number;
  tickets: Ticket[];
  audit: AuditLogEntry[];
  notifications: AppNotification[];
  technicians: Technician[];
  categoryConfigs: CategoryConfig[];
};

export const categories: Category[] = [
  "Rò nước", "Chập điện", "Thang máy", "An ninh nghiêm trọng",
  "Hỏng khóa / cửa", "Điều hòa / thông gió", "Mất điện cục bộ",
  "Kết cấu", "Hỏng đèn", "Mùi hôi / vệ sinh", "Tiếng ồn",
];

export const priorities: Priority[] = ["P5", "P4", "P3", "P2", "P1", "P0"];

export const statusLabels: Record<TicketStatus, string> = {
  new: "Mới", analyzing: "Đang phân tích", awaiting_response: "Đang chờ bạn trả lời", needs_info: "Cần bổ sung",
  p0_review: "Chờ duyệt", approved: "Đã duyệt", assigned: "Đã gán kỹ thuật viên", in_progress: "Đang xử lý",
  completed: "Hoàn thành", cannot_resolve: "Không xử lý được", invalid: "Không hợp lệ", cancelled: "Đã hủy",
};

export const priorityLabels: Record<Priority, string> = {
  P0: "Cần BQL duyệt",
  P1: "Bình thường",
  P2: "Theo lịch",
  P3: "Cần sớm",
  P4: "Trong ca",
  P5: "Rất khẩn cấp",
};

/** What a resident is told, in hours of the working day rather than in the
 *  service minutes the backend counts. `docs/risk_scoring_v2.md` §6.1 gives
 *  1800/1200/600/180 service minutes over a ten-hour window; these are the same
 *  promises said in a way somebody waiting can act on. */
export const slaLabels: Record<Priority, string> = {
  P0: "Chờ BQL xác nhận thủ công",
  P1: "Dự kiến xử lý trong 3 ngày làm việc",
  P2: "Dự kiến xử lý trong 2 ngày làm việc",
  P3: "Dự kiến xử lý trong 1 ngày làm việc",
  P4: "Dự kiến xử lý trong 3 giờ làm việc",
  P5: "Ban quản lý đang xử lý ngay",
};

const now = () => new Date().toISOString();
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const uid = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 7)}`;
const addMinutes = (iso: string, minutes: number) => new Date(Date.parse(iso) + minutes * 60_000).toISOString();
const mockSlaMinutes: Partial<Record<Priority, number>> = { P1: 1800, P2: 1200, P3: 600, P4: 180, P5: 5 };
const mockPriorityScores: Partial<Record<Priority, number>> = { P1: 10, P2: 30, P3: 50, P4: 70, P5: 90 };
const dueAt = (priority: Priority, createdAt: string) => {
  const minutes = mockSlaMinutes[priority];
  return minutes == null ? undefined : addMinutes(createdAt, minutes);
};
const scoreForPriority = (priority: Priority) => mockPriorityScores[priority] ?? null;
const timeline = (status: TicketStatus, label: string, actor: string, createdAt: string): TimelineEntry => ({ id: uid("TL"), status, label, actor, createdAt });

type SeedTicket = Pick<Ticket, "id" | "title" | "unitId" | "floor" | "locationType" | "category" | "priority" | "score" | "status" | "createdAt"> & Partial<Ticket>;
const seedTicket = (input: SeedTicket): Ticket => ({
  description: "Phản ánh từ cư dân.", updatedAt: input.createdAt, dueAt: dueAt(input.priority, input.createdAt),
  residentName: `Cư dân ${input.unitId}`, residentPhone: "0912•••456", imageReadable: true, redFlag: false, managerSeen: true,
  timeline: [timeline("new", "Đã gửi", "Cư dân", input.createdAt)], ...input,
});

const seedStore = (): Store => {
  const tickets: Ticket[] = [
    seedTicket({ id: "TK-1042", title: "Thang máy số 2 báo lỗi", description: "Thang máy dừng tầng 12, chuông báo đang kêu liên tục nhưng không có cư dân bên trong.", unitId: "A-1203", floor: "Tòa A · Tầng 12", locationType: "Thang máy số 2", category: "Thang máy", priority: "P4", score: 74, status: "in_progress", createdAt: ago(8), updatedAt: ago(5), redFlag: true, image: { name: "Ảnh bảng báo lỗi", dataUrl: DEMO_IMAGE, crop: "left" }, technicianId: "TECH-01", technicianName: "Nguyễn Minh An", assignmentNote: "Ưu tiên kiểm tra cabin số 2.", timeline: [timeline("new", "Đã gửi", "Cư dân", ago(8)), timeline("approved", "Đã duyệt", "BQL Lan", ago(7)), timeline("assigned", "Đã gán kỹ thuật viên", "BQL Lan", ago(6)), timeline("in_progress", "Đang xử lý", "Nguyễn Minh An", ago(4))] }),
    seedTicket({ id: "TK-1041", title: "Dây điện hở tại hầm xe", description: "Có tia lửa và mùi khét gần khu vực đỗ xe.", unitId: "B-0305", floor: "Tòa B · Tầng 3", locationType: "Bãi xe hầm", category: "Chập điện", priority: "P5", score: 90, status: "new", createdAt: ago(18), redFlag: true, image: { name: "Ảnh dây điện", dataUrl: DEMO_IMAGE, crop: "right" } }),
    seedTicket({ id: "TK-1040", title: "Nước thấm trần hành lang", description: "Nước nhỏ liên tục từ trần xuống hành lang.", unitId: "A-0708", floor: "Tòa A · Tầng 7", locationType: "Hành lang", category: "Rò nước", priority: "P2", score: 46, status: "approved", createdAt: ago(42), updatedAt: ago(40), image: { name: "Ảnh trần thấm", dataUrl: DEMO_IMAGE, crop: "center" }, timeline: [timeline("new", "Đã gửi", "Cư dân", ago(42)), timeline("approved", "Đã duyệt", "BQL Lan", ago(40))] }),
    seedTicket({ id: "TK-1039", title: "Ảnh và mô tả không khớp", description: "Tôi nghe tiếng ồn lớn ở hành lang, ảnh chụp lại là vệt nước dưới sàn.", unitId: "A-1203", floor: "Tòa A · Tầng 12", locationType: "Trong căn hộ", category: "Rò nước", priority: "P0", score: null, status: "p0_review", createdAt: ago(1140), image: { name: "Ảnh vệt nước", dataUrl: DEMO_IMAGE, crop: "center" }, p0Evidence: { imageCategory: "Rò nước", textCategory: "Tiếng ồn", imageSeverity: "Vừa" }, timeline: [timeline("new", "Đã gửi", "Cư dân", ago(1140)), timeline("p0_review", "Chờ BQL xác nhận", "Hệ thống AI", ago(1139))] }),
    seedTicket({ id: "TK-1038", title: "Khóa cửa chính bị kẹt", description: "Khóa cửa không thể đóng lại hoàn toàn.", unitId: "C-0201", floor: "Tòa C · Tầng 2", locationType: "Cửa chính", category: "Hỏng khóa / cửa", priority: "P2", score: 38, status: "in_progress", createdAt: ago(1215), updatedAt: ago(1200), image: { name: "Ảnh khóa cửa", dataUrl: DEMO_IMAGE, crop: "left" }, timeline: [timeline("new", "Đã gửi", "Cư dân", ago(1215)), timeline("approved", "Đã duyệt", "BQL Lan", ago(1210)), timeline("in_progress", "Đang xử lý", "BQL Lan", ago(1200))] }),
    seedTicket({ id: "TK-1037", title: "Chập điện khu vực hầm xe", unitId: "B-0309", floor: "Tòa B · Tầng 3", locationType: "Bãi xe hầm", category: "Chập điện", priority: "P4", score: 72, status: "in_progress", createdAt: ago(70), updatedAt: ago(65), redFlag: true }),
    seedTicket({ id: "TK-1036", title: "Đèn hành lang bị hỏng", unitId: "B-0902", floor: "Tòa B · Tầng 9", locationType: "Hành lang", category: "Hỏng đèn", priority: "P1", score: 22, status: "approved", createdAt: ago(1550), updatedAt: ago(1540), image: { name: "Ảnh bóng đèn", dataUrl: DEMO_IMAGE, crop: "right" } }),
    seedTicket({ id: "TK-1035", title: "Tiếng ồn kéo dài", unitId: "A-0502", floor: "Tòa A · Tầng 5", locationType: "Trong căn hộ", category: "Tiếng ồn", priority: "P1", score: 17, status: "new", createdAt: ago(1700) }),
    seedTicket({ id: "TK-1029", title: "Nước đọng hành lang tầng 8", unitId: "A-0803", floor: "Tòa A · Tầng 8", locationType: "Hành lang", category: "Rò nước", priority: "P2", score: 40, status: "in_progress", createdAt: ago(1800), updatedAt: ago(1780) }),
    seedTicket({ id: "TK-1018", title: "Vệt thấm từ trần tầng 6", unitId: "A-0602", floor: "Tòa A · Tầng 6", locationType: "Hành lang", category: "Rò nước", priority: "P1", score: 28, status: "approved", createdAt: ago(2400), updatedAt: ago(2390) }),
    seedTicket({ id: "TK-1028", title: "Đèn trước căn hộ đã được thay", unitId: "A-1203", floor: "Tòa A · Tầng 12", locationType: "Hành lang", category: "Hỏng đèn", priority: "P1", score: 18, status: "completed", createdAt: ago(12_000), updatedAt: ago(11_400), timeline: [timeline("new", "Đã gửi", "Cư dân", ago(12_000)), timeline("approved", "Đã duyệt", "BQL Lan", ago(11_980)), timeline("in_progress", "Đang xử lý", "BQL Lan", ago(11_700)), timeline("completed", "Hoàn thành", "BQL Lan", ago(11_400))] }),
    seedTicket({ id: "TK-1022", title: "Cần làm rõ mức độ rò nước", description: "Có vết nước gần cửa.", unitId: "A-1203", floor: "Tòa A · Tầng 12", locationType: "Trong căn hộ", category: "Rò nước", priority: "P0", score: null, status: "awaiting_response", createdAt: ago(3), updatedAt: ago(2), timeline: [timeline("new", "Đã gửi", "Cư dân", ago(3)), timeline("awaiting_response", "Đang chờ bạn trả lời", "Hệ thống AI", ago(2))] }),
    seedTicket({ id: "TK-1019", title: "Ticket đã đóng do quá thời gian", description: "Mô tả chưa đủ rõ.", unitId: "A-1203", floor: "Tòa A · Tầng 12", locationType: "Hành lang", category: "Chưa xác định", priority: "P0", score: null, status: "invalid", createdAt: ago(15_000), updatedAt: ago(14_995), timeline: [timeline("new", "Đã gửi", "Cư dân", ago(15_000)), timeline("awaiting_response", "Đang chờ bạn trả lời", "Hệ thống AI", ago(14_999)), timeline("invalid", "Không hợp lệ do quá thời gian", "Hệ thống", ago(14_995))] }),
    seedTicket({ id: "TK-0987", title: "Mùi hôi không xác định nguồn", unitId: "A-1203", floor: "Tòa A · Tầng 12", locationType: "Hành lang", category: "Mùi hôi / vệ sinh", priority: "P1", score: 12, status: "cannot_resolve", createdAt: ago(24_000), updatedAt: ago(23_800), cannotResolveReason: "Không xác định được nguồn phát sinh." }),
  ];
  return {
    version: 5, revision: 1, tickets,
    audit: [
      { id: "AUD-3", ticketId: "TK-1042", actor: "BQL Lan", action: "Đổi trạng thái", before: "Đã duyệt", after: "Đang xử lý", createdAt: ago(5) },
      { id: "AUD-2", ticketId: "TK-1041", actor: "Hệ thống AI", action: "Red-flag override", before: "—", after: "P5", reason: "RedFlagSignal: dây điện hở", createdAt: ago(18) },
      { id: "AUD-1", ticketId: "TK-1040", actor: "BQL Lan", action: "Duyệt ticket", before: "Mới", after: "Đã duyệt", createdAt: ago(40) },
    ],
    notifications: [
      { id: "NOTI-3", ticketId: "TK-1042", role: "resident", title: "Cập nhật trạng thái", body: "#TK-1042 → Đang xử lý", createdAt: ago(5), unread: true },
      { id: "NOTI-2", ticketId: "TK-1039", role: "resident", title: "Ticket đang chờ BQL duyệt", body: "BQL đang đối chiếu ảnh và nội dung của #TK-1039.", createdAt: ago(1139), unread: true },
      { id: "NOTI-1", ticketId: "TK-1028", role: "resident", title: "Hoàn thành", body: "#TK-1028 đã hoàn thành.", createdAt: ago(11_400), unread: false },
      { id: "NOTI-TECH-1", ticketId: "TK-1042", role: "technician", title: "Công việc ưu tiên được giao", body: "#TK-1042 · Thang máy số 2 · Tòa A tầng 12", createdAt: ago(6), unread: true },
      { id: "NOTI-4", ticketId: "TK-1022", role: "resident", title: "Cần bạn trả lời", body: "AI cần thêm thông tin để phân loại #TK-1022.", createdAt: ago(2), unread: true },
    ],
    technicians: [
      { id: "TECH-01", name: "Nguyễn Minh An", phone: "0901•••128", specialty: ["Thang máy", "Chập điện"], available: false, active: true },
      { id: "TECH-02", name: "Trần Quốc Bình", phone: "0902•••245", specialty: ["Rò nước", "Kết cấu", "Hỏng khóa / cửa"], available: true, active: true },
      { id: "TECH-03", name: "Lê Hoàng Nam", phone: "0903•••367", specialty: ["Điều hòa / thông gió", "Mất điện cục bộ", "Hỏng đèn"], available: true, active: true },
    ],
    categoryConfigs: categories.map((name, index) => ({ id: `CAT-${String(index + 1).padStart(2, "0")}`, name, ceiling: ["Mùi hôi / vệ sinh", "Tiếng ồn"].includes(name) ? "P1" : ["Hỏng khóa / cửa", "Điều hòa / thông gió", "Mất điện cục bộ", "Kết cấu", "Hỏng đèn"].includes(name) ? "P2" : "Không giới hạn", active: true })),
  };
};

let serverSnapshot = seedStore();
const normalizeStore = (parsed: Partial<Store> & { version?: number }): Store => {
  const seeded = seedStore();
  const normalized: Store = {
    ...seeded,
    ...parsed,
    version: 5,
    revision: Number.isFinite(parsed.revision) ? parsed.revision! : seeded.revision,
    tickets: Array.isArray(parsed.tickets) ? parsed.tickets : seeded.tickets,
    audit: Array.isArray(parsed.audit) ? parsed.audit : seeded.audit,
    notifications: Array.isArray(parsed.notifications) ? parsed.notifications : seeded.notifications,
    technicians: Array.isArray(parsed.technicians) && parsed.technicians.length > 0 ? parsed.technicians : seeded.technicians,
    categoryConfigs: Array.isArray(parsed.categoryConfigs) && parsed.categoryConfigs.length > 0 ? parsed.categoryConfigs : seeded.categoryConfigs,
  };
  window.localStorage.setItem(STORE_KEY, JSON.stringify(normalized));
  return normalized;
};

const readStore = (): Store => {
  if (typeof window === "undefined") return serverSnapshot;
  const raw = window.localStorage.getItem(STORE_KEY);
  if (!raw) { const seeded = seedStore(); writeStore(seeded); return seeded; }
  try {
    const parsed = JSON.parse(raw) as Store & { version: number };
    if (parsed.version === 5) return normalizeStore(parsed);
    if (parsed.version === 4) {
      const seeded = seedStore();
      const demoAssignment = seeded.tickets.find((ticket) => ticket.id === "TK-1042");
      const tickets = parsed.tickets.map((ticket) => ticket.id === "TK-1042" && !ticket.technicianId && demoAssignment ? { ...ticket, technicianId: demoAssignment.technicianId, technicianName: demoAssignment.technicianName, assignmentNote: demoAssignment.assignmentNote, status: demoAssignment.status, timeline: demoAssignment.timeline } : ticket);
      const technicianNotifications = parsed.notifications.some((item) => item.role === "technician") ? [] : seeded.notifications.filter((item) => item.role === "technician");
      const migrated: Store = { ...parsed, version: 5, tickets, notifications: [...parsed.notifications, ...technicianNotifications], technicians: seeded.technicians, categoryConfigs: seeded.categoryConfigs };
      window.localStorage.setItem(STORE_KEY, JSON.stringify(migrated));
      return migrated;
    }
    if (parsed.version === 3) {
      const seeded = seedStore();
      const demoAssignment = seeded.tickets.find((ticket) => ticket.id === "TK-1042");
      const tickets = parsed.tickets.map((ticket) => ({ ...ticket, ...(ticket.id === "TK-1042" && demoAssignment ? { technicianId: demoAssignment.technicianId, technicianName: demoAssignment.technicianName, assignmentNote: demoAssignment.assignmentNote, status: demoAssignment.status, timeline: demoAssignment.timeline } : {}), managerSeen: ticket.managerSeen ?? Number(ticket.id.replace("TK-", "")) <= 1042 }));
      const migrated: Store = { ...parsed, version: 5, technicians: seeded.technicians, categoryConfigs: seeded.categoryConfigs, tickets, notifications: [...parsed.notifications, ...seeded.notifications.filter((item) => item.role === "technician")] };
      window.localStorage.setItem(STORE_KEY, JSON.stringify(migrated));
      return migrated;
    }
    return seedStore();
  }
  catch { const seeded = seedStore(); writeStore(seeded); return seeded; }
};

const writeStore = (store: Store) => {
  store.revision += 1; serverSnapshot = store;
  if (typeof window !== "undefined") { window.localStorage.setItem(STORE_KEY, JSON.stringify(store)); window.dispatchEvent(new Event(STORE_EVENT)); }
};
const addAudit = (store: Store, entry: Omit<AuditLogEntry, "id" | "createdAt">) => store.audit.unshift({ id: uid("AUD"), createdAt: now(), ...entry });
const addNotification = (store: Store, entry: Omit<AppNotification, "id" | "createdAt" | "unread">) => store.notifications.unshift({ id: uid("NOTI"), createdAt: now(), unread: true, ...entry });
const addTimeline = (ticket: Ticket, status: TicketStatus, actor: string, label = statusLabels[status]) => ticket.timeline.push(timeline(status, label, actor, now()));
const commitStatus = (ticket: Ticket, status: TicketStatus, actor: string, label?: string) => { ticket.status = status; ticket.updatedAt = now(); addTimeline(ticket, status, actor, label); };

const inferTicket = (payload: CreateTicketPayload) => {
  const text = `${payload.description} ${payload.locationType}`.toLowerCase();
  const images = payload.images?.length ? payload.images : payload.image ? [payload.image] : [];
  if (images.some((image) => image.name.toLowerCase().includes("mờ"))) return { unreadable: true } as const;
  if (/(cháy|khói|kẹt người|điện hở|ngất|gây rối)/i.test(text)) return { category: text.includes("kẹt") ? "Thang máy" as Category : "Chập điện" as Category, priority: "P5" as Priority, score: 90, redFlag: true, title: "Sự cố cần xử lý khẩn cấp" };
  if (images.length > 0 && /(ồn|tiếng)/i.test(text)) return { category: "Rò nước" as Category, priority: "P0" as Priority, score: null, redFlag: false, title: "Cần BQL xác nhận", p0Evidence: { imageCategory: "Rò nước" as Category, textCategory: "Tiếng ồn" as Category, imageSeverity: "Vừa" as const } };
  if (/(nước|rò|tràn|thấm)/i.test(text)) return { category: "Rò nước" as Category, priority: "P1" as Priority, score: 24, redFlag: false, title: "Rò nước cần kiểm tra" };
  if (/(đèn|bóng)/i.test(text)) return { category: "Hỏng đèn" as Category, priority: "P1" as Priority, score: 18, redFlag: false, title: "Hỏng đèn khu vực chung" };
  return { category: "Mùi hôi / vệ sinh" as Category, priority: "P1" as Priority, score: 20, redFlag: false, title: "Phản ánh cư dân" };
};

export const subscribeStore = (callback: () => void) => { window.addEventListener(STORE_EVENT, callback); window.addEventListener("storage", callback); return () => { window.removeEventListener(STORE_EVENT, callback); window.removeEventListener("storage", callback); }; };
export const getStoreSnapshot = () => readStore().revision;
export const getServerSnapshot = () => 0;

export const listTickets = (role: UserRole, filters: TicketFilters = {}) => {
  let tickets = readStore().tickets;
  if (role === "resident") tickets = tickets.filter((ticket) => ticket.unitId === "A-1203");
  if (role === "technician") tickets = tickets.filter((ticket) => ticket.technicianId === "TECH-01");
  if (filters.query) { const q = filters.query.toLowerCase(); tickets = tickets.filter((ticket) => `${ticket.id} ${ticket.title} ${ticket.description} ${ticket.unitId}`.toLowerCase().includes(q)); }
  if (filters.category && filters.category !== "all") tickets = tickets.filter((ticket) => ticket.category === filters.category);
  if (filters.priority && filters.priority !== "all") tickets = tickets.filter((ticket) => ticket.priority === filters.priority);
  if (filters.status && filters.status !== "all") tickets = tickets.filter((ticket) => ticket.status === filters.status);
  if (filters.date && filters.date !== "all") { const cutoff = Date.now() - (filters.date === "today" ? 86_400_000 : 604_800_000); tickets = tickets.filter((ticket) => Date.parse(ticket.createdAt) >= cutoff); }
  return [...tickets].sort((a, b) => priorityRank(b.priority) - priorityRank(a.priority) || Date.parse(b.createdAt) - Date.parse(a.createdAt));
};

export const getTicket = (ticketId: string) => readStore().tickets.find((ticket) => ticket.id === ticketId);
export const listAudit = () => readStore().audit;
export const listNotifications = (role: UserRole) => readStore().notifications.filter((item) => item.role === role);
export const listTechnicians = () => readStore().technicians;
export const listCategoryConfigs = () => readStore().categoryConfigs;

export const assignTicket = (ticketId: string, technicianId: string, note = "") => {
  const store = readStore();
  const ticket = store.tickets.find((item) => item.id === ticketId);
  const technician = store.technicians.find((item) => item.id === technicianId && item.active);
  if (!ticket || ticket.priority === "P5" || !technician || !["approved", "assigned", "in_progress"].includes(ticket.status) || ticket.technicianId === technicianId) return false;
  const previousTechnicianId = ticket.technicianId;
  const previousTechnicianName = ticket.technicianName;
  ticket.technicianId = technician.id;
  ticket.technicianName = technician.name;
  ticket.assignmentNote = note.trim();
  technician.available = false;
  commitStatus(ticket, "assigned", "BQL Lan");
  if (previousTechnicianId) {
    const previousTechnician = store.technicians.find((item) => item.id === previousTechnicianId);
    const stillBusy = store.tickets.some((item) => item.id !== ticket.id && item.technicianId === previousTechnicianId && ["assigned", "in_progress"].includes(item.status));
    if (previousTechnician) previousTechnician.available = !stillBusy;
  }
  const reassigned = Boolean(previousTechnicianId);
  addAudit(store, { ticketId, actor: "BQL Lan", action: reassigned ? "Gán lại kỹ thuật viên" : "Gán kỹ thuật viên", before: previousTechnicianName || "Chưa phân công", after: technician.name, reason: note.trim() || undefined });
  addNotification(store, { ticketId, role: "technician", title: "Công việc mới được giao", body: `${ticket.id} · ${ticket.category} · ${ticket.floor}` });
  addNotification(store, { ticketId, role: "resident", title: `${ticket.id} ${reassigned ? "đã đổi kỹ thuật viên" : "đã được phân công"}`, body: `Kỹ thuật viên ${technician.name} sẽ tiếp nhận phản ánh.` });
  writeStore(store);
  return true;
};

export const updateTechnicianTicket = (ticketId: string, payload: TechnicianUpdatePayload) => {
  const store = readStore();
  const ticket = store.tickets.find((item) => item.id === ticketId && item.technicianId === "TECH-01");
  if (!ticket) return false;
  const allowed: Partial<Record<TicketStatus, TechnicianUpdatePayload["status"][]>> = { assigned: ["in_progress"], in_progress: ["completed", "cannot_resolve"] };
  if (!allowed[ticket.status]?.includes(payload.status)) return false;
  if (payload.status === "completed" && (!payload.note?.trim() || !payload.completionImages?.length)) return false;
  if (payload.status === "cannot_resolve" && !payload.reason?.trim()) return false;
  if (payload.note?.trim()) ticket.workNote = payload.note.trim();
  if (payload.completionImages?.length) ticket.completionImages = payload.completionImages;
  if (payload.reason?.trim()) ticket.cannotResolveReason = payload.reason.trim();
  commitStatus(ticket, payload.status, ticket.technicianName || "Kỹ thuật viên");
  if (["completed", "cannot_resolve"].includes(payload.status)) {
    const technician = store.technicians.find((item) => item.id === ticket.technicianId);
    if (technician) technician.available = true;
  }
  addAudit(store, { ticketId, actor: ticket.technicianName || "Kỹ thuật viên", action: "Cập nhật xử lý", before: "—", after: statusLabels[payload.status], reason: payload.reason || payload.note });
  addNotification(store, { ticketId, role: "resident", title: `Cập nhật ${ticket.id}`, body: payload.status === "completed" ? "Phản ánh đã được xử lý hoàn thành." : payload.status === "cannot_resolve" ? `Không xử lý được: ${payload.reason}` : `Trạng thái: ${statusLabels[payload.status]}` });
  writeStore(store);
  return true;
};

export const saveTechnician = (input: Omit<Technician, "id"> & { id?: string }) => {
  const store = readStore();
  const existing = input.id ? store.technicians.find((item) => item.id === input.id) : undefined;
  if (existing) Object.assign(existing, input);
  else store.technicians.push({ ...input, id: `TECH-${String(store.technicians.length + 1).padStart(2, "0")}` });
  writeStore(store);
};

export const toggleTechnician = (technicianId: string) => {
  const store = readStore(); const technician = store.technicians.find((item) => item.id === technicianId); if (!technician) return;
  technician.active = !technician.active; writeStore(store);
};

export const addCategoryConfig = (name: string, ceiling: CategoryConfig["ceiling"]) => {
  const store = readStore(); const normalized = name.trim(); if (!normalized || store.categoryConfigs.some((item) => item.name.toLowerCase() === normalized.toLowerCase())) return false;
  store.categoryConfigs.push({ id: `CAT-${Date.now()}`, name: normalized, ceiling, active: true }); writeStore(store); return true;
};

export const toggleCategoryConfig = (categoryId: string) => {
  const store = readStore(); const category = store.categoryConfigs.find((item) => item.id === categoryId); if (!category) return;
  category.active = !category.active; writeStore(store);
};

export const createTicket = async (payload: CreateTicketPayload): Promise<CreateTicketResult> => {
  await new Promise((resolve) => setTimeout(resolve, 1200));
  const inferred = inferTicket(payload);
  if ("unreadable" in inferred) return { ok: false, reason: "unreadable_image", message: "Hệ thống không thể xác định vấn đề của bạn, vui lòng mô tả vấn đề hoặc chụp ảnh rõ hơn." };
  const store = readStore(); const createdAt = now(); const status: TicketStatus = inferred.priority === "P0" ? "p0_review" : "new";
  const images = payload.images?.length ? payload.images : payload.image ? [payload.image] : [];
  const ticket = seedTicket({ id: `TK-${Math.floor(1100 + Math.random() * 8800)}`, description: payload.description, unitId: "A-1203", floor: payload.floor, locationType: payload.locationType, createdAt, updatedAt: createdAt, residentName: "Cư dân A-1203", residentPhone: "0912•••456", image: images[0], images, status, managerSeen: false, ...inferred });
  if (status === "p0_review") addTimeline(ticket, status, "Hệ thống AI");
  store.tickets.unshift(ticket);
  addAudit(store, { ticketId: ticket.id, actor: "Hệ thống AI", action: "Tạo và phân loại ticket", after: `${ticket.category} / ${ticket.priority}` });
  addNotification(store, { ticketId: ticket.id, role: "resident", title: `${ticket.id} đã được tiếp nhận`, body: slaLabels[ticket.priority] });
  writeStore(store); return { ok: true, ticket };
};

export const reviewP0 = (ticketId: string, category: Category) => {
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket || ticket.priority !== "P0") return false;
  ticket.category = category; ticket.priority = category === "Chập điện" || category === "Thang máy" ? "P4" : category === "Rò nước" ? "P2" : "P1";
  ticket.score = scoreForPriority(ticket.priority); ticket.dueAt = dueAt(ticket.priority, ticket.createdAt); ticket.p0Evidence = undefined;
  commitStatus(ticket, "approved", "BQL Lan");
  addAudit(store, { ticketId, actor: "BQL Lan", action: "Duyệt P0", before: "P0", after: `${category} / ${ticket.priority}`, reason: "Đối chiếu ảnh và mô tả gốc" });
  addNotification(store, { ticketId, role: "resident", title: `${ticketId} đã được BQL xác nhận`, body: slaLabels[ticket.priority] }); writeStore(store); return true;
};

export const requestMoreInfo = (ticketId: string, reason: string) => {
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket || !reason.trim()) return false;
  const before = statusLabels[ticket.status]; commitStatus(ticket, "needs_info", "BQL Lan");
  addAudit(store, { ticketId, actor: "BQL Lan", action: "Yêu cầu bổ sung", before, after: "Chờ cư dân", reason });
  addNotification(store, { ticketId, role: "resident", title: "Yêu cầu bổ sung", body: `BQL cần thêm thông tin cho #${ticketId.replace("TK-", "TK-")}: ${reason}` }); writeStore(store); return true;
};

export const rejectTicket = (ticketId: string, reason: string) => {
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket || ticket.status !== "p0_review" || !reason.trim()) return false;
  commitStatus(ticket, "invalid", "BQL Lan", "Không hợp lệ");
  addAudit(store, { ticketId, actor: "BQL Lan", action: "Loại ticket thủ công", before: "Chờ duyệt", after: "Không hợp lệ", reason: reason.trim() });
  addNotification(store, { ticketId, role: "resident", title: `${ticketId} không hợp lệ`, body: reason.trim() });
  writeStore(store); return true;
};

export const answerAiQuestion = (ticketId: string, answer: string) => {
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket || ticket.status !== "awaiting_response" || !answer.trim()) return false;
  ticket.description = `${ticket.description}\n\nTrả lời AI: ${answer.trim()}`;
  const dangerous = /(nguy hiểm|chảy thành dòng|tràn|tia lửa|khói|kẹt)/i.test(answer);
  ticket.category = dangerous && /(điện|tia lửa|khói)/i.test(answer) ? "Chập điện" : "Rò nước";
  ticket.priority = dangerous ? "P5" : "P1";
  ticket.score = scoreForPriority(ticket.priority);
  ticket.redFlag = dangerous;
  ticket.dueAt = dueAt(ticket.priority, ticket.createdAt);
  commitStatus(ticket, "new", "Hệ thống AI", dangerous ? "Phát hiện dấu hiệu nguy hiểm — chuyển BQL ngay" : "Đã nhận câu trả lời bổ sung");
  addAudit(store, { ticketId, actor: "Hệ thống AI", action: "Xử lý câu trả lời cư dân", before: "Đang chờ trả lời", after: dangerous ? "P5 / Mới" : "P1 / Mới", reason: answer.trim() });
  addNotification(store, { ticketId, role: "resident", title: dangerous ? "Đã chuyển sự cố khẩn cấp tới BQL" : "Đã nhận câu trả lời", body: dangerous ? "Ảnh hoặc nội dung có dấu hiệu nguy hiểm, BQL sẽ liên hệ ngay." : "Ticket đang tiếp tục được xử lý." });
  writeStore(store); return true;
};

export const supplementTicket = (ticketId: string, description: string, image?: TicketImage) => {
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket || ticket.status !== "needs_info" || (!description.trim() && !image)) return false;
  if (description.trim()) ticket.description = `${ticket.description}\n\nBổ sung: ${description.trim()}`;
  if (image) ticket.image = image;
  const inferred = inferTicket({ description: ticket.description, floor: ticket.floor, locationType: ticket.locationType, image: ticket.image });
  if ("unreadable" in inferred) return false;
  Object.assign(ticket, inferred); ticket.p0Evidence = "p0Evidence" in inferred ? inferred.p0Evidence : undefined;
  const nextStatus: TicketStatus = inferred.priority === "P0" ? "p0_review" : "new";
  commitStatus(ticket, nextStatus, "Cư dân", "Đã bổ sung thông tin");
  addAudit(store, { ticketId, actor: "Cư dân", action: "Bổ sung thông tin", before: "Chờ cư dân", after: statusLabels[nextStatus] });
  addNotification(store, { ticketId, role: "resident", title: `${ticketId} đã nhận bổ sung`, body: "BQL sẽ tiếp tục xem xét phản ánh." }); writeStore(store); return true;
};

export const overrideTicket = (ticketId: string, category: Category, priority: Priority, reason: string) => {
  if (!reason.trim() || priority === "P0" || priority === "P5") return false;
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket) return false;
  const before = `${ticket.category} / ${ticket.priority}`; ticket.category = category; ticket.priority = priority; ticket.score = scoreForPriority(priority); ticket.dueAt = dueAt(priority, ticket.createdAt); ticket.overrideReason = reason;
  if (["new", "p0_review", "needs_info"].includes(ticket.status)) commitStatus(ticket, "approved", "BQL Lan"); else ticket.updatedAt = now();
  addAudit(store, { ticketId, actor: "BQL Lan", action: "Override Category/Priority", before, after: `${category} / ${priority}`, reason });
  addNotification(store, { ticketId, role: "resident", title: `${ticketId} đã được điều chỉnh`, body: slaLabels[priority] }); writeStore(store); return true;
};

export const updateTicketStatus = (ticketId: string, payload: StatusUpdatePayload) => {
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket || ticket.priority === "P0" || ticket.priority === "P5") return false;
  const allowed: Partial<Record<TicketStatus, StatusUpdatePayload["status"][]>> = { new: ["approved"] };
  if (!allowed[ticket.status]?.includes(payload.status)) return false;
  const before = statusLabels[ticket.status]; commitStatus(ticket, payload.status, "BQL Lan");
  addAudit(store, { ticketId, actor: "BQL Lan", action: "Đổi trạng thái", before, after: statusLabels[payload.status] });
  addNotification(store, { ticketId, role: "resident", title: "Cập nhật trạng thái", body: `#${ticketId.replace("TK-", "TK-")} → ${statusLabels[payload.status]}` }); writeStore(store); return true;
};

export const cancelTicket = (ticketId: string) => {
  const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (!ticket || ticket.status !== "new") return false;
  commitStatus(ticket, "cancelled", "Cư dân"); addAudit(store, { ticketId, actor: "Cư dân", action: "Hủy ticket", before: "Mới", after: "Đã hủy" }); writeStore(store); return true;
};

export const listClusters = (): TicketCluster[] => {
  const cutoff = Date.now() - 3 * 86_400_000;
  const candidates = readStore().tickets.filter((ticket): ticket is Ticket & { category: "Rò nước" | "Chập điện" } => (ticket.category === "Rò nước" || ticket.category === "Chập điện") && ticket.priority !== "P0" && ticket.priority !== "P5" && ticket.status !== "cancelled" && Date.parse(ticket.createdAt) >= cutoff);
  const buckets = new Map<string, Ticket[]>();
  candidates.forEach((ticket) => { const key = `${ticket.category}|${buildingOf(ticket)}`; buckets.set(key, [...(buckets.get(key) || []), ticket]); });
  const clusters: TicketCluster[] = [];
  buckets.forEach((bucket, key) => {
    const sorted = [...bucket].sort((a, b) => floorOf(a) - floorOf(b));
    const groups: Ticket[][] = [];
    sorted.forEach((ticket) => { const last = groups.at(-1); if (last && floorOf(ticket) - Math.max(...last.map(floorOf)) <= 1) last.push(ticket); else groups.push([ticket]); });
    groups.filter((group) => group.length > 1).forEach((group) => {
      const [category, building] = key.split("|") as ["Rò nước" | "Chập điện", string];
      const floors = group.map(floorOf); const knownId = category === "Rò nước" && building === "Tòa A" ? "C-07" : category === "Chập điện" && building === "Tòa B" ? "C-08" : `C-${String(clusters.length + 9).padStart(2, "0")}`;
      clusters.push({ id: knownId, category, building, floorLabel: Math.min(...floors) === Math.max(...floors) ? `Tầng ${floors[0]}` : `Tầng ${Math.min(...floors)}–${Math.max(...floors)}`, density: group.length, closed: group.every((ticket) => ["completed", "cannot_resolve"].includes(ticket.status)), tickets: group });
    });
  });
  return clusters.sort((a, b) => Number(a.closed) - Number(b.closed) || b.density - a.density);
};

export const getClusterForTicket = (ticketId: string) => listClusters().find((cluster) => cluster.tickets.some((ticket) => ticket.id === ticketId));
export const markTicketSeen = (ticketId: string) => { const store = readStore(); const ticket = store.tickets.find((item) => item.id === ticketId); if (ticket?.managerSeen === false) { ticket.managerSeen = true; writeStore(store); } };
export const markNotificationRead = (notificationId: string) => { const store = readStore(); const item = store.notifications.find((notification) => notification.id === notificationId); if (item) { item.unread = false; writeStore(store); } };
export const resetDemoData = () => writeStore(seedStore());
export const formatDateTime = (iso: string) => new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
export const formatRemaining = (iso?: string) => { if (!iso) return "Chờ xác nhận"; const ms = Date.parse(iso) - Date.now(); const abs = Math.abs(ms); const hours = Math.floor(abs / 3_600_000); const minutes = Math.floor((abs % 3_600_000) / 60_000); return `${ms < 0 ? "Quá hạn" : "Còn"} ${hours ? `${hours}g ` : ""}${minutes}p`; };
const buildingOf = (ticket: Ticket) => ticket.floor.match(/Tòa\s+[A-Z]/i)?.[0] || `Tòa ${ticket.unitId.charAt(0)}`;
const floorOf = (ticket: Ticket) => Number(ticket.floor.match(/Tầng\s+(\d+)/i)?.[1] || 0);
/** Sort weight, most urgent first. P5 is the emergency and P0 is "nobody has
 *  classified it yet", which sits below every real band rather than above them:
 *  an unclassified report is not urgent, it is unknown. */
const priorityRank = (priority: Priority): number => ({ P5: 6, P4: 5, P3: 4, P2: 3, P1: 2, P0: 1 })[priority];
