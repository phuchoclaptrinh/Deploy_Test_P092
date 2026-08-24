"use client";

import { Eye, KeyRound, Plus, Search, Trash2, UserRoundCheck, X } from "lucide-react";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import { createCoordinatorTechnician, deleteCoordinatorTechnician, listBackendCategories, listCoordinatorTechnicians, resetCoordinatorTechnicianPassword } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { buildAccountEmailFromName } from "@/lib/accountEmail";
import { formatCategoryName } from "@/lib/category";
import type { CoordinatorCategory, TechnicianSummary } from "@/types/api";

export default function ManagerTechniciansPage() {
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [showForm, setShowForm] = useState(false);
  const [all, setAll] = useState<TechnicianSummary[]>([]), [categoryRows, setCategoryRows] = useState<CoordinatorCategory[]>([]), [mockNames, setMockNames] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<Record<string, AccountCredential>>({});
  const [selectedCredential, setSelectedCredential] = useState<AccountCredential | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { Promise.all([listCoordinatorTechnicians(), listBackendCategories()]).then(([roster, categories]) => { setAll(roster); setCategoryRows(categories); setError(""); }).catch((reason) => setError(reason instanceof Error ? reason.message : "Không tải được dữ liệu kỹ thuật viên.")); }, []);
  const categoryName = (id: string) => { const category = categoryRows.find((row) => row.id === id); return category ? formatCategoryName(category.code, category.display_name) : id.slice(0, 8); };
  const technicians = all.filter((item) => `${mockNames[item.user_id] || item.full_name || ""} ${item.phone_e164 || ""} ${item.skill_category_ids.map(categoryName).join(" ")}`.toLowerCase().includes(query.toLowerCase()) && (categoryFilter === "all" || item.skill_category_ids.includes(categoryFilter)));
  async function resetTechnicianPassword(technician: TechnicianSummary) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await resetCoordinatorTechnicianPassword(technician.user_id);
      const name = mockNames[technician.user_id] || technician.full_name || "Kỹ thuật viên";
      const credential = { fullName: name, email: result.email || buildAccountEmailFromName(name), password: result.temporary_password || undefined, roleLabel: "Kỹ thuật viên" };
      setCredentials((items) => ({ ...items, [technician.user_id]: credential }));
      setSelectedCredential(credential);
      setNotice("Đã cấp lại mật khẩu KTV.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không cấp lại được mật khẩu KTV.");
    } finally {
      setBusy(false);
    }
  }

  async function deleteTechnician(technician: TechnicianSummary) {
    const name = mockNames[technician.user_id] || technician.full_name || "kỹ thuật viên này";
    if (!window.confirm(`Xóa tài khoản ${name}? Chỉ xóa được tài khoản chưa có lịch sử phân công.`)) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await deleteCoordinatorTechnician(technician.user_id);
      setAll((items) => items.filter((item) => item.user_id !== technician.user_id));
      setNotice("Đã xóa tài khoản kỹ thuật viên.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không xóa được kỹ thuật viên.");
    } finally {
      setBusy(false);
    }
  }

  return <RoleShell role="manager" title="Danh sách kỹ thuật viên" subtitle="Quản lý chuyên môn và khả năng tiếp nhận công việc.">
    <div className="managerPageStack managerTechnicianPage">
      <div className="managerTechnicianToolbar">
        <div className="managerTechnicianFilters">
          <label className="managerHeaderSearch"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Tìm tên, số điện thoại, chuyên môn..." /></label>
          <select aria-label="Lọc theo danh mục" value={categoryFilter} onChange={(event) => setCategoryFilter(event.target.value)}><option value="all">Tất cả danh mục</option>{categoryRows.map((item) => <option key={item.id} value={item.id}>{formatCategoryName(item.code, item.display_name)}</option>)}</select>
        </div>
        <button className="button" onClick={() => setShowForm((value) => !value)}>{showForm ? <X size={16} /> : <Plus size={16} />}{showForm ? "Đóng form" : "Thêm KTV"}</button>
      </div>
      {notice && <div className="alert success">{notice}</div>}
      {error && <div className="alert error">{error}</div>}
      {showForm && <AccountCreateModal title="Thêm KTV" description="Nhập họ tên, số điện thoại và chuyên môn của kỹ thuật viên." icon={<UserRoundCheck size={18} />} onClose={() => setShowForm(false)}><TechnicianForm categories={categoryRows} busy={busy} onDone={async (input) => { setBusy(true); setError(""); setNotice(""); try { const created = await createCoordinatorTechnician(input); setAll((items) => [{ user_id: created.user_id, full_name: created.full_name, phone_e164: created.phone_e164, is_active: created.is_active, is_available: created.is_available ?? true, skill_category_ids: created.skill_category_ids }, ...items]); setMockNames((items) => ({ ...items, [created.user_id]: created.full_name || input.full_name })); setNotice("Đã tạo tài khoản KTV. Mật khẩu ban đầu không được hiển thị; hãy cấp lại khi cần."); setShowForm(false); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không tạo được tài khoản KTV."); } finally { setBusy(false); } }} /></AccountCreateModal>}
      <section className="managerTechnicianGrid">
        {technicians.map((technician) => { const name = mockNames[technician.user_id] || technician.full_name || "Kỹ thuật viên"; return <article className={`managerTechnicianCard${technician.is_active ? "" : " inactive"}`} key={technician.user_id}>
          <div className="managerTechnicianProfile">
            <span className="managerTechnicianAvatar" aria-label={`Avatar ${name}`}><strong>{getInitials(name)}</strong><i className={!technician.is_active ? "locked" : technician.is_available ? "online" : "busy"} aria-hidden="true" /></span>
            <h2>{name}</h2><p>{technician.phone_e164 || "Chưa có số điện thoại"}</p>
            <b className={`managerTechnicianStatus ${!technician.is_active ? "locked" : technician.is_available ? "available" : "busy"}`}>{!technician.is_active ? "Tạm khóa" : technician.is_available ? "Đang rảnh" : "Đang bận"}</b>
          </div>
          <div className="managerTechnicianSpecialties">
            {technician.skill_category_ids.slice(0, 3).map((item) => <span key={item}>{categoryName(item)}</span>)}
            {technician.skill_category_ids.length > 3 && <span>+{technician.skill_category_ids.length - 3} chuyên môn</span>}
          </div>
          <footer><button type="button" className="tableAction managerAccountActionButton" onClick={() => setSelectedCredential({ ...(credentials[technician.user_id] || buildCredentialPreview(name, "Kỹ thuật viên")), skills: technician.skill_category_ids.map(categoryName) })}><Eye size={14} />Xem</button><button type="button" className="tableAction managerAccountActionButton" disabled={busy} onClick={() => resetTechnicianPassword(technician)}><KeyRound size={14} />Cấp lại MK</button><button type="button" className="tableAction managerAccountActionButton dangerHover" disabled={busy} onClick={() => void deleteTechnician(technician)}><Trash2 size={14} />Xóa</button></footer>
        </article>; })}
        {technicians.length === 0 && <div className="managerTechnicianEmpty"><Search size={22} /><strong>Không tìm thấy kỹ thuật viên</strong><span>Thử đổi từ khóa hoặc danh mục đang lọc.</span></div>}
        <button type="button" className="managerAddTechnicianCard" onClick={() => setShowForm(true)}>
          <span><Plus size={23} /></span><strong>Thêm KTV</strong><small>Tạo kỹ thuật viên mới</small>
        </button>
      </section>
      {selectedCredential && <CredentialDialog credential={selectedCredential} onClose={() => setSelectedCredential(null)} />}
    </div>
  </RoleShell>;
}

type AccountCredential = { fullName: string; email: string; password?: string; roleLabel: string; skills?: string[] };

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
        {credential.skills && <div><span>Chuyên môn</span><strong>{credential.skills.length ? credential.skills.join(" · ") : "Chưa gán chuyên môn"}</strong></div>}
        <div><span>Mật khẩu</span>{credential.password ? <><strong>{credential.password}</strong><small>Mật khẩu tạm vừa được BQL cấp.</small></> : <small>Mật khẩu ban đầu và mật khẩu đã đổi không được hiển thị. Chỉ có thể cấp lại mật khẩu.</small>}</div>
      </div>
    </div>
  </div>;
}

function getInitials(name: string) {
  const parts = name.trim().split(/\s+/);
  return `${parts.at(-2)?.[0] || ""}${parts.at(-1)?.[0] || ""}`.toUpperCase();
}

function TechnicianForm({ categories, busy, onDone }: { categories: CoordinatorCategory[]; busy: boolean; onDone: (input: { full_name: string; phone_number?: string | null; skill_category_ids: string[]; is_available: boolean }) => void }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [skills, setSkills] = useState<string[]>([]);
  const toggleSkill = (id: string) => setSkills((items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id]);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onDone({ full_name: name.trim(), phone_number: phone.trim() || null, skill_category_ids: skills, is_available: true });
  };
  // A technician with no skill can never be picked by assignment matching, so
  // the form asks for at least one before it will submit.
  const ready = Boolean(name.trim()) && skills.length > 0;
  return <form className="managerInlineCreate managerTechnicianCreate" onSubmit={submit}>
    <div className="field"><label>Họ tên</label><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Nguyễn Văn A" required /></div>
    <div className="field"><label>Số điện thoại</label><input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="0901234567" inputMode="tel" /></div>
    <div className="managerGeneratedPasswordNote"><span>Tài khoản</span><strong>Backend tự sinh email đăng nhập và mật khẩu 6 ký tự</strong></div>
    <fieldset className="managerSkillPicker">
      <legend>Chuyên môn <small>(chọn ít nhất một)</small></legend>
      <div className="managerSkillOptions">
        {categories.map((item) => <label className={skills.includes(item.id) ? "active" : ""} key={item.id}>
          <input type="checkbox" checked={skills.includes(item.id)} onChange={() => toggleSkill(item.id)} />
          <span>{formatCategoryName(item.code, item.display_name)}</span>
        </label>)}
        {!categories.length && <p className="helper">Chưa có danh mục nào đang hoạt động.</p>}
      </div>
    </fieldset>
    <button className="button" type="submit" disabled={busy || !ready}>{busy ? "Đang lưu..." : "Lưu kỹ thuật viên"}</button>
  </form>;
}
