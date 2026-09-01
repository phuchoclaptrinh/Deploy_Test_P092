"use client";

import { Check, ChevronDown, ChevronRight, Building2, Eye, KeyRound, Plus, Power, Search, UserPlus, X } from "lucide-react";
import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { createCoordinatorResident, listCoordinatorResidents, listManagerLocations, resetCoordinatorResidentPassword, setCoordinatorResidentActive } from "@/api/backend.api";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { RoleShell } from "@/components/RoleShell";
import { buildAccountEmailFromName } from "@/lib/accountEmail";
import type { CoordinatorResidentSummary, LocationItem } from "@/types/api";

export default function ManagerResidentsPage() {
  const [residents, setResidents] = useState<CoordinatorResidentSummary[]>([]);
  const [locations, setLocations] = useState<LocationItem[]>([]);
  const [credentials, setCredentials] = useState<Record<string, AccountCredential>>({});
  const [selectedCredential, setSelectedCredential] = useState<AccountCredential | null>(null);
  const [query, setQuery] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [residentRows, locationRows] = await Promise.all([listCoordinatorResidents(), listManagerLocations()]);
      setResidents(residentRows);
      setLocations(locationRows);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tải được dữ liệu cư dân.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const unitOptions = useMemo(() => {
    const map = new Map<string, ResidentUnitOption>();
    locations.forEach((location) => {
      if (!location.unit_code) return;
      const key = location.unit_code;
      if (!map.has(key)) map.set(key, {
        unit_code: location.unit_code,
        floor_code: location.floor_code,
        floor_display_name: location.floor_display_name || `Tầng ${location.floor_code}`,
        label: location.unit_code,
      });
    });
    return [...map.values()].sort(compareResidentUnitOptions);
  }, [locations]);

  const visible = residents.filter((resident) => `${resident.full_name || ""} ${resident.phone_e164 || ""} ${resident.unit_code || ""}`.toLowerCase().includes(query.trim().toLowerCase()));

  async function createResident(input: { full_name: string; phone?: string | null; unit_code: string }) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const created = await createCoordinatorResident(input);
      const credential = { fullName: input.full_name, email: created.email || "", password: created.temporary_password || undefined, roleLabel: "Cư dân" };
      setCredentials((items) => ({ ...items, [created.user_id]: credential }));
      setSelectedCredential(credential);
      setNotice("Đã tạo tài khoản cư dân và gán căn hộ.");
      setShowForm(false);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không tạo được tài khoản cư dân.");
    } finally {
      setBusy(false);
    }
  }

  async function resetResidentPassword(resident: CoordinatorResidentSummary) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await resetCoordinatorResidentPassword(resident.user_id);
      const credential = {
        fullName: resident.full_name || "Cư dân",
        email: result.email || buildAccountEmailFromName(resident.full_name || "Cư dân"),
        password: result.temporary_password || undefined,
        roleLabel: "Cư dân",
      };
      setCredentials((items) => ({ ...items, [resident.user_id]: credential }));
      setSelectedCredential(credential);
      setNotice("Đã cấp lại mật khẩu cư dân.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không cấp lại được mật khẩu cư dân.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleResidentActive(resident: CoordinatorResidentSummary) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await setCoordinatorResidentActive(resident.user_id, !resident.is_active);
      setResidents((items) => items.map((item) => item.user_id === resident.user_id ? { ...item, is_active: result.is_active } : item));
      setNotice(result.is_active ? "Đã mở khóa tài khoản cư dân." : "Đã khóa tài khoản cư dân.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không cập nhật được trạng thái cư dân.");
    } finally {
      setBusy(false);
    }
  }

  return <RoleShell role="manager" title="Quản lý cư dân" subtitle="Tạo tài khoản đăng nhập và gán cư dân với căn hộ.">
    <div className="managerPageStack managerResidentPage">
      <div className="managerTechnicianToolbar">
        <label className="managerHeaderSearch"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm tên, căn hộ, số điện thoại..." /></label>
        <button className="button" onClick={() => setShowForm((value) => !value)}>{showForm ? <X size={16} /> : <Plus size={16} />}{showForm ? "Đóng form" : "Thêm cư dân"}</button>
      </div>

      {notice && <div className="alert success">{notice}</div>}
      {error && <div className="alert error">{error}</div>}

      {showForm && <AccountCreateModal title="Thêm cư dân" description="Nhập họ tên để hệ thống tự sinh email đăng nhập." icon={<UserPlus size={18} />} onClose={() => setShowForm(false)}>
        <ResidentForm units={unitOptions} busy={busy} onDone={createResident} />
      </AccountCreateModal>}

      <ManagerSurface title="Danh sách cư dân" description="Các tài khoản cư dân đã được liên kết căn hộ." icon={<Building2 size={19} />} actions={<span className="managerCountBadge">{visible.length} cư dân</span>} bodyClassName="managerSurfaceTableBody">
        {loading ? <div className="emptyState"><span className="spinner" /><h3>Đang tải cư dân...</h3></div> : visible.length === 0 ? <div className="managerTechnicianEmpty"><Search size={22} /><strong>Không có cư dân phù hợp</strong><span>Thử đổi từ khóa hoặc tạo tài khoản mới.</span></div> : <div className="tableWrap"><table className="dataTable managerResidentTable"><thead><tr><th>Cư dân</th><th>Căn hộ</th><th>Liên hệ</th><th>Trạng thái</th><th>Vai trò</th><th>Đăng nhập</th></tr></thead><tbody>{visible.map((resident) => {
          const credential = credentials[resident.user_id] || buildCredentialPreview(resident.full_name, "Cư dân");
          return <tr key={resident.user_id}><td><strong>{resident.full_name || "Cư dân"}</strong><small>#{resident.user_id.slice(0, 8).toUpperCase()}</small></td><td>{resident.unit_code || "Chưa gán"}</td><td>{resident.phone_e164 || credential.email}</td><td><span className={`managerActiveState ${resident.is_active ? "on" : "off"}`}>{resident.is_active ? "Đang hoạt động" : "Tạm khóa"}</span></td><td>{resident.is_primary ? "Chủ hộ" : "Cư dân"}</td><td><span className="managerAccountActions"><button type="button" className="tableAction managerAccountActionButton" onClick={() => setSelectedCredential(credential)}><Eye size={14} />Xem</button><button type="button" className="tableAction managerAccountActionButton" disabled={busy} onClick={() => resetResidentPassword(resident)}><KeyRound size={14} />Cấp lại MK</button><button type="button" className={`tableAction managerAccountActionButton ${resident.is_active ? "dangerHover" : "successHover"}`} disabled={busy} onClick={() => toggleResidentActive(resident)}><Power size={14} />{resident.is_active ? "Tạm khóa" : "Mở khóa"}</button></span></td></tr>;
        })}</tbody></table></div>}
      </ManagerSurface>
      {selectedCredential && <CredentialDialog credential={selectedCredential} onClose={() => setSelectedCredential(null)} />}
    </div>
  </RoleShell>;
}

type AccountCredential = { fullName: string; email: string; password?: string; roleLabel: string };
type ResidentUnitOption = {
  unit_code: string;
  floor_code: string;
  floor_display_name: string;
  label: string;
};
type ResidentFloorGroup = { floor_code: string; floor_display_name: string; units: ResidentUnitOption[] };

function compareResidentUnitOptions(a: ResidentUnitOption, b: ResidentUnitOption) {
  const floor = Number(a.floor_code) - Number(b.floor_code);
  if (Number.isFinite(floor) && floor !== 0) return floor;
  const floorText = a.floor_code.localeCompare(b.floor_code, "vi", { numeric: true });
  if (floorText !== 0) return floorText;
  return a.unit_code.localeCompare(b.unit_code, "vi", { numeric: true });
}

function AccountCreateModal({ title, description, icon, children, onClose }: { title: string; description: string; icon: ReactNode; children: ReactNode; onClose: () => void }) {
  return <div className="modalBackdrop" role="presentation" onMouseDown={onClose}>
    <section className="managerModal managerAccountCreateModal" role="dialog" aria-modal="true" aria-label={title} onMouseDown={(event) => event.stopPropagation()}>
      <header>
        <div className="managerAccountCreateHeading"><span>{icon}</span><div><h2>{title}</h2><p>{description}</p></div></div>
        <button type="button" onClick={onClose} aria-label="Đóng"><X size={18} /></button>
      </header>
      <div className="managerAccountCreateBody">{children}</div>
    </section>
  </div>;
}

function buildCredentialPreview(fullName: string | null, roleLabel: string): AccountCredential {
  const name = fullName || roleLabel;
  return { fullName: name, email: buildAccountEmailFromName(name), roleLabel };
}

function CredentialDialog({ credential, onClose }: { credential: AccountCredential; onClose: () => void }) {
  return <div className="modalBackdrop" role="presentation" onMouseDown={onClose}>
    <div className="managerModal managerCredentialModal" role="dialog" aria-modal="true" aria-label="Thông tin đăng nhập" onMouseDown={(event) => event.stopPropagation()}>
      <header><h2>Thông tin đăng nhập</h2><button type="button" onClick={onClose} aria-label="Đóng"><X size={18} /></button></header>
      <div className="managerCredentialBody">
        <div><span>Tài khoản</span><strong>{credential.fullName}</strong><small>{credential.roleLabel}</small></div>
        <div><span>Email</span><strong>{credential.email || "Chưa có email"}</strong></div>
        <div><span>Mật khẩu</span><strong>{credential.password || "Không lưu mật khẩu cũ"}</strong><small>{credential.password ? "Mật khẩu tạm vừa được BQL cấp." : "Hãy cấp lại mật khẩu nếu cần gửi cho người dùng."}</small></div>
      </div>
    </div>
  </div>;
}

function ResidentForm({ units, busy, onDone }: { units: ResidentUnitOption[]; busy: boolean; onDone: (input: { full_name: string; phone?: string | null; unit_code: string }) => void }) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [unitKey, setUnitKey] = useState("");
  const [unitPickerOpen, setUnitPickerOpen] = useState(false);
  const [activeFloor, setActiveFloor] = useState("");
  const selected = units.find((unit) => unit.unit_code === unitKey);
  const unitTree = useMemo(() => {
    const floorMap = new Map<string, ResidentFloorGroup>();
    units.forEach((unit) => {
      if (!floorMap.has(unit.floor_code)) floorMap.set(unit.floor_code, { floor_code: unit.floor_code, floor_display_name: unit.floor_display_name, units: [] });
      floorMap.get(unit.floor_code)?.units.push(unit);
    });
    return [...floorMap.values()]
      .map((floor) => ({ ...floor, units: floor.units.sort(compareResidentUnitOptions) }))
      .sort((a, b) => {
        const numeric = Number(a.floor_code) - Number(b.floor_code);
        return Number.isFinite(numeric) && numeric !== 0 ? numeric : a.floor_code.localeCompare(b.floor_code, "vi", { numeric: true });
      });
  }, [units]);
  const selectedFloor = unitTree.find((floor) => floor.floor_code === activeFloor);
  const selectedLabel = selected ? `${selected.floor_display_name} · ${selected.unit_code}` : "";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!selected) return;
    onDone({ full_name: name.trim(), phone: phone.trim() || null, unit_code: selected.unit_code });
  };
  const updateName = (value: string) => {
    setName(value);
    setEmail(buildAccountEmailFromName(value));
  };
  const chooseFloor = (floorCode: string) => {
    setActiveFloor(floorCode);
  };
  const chooseUnit = (unit: ResidentUnitOption) => {
    setUnitKey(unit.unit_code);
    setActiveFloor(unit.floor_code);
    setUnitPickerOpen(false);
  };
  return <form className="managerInlineCreate managerResidentCreate" onSubmit={submit}>
    <div className="field"><label>Họ tên</label><input value={name} onChange={(event) => updateName(event.target.value)} placeholder="Nguyễn Văn A" required /></div>
    <div className="field"><label>Email dự kiến</label><input className="managerEmailInput" type="email" value={email} readOnly /></div>
    <div className="managerGeneratedPasswordNote"><span>Mật khẩu</span><strong>Backend tự sinh 6 ký tự</strong></div>
    <div className="field managerNestedUnitPicker"><label>Căn hộ</label><button type="button" className={`managerNestedUnitTrigger ${selected ? "selected" : ""}`} onClick={() => setUnitPickerOpen(true)}><span>{selectedLabel || "Chọn tầng, căn hộ"}</span><ChevronDown size={16} /></button>{unitPickerOpen && <div className="managerUnitPickerOverlay" role="presentation" onMouseDown={() => setUnitPickerOpen(false)}>
      <section className="managerUnitPickerDialog" role="dialog" aria-modal="true" aria-label="Chọn căn hộ" onMouseDown={(event) => event.stopPropagation()}>
        <header><div><h3>Chọn căn hộ</h3><p>Chọn lần lượt tầng rồi căn hộ.</p></div><button type="button" onClick={() => setUnitPickerOpen(false)} aria-label="Đóng"><X size={18} /></button></header>
        <div className="managerUnitPickerGrid">
          <div className="managerNestedUnitColumn"><small>Tầng</small>{unitTree.map((floor) => <button type="button" key={floor.floor_code} className={activeFloor === floor.floor_code ? "active" : ""} onClick={() => chooseFloor(floor.floor_code)}><span>{floor.floor_display_name}</span><ChevronRight size={14} /></button>)}</div>
          <div className="managerNestedUnitColumn"><small>Căn hộ</small>{selectedFloor ? selectedFloor.units.map((unit) => <button type="button" key={unit.unit_code} className={unitKey === unit.unit_code ? "active" : ""} onClick={() => chooseUnit(unit)}><span>{unit.unit_code}</span>{unitKey === unit.unit_code && <Check size={14} />}</button>) : <span className="managerUnitPickerHint">Chọn tầng trước</span>}</div>
        </div>
      </section>
    </div>}</div>
    <div className="field"><label>Số điện thoại</label><input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="0290329832" /></div>
    <button className="button" type="submit" disabled={busy || !selected || !email}><KeyRound size={16} />{busy ? "Đang tạo..." : "Tạo tài khoản"}</button>
  </form>;
}
