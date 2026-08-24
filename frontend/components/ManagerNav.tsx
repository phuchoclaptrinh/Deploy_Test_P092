"use client";

import { BarChart3, Building2, History, Layers3, LayoutDashboard, LogOut, PanelLeft, ScrollText, SlidersHorizontal, UserRound, Users, Wrench } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { clearAccessToken } from "@/config/api";

export function ManagerNav({ open = false, collapsed = false, onNavigate, onToggleCollapsed }: { open?: boolean; collapsed?: boolean; onNavigate?: () => void; onToggleCollapsed?: () => void }) {
  const pathname = usePathname();
  const [profileOpen, setProfileOpen] = useState(false);
  const profileRef = useRef<HTMLDivElement>(null);
  useEffect(() => setProfileOpen(false), [pathname]);
  useEffect(() => {
    if (!profileOpen) return;
    const closeOutside = (event: MouseEvent) => { if (!profileRef.current?.contains(event.target as Node)) setProfileOpen(false); };
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setProfileOpen(false); };
    document.addEventListener("mousedown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => { document.removeEventListener("mousedown", closeOutside); document.removeEventListener("keydown", closeOnEscape); };
  }, [profileOpen]);

  const toggleLabel = collapsed ? "Mở rộng thanh điều hướng" : "Thu gọn thanh điều hướng";
  return (
    <aside className={`managerSidebar${open ? " open" : ""}${collapsed ? " collapsed" : ""}`} aria-label="Điều hướng BQL">
      <div className="managerSidebarTop">
        <span className="managerSidebarMark" aria-hidden="true"><Wrench size={18} /></span>
        <span className="managerSidebarWordmark">FixIt BQL</span>
        <button type="button" className="managerSidebarToggle" title={toggleLabel} aria-label={toggleLabel} aria-expanded={!collapsed} onClick={onToggleCollapsed}>
          <PanelLeft size={17} />
        </button>
      </div>
      <nav className="managerNavPrimary">
        {/* Auto-assignment now lives inside the dashboard workspace, and the
            category catalog moved to the business-configuration group below. */}
        <Link className={pathname === "/manager" ? "active" : ""} href="/manager" title="Dashboard" onClick={onNavigate}>
          <LayoutDashboard size={18} /><span>Dashboard</span>
        </Link>
        {/* A view of its own, not a tab inside the assignment workspace:
            looking up a past round must not require opening a draft screen. */}
        <Link className={pathname === "/manager/assignment-history" ? "active" : ""} href="/manager/assignment-history" title="Lịch sử phân việc" onClick={onNavigate}>
          <History size={18} /><span>Lịch sử phân việc</span>
        </Link>
        <Link className={pathname === "/manager/clusters" ? "active" : ""} href="/manager/clusters" title="Cụm ticket" onClick={onNavigate}>
          <Layers3 size={18} /><span>Cụm ticket</span>
        </Link>
        <Link className={pathname === "/manager/technicians" ? "active" : ""} href="/manager/technicians" title="Kỹ thuật viên" onClick={onNavigate}>
          <Users size={18} /><span>Kỹ thuật viên</span>
        </Link>
        <Link className={pathname === "/manager/residents" ? "active" : ""} href="/manager/residents" title="Cư dân" onClick={onNavigate}>
          <Building2 size={18} /><span>Cư dân</span>
        </Link>
        <Link className={pathname === "/manager/reports" ? "active" : ""} href="/manager/reports" title="Báo cáo" onClick={onNavigate}>
          <BarChart3 size={18} /><span>Báo cáo</span>
        </Link>
        <Link className={pathname === "/manager/audit" ? "active" : ""} href="/manager/audit" title="Lịch sử thay đổi" onClick={onNavigate}>
          <ScrollText size={18} /><span>Lịch sử thay đổi</span>
        </Link>
      </nav>
      <div className="managerSidebarBottom">
        <Link className={`managerSidebarSetting${pathname === "/manager/categories" ? " active" : ""}`} href="/manager/categories" title="Cấu hình nghiệp vụ · Danh mục phản ánh" onClick={onNavigate}>
          <SlidersHorizontal size={18} /><span>Cấu hình nghiệp vụ</span>
        </Link>
        <div className="managerSidebarProfileTop" ref={profileRef}>
          <button type="button" className={`managerSidebarProfileTrigger${profileOpen ? " active" : ""}`} title="Hồ sơ điều phối viên" aria-label="Mở hồ sơ điều phối viên" aria-haspopup="menu" aria-expanded={profileOpen} onClick={() => setProfileOpen((value) => !value)}>
            <span className="managerProfileAvatar">LN</span>
            <span className="managerProfileText"><strong>Lan Nguyễn</strong><small>Điều phối viên</small></span>
          </button>
          {profileOpen && <div className="managerProfilePopover" role="menu">
            <div className="managerProfilePopoverHeader"><UserRound size={17} /><div><strong>Tài khoản điều phối viên</strong><small>Lan Nguyễn</small></div></div>
            <button type="button" role="menuitem" className="managerProfileLogout" onClick={() => { clearAccessToken("manager"); setProfileOpen(false); onNavigate?.(); window.location.href = "/login"; }}><LogOut size={16} /><span>Đăng xuất</span></button>
          </div>}
        </div>
      </div>
    </aside>
  );
}
