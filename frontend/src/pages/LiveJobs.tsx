import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';

import Alert from '../components/Alert';
import EmptyState from '../components/EmptyState';
import PageHeader from '../components/PageHeader';
import Pager from '../components/Pager';
import { ApiError, api } from '../services/api';
import type {
  ImportSummary,
  Job,
  ProviderCatalog,
  ProviderJob,
  ProviderSearchRequest,
  ProviderSearchResponse,
  ProviderStatus,
} from '../types';

const PAGE_SIZE = 10;

const EMPLOYMENT_TYPES = ['full-time', 'part-time', 'contract', 'internship', 'temporary'] as const;
const WORK_MODES = ['remote', 'hybrid', 'onsite'] as const;
const POSTED_WITHIN = [1, 3, 7, 14, 30] as const;

const PERIOD_LABEL: Record<string, string> = {
  hourly: 'an hour',
  daily: 'a day',
  weekly: 'a week',
  monthly: 'a month',
  yearly: 'a year',
};

interface SearchForm {
  query: string;
  city: string;
  state: string;
  category: string;
  employmentType: string;
  workMode: string;
  experience: string;
  minMonthlyPay: string;
  postedWithin: string;
  fresher: boolean;
}

const EMPTY_FORM: SearchForm = {
  query: '',
  city: '',
  state: '',
  category: '',
  employmentType: '',
  workMode: '',
  experience: '',
  minMonthlyPay: '',
  postedWithin: '',
  fresher: false,
};

function formatDate(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

/** Salary exactly as advertised: the provider's currency and period are kept. */
function formatSalary(job: {
  salary_min?: number | null;
  salary_max?: number | null;
  salary_currency?: string | null;
  salary_period?: string | null;
}): string {
  const { salary_min: min, salary_max: max } = job;
  if (min == null && max == null) return '';
  const currency = job.salary_currency || 'INR';
  const compact = (value: number) => {
    try {
      return new Intl.NumberFormat('en-IN', {
        style: 'currency',
        currency,
        maximumFractionDigits: 0,
      }).format(value);
    } catch {
      return `${currency} ${new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(value)}`;
    }
  };
  const range =
    min != null && max != null
      ? `${compact(min)} – ${compact(max)}`
      : min != null
        ? `from ${compact(min)}`
        : `up to ${compact(max as number)}`;
  const period = job.salary_period ? PERIOD_LABEL[job.salary_period.toLowerCase()] : null;
  return period ? `${range} ${period}` : range;
}

function statusChip(status: string | null | undefined): string {
  switch (status) {
    case 'active':
      return 'bg-emerald-50 text-emerald-700';
    case 'stale':
      return 'bg-amber-50 text-amber-700';
    case 'expired':
      return 'bg-slate-100 text-slate-500';
    default:
      return 'bg-slate-100 text-slate-500';
  }
}

const chipClass = 'rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600';

function formToPayload(form: SearchForm, page: number): ProviderSearchRequest {
  return {
    query: form.query.trim() || undefined,
    city: form.city.trim() || undefined,
    state: form.state.trim() || undefined,
    category: form.category || undefined,
    employment_type: form.employmentType || undefined,
    work_mode: form.workMode || undefined,
    experience: form.experience ? Number(form.experience) : undefined,
    min_monthly_pay: form.minMonthlyPay ? Number(form.minMonthlyPay) : undefined,
    posted_within_days: form.postedWithin ? Number(form.postedWithin) : undefined,
    fresher: form.fresher || undefined,
    page,
    page_size: PAGE_SIZE,
    ingest: true,
    fetch_details: true,
  };
}

function hasFilters(form: SearchForm): boolean {
  return Boolean(
    form.query.trim() ||
      form.city.trim() ||
      form.state.trim() ||
      form.category ||
      form.employmentType ||
      form.workMode ||
      form.experience ||
      form.minMonthlyPay ||
      form.postedWithin ||
      form.fresher,
  );
}

export default function LiveJobs() {
  // Provider configuration (never contains credentials)
  const [provider, setProvider] = useState<ProviderStatus | null>(null);
  const [catalog, setCatalog] = useState<ProviderCatalog | null>(null);

  // Search
  const [form, setForm] = useState<SearchForm>(EMPTY_FORM);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<ProviderSearchResponse | null>(null);
  const [lastSearch, setLastSearch] = useState<ProviderSearchRequest | null>(null);

  // Imported rows stored in MySQL
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  // Manual import
  const [showImport, setShowImport] = useState(false);
  const [importFormat, setImportFormat] = useState<'csv' | 'json'>('csv');
  const [importText, setImportText] = useState('');
  const [importing, setImporting] = useState(false);
  const [importSummary, setImportSummary] = useState<ImportSummary | null>(null);

  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const loadImported = useCallback(async (targetPage: number) => {
    setLoading(true);
    try {
      const result = await api.listJobs({
        page: targetPage,
        page_size: PAGE_SIZE,
        source: 'provider',
      });
      setJobs(result.items);
      setTotal(result.total);
      setPage(result.page);
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Could not load imported jobs.',
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadImported(1);
    api
      .getProviderStatuses()
      .then((statuses) => setProvider(statuses.find((item) => item.name === 'jobsforce') ?? null))
      .catch(() => setProvider(null));
    api
      .getProviderCatalog()
      .then(setCatalog)
      .catch(() => setCatalog(null));
  }, [loadImported]);

  const runSearch = useCallback(
    async (payload: ProviderSearchRequest) => {
      setSearching(true);
      setMessage(null);
      try {
        const response = await api.searchProviderJobs(payload);
        setSearchResult(response);
        setLastSearch(payload);
        if (response.results.length === 0) {
          setMessage({
            kind: 'success',
            text: 'The provider returned no listings for these filters.',
          });
        } else {
          const details =
            response.details_fetched > 0
              ? ` Full details fetched for ${response.details_fetched} listing(s).`
              : '';
          setMessage({
            kind: 'success',
            text: `${response.created} new and ${response.updated} updated listing(s) saved.${details}`,
          });
          if (response.created > 0 || response.updated > 0) await loadImported(1);
        }
      } catch (error) {
        setMessage({
          kind: 'error',
          text: error instanceof ApiError ? error.message : 'The provider search failed.',
        });
      } finally {
        setSearching(false);
      }
    },
    [loadImported],
  );

  const onSearch = async (event: FormEvent) => {
    event.preventDefault();
    if (!hasFilters(form)) return;
    await runSearch(formToPayload(form, 1));
  };

  const onImport = async (event: FormEvent) => {
    event.preventDefault();
    setImporting(true);
    setMessage(null);
    setImportSummary(null);
    try {
      const summary =
        importFormat === 'csv'
          ? await api.importJobsCsv(importText)
          : await api.importJobsJson(parseJsonJobs(importText));
      setImportSummary(summary);
      if (summary.created > 0 || summary.updated > 0) {
        setMessage({
          kind: 'success',
          text: `Imported ${summary.created} new job(s), updated ${summary.updated}.`,
        });
        await loadImported(1);
      }
    } catch (error) {
      if (error instanceof SyntaxError) {
        setImportSummary({
          total_rows: 0,
          valid_rows: 0,
          invalid_rows: 0,
          created: 0,
          updated: 0,
          skipped: 0,
          errors: [`Invalid JSON: ${error.message}`],
          messages: [],
        });
      } else {
        setMessage({
          kind: 'error',
          text: error instanceof ApiError ? error.message : 'The import failed.',
        });
      }
    } finally {
      setImporting(false);
    }
  };

  const onDelete = async (job: Job) => {
    if (!window.confirm(`Delete "${job.title}" at ${job.company}?`)) return;
    try {
      await api.deleteJob(job.id);
      setMessage({ kind: 'success', text: `Deleted "${job.title}".` });
      void loadImported(jobs.length === 1 && page > 1 ? page - 1 : page);
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Could not delete the job.',
      });
    }
  };

  const cityOptions = useMemo(
    () => (catalog?.cities ?? []).map((city) => city.name),
    [catalog],
  );

  const inputClass =
    'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-navy-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none';

  return (
    <div>
      <PageHeader
        title="Live Job Discovery"
        subtitle="Search JobsForce India, import CSV/JSON listings and push them into the matching workflow. Listing data stays attributable to its original source."
      />

      <div className="space-y-4">
        {message && <Alert kind={message.kind}>{message.text}</Alert>}

        {/* Provider configuration — status only, never any credentials */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-medium text-navy-500">Provider status</h2>
              {provider === null ? (
                <p className="mt-1 text-sm text-navy-700">Checking provider…</p>
              ) : provider.configured ? (
                <p className="mt-1 text-sm text-emerald-700">
                  {provider.name === 'jobsforce' ? 'JobsForce India' : provider.name} is available
                  (India only, no credentials required).
                </p>
              ) : (
                <p className="mt-1 max-w-2xl text-sm text-amber-700">{provider.message}</p>
              )}
              <p className="mt-1 max-w-2xl text-xs text-slate-500">
                JobsForce India is the only external source. Every listing links back to its
                original page and application form.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowImport((open) => !open)}
              className="rounded-lg border border-brand-600 px-4 py-2 text-sm font-medium text-brand-600 hover:bg-brand-50"
            >
              {showImport ? 'Close importer' : 'Import CSV / JSON'}
            </button>
          </div>
        </section>

        {/* Search form */}
        <form
          onSubmit={onSearch}
          className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-sm font-medium text-navy-500">Search live jobs</h2>
            {catalog?.total_jobs != null && (
              <span className="text-xs text-slate-400">
                {catalog.total_jobs.toLocaleString()} open listing(s) in the catalog
              </span>
            )}
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <label className="block text-sm">
              <span className="text-navy-700">Keywords (title / skills)</span>
              <input
                type="text"
                value={form.query}
                onChange={(event) => setForm({ ...form, query: event.target.value })}
                placeholder="e.g. Python Developer"
                maxLength={200}
                className={inputClass}
              />
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">City</span>
              <input
                type="text"
                list="live-job-cities"
                value={form.city}
                onChange={(event) => setForm({ ...form, city: event.target.value })}
                placeholder="e.g. Bengaluru"
                maxLength={80}
                className={inputClass}
              />
              <datalist id="live-job-cities">
                {cityOptions.map((city) => (
                  <option key={city} value={city} />
                ))}
              </datalist>
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">State</span>
              <input
                type="text"
                value={form.state}
                onChange={(event) => setForm({ ...form, state: event.target.value })}
                placeholder="e.g. Karnataka"
                maxLength={80}
                className={inputClass}
              />
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Category</span>
              <select
                value={form.category}
                onChange={(event) => setForm({ ...form, category: event.target.value })}
                className={inputClass}
              >
                <option value="">Any category</option>
                {(catalog?.categories ?? []).map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.name}
                    {entry.jobs != null ? ` (${entry.jobs})` : ''}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Employment type</span>
              <select
                value={form.employmentType}
                onChange={(event) => setForm({ ...form, employmentType: event.target.value })}
                className={inputClass}
              >
                <option value="">Any</option>
                {EMPLOYMENT_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Work mode</span>
              <select
                value={form.workMode}
                onChange={(event) => setForm({ ...form, workMode: event.target.value })}
                className={inputClass}
              >
                <option value="">Any</option>
                {WORK_MODES.map((mode) => (
                  <option key={mode} value={mode}>
                    {mode}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Experience (years)</span>
              <input
                type="number"
                min={0}
                max={50}
                step={0.5}
                value={form.experience}
                onChange={(event) => setForm({ ...form, experience: event.target.value })}
                placeholder="e.g. 3"
                className={inputClass}
              />
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Minimum monthly pay (INR)</span>
              <input
                type="number"
                min={0}
                step={1000}
                value={form.minMonthlyPay}
                onChange={(event) => setForm({ ...form, minMonthlyPay: event.target.value })}
                placeholder="e.g. 30000"
                className={inputClass}
              />
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Posted within</span>
              <select
                value={form.postedWithin}
                onChange={(event) => setForm({ ...form, postedWithin: event.target.value })}
                className={inputClass}
              >
                <option value="">Any time</option>
                {POSTED_WITHIN.map((days) => (
                  <option key={days} value={days}>
                    {days} day(s)
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-end gap-2 pb-2 text-sm text-navy-700">
              <input
                type="checkbox"
                checked={form.fresher}
                onChange={(event) => setForm({ ...form, fresher: event.target.checked })}
                className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:brand-500"
              />
              Fresher-friendly only
            </label>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={searching || !hasFilters(form)}
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600/90 disabled:opacity-60"
            >
              {searching ? 'Searching…' : 'Search live jobs'}
            </button>
            <button
              type="button"
              onClick={() => {
                setForm(EMPTY_FORM);
              }}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-navy-700 hover:bg-slate-50"
            >
              Reset filters
            </button>
            <span className="text-xs text-slate-400">
              Every filter is applied by the provider; at least one is required.
            </span>
          </div>
        </form>

        {/* Manual import */}
        {showImport && (
          <form
            onSubmit={onImport}
            className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-medium text-navy-500">
                Manual import ({importFormat.toUpperCase()})
              </h2>
              <div className="flex gap-2">
                {(['csv', 'json'] as const).map((format) => (
                  <button
                    key={format}
                    type="button"
                    onClick={() => setImportFormat(format)}
                    className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                      importFormat === format
                        ? 'bg-brand-600 text-white'
                        : 'border border-slate-200 text-navy-700 hover:bg-slate-50'
                    }`}
                  >
                    {format.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>

            <p className="mt-2 text-xs text-navy-500">
              {importFormat === 'csv'
                ? 'CSV needs a header row with at least: title, company, description. Optional: location, required_skills, preferred_skills, minimum_experience, employment_type.'
                : 'JSON must be an array of objects with at least title, company and description.'}
            </p>

            <textarea
              value={importText}
              onChange={(event) => setImportText(event.target.value)}
              rows={8}
              spellCheck={false}
              placeholder={
                importFormat === 'csv'
                  ? 'title,company,description,location,required_skills\nData Engineer,Acme,Build pipelines,Remote,"Python, SQL"'
                  : '[{"title": "Data Engineer", "company": "Acme", "description": "Build pipelines"}]'
              }
              className={`${inputClass} mt-3 font-mono`}
            />

            <div className="mt-3 flex gap-2">
              <button
                type="submit"
                disabled={importing || !importText.trim()}
                className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600/90 disabled:opacity-60"
              >
                {importing ? 'Importing…' : 'Run import'}
              </button>
            </div>

            {importSummary && (
              <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm">
                <p className="font-medium text-navy-700">
                  {importSummary.total_rows} row(s): {importSummary.created} created,{' '}
                  {importSummary.updated} updated, {importSummary.skipped} skipped,{' '}
                  {importSummary.invalid_rows} invalid.
                </p>
                {importSummary.errors.length > 0 && (
                  <ul className="mt-2 list-inside list-disc space-y-1 text-rose-600">
                    {importSummary.errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                )}
                {importSummary.messages.length > 0 && (
                  <ul className="mt-2 list-inside list-disc space-y-1 text-navy-500">
                    {importSummary.messages.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </form>
        )}

        {/* Live search results */}
        {searchResult && (
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-medium text-navy-500">
                {searchResult.query
                  ? `Results for “${searchResult.query}”`
                  : 'Filtered results'}
                {' · '}
                {searchResult.city || searchResult.state || 'India'}
              </h2>
              <span className="text-xs text-slate-400">
                Page {searchResult.page} · {searchResult.results.length} shown
                {searchResult.total_available != null
                  ? ` of ${searchResult.total_available} matching`
                  : ''}
              </span>
            </div>

            {searchResult.results.length === 0 ? (
              <div className="mt-4">
                <EmptyState
                  title="No live listings found"
                  description="Try broader keywords, another city, or remove the experience/pay/fresher filters."
                />
              </div>
            ) : (
              <ul className="mt-4 space-y-3">
                {searchResult.results.map((result) => (
                  <LiveResult key={result.provider_job_id} result={result} />
                ))}
              </ul>
            )}

            <div className="mt-4 flex items-center justify-between">
              <button
                type="button"
                disabled={searching || searchResult.page <= 1 || !lastSearch}
                onClick={() => {
                  if (lastSearch) void runSearch({ ...lastSearch, page: searchResult.page - 1 });
                }}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-slate-50 disabled:opacity-40"
              >
                ← Previous page
              </button>
              <button
                type="button"
                disabled={searching || !searchResult.has_more || !lastSearch}
                onClick={() => {
                  if (lastSearch) void runSearch({ ...lastSearch, page: searchResult.page + 1 });
                }}
                className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-slate-50 disabled:opacity-40"
              >
                Next page →
              </button>
            </div>

            {(searchResult.notes.length > 0 || searchResult.provider_notes.length > 0) && (
              <ul className="mt-4 space-y-1 text-xs text-slate-500">
                {[...searchResult.notes, ...searchResult.provider_notes].map((note) => (
                  <li key={note}>· {note}</li>
                ))}
              </ul>
            )}
          </section>
        )}

        {/* Imported jobs persisted in MySQL */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-navy-500">Imported jobs</h2>
            {!loading && <span className="text-xs text-slate-400">{total} total</span>}
          </div>

          {loading ? (
            <p className="mt-4 text-sm text-navy-500">Loading…</p>
          ) : jobs.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No imported jobs yet"
                description="Run a live search above or import a CSV/JSON file. Manually created jobs are listed on the Jobs page and are never overwritten by imports."
              />
            </div>
          ) : (
            <ul className="mt-4 space-y-3">
              {jobs.map((job) => (
                <li key={job.id} className="rounded-lg border border-slate-200 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-semibold text-navy-900">{job.title}</h3>
                        <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-brand-600">
                          {job.provider === 'jobsforce'
                            ? 'JobsForce India'
                            : (job.provider ?? job.source ?? 'live')}
                        </span>
                        {job.listing_status && (
                          <span
                            className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${statusChip(job.listing_status)}`}
                          >
                            {job.listing_status}
                          </span>
                        )}
                        {job.employment_type && (
                          <span className={chipClass}>{job.employment_type}</span>
                        )}
                        {job.work_mode && <span className={chipClass}>{job.work_mode}</span>}
                        {job.category && <span className={chipClass}>{job.category}</span>}
                        {job.hiring_type === 'agency' && (
                          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                            agency · client confidential
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs text-navy-500">
                        {job.company}
                        {job.location ? ` · ${job.location}` : ''}
                        {job.posted_date ? ` · posted ${formatDate(job.posted_date)}` : ''}
                        {job.expires_date ? ` · closes ${formatDate(job.expires_date)}` : ''}
                      </p>
                      {formatSalary(job) && (
                        <p className="mt-0.5 text-xs text-navy-500">{formatSalary(job)}</p>
                      )}
                    </div>

                    <div className="flex flex-wrap gap-2">
                      {job.listing_url && (
                        <a
                          href={job.listing_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-slate-50"
                        >
                          View listing ↗
                        </a>
                      )}
                      {job.application_url && (
                        <a
                          href={job.application_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="rounded-lg border border-brand-500 px-3 py-1.5 text-xs font-medium text-brand-600 hover:bg-brand-50"
                        >
                          Apply on source ↗
                        </a>
                      )}
                      <Link
                        to={`/resume-matching?job=${job.id}`}
                        className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600/90"
                      >
                        Match this job
                      </Link>
                      <button
                        type="button"
                        onClick={() => void onDelete(job)}
                        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          <Pager
            page={page}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={(next) => void loadImported(next)}
          />
        </section>
      </div>
    </div>
  );
}

function LiveResult({ result }: { result: ProviderJob }) {
  const salary = formatSalary(result);
  const isAgency = result.hiring_type === 'agency';
  return (
    <li className="rounded-lg border border-slate-200 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-navy-900">{result.title}</h3>
            <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-brand-600">
              {result.provider === 'jobsforce' ? 'JobsForce India' : result.provider}
            </span>
            {result.listing_status && (
              <span
                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${statusChip(result.listing_status)}`}
              >
                {result.listing_status}
              </span>
            )}
            {result.employment_type && <span className={chipClass}>{result.employment_type}</span>}
            {result.work_mode && <span className={chipClass}>{result.work_mode}</span>}
            {result.category && <span className={chipClass}>{result.category}</span>}
            {isAgency && (
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                agency · client confidential
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-navy-500">
            {result.company}
            {result.location ? ` · ${result.location}` : ''}
            {result.posted_date ? ` · posted ${formatDate(result.posted_date)}` : ''}
            {result.expires_date ? ` · closes ${formatDate(result.expires_date)}` : ''}
          </p>
          {salary && <p className="mt-0.5 text-xs text-navy-500">{salary}</p>}
        </div>

        <div className="flex flex-wrap gap-2">
          {result.listing_url && (
            <a
              href={result.listing_url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-slate-50"
            >
              View listing ↗
            </a>
          )}
          {result.application_url && (
            <a
              href={result.application_url}
              target="_blank"
              rel="noopener noreferrer"
              title="Opens the original posting on the provider's site"
              className="rounded-lg border border-brand-500 px-3 py-1.5 text-xs font-medium text-brand-600 hover:bg-brand-50"
            >
              Apply on source ↗
            </a>
          )}
          {result.job_id != null && (
            <Link
              to={`/resume-matching?job=${result.job_id}`}
              className="rounded-lg bg-brand-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600/90"
            >
              Match this job
            </Link>
          )}
        </div>
      </div>
    </li>
  );
}

/** Parse the textarea as a JSON array, surfacing a readable syntax error. */
function parseJsonJobs(text: string): unknown[] {
  const data: unknown = JSON.parse(text);
  if (!Array.isArray(data)) {
    throw new SyntaxError('The JSON payload must be an array of job objects.');
  }
  return data;
}
