import type { ReactNode } from 'react';

interface AlertProps {
  kind: 'success' | 'error';
  children: ReactNode;
}

/** Inline success / error message. */
export default function Alert({ kind, children }: AlertProps) {
  const styles =
    kind === 'success'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
      : 'border-rose-200 bg-rose-50 text-rose-800';

  return (
    <div role={kind === 'error' ? 'alert' : 'status'} className={`rounded-lg border px-4 py-3 text-sm ${styles}`}>
      {children}
    </div>
  );
}
