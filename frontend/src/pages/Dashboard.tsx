import { useEffect, useState } from 'react';

import PageHeader from '../components/PageHeader';
import { api } from '../services/api';
import type { DatabaseHealthResponse, HealthResponse } from '../types';

type LoadState = 'loading' | 'ready' | 'error';

const ROADMAP = [
  'Job postings management',
  'Resume upload and parsing (PDF / DOCX)',
  'Skill and experience matching',
  'Recommendation ranking',
];

export default function Dashboard() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [dbHealth, setDbHealth] = useState<DatabaseHealthResponse | null>(null);
  const [state, setState] = useState<LoadState>('loading');
  const [counts, setCounts] = useState<{ resumes: number; jobs: number } | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const [healthResponse, databaseResponse, resumePage, jobPage] = await Promise.all([
          api.getHealth(),
          api.getDatabaseHealth().catch(() => null),
          api.listResumes(1, 1).catch(() => null),
          api.listJobs({ page: 1, page_size: 1 }).catch(() => null),
        ]);
        if (cancelled) return;
        setHealth(healthResponse);
        setDbHealth(databaseResponse);
        if (resumePage && jobPage) {
          setCounts({ resumes: resumePage.total, jobs: jobPage.total });
        }
        setState('ready');
      } catch {
        if (!cancelled) setState('error');
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  const dbConnected = dbHealth?.database.connected ?? false;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="System overview for the MatchWise matching platform."
      />

      <div className="grid gap-4 sm:grid-cols-2">
        {/* Stored data counts (real totals from MySQL) */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-medium text-navy-500">Stored data</h2>
          <div className="mt-4 grid grid-cols-2 gap-4">
            <div>
              <p className="text-3xl font-semibold text-navy-900">
                {counts ? counts.resumes : '—'}
              </p>
              <p className="mt-1 text-xs text-navy-500">Resumes in MySQL</p>
            </div>
            <div>
              <p className="text-3xl font-semibold text-navy-900">{counts ? counts.jobs : '—'}</p>
              <p className="mt-1 text-xs text-navy-500">Job postings</p>
            </div>
          </div>
        </section>

        {/* Backend status */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-navy-500">Backend API</h2>
            <span
              className={[
                'rounded-full px-2.5 py-1 text-xs font-medium',
                state === 'ready'
                  ? 'bg-emerald-50 text-emerald-700'
                  : state === 'error'
                    ? 'bg-rose-50 text-rose-700'
                    : 'bg-slate-100 text-slate-500',
              ].join(' ')}
            >
              {state === 'ready' ? 'Connected' : state === 'error' ? 'Unreachable' : 'Checking…'}
            </span>
          </div>
          <dl className="mt-4 space-y-1.5 text-sm">
            <div className="flex justify-between">
              <dt className="text-navy-500">Application</dt>
              <dd className="font-medium text-navy-900">{health?.app ?? '—'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-navy-500">Version</dt>
              <dd className="font-medium text-navy-900">{health?.version ?? '—'}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-navy-500">Environment</dt>
              <dd className="font-medium text-navy-900">{health?.environment ?? '—'}</dd>
            </div>
          </dl>
        </section>

        {/* Database status */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-navy-500">MySQL Database</h2>
            <span
              className={[
                'rounded-full px-2.5 py-1 text-xs font-medium',
                dbConnected
                  ? 'bg-emerald-50 text-emerald-700'
                  : state === 'loading'
                    ? 'bg-slate-100 text-slate-500'
                    : 'bg-rose-50 text-rose-700',
              ].join(' ')}
            >
              {dbConnected ? 'Connected' : state === 'loading' ? 'Checking…' : 'Not connected'}
            </span>
          </div>
          <p className="mt-4 text-sm leading-relaxed text-navy-500">
            {dbConnected
              ? (dbHealth?.database.detail ?? 'Connection OK')
              : 'matchwise_db is not reachable. Start MySQL and verify DATABASE_URL in backend/.env.'}
          </p>
        </section>
      </div>

      <section className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-medium text-navy-500">Roadmap</h2>
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {ROADMAP.map((item) => (
            <li
              key={item}
              className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2.5 text-sm text-navy-700"
            >
              <span aria-hidden="true" className="mt-1.5 h-1.5 w-1.5 rounded-full bg-brand-500" />
              {item}
            </li>
          ))}
        </ul>
        <p className="mt-4 text-xs text-slate-400">
          Phase 1 delivers the foundation only — no matching logic exists yet.
        </p>
      </section>
    </div>
  );
}
