"use client";

import { AlertTriangle, Clock3, Download, FileJson, FlaskConical, PlayCircle, RotateCcw, ShieldAlert, Timer, TrendingDown, Users } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { runCapacitySimulation } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { ManagerStatCard } from "@/components/manager/DashboardWidgets";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import {
  DECISION_SOURCE_LABELS,
  OUTCOME_LABELS,
  REASON_LABELS,
  RISK_REASON_LABELS,
  SAMPLE_SCENARIO,
  SCENARIO_LABELS,
  SCENARIO_NOTES,
  SCENARIO_ORDER,
  SLA_DURATION_SOURCE_LABELS,
  SLA_POLICY_LABELS,
  SLA_STATUS_LABELS,
  SLA_STATUS_TONES,
  SimulationInputError,
  atRiskTickets,
  buildExportFile,
  comparisonCards,
  excludedSummary,
  formatClock,
  formatCompliance,
  formatMinutes,
  hasSlaOverride,
  lateStartedTickets,
  notStartedTickets,
  parseScenario,
  scenarioOf,
  scenarioSlaPolicy,
} from "@/lib/simulation";
import type { SimulationRun, SimulationScenarioKey, SimulationScenarioResult } from "@/types/api";

/** Mô phỏng công suất & SLA — màn hình duy nhất nơi Ban quản lý hỏi được
 *  "nếu làm cách khác thì sao?" mà không có gì thực sự xảy ra.
 *
 *  Hai lần chạy trên một kịch bản dán vào. Backend không tạo ticket, không tạo
 *  phân công, không tạo dispatch event — nên trang này không có bước xác nhận,
 *  không có optimistic state và không có gì để hoàn tác: kết quả *là* response,
 *  và đóng tab là vứt nó đi.
 *
 *  Ba điều bố cục này chịu trách nhiệm, cả ba đều là chuyện không gây hiểu nhầm:
 *
 *  * **Không cột nào là production.** `NEW_APP` là mô phỏng một chính sách giả
 *    định, và nó nói vậy ở tiêu đề cột, ở ghi chú dưới tiêu đề, và ở banner.
 *    Không có huy hiệu "Production" nào để trao.
 *  * **SLA được tính tại thời điểm bắt đầu sửa.** Mọi nhãn nói "bắt đầu"; thời
 *    gian hoàn tất chỉ xuất hiện ở chỗ nó thật sự có nghĩa — công suất và lịch
 *    kỹ thuật viên.
 *  * **Mọi tỷ lệ đúng hạn đều hiện mẫu số.** "88.9% (8/9 đánh giá được)" cùng
 *    một dòng nói cái gì *không* nằm trong chín đó. Một tỷ lệ giấu mẫu số là
 *    một tỷ lệ cải thiện được bằng cách đánh rơi ticket.
 */
export default function ManagerSimulationPage() {
  const [scenarioText, setScenarioText] = useState(SAMPLE_SCENARIO);
  const [run, setRun] = useState<SimulationRun | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<SimulationScenarioKey>("NEW_APP");
  const resultRef = useRef<HTMLDivElement>(null);

  async function execute() {
    setRunning(true);
    setError("");
    try {
      // Parse ở đây trước để một dấu ngoặc thiếu được báo ngay trên trình soạn
      // thảo mà điều phối viên đang nhìn, trước khi có round trip. Backend kiểm
      // lại đúng tài liệu đó và nó mới là nơi có thẩm quyền.
      const scenario = parseScenario(scenarioText);
      const result = await runCapacitySimulation(scenario);
      setRun(result);
      resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (reason) {
      setRun(null);
      setError(reason instanceof SimulationInputError || reason instanceof Error ? reason.message : "Không chạy được mô phỏng.");
    } finally {
      setRunning(false);
    }
  }

  function exportJson() {
    if (!run) return;
    const { filename, content } = buildExportFile(run);
    const url = URL.createObjectURL(new Blob([content], { type: "application/json;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const draftPolicy = useMemo(() => {
    try {
      return scenarioSlaPolicy(parseScenario(scenarioText)) === "SERVICE_HOURS_DRAFT_V1";
    } catch {
      return false;
    }
  }, [scenarioText]);

  const result = run ? scenarioOf(run, selected) : null;
  const late = useMemo(() => (result ? lateStartedTickets(result) : []), [result]);
  const notStarted = useMemo(() => (result ? notStartedTickets(result) : []), [result]);
  const atRisk = useMemo(() => (result ? atRiskTickets(result) : []), [result]);
  const cards = run ? comparisonCards(run) : [];

  return <RoleShell role="manager" title="So sánh app cũ và app mới" eyebrow="Công cụ hoạch định" subtitle="Cùng một tập ticket và đội KTV, chạy lại theo hai luồng xử lý. Công cụ không tạo hoặc sửa dữ liệu vận hành.">
    <div className="managerPageStack">
      <ManagerSurface
        title="Kịch bản JSON"
        description="Một tài liệu JSON duy nhất: building, sla_policy, settings, technicians, tickets. Dữ liệu chỉ tồn tại trong lần chạy này."
        eyebrow="Đầu vào"
        icon={<FileJson size={19} />}
        actions={<div className="simActions">
          <label className="simUpload">Tải tệp .json<input type="file" accept=".json,application/json" onChange={async (event) => {
            const file = event.target.files?.[0];
            if (file) { setScenarioText(await file.text()); setError(""); }
            event.target.value = "";
          }} /></label>
          <button type="button" className="button secondary small" onClick={() => { setScenarioText(SAMPLE_SCENARIO); setRun(null); setError(""); }}><RotateCcw size={15} />Dữ liệu mẫu</button>
        </div>}
      >
        <div className="field simEditor">
          <textarea value={scenarioText} spellCheck={false} onChange={(event) => setScenarioText(event.target.value)} aria-label="Kịch bản JSON" />
          <p className="helper">Giờ phục vụ 08:00–18:00 mỗi ngày, không nghỉ trưa. Với thang P1–P5 hiện tại, P5 do BQL xử lý tay và chạy 24/7; P3 chỉ còn là mức khẩn cấp trong chính sách cũ để đối chiếu. Tối đa 500 ticket và 200 KTV mỗi lần chạy.</p>
        </div>
        {draftPolicy && <p className="simDraftBanner"><ShieldAlert size={16} />Kịch bản đang dùng <strong>SERVICE_HOURS_DRAFT_V1</strong> — chính sách SLA đề xuất, <strong>chưa áp dụng production</strong>. Hạn SLA trong kết quả không phải hạn đang cam kết với cư dân.</p>}
        <div className="simRunBar">
          <button type="button" className="button" disabled={running} onClick={() => void execute()}><PlayCircle size={16} />{running ? "Đang chạy…" : "Chạy mô phỏng"}</button>
          <button type="button" className="button secondary" disabled={!run} onClick={exportJson}><Download size={16} />Xuất JSON kết quả</button>
        </div>
        {error && <p className="simError"><AlertTriangle size={16} />{error}</p>}
      </ManagerSurface>

      <div ref={resultRef}>
        {run && <div className="managerPageStack">
          {run.warnings.length > 0 && <div className="simWarnings">{run.warnings.map((warning) => <p key={warning}><AlertTriangle size={15} />{warning}</p>)}</div>}

          <section className="managerStatGrid">
            <ManagerStatCard icon={<Timer size={19} />} label={cards[0].label} value={cards[0].value} description={cards[0].description} tone={cards[0].better ? "primary" : "neutral"} />
            <ManagerStatCard icon={<TrendingDown size={19} />} label={cards[1].label} value={cards[1].value} description={cards[1].description} tone={cards[1].better ? "green" : "neutral"} />
            <ManagerStatCard icon={<Clock3 size={19} />} label={cards[2].label} value={cards[2].value} description={cards[2].description} tone={cards[2].better ? "green" : "neutral"} />
            <ManagerStatCard icon={<FlaskConical size={19} />} label="Chính sách SLA" value={run.sla_policy === "WALL_CLOCK_V1" ? "Treo tường" : "Giờ phục vụ"} description={SLA_POLICY_LABELS[run.sla_policy]} tone="neutral" />
          </section>

          <ManagerSurface title="So sánh hai luồng" description={`Cùng một tập ticket, cùng một đội KTV. Kịch bản: ${run.scenario_name}.`} eyebrow="Kết quả" icon={<FlaskConical size={19} />} bodyClassName="managerSurfaceTableBody">
            <p className="simDraftBanner"><ShieldAlert size={16} />
              <span><strong>SLA trong báo cáo này được tính tại thời điểm KTV tới nơi và bắt đầu xử lý.</strong> Thời gian hoàn tất chỉ dùng để tính công suất và lịch KTV.</span>
            </p>
            <div className="tableWrap"><table className="dataTable simCompareTable">
              <thead><tr>
                <th>Chỉ số</th>
                {SCENARIO_ORDER.map((key) => <th key={key}>
                  {SCENARIO_LABELS[key]}
                  <span className="simBadge warn simColBadge">Mô phỏng</span>
                  <small>{SCENARIO_NOTES[key]}</small>
                </th>)}
              </tr></thead>
              <tbody>
                <ComplianceRow run={run} />
                <Row run={run} label="Tổng ticket" pick={(r) => String(r.summary.total_tickets)} />
                <Row run={run} label="Đã phân công" pick={(r) => String(r.summary.assigned_tickets)} />
                <Row run={run} label="Đã bắt đầu xử lý" pick={(r) => String(r.summary.started_tickets)} />
                <Row run={run} label="Đánh giá được SLA (mẫu số)" pick={(r) => String(r.summary.sla_evaluable_tickets)} />
                <Row run={run} label="Bắt đầu đúng hạn" pick={(r) => String(r.summary.sla_on_time_tickets)} />
                <Row run={run} label="Bắt đầu trễ" pick={(r) => String(r.summary.sla_late_started_tickets)} />
                <Row run={run} label="Chưa bắt đầu, đã quá hạn" pick={(r) => String(r.summary.sla_open_overdue_tickets)} />
                <Row run={run} label="Chưa bắt đầu, chưa tới hạn" pick={(r) => String(r.summary.sla_open_not_due_tickets)} />
                <Row run={run} label="Không đánh giá được" pick={(r) => String(r.summary.sla_not_evaluable_tickets)} />
                <Row run={run} label="Trễ thời điểm bắt đầu (tổng)" pick={(r) => formatMinutes(r.summary.total_start_late_minutes)} />
                <Row run={run} label="Phân công không bảo đảm SLA" pick={(r) => String(r.summary.at_risk_tickets)} />
                <Row run={run} label="Phản hồi trung bình (từ lúc gửi)" pick={(r) => formatMinutes(r.summary.average_response_minutes)} />
                <Row run={run} label="Phản hồi P95" pick={(r) => formatMinutes(r.summary.p95_response_minutes)} />
                <Row run={run} label="Thời gian di chuyển (tổng)" pick={(r) => formatMinutes(r.summary.total_travel_minutes)} />
                <Row run={run} label="Thời gian BQL bỏ ra" pick={(r) => formatMinutes(r.summary.bql_effort_minutes)} />
              </tbody>
            </table></div>
            <p className="helper simFootnote">
              “Phản hồi” tính từ lúc cư dân gửi phản ánh nên so sánh được giữa hai luồng: app cũ tiêu tốn {run.settings.old_app.manual_category_minutes + run.settings.old_app.manual_dispatch_minutes} phút xử lý tay <em>trước</em> khi ticket vào hàng đợi.
              Mẫu số đúng hạn gồm ba nhóm: bắt đầu đúng hạn, bắt đầu trễ, và chưa bắt đầu mà đã quá hạn — nhóm thứ ba là vi phạm nên không được để ngoài.
              Mọi ticket chưa bắt đầu được tính đến mốc {formatClock(run.horizon_end)}.
            </p>
          </ManagerSurface>

          <div className="simScenarioTabs" role="tablist" aria-label="Chọn luồng để xem chi tiết">
            {SCENARIO_ORDER.map((key) => <button key={key} type="button" role="tab" aria-selected={selected === key} className={selected === key ? "active" : ""} onClick={() => setSelected(key)}>{SCENARIO_LABELS[key]}</button>)}
          </div>

          {result && <>
            <p className="simDraftBanner"><ShieldAlert size={16} />
              <span>{SCENARIO_NOTES[selected]} — số liệu dưới đây <strong>không mô tả hành vi production</strong>.</span>
            </p>
            {hasSlaOverride(result) && <p className="simDraftBanner">
              <ShieldAlert size={16} />
              <span>Một số ticket dùng <strong>hạn SLA tự đặt</strong> thay vì hạn của chính sách {run.sla_policy}. Tỷ lệ đúng hạn ở đây không phải tỷ lệ theo chính sách đó.</span>
            </p>}

            <ManagerSurface
              title={`Phân công không bảo đảm SLA · ${SCENARIO_LABELS[selected]}`}
              description="Không KTV nào bắt đầu kịp hạn. Việc vẫn được giao — bỏ trống một phản ánh không làm nó biến mất — và hệ thống thật sẽ đồng thời báo BQL và ghi audit."
              eyebrow="Cần chú ý"
              icon={<ShieldAlert size={19} />}
              actions={<span className="managerCountBadge">{atRisk.length} ticket</span>}
              bodyClassName="managerSurfaceTableBody"
            >
              <div className="tableWrap"><table className="dataTable">
                <thead><tr><th>Ticket</th><th>Ưu tiên</th><th>Vị trí</th><th>Dự kiến bắt đầu</th><th>Hạn SLA</th><th>Dự kiến trễ</th><th>KTV</th><th>Thông báo</th><th>Nguồn quyết định</th></tr></thead>
                <tbody>
                  {atRisk.map((ticket) => <tr key={ticket.ticket_id}>
                    <td className="tablePrimary">{ticket.ticket_id}<span className="tableSecondary">{ticket.risk_reason ? RISK_REASON_LABELS[ticket.risk_reason] : ""}</span></td>
                    <td>{ticket.priority}</td>
                    <td>T{ticket.floor} · {ticket.unit}</td>
                    <td>{formatClock(ticket.projected_start_at)}</td>
                    <td>{formatClock(ticket.sla_due_at)}</td>
                    <td><strong className="simLate">{formatMinutes(ticket.projected_start_late_minutes)}</strong></td>
                    <td>{ticket.assigned_technician_id || <span className="unknownValue">—</span>}</td>
                    <td>{ticket.would_notify_bql ? <span className="simBadge warn">Sẽ thông báo BQL</span> : <span className="unknownValue">—</span>}</td>
                    <td><small>{ticket.decision_source ? DECISION_SOURCE_LABELS[ticket.decision_source] : "—"}</small></td>
                  </tr>)}
                  {!atRisk.length && <tr><td colSpan={9}>Mọi phân công trong kịch bản này đều bắt đầu kịp hạn.</td></tr>}
                </tbody>
              </table></div>
            </ManagerSurface>

            <ManagerSurface
              title={`Ticket bắt đầu trễ · ${SCENARIO_LABELS[selected]}`}
              description="KTV có tới nơi, nhưng sau hạn. Trễ bao lâu tính theo đồng hồ của chính sách đang chạy."
              eyebrow="Chi tiết"
              icon={<AlertTriangle size={19} />}
              actions={<span className="managerCountBadge">{late.length} ticket</span>}
              bodyClassName="managerSurfaceTableBody"
            >
              <div className="tableWrap"><table className="dataTable">
                <thead><tr><th>Ticket</th><th>Ưu tiên</th><th>Vị trí</th><th>Hạn SLA</th><th>Bắt đầu di chuyển</th><th>Thời điểm bắt đầu</th><th>Trễ thời điểm bắt đầu</th><th>KTV</th></tr></thead>
                <tbody>
                  {late.map((ticket) => <tr key={ticket.ticket_id}>
                    <td className="tablePrimary">{ticket.ticket_id}<span className="tableSecondary">Sẵn sàng {formatClock(ticket.ready_at)}</span></td>
                    <td>{ticket.priority}</td>
                    <td>T{ticket.floor} · {ticket.unit}</td>
                    <td>{formatClock(ticket.sla_due_at)}</td>
                    <td>{formatClock(ticket.departed_at)}</td>
                    <td>{formatClock(ticket.work_started_at)}</td>
                    <td><strong className="simLate">{formatMinutes(ticket.start_late_minutes)}</strong></td>
                    <td>{ticket.assigned_technician_id || <span className="unknownValue">chưa có</span>}</td>
                  </tr>)}
                  {!late.length && <tr><td colSpan={8}>Không có ticket nào bắt đầu trễ trong kịch bản này.</td></tr>}
                </tbody>
              </table></div>
            </ManagerSurface>

            <ManagerSurface
              title={`Ticket chưa bắt đầu · ${SCENARIO_LABELS[selected]}`}
              description="Tới hết thời gian mô phỏng vẫn chưa ai tới nơi. Cái đã quá hạn là vi phạm và nằm trong mẫu số; cái chưa tới hạn thì không."
              eyebrow="Chi tiết"
              icon={<Users size={19} />}
              actions={<span className="managerCountBadge">{notStarted.length} ticket</span>}
              bodyClassName="managerSurfaceTableBody"
            >
              <div className="tableWrap"><table className="dataTable">
                <thead><tr><th>Ticket</th><th>Ưu tiên</th><th>Vị trí</th><th>Kỹ năng cần</th><th>Kết quả</th><th>Lý do</th><th>Hạn SLA</th><th>Quá hạn đến mốc cuối</th><th>Trạng thái</th></tr></thead>
                <tbody>
                  {notStarted.map((ticket) => <tr key={ticket.ticket_id}>
                    <td className="tablePrimary">{ticket.ticket_id}</td>
                    <td>{ticket.priority}</td>
                    <td>T{ticket.floor} · {ticket.unit}</td>
                    <td>{ticket.required_skill}</td>
                    <td>{OUTCOME_LABELS[ticket.outcome]}</td>
                    <td>{ticket.reason ? REASON_LABELS[ticket.reason] : "—"}</td>
                    <td>{formatClock(ticket.sla_due_at)}</td>
                    <td>{ticket.start_late_minutes ? <strong className="simLate">{formatMinutes(ticket.start_late_minutes)}</strong> : "—"}</td>
                    <td><span className={`simBadge ${SLA_STATUS_TONES[ticket.sla_status]}`}>{SLA_STATUS_LABELS[ticket.sla_status]}</span></td>
                  </tr>)}
                  {!notStarted.length && <tr><td colSpan={9}>Mọi ticket đều đã được bắt đầu xử lý.</td></tr>}
                </tbody>
              </table></div>
            </ManagerSurface>

            <ManagerSurface
              title={`Tải kỹ thuật viên · ${SCENARIO_LABELS[selected]}`}
              description="Tỷ lệ sử dụng = (thời gian sửa + di chuyển) / quỹ giờ làm mà lần chạy này trải qua. Đây là chỗ duy nhất thời gian hoàn tất có nghĩa."
              eyebrow="Chi tiết"
              icon={<Users size={19} />}
              bodyClassName="managerSurfaceTableBody"
            >
              <div className="tableWrap"><table className="dataTable">
                <thead><tr><th>Kỹ thuật viên</th><th>Số ticket</th><th>Thời gian sửa</th><th>Di chuyển</th><th>Tổng bận</th><th>Quỹ giờ</th><th>Tỷ lệ sử dụng</th></tr></thead>
                <tbody>
                  {result.summary.technician_utilization.map((load) => <tr key={load.technician_id}>
                    <td className="tablePrimary">{load.technician_id}</td>
                    <td>{load.assigned_ticket_count}</td>
                    <td>{formatMinutes(load.work_minutes)}</td>
                    <td>{formatMinutes(load.travel_minutes)}</td>
                    <td>{formatMinutes(load.busy_minutes)}</td>
                    <td>{formatMinutes(load.capacity_minutes)}</td>
                    <td><div className="simUtilBar"><span style={{ width: `${Math.min(100, load.utilization_percent)}%` }} /><small>{load.utilization_percent}%</small></div></td>
                  </tr>)}
                  {!result.summary.technician_utilization.length && <tr><td colSpan={7}>Không có kỹ thuật viên khả dụng trong lần chạy này.</td></tr>}
                </tbody>
              </table></div>
            </ManagerSurface>

            <ManagerSurface title={`Toàn bộ ticket · ${SCENARIO_LABELS[selected]}`} description="Ba mốc tách rời: rời việc trước, tới nơi và bắt đầu (mốc SLA), rồi sửa xong." eyebrow="Chi tiết" icon={<Clock3 size={19} />} actions={<span className="managerCountBadge">{result.tickets.length} ticket</span>} bodyClassName="managerSurfaceTableBody">
              <div className="tableWrap"><table className="dataTable">
                <thead><tr><th>Ticket</th><th>Ưu tiên</th><th>Sẵn sàng</th><th>Bắt đầu di chuyển</th><th>Thời điểm bắt đầu</th><th>Hoàn tất</th><th>Hạn SLA</th><th>Di chuyển</th><th>Trễ thời điểm bắt đầu</th><th>Trạng thái</th><th>KTV</th><th>Nguồn quyết định</th></tr></thead>
                <tbody>
                  {result.tickets.map((ticket) => <tr key={ticket.ticket_id}>
                    <td className="tablePrimary">{ticket.ticket_id}<span className="tableSecondary">T{ticket.floor} · {ticket.unit}</span></td>
                    <td>{ticket.priority}</td>
                    <td>{formatClock(ticket.ready_at)}</td>
                    <td>{formatClock(ticket.departed_at)}</td>
                    <td>{formatClock(ticket.work_started_at)}</td>
                    <td>{formatClock(ticket.completed_at)}</td>
                    <td>
                      {formatClock(ticket.sla_due_at)}
                      <span className="tableSecondary">{ticket.sla_minutes} phút · {SLA_DURATION_SOURCE_LABELS[ticket.sla_duration_source]}</span>
                    </td>
                    <td>{formatMinutes(ticket.travel_minutes)}</td>
                    <td>{ticket.start_late_minutes ? <strong className="simLate">{formatMinutes(ticket.start_late_minutes)}</strong> : "—"}</td>
                    <td><span className={`simBadge ${SLA_STATUS_TONES[ticket.sla_status]}`}>{SLA_STATUS_LABELS[ticket.sla_status]}</span></td>
                    <td>{ticket.assigned_technician_id || <span className="unknownValue">—</span>}</td>
                    <td><small>{ticket.decision_source ? DECISION_SOURCE_LABELS[ticket.decision_source] : "—"}</small></td>
                  </tr>)}
                </tbody>
              </table></div>
            </ManagerSurface>
          </>}
        </div>}
      </div>
    </div>
  </RoleShell>;
}

/** Dòng tỷ lệ đúng hạn, không bao giờ hiện tỷ lệ mà thiếu mẫu số. */
function ComplianceRow({ run }: { run: SimulationRun }) {
  return <tr className="simComplianceRow">
    <td className="tablePrimary">Tỷ lệ bắt đầu đúng hạn</td>
    {SCENARIO_ORDER.map((key) => {
      const result = scenarioOf(run, key);
      const excluded = excludedSummary(result);
      return <td key={key}>
        <strong>{formatCompliance(result)}</strong>
        {excluded && <span className="tableSecondary">Ngoài mẫu số: {excluded}</span>}
      </td>;
    })}
  </tr>;
}

function Row({ run, label, pick }: { run: SimulationRun; label: string; pick: (result: SimulationScenarioResult) => string }) {
  return <tr>
    <td className="tablePrimary">{label}</td>
    {SCENARIO_ORDER.map((key) => <td key={key}>{pick(scenarioOf(run, key))}</td>)}
  </tr>;
}
