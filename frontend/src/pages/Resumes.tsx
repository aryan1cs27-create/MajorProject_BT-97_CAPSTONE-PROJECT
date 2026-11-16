import { useCallback, useEffect, useState, type FormEvent } from 'react';

import Alert from '../components/Alert';
import EmptyState from '../components/EmptyState';
import PageHeader from '../components/PageHeader';
import Pager from '../components/Pager';
import { ApiError, api } from '../services/api';
import type { ResumeDetail, ResumeSummary } from '../types';

const PAGE_SIZE = 10;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = '.pdf,.docx';

const STATUS_LABELS: Record<string, string> = {
  processed: 'Processed',
  no_text: 'No text found',
  pending: 'Pending',
  failed: 'Failed',
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export default function Resumes() {
  const [resumes, setResumes] = useState<ResumeSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  const [file, setFile] = useState<File | null>(null);
  const [candidateName, setCandidateName] = useState('');
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);

  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ResumeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async (targetPage: number) => {
    setLoading(true);
    try {
      const result = await api.listResumes(targetPage, PAGE_SIZE);
      setResumes(result.items);
      setTotal(result.total);
      setPage(result.page);
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Could not load resumes.',
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(1);
  }, [load]);

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setMessage(null);

    if (!file) {
      setMessage({ kind: 'error', text: 'Choose a PDF or DOCX file first.' });
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setMessage({ kind: 'error', text: 'File is larger than the 5 MB limit.' });
      return;
    }

    setUploading(true);
    setProgress(0);
    try {
      const created = await api.uploadResume(file, candidateName, setProgress);
      setMessage({
        kind: 'success',
        text: `Uploaded "${created.original_filename}" (${created.extracted_chars} characters extracted, status: ${
          STATUS_LABELS[created.processing_status] ?? created.processing_status
        }).`,
      });
      setFile(null);
      setCandidateName('');
      const input = document.getElementById('resume-file') as HTMLInputElement | null;
      if (input) input.value = '';
      await load(1);
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Upload failed.',
      });
    } finally {
      setUploading(false);
      setProgress(0);
    }
  };

  const onPickFile = (selected: File | null) => {
    setMessage(null);
    if (selected && !selected.name.toLowerCase().match(/\.(pdf|docx)$/)) {
      setMessage({ kind: 'error', text: 'Only PDF and DOCX files are accepted.' });
      setFile(null);
      return;
    }
    setFile(selected);
  };

  const toggleDetail = async (id: number) => {
    if (openId === id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(id);
    setDetail(null);
    setDetailLoading(true);
    try {
      setDetail(await api.getResume(id));
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Could not load the resume.',
      });
      setOpenId(null);
    } finally {
      setDetailLoading(false);
    }
  };

  const onDelete = async (resume: ResumeSummary) => {
    const label = resume.candidate_name ?? resume.original_filename;
    if (!window.confirm(`Delete the resume for ${label}? This also removes the stored file.`)) return;
    try {
      await api.deleteResume(resume.id);
      setMessage({ kind: 'success', text: `Deleted resume for ${label}.` });
      setOpenId(null);
      setDetail(null);
      await load(resumes.length === 1 && page > 1 ? page - 1 : page);
    } catch (error) {
      setMessage({
        kind: 'error',
        text: error instanceof ApiError ? error.message : 'Could not delete the resume.',
      });
    }
  };

  return (
    <div>
      <PageHeader
        title="Resumes"
        subtitle="Upload candidate resumes (PDF or DOCX); text is extracted and stored in MySQL."
      />

      <div className="space-y-4">
        {message && <Alert kind={message.kind}>{message.text}</Alert>}

        {/* Upload form */}
        <form onSubmit={onSubmit} className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-medium text-navy-500">Upload a resume</h2>

          <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="block text-sm">
              <span className="text-navy-700">Candidate name</span>
              <input
                type="text"
                value={candidateName}
                onChange={(event) => setCandidateName(event.target.value)}
                maxLength={255}
                placeholder="Optional"
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-navy-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none"
              />
            </label>

            <label className="block text-sm">
              <span className="text-navy-700">Document</span>
              <input
                id="resume-file"
                type="file"
                accept={ACCEPTED_EXTENSIONS}
                onChange={(event) => onPickFile(event.target.files?.[0] ?? null)}
                className="mt-1 w-full cursor-pointer rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-navy-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-navy-700"
              />
            </label>

            <button
              type="submit"
              disabled={uploading}
              className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600/90 disabled:opacity-60"
            >
              {uploading ? `Uploading ${progress}%` : 'Upload'}
            </button>
          </div>

          <p className="mt-2 text-xs text-slate-400">PDF or DOCX · maximum 5 MB.</p>

          {uploading && (
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-brand-500 transition-all"
                style={{ width: `${Math.max(progress, 5)}%` }}
              />
            </div>
          )}
        </form>

        {/* List */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium text-navy-500">Uploaded resumes</h2>
            {!loading && <span className="text-xs text-slate-400">{total} total</span>}
          </div>

          {loading ? (
            <p className="mt-4 text-sm text-navy-500">Loading…</p>
          ) : resumes.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No resumes yet"
                description="Upload a PDF or DOCX above — extracted text will be stored in MySQL and listed here."
              />
            </div>
          ) : (
            <ul className="mt-4 divide-y divide-slate-100">
              {resumes.map((resume) => (
                <li key={resume.id} className="py-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-xs font-semibold uppercase text-brand-600">
                      {resume.file_type}
                    </span>

                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-navy-900">
                        {resume.candidate_name ?? 'Unnamed candidate'}
                      </p>
                      <p className="truncate text-xs text-navy-500">
                        {resume.original_filename} · {formatBytes(resume.file_size)} ·{' '}
                        {formatDate(resume.uploaded_at)}
                      </p>
                    </div>

                    <span
                      className={
                        resume.processing_status === 'processed'
                          ? 'rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-700'
                          : 'rounded-full bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700'
                      }
                    >
                      {STATUS_LABELS[resume.processing_status] ?? resume.processing_status}
                    </span>

                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void toggleDetail(resume.id)}
                        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-navy-700 hover:bg-slate-50"
                      >
                        {openId === resume.id ? 'Hide' : 'View'}
                      </button>
                      <button
                        type="button"
                        onClick={() => void onDelete(resume)}
                        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50"
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  {openId === resume.id && (
                    <div className="mt-3 rounded-lg bg-slate-50 p-4">
                      {detailLoading || !detail ? (
                        <p className="text-sm text-navy-500">Loading extracted text…</p>
                      ) : (
                        <>
                          <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                            Extracted text ({detail.extracted_text.length} characters)
                          </p>
                          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-navy-700">
                            {detail.extracted_text || 'No extractable text in this document.'}
                          </pre>
                        </>
                      )}
                    </div>
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
