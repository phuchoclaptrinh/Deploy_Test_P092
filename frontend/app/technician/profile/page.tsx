"use client";

import { LogOut, Power, ShieldCheck, Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { getTechnicianAvailability, updateTechnicianAvailability } from "@/api/backend.api";
import { RoleShell } from "@/components/RoleShell";
import { clearAccessToken } from "@/config/api";
import type { CurrentUser } from "@/types/api";

const technicianProfileKey = "fixit-technician-profile";

function readTechnicianProfile() {
  try {
    const profile = window.sessionStorage.getItem(technicianProfileKey);
    return profile ? JSON.parse(profile) as CurrentUser : null;
  } catch {
    return null;
  }
}

function clearTechnicianProfile() {
  try {
    window.sessionStorage.removeItem(technicianProfileKey);
  } catch {
    // The access token is still cleared when browser storage is unavailable.
  }
}

export default function TechnicianProfilePage() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isAvailable, setIsAvailable] = useState<boolean | null>(null);
  const [updatingAvailability, setUpdatingAvailability] = useState(false);
  const [availabilityError, setAvailabilityError] = useState("");
  useEffect(() => {
    setUser(readTechnicianProfile());
    getTechnicianAvailability().then((result) => setIsAvailable(result.is_available)).catch((reason) => {
      setAvailabilityError(reason instanceof Error ? reason.message : "Không tải được trạng thái nhận việc.");
    });
  }, []);

  const name = user?.full_name?.trim() || "Nguyễn Minh An";
  const nameParts = name.split(/\s+/).filter(Boolean);
  const initials = `${nameParts[0]?.[0] || "K"}${nameParts.length > 1 ? nameParts[nameParts.length - 1]?.[0] || "" : ""}`.toUpperCase();
  const toggleAvailability = async () => {
    if (isAvailable === null || updatingAvailability) return;
    const previous = isAvailable;
    const next = !previous;
    setIsAvailable(next);
    setAvailabilityError("");
    setUpdatingAvailability(true);
    try {
      const result = await updateTechnicianAvailability(next);
      setIsAvailable(result.is_available);
    } catch (reason) {
      setIsAvailable(previous);
      setAvailabilityError(reason instanceof Error ? reason.message : "Không thể cập nhật trạng thái nhận việc.");
    } finally {
      setUpdatingAvailability(false);
    }
  };

  return <RoleShell role="technician" title="Tôi">
    <section className="technicianAccountCard">
      <span className="technicianAccountAvatar">{initials}</span>
      <div>
        <strong>{name}</strong>
        <span>{user?.phone_e164 || "Kỹ thuật viên FixIt"}</span>
      </div>
    </section>

    <section className="technicianAccountInfo">
      <div><span><Wrench size={16} />Vai trò</span><strong>Kỹ thuật viên</strong></div>
      <div><span><ShieldCheck size={16} />Trạng thái</span><strong>Đã xác thực</strong></div>
    </section>

    <section className={`technicianAvailabilityCard${isAvailable ? " available" : ""}`} aria-live="polite">
      <div>
        <span className="technicianAvailabilityIcon"><Power size={18} /></span>
        <div><strong>{isAvailable ? "Đang sẵn sàng nhận việc" : "Tạm ngừng nhận việc"}</strong><p>{isAvailable ? "Bạn có thể được hệ thống phân công ticket mới." : "Bạn sẽ không nhận ticket mới từ phân việc tự động."}</p></div>
      </div>
      <button type="button" className="technicianAvailabilitySwitch" role="switch" aria-checked={Boolean(isAvailable)} aria-label="Bật hoặc tắt trạng thái sẵn sàng nhận việc" disabled={isAvailable === null || updatingAvailability} onClick={() => void toggleAvailability()}>
        <span />
      </button>
      {availabilityError && <p className="technicianAvailabilityError" role="alert">{availabilityError}</p>}
    </section>

    <button className="technicianAccountLogout" type="button" onClick={() => { clearAccessToken("technician"); clearTechnicianProfile(); window.location.href = "/login"; }}>
      <LogOut size={18} />
      <span>Đăng xuất</span>
    </button>
  </RoleShell>;
}
