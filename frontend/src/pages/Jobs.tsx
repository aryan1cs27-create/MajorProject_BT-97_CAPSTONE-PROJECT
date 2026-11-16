import { useCallback, useEffect, useState, type FormEvent } from 'react';

import Alert from '../components/Alert';
import EmptyState from '../components/EmptyState';
import PageHeader from '../components/PageHeader';
import Pager from '../components/Pager';
import { ApiError, api } from '../services/api';
import type { Job, JobInput } from '../types';

const PAGE_SIZE = 10;

const EMPLOYMENT_TYPES = ['full-time', 'part-time', 'contract', 'internship', 'temporary'] as const;

const EMPTY_FORM: JobInput = {
  title: '',
  company: '',
  location: '',
  description: '',
  required_skills: [],
  preferred_skills: [],
  provider: null,
  provider_job_id: null,
  application_url: null,
  minimum_experience: null,
  employment_type: null,
};

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function parseSkills(value: string): string[] {
  return value
    .split(',')
    .map((skill) => skill.trim())
    .filter(Boolean);
}

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  // Filters
  const [titleFilter, setTitleFilter] = useState('');
  const [companyFilter, setCompanyFilter] = useState('');
  const [locationFilter, setLocationFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');

  // Create form
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<JobInput>(EMPTY_FORM);
  const [requiredSkillsText, setRequiredSkillsText] = useState('');
  const [preferredSkillsText, setPreferredSkillsText] = useState('');
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const load = useCallback(
    async (targetPage: number) => {
      setLoading(true);
      try {
        const result = await api.listJobs({
          page: targetPage,
          page_size: PAGE_SIZE,
          title: titleFilter || undefined,
          company: companyFilter || undefined,
          location: locationFilter || undefined,
          employment_type: typeFilter || undefined,
        });
        setJobs(result.items);
        setTotal(result.total);
        setPage(result.page);
      } catch (error) {
        setMessage({
          kind: 'error',
          text: error instanceof ApiError ? error.message : 'Could not load jobs.',
        });
      } finally {
        setLoading(false);
      }
    },
    [titleFilter, companyFilter, locationFilter, typeFilter],
  );

  useEffect(() => {
    void load(1);
  }, [load]);

  const onFilter = (event: FormEvent) => {
    event.preventDefault();
    void load(1);
  };

  const onClearFilters = () => {
    setTitleFilter('');
    setCompanyFilter('');
    setLocationFilter('');
    setTypeFilter('');
  };

  const onCreate = async (event: FormEvent) => {
    event.preventDefault();
    setMessage(null);

    if (!form.title.trim() || !form.company.trim() || !form.description.trim()) {
      setMessage({ kind: 'error', text: 'Title, company and description are required.' });
      return;
    }

    setSaving(true);
    try {
      const created = await api.createJob({
        ...form,
        title: form.title.trim(),
        company: form.company.trim(),
        location: form.location?.trim() || null,
        description: form.description.trim(),
        required_skills: parseSkills(requiredSkillsText),
        preferred_skills: parseSkills(preferredSkillsText),
      });
      setMessage({ kind: 'success', text: `Created job "${created.title}" at ${created.company}.` });
      setForm(EMPTY_FORM);
      setRequiredSkillsText('');
      setPreferredSkillsText('');
      setShowForm(false);
      await load(1);
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Could not create the job.',
      });
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (job: Job) => {
    if (!window.confirm(`Delete "${job.title}" at ${job.company}?`)) return;
    try {
      await api.deleteJob(job.id);
      setMessage({ kind: 'success', text: `Deleted "${job.title}".` });
      await load(jobs.length === 1 && page > 1 ? page - 1 : page);
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Could not delete the job.',
      });
    }
  };

  const inputClass =
    'mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-navy-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none';

  return (
    <div>
      <PageHeader
        title="Jobs"
        subtitle="Job postings stored in MySQL — sample rows created by the seed script are labelled."
      />

      <div className="space-y-4">
        {message && <Alert kind={message.kind}>{message.text}</Alert>}

        {/* Filters */}
        <form onSubmit={onFilter} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="block text-sm">
              <span className="text-navy-700">Title</span>
              <input
                type="text"
                value={titleFilter}
                onChange={(event) => setTitleFilter(event.target.value)}
                placeholder="e.g. Engineer"
                className={inputClass}
              />
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Company</span>
              <input
                type="text"
                value={companyFilter}
                onChange={(event) => setCompanyFilter(event.target.value)}
                placeholder="e.g. Northwind"
                className={inputClass}
              />
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Location</span>
              <input
                type="text"
                value={locationFilter}
                onChange={(event) => setLocationFilter(event.target.value)}
                placeholder="e.g. Remote"
                className={inputClass}
              />
            </label>
            <label className="block text-sm">
              <span className="text-navy-700">Employment type</span>
              <select
                value={typeFilter}
                onChange={(event) => setTypeFilter(event.target.value)}
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
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="submit"
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600/90"
            >
              Apply filters
            </button>
            <button
              type="button"
              onClick={onClearFilters}
              className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-navy-700 hover:bg-slate-50"
            >
              Clear
            </button>
            <span className="grow" />
            <button
              type="button"
              onClick={() => setShowForm((open) => !open)}
              className="rounded-lg border border-brand-600 px-4 py-2 text-sm font-medium text-brand-600 hover:bg-brand-50"
            >
              {showForm ? 'Close form' : '+ New job'}
            </button>
          </div>
        </form>

        {/* Create form */}
        {showForm && (
          <form onSubmit={onCreate} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-medium text-navy-500">Create a job</h2>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="text-navy-700">Title *</span>
                <input
                  type="text"
                  value={form.title}
                  onChange={(event) => setForm({ ...form, title: event.target.value })}
                  maxLength={255}
                  className={inputClass}
                  required
                />
              </label>
              <label className="block text-sm">
                <span className="text-navy-700">Company *</span>
                <input
                  type="text"
                  value={form.company}
                  onChange={(event) => setForm({ ...form, company: event.target.value })}
                  maxLength={255}
                  className={inputClass}
                  required
                />
              </label>
              <label className="block text-sm">
                <span className="text-navy-700">Location</span>
                <input
                  type="text"
                  value={form.location ?? ''}
                  onChange={(event) => setForm({ ...form, location: event.target.value })}
                  maxLength={255}
                  placeholder="Optional"
                  className={inputClass}
                />
              </label>
              <label className="block text-sm">
                <span className="text-navy-700">Employment type</span>
                <select
                  value={form.employment_type ?? ''}
                  onChange={(event) =>
                    setForm({ ...form, employment_type: event.target.value || null })
                  }
                  className={inputClass}
                >
                  <option value="">Not specified</option>
                  {EMPLOYMENT_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                <span className="text-navy-700">Minimum experience (years)</span>
                <input
                  type="number"
                  min={0}
                  max={50}
                  step={0.5}
                  value={form.minimum_experience ?? ''}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      minimum_experience: event.target.value === '' ? null : Number(event.target.value),
                    })
                  }
                  placeholder="Optional"
                  className={inputClass}
                />
              </label>
              <label className="block text-sm">
                <span className="text-navy-700">Required skills</span>
                <input
                  type="text"
                  value={requiredSkillsText}
                  onChange={(event) => setRequiredSkillsText(event.target.value)}
                  placeholder="Comma separated, e.g. Python, SQL"
                  className={inputClass}
                />
              </label>
              <label className="block text-sm sm:col-span-2">
                <span className="text-navy-700">Preferred skills</span>
                <input
                  type="text"
                  value={preferredSkillsText}
                  onChange={(event) => setPreferredSkillsText(event.target.value)}
                  placeholder="Comma separated, e.g. Docker, pytest"
                  className={inputClass}
                />
              </label>
              <label className="block text-sm sm:col-span-2">
                <span className="text-navy-700">Description *</span>
                <textarea
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                  rows={4}
                  className={inputClass}
                  required
                />
              </label>
            </div>

            <div className="mt-4 flex gap-2">
              <button
                type="submit"
                disabled={saving}
                className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600/90 disabled:opacity-60"
              >
                {saving ? 'Saving…' : 'Create job'}
              </button>
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-medium text-navy-700 hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        {/* List */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-navy-500">Job postings</h2>
            {!loading && <span className="text-xs text-slate-400">{total} total</span>}
          </div>

          {loading ? (
            <p className="mt-4 text-sm text-navy-500">Loading…</p>
          ) : jobs.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title={total === 0 ? 'No jobs yet' : 'No jobs match these filters'}
                description={
                  total === 0
                    ? 'Create a job with the form above, or run the seed script (backend/scripts/seed_jobs.py) to insert labelled sample jobs.'
                    : 'Adjust or clear the filters to see more postings.'
                }
              />
            </div>
          ) : (
            <ul className="mt-4 space-y-3">
              {jobs.map((job) => (
                <li key={job.id} className="rounded-lg border border-slate-200 p-4">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-sm font-semibold text-navy-900">{job.title}</h3>
                        {job.is_sample && (
                          <span
                            title="Inserted by the sample seed script, not by a user"
                            className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-500"
                          >
                            Sample data
                          </span>
                        )}
                        {job.employment_type && (
                          <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-medium text-brand-600">
                            {job.employment_type}
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 text-xs text-navy-500">
                        {job.company}
                        {job.location ? ` · ${job.location}` : ''}
                        {job.minimum_experience !== null ? ` · ${job.minimum_experience}+ yrs` : ''}
                        {` · created ${formatDate(job.created_at)}`}
                      </p>

                      {job.required_skills.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {job.required_skills.map((skill) => (
                            <span
                              key={skill}
                              className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-navy-700"
                            >
                              {skill}
                            </span>
                          ))}
                          {job.preferred_skills.map((skill) => (
                            <span
                              key={`pref-${skill}`}
                              className="rounded-md border border-slate-200 px-2 py-0.5 text-[11px] text-navy-500"
                            >
                              {skill}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => setExpandedId(expandedId === job.id ? null : job.id)}
                        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-slate-50"
                      >
                        {expandedId === job.id ? 'Hide' : 'Details'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void onDelete(job)}
                        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  {expandedId === job.id && (
                    <p className="mt-3 whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-sm leading-relaxed text-navy-700">
                      {job.description}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}

          <Pager page={page} pageSize={PAGE_SIZE} total={total} onPageChange={(next) => void load(next)} />
        </section>
      </div>
    </div>
  );
}
