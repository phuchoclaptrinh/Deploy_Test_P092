export type UserRole = "resident" | "manager" | "technician";
export type Priority = "P0" | "P1" | "P2" | "P3";

export type TicketStatus =
  | "new"
  | "analyzing"
  | "awaiting_response"
  | "needs_info"
  | "p0_review"
  | "approved"
  | "assigned"
  | "in_progress"
  | "completed"
  | "cannot_resolve"
  | "invalid"
  | "cancelled";

export type Category = string;

export type TicketImage = {
  name: string;
  dataUrl: string;
  size?: number;
  crop?: "left" | "center" | "right";
};

export type TimelineEntry = {
  id: string;
  status: TicketStatus;
  label: string;
  actor: string;
  createdAt: string;
};

export type P0Evidence = {
  imageCategory: Category;
  textCategory: Category;
  imageSeverity: "Thấp" | "Vừa" | "Cao";
};

export type Ticket = {
  id: string;
  title: string;
  description: string;
  unitId: string;
  floor: string;
  locationType: string;
  category: Category;
  priority: Priority;
  score: number | null;
  status: TicketStatus;
  createdAt: string;
  updatedAt: string;
  dueAt?: string;
  residentName: string;
  residentPhone: string;
  image?: TicketImage;
  images?: TicketImage[];
  imageReadable: boolean;
  redFlag: boolean;
  managerSeen?: boolean;
  p0Evidence?: P0Evidence;
  cannotResolveReason?: string;
  overrideReason?: string;
  technicianId?: string;
  technicianName?: string;
  assignmentNote?: string;
  workNote?: string;
  completionImages?: TicketImage[];
  timeline: TimelineEntry[];
};

export type Technician = {
  id: string;
  name: string;
  phone: string;
  specialty: Category[];
  available: boolean;
  active: boolean;
};

export type CategoryConfig = {
  id: string;
  name: Category;
  ceiling: "P1" | "P2" | "Không giới hạn";
  active: boolean;
};

export type AuditLogEntry = {
  id: string;
  ticketId: string;
  actor: string;
  action: string;
  before?: string;
  after?: string;
  reason?: string;
  createdAt: string;
};

export type AppNotification = {
  id: string;
  ticketId: string;
  role: UserRole;
  title: string;
  body: string;
  createdAt: string;
  unread: boolean;
};

export type TicketFilters = {
  query?: string;
  category?: Category | "all";
  priority?: Priority | "all";
  status?: TicketStatus | "all";
  date?: "all" | "today" | "week";
};

export type CreateTicketPayload = {
  description: string;
  floor: string;
  locationType: string;
  image?: TicketImage;
  images?: TicketImage[];
};

export type CreateTicketResult =
  | { ok: true; ticket: Ticket }
  | { ok: false; reason: "unreadable_image"; message: string };

export type StatusUpdatePayload = {
  status: "approved" | "in_progress" | "completed" | "cannot_resolve";
};

export type TechnicianUpdatePayload = {
  status: "in_progress" | "completed" | "cannot_resolve";
  note?: string;
  reason?: string;
  completionImages?: TicketImage[];
};

export type TicketCluster = {
  id: string;
  category: "Rò nước" | "Chập điện";
  building: string;
  floorLabel: string;
  density: number;
  closed: boolean;
  tickets: Ticket[];
};
