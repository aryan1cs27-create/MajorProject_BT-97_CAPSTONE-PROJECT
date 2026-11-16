interface PagerProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}

/** Previous / next controls plus a range summary. */
export default function Pager({ page, pageSize, total, onPageChange }: PagerProps) {
  if (total === 0) return null;

  const first = (page - 1) * pageSize + 1;
  const last = Math.min(page * pageSize, total);
  const hasPrevious = page > 1;
  const hasNext = last < total;

  const buttonClass =
    'rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-navy-700 enabled:hover:bg-slate-50 disabled:opacity-40';

  return (
    <div className="mt-4 flex items-center justify-between gap-3">
      <p className="text-xs text-navy-500">
        Showing {first}–{last} of {total}
      </p>
      <div className="flex gap-2">
        <button type="button" className={buttonClass} disabled={!hasPrevious} onClick={() => onPageChange(page - 1)}>
          Previous
        </button>
        <button type="button" className={buttonClass} disabled={!hasNext} onClick={() => onPageChange(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}
