"use client";

import { AlarmClock, ArrowLeft, CheckCircle2, ClipboardList, Info, Layers3, Loader2, PowerOff, RotateCcw, Sparkles, UserCog, XCircle, Zap } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { approveCoordinatorCluster, assignCoordinatorCluster, cancelCoordinatorAssignmentProposal, confirmCoordinatorAssignmentProposal, createCoordinatorAssignmentProposal, disableDirectAutoAssignment, getAssignmentSchedule, getAutoAssignmentSettings, getCoordinatorAssignmentProposal, listCoordinatorAssignmentProposals, listCoordinatorClusters, updateAssignmentSchedule, updateCoordinatorAssignmentProposalItem } from "@/api/backend.api";
import { PriorityBadge } from "@/components/StatusBadge";
import { AutoApproveAction } from "@/components/manager/AutoApproveAction";
import {
  AI_GROUP_LABEL,
  BUILDING_STEP_MS,
  COORDINATOR_GROUP_LABEL,
  DIRECT_ACTIVATED_MESSAGE,
  DIRECT_DISABLE_NOTICE,
  NOTHING_PLACED_HINT,
  SCHEDULE_OPTIONS,
  UNASSIGNED_COLUMN,
  activeTechnicians,
  approvalTicketsFromEntries,
  assignedResult,
  assignmentErrorMessage,
  assignmentSourceQueues,
  awaitingApprovalReason,
  buildingSteps,
  canConfirmBatch,
  confirmSummary,
  draftBoard,
  draftCardRows,
  draftSummary,
  directActivatedByConfirmation,
  directControl,
  dropChange,
  formatCountdown,
  queueTicketCount,
  scheduleChoiceOf,
  scheduleLabel,
  technicianChoiceGroups,
  unassignedConsequence,
} from "@/lib/assignment";
import type { AssignmentQueueEntry } from "@/lib/assignment";
import { formatCategoryName } from "@/lib/category";
import { formatTicketCode } from "@/lib/display";
import { deadlineLabel, formatDateTime } from "@/lib/managerTicket";
import type { AssignmentProposalBatch, AssignmentProposalItem, AssignmentSchedule, AutoAssignmentSettings, CoordinatorCluster, CoordinatorTicket, ProposalScheduleChoice, TechnicianSummary } from "@/types/api";

type Props = {
  tickets: CoordinatorTicket[];
  technicians: TechnicianSummary[];
  selectedTicketId: string | null;
  onOpenTicket: (ticketId: string) => void;
  /** Back to the dashboard table (state 0). */
  onClose: () => void;
  onAssignmentsChanged: () => void;
};

/** States 1, 2 and 3 of the assignment flow.
 *
 *  Which one is on screen follows the data rather than a flag the user toggles.
 *  No open batch means there is nothing to draft, so the two source queues show
 *  (state 1); a `BUILDING` batch is the decision engine still answering (state 2); a
 *  `READY` one is the draft board (state 3). A reload, a cancel or an expiry
 *  therefore all land the coordinator in the right place with nothing to keep
 *  in sync.
 *
 *  Entering the workspace deliberately does *not* create a batch. The previous
 *  version did, which meant opening the screen showed an empty `BUILDING`
 *  surface before the coordinator had seen what was in the queue.
 */
export function AssignmentWorkspace({ tickets, technicians, selectedTicketId, onOpenTicket, onClose, onAssignmentsChanged }: Props) {
  const [batches, setBatches] = useState<AssignmentProposalBatch[]>([]);
  const [clusters, setClusters] = useState<CoordinatorCluster[]>([]);
  const [schedule, setSchedule] = useState<AssignmentSchedule | null>(null);
  const [direct, setDirect] = useState<AutoAssignmentSettings | null>(null);
  const [stopDirectOpen, setStopDirectOpen] = useState(false);
  const [edited, setEdited] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [result, setResult] = useState<AssignmentProposalBatch | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const roster = useMemo(() => activeTechnicians(technicians), [technicians]);

  const load = useCallback(async () => {
    try {
      const [nextBatches, nextSchedule, nextDirect, nextClusters] = await Promise.all([
        listCoordinatorAssignmentProposals(),
        getAssignmentSchedule(),
        getAutoAssignmentSettings(),
        listCoordinatorClusters(),
      ]);
      if (!mounted.current) return;
      setBatches(nextBatches);
      setSchedule(nextSchedule);
      setDirect(nextDirect);
      setClusters(nextClusters);
      setError("");
    } catch {
      // A background refresh must not replace the coordinator's working screen
      // with a generic transport message. Explicit actions still surface their
      // own actionable errors through runAction below.
      if (mounted.current) setError("");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer); }, []);

  const activeBatch = batches.find((batch) => ["BUILDING", "READY"].includes(batch.status)) || null;
  const activeBatchId = activeBatch?.id;
  const building = activeBatch?.status === "BUILDING";
  useEffect(() => {
    // Only while BUILDING, and only the one batch: it turns READY when the
    // engine has answered, and any other outcome (cancelled, expired) ends the
    // poll just as well because the batch stops being the active one.
    if (!building || !activeBatchId) return;
    const timer = window.setInterval(() => {
      void getCoordinatorAssignmentProposal(activeBatchId)
        .then((batch) => { if (mounted.current) setBatches((items) => items.map((item) => (item.id === batch.id ? batch : item))); })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [activeBatchId, building]);

  const sourceQueues = useMemo(() => assignmentSourceQueues(tickets, clusters), [tickets, clusters]);
  const awaitingApproval = sourceQueues.approval;
  const awaitingAssignment = sourceQueues.ready;
  const approvalTickets = useMemo(() => approvalTicketsFromEntries(awaitingApproval), [awaitingApproval]);
  const awaitingApprovalCount = queueTicketCount(awaitingApproval);
  const awaitingAssignmentCount = queueTicketCount(awaitingAssignment);

  const expiresAt = activeBatch?.expires_at ? Date.parse(activeBatch.expires_at) : null;
  const remainingMs = expiresAt === null ? null : expiresAt - now;
  const expired = remainingMs !== null && remainingMs <= 0;
  const buildingSince = activeBatch?.created_at ? Date.parse(activeBatch.created_at) : now;
  const automationRunning = Boolean(direct?.enabled || schedule?.enabled);

  const runAction = async (action: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
      if (mounted.current && success) setNotice(success);
    } catch (reason) {
      if (mounted.current) setError(assignmentErrorMessage(reason));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const createProposal = () => runAction(async () => {
    const batch = await createCoordinatorAssignmentProposal(20);
    setBatches((items) => [batch, ...items.filter((item) => item.id !== batch.id)]);
    setEdited(false);
  }, "");

  const patchItem = (item: AssignmentProposalItem, change: { selected?: boolean; technician_id?: string }) => {
    if (!activeBatch) return;
    void runAction(async () => {
      const batch = await updateCoordinatorAssignmentProposalItem(activeBatch.id, item.id, change);
      setBatches((items) => items.map((row) => (row.id === batch.id ? batch : row)));
      setEdited(true);
    }, "");
  };

  const confirmBatch = () => {
    if (!activeBatch) return;
    void runAction(async () => {
      // An edited table confirms against the version it was edited on. Nothing
      // about DIRECT or the repeat schedule is sent: DIRECT activation is the
      // backend's consequence of this succeeding, and the repeat is a separate
      // decision the result modal asks about once this has actually happened.
      const batch = await confirmCoordinatorAssignmentProposal(activeBatch.id, edited ? activeBatch.version : undefined);
      setBatches((items) => items.map((row) => (row.id === batch.id ? batch : row)));
      setEdited(false);
      setConfirmOpen(false);
      setResult(batch);
      onAssignmentsChanged();
      await load();
    }, "");
  };

  const cancelBatch = () => {
    if (!activeBatch) return;
    void runAction(async () => {
      const batch = await cancelCoordinatorAssignmentProposal(activeBatch.id);
      setBatches((items) => items.map((row) => (row.id === batch.id ? batch : row)));
      setEdited(false);
      setConfirmOpen(false);
    }, "Đã hủy đề xuất. Không ticket nào được phân.");
  };

  const finishResult = (choice: ProposalScheduleChoice) => {
    const batch = result;
    if (!batch) return;
    void runAction(async () => {
      const next = await updateAssignmentSchedule(choice === "NONE" ? null : choice, { afterBatchId: batch.id });
      setSchedule(next);
      setResult(null);
      await load();
    }, choice === "NONE" ? "Đã phân việc. Không đặt lịch lặp lại." : `Đã phân việc. Hệ thống sẽ tự tạo đề xuất mới: ${scheduleLabel(choice)}.`);
  };

  const stopDirect = () => runAction(async () => {
    const [next, nextSchedule] = await Promise.all([
      disableDirectAutoAssignment(),
      updateAssignmentSchedule(null, { expectedVersion: schedule?.version }),
    ]);
    setDirect(next);
    setSchedule(nextSchedule);
    setStopDirectOpen(false);
    onAssignmentsChanged();
  }, "Đã tắt tự động phân việc và lịch lặp lại. Ticket đã phân công không thay đổi.");

  const returnToTickets = () => {
    onAssignmentsChanged();
    onClose();
  };
  const resetAssignmentQueue = () => {
    setNotice("");
    setError("");
    onAssignmentsChanged();
    void load();
  };

  return <section className="assignmentWorkspace">
    <header className="assignmentWorkspaceHeader">
      <div className="assignmentWorkspaceTitle">
        <button type="button" className="tableAction" onClick={returnToTickets}><ArrowLeft size={14} />Quay lại danh sách</button>
        <div>
          <h2>Phân việc tự động</h2>
          {!activeBatch && <span className="assignmentWorkspaceCounts">
            {awaitingApprovalCount} ticket chờ duyệt · {awaitingAssignmentCount} ticket sẵn sàng phân việc
          </span>}
        </div>
      </div>
      <div className="assignmentWorkspaceHeaderActions">
        {automationRunning && <button
          type="button"
          className="button secondary assignmentStopButton"
          disabled={busy}
          onClick={() => setStopDirectOpen(true)}
        ><PowerOff size={16} />Tắt tự động phân việc</button>}
        {!activeBatch && <button
          type="button"
          className="button secondary"
          disabled={busy}
          title="Tải lại trạng thái ticket chờ phân công từ hệ thống."
          onClick={resetAssignmentQueue}
        ><RotateCcw size={16} />Đặt lại hàng chờ</button>}
        {/* State 1's primary action, up here with the queues rather than in a
            section of its own. It only appears when no batch is open. */}
        {!activeBatch && <button
          type="button"
          className="button"
          disabled={busy || awaitingAssignmentCount === 0}
          title={awaitingAssignmentCount === 0 ? "Chưa có ticket nào đã duyệt và chờ phân công." : undefined}
          onClick={() => void createProposal()}
        ><ClipboardList size={16} />Tạo đề xuất phân việc</button>}
      </div>
    </header>

    {notice && <div className="alert success" role="status">{notice}</div>}
    {error && !confirmOpen && !result && !stopDirectOpen && <div className="alert error" role="alert">{error}</div>}

    <div className="assignmentWorkspaceContent">
      {!activeBatch
        ? <PreparationQueues
          awaitingApproval={awaitingApproval}
          awaitingAssignment={awaitingAssignment}
          approvalTickets={approvalTickets}
          technicians={roster}
          schedule={schedule}
          direct={direct}
          busy={busy}
          selectedTicketId={selectedTicketId}
          onOpenTicket={onOpenTicket}
          onStopDirect={() => setStopDirectOpen(true)}
          onApproved={(approved, failed) => {
            setNotice(failed > 0
              ? `Đã tự động duyệt ${approved} ticket; ${failed} ticket chưa thể duyệt.`
              : `Đã tự động duyệt ${approved} ticket và đưa vào hàng chờ phân công.`);
            onAssignmentsChanged();
            void load();
          }}
          onApproveCase={(entry) => void runAction(async () => {
            await approveCoordinatorCluster(entry.caseRow.id);
            onAssignmentsChanged();
            await load();
          }, `Đã duyệt các ticket đủ điều kiện trong case ${entry.caseRow.category}.`)}
          onAssignCase={(entry, technicianId) => void runAction(async () => {
            await assignCoordinatorCluster(entry.caseRow.id, technicianId);
            onAssignmentsChanged();
            await load();
          }, `Đã phân tay ${entry.ticketCount} ticket trong case cho kỹ thuật viên.`)}
        />
        : building
          ? <BuildingSurface elapsedMs={now - buildingSince} technicianCount={roster.length} busy={busy} onCancel={cancelBatch} />
          : <DraftBoard
            batch={activeBatch}
            roster={roster}
            busy={busy}
            expired={expired}
            remainingMs={remainingMs}
            selectedTicketId={selectedTicketId}
            onOpenTicket={onOpenTicket}
            onPatchItem={patchItem}
            onCancel={cancelBatch}
            onConfirm={() => setConfirmOpen(true)}
          />}
    </div>

    {confirmOpen && activeBatch && <ConfirmDialog
      batch={activeBatch}
      busy={busy}
      error={error}
      onClose={() => setConfirmOpen(false)}
      onConfirm={confirmBatch}
    />}

    {stopDirectOpen && <StopDirectDialog
      busy={busy}
      error={error}
      onClose={() => setStopDirectOpen(false)}
      onConfirm={() => void stopDirect()}
    />}

    {result && <ResultDialog
      batch={result}
      current={scheduleChoiceOf(schedule)}
      busy={busy}
      error={error}
      onFinish={finishResult}
    />}
  </section>;
}

// ---------------------------------------------------------------------------
// State 1 — the two source queues
// ---------------------------------------------------------------------------

type CaseEntry = Extract<AssignmentQueueEntry, { kind: "case" }>;

type PreparationProps = {
  awaitingApproval: AssignmentQueueEntry[];
  awaitingAssignment: AssignmentQueueEntry[];
  approvalTickets: CoordinatorTicket[];
  technicians: TechnicianSummary[];
  schedule: AssignmentSchedule | null;
  direct: AutoAssignmentSettings | null;
  busy: boolean;
  selectedTicketId: string | null;
  onOpenTicket: (id: string) => void;
  onStopDirect: () => void;
  onApproved: (approved: number, failed: number) => void;
  onApproveCase: (entry: CaseEntry) => void;
  onAssignCase: (entry: CaseEntry, technicianId: string) => void;
};

function PreparationQueues({ awaitingApproval, awaitingAssignment, approvalTickets, technicians, schedule, direct, busy, selectedTicketId, onOpenTicket, onStopDirect, onApproved, onApproveCase, onAssignCase }: PreparationProps) {
  const control = directControl(direct);
  const automationRunning = control.kind === "disable" || Boolean(schedule?.enabled);
  return <>
    {/* DIRECT: when it is on, the control that stops it; when it is off, the
        sentence explaining how it starts. There is no third rendering, because
        `directControl` has no third shape — no enable button, and no greyed-out
        one either, since a disabled control still advertises an action that
        does not exist. */}
    {automationRunning
      ? <div className="alert warning directBanner" role="status">
        <Zap size={14} />
        <span>{control.kind === "disable" ? `Phân việc tự động đang bật · ${control.delay}.` : "Lịch tự động tạo đề xuất đang bật."} Tắt điều khiển này sẽ dừng cả tự động phân việc và lịch lặp lại.</span>
        <button type="button" className="tableAction" disabled={busy} onClick={onStopDirect}>
          <PowerOff size={13} />Tắt tự động phân việc
        </button>
      </div>
      : <p className="directGuidance"><Info size={14} /><span>{control.message}</span></p>}

    <div className="assignmentClassificationAction">
      <div><strong>Tự động duyệt</strong><span>Duyệt các ticket đã có kết quả phân loại của hệ thống để đưa vào hàng chờ phân việc.</span></div>
      <AutoApproveAction tickets={approvalTickets} disabled={busy} onApproved={onApproved} />
    </div>

    {/* Exactly two queues. There is no third column for an open proposal,
        because this state exists only when there is no open proposal. */}
    <div className="assignQueues">
      <QueuePanel
        title="Chờ duyệt thủ công"
        hint="Chưa duyệt hoặc chưa đủ thông tin phân loại. Mở ticket để duyệt hoặc bổ sung — không đưa vào đề xuất được."
        entries={awaitingApproval}
        reasonFor={awaitingApprovalReason}
        selectedTicketId={selectedTicketId}
        onOpenTicket={onOpenTicket}
        onApproveCase={onApproveCase}
      />
      <QueuePanel
        title="Đã duyệt · chưa phân công"
        hint="Nguồn duy nhất của đề xuất phân việc."
        entries={awaitingAssignment}
        selectedTicketId={selectedTicketId}
        onOpenTicket={onOpenTicket}
        technicians={technicians}
        onAssignCase={onAssignCase}
      />
    </div>

    {queueTicketCount(awaitingAssignment) === 0 && <p className="assignQueuesNote">
      Chưa có ticket nào đã duyệt và chờ phân công, nên chưa tạo được đề xuất. Duyệt ticket ở cột bên trái trước.
    </p>}
  </>;
}

type QueuePanelProps = {
  title: string;
  hint: string;
  entries: AssignmentQueueEntry[];
  reasonFor?: (ticket: CoordinatorTicket) => string;
  selectedTicketId: string | null;
  onOpenTicket: (id: string) => void;
  technicians?: TechnicianSummary[];
  onApproveCase?: (entry: CaseEntry) => void;
  onAssignCase?: (entry: CaseEntry, technicianId: string) => void;
};

function QueuePanel({ title, hint, entries, reasonFor, selectedTicketId, onOpenTicket, technicians = [], onApproveCase, onAssignCase }: QueuePanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [assigningCase, setAssigningCase] = useState<CaseEntry | null>(null);
  const visibleEntries = expanded ? entries : entries.slice(0, 5);
  return <section className="assignQueue">
    <header><strong>{title}</strong><span>{queueTicketCount(entries)} ticket</span></header>
    <p className="assignQueueHint">{hint}</p>
    <ul>
      {visibleEntries.map((entry) => <li key={entry.kind === "case" ? entry.caseRow.id : entry.ticket.id}>
        {entry.kind === "ticket"
          ? <TicketQueueCard ticket={entry.ticket} reasonFor={reasonFor} selectedTicketId={selectedTicketId} onOpenTicket={onOpenTicket} />
          : <CaseQueueCard
            entry={entry}
            canApprove={Boolean(onApproveCase)}
            canAssign={Boolean(onAssignCase)}
            selectedTicketId={selectedTicketId}
            onOpenTicket={onOpenTicket}
            onApprove={() => onApproveCase?.(entry)}
            onAssign={() => setAssigningCase(entry)}
          />}
      </li>)}
      {entries.length === 0 && <li className="assignQueueEmpty">Không có ticket nào.</li>}
    </ul>
    {entries.length > 5 && <button type="button" className="assignQueueMore" onClick={() => setExpanded((value) => !value)}>
      {expanded ? "Thu gọn" : `Xem thêm ${entries.length - 5} mục`}
    </button>}
    {assigningCase && <CaseManualAssignDialog
      entry={assigningCase}
      technicians={technicians}
      onClose={() => setAssigningCase(null)}
      onAssign={(technicianId) => { onAssignCase?.(assigningCase, technicianId); setAssigningCase(null); }}
    />}
  </section>;
}

function TicketQueueCard({ ticket, reasonFor, selectedTicketId, onOpenTicket }: { ticket: CoordinatorTicket; reasonFor?: (ticket: CoordinatorTicket) => string; selectedTicketId: string | null; onOpenTicket: (id: string) => void }) {
  return (
    // The row itself opens the detail panel; there is no separate View.
    <button type="button" className={selectedTicketId === ticket.id ? "assignTicketCard selected" : "assignTicketCard"} onClick={() => onOpenTicket(ticket.id)}>
      <span className="assignTicketTop">
        <strong>{formatTicketCode(ticket.id)}</strong>
        {ticket.priority && <PriorityBadge priority={ticket.priority} />}
      </span>
      <span className="assignTicketMeta">{formatCategoryName(ticket.category)} · {ticket.location_label || "Chưa xác định"}</span>
      <span className="assignTicketMeta">Gửi {formatDateTime(ticket.created_at)} · Hạn {deadlineLabel(ticket.sla_due_at)}</span>
      {reasonFor && <small className="assignTicketReason">{reasonFor(ticket)}</small>}
    </button>
  );
}

/** A materialized case renders as one card, every member listed and openable,
 *  with a single action that operates on the whole case (`onApprove`/
 *  `onAssign`) — never a per-member action, so a case can never be half
 *  approved or half assigned through this panel by accident. */
function CaseQueueCard({ entry, canApprove, canAssign, selectedTicketId, onOpenTicket, onApprove, onAssign }: { entry: CaseEntry; canApprove: boolean; canAssign: boolean; selectedTicketId: string | null; onOpenTicket: (id: string) => void; onApprove: () => void; onAssign: () => void }) {
  return <article className="assignCaseCard">
    <header className="assignTicketTop">
      <strong><Layers3 size={13} />Case · {entry.ticketCount} ticket</strong>
      <span className="assignCaseCategory">{formatCategoryName(entry.caseRow.category)} · {entry.caseRow.building}</span>
    </header>
    <ul className="assignCaseMembers">
      {entry.tickets.map((ticket) => <li key={ticket.id}>
        <button type="button" className={selectedTicketId === ticket.id ? "assignTicketCard selected" : "assignTicketCard"} onClick={() => onOpenTicket(ticket.id)}>
          <span className="assignTicketTop">
            <strong>{formatTicketCode(ticket.id)}</strong>
            {ticket.priority && <PriorityBadge priority={ticket.priority} />}
          </span>
          <span className="assignTicketMeta">{ticket.location_label || "Chưa xác định"}</span>
        </button>
      </li>)}
    </ul>
    {(canApprove || canAssign) && <footer className="assignCaseActions">
      {canApprove && <button type="button" className="tableAction" onClick={onApprove}>Duyệt case</button>}
      {canAssign && <button type="button" className="tableAction" onClick={onAssign}>Phân tay case</button>}
    </footer>}
  </article>;
}

function CaseManualAssignDialog({ entry, technicians, onClose, onAssign }: { entry: CaseEntry; technicians: TechnicianSummary[]; onClose: () => void; onAssign: (technicianId: string) => void }) {
  const [technicianId, setTechnicianId] = useState(technicians.find((technician) => technician.is_available)?.user_id || "");
  return <div className="managerModalBackdrop" role="dialog" aria-modal="true" aria-labelledby="caseManualAssignTitle">
    <div className="managerModalDialog">
      <h3 id="caseManualAssignTitle">Phân tay case</h3>
      <p>Gán cùng một kỹ thuật viên cho cả {entry.ticketCount} ticket trong case. Nếu bất kỳ ticket nào không đủ điều kiện, không ticket nào trong case sẽ được phân.</p>
      <label className="field"><span>Kỹ thuật viên</span>
        <select value={technicianId} onChange={(event) => setTechnicianId(event.target.value)}>
          <option value="">Chọn kỹ thuật viên</option>
          {technicians.map((technician) => <option value={technician.user_id} key={technician.user_id}>{technician.full_name || technician.user_id.slice(0, 8)}{technician.is_available ? "" : " · Bận"}</option>)}
        </select>
      </label>
      <div className="managerModalActions">
        <button type="button" className="button secondary" onClick={onClose}>Hủy</button>
        <button type="button" className="button" disabled={!technicianId} onClick={() => onAssign(technicianId)}>Phân tay</button>
      </div>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// State 2 — the decision engine is answering
// ---------------------------------------------------------------------------

function BuildingSurface({ elapsedMs, technicianCount, busy, onCancel }: { elapsedMs: number; technicianCount: number; busy: boolean; onCancel: () => void }) {
  const steps = buildingSteps(elapsedMs);
  // Enough skeleton columns to preview the board without pretending to know
  // how many technicians will end up holding something.
  const skeletons = Math.max(2, Math.min(technicianCount, 4));

  return <div className="draftBuildingSurface">
    <div className="draftBuildingHead">
      <div>
        <strong><Sparkles size={16} />Đang tạo đề xuất phân việc</strong>
        <p>Hệ thống đang chuẩn bị quyết định:</p>
      </div>
      {/* Available throughout BUILDING — including for a legacy batch that
          never turns READY, which would otherwise trap this screen. */}
      <button type="button" className="button secondary" disabled={busy} onClick={onCancel}>
        <XCircle size={16} />Hủy tạo đề xuất
      </button>
    </div>

    <ul className="draftBuildingSteps" aria-live="polite">
      {steps.map((step) => <li key={step.label} className={`draftBuildingStep ${step.state}`}>
        <span className="draftBuildingBullet" aria-hidden="true">{step.state === "done" ? "✓" : step.state === "active" ? "●" : "○"}</span>
        <span>{step.label}</span>
      </li>)}
    </ul>

    {/* A preview of the layout the coordinator is about to work in, so the
        screen does not go blank while the request is in flight. */}
    <div className="draftBoard" aria-hidden="true">
      <section className="draftColumn draftSkeletonColumn">
        <header><strong>Chưa phân công</strong></header>
        <div className="draftColumnItems">{[0, 1].map((row) => <div className="draftSkeletonCard tall" key={row} />)}</div>
      </section>
      <div className="draftTechnicians">
        {Array.from({ length: skeletons }, (_, index) => <section className="draftColumn draftSkeletonColumn" key={index}>
          <header><span className="draftSkeletonLine" /></header>
          <div className="draftColumnItems"><div className="draftSkeletonCard" /><div className="draftSkeletonCard" /></div>
        </section>)}
      </div>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// State 3 — the assignment draft board
// ---------------------------------------------------------------------------

type DraftBoardProps = {
  batch: AssignmentProposalBatch;
  roster: TechnicianSummary[];
  busy: boolean;
  expired: boolean;
  remainingMs: number | null;
  selectedTicketId: string | null;
  onOpenTicket: (id: string) => void;
  onPatchItem: (item: AssignmentProposalItem, change: { selected?: boolean; technician_id?: string }) => void;
  onCancel: () => void;
  onConfirm: () => void;
};

function DraftBoard({ batch, roster, busy, expired, remainingMs, selectedTicketId, onOpenTicket, onPatchItem, onCancel, onConfirm }: DraftBoardProps) {
  const [dragItemId, setDragItemId] = useState<string | null>(null);
  const board = useMemo(() => draftBoard(batch, roster), [batch, roster]);
  const summary = draftSummary(batch);

  /** One path for every way a row can move — drag, drop target, or the select
   *  on the card — so they cannot disagree about what a move means. */
  const move = (item: AssignmentProposalItem, columnId: string) => {
    const change = dropChange(item, columnId);
    if (change) onPatchItem(item, change);
  };

  const drop = (columnId: string) => {
    const item = batch.items.find((row) => row.id === dragItemId);
    setDragItemId(null);
    if (item) move(item, columnId);
  };

  return <>
    <div className="draftHeader">
      <strong>Bản nháp phân việc</strong>
      <span className="draftHeaderCount">{summary.placed}/{summary.total} ticket đã được đề xuất</span>
      <span className={expired ? "assignmentExpiry expired" : "assignmentExpiry"}>
        <AlarmClock size={14} />{expired ? "Đã hết hạn 10 phút" : `Còn ${formatCountdown(remainingMs || 0)} để xác nhận`}
      </span>
    </div>

    <div className="draftBoard">
      <section
        className={`draftColumn draftUnassigned${dragItemId ? " droppable" : ""}`}
        onDragOver={(event) => event.preventDefault()}
        onDrop={() => drop(UNASSIGNED_COLUMN)}
      >
        {/* Never "đã xoá": these tickets are out of this round, not gone. */}
        <header><strong>Chưa phân công</strong><span>{board.unassigned.length}</span></header>
        <div className="draftColumnItems">
          {board.unassigned.map((item) => <DraftCard
            key={item.id}
            item={item}
            roster={roster}
            busy={busy}
            detailed
            columnId={UNASSIGNED_COLUMN}
            selectedTicketId={selectedTicketId}
            onOpenTicket={onOpenTicket}
            onMove={move}
            onDragStart={() => setDragItemId(item.id)}
            onDragEnd={() => setDragItemId(null)}
          />)}
          {board.unassigned.length === 0 && <p className="assignQueueEmpty">Mọi ticket đã được đặt vào một kỹ thuật viên.</p>}
        </div>
      </section>

      <div className="draftTechnicians">
        {board.technicians.map((column) => <section
          className={`draftColumn draftTechnician${dragItemId ? " droppable" : ""}`}
          key={column.id}
          onDragOver={(event) => event.preventDefault()}
          onDrop={() => drop(column.id)}
        >
          {/* Name and count only. There is no reliable capacity or duty data,
              so the screen shows neither rather than inventing one. */}
          <header><UserCog size={15} /><strong>{column.name}</strong><span>{column.items.length} việc</span></header>
          <div className="draftColumnItems">
            {column.items.map((item) => <DraftCard
              key={item.id}
              item={item}
              roster={roster}
              busy={busy}
              columnId={column.id}
              selectedTicketId={selectedTicketId}
              onOpenTicket={onOpenTicket}
              onMove={move}
              onDragStart={() => setDragItemId(item.id)}
              onDragEnd={() => setDragItemId(null)}
            />)}
            {column.items.length === 0 && <p className="assignQueueEmpty">Kéo ticket vào đây.</p>}
          </div>
        </section>)}
        {board.technicians.length === 0 && <p className="assignQueueEmpty">Chưa có kỹ thuật viên đang hoạt động.</p>}
      </div>
    </div>

    {/* Sticky at the bottom of the workspace: two actions, nothing else. */}
    <div className="draftActionBar">
      <p className="draftActionSummary">
        <span>{summary.placed} ticket đã gán · {summary.unplaced} ticket chưa gán</span>
        {summary.unplaced > 0 && <small>{unassignedConsequence(summary.unplaced)}</small>}
      </p>
      <div className="draftActionButtons">
        <button type="button" className="button secondary" disabled={busy} onClick={onCancel}><XCircle size={16} />Hủy đề xuất</button>
        <button
          type="button"
          className="button"
          disabled={busy || !canConfirmBatch(batch, expired)}
          title={summary.placed === 0 ? NOTHING_PLACED_HINT : undefined}
          onClick={onConfirm}
        ><CheckCircle2 size={16} />Xác nhận và phân việc</button>
      </div>
    </div>
    {expired && <p className="proposalExpiredNote">Đề xuất đã quá 10 phút nên không thể xác nhận. Hãy hủy và tạo đề xuất mới.</p>}
    {!expired && summary.placed === 0 && <p className="proposalExpiredNote">{NOTHING_PLACED_HINT}</p>}
  </>;
}

type DraftCardProps = {
  item: AssignmentProposalItem;
  roster: TechnicianSummary[];
  busy: boolean;
  /** The unassigned column shows a full dashboard-like card; a technician
   *  column shows the compact one. Same row, two amounts of detail. */
  detailed?: boolean;
  columnId: string;
  selectedTicketId: string | null;
  onOpenTicket: (id: string) => void;
  onMove: (item: AssignmentProposalItem, columnId: string) => void;
  onDragStart: () => void;
  onDragEnd: () => void;
};

function DraftCard({ item, roster, busy, detailed, columnId, selectedTicketId, onOpenTicket, onMove, onDragStart, onDragEnd }: DraftCardProps) {
  const rows = draftCardRows(item, formatTicketCode);
  const groups = technicianChoiceGroups(item, roster);
  const selected = rows.some((row) => row.ticketId === selectedTicketId);

  return <article
    className={`draftCard${detailed ? " detailed" : ""}${selected ? " selected" : ""}`}
    draggable={!busy}
    onDragStart={onDragStart}
    onDragEnd={onDragEnd}
  >
    <div className="draftCardHead">
      {item.work_item_type === "INCIDENT_CASE" && <span className="badge managerTableStatus neutral">Cụm {rows.length} ticket</span>}
    </div>

    {/* Every member of the work item, each openable. A case is never rendered
        as one ticket. */}
    <ul className="draftCardMembers">
      {rows.map((row) => <li key={row.ticketId}>
        <button type="button" className="draftCardTicket" onClick={() => onOpenTicket(row.ticketId)}>
          <span className="draftCardTop">
            <strong>{row.code}</strong>
            {row.priority && <PriorityBadge priority={row.priority} />}
          </span>
          {detailed
            ? <>
              <span className="draftCardMeta">{formatCategoryName(row.category)} · {row.location}</span>
              <span className="draftCardMeta">Gửi {formatDateTime(row.createdAt)} · Hạn {deadlineLabel(row.slaDueAt)}</span>
              <span className="draftCardId">ID {row.ticketId}</span>
            </>
            : <>
              <span className="draftCardMeta">{row.location}</span>
              <span className="draftCardMeta">{deadlineLabel(row.slaDueAt)}</span>
            </>}
        </button>
      </li>)}
    </ul>

    {item.reason && detailed && <small className="draftCardReason">{item.reason}</small>}

    {/* Keyboard and touch equivalent of the drag: the same move, same rules. */}
    <label className="draftCardMove">
      <span className="srOnly">Chuyển ticket sang kỹ thuật viên khác</span>
      <select
        value={columnId === UNASSIGNED_COLUMN ? "" : columnId}
        disabled={busy}
        onChange={(event) => onMove(item, event.target.value || UNASSIGNED_COLUMN)}
      >
        <option value="">Chưa phân công</option>
        {groups.map((group) => <optgroup label={group.label} key={group.label}>
          {group.choices.map((choice) => <option value={choice.id} key={choice.id}>{choice.name}</option>)}
        </optgroup>)}
      </select>
    </label>
  </article>;
}

// ---------------------------------------------------------------------------
// Confirmation
// ---------------------------------------------------------------------------

function ConfirmDialog({ batch, busy, error, onClose, onConfirm }: { batch: AssignmentProposalBatch; busy: boolean; error: string; onClose: () => void; onConfirm: () => void }) {
  const summary = confirmSummary(batch);
  const confirmRef = useRef<HTMLButtonElement>(null);
  // Focus lands here once, when the dialog opens. The ten-minute countdown
  // re-renders every second and must never move it again.
  useEffect(() => { confirmRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  return <div className="managerModalBackdrop" role="dialog" aria-modal="true" aria-labelledby="draftConfirmTitle">
    <div className="managerModalDialog">
      <h3 id="draftConfirmTitle">Xác nhận và phân việc</h3>
      <ul className="managerModalList">
        <li><strong>{summary.ticketCount}</strong> ticket trên <strong>{summary.itemCount}</strong> dòng sẽ được phân ngay.</li>
        {summary.overrideCount > 0 && <li><strong>{summary.overrideCount}</strong> dòng do Ban quản lý đổi người ({COORDINATOR_GROUP_LABEL}), không phải {AI_GROUP_LABEL}.</li>}
      </ul>
      {/* A partial confirmation is valid, so what happens to the rest is
          stated here rather than discovered afterwards. */}
      {summary.consequence && <p className="managerModalCount"><Info size={14} /> {summary.consequence}</p>}
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="managerModalActions">
        <button type="button" className="button secondary" disabled={busy} onClick={onClose}>Quay lại</button>
        <button type="button" className="button" ref={confirmRef} disabled={busy} onClick={onConfirm}>
          {busy ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}Xác nhận và phân việc
        </button>
      </div>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// Stopping DIRECT
// ---------------------------------------------------------------------------

function StopDirectDialog({ busy, error, onClose, onConfirm }: { busy: boolean; error: string; onClose: () => void; onConfirm: () => void }) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { confirmRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  return <div className="managerModalBackdrop" role="dialog" aria-modal="true" aria-labelledby="stopDirectTitle">
    <div className="managerModalDialog">
      <h3 id="stopDirectTitle">Tắt phân việc tự động</h3>
      <p>{DIRECT_DISABLE_NOTICE}</p>
      <p className="managerModalCount">
        Lịch tạo đề xuất theo chu kỳ cũng sẽ dừng. Các ticket đã phân công không bị thay đổi.
      </p>
      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="managerModalActions">
        <button type="button" className="button secondary" disabled={busy} onClick={onClose}>Quay lại</button>
        <button type="button" className="button" ref={confirmRef} disabled={busy} onClick={onConfirm}>
          {busy ? <Loader2 size={16} className="spin" /> : <PowerOff size={16} />}Tắt phân việc tự động
        </button>
      </div>
    </div>
  </div>;
}

// ---------------------------------------------------------------------------
// The result, and the repeat that may follow it
// ---------------------------------------------------------------------------

function ResultDialog({ batch, current, busy, error, onFinish }: { batch: AssignmentProposalBatch; current: ProposalScheduleChoice; busy: boolean; error: string; onFinish: (choice: ProposalScheduleChoice) => void }) {
  const [choice, setChoice] = useState<ProposalScheduleChoice>(current);
  const result = assignedResult(batch);
  const finishRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { finishRef.current?.focus(); }, []);

  return <div className="managerModalBackdrop" role="dialog" aria-modal="true" aria-labelledby="draftResultTitle">
    <div className="managerModalDialog">
      <h3 id="draftResultTitle">Đã phân công {result.ticketCount} ticket cho {result.technicianCount} kỹ thuật viên</h3>
      {result.unassignedCount > 0 && <p>{result.unassignedCount} ticket chưa thể phân công và vẫn nằm trong hàng chờ.</p>}
      {/* Confirming the first eligible proposal is what starts DIRECT. That is
          a large consequence to discover later, so it is said here. */}
      {directActivatedByConfirmation(batch) && <p className="alert warning" role="status">
        <Zap size={14} /><span>{DIRECT_ACTIVATED_MESSAGE}</span>
      </p>}

      <fieldset className="assignScheduleChoices">
        <legend>Tự động tạo đợt phân việc tiếp theo:</legend>
        {SCHEDULE_OPTIONS.map((option) => <label key={option.value}>
          <input
            type="radio"
            name="proposalSchedule"
            value={option.value}
            checked={choice === option.value}
            disabled={busy}
            onChange={() => setChoice(option.value)}
          />
          {option.label}
        </label>)}
        {/* The word that stops this reading as a delay on this batch. */}
        <small>Hệ thống sẽ tạo <strong>đề xuất mới để Ban quản lý duyệt</strong> theo chu kỳ này, không tự phân việc.</small>
      </fieldset>

      {error && <div className="alert error" role="alert">{error}</div>}
      <div className="managerModalActions">
        <button type="button" className="button" ref={finishRef} disabled={busy} onClick={() => onFinish(choice)}>
          {busy ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}Hoàn tất
        </button>
      </div>
    </div>
  </div>;
}
