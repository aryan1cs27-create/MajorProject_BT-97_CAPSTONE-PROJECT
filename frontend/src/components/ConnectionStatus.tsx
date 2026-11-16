import { useEffect, useState } from 'react';

import { api } from '../services/api';

type Status = 'checking' | 'online' | 'offline';

const POLL_INTERVAL_MS = 15_000;

/**
 * Polls `GET /api/health` and shows whether the FastAPI backend is reachable.
 */
export default function ConnectionStatus() {
  const [status, setStatus] = useState<Status>('checking');

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        await api.getHealth();
        if (!cancelled) setStatus('online');
      } catch {
        if (!cancelled) setStatus('offline');
      }
    };

    void check();
    const id = window.setInterval(check, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  const styles: Record<Status, { dot: string; label: string; text: string; title: string }> = {
    checking: {
      dot: 'bg-slate-400',
      label: 'Checking…',
      text: 'text-slate-500',
      title: 'Checking backend availability',
    },
    online: {
      dot: 'bg-emerald-500',
      label: 'Backend online',
      text: 'text-emerald-700',
      title: 'FastAPI backend is reachable',
    },
    offline: {
      dot: 'bg-rose-500',
      label: 'Backend offline',
      text: 'text-rose-700',
      title: 'FastAPI backend is not reachable — start it with: uvicorn app.main:app --reload',
    },
  };

  const style = styles[status];

  return (
    <div
      role="status"
      title={style.title}
      className={`inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium ${style.text}`}
    >
      <span className={`h-2 w-2 rounded-full ${style.dot}`} aria-hidden="true" />
      {style.label}
    </div>
  );
}
