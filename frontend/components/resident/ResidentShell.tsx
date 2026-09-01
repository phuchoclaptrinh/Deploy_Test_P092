"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, Bell, ClipboardList, Home, Phone, Plus, ShieldCheck, UserRound, WifiOff, Wrench, X } from "lucide-react";
import { useEffect, useState } from "react";
import { listNotifications } from "@/api/backend.api";
import { buildingManagementPhone } from "@/lib/residentContact";
import { useResidentProfile } from "@/lib/residentProfile";
import { residentMockOtpEnabled } from "@/lib/residentMockSession";

/** C-01 Resident app shell: header, offline banner, access blockers and the
 *  five-position bottom navigation. */
type ResidentShellProps = {
  title: string;
  children: React.ReactNode;
  /** tab = main section with bottom navigation, detail = back button, sheet = close button. */
  variant?: "tab" | "detail" | "sheet";
  backHref?: string;
  onClose?: () => void;
  closeLabel?: string;
  headerAction?: React.ReactNode;
  showBrand?: boolean;
  /** Screens that cannot work without a linked apartment show the blocker instead. */
  requireApartment?: boolean;
  /** Draws attention to the + button once, for a first-time resident. */
  reportHint?: boolean;
};

const navItems = [
  { href: "/resident", label: "Trang chủ", Icon: Home },
  { href: "/resident/notifications", label: "Thông báo", Icon: Bell },
  { href: "/resident/history", label: "Phản ánh", Icon: ClipboardList },
  { href: "/resident/profile", label: "Tài khoản", Icon: UserRound },
];

export function ResidentShell({
  title,
  children,
  variant = "tab",
  backHref,
  onClose,
  closeLabel = "Đóng",
  headerAction,
  showBrand = false,
  requireApartment = false,
  reportHint = false,
}: ResidentShellProps) {
  const scrolled = useScrolled();
  const offline = useOffline();
  const { user, loading } = useResidentProfile();
  const blocked = requireApartment && !loading && Boolean(user) && !user?.unit;

  return (
    <div className="rdApp" data-variant={variant} data-hint={reportHint || undefined}>
      <header className="rdHeader" data-scrolled={scrolled} data-align={variant === "tab" ? "start" : "center"} data-lead={variant === "tab" && !showBrand ? "none" : "icon"} data-action={headerAction ? "true" : undefined}>
        {variant === "detail" && backHref ? (
          <Link className="rdHeaderButton" href={backHref} aria-label="Quay lại" title="Quay lại"><ArrowLeft size={22} /></Link>
        ) : variant === "sheet" ? (
          <button className="rdHeaderButton" type="button" onClick={onClose} aria-label={closeLabel} title={closeLabel}><X size={22} /></button>
        ) : showBrand ? (
          <span className="rdHeaderMark" aria-hidden="true"><Wrench size={19} /></span>
        ) : null}
        <h1 className="rdHeaderTitle">{title}</h1>
        {headerAction || <span className="rdHeaderSpacer" aria-hidden="true" />}
      </header>

      {offline && <div className="rdOffline" role="status"><WifiOff size={16} />Bạn đang ngoại tuyến. Một số thông tin có thể chưa được cập nhật.</div>}

      <main className="rdMain" data-nav={variant === "tab"}>
        {blocked ? <ApartmentBlocker /> : children}
      </main>

      {variant === "tab" && <ResidentBottomNav reportHint={reportHint} />}
    </div>
  );
}

function ApartmentBlocker() {
  return (
    <section className="rdBlocker">
      <span aria-hidden="true"><ShieldCheck size={26} /></span>
      <h2>Tài khoản chưa được liên kết với căn hộ</h2>
      <p>Bạn chưa thể gửi hoặc xem phản ánh. Vui lòng liên hệ Ban quản lý để hoàn tất liên kết căn hộ.</p>
      {buildingManagementPhone && <a className="rdButton" href={`tel:${buildingManagementPhone}`}><Phone size={18} />Gọi Ban quản lý</a>}
    </section>
  );
}

function ResidentBottomNav({ reportHint }: { reportHint: boolean }) {
  const pathname = usePathname();
  const unread = useUnreadNotices();
  return (
    <nav className="rdNav" aria-label="Điều hướng chính">
      {navItems.slice(0, 2).map((item) => <NavItem key={item.href} {...item} pathname={pathname} unread={item.href === "/resident/notifications" ? unread : null} />)}
      <span className="rdNavItem rdNavReport">
        <Link className="rdNavReportButton" data-hint={reportHint} href="/resident/new" aria-label="Tạo phản ánh mới" title="Tạo phản ánh mới"><Plus size={26} strokeWidth={2.6} /></Link>
      </span>
      {navItems.slice(2).map((item) => <NavItem key={item.href} {...item} pathname={pathname} unread={null} />)}
    </nav>
  );
}

type UnreadState = { count: number; exact: boolean } | null;

function NavItem({ href, label, Icon, pathname, unread }: { href: string; label: string; Icon: typeof Home; pathname: string; unread: UnreadState }) {
  const active = pathname === href;
  const badge = unread && unread.count > 0;
  return (
    <Link className="rdNavItem" href={href} aria-current={active ? "page" : undefined}>
      <span className="rdNavIcon">
        <Icon size={21} strokeWidth={active ? 2.4 : 2} />
        {badge && (unread.exact
          ? <span className="rdNavBadge">{unread.count > 99 ? "99+" : unread.count}</span>
          : <span className="rdNavBadge dot" aria-hidden="true" />)}
      </span>
      <span>{label}</span>
      {badge && <span className="rdSrOnly">{unread.exact ? `${unread.count} thông báo chưa đọc` : "Có thông báo chưa đọc"}</span>}
    </Link>
  );
}

/** C-20 Notice badge. The list endpoint caps at 200 rows and returns no unread
 *  total, so a full page of results downgrades the badge to a dot. */
export const noticeListLimit = 200;
const noticeCacheTtlMs = 30_000;
let cachedUnread: UnreadState = null;
let cachedUnreadAt = 0;

export const residentNoticesChangedEvent = "resident-notices-changed";

/** The Notice screen holds the whole list, so it owns the count while it is
 *  open and pushes it here. Re-reading it from the API instead would race the
 *  writes that just changed it. */
export function publishResidentNoticeCount(count: number, exact: boolean) {
  cachedUnread = { count, exact };
  cachedUnreadAt = Date.now();
  window.dispatchEvent(new CustomEvent<UnreadState>(residentNoticesChangedEvent, { detail: cachedUnread }));
}

function useUnreadNotices() {
  const [unread, setUnread] = useState<UnreadState>(cachedUnread);
  useEffect(() => {
    if (residentMockOtpEnabled) return;
    let active = true;
    const load = () => {
      if (cachedUnread && Date.now() - cachedUnreadAt < noticeCacheTtlMs) return;
      listNotifications("resident").then((items) => {
        cachedUnread = { count: items.filter((item) => item.status !== "READ").length, exact: items.length < noticeListLimit };
        cachedUnreadAt = Date.now();
        if (active) setUnread(cachedUnread);
      }).catch(() => undefined);
    };
    load();
    const apply = (event: Event) => {
      const pushed = (event as CustomEvent<UnreadState>).detail;
      if (pushed && active) setUnread(pushed);
    };
    const refreshWhenVisible = () => { if (document.visibilityState === "visible") load(); };
    window.addEventListener(residentNoticesChangedEvent, apply);
    window.addEventListener("focus", load);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      active = false;
      window.removeEventListener(residentNoticesChangedEvent, apply);
      window.removeEventListener("focus", load);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, []);
  return unread;
}

function useScrolled() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 4);
    update();
    window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);
  return scrolled;
}

function useOffline() {
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    const update = () => setOffline(!navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => { window.removeEventListener("online", update); window.removeEventListener("offline", update); };
  }, []);
  return offline;
}
