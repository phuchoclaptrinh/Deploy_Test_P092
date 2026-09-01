import { ChevronLeft, ChevronRight } from "lucide-react";

type ManagerPaginationProps = {
  page: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
  itemLabel?: string;
  /** The dashboard footer sits under a fixed-height table, where a full page
   *  strip would compete with the rows: it shows the range and one page step. */
  compact?: boolean;
};

export function ManagerPagination({ page, pageSize, totalItems, onPageChange, itemLabel = "bản ghi", compact = false }: ManagerPaginationProps) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const currentPage = Math.min(page, totalPages);
  const firstItem = totalItems === 0 ? 0 : (currentPage - 1) * pageSize + 1;
  const lastItem = Math.min(currentPage * pageSize, totalItems);
  const pages = Array.from({ length: totalPages }, (_, index) => index + 1);

  if (compact) return (
    <footer className="mdFoot">
      <span>Hiển thị {firstItem}–{lastItem} trong {totalItems} {itemLabel}</span>
      <nav aria-label="Phân trang" className="mdFootNav">
        <span>Trang {currentPage} / {totalPages}</span>
        <button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => onPageChange(currentPage - 1)}><ChevronLeft size={16} /></button>
        <button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => onPageChange(currentPage + 1)}><ChevronRight size={16} /></button>
      </nav>
    </footer>
  );

  return (
    <footer className="managerTableFooter managerPagination">
      <span className="managerPaginationSummary">Hiển thị <strong>{firstItem}-{lastItem}</strong> trong <strong>{totalItems}</strong> {itemLabel}</span>
      <nav aria-label="Phân trang" className="managerPaginationControls">
        <button type="button" aria-label="Trang trước" disabled={currentPage === 1} onClick={() => onPageChange(currentPage - 1)}><ChevronLeft size={15} /></button>
        {pages.map((pageNumber) => <button type="button" className={pageNumber === currentPage ? "active" : ""} aria-current={pageNumber === currentPage ? "page" : undefined} key={pageNumber} onClick={() => onPageChange(pageNumber)}>{pageNumber}</button>)}
        <button type="button" aria-label="Trang sau" disabled={currentPage === totalPages} onClick={() => onPageChange(currentPage + 1)}><ChevronRight size={15} /></button>
      </nav>
    </footer>
  );
}
