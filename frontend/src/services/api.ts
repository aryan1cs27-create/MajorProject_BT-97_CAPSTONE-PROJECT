import type {
  ApiErrorBody,
  CalculateResponse,
  DatabaseHealthResponse,
  FeedbackEvent,
  FeedbackInput,
  FeedbackSummary,
  HealthResponse,
  ImprovementPlan,
  Job,
  JobFilters,
  JobInput,
  MatchExplanation,
  MatchListResponse,
  MatchResult,
  ImportSummary,
  Paginated,
  ProviderCatalog,
  ProviderSearchRequest,
  ProviderSearchResponse,
  ProviderStatus,
  RankMode,
  ResumeDetail,
  ResumeSummary,
  ResumeSuggestions,
  ResumeUploadResponse,
  SkillGapResponse,
} from '../types';

/**
 * Base URL of the FastAPI backend, configured through the frontend
 * environment variable `VITE_API_BASE_URL` (see `.env.example`).
 * The backend currently runs on port 8001 (8000 was blocked by WinError 10013).
 */
const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8001/api';

/**
 * Local API token (Phase 5.1). The backend only enforces it when `API_TOKEN`
 * is configured; when it is, `VITE_API_TOKEN` must match it. Never a
 * privileged secret — this is a single-operator local token.
 */
const API_TOKEN: string = (import.meta.env.VITE_API_TOKEN as string | undefined) ?? '';

function authHeaders(): Record<string, string> {
  return API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {};
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code = 'error') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

function absoluteUrl(path: string): string {
  return `${API_BASE_URL.replace(/\/$/, '')}/${path.replace(/^\//, '')}`;
}

function messageFrom(body: unknown, fallback: string): string {
  const parsed = body as ApiErrorBody | null;
  return parsed?.error?.message ?? parsed?.detail ?? fallback;
}

/** Thin fetch wrapper: JSON in, JSON out, non-2xx becomes an ApiError. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(absoluteUrl(path), {
      ...init,
      headers: {
        Accept: 'application/json',
        ...authHeaders(),
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError('Cannot reach the backend server.', 0, 'network_error');
  }

  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const parsed = body as ApiErrorBody | null;
    throw new ApiError(
      messageFrom(body, `Request failed with status ${response.status}`),
      response.status,
      parsed?.error?.code ?? 'error',
    );
  }

  return body as T;
}

/** Multipart upload with progress reporting (XHR — fetch has no upload progress). */
export function uploadResume(
  file: File,
  candidateName: string | null,
  onProgress?: (percent: number) => void,
): Promise<ResumeUploadResponse> {
  const formData = new FormData();
  formData.append('file', file);
  if (candidateName && candidateName.trim()) {
    formData.append('candidate_name', candidateName.trim());
  }

  return new Promise<ResumeUploadResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', absoluteUrl('/resumes/upload'));
    xhr.responseType = 'text';
    xhr.setRequestHeader('Accept', 'application/json');
    if (API_TOKEN) xhr.setRequestHeader('Authorization', `Bearer ${API_TOKEN}`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };

    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        onProgress?.(100);
        resolve(body as ResumeUploadResponse);
      } else {
        const parsed = body as ApiErrorBody | null;
        reject(
          new ApiError(
            messageFrom(body, `Upload failed with status ${xhr.status}`),
            xhr.status,
            parsed?.error?.code ?? 'upload_failed',
          ),
        );
      }
    };

    xhr.onerror = () => reject(new ApiError('Cannot reach the backend server.', 0, 'network_error'));
    xhr.ontimeout = () => reject(new ApiError('The upload timed out.', 0, 'timeout'));
    xhr.send(formData);
  });
}

function queryString(params: object): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : '';
}

export const api = {
  baseUrl: API_BASE_URL,

  // Health
  getHealth: () => request<HealthResponse>('/health'),
  getDatabaseHealth: () => request<DatabaseHealthResponse>('/health/db'),

  // Resumes
  listResumes: (page = 1, pageSize = 10) =>
    request<Paginated<ResumeSummary>>(`/resumes${queryString({ page, page_size: pageSize })}`),
  getResume: (id: number) => request<ResumeDetail>(`/resumes/${id}`),
  deleteResume: (id: number) => request<null>(`/resumes/${id}`, { method: 'DELETE' }),
  uploadResume,

  // Jobs
  listJobs: (filters: JobFilters = {}) =>
    request<Paginated<Job>>(`/jobs${queryString(filters)}`),
  getJob: (id: number) => request<Job>(`/jobs/${id}`),
  createJob: (payload: JobInput) =>
    request<Job>('/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  updateJob: (id: number, payload: Partial<JobInput>) =>
    request<Job>(`/jobs/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  deleteJob: (id: number) => request<null>(`/jobs/${id}`, { method: 'DELETE' }),

  // Phase 5/7 — live provider discovery & manual import
  getProviderStatuses: () => request<ProviderStatus[]>('/jobs/providers'),
  getProviderCatalog: () => request<ProviderCatalog>('/jobs/provider/catalog'),
  searchProviderJobs: (payload: ProviderSearchRequest) =>
    request<ProviderSearchResponse>('/jobs/provider/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  importJobsCsv: (content: string) =>
    request<ImportSummary>('/jobs/import/csv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    }),
  importJobsJson: (jobs: unknown[]) =>
    request<ImportSummary>('/jobs/import/json', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jobs }),
    }),

  // Matching
  calculateMatches: (resumeId: number, jobIds?: number[]) =>
    request<CalculateResponse>(`/matches/calculate/${resumeId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(jobIds && jobIds.length ? { job_ids: jobIds } : {}),
    }),
  getMatchesForResume: (
    resumeId: number,
    page = 1,
    pageSize = 50,
    rank: RankMode = 'engine',
  ) =>
    request<MatchListResponse>(
      `/matches/resume/${resumeId}${queryString({ page, page_size: pageSize, rank })}`,
    ),
  getMatchesForJob: (jobId: number, page = 1, pageSize = 50) =>
    request<MatchListResponse>(`/matches/job/${jobId}${queryString({ page, page_size: pageSize })}`),
  getMatch: (matchId: number) => request<MatchResult>(`/matches/${matchId}`),

  // Phase 4 — explanations & recommendations for one saved match
  getMatchExplanation: (matchId: number) =>
    request<MatchExplanation>(`/matches/${matchId}/explanation`),
  getMatchSkillGaps: (matchId: number) =>
    request<SkillGapResponse>(`/matches/${matchId}/skill-gaps`),
  getImprovementPlan: (matchId: number) =>
    request<ImprovementPlan>(`/matches/${matchId}/improvement-plan`),
  getResumeSuggestions: (matchId: number) =>
    request<ResumeSuggestions>(`/matches/${matchId}/resume-suggestions`),

  // Phase 6 — feedback capture and feedback-aware ranking
  submitFeedback: (matchId: number, payload: FeedbackInput) =>
    request<FeedbackEvent>(`/matches/${matchId}/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  getFeedbackSummary: (resumeId: number) =>
    request<FeedbackSummary>(`/matches/resume/${resumeId}/feedback-summary`),
};
