"use client";

import { AlertTriangle, ArrowLeft, CheckCircle2, Clock, Layers3, Loader2, RotateCcw, UserCog } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { confirmVisualAssignment, getVisualAssignmentBoard } from "@/api/backend.api";
import { formatCategoryName } from "@/lib/category";
import { formatDateTime } from "@/lib/managerTicket";
import {
  POOL,
  advisoryWarnings,
  blockingWarnings,
  canConfirm,
  canPlace,
  columnLoad,
  confirmFailureMessage,
  confirmSummary,
  emptyDraft,
  failedUnitIds,
  hoursLabel,
  orderedTechnicians,
  placementsOf,
  reconcileDraft,
  riskyPlacements,
  slackLabel,
  unitsInColumn,
  warningLabel,
} from "@/lib/visualAssignment";
import type { Draft } from "@/lib/visualAssignment";
import type { BoardTechnician, BoardUnit, VisualBoard, VisualPlacementFailure } from "@/types/api";

type Props = {
  onClose: () => void;
  onOpenTicket: (ticketId: string) => void;
  onAssignmentsChanged: () => void;
};

/** §1's board.
 *
 *  The pool on the left, one column per technician on the right, and one confirm
 *  at the bottom. Three things it deliberately does not do:
 *
 *  * **It does not propose anyone.** There is no ranking, no suggestion and no
 *    "AI recommends" row — §1 removes all three. The columns are ordered by
 *    availability so the free technician is easy to find, and that is the only
 *    opinion the screen has.
 *  * **It does not allow an invalid drop.** Skill, availability and shift are §3
 *    hard constraints and the bulk confirm rejects them, so the board refuses
 *    the drop rather than letting a manager discover it at the end. Overload and
 *    schedule risk are *not* §3 constraints and are shown as warnings on a card
 *    the manager may still confirm.
 *  * **It does not split a group.** A cluster arrives as one unit with several
 *    members, and there is no interaction that could separate them.
 */
export function VisualAssignmentBoard({ onClose, onOpenTicket, onAssignmentsChanged }: Props) {
  const [board, setBoard] = useState<VisualBoard | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [dragging, setDragging] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [failedUnits, setFailedUnits] = useState<string[]>([]);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const load = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const next = await getVisualAssignmentBoard();
      if (!mounted.current) return;
      setBoard(next);
      // Placements survive a refresh, but only for units still on the board:
      // one that vanished was taken by someone else, and confirming it would be
      // confirming something the manager can no longer see.
      setDraft((current) => (Object.keys(current).length ? reconcileDraft(current, next) : emptyDraft(next)));
      setError("");
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "Không tải được bảng phân việc.");
    } finally {
      if (mounted.current && showLoading) setLoading(false);
    }
  }, []);
  useEffect(() => { void load(true); }, [load]);

  const technicians = useMemo(() => orderedTechnicians(board), [board]);
  const pool = useMemo(() => unitsInColumn(board, draft, POOL), [board, draft]);
  const risky = useMemo(() => riskyPlacements(board, draft), [board, draft]);
  const ready = canConfirm(board, draft);

  const place = (unitId: string, columnId: string) => {
    const unit = board?.units.find((item) => item.unit_id === unitId);
    if (!unit || !canPlace(unit, columnId)) return;
    setDraft((current) => ({ ...current, [unitId]: columnId }));
    setFailedUnits((current) => current.filter((id) => id !== unitId));
    setNotice("");
  };

  const drop = (columnId: string) => (event: React.DragEvent) => {
    event.preventDefault();
    setHover(null);
    const unitId = event.dataTransfer.getData("text/plain") || dragging;
    if (unitId) place(unitId, columnId);
    setDragging(null);
  };

  const dragOver = (columnId: string) => (event: React.DragEvent) => {
    const unit = board?.units.find((item) => item.unit_id === dragging);
    // Refusing the drag-over is what makes the browser show "not allowed"
    // rather than letting the drop land and bounce back.
    if (dragging && unit && !canPlace(unit, columnId)) return;
    event.preventDefault();
    setHover(columnId);
  };

  const confirm = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await confirmVisualAssignment(placementsOf(draft));
      if (!mounted.current) return;
      setNotice(`Đã phân ${result.assigned_ticket_count} phản ánh cho kỹ thuật viên.`);
      setFailedUnits([]);
      setDraft({});
      await load();
      onAssignmentsChanged();
    } catch (reason) {
      if (!mounted.current) return;
      const failures = (reason as { details?: { failures?: VisualPlacementFailure[] } })?.details?.failures;
      // Nothing was written, so the draft is left exactly as it was and the
      // offending cards are marked instead of being silently reset.
      setFailedUnits(failedUnitIds(failures));
      setError(failures ? confirmFailureMessage(failures) : reason instanceof Error ? reason.message : "Không phân việc được.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const reset = () => { setDraft(emptyDraft(board)); setFailedUnits([]); setNotice(""); setError(""); };

  const card = (unit: BoardUnit, columnId: string) => {
    const advisory = columnId === POOL ? [] : advisoryWarnings(unit, columnId);
    const failed = failedUnits.includes(unit.unit_id);
    return (
      <article
        key={unit.unit_id}
        className={`draftCard${dragging === unit.unit_id ? " selected" : ""}${failed ? " vaCardFailed" : ""}`}
        draggable
        onDragStart={(event) => { event.dataTransfer.setData("text/plain", unit.unit_id); setDragging(unit.unit_id); }}
        onDragEnd={() => { setDragging(null); setHover(null); }}
        aria-label={`${unit.display_codes.join(", ")} — ${formatCategoryName(unit.category_code)}`}
      >
        <div className="draftCardHead">
          {unit.priority && <span className={`mdPriority mdPriority-${unit.priority}`}><i aria-hidden="true" />{unit.priority}</span>}
          {unit.unit_type === "GROUP" && (
            <span className="vaGroupBadge"><Layers3 size={12} aria-hidden="true" />Cụm {unit.member_count} phản ánh</span>
          )}
          <span className="draftCardMeta">{hoursLabel(unit.p80_seconds)}</span>
        </div>
        <ul className="draftCardMembers">
          {unit.ticket_ids.map((ticketId, index) => (
            <li key={ticketId}>
              <button type="button" className="draftCardTicket" onClick={() => onOpenTicket(ticketId)}>
                <span className="draftCardTop">
                  <strong>{unit.display_codes[index]}</strong>
                  <span className="draftCardMeta">{formatCategoryName(unit.category_code)}</span>
                </span>
                <span className="draftCardMeta">{unit.location_labels.join(", ") || "Chưa xác định vị trí"}</span>
              </button>
            </li>
          ))}
        </ul>
        {advisory.length > 0 && (
          <ul className="vaWarnings">
            {advisory.map((code) => (
              <li key={code}><AlertTriangle size={12} aria-hidden="true" />{warningLabel(code)}</li>
            ))}
          </ul>
        )}
        {/* A keyboard path to the same decision: drag and drop alone would put
            the whole screen out of reach for anyone not using a mouse. */}
        <label className="draftCardMove">
          <span className="srOnly">Chuyển {unit.display_codes.join(", ")} sang kỹ thuật viên</span>
          <select value={columnId} onChange={(event) => place(unit.unit_id, event.target.value)}>
            <option value={POOL}>Chưa phân công</option>
            {technicians.map((technician) => (
              <option key={technician.technician_id} value={technician.technician_id} disabled={!canPlace(unit, technician.technician_id)}>
                {technician.display_name || "Kỹ thuật viên"}
                {canPlace(unit, technician.technician_id) ? "" : ` — ${blockingWarnings(unit, technician.technician_id).map(warningLabel).join(", ")}`}
              </option>
            ))}
          </select>
        </label>
      </article>
    );
  };

  const column = (technician: BoardTechnician) => {
    const units = unitsInColumn(board, draft, technician.technician_id);
    const load = columnLoad(board, draft, technician);
    const blocked = !technician.is_active || !technician.is_available;
    return (
      <section
        key={technician.technician_id}
        className={`draftColumn draftTechnician${hover === technician.technician_id ? " droppable" : ""}${blocked ? " vaColumnBlocked" : ""}`}
        onDragOver={dragOver(technician.technician_id)}
        onDragLeave={() => setHover(null)}
        onDrop={drop(technician.technician_id)}
      >
        <header>
          <UserCog size={14} aria-hidden="true" />
          <strong>{technician.display_name || "Kỹ thuật viên"}</strong>
          <span>{load.current} việc đang giữ{load.adding ? ` · +${load.adding}` : ""}</span>
        </header>
        <p className="vaColumnMeta">
          {blocked
            ? "Không sẵn sàng nhận việc"
            : technician.day_ends_at
              ? <><Clock size={11} aria-hidden="true" /> Lịch hiện tới {formatDateTime(technician.day_ends_at)}</>
              : "Chưa có việc nào trong lịch"}
          {load.hours > 0 && <span className="vaColumnAdding"> · thêm {hoursLabel(load.hours * 3600)}</span>}
        </p>
        <div className="draftColumnItems">
          {units.map((unit) => card(unit, technician.technician_id))}
          {units.length === 0 && <p className="vaColumnEmpty">Kéo công việc vào đây</p>}
        </div>
      </section>
    );
  };

  return (
    <div className="mdBoard assignmentWorkspaceContent">
      <div className="mdBoardHead">
        <button type="button" className="clusterBackButton" onClick={onClose} aria-label="Quay lại danh sách ticket"><ArrowLeft size={17} /></button>
        <div className="mdBoardTitle">
          <h2>Phân việc trực quan</h2>
          <span>{pool.length} nhóm việc chờ phân công</span>
        </div>
        <button type="button" className="button secondary" onClick={reset} disabled={busy}><RotateCcw size={16} />Đặt lại</button>
      </div>

      {board && !board.within_working_shift && (
        <div className="alert warning mdAlert">
          Ngoài ca làm việc (08:00–18:00). Không thể phân công cho tới ca tiếp theo.
        </div>
      )}
      {error && <div className="alert error mdAlert">{error}</div>}
      {notice && <div className="alert success mdAlert"><CheckCircle2 size={16} />{notice}</div>}

      {loading ? (
        <section className="mdCard mdCardState"><div className="spinner" /><h3>Đang tải bảng phân việc...</h3></section>
      ) : (
        <>
          <div className="draftBoard">
            <section
              className={`draftColumn${hover === POOL ? " droppable" : ""}`}
              onDragOver={dragOver(POOL)}
              onDragLeave={() => setHover(null)}
              onDrop={drop(POOL)}
            >
              <header>
                <Layers3 size={14} aria-hidden="true" />
                <strong>Chờ phân công</strong>
                <span>{pool.length}</span>
              </header>
              <div className="draftColumnItems">
                {pool.map((unit) => card(unit, POOL))}
                {pool.length === 0 && <p className="vaColumnEmpty">Không còn công việc nào chờ phân công.</p>}
              </div>
            </section>
            <div className="draftTechnicians">
              {technicians.map(column)}
              {technicians.length === 0 && <p className="vaColumnEmpty">Chưa có kỹ thuật viên nào.</p>}
            </div>
          </div>

          <div className="draftActionBar">
            <p className="draftActionSummary">
              {confirmSummary(board, draft)}
              {risky > 0 && <small>{risky} vị trí có cảnh báo lịch. Bạn vẫn có thể xác nhận.</small>}
            </p>
            <div className="draftActionButtons">
              <button type="button" className="button" onClick={confirm} disabled={!ready || busy}>
                {busy ? <><Loader2 size={16} className="spin" />Đang phân việc...</> : <><CheckCircle2 size={16} />Xác nhận phân việc</>}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
