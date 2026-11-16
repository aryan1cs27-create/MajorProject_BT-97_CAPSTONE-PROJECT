/** Shared API response types (mirrors the FastAPI Pydantic schemas). */

export interface HealthResponse {
  status: 'ok' | 'error';
  app: string;
  version: string;
  environment: string;
}

export interface DatabaseStatus {
  connected: boolean;
  detail: string;
}

export interface DatabaseHealthResponse {
  status: 'ok' | 'error';
  database: DatabaseStatus;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/* ------------------------------- Jobs ------------------------------------ */

export type EmploymentType = 'full-time' | 'part-time' | 'contract' | 'internship' | 'temporary';

export interface Job {
  id: number;
  title: string;
  company: string;
  location: string | null;
  description: string;
  required_skills: string[];
  preferred_skills: string[];
  minimum_experience: number | null;
  employment_type: string | null;
  /** true for rows inserted by the sample-data seed script */
  is_sample: boolean;
  created_at: string;
  updated_at: string;
  /** Live provider name (e.g. 'jobsforce'); null for manual/imported rows */
  provider: string | null;
  /** Provider's internal job ID (unique within the provider's system) */
  provider_job_id: string | null;
  /** Canonical application URL from the provider */
  application_url: string | null;
  /** Application tracking status: saved | applied | interview | rejected | offer */
  application_status?: string | null;
  /* --- Phase 5: provenance of the row (optional so JobInput stays valid) --- */
  /** Where the row came from: provider | import | manual | sample */
  source?: 'provider' | 'import' | 'manual' | 'sample';
  /** When the provider published the listing (UTC) */
  posted_date?: string | null;
  /** First time the listing was imported / last time it was seen again */
  fetched_at?: string | null;
  verified_at?: string | null;
  /** Lifecycle of an imported listing: active | stale | expired */
  listing_status?: string | null;
  /** Advertised salary range, when the provider supplies one */
  salary_min?: number | null;
  salary_max?: number | null;
  /** Salary unit: the provider states currency + period, never converted */
  salary_currency?: string | null;
  salary_period?: string | null; // hourly | daily | weekly | monthly | yearly
  /** Canonical job page on the provider's site (application_url is the form) */
  listing_url?: string | null;
  /** remote | hybrid | onsite */
  work_mode?: string | null;
  category?: string | null;
  /** Date the listing closes (YYYY-MM-DD) */
  expires_date?: string | null;
  /** employer | portal | agency — agency listings hide the client employer */
  hiring_type?: string | null;
}

export type JobInput = Omit<Job, 'id' | 'is_sample' | 'created_at' | 'updated_at'>;

export interface JobFilters {
  page?: number;
  page_size?: number;
  title?: string;
  company?: string;
  location?: string;
  employment_type?: string;
  /** provider/live | import | manual | sample */
  source?: string;
  category?: string;
  work_mode?: string;
  provider?: string;
}

/* --------------------------- Phase 5: live jobs -------------------------- */

/** Configuration state of a live provider — never contains credentials. */
export interface ProviderStatus {
  name: string;
  configured: boolean;
  default_country: string;
  message: string | null;
}

/**
 * Body for POST /jobs/provider/search (JobsForce India).
 *
 * `query` is optional — a search may consist of filters only. `location` is a
 * shorthand for `city` and `remote` for `work_mode: "remote"`.
 */
export interface ProviderSearchRequest {
  query?: string;
  location?: string;
  city?: string;
  state?: string;
  category?: string;
  employment_type?: string;
  work_mode?: string;
  remote?: boolean;
  fresher?: boolean;
  experience?: number;
  min_monthly_pay?: number;
  posted_within_days?: number;
  page?: number;
  page_size?: number;
  ingest?: boolean;
  fetch_details?: boolean;
}

/** One live result (already persisted when `job_id` is set). */
export interface ProviderJob {
  job_id: number | null;
  provider: string;
  provider_job_id: string;
  title: string;
  company: string;
  location: string | null;
  description: string | null;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  salary_period: string | null;
  employment_type: string | null;
  work_mode: string | null;
  category: string | null;
  listing_url: string | null;
  application_url: string | null;
  posted_date: string | null;
  expires_date: string | null;
  hiring_type: string | null;
  listing_status: string | null;
  has_details: boolean;
  source: string;
}

export interface ProviderSearchResponse {
  provider: string;
  query: string;
  location: string | null;
  city: string | null;
  state: string | null;
  category: string | null;
  country: string;
  work_mode: string | null;
  remote: boolean;
  fresher: boolean | null;
  experience: number | null;
  min_monthly_pay: number | null;
  posted_within_days: number | null;
  employment_type: string | null;
  page: number;
  page_size: number;
  results: ProviderJob[];
  total_available: number | null;
  has_more: boolean;
  next_page: number | null;
  created: number;
  updated: number;
  details_fetched: number;
  details_failed: number;
  notes: string[];
  provider_notes: string[];
}

/** Filter values the provider accepts (GET /jobs/provider/catalog). */
export interface CatalogEntry {
  name: string;
  jobs: number | null;
  url: string | null;
}

export interface CatalogValue {
  value: string;
  jobs: number | null;
}

export interface ProviderCatalog {
  provider: string;
  total_jobs: number | null;
  newest_job: string | null;
  cities: CatalogEntry[];
  categories: CatalogEntry[];
  job_types: CatalogValue[];
  work_modes: CatalogValue[];
  notes: string[];
}

/** Result summary of a CSV/JSON import. */
export interface ImportSummary {
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
  messages: string[];
}

/* ------------------------------ Resumes ---------------------------------- */

export interface ResumeSummary {
  id: number;
  candidate_name: string | null;
  original_filename: string;
  file_type: string;
  file_size: number;
  processing_status: string;
  uploaded_at: string;
  text_preview: string;
}

export interface ResumeDetail extends ResumeSummary {
  extracted_text: string;
  skills: string[];
  stored_filename: string;
}

export interface ResumeUploadResponse {
  id: number;
  candidate_name: string | null;
  original_filename: string;
  file_type: string;
  processing_status: string;
  text_preview: string;
  extracted_chars: number;
  uploaded_at: string;
}

/** Shape returned by the backend for non-2xx responses. */
export interface ApiErrorBody {
  error?: { code?: string; status?: number; message?: string };
  detail?: string;
}

/* ------------------------------ Matching --------------------------------- */

export interface SemanticInfo {
  available: boolean;
  reason: string | null;
}

export interface MatchSignals {
  experience?: {
    resume_years: number | null;
    required_years: number | null;
    status: 'meets' | 'insufficient' | 'unknown' | 'not_required';
    resume_titles: string[];
  };
  qualifications?: {
    resume_education: string | null;
    job_mentions_education: boolean;
    resume_certifications: string[];
    job_certifications: string[];
    matched_certifications: string[];
  };
  related_skill_near_misses?: { missing: string; resume_has: string[] }[];
  role_family?: string | null;
  seniority?: string | null;
  skill_categories_overlap?: string[];
}

export interface MatchResult {
  id: number;
  resume_id: number;
  job_id: number;
  final_score: number;
  required_score: number | null;
  preferred_score: number | null;
  semantic_score: number | null;
  matched_skills: string[];
  missing_required: string[];
  missing_preferred: string[];
  weights: Record<string, number>;
  components: Record<string, number | null>;
  discovered_skills: { skill: string; phrase: string; confidence: number; category: string | null }[];
  signals: MatchSignals;
  confidence: number | null;
  created_at: string;
  updated_at: string;
  job?: Job;
  resume?: ResumeSummary;

  // Phase 6 — feedback-aware ranking (only set when rank=blended)
  blended_score: number | null;
  feedback_adjustment: number | null;
  rank_explanation: RankExplanation | null;
  latest_feedback: FeedbackEvent | null;
}

export interface MatchListResponse {
  items: MatchResult[];
  total: number;
  page: number;
  page_size: number;
  semantic: SemanticInfo;
  rank: RankMode;
  feedback_coverage: number;
}

/* ----------------------- Phase 6 feedback & ranking --------------------- */

export type RankMode = 'engine' | 'blended';
export type FeedbackVerdict = 'helpful' | 'not_relevant';
export type FeedbackOutcome = 'shortlisted' | 'applied' | 'interview' | 'rejected';

export interface FeedbackEvent {
  id: number;
  match_id: number;
  resume_id: number;
  job_id: number;
  verdict: FeedbackVerdict;
  relevance: number | null;
  outcome: FeedbackOutcome | null;
  reason: string | null;
  created_at: string;
}

export interface FeedbackInput {
  verdict: FeedbackVerdict;
  relevance?: number | null;
  outcome?: FeedbackOutcome | null;
  reason?: string | null;
}

export interface RankSignal {
  signal: 'job' | 'skill_category' | 'company';
  label: string;
  weight: number;
  raw: number;
  points: number;
  positive: number;
  negative: number;
  detail: string;
}

export interface RankExplanation {
  mode: RankMode;
  enabled: boolean;
  engine_score: number;
  adjustment: number;
  blended_score: number;
  max_adjustment: number;
  capped: boolean;
  signals: RankSignal[];
  summary: string;
  notes: string[];
}

export interface FeedbackSummary {
  resume_id: number;
  total_events: number;
  helpful_count: number;
  not_relevant_count: number;
  average_relevance: number | null;
  outcome_distribution: Record<string, number>;
  most_helpful_skill_categories: { category: string; count: number }[];
  most_rejected_skill_categories: { category: string; count: number }[];
  feedback_coverage: number;
  matches_total: number;
  matches_with_feedback: number;
  notes: string[];
}

export interface CalculateResponse {
  items: MatchResult[];
  jobs_evaluated: number;
  semantic: SemanticInfo;
  configured_weights: Record<string, number>;
}

/* --------------------------- Phase 4 explanations ------------------------ */

export type GeneratedBy = 'deterministic' | 'ai';

export type EvidenceStatus = 'matched' | 'missing' | 'related' | 'unknown' | 'info';
export type RequirementType = 'required' | 'preferred';
export type GapPriority = 'high' | 'medium' | 'low' | 'none';

export interface ScoreFactor {
  component: string;
  label: string;
  available: boolean;
  score: number | null;
  weight: number | null;
  contribution: number | null;
  reason: string | null;
}

export interface EvidenceItem {
  kind: string;
  status: EvidenceStatus;
  label: string;
  detail: string;
  source: string;
}

export interface RelatedSkill {
  requirement: string;
  resume_has: string[];
}

export interface MatchExplanation {
  match_id: number;
  resume_id: number;
  job_id: number;
  final_score: number;
  interpretation: string;
  generated_by: GeneratedBy;
  score_factors: ScoreFactor[];
  required_matched: string[];
  required_missing: string[];
  preferred_matched: string[];
  preferred_missing: string[];
  related_skills: RelatedSkill[];
  experience: {
    status: 'meets' | 'insufficient' | 'unknown' | 'not_required';
    resume_years: number | null;
    required_years: number | null;
    detail: string;
  };
  education: {
    status: 'present' | 'unknown' | 'not_required';
    resume_education: string | null;
    job_mentions_education: boolean;
    detail: string;
  };
  certifications: {
    job_certifications: string[];
    resume_certifications: string[];
    matched: string[];
    detail: string;
  };
  semantic: { enabled: boolean; score: number | null; weight: number | null; reason: string | null };
  strengths: string[];
  gaps: string[];
  limitations: string[];
  evidence: EvidenceItem[];
  confidence: number | null;
}

export interface SkillGapItem {
  skill: string;
  requirement: RequirementType;
  status: 'matched' | 'missing' | 'related';
  priority: GapPriority;
  explanation: string;
  related_resume_skills: string[];
  suggested_action: string | null;
}

export interface UnknownItem {
  area: 'experience' | 'education' | 'certification';
  status: 'unknown';
  explanation: string;
}

export interface SkillGapResponse {
  match_id: number;
  resume_id: number;
  job_id: number;
  required_total: number;
  required_matched: number;
  required_missing: number;
  preferred_total: number;
  preferred_matched: number;
  preferred_missing: number;
  items: SkillGapItem[];
  unknowns: UnknownItem[];
  related_not_counted: RelatedSkill[];
  priority_counts: Record<string, number>;
  notes: string[];
  generated_by: GeneratedBy;
}

export interface PlanActivity {
  stage: 'start_now' | 'practice_next' | 'consider_later';
  type: 'learning' | 'practice' | 'project' | 'certification' | 'clarification';
  skill: string | null;
  title: string;
  detail: string;
  estimate: string | null;
}

export interface ImprovementPlan {
  match_id: number;
  resume_id: number;
  job_id: number;
  generated_by: GeneratedBy;
  focus_skills: string[];
  existing_skills: string[];
  start_now: PlanActivity[];
  practice_next: PlanActivity[];
  consider_later: PlanActivity[];
  sequencing: string;
  prioritisation: string;
  disclaimers: string[];
  notes: string[];
}

export interface ResumeSuggestion {
  category: 'highlight' | 'strengthen' | 'quantify' | 'clarify' | 'keyword';
  title: string;
  detail: string;
  verify: string;
  skill: string | null;
}

export interface ResumeSuggestions {
  match_id: number;
  resume_id: number;
  job_id: number;
  generated_by: GeneratedBy;
  suggestions: ResumeSuggestion[];
  keywords_present: string[];
  keywords_absent: string[];
  disclaimers: string[];
  notes: string[];
}
