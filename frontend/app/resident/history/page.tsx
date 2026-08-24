"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CalendarRange, ClipboardList, RotateCcw, Search, SearchX, SlidersHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listResidentCategories, listResidentTickets } from "@/api/backend.api";
import { ResidentShell } from "@/components/resident/ResidentShell";
import { ResidentAlert, ResidentCardSkeletons, ResidentEmpty, ResidentStatusBadge } from "@/components/resident/ResidentUI";
import { formatTime, groupByDate } from "@/lib/residentDate";
import { residentErrorMessage } from "@/lib/residentErrors";
import { residentMockOtpEnabled } from "@/lib/residentMockSession";
import { residentCategoryLabel, residentExpectedLabel, residentStatusView } from "@/lib/residentStatus";
import type { ResidentCategory, ResidentLifecycleGroup, ResidentTicket } from "@/types/api";

/** R-03 Requests. Every filter, the count and the ordering are applied by the
 *  backend; this screen renders one page at a time and appends the next.
 *  Copy follows docs/ui/SCREEN_INVENTORY.md section 6. */
const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 350;

type Filters = {
  group: ResidentLifecycleGroup | null;
  categoryId: string;
  from: string;
  to: string;
  search: string;
};

const groupTabs: Array<{ value: ResidentLifecycleGroup | null; label: string }> = [
  { value: null, label: "Tất cả" },
  { value: "ACTIVE", label: "Đang theo dõi" },
  { value: "FINISHED", label: "Đã kết thúc" },
];

function readFilters(params: URLSearchParams): Filters {
  const group = params.get("group");
  return {
    group: group === "ACTIVE" || group === "FINISHED" ? group : null,
    categoryId: params.get("category") || "",
    from: params.get("from") || "",
    to: params.get("to") || "",
    search: params.get("q") || "",
  };
}

function writeFilters(filters: Filters) {
  const params = new URLSearchParams();
  if (filters.group) params.set("group", filters.group);
  if (filters.categoryId) params.set("category", filters.categoryId);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  if (filters.search.trim()) params.set("q", filters.search.trim());
  const query = params.toString();
  return query ? `/resident/history?${query}` : "/resident/history";
}

export default function ResidentRequestsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const filters = useMemo(() => readFilters(new URLSearchParams(searchParams.toString())), [searchParams]);
  const filterKey = `${filters.group || ""}|${filters.categoryId}|${filters.from}|${filters.to}|${filters.search}`;

  const [items, setItems] = useState<ResidentTicket[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [sheetOpen, setSheetOpen] = useState(false);
  const [categories, setCategories] = useState<ResidentCategory[]>([]);
  const [searchDraft, setSearchDraft] = useState(filters.search);
  // Guards against an older in-flight request overwriting a newer filter's result.
  const requestVersion = useRef(0);

  const applyFilters = useCallback((next: Filters) => {
    router.replace(writeFilters(next), { scroll: false });
  }, [router]);

  const load = useCallback(async (nextPage: number, append: boolean, active: Filters) => {
    if (residentMockOtpEnabled) { setLoading(false); return; }
    const version = ++requestVersion.current;
    if (append) setLoadingMore(true); else setLoading(true);
    try {
      const result = await listResidentTickets({
        page: nextPage,
        pageSize: PAGE_SIZE,
        statusGroup: active.group,
        categoryId: active.categoryId || null,
        from: active.from || null,
        to: active.to || null,
        search: active.search || null,
      });
      if (version !== requestVersion.current) return;
      setItems((current) => {
        if (!append) return result.items;
        // A report can move between pages while paging; never render it twice.
        const seen = new Set(current.map((item) => item.id));
        return [...current, ...result.items.filter((item) => !seen.has(item.id))];
      });
      setTotal(result.total);
      setPage(nextPage);
      setError("");
    } catch (reason) {
      if (version !== requestVersion.current) return;
      setError(residentErrorMessage(reason, append ? "Không tải được thêm phản ánh." : "Không tải được danh sách phản ánh."));
    } finally {
      if (version === requestVersion.current) { setLoading(false); setLoadingMore(false); }
    }
  }, []);

  // Any filter change restarts the list at page 1.
  useEffect(() => { void load(1, false, filters); }, [filterKey, load]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the field in step when the URL changes from elsewhere (back button, clear filters).
  useEffect(() => { setSearchDraft(filters.search); }, [filters.search]);

  // Typing settles before it becomes a request.
  useEffect(() => {
    if (searchDraft.trim() === filters.search) return;
    const timer = window.setTimeout(() => applyFilters({ ...filters, search: searchDraft }), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [applyFilters, filters, searchDraft]);

  useEffect(() => {
    if (residentMockOtpEnabled) return;
    listResidentCategories().then(setCategories).catch(() => undefined);
  }, []);

  const clearAll = () => { setSearchDraft(""); applyFilters({ group: null, categoryId: "", from: "", to: "", search: "" }); };
  const filtered = Boolean(filters.group || filters.categoryId || filters.from || filters.to || filters.search);
  const extraFilters = [filters.categoryId, filters.from || filters.to].filter(Boolean).length;
  const hasMore = items.length < total;
  const groups = useMemo(() => groupByDate(items, (item) => item.created_at), [items]);

  return (
    <ResidentShell title="Phản ánh" requireApartment>
      <div className="rdSearch">
        <Search size={18} />
        <input
          value={searchDraft}
          onChange={(event) => setSearchDraft(event.target.value)}
          placeholder="Tìm theo mã hoặc nội dung phản ánh"
          aria-label="Tìm phản ánh theo mã hoặc nội dung"
          enterKeyHint="search"
        />
        {searchDraft && (
          <button type="button" onClick={() => setSearchDraft("")} aria-label="Xóa từ khóa" title="Xóa từ khóa"><X size={18} /></button>
        )}
      </div>

      <div className="rdChips" role="tablist" aria-label="Lọc theo trạng thái">
        {groupTabs.map((tab) => (
          <button
            className="rdChip"
            type="button"
            role="tab"
            aria-selected={filters.group === tab.value}
            key={tab.label}
            onClick={() => applyFilters({ ...filters, group: tab.value })}
          >{tab.label}</button>
        ))}
      </div>

      <div className="rdListMeta">
        <span className="rdListCount" role="status">{loading ? "Đang tải…" : `${total} phản ánh`}</span>
        <button className="rdFilterButton" type="button" onClick={() => setSheetOpen(true)} aria-haspopup="dialog">
          <SlidersHorizontal size={17} />
          Bộ lọc
          {extraFilters > 0 && <b>{extraFilters}</b>}
        </button>
      </div>

      {error && (
        <ResidentAlert tone="error">
          {error}
          <button className="rdTextButton" type="button" onClick={() => load(1, false, filters)}><RotateCcw size={15} />Thử lại</button>
        </ResidentAlert>
      )}

      {loading ? <ResidentCardSkeletons /> : groups.length > 0 ? (
        <>
          {groups.map((group) => (
            <section className="rdGroup" key={group.key}>
              <h2 className="rdGroupLabel">{group.label}</h2>
              {group.items.map((ticket) => <TicketRow ticket={ticket} key={ticket.id} />)}
            </section>
          ))}
          {hasMore && (
            <button className="rdButton secondary" type="button" disabled={loadingMore} onClick={() => load(page + 1, true, filters)}>
              {loadingMore ? <><span className="rdSpinner" />Đang tải…</> : `Xem thêm (${total - items.length})`}
            </button>
          )}
        </>
      ) : filtered ? (
        <ResidentEmpty
          icon={<SearchX size={26} />}
          title="Không có phản ánh phù hợp với bộ lọc."
          action={<button className="rdButton secondary inline" type="button" onClick={clearAll}>Xóa bộ lọc</button>}
        />
      ) : !error && (
        <ResidentEmpty icon={<ClipboardList size={26} />} title="Chưa có phản ánh" body="Nhấn nút + để gửi phản ánh đầu tiên." />
      )}

      {sheetOpen && (
        <FilterSheet
          filters={filters}
          categories={categories}
          onClose={() => setSheetOpen(false)}
          onApply={(next) => { setSheetOpen(false); applyFilters(next); }}
        />
      )}
    </ResidentShell>
  );
}

/** C-12 report card. Two columns: what the report says on the left, where it
 *  stands on the right. The visible code is deliberately absent — it belongs on
 *  the detail screen, and search finds it without needing it on every row. */
function TicketRow({ ticket }: { ticket: ResidentTicket }) {
  const status = residentStatusView(ticket);
  const expected = residentExpectedLabel(ticket);
  return (
    <Link className="rdTicketRow" data-tone={status.tone} href={`/resident/tickets/${ticket.id}?from=requests`}>
      <h3 className="rdTicketTitle">{residentCategoryLabel(ticket)}</h3>
      <span className="rdTicketStatus"><ResidentStatusBadge status={status} /></span>
      {/* Always rendered, so a report without text is exactly as tall as one with it. */}
      <p className="rdTicketDesc">{ticket.description?.trim() || "Phản ánh gửi bằng hình ảnh."}</p>
      <time className="rdTicketTime" dateTime={ticket.created_at}>{formatTime(ticket.created_at)}</time>
      {expected && <b className="rdDeadline">{expected}</b>}
    </Link>
  );
}

/** O-04 filter sheet. Issue type and date range are sent to the backend. */
function FilterSheet({
  filters,
  categories,
  onClose,
  onApply,
}: {
  filters: Filters;
  categories: ResidentCategory[];
  onClose: () => void;
  onApply: (next: Filters) => void;
}) {
  const [categoryId, setCategoryId] = useState(filters.categoryId);
  const [from, setFrom] = useState(filters.from);
  const [to, setTo] = useState(filters.to);
  const invalidRange = Boolean(from && to && to < from);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onClose]);

  return (
    <div className="rdDialogBackdrop" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <form
        className="rdDialog rdFilterSheet"
        role="dialog"
        aria-modal="true"
        aria-label="Bộ lọc phản ánh"
        onSubmit={(event) => { event.preventDefault(); if (!invalidRange) onApply({ ...filters, categoryId, from, to }); }}
      >
        <h2>Bộ lọc</h2>

        <div className="rdField">
          <label htmlFor="rd-filter-category">Loại sự cố</label>
          <select id="rd-filter-category" value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
            <option value="">Tất cả loại sự cố</option>
            {categories.map((category) => <option value={category.id} key={category.id}>{category.display_name}</option>)}
          </select>
          {categories.length === 0 && <p className="rdFieldHint"><span>Chưa tải được danh mục loại sự cố.</span></p>}
        </div>

        <div className="rdField">
          <span className="rdFieldLabel"><CalendarRange size={15} aria-hidden="true" /> Thời gian</span>
          <div className="rdLocationGrid">
            <input type="date" value={from} max={to || undefined} onChange={(event) => setFrom(event.target.value)} aria-label="Từ ngày" />
            <input type="date" value={to} min={from || undefined} onChange={(event) => setTo(event.target.value)} aria-label="Đến ngày" />
          </div>
          {invalidRange && <p className="rdFieldError">Ngày bắt đầu phải trước ngày kết thúc.</p>}
        </div>

        <button className="rdButton" type="submit" disabled={invalidRange}>Áp dụng</button>
        <button className="rdButton secondary" type="button" onClick={() => onApply({ ...filters, categoryId: "", from: "", to: "" })}>Xóa bộ lọc</button>
      </form>
    </div>
  );
}
