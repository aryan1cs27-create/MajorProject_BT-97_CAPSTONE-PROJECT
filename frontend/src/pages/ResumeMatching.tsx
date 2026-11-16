import { useCallback, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';

import Alert from '../components/Alert';
import EmptyState from '../components/EmptyState';
import PageHeader from '../components/PageHeader';
import { ApiError, api } from '../services/api';
import type {
  FeedbackInput,
  FeedbackOutcome,
  FeedbackVerdict,
  ImprovementPlan,
  Job,
  MatchExplanation,
  MatchResult,
  PlanActivity,
  RankExplanation,
  RankMode,
  ResumeSuggestions,
  ResumeSummary,
  SemanticInfo,
  SkillGapResponse,
} from '../types';

const WEIGHT_LABELS: Record<string, string> = {
  required: 'Required skills',
  preferred: 'Preferred skills',
  semantic: 'Semantic similarity',
};

const EXPERIENCE_LABELS: Record<string, string> = {
  meets: 'Meets the stated requirement',
  insufficient: 'Below the stated requirement',
  unknown: 'Not stated in the resume — not penalised',
  not_required: 'No minimum experience requested',
};

const OUTCOME_LABELS: Record<FeedbackOutcome, string> = {
  shortlisted: 'Shortlisted',
  applied: 'Applied',
  interview: 'Interview',
  rejected: 'Rejected',
};

const PRIORITY_LABELS: Record<string, string> = {
  high: 'High priority',
  medium: 'Medium priority',
  low: 'Low priority',
  none: 'Already matched',
};

const SUGGESTION_LABELS: Record<string, string> = {
  highlight: 'Highlight',
  strengthen: 'Strengthen',
  quantify: 'Quantify',
  clarify: 'Clarify',
  keyword: 'Keyword',
};

function scoreColor(score: number): string {
  if (score >= 60) return 'text-emerald-700';
  if (score >= 35) return 'text-amber-600';
  return 'text-slate-500';
}

function barColor(score: number): string {
  if (score >= 60) return 'bg-emerald-500';
  if (score >= 35) return 'bg-amber-500';
  return 'bg-slate-400';
}

function statusChip(status: string): string {
  switch (status) {
    case 'matched':
      return 'bg-emerald-50 text-emerald-700';
    case 'missing':
      return 'bg-rose-50 text-rose-600';
    case 'related':
      return 'bg-amber-50 text-amber-700';
    case 'unknown':
      return 'bg-slate-100 text-slate-600';
    default:
      return 'bg-brand-50 text-brand-600';
  }
}

function priorityChip(priority: string): string {
  switch (priority) {
    case 'high':
      return 'bg-rose-50 text-rose-600';
    case 'medium':
      return 'bg-amber-50 text-amber-700';
    case 'low':
      return 'bg-sky-50 text-sky-700';
    default:
      return 'bg-slate-100 text-slate-500';
  }
}

/* ------------------------------- Phase 4 plumbing ------------------------ */

type TabKey = 'breakdown' | 'explanation' | 'gaps' | 'plan' | 'suggestions';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'breakdown', label: 'Score breakdown' },
  { key: 'explanation', label: 'Why this match?' },
  { key: 'gaps', label: 'Skill gaps' },
  { key: 'plan', label: 'Improvement plan' },
  { key: 'suggestions', label: 'Resume suggestions' },
];

interface DetailState<T> {
  loading: boolean;
  error: string | null;
  data: T | null;
}

const IDLE = { loading: false, error: null, data: null } as const;

function TabBody<T>({
  state,
  empty,
  onRetry,
  children,
}: {
  state: DetailState<T>;
  empty: string;
  onRetry: () => void;
  children: (data: T) => ReactNode;
}) {
  if (state.loading) {
    return <p className="py-3 text-sm text-navy-500">Loading…</p>;
  }
  if (state.error) {
    return (
      <Alert kind="error">
        {state.error}{' '}
        <button type="button" onClick={onRetry} className="font-medium underline">
          Retry
        </button>
      </Alert>
    );
  }
  if (!state.data) {
    return <p className="py-3 text-sm text-navy-500">{empty}</p>;
  }
  return <>{children(state.data)}</>;
}

function GeneratedBy({ value }: { value: string }) {
  return (
    <span
      className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500"
      title="How this text was produced"
    >
      {value === 'ai' ? 'AI-generated wording' : 'Deterministic templates (no AI)'}
    </span>
  );
}

/** Neutral note box (Alert only supports success/error). */
function NoteBox({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      {children}
    </div>
  );
}

function ActivityCard({ activity }: { activity: PlanActivity }) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-brand-600">
          {activity.type}
        </span>
        <h4 className="text-sm font-semibold text-navy-900">{activity.title}</h4>
      </div>
      <p className="mt-1 text-sm text-navy-700">{activity.detail}</p>
      {activity.estimate && (
        <p className="mt-1 text-xs italic text-slate-400">{activity.estimate}</p>
      )}
    </li>
  );
}

function PlanSection({ title, items }: { title: string; items: PlanActivity[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-400">{title}</p>
      <ul className="mt-2 space-y-2">
        {items.map((activity, index) => (
          <ActivityCard key={`${activity.title}-${index}`} activity={activity} />
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------ Phase 6 ranking -------------------------- */

function RankMoved({ explanation }: { explanation: RankExplanation }) {
  return (
    <div className="rounded-lg border border-brand-200 bg-brand-50/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-medium uppercase tracking-wide text-brand-700">
          Why this rank moved
        </p>
        <span className="rounded-full bg-white px-2 py-0.5 text-[11px] text-slate-500">
          Feedback-adjusted ranking
        </span>
        {explanation.capped && (
          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">
            adjustment capped
          </span>
        )}
      </div>

      <p className="mt-1 text-sm text-navy-900">{explanation.summary}</p>

      <div className="mt-2 grid gap-1 text-xs text-navy-700 sm:grid-cols-3">
        <span>
          Engine score <strong>{explanation.engine_score.toFixed(1)}%</strong>{' '}
          <span className="text-slate-400">(stored, never modified)</span>
        </span>
        <span>
          Adjustment{' '}
          <strong>
            {explanation.adjustment >= 0 ? '+' : ''}
            {explanation.adjustment.toFixed(1)}
          </strong>{' '}
          <span className="text-slate-400">(cap ±{explanation.max_adjustment})</span>
        </span>
        <span>
          Blended score <strong>{explanation.blended_score.toFixed(1)}%</strong>
        </span>
      </div>

      <ul className="mt-2 space-y-1 text-xs text-navy-700">
        {explanation.signals.map((signal) => (
          <li key={signal.signal} className="flex flex-wrap items-baseline gap-x-2">
            <span className="font-medium">{signal.label}</span>
            <span className="text-slate-500">
              weight {Math.round(signal.weight * 100)}% · signal {signal.raw.toFixed(2)} ·{' '}
              {signal.positive} helpful / {signal.negative} not relevant
            </span>
            <span
              className={
                signal.points > 0
                  ? 'text-emerald-700'
                  : signal.points < 0
                    ? 'text-rose-600'
                    : 'text-slate-400'
              }
            >
              {signal.points >= 0 ? '+' : ''}
              {signal.points.toFixed(1)} pts
            </span>
            <span className="text-slate-400">{signal.detail}</span>
          </li>
        ))}
      </ul>

      {explanation.notes.map((note) => (
        <p key={note} className="mt-1 text-[11px] text-slate-400">
          {note}
        </p>
      ))}
    </div>
  );
}

function FeedbackControls({
  match,
  busy,
  error,
  onSubmit,
}: {
  match: MatchResult;
  busy: boolean;
  error: string | null;
  onSubmit: (payload: FeedbackInput) => Promise<void>;
}) {
  const latest = match.latest_feedback;
  const [open, setOpen] = useState(false);
  const [verdict, setVerdict] = useState<FeedbackVerdict>(latest?.verdict ?? 'helpful');
  const [relevance, setRelevance] = useState(latest?.relevance ? String(latest.relevance) : '');
  const [outcome, setOutcome] = useState<string>(latest?.outcome ?? '');
  const [reason, setReason] = useState(latest?.reason ?? '');

  const pill = (active: boolean) =>
    `rounded-md border px-2 py-0.5 text-[11px] font-medium transition ${
      active
        ? 'border-brand-500 bg-brand-600 text-white'
        : 'border-slate-300 bg-white text-navy-700 hover:bg-slate-50'
    }`;

  return (
    <div className="mt-3 border-t border-slate-100 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
          Your feedback
        </span>
        <button
          type="button"
          disabled={busy}
          onClick={() => void onSubmit({ verdict: 'helpful' })}
          className={pill(latest?.verdict === 'helpful')}
        >
          Helpful
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => void onSubmit({ verdict: 'not_relevant' })}
          className={pill(latest?.verdict === 'not_relevant')}
        >
          Not relevant
        </button>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="text-[11px] font-medium text-brand-600 hover:underline"
        >
          {open ? 'Hide details' : 'Add details'}
        </button>

        {latest && (
          <span className="text-[11px] text-slate-500">
            Saved: {latest.verdict === 'helpful' ? 'Helpful' : 'Not relevant'}
            {latest.relevance ? ` · ${latest.relevance}/5` : ''}
            {latest.outcome ? ` · ${OUTCOME_LABELS[latest.outcome]}` : ''}
          </span>
        )}
        {busy && <span className="text-[11px] text-slate-400">Saving…</span>}
      </div>

      {error && <p className="mt-1 text-xs text-rose-600">{error}</p>}

      {open && (
        <div className="mt-2 grid gap-2 rounded-lg bg-slate-50 p-2 sm:grid-cols-2 lg:grid-cols-4">
          <label className="block text-[11px] text-navy-600">
            Verdict
            <select
              value={verdict}
              onChange={(event) => setVerdict(event.target.value as FeedbackVerdict)}
              className="mt-0.5 w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs"
            >
              <option value="helpful">Helpful</option>
              <option value="not_relevant">Not relevant</option>
            </select>
          </label>
          <label className="block text-[11px] text-navy-600">
            Relevance (1–5)
            <select
              value={relevance}
              onChange={(event) => setRelevance(event.target.value)}
              className="mt-0.5 w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs"
            >
              <option value="">Not rated</option>
              {[1, 2, 3, 4, 5].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-[11px] text-navy-600">
            Outcome
            <select
              value={outcome}
              onChange={(event) => setOutcome(event.target.value)}
              className="mt-0.5 w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs"
            >
              <option value="">Not tracked</option>
              {(Object.keys(OUTCOME_LABELS) as FeedbackOutcome[]).map((value) => (
                <option key={value} value={value}>
                  {OUTCOME_LABELS[value]}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-[11px] text-navy-600">
            Reason (optional)
            <input
              value={reason}
              maxLength={500}
              onChange={(event) => setReason(event.target.value)}
              placeholder="e.g. strong skill alignment"
              className="mt-0.5 w-full rounded border border-slate-300 bg-white px-2 py-1 text-xs"
            />
          </label>
          <div className="sm:col-span-2 lg:col-span-4">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void onSubmit({
                  verdict,
                  relevance: relevance ? Number(relevance) : null,
                  outcome: (outcome || null) as FeedbackOutcome | null,
                  reason: reason.trim() ? reason.trim() : null,
                }).then(() => setOpen(false))
              }
              className="rounded-md bg-brand-600 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600/90 disabled:opacity-60"
            >
              Save feedback
            </button>
            <span className="ml-2 text-[11px] text-slate-400">
              Feedback never changes the stored match score.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------ page ------------------------------- */

export default function ResumeMatching() {
  const [resumes, setResumes] = useState<ResumeSummary[]>([]);
  const [selectedResumeId, setSelectedResumeId] = useState<number | null>(null);
  const [results, setResults] = useState<MatchResult[]>([]);
  const [semantic, setSemantic] = useState<SemanticInfo | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>('breakdown');

  // Phase 6: ranking mode, outcome filter and per-match feedback state.
  const [rankMode, setRankMode] = useState<RankMode>('engine');
  const [statusFilter, setStatusFilter] = useState<'all' | FeedbackOutcome>('all');
  const [feedbackBusy, setFeedbackBusy] = useState<Record<number, boolean>>({});
  const [feedbackErrors, setFeedbackErrors] = useState<Record<number, string | null>>({});

  // Optional focus on one job, deep-linked as /resume-matching?job=<id>
  // (used by the Live Jobs page: “Match this job”).
  const [searchParams, setSearchParams] = useSearchParams();
  const focusJobId = Number(searchParams.get('job')) || null;
  const [focusJob, setFocusJob] = useState<Job | null>(null);

  const [loadingResumes, setLoadingResumes] = useState(true);
  const [loadingResults, setLoadingResults] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Phase 4 detail caches, keyed by match id
  const [explanations, setExplanations] = useState<Record<number, DetailState<MatchExplanation>>>({});
  const [gaps, setGaps] = useState<Record<number, DetailState<SkillGapResponse>>>({});
  const [plans, setPlans] = useState<Record<number, DetailState<ImprovementPlan>>>({});
  const [suggestions, setSuggestions] = useState<Record<number, DetailState<ResumeSuggestions>>>({});
  const loadedRef = useRef<Set<string>>(new Set());

  const loadResumes = useCallback(async () => {
    setLoadingResumes(true);
    try {
      const page = await api.listResumes(1, 100);
      setResumes(page.items);
      if (page.items.length > 0) {
        setSelectedResumeId((current) => current ?? page.items[0].id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load resumes.');
    } finally {
      setLoadingResumes(false);
    }
  }, []);

  const loadStoredResults = useCallback(async (resumeId: number) => {
    setLoadingResults(true);
    setError(null);
    try {
      const response = await api.getMatchesForResume(resumeId, 1, 50, rankMode);
      const items =
        focusJobId === null
          ? response.items
          : response.items.filter((item) => item.job_id === focusJobId);
      setResults(items);
      setSemantic(response.semantic);
      setExpandedId(items.length > 0 ? items[0].id : null);
      setActiveTab('breakdown');
      loadedRef.current.clear();
    } catch (err) {
      setResults([]);
      if (err instanceof ApiError && err.status === 404) {
        setError(null); // resume simply has no results yet
      } else {
        setError(err instanceof ApiError ? err.message : 'Could not load match results.');
      }
    } finally {
      setLoadingResults(false);
    }
  }, [focusJobId, rankMode]);

  useEffect(() => {
    void loadResumes();
  }, [loadResumes]);

  // Resolve the title of a deep-linked job for the focus banner.
  useEffect(() => {
    if (focusJobId === null) {
      setFocusJob(null);
      return;
    }
    let cancelled = false;
    api
      .getJob(focusJobId)
      .then((job) => {
        if (!cancelled) setFocusJob(job);
      })
      .catch(() => {
        if (!cancelled) setFocusJob(null);
      });
    return () => {
      cancelled = true;
    };
  }, [focusJobId]);

  useEffect(() => {
    if (selectedResumeId !== null) {
      void loadStoredResults(selectedResumeId);
    }
  }, [selectedResumeId, loadStoredResults]);

  const onCalculate = async () => {
    if (selectedResumeId === null) return;
    setCalculating(true);
    setError(null);
    try {
      const response = await api.calculateMatches(
        selectedResumeId,
        focusJobId === null ? undefined : [focusJobId],
      );
      setResults(response.items);
      setSemantic(response.semantic);
      setExpandedId(response.items.length > 0 ? response.items[0].id : null);
      setActiveTab('breakdown');
      loadedRef.current.clear();
      // The calculate response is engine-ordered; refetch to apply the
      // feedback-adjusted ordering when that mode is selected.
      if (rankMode === 'blended') {
        await loadStoredResults(selectedResumeId);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not calculate matches.');
    } finally {
      setCalculating(false);
    }
  };

  /** Fetch a Phase 4 payload once per match+tab (retry clears the flag). */
  const ensureLoaded = useCallback((matchId: number, tab: TabKey) => {
    if (tab === 'breakdown') return;
    const key = `${matchId}:${tab}`;
    if (loadedRef.current.has(key)) return;
    loadedRef.current.add(key);

    const message = (err: unknown, fallback: string) =>
      err instanceof ApiError ? err.message : fallback;

    if (tab === 'explanation') {
      setExplanations((prev) => ({
        ...prev,
        [matchId]: { loading: true, error: null, data: prev[matchId]?.data ?? null },
      }));
      api
        .getMatchExplanation(matchId)
        .then((data) => setExplanations((prev) => ({ ...prev, [matchId]: { loading: false, error: null, data } })))
        .catch((err) => {
          loadedRef.current.delete(key);
          setExplanations((prev) => ({
            ...prev,
            [matchId]: { loading: false, error: message(err, 'Could not load the explanation.'), data: null },
          }));
        });
    } else if (tab === 'gaps') {
      setGaps((prev) => ({
        ...prev,
        [matchId]: { loading: true, error: null, data: prev[matchId]?.data ?? null },
      }));
      api
        .getMatchSkillGaps(matchId)
        .then((data) => setGaps((prev) => ({ ...prev, [matchId]: { loading: false, error: null, data } })))
        .catch((err) => {
          loadedRef.current.delete(key);
          setGaps((prev) => ({
            ...prev,
            [matchId]: { loading: false, error: message(err, 'Could not load the skill-gap analysis.'), data: null },
          }));
        });
    } else if (tab === 'plan') {
      setPlans((prev) => ({
        ...prev,
        [matchId]: { loading: true, error: null, data: prev[matchId]?.data ?? null },
      }));
      api
        .getImprovementPlan(matchId)
        .then((data) => setPlans((prev) => ({ ...prev, [matchId]: { loading: false, error: null, data } })))
        .catch((err) => {
          loadedRef.current.delete(key);
          setPlans((prev) => ({
            ...prev,
            [matchId]: { loading: false, error: message(err, 'Could not load the improvement plan.'), data: null },
          }));
        });
    } else {
      setSuggestions((prev) => ({
        ...prev,
        [matchId]: { loading: true, error: null, data: prev[matchId]?.data ?? null },
      }));
      api
        .getResumeSuggestions(matchId)
        .then((data) => setSuggestions((prev) => ({ ...prev, [matchId]: { loading: false, error: null, data } })))
        .catch((err) => {
          loadedRef.current.delete(key);
          setSuggestions((prev) => ({
            ...prev,
            [matchId]: { loading: false, error: message(err, 'Could not load resume suggestions.'), data: null },
          }));
        });
    }
  }, []);

  const selectTab = (matchId: number, tab: TabKey) => {
    setActiveTab(tab);
    ensureLoaded(matchId, tab);
  };

  const retry = (matchId: number, tab: TabKey) => {
    loadedRef.current.delete(`${matchId}:${tab}`);
    ensureLoaded(matchId, tab);
  };

  /** Save feedback, then refresh ordering when in blended mode. */
  const submitFeedback = async (match: MatchResult, payload: FeedbackInput) => {
    setFeedbackBusy((prev) => ({ ...prev, [match.id]: true }));
    setFeedbackErrors((prev) => ({ ...prev, [match.id]: null }));
    try {
      const event = await api.submitFeedback(match.id, payload);
      setResults((prev) =>
        prev.map((item) => (item.id === match.id ? { ...item, latest_feedback: event } : item)),
      );
      if (rankMode === 'blended' && selectedResumeId !== null) {
        await loadStoredResults(selectedResumeId);
      }
    } catch (err) {
      setFeedbackErrors((prev) => ({
        ...prev,
        [match.id]: err instanceof ApiError ? err.message : 'Could not save feedback.',
      }));
    } finally {
      setFeedbackBusy((prev) => ({ ...prev, [match.id]: false }));
    }
  };

  const discoveredSkills = results[0]?.discovered_skills ?? [];
  const selectedResume = resumes.find((resume) => resume.id === selectedResumeId) ?? null;
  const visibleResults =
    statusFilter === 'all'
      ? results
      : results.filter((match) => match.latest_feedback?.outcome === statusFilter);

  return (
    <div>
      <PageHeader
        title="Resume Matching"
        subtitle="Rank jobs against a resume using extracted skills, weighted coverage and semantic similarity."
      />

      <div className="space-y-4">
        {error && <Alert kind="error">{error}</Alert>}

        {semantic && !semantic.available && (
          <Alert kind="error">
            <strong>Semantic matching unavailable.</strong>{' '}
            {semantic.reason ?? 'The model could not be loaded.'} Scores are computed from skill
            coverage only (weights redistributed automatically).
          </Alert>
        )}

        {/* Controls */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="grid gap-4 sm:grid-cols-[minmax(0, 320px)_auto] sm:items-end">
            <label className="block text-sm">
              <span className="text-navy-700">Resume</span>
              <select
                value={selectedResumeId ?? ''}
                onChange={(event) => setSelectedResumeId(Number(event.target.value))}
                disabled={loadingResumes || resumes.length === 0}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-navy-900 focus:border-brand-500 focus:outline-none disabled:opacity-60"
              >
                {resumes.length === 0 && <option value="">No resumes uploaded yet</option>}
                {resumes.map((resume) => (
                  <option key={resume.id} value={resume.id}>
                    {resume.candidate_name ?? 'Unnamed candidate'} — {resume.original_filename}
                  </option>
                ))}
              </select>
            </label>

            <button
              type="button"
              onClick={() => void onCalculate()}
              disabled={calculating || selectedResumeId === null}
              className="h-[42px] rounded-lg bg-brand-600 px-5 text-sm font-medium text-white hover:bg-brand-600/90 disabled:opacity-60"
            >
              {calculating ? 'Calculating…' : 'Calculate matches'}
            </button>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-xs text-navy-500">Ranking</span>
            <div
              role="group"
              aria-label="Ranking mode"
              className="inline-flex overflow-hidden rounded-lg border border-slate-300"
            >
              {(['engine', 'blended'] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={rankMode === mode}
                  onClick={() => setRankMode(mode)}
                  className={`px-3 py-1 text-xs font-medium transition ${
                    rankMode === mode
                      ? 'bg-brand-600 text-white'
                      : 'bg-white text-navy-700 hover:bg-slate-50'
                  }`}
                >
                  {mode === 'engine' ? 'Original ranking' : 'Feedback-adjusted ranking'}
                </button>
              ))}
            </div>
            <span className="text-[11px] text-slate-400">
              {rankMode === 'blended'
                ? 'Feedback can reorder close matches; the stored engine score stays unchanged.'
                : 'Stored Phase 3 order — feedback never changes it.'}
            </span>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-navy-500">
            <span>
              {resumes.length} resume{resumes.length === 1 ? '' : 's'} available
            </span>
            {semantic && (
              <span className={semantic.available ? 'text-emerald-700' : 'text-amber-600'}>
                Semantic matching: {semantic.available ? 'enabled' : 'disabled'}
              </span>
            )}
            {selectedResume && <span>Analysing: {selectedResume.original_filename}</span>}
          </div>

          {focusJobId !== null && (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-brand-50 px-3 py-2 text-xs text-brand-700">
              <span className="font-medium">
                Focused on one job:{' '}
                {focusJob ? `${focusJob.title} · ${focusJob.company}` : `job #${focusJobId}`}
              </span>
              <span className="text-brand-600/80">
                “Calculate matches” scores this job only; details below cover explanations and
                skill gaps.
              </span>
              <button
                type="button"
                onClick={() => setSearchParams({})}
                className="ml-auto rounded border border-brand-300 px-2 py-0.5 text-brand-700 hover:bg-white"
              >
                Clear focus
              </button>
            </div>
          )}

          {calculating && (
            <div className="mt-3 h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div className="h-full w-1/3 animate-pulse rounded-full bg-brand-500" />
            </div>
          )}
        </section>

        {/* Results */}
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-medium text-navy-500">Ranked recommendations</h2>
            <div className="flex flex-wrap items-center gap-3">
              {!loadingResults && results.length > 0 && (
                <label className="flex items-center gap-1.5 text-xs text-navy-500">
                  Status
                  <select
                    value={statusFilter}
                    onChange={(event) =>
                      setStatusFilter(event.target.value as 'all' | FeedbackOutcome)
                    }
                    className="rounded border border-slate-300 bg-white px-2 py-1 text-xs text-navy-800"
                  >
                    <option value="all">All</option>
                    {(Object.keys(OUTCOME_LABELS) as FeedbackOutcome[]).map((outcome) => (
                      <option key={outcome} value={outcome}>
                        {OUTCOME_LABELS[outcome]}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {!loadingResults && results.length > 0 && (
                <span className="text-xs text-slate-400">
                  {visibleResults.length === results.length
                    ? `${results.length} job(s)`
                    : `${visibleResults.length} of ${results.length} job(s)`}
                </span>
              )}
            </div>
          </div>

          {loadingResults ? (
            <p className="mt-4 text-sm text-navy-500">Loading stored results…</p>
          ) : resumes.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No resumes to match"
                description="Upload a PDF or DOCX on the Resumes page first — matching runs on the extracted text stored in MySQL."
              />
            </div>
          ) : results.length === 0 ? (
            <div className="mt-4">
              <EmptyState
                title="No matches calculated yet"
                description="Choose a resume and press “Calculate matches” to score every job posting against it."
              />
            </div>
          ) : (
            <ul className="mt-4 space-y-3">
              {visibleResults.map((match, index) => {
                const job = match.job;
                const expanded = expandedId === match.id;
                return (
                  <li key={match.id} className="rounded-lg border border-slate-200 p-4">
                    <div className="flex flex-wrap items-start gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="text-xs font-semibold text-slate-400">#{index + 1}</span>
                          <h3 className="text-sm font-semibold text-navy-900">
                            {job?.title ?? `Job #${match.job_id}`}
                          </h3>
                          {job?.employment_type && (
                            <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[11px] font-medium text-brand-600">
                              {job.employment_type}
                            </span>
                          )}
                          {match.signals.seniority && (
                            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-navy-500">
                              {match.signals.seniority}
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-navy-500">
                          {job ? `${job.company}${job.location ? ` · ${job.location}` : ''}` : ''}
                          {match.signals.role_family ? ` · ${match.signals.role_family}` : ''}
                        </p>

                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {match.matched_skills.slice(0, 8).map((skill) => (
                            <span
                              key={skill}
                              className="rounded-md bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700"
                            >
                              {skill}
                            </span>
                          ))}
                          {match.missing_required.slice(0, 6).map((skill) => (
                            <span
                              key={`missing-${skill}`}
                              title="Required skill not found in the resume"
                              className="rounded-md bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600"
                            >
                              {skill}
                            </span>
                          ))}
                        </div>
                      </div>

                      <div className="text-right">
                        <p className={`text-2xl font-semibold ${scoreColor(match.final_score)}`}>
                          {match.final_score.toFixed(1)}
                          <span className="text-sm font-normal">%</span>
                        </p>
                        <div className="mt-1 h-1.5 w-28 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className={`h-full rounded-full ${barColor(match.final_score)}`}
                            style={{ width: `${Math.max(match.final_score, 2)}%` }}
                          />
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            setExpandedId(expanded ? null : match.id);
                            if (!expanded) setActiveTab('breakdown');
                          }}
                          className="mt-2 text-xs font-medium text-brand-600 hover:underline"
                        >
                          {expanded ? 'Hide details' : 'Show details'}
                        </button>
                      </div>
                    </div>

                    <FeedbackControls
                      match={match}
                      busy={Boolean(feedbackBusy[match.id])}
                      error={feedbackErrors[match.id] ?? null}
                      onSubmit={(payload) => submitFeedback(match, payload)}
                    />

                    {expanded && (
                      <div className="mt-4 space-y-4 rounded-lg bg-slate-50 p-4">
                        {/* Tabs */}
                        <div
                          role="tablist"
                          aria-label="Match details"
                          className="flex flex-wrap gap-1 border-b border-slate-200 pb-2"
                        >
                          {TABS.map((tab) => (
                            <button
                              key={tab.key}
                              type="button"
                              role="tab"
                              aria-selected={activeTab === tab.key}
                              onClick={() => selectTab(match.id, tab.key)}
                              className={`rounded-lg px-3 py-1.5 text-xs font-medium focus:outline-none focus:ring-2 focus:brand-500 ${
                                activeTab === tab.key
                                  ? 'bg-white text-navy-900 shadow-sm ring-1 ring-slate-200'
                                  : 'text-navy-500 hover:bg-white/60'
                              }`}
                            >
                              {tab.label}
                            </button>
                          ))}
                        </div>

                        {/* ---------------------------------------- breakdown */}
                        {activeTab === 'breakdown' && (
                          <div className="space-y-4">
                            <div>
                              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                                Score breakdown
                              </p>
                              <div className="mt-2 space-y-2">
                                {(['required', 'preferred', 'semantic'] as const).map((key) => {
                                  const value = match.components?.[key] ?? null;
                                  const weight = match.weights?.[key];
                                  return (
                                    <div key={key} className="flex items-center gap-3 text-sm">
                                      <span className="w-40 shrink-0 text-navy-700">
                                        {WEIGHT_LABELS[key]}
                                      </span>
                                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
                                        <div
                                          className={`h-full rounded-full ${barColor(value ?? 0)}`}
                                          style={{
                                            width: `${value === null ? 0 : Math.max(value, 1)}%`,
                                          }}
                                        />
                                      </div>
                                      <span className="w-32 shrink-0 text-right text-xs text-navy-500">
                                        {value === null ? 'unavailable' : `${value.toFixed(1)}%`}
                                        {weight !== undefined ? ` · weight ${(weight * 100).toFixed(0)}%` : ''}
                                      </span>
                                    </div>
                                  );
                                })}
                              </div>
                              <p className="mt-2 text-xs text-slate-400">
                                Weights: {Object.entries(match.weights)
                                  .map(([key, value]) => `${WEIGHT_LABELS[key] ?? key} ${(value * 100).toFixed(0)}%`)
                                  .join(' · ') || 'no component available'}
                                {match.confidence !== null && ` · confidence ${Math.round(match.confidence * 100)}%`}
                              </p>
                            </div>

                            <div className="grid gap-3 sm:grid-cols-2">
                              <div>
                                <p className="text-xs font-medium uppercase tracking-wide text-emerald-700">
                                  Matched skills ({match.matched_skills.length})
                                </p>
                                <p className="mt-1 text-sm text-navy-700">
                                  {match.matched_skills.length ? match.matched_skills.join(', ') : 'None matched'}
                                </p>

                                <p className="mt-3 text-xs font-medium uppercase tracking-wide text-rose-600">
                                  Missing required ({match.missing_required.length})
                                </p>
                                <p className="mt-1 text-sm text-navy-700">
                                  {match.missing_required.length
                                    ? match.missing_required.join(', ')
                                    : 'All required skills found'}
                                </p>
                              </div>
                              <div>
                                <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                                  Missing preferred ({match.missing_preferred.length})
                                </p>
                                <p className="mt-1 text-sm text-navy-700">
                                  {match.missing_preferred.length
                                    ? match.missing_preferred.join(', ')
                                    : 'All preferred skills found'}
                                </p>

                                {match.signals.experience && (
                                  <>
                                    <p className="mt-3 text-xs font-medium uppercase tracking-wide text-slate-500">
                                      Experience signal
                                    </p>
                                    <p className="mt-1 text-sm text-navy-700">
                                      {EXPERIENCE_LABELS[match.signals.experience.status] ??
                                        match.signals.experience.status}
                                      {match.signals.experience.resume_years !== null &&
                                        ` · resume states ${match.signals.experience.resume_years} years`}
                                      {match.signals.experience.required_years !== null &&
                                        ` · job asks ${match.signals.experience.required_years}+`}
                                    </p>
                                  </>
                                )}
                              </div>
                            </div>

                            {match.signals.related_skill_near_misses &&
                              match.signals.related_skill_near_misses.length > 0 && (
                                <p className="text-xs text-navy-500">
                                  Related but not counted as matches:{' '}
                                  {match.signals.related_skill_near_misses
                                    .map(
                                      (item) =>
                                        `${item.missing} (resume has ${item.resume_has.join(', ')})`,
                                    )
                                    .join('; ')}
                                </p>
                              )}

                            {index === 0 && discoveredSkills.length > 0 && (
                              <p className="text-xs text-navy-500">
                                Newly discovered skills in this resume (not in the taxonomy yet):{' '}
                                {discoveredSkills
                                  .slice(0, 10)
                                  .map((skill) => `${skill.skill} (${Math.round(skill.confidence * 100)}%)`)
                                  .join(', ')}
                              </p>
                            )}
                          </div>
                        )}

                        {/* ------------------------------------- explanation */}
                        {activeTab === 'explanation' && (
                          <TabBody
                            state={explanations[match.id] ?? IDLE}
                            empty="No explanation has been generated for this match yet."
                            onRetry={() => retry(match.id, 'explanation')}
                          >
                            {(data) => (
                              <div className="space-y-4">
                                {match.rank_explanation ? (
                                  <RankMoved explanation={match.rank_explanation} />
                                ) : (
                                  <p className="text-xs text-slate-400">
                                    Switch Ranking to “Feedback-adjusted ranking” to see how your
                                    feedback moved this match.
                                  </p>
                                )}

                                <div className="flex flex-wrap items-center gap-2">
                                  <GeneratedBy value={data.generated_by} />
                                  <span className="text-xs text-slate-400">
                                    Score {data.final_score.toFixed(1)}% · confidence{' '}
                                    {data.confidence !== null
                                      ? `${Math.round(data.confidence * 100)}%`
                                      : 'unknown'}
                                  </span>
                                </div>

                                <p className="text-sm font-medium text-navy-900">
                                  {data.interpretation}
                                </p>

                                {/* Score factors */}
                                <div>
                                  <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                                    What drove the score
                                  </p>
                                  <ul className="mt-2 space-y-1.5">
                                    {data.score_factors.map((factor) => (
                                      <li
                                        key={factor.component}
                                        className="flex flex-wrap items-baseline gap-x-3 text-sm"
                                      >
                                        <span className="w-40 shrink-0 text-navy-700">
                                          {factor.label}
                                        </span>
                                        {factor.available ? (
                                          <span className="text-navy-900">
                                            {factor.score?.toFixed(1)}%
                                            <span className="text-navy-500">
                                              {' '}
                                              × weight {Math.round((factor.weight ?? 0) * 100)}% ={' '}
                                              {factor.contribution?.toFixed(1)} points
                                            </span>
                                          </span>
                                        ) : (
                                          <span className="text-slate-500">
                                            unavailable — {factor.reason}
                                          </span>
                                        )}
                                      </li>
                                    ))}
                                  </ul>
                                </div>

                                <div className="grid gap-4 sm:grid-cols-3">
                                  <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-emerald-700">
                                      Strongest reasons
                                    </p>
                                    <ul className="mt-1 list-disc space-y-1 pl-4 text-sm text-navy-700">
                                      {data.strengths.map((item) => (
                                        <li key={item}>{item}</li>
                                      ))}
                                    </ul>
                                  </div>
                                  <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-rose-600">
                                      Main gaps
                                    </p>
                                    <ul className="mt-1 list-disc space-y-1 pl-4 text-sm text-navy-700">
                                      {data.gaps.map((item) => (
                                        <li key={item}>{item}</li>
                                      ))}
                                    </ul>
                                  </div>
                                  <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                                      Limitations
                                    </p>
                                    <ul className="mt-1 list-disc space-y-1 pl-4 text-sm text-navy-700">
                                      {data.limitations.map((item) => (
                                        <li key={item}>{item}</li>
                                      ))}
                                    </ul>
                                  </div>
                                </div>

                                {/* Evidence */}
                                <div>
                                  <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                                    Evidence ({data.evidence.length})
                                  </p>
                                  <ul className="mt-2 space-y-2">
                                    {data.evidence.map((item, idx) => (
                                      <li
                                        key={`${item.kind}-${item.label}-${idx}`}
                                        className="rounded-lg border border-slate-200 bg-white p-3"
                                      >
                                        <div className="flex flex-wrap items-center gap-2">
                                          <span
                                            className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ${statusChip(item.status)}`}
                                          >
                                            {item.status}
                                          </span>
                                          <span className="text-sm font-semibold text-navy-900">
                                            {item.label}
                                          </span>
                                          <span className="text-[11px] text-slate-400">
                                            {item.kind.replace('_', ' ')}
                                          </span>
                                        </div>
                                        <p className="mt-1 text-sm text-navy-700">{item.detail}</p>
                                        <p className="mt-1 text-[11px] text-slate-400">
                                          Source: {item.source}
                                        </p>
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              </div>
                            )}
                          </TabBody>
                        )}

                        {/* ------------------------------------------ gaps */}
                        {activeTab === 'gaps' && (
                          <TabBody
                            state={gaps[match.id] ?? IDLE}
                            empty="No skill-gap analysis available for this match yet."
                            onRetry={() => retry(match.id, 'gaps')}
                          >
                            {(data) => {
                              const relatedRequired = data.items.filter(
                                (item) => item.status === 'related' && item.requirement === 'required',
                              ).length;
                              const relatedPreferred = data.items.filter(
                                (item) => item.status === 'related' && item.requirement === 'preferred',
                              ).length;
                              return (
                              <div className="space-y-4">
                                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-navy-500">
                                  <GeneratedBy value={data.generated_by} />
                                  <span>
                                    Required: {data.required_matched}/{data.required_total} matched
                                    {data.required_missing > 0 &&
                                      ` · ${data.required_missing} missing`}
                                    {relatedRequired > 0 && ` · ${relatedRequired} related`}
                                  </span>
                                  <span>
                                    Preferred: {data.preferred_matched}/{data.preferred_total} matched
                                    {data.preferred_missing > 0 &&
                                      ` · ${data.preferred_missing} missing`}
                                    {relatedPreferred > 0 && ` · ${relatedPreferred} related`}
                                  </span>
                                  <span>
                                    High {data.priority_counts.high ?? 0} · Medium{' '}
                                    {data.priority_counts.medium ?? 0} · Low{' '}
                                    {data.priority_counts.low ?? 0}
                                  </span>
                                </div>

                                {(['required', 'preferred'] as const).map((requirement) => {
                                  const items = data.items.filter(
                                    (item) => item.requirement === requirement,
                                  );
                                  if (items.length === 0) return null;
                                  return (
                                    <div key={requirement}>
                                      <p
                                        className={`text-xs font-medium uppercase tracking-wide ${
                                          requirement === 'required'
                                            ? 'text-rose-600'
                                            : 'text-slate-500'
                                        }`}
                                      >
                                        {requirement} skills
                                        {requirement === 'preferred' ? ' (optional)' : ''}
                                      </p>
                                      <ul className="mt-2 space-y-2">
                                        {items.map((item) => (
                                          <li
                                            key={`${item.requirement}-${item.skill}`}
                                            className="rounded-lg border border-slate-200 bg-white p-3"
                                          >
                                            <div className="flex flex-wrap items-center gap-2">
                                              <span className="text-sm font-semibold text-navy-900">
                                                {item.skill}
                                              </span>
                                              <span
                                                className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ${statusChip(item.status)}`}
                                              >
                                                {item.status}
                                              </span>
                                              <span
                                                className={`rounded-md px-1.5 py-0.5 text-[11px] font-medium ${priorityChip(item.priority)}`}
                                              >
                                                {PRIORITY_LABELS[item.priority] ?? item.priority}
                                              </span>
                                            </div>
                                            <p className="mt-1 text-sm text-navy-700">
                                              {item.explanation}
                                            </p>
                                            {item.related_resume_skills.length > 0 && (
                                              <p className="mt-1 text-xs text-amber-700">
                                                Related skills in your resume (not counted as matches):{' '}
                                                {item.related_resume_skills.join(', ')}
                                              </p>
                                            )}
                                            {item.suggested_action && (
                                              <p className="mt-1 text-xs text-brand-600">
                                                Next step: {item.suggested_action}
                                              </p>
                                            )}
                                          </li>
                                        ))}
                                      </ul>
                                    </div>
                                  );
                                })}

                                {data.unknowns.length > 0 && (
                                  <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                                      Unknown — not assessed as a failure
                                    </p>
                                    <ul className="mt-2 space-y-2">
                                      {data.unknowns.map((item) => (
                                        <li
                                          key={item.area}
                                          className="rounded-lg border border-dashed border-slate-300 bg-white p-3"
                                        >
                                          <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-slate-600">
                                            {item.area}
                                          </span>
                                          <p className="mt-1 text-sm text-navy-700">
                                            {item.explanation}
                                          </p>
                                        </li>
                                      ))}
                                    </ul>
                                  </div>
                                )}

                                <p className="text-xs text-slate-400">{data.notes.join(' ')}</p>
                              </div>
                              );
                            }}
                          </TabBody>
                        )}

                        {/* ------------------------------------------- plan */}
                        {activeTab === 'plan' && (
                          <TabBody
                            state={plans[match.id] ?? IDLE}
                            empty="No improvement plan has been generated for this match yet."
                            onRetry={() => retry(match.id, 'plan')}
                          >
                            {(data) => (
                              <div className="space-y-4">
                                <div className="flex flex-wrap items-center gap-2">
                                  <GeneratedBy value={data.generated_by} />
                                  {data.focus_skills.map((skill) => (
                                    <span
                                      key={skill}
                                      className="rounded-md bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600"
                                    >
                                      Focus: {skill}
                                    </span>
                                  ))}
                                  {data.focus_skills.length === 0 && (
                                    <span className="rounded-md bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                                      No required-skill gaps to close for this job
                                    </span>
                                  )}
                                </div>

                                <PlanSection title="Start now" items={data.start_now} />
                                <PlanSection title="Practice next" items={data.practice_next} />
                                <PlanSection title="Consider later" items={data.consider_later} />

                                {data.existing_skills.length > 0 && (
                                  <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-emerald-700">
                                      Skills you already have (not recommendations)
                                    </p>
                                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                                      {data.existing_skills.map((skill) => (
                                        <span
                                          key={skill}
                                          className="rounded-md bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700"
                                        >
                                          {skill}
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                <div className="text-xs text-navy-500">
                                  <p>{data.sequencing}</p>
                                  <p className="mt-1">{data.prioritisation}</p>
                                </div>

                                <NoteBox>
                                  <ul className="list-disc space-y-1 pl-4">
                                    {data.disclaimers.map((item) => (
                                      <li key={item}>{item}</li>
                                    ))}
                                  </ul>
                                </NoteBox>
                              </div>
                            )}
                          </TabBody>
                        )}

                        {/* ------------------------------------ suggestions */}
                        {activeTab === 'suggestions' && (
                          <TabBody
                            state={suggestions[match.id] ?? IDLE}
                            empty="No resume suggestions have been generated for this match yet."
                            onRetry={() => retry(match.id, 'suggestions')}
                          >
                            {(data) => (
                              <div className="space-y-4">
                                <div className="flex flex-wrap items-center gap-2">
                                  <GeneratedBy value={data.generated_by} />
                                  <span className="text-xs text-slate-400">
                                    Nothing is written to your resume automatically — every
                                    suggestion must be verified by you.
                                  </span>
                                </div>

                                <ul className="space-y-2">
                                  {data.suggestions.map((item, idx) => (
                                    <li
                                      key={`${item.category}-${idx}`}
                                      className="rounded-lg border border-slate-200 bg-white p-3"
                                    >
                                      <div className="flex flex-wrap items-center gap-2">
                                        <span className="rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-brand-600">
                                          {SUGGESTION_LABELS[item.category] ?? item.category}
                                        </span>
                                        <span className="text-sm font-semibold text-navy-900">
                                          {item.title}
                                        </span>
                                      </div>
                                      <p className="mt-1 text-sm text-navy-700">{item.detail}</p>
                                      <p className="mt-1 text-xs text-amber-700">
                                        Verify first: {item.verify}
                                      </p>
                                    </li>
                                  ))}
                                </ul>

                                <div className="grid gap-4 sm:grid-cols-2">
                                  <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-emerald-700">
                                      Job keywords already in your resume ({data.keywords_present.length})
                                    </p>
                                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                                      {data.keywords_present.length ? (
                                        data.keywords_present.map((keyword) => (
                                          <span
                                            key={keyword}
                                            className="rounded-md bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700"
                                          >
                                            {keyword}
                                          </span>
                                        ))
                                      ) : (
                                        <span className="text-sm text-navy-500">None detected</span>
                                      )}
                                    </div>
                                  </div>
                                  <div>
                                    <p className="text-xs font-medium uppercase tracking-wide text-rose-600">
                                      Relevant keywords missing ({data.keywords_absent.length})
                                    </p>
                                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                                      {data.keywords_absent.length ? (
                                        data.keywords_absent.map((keyword) => (
                                          <span
                                            key={keyword}
                                            title="Include only if this is true for you"
                                            className="rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-600"
                                          >
                                            {keyword}
                                          </span>
                                        ))
                                      ) : (
                                        <span className="text-sm text-navy-500">
                                          None — the job’s keywords are already covered
                                        </span>
                                      )}
                                    </div>
                                    {data.keywords_absent.length > 0 && (
                                      <p className="mt-1 text-xs text-slate-400">
                                        Suggestions only: add a keyword solely if it is truthful.
                                      </p>
                                    )}
                                  </div>
                                </div>

                                <NoteBox>
                                  <ul className="list-disc space-y-1 pl-4">
                                    {data.disclaimers.map((item) => (
                                      <li key={item}>{item}</li>
                                    ))}
                                  </ul>
                                </NoteBox>
                              </div>
                            )}
                          </TabBody>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}