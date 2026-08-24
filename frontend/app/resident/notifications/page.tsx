"use client";

import { useRouter } from "next/navigation";
import { Bell, CheckCheck, Inbox, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { listNotifications, markBackendNotificationRead } from "@/api/backend.api";
import { ResidentShell, noticeListLimit, publishResidentNoticeCount } from "@/components/resident/ResidentShell";
import { ResidentAlert, ResidentCardSkeletons, ResidentEmpty } from "@/components/resident/ResidentUI";
import { formatTicketCode } from "@/lib/display";
import { formatTime, groupByDate } from "@/lib/residentDate";
import { residentErrorMessage } from "@/lib/residentErrors";
import { residentMockOtpEnabled } from "@/lib/residentMockSession";
import { noticeLook, noticeTone } from "@/lib/residentNotice";
import type { BackendNotification } from "@/types/api";

/** R-05 Notice, as a compact inbox: newest first, grouped by day, with the
 *  whole row as the target. Copy follows docs/ui/SCREEN_INVENTORY.md section 8. */
type NoticeFilter = "all" | "unread";

export default function ResidentNoticePage() {
  const router = useRouter();
  const [notices, setNotices] = useState<BackendNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<NoticeFilter>("all");
  const [markingAll, setMarkingAll] = useState(false);

  const load = useCallback((quiet = false) => {
    if (residentMockOtpEnabled) { setLoading(false); return; }
    if (!quiet) setLoading(true);
    listNotifications("resident", true)
      .then((items) => { setNotices(items); setError(""); })
      .catch((reason) => { if (!quiet) setError(residentErrorMessage(reason, "Không tải được thông báo.")); })
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  // A notice that arrives while this screen is open would otherwise sit in the
  // badge without ever appearing in the list.
  useEffect(() => {
    const refresh = () => load(true);
    const refreshWhenVisible = () => { if (document.visibilityState === "visible") refresh(); };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
    };
  }, [load]);

  const unreadCount = notices.filter((item) => item.status !== "READ").length;
  // This screen holds the whole list, so it is the authority on the badge for
  // as long as it is open — including the rollback when a write fails.
  useEffect(() => {
    if (!loading) publishResidentNoticeCount(unreadCount, notices.length < noticeListLimit);
  }, [loading, notices.length, unreadCount]);
  const visible = useMemo(() => {
    const sorted = [...notices].sort((a, b) => b.created_at.localeCompare(a.created_at));
    return filter === "unread" ? sorted.filter((item) => item.status !== "READ") : sorted;
  }, [filter, notices]);
  const groups = useMemo(() => groupByDate(visible, (item) => item.created_at), [visible]);

  const markRead = async (notice: BackendNotification) => {
    // Optimistic: reading the notice and opening the report must not wait on the write.
    setNotices((current) => current.map((item) => item.id === notice.id ? { ...item, status: "READ" } : item));
    try {
      await markBackendNotificationRead("resident", notice.id);
    } catch {
      setNotices((current) => current.map((item) => item.id === notice.id ? { ...item, status: notice.status } : item));
    }
  };

  const open = async (notice: BackendNotification) => {
    if (notice.status !== "READ") await markRead(notice);
    if (notice.ticket_id) router.push(`/resident/tickets/${notice.ticket_id}?from=notice`);
  };

  /** No bulk endpoint exists, so this marks each unread notice individually. */
  const markAllRead = async () => {
    const unread = notices.filter((item) => item.status !== "READ");
    if (!unread.length) return;
    setMarkingAll(true);
    setNotices((current) => current.map((item) => ({ ...item, status: "READ" })));
    const results = await Promise.allSettled(
      unread.map((item) => markBackendNotificationRead("resident", item.id)),
    );
    // Roll each failure back to the status it actually had, not a guessed one.
    const failed = new Map(
      unread.filter((_, index) => results[index].status === "rejected").map((item) => [item.id, item.status]),
    );
    if (failed.size) {
      setNotices((current) => current.map((item) => failed.has(item.id) ? { ...item, status: failed.get(item.id)! } : item));
      setError("Không đánh dấu được một số thông báo. Vui lòng thử lại.");
    }
    setMarkingAll(false);
  };

  return (
    <ResidentShell
      title="Thông báo"
      headerAction={
        <button className="rdInboxAction" type="button" onClick={markAllRead} disabled={markingAll || unreadCount === 0}>
          <CheckCheck size={16} />Đánh dấu đã đọc
        </button>
      }
    >
      <div className="rdChips" role="tablist" aria-label="Lọc thông báo">
        <button className="rdChip" type="button" role="tab" aria-selected={filter === "all"} onClick={() => setFilter("all")}>Tất cả</button>
        <button className="rdChip" type="button" role="tab" aria-selected={filter === "unread"} onClick={() => setFilter("unread")}>
          Chưa đọc{unreadCount > 0 ? ` (${unreadCount})` : ""}
        </button>
      </div>

      {error && <ResidentAlert tone="error">{error}<button className="rdTextButton" type="button" onClick={() => load()}><RotateCcw size={15} />Thử lại</button></ResidentAlert>}

      {loading ? <ResidentCardSkeletons /> : groups.length > 0 ? (
        groups.map((group) => (
          <section className="rdGroup" key={group.key}>
            <h2 className="rdGroupLabel">{group.label}</h2>
            <div className="rdInbox">
              {group.items.map((notice) => <NoticeRow notice={notice} key={notice.id} onOpen={open} />)}
            </div>
          </section>
        ))
      ) : !error && (
        filter === "unread"
          ? <ResidentEmpty icon={<Inbox size={26} />} title="Bạn đã đọc hết thông báo" body="Các cập nhật mới sẽ xuất hiện tại đây." />
          : <ResidentEmpty icon={<Bell size={26} />} title="Chưa có thông báo" body="Các cập nhật về phản ánh sẽ xuất hiện tại đây." />
      )}
    </ResidentShell>
  );
}

function NoticeRow({ notice, onOpen }: { notice: BackendNotification; onOpen: (notice: BackendNotification) => void }) {
  const unread = notice.status !== "READ";
  const { Icon } = noticeLook(notice);
  const tone = noticeTone(notice);
  return (
    <button className="rdInboxRow" type="button" data-unread={unread} data-tone={tone} onClick={() => onOpen(notice)}>
      <span className="rdInboxIcon" data-tone={tone} aria-hidden="true"><Icon size={18} /></span>
      <span className="rdInboxBody">
        <strong>{unread && <span className="rdSrOnly">Chưa đọc. </span>}{notice.title}</strong>
        <p>{notice.body}</p>
        {notice.ticket_id && (
          <span className="rdInboxMeta"><span>{formatTicketCode(notice.ticket_id)}</span></span>
        )}
      </span>
      <span className="rdInboxAside">
        {unread && <span className="rdInboxDot" aria-hidden="true" />}
        <time dateTime={notice.created_at}>{formatTime(notice.created_at)}</time>
      </span>
    </button>
  );
}
