"use client";

import {
  Camera,
  MapPin,
  MessageSquareText,
  PhoneCall,
  ShieldCheck,
} from "lucide-react";
import { ResidentShell } from "@/components/resident/ResidentShell";
import { ResidentAlert } from "@/components/resident/ResidentUI";
import { buildingManagementPhone, formatPhoneForDisplay } from "@/lib/residentContact";
import { useResidentProfile } from "@/lib/residentProfile";

export default function ResidentPlacePage() {
  const { user, loading, error } = useResidentProfile();
  const name = user?.full_name?.trim();
  const unit = user?.unit;
  return (
    <ResidentShell title="Trang chủ" showBrand requireApartment>
      {error && <ResidentAlert tone="error">Không tải được thông tin tài khoản. Bạn vẫn có thể gửi phản ánh.</ResidentAlert>}

      <div className="rdHome">
        <section className="rdWelcome">
          <h1>
            {loading
              ? <span className="rdSkeleton" style={{ display: "block", width: 220, height: 28 }} />
              : <>Xin chào, {name || (unit ? `Căn hộ ${unit.unit_code}` : "Cư dân")} <span aria-hidden="true">👋</span></>}
          </h1>
          <p>{unit ? `Căn hộ ${unit.unit_code}` : "Gửi phản ánh về căn hộ hoặc khu vực chung."}</p>
        </section>

        <section className="rdEmergencyCall" aria-labelledby="rd-emergency-title">
          <span className="rdEmergencyIcon" aria-hidden="true"><PhoneCall size={22} /></span>
          <div>
            <div className="rdEmergencyTitle"><strong id="rd-emergency-title">Sự cố nguy hiểm</strong><span>Khẩn cấp</span></div>
            <p>Cháy, khói, rò điện hoặc rò gas: gọi Ban quản lý ngay.</p>
            {buildingManagementPhone ? (
              <a href={`tel:${buildingManagementPhone}`} aria-label={`Gọi Ban quản lý ${formatPhoneForDisplay(buildingManagementPhone)}`}>
                <PhoneCall size={16} aria-hidden="true" />Gọi Ban quản lý · {formatPhoneForDisplay(buildingManagementPhone)}
              </a>
            ) : <span className="rdEmergencyUnavailable"><PhoneCall size={16} aria-hidden="true" />Gọi Ban quản lý</span>}
          </div>
        </section>

        <section className="rdHomeGuide" aria-labelledby="rd-home-guide-title">
          <div className="rdHomeGuideHead">
            <strong id="rd-home-guide-title">Gửi phản ánh trong vài bước</strong>
          </div>
          <ol>
            <li><span aria-hidden="true">1</span><MapPin size={18} /><p><b>Chọn vị trí</b><small>Cho biết nơi xảy ra sự cố.</small></p></li>
            <li><span aria-hidden="true">2</span><MessageSquareText size={18} /><p><b>Mô tả ngắn gọn</b><small>Chia sẻ điều bạn cần hỗ trợ.</small></p></li>
            <li><span aria-hidden="true">3</span><Camera size={18} /><p><b>Thêm ảnh nếu có</b><small>Giúp Ban quản lý xử lý nhanh hơn.</small></p></li>
          </ol>
        </section>

        <p className="rdBand rdHomePrivacy">
          <ShieldCheck size={19} aria-hidden="true" />
          <span><b>Thông tin của bạn được giữ riêng.</b> Chỉ người xử lý được xem phản ánh của căn hộ bạn.</span>
        </p>
      </div>
    </ResidentShell>
  );
}
