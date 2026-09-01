"use client";

import Link from "next/link";
import { ArrowLeft, Bell, History, Home, Menu, ShieldCheck, UserRound, X } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { listCoordinatorTickets } from "@/api/backend.api";
import { ManagerNav } from "@/components/ManagerNav";
import { hasBackendSession } from "@/config/api";
import { formatCategoryName } from "@/lib/category";
import { formatTicketCode } from "@/lib/display";
import { getSeenManagerTickets } from "@/lib/managerTicketSeen";
import { listTickets } from "@/lib/mockService";
import type { CoordinatorTicket } from "@/types/api";
import type { UserRole } from "@/lib/types";
import { useStoreRevision } from "@/lib/useStore";

type RoleShellProps = {
  role: UserRole;
  title: string;
  eyebrow?: string;
  subtitle?: string;
  children: React.ReactNode;
  actions?: React.ReactNode;
  backHref?: string;
  residentTab?: boolean;
  managerHeader?: React.ReactNode;
};

export function RoleShell({ role, title, eyebrow, subtitle, children, actions, backHref, residentTab = false, managerHeader }: RoleShellProps) {
  const pathname = usePathname();
  const [managerMenuOpen, setManagerMenuOpen] = useState(false);
  const [managerNavCollapsed, setManagerNavCollapsed] = useState(false);
  useEffect(() => setManagerMenuOpen(false), [pathname]);
  useEffect(() => { setManagerNavCollapsed(window.localStorage.getItem("fixit-manager-sidebar-collapsed") === "true"); }, []);
  const isResidentSubpage = (role === "resident" || role === "technician") && Boolean(backHref);
  const isTechnicianHome = role === "technician" && pathname === "/technician";
  const isTechnicianTab = role === "technician" && ["/technician", "/technician/profile"].includes(pathname);
  const isResidentProfile = pathname === "/resident/profile";
  const isManagerDispatch = role === "manager" && pathname === "/manager";
  const residentViewClass = role !== "resident"
    ? ""
    : pathname === "/resident"
      ? " residentHomePage"
      : pathname === "/resident/history"
        ? " residentHistoryPage"
        : pathname === "/resident/notifications"
          ? " residentNotificationsPage"
          : pathname === "/resident/new"
            ? " residentCreatePage"
            : pathname.startsWith("/resident/tickets/")
              ? " residentTicketDetailPage"
              : "";
  const showResidentNav = role === "resident" && ["/resident", "/resident/history", "/resident/notifications", "/resident/profile"].includes(pathname);
  const showTechnicianNav = role === "technician" && ["/technician", "/technician/profile"].includes(pathname);
  const resolvedManagerHeader = managerHeader || <section className="managerDashboardHeader"><div>{eyebrow && <p>{eyebrow}</p>}<h1>{title}</h1>{subtitle && <span>{subtitle}</span>}</div></section>;

  return (
    <div className={`appFrame role-${role}${isResidentSubpage ? " residentSubpage" : ""}${isTechnicianTab ? " technicianTabPage" : ""}${isTechnicianHome ? " technicianHomePage" : ""}${residentTab ? " residentTabPage" : ""}${isResidentProfile ? " residentAccountPage" : ""}${isManagerDispatch ? " managerDispatchPage" : ""}${residentViewClass}`}>
      {role === "manager" ? (
        <div className={`managerWorkspace${managerNavCollapsed ? " sidebarCollapsed" : ""}`}>
          <ManagerNav open={managerMenuOpen} collapsed={managerNavCollapsed} onNavigate={() => setManagerMenuOpen(false)} onToggleCollapsed={() => setManagerNavCollapsed((value) => { const next = !value; window.localStorage.setItem("fixit-manager-sidebar-collapsed", String(next)); return next; })} />
          {managerMenuOpen && <button className="managerSidebarBackdrop" aria-label="Đóng menu" onClick={() => setManagerMenuOpen(false)} />}
          <div className="managerMainColumn">
            <header className="managerDashboardTop">
              {resolvedManagerHeader}
              <ManagerUserActions menuOpen={managerMenuOpen} onMenuToggle={() => setManagerMenuOpen((value) => !value)} />
            </header>
            {actions && <div className="managerDashboardSearchRow">{actions}</div>}
            <main className="managerPageWrap managerDashboardPageWrap">{children}</main>
          </div>
        </div>
      ) : <>
      <header className={`topbar${isResidentSubpage ? " residentSubpageTopbar" : ""}${residentTab ? " residentTabTopbar" : ""}`}>
        {residentTab ? <>
          <div className="residentTabHeading"><small>RESIDENT</small><strong className="residentTabTitle">{title}</strong></div>
          {actions && <div className="topbarActions">{actions}</div>}
        </> : isResidentSubpage ? (
          <>
            <Link href={backHref!} className="iconButton backButton" title="Quay lại" aria-label="Quay lại"><ArrowLeft size={21} /></Link>
            <strong className="residentSubpageTitle">{title}</strong>
            <span className="residentSubpageHeaderSpacer" aria-hidden="true" />
          </>
        ) : role === "resident" ? <>
          <Link href="/resident" className="residentTopbarBrand"><span className="brandMark"><ShieldCheck size={18} /></span><span><small>RESIDENT</small><strong>{title}</strong></span></Link>
          <Link href="/resident/notifications" className="iconButton" title="Thông báo" aria-label="Mở thông báo"><Bell size={18} /></Link>
        </> : <>
          <strong className="technicianTopbarTitle">{title}</strong>
          {actions && <div className="topbarActions">{actions}</div>}
        </>}
      </header>
      <main className="pageWrap">{children}</main>
      </>}

      {showResidentNav && <nav className="bottomNav residentBottomNav">
        <Link className={pathname === "/resident" ? "active" : ""} href="/resident"><Home size={20} /><span>Trang chủ</span></Link>
        <Link className={pathname === "/resident/history" ? "active" : ""} href="/resident/history"><History size={20} /><span>Lịch sử</span></Link>
        <Link className={pathname === "/resident/notifications" ? "active" : ""} href="/resident/notifications"><Bell size={20} /><span>Thông báo</span></Link>
        <Link className={pathname === "/resident/profile" ? "active" : ""} href="/resident/profile"><UserRound size={20} /><span>Tôi</span></Link>
      </nav>}
      {showTechnicianNav && <nav className="bottomNav technicianBottomNav">
        <Link className={pathname === "/technician" ? "active" : ""} href="/technician"><Home size={20} /><span>Trang chủ</span></Link>
        <Link className={pathname === "/technician/profile" ? "active" : ""} href="/technician/profile"><UserRound size={20} /><span>Tôi</span></Link>
      </nav>}
    </div>
  );
}

function ManagerUserActions({ menuOpen, onMenuToggle }: { menuOpen: boolean; onMenuToggle: () => void }) {
  useStoreRevision();
  const [backendTickets, setBackendTickets] = useState<CoordinatorTicket[]>([]);
  const [, setSeenRevision] = useState(0);
  const usingBackend = hasBackendSession("manager");
  useEffect(() => {
    if (!usingBackend) return;
    const load = () => listCoordinatorTickets().then((result) => setBackendTickets(result.items)).catch(() => undefined);
    load();
    const timer = window.setInterval(load, 15000);
    const refreshSeen = () => setSeenRevision((value) => value + 1);
    window.addEventListener("manager-ticket-seen", refreshSeen);
    return () => { window.clearInterval(timer); window.removeEventListener("manager-ticket-seen", refreshSeen); };
  }, [usingBackend]);
  const seen = getSeenManagerTickets();
  const mockNewTickets = listTickets("manager").filter((ticket) => ticket.managerSeen === false);
  const newTickets = usingBackend ? backendTickets.filter((ticket) => ticket.status === "NEW" && !seen.has(ticket.id)) : mockNewTickets;
  const newTicketCount = newTickets.length;
  const [notificationsOpen, setNotificationsOpen] = useState(false);
  const [toastTicket, setToastTicket] = useState<(typeof newTickets)[number] | null>(null);
  const notificationRef = useRef<HTMLDivElement>(null);
  const latestNewTicket = newTickets[0];
  const latestNewTicketId = latestNewTicket?.id;
  useEffect(() => {
    if (!notificationsOpen) return;
    const close = (event: MouseEvent) => { if (!notificationRef.current?.contains(event.target as Node)) setNotificationsOpen(false); };
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") setNotificationsOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", closeOnEscape); };
  }, [notificationsOpen]);
  useEffect(() => {
    if (!latestNewTicket) return;
    const notificationKey = "manager-last-ticket-notification";
    if (sessionStorage.getItem(notificationKey) === latestNewTicket.id) return;
    sessionStorage.setItem(notificationKey, latestNewTicket.id);
    setToastTicket(latestNewTicket);
    const timeout = window.setTimeout(() => setToastTicket(null), 6500);
    return () => window.clearTimeout(timeout);
  }, [latestNewTicketId]);
  return <>
  <div className="topbarActions managerUserActions">
    <button className="managerMenuButton" aria-label={menuOpen ? "Đóng menu" : "Mở menu"} onClick={onMenuToggle}>{menuOpen ? <X size={20} /> : <Menu size={20} />}</button>
    <div className="managerNotificationWrap" ref={notificationRef}>
      <button type="button" className={`iconButton managerNotificationButton${notificationsOpen ? " active" : ""}`} title="Thông báo ticket mới" aria-label={`Thông báo ticket mới${newTicketCount ? ` (${newTicketCount})` : ""}`} aria-expanded={notificationsOpen} onClick={() => setNotificationsOpen((value) => !value)}><Bell size={18} />{newTicketCount > 0 && <b>{newTicketCount}</b>}</button>
      {notificationsOpen && <div className="managerNotificationDropdown">
        <header><div><strong>Ticket mới</strong><span>{newTicketCount} ticket chưa xem</span></div>{newTicketCount > 0 && <b>{newTicketCount}</b>}</header>
        <div className="managerNotificationList">
          {newTickets.length > 0 ? newTickets.slice(0, 5).map((ticket) => { const backend = "created_at" in ticket; return <Link href="/manager?view=new" key={ticket.id} onClick={() => setNotificationsOpen(false)}><span className={`priorityDot priorityDot-${ticket.priority || "P1"}`} /><div><strong>{formatTicketCode(ticket.id)} · {formatCategoryName(ticket.category)}</strong><small>{backend ? ticket.location_label || "Chưa xác định" : `${ticket.floor} · ${ticket.locationType}`}</small></div></Link>; }) : <div className="managerNotificationEmpty"><Bell size={20} /><span>Không có ticket mới</span></div>}
        </div>
        <Link className="managerNotificationAll" href="/manager?view=new" onClick={() => setNotificationsOpen(false)}>Mở dashboard ticket mới</Link>
      </div>}
    </div>
  </div>
  {toastTicket && <div className="managerNewTicketToast" role="status" aria-live="polite">
    <span><Bell size={18} /></span>
    <div><strong>Ticket mới từ cư dân</strong><p>{formatTicketCode(toastTicket.id)} · {formatCategoryName(toastTicket.category)} · {"created_at" in toastTicket ? toastTicket.location_label || "Chưa xác định" : toastTicket.floor}</p><Link href="/manager?view=new" onClick={() => setToastTicket(null)}>Mở dashboard</Link></div>
    <button type="button" title="Đóng thông báo" aria-label="Đóng thông báo" onClick={() => setToastTicket(null)}><X size={16} /></button>
  </div>}
  </>;
}
