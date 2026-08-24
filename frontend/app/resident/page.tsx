"use client";

import {
  PhoneCall,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { ResidentShell } from "@/components/resident/ResidentShell";
import { ResidentAlert } from "@/components/resident/ResidentUI";
import { buildingManagementPhone, formatPhoneForDisplay } from "@/lib/residentContact";
import { useResidentProfile } from "@/lib/residentProfile";

const coachDismissedKey = "fixit-resident-report-hint-dismissed";

export default function ResidentPlacePage() {
  const { user, loading, error } = useResidentProfile();
  const name = user?.full_name?.trim();
  const unit = user?.unit;
  const [showCoach, setShowCoach] = useState(false);

  // First visit only. The hint disappears for good once dismissed.
  useEffect(() => {
    try {
      if (window.localStorage.getItem(coachDismissedKey) !== "1") setShowCoach(true);
    } catch {
      // A browser blocking storage simply never shows the hint.
    }
  }, []);

  const dismissCoach = () => {
    setShowCoach(false);
    try { window.localStorage.setItem(coachDismissedKey, "1"); } catch { /* Nothing to persist. */ }
  };

  return (
    <ResidentShell title="Trang chủ" showBrand requireApartment reportHint={showCoach}>
      {error && <ResidentAlert tone="error">Không tải được thông tin tài khoản. Bạn vẫn có thể gửi phản ánh.</ResidentAlert>}

      <section className="rdWelcome">
        <h1>
          {loading
            ? <span className="rdSkeleton" style={{ display: "block", width: 220, height: 28 }} />
            : <>Xin chào, {name || (unit ? `Căn hộ ${unit.unit_code}` : "Cư dân")} <span aria-hidden="true">👋</span></>}
        </h1>
        <p>{unit ? `Căn hộ ${unit.unit_code} · Tòa ${unit.building_code}` : "Gửi phản ánh về căn hộ hoặc khu vực chung của bạn."}</p>
      </section>

      <section className="rdEmergencyCall" aria-labelledby="rd-emergency-title">
        <span className="rdEmergencyIcon" aria-hidden="true"><PhoneCall size={22} /></span>
        <div>
          <div className="rdEmergencyTitle"><strong id="rd-emergency-title">Sự cố P3 · nguy hiểm</strong><span>Gọi ngay</span></div>
          <p>Cháy, khói, rò điện, rò gas hoặc nguy cơ mất an toàn: hãy gọi Ban quản lý trước, không chờ gửi phản ánh.</p>
          {buildingManagementPhone ? (
            <a href={`tel:${buildingManagementPhone}`} aria-label={`Gọi Ban quản lý ${formatPhoneForDisplay(buildingManagementPhone)}`}>
              <PhoneCall size={16} aria-hidden="true" />Gọi Ban quản lý · {formatPhoneForDisplay(buildingManagementPhone)}
            </a>
          ) : <span className="rdEmergencyUnavailable"><PhoneCall size={16} aria-hidden="true" />Gọi Ban quản lý</span>}
        </div>
      </section>

      <p className="rdBand">
        <ShieldCheck size={19} aria-hidden="true" />
        <span><b>Thông tin của bạn được giữ riêng.</b> Chỉ Ban quản lý và kỹ thuật viên xử lý mới xem được phản ánh của căn hộ bạn.</span>
      </p>

      {showCoach && (
        <div className="rdCoach" role="note">
          Nhấn + để gửi phản ánh
          <button type="button" onClick={dismissCoach} aria-label="Đã hiểu, ẩn gợi ý" title="Đã hiểu"><X size={14} /></button>
        </div>
      )}
    </ResidentShell>
  );
}
