"use client";

import { Building2, Check, Hash, Layers, LogOut, Palette, Phone, ShieldCheck, User } from "lucide-react";
import { useEffect, useState } from "react";
import { ResidentShell } from "@/components/resident/ResidentShell";
import { ResidentAlert, ResidentConfirmDialog } from "@/components/resident/ResidentUI";
import { clearAccessToken } from "@/config/api";
import { clearResidentMockSession } from "@/lib/residentMockSession";
import { clearResidentProfileCache, useResidentProfile } from "@/lib/residentProfile";
import { readResidentTheme, residentThemes, setResidentTheme, type ResidentTheme } from "@/lib/residentTheme";

/** R-06 Profile: read-only account information plus a safe logout.
 *  Copy follows docs/ui/SCREEN_INVENTORY.md section 9. */
export default function ResidentAccountPage() {
  const { user, loading, error } = useResidentProfile();
  const [confirmLogout, setConfirmLogout] = useState(false);
  // Starts on the default so the server and the first client render agree; the
  // effect then reads what the layout script already put on <html>.
  const [theme, setTheme] = useState<ResidentTheme>("blue");
  useEffect(() => setTheme(readResidentTheme()), []);
  const chooseTheme = (next: ResidentTheme) => { setResidentTheme(next); setTheme(next); };
  const initials = (user?.full_name || "Cư dân").split(/\s+/).filter(Boolean).slice(-2).map((part) => part[0]).join("").toLocaleUpperCase("vi");
  const value = (text: string | null | undefined, fallback = "Chưa cập nhật") => (loading ? "…" : text?.trim() || fallback);

  const logout = () => {
    clearAccessToken("resident");
    clearResidentMockSession();
    clearResidentProfileCache();
    window.location.href = "/login";
  };

  return (
    <ResidentShell title="Tài khoản">
      {error && <ResidentAlert tone="error">Không tải được thông tin tài khoản.</ResidentAlert>}

      <section className="rdAccount">
        <span className="rdAvatar" aria-hidden="true">{initials}</span>
        <div>
          <strong>{value(user?.full_name, "Cư dân")}</strong>
          <span>{value(user?.phone_e164, "Chưa có số điện thoại")}</span>
        </div>
      </section>

      <section className="rdInfoList">
        <div className="rdInfoRow"><span><User size={17} />Họ và tên</span><strong>{value(user?.full_name, "Chưa cập nhật")}</strong></div>
        <div className="rdInfoRow"><span><Phone size={17} />Số điện thoại</span><strong>{value(user?.phone_e164)}</strong></div>
        <div className="rdInfoRow"><span><Building2 size={17} />Tòa nhà</span><strong>{value(user?.unit?.building_code && `Tòa ${user.unit.building_code}`, "Chưa liên kết")}</strong></div>
        <div className="rdInfoRow"><span><Layers size={17} />Tầng</span><strong>{value(user?.unit?.floor_code, "Chưa liên kết")}</strong></div>
        <div className="rdInfoRow"><span><Hash size={17} />Căn hộ</span><strong>{value(user?.unit?.unit_code, "Chưa liên kết")}</strong></div>
        <div className="rdInfoRow"><span><ShieldCheck size={17} />Trạng thái tài khoản</span><strong className="positive">Đang hoạt động</strong></div>
      </section>

      <p className="rdHelperText">Thông tin chưa đúng? Liên hệ Ban quản lý.</p>

      <section className="rdCard" aria-labelledby="rd-theme-title">
        <div className="rdCardHead">
          <span aria-hidden="true"><Palette size={19} /></span>
          <div><strong id="rd-theme-title">Tông màu</strong><small>Áp dụng cho toàn bộ ứng dụng cư dân</small></div>
        </div>
        <div className="rdThemePicker" role="radiogroup" aria-labelledby="rd-theme-title">
          {residentThemes.map((option) => (
            <button
              className="rdThemeOption"
              type="button"
              role="radio"
              aria-checked={theme === option.value}
              key={option.value}
              onClick={() => chooseTheme(option.value)}
            >
              <span className="rdThemeSwatch" style={{ background: option.accent }} aria-hidden="true">
                {theme === option.value && <Check size={15} strokeWidth={3} />}
              </span>
              <b>{option.label}</b>
              <small>{option.hint}</small>
            </button>
          ))}
        </div>
      </section>

      <button className="rdButton secondary" type="button" onClick={() => setConfirmLogout(true)}><LogOut size={18} />Đăng xuất</button>

      {confirmLogout && (
        <ResidentConfirmDialog
          title="Đăng xuất khỏi ứng dụng?"
          body="Bạn sẽ cần đăng nhập lại để xem và gửi phản ánh."
          safeLabel="Ở lại"
          destructiveLabel="Đăng xuất"
          onSafe={() => setConfirmLogout(false)}
          onDestructive={logout}
        />
      )}
    </ResidentShell>
  );
}
