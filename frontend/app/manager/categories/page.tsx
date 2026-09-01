"use client";

import { ListChecks, Plus, Tags } from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { createBackendCategory, listBackendCategories, updateBackendCategory } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { ManagerPagination } from "@/components/manager/ManagerPagination";
import { ManagerSurface } from "@/components/manager/ManagerSurface";
import { formatCategoryName } from "@/lib/category";
import type { CoordinatorCategory } from "@/types/api";

const CATEGORY_PAGE_SIZE = 6;

export default function ManagerCategoriesPage() {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [categoryPage, setCategoryPage] = useState(1);
  const [configs, setConfigs] = useState<CoordinatorCategory[]>([]);
  const load = useCallback(() => listBackendCategories().then(setConfigs).catch((reason) => setError(reason instanceof Error ? reason.message : "Không tải được danh mục.")), []);
  useEffect(() => { load(); }, [load]);
  const totalCategoryPages = Math.max(1, Math.ceil(configs.length / CATEGORY_PAGE_SIZE));
  const activeCategoryPage = Math.min(categoryPage, totalCategoryPages);
  const visibleConfigs = configs.slice((activeCategoryPage - 1) * CATEGORY_PAGE_SIZE, activeCategoryPage * CATEGORY_PAGE_SIZE);
  const submit = async (event: FormEvent) => { event.preventDefault(); try { const code = name.trim().normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_|_$/g, "").toUpperCase(); await createBackendCategory(code, name.trim()); setName(""); setError(""); await load(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể thêm danh mục."); } };
  return <RoleShell role="manager" title="Danh mục phản ánh" subtitle="Các nhóm vấn đề được sử dụng để phân loại phản ánh.">
    <div className="managerPageStack">
      <ManagerSurface title="Thêm danh mục" description="Đặt tên cho một nhóm sự cố mới. Danh mục dùng để định tuyến kỹ năng và thống kê, không tham gia chấm điểm rủi ro." icon={<Tags size={19} />} bodyClassName="managerSurfaceFormBody">
        <form className="managerInlineCreate managerCategoryCreate" onSubmit={submit}><div className="field"><label>Tên danh mục mới</label><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Nhập tên danh mục" /></div><button className="button" type="submit"><Plus size={15} />Thêm danh mục</button>{error && <span className="fieldError">{error}</span>}</form>
      </ManagerSurface>
      <ManagerSurface title="Danh sách danh mục" description="Các nhóm phân loại hiện có trong hệ thống." icon={<ListChecks size={19} />} actions={<span className="managerCountBadge">{configs.length} danh mục</span>} bodyClassName="managerSurfaceTableBody">
        <div className="managerCategoryTable"><table className="dataTable"><thead><tr><th>Mã</th><th>Tên danh mục</th><th>Hiệu lực</th><th>Hành động</th></tr></thead><tbody>{visibleConfigs.map((item) => <tr key={item.id}><td>{item.code}</td><td><strong>{formatCategoryName(item.code, item.display_name)}</strong></td><td><span className={`managerActiveState ${item.is_active ? "on" : "off"}`}>{item.is_active ? "Đang áp dụng" : "Tạm tắt"}</span></td><td><button className="tableAction" onClick={async () => { await updateBackendCategory(item.id, { is_active: !item.is_active }); await load(); }}>{item.is_active ? "Tạm tắt" : "Kích hoạt"}</button></td></tr>)}</tbody></table></div>
        <ManagerPagination page={activeCategoryPage} pageSize={CATEGORY_PAGE_SIZE} totalItems={configs.length} itemLabel="danh mục" onPageChange={setCategoryPage} />
      </ManagerSurface>
    </div>
  </RoleShell>;
}
