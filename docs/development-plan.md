# Development Plan — MatchWise

Target: a functional MVP, delivered incrementally. Each phase is independently
verifiable and leaves the application in a runnable state.

## Phase numbering (read this first)

The original draft of this plan described Phase 4 as "ranking/fusion of signals,
filtering, feedback capture" and Phase 5 as an optional local Qwen model. The
implementation took a different — and now settled — route:

| Phase | Shipped as |
| ----- | ---------- |
| 1 | Foundation: FastAPI, MySQL, React shell, health checks |
| 2 | Data model, resume ingestion, job management |
| 3 | Deterministic hybrid matching engine |
| 4 | **Deterministic explainability & advice** (not and LLM) — explanations, skill gaps, improvement plan, resume suggestions |
| 5 | **Live job discovery** (originally Adzuna + CSV/JSON import) — not an LLM |
| 5.1 | Prerequisites for Phase 6: API token auth, Alembic migrations, provider verification script, docs sync |
| 6 | Feedback capture + ranking fusion |
| 7 | **JobsForce India** as the only external provider (Adzuna removed) |

An optional local LLM is **not implemented and not planned** in the current
roadmap: the Phase 4 reports are derived from stored match evidence with
explicit "unknown ≠ failure" rules, and nothing in the current feature set needs
generative text. It stays a possible future extension, not a phase.

## Phase 1 — Project foundation & setup (complete)

- FastAPI app with `/api/health` and `/api/health/db`, CORS for Vite, env-based config.
- SQLAlchemy engine/session infrastructure ready for future entities.
- React + TypeScript + Tailwind dashboard shell with sidebar, placeholders and a live
  backend connection indicator.
- MySQL database script (`backend/scripts/create_matchwise_db.sql`), README and docs.

## Phase 2 — Data model, resume ingestion & job management (complete)

- SQLAlchemy models `Resume` and `Job` with JSON skill columns and indexes.
- `POST /api/resumes/upload` (multipart PDF/DOCX, magic-byte validation, 5 MB
  configurable limit, safe UUID filenames in `backend/uploads/`),
  `GET /api/resumes`, `GET /api/resumes/{id}`, `DELETE /api/resumes/{id}`.
- Full job CRUD with pagination and filters; Pydantic validation.
- Sample jobs via `backend/scripts/seed_jobs.py` (idempotent, `is_sample = true`).
- Frontend: Resumes page, Jobs page, dashboard counts from real data.

## Phase 3 — Matching engine (complete)

- Dynamic skill extraction: taxonomy-assisted (`app/core/skill_taxonomy.json`)
  plus a generic phrase fallback for skills absent from the taxonomy.
- Feature extraction into `ResumeProfile` / `JobProfile`; unknowns stay `None`.
- Deterministic hybrid scoring: required 60% / preferred 15% / semantic 25%,
  configurable, renormalised over available components.
- Optional local Sentence Transformers similarity, with a documented disabled mode.
- `match_results` unique per resume↔job, upserted on recalculation;
  `/api/matches/calculate/{resume_id}` and GET endpoints for resume/job/match.
- Resume Matching page with ranked cards and component breakdown.
- Design & extension guide: `docs/matching.md`.

## Phase 4 — Explainability & advice (complete)

- Deterministic services in `app/services/explanation.py`; no LLM, no network.
- `GET /api/matches/{id}/explanation` — strengths, gaps, limitations, evidence,
  score factors and per-requirement status.
- `GET /api/matches/{id}/skill-gaps` — required vs preferred, priorities,
  related-but-not-equivalent skills, explicit unknowns.
- `GET /api/matches/{id}/improvement-plan` — start now / practice next /
  consider later, with disclaimers and rough estimates.
- `GET /api/matches/{id}/resume-suggestions` — truthful suggestions only.
- Frontend tabs on the Resume Matching page render all four reports.

## Phase 5 — Live job discovery (historical: provider replaced in Phase 7)

- Provider layer with credential handling, country resolution and error mapping
  (Adzuna at the time; see Phase 7 for the current JobsForce India provider).
- Ingestion with dedupe on `(provider, provider_job_id)`, listing lifecycle
  (`active → stale → expired`, rows marked, never deleted).
- CSV/JSON import with per-row validation and duplicate safety.
- `/api/jobs/providers`, `/api/jobs/provider/search`, `/api/jobs/import/csv`,
  `/api/jobs/import/json`, `?source=` filter; Live Jobs page.
- **Historical gap:** no live Adzuna request was verified during Phase 5
  (credentials were unavailable). Superseded by the Phase 7 live check.

## Phase 5.1 — Prerequisites (complete, except the external check)

1. **API token authentication** — `API_TOKEN` guards `/api/resumes*`,
   `/api/jobs*` and `/api/matches*` via one reusable dependency
   (`app/core/security.py`); `Authorization: Bearer` or `X-API-Token`. Missing
   token → `401 unauthorized`, wrong token → `401 invalid_token`. Health checks
   stay open. When `API_TOKEN` is unset the API stays open in local development
   mode and logs a warning at startup.
2. **Alembic migrations** — `backend/alembic.ini` + `backend/migrations/`.
   `init_db()` now runs `upgrade head` instead of `create_all` plus hand-written
   `ALTER TABLE` statements. The baseline (`0001_baseline`) is adoption-safe: it
   creates only missing tables, adds only missing Phase 5 columns/indexes and
   preserves existing rows, so an old database is adopted in place. Its
   downgrade is a deliberate no-op (dropping pre-Alembic tables would destroy
   data); `0002_match_feedback` drops only the feedback table.
3. **Provider verification script** — the Phase 5 script made one bounded
   provider request. It has since been replaced by
   `backend/scripts/verify_jobsforce.py` (Phase 7), which needs no credentials.
4. **Documentation sync** — this file and the README now match the code, with
   consistent phase numbering.

## Phase 6 — Feedback capture + ranking fusion (complete)

- **Model:** `match_feedback` (migration `0002_match_feedback`), append-only
  events keyed to a `match_results` row. Resume/job ids are derived server-side;
  `event_key` (payload hash or `Idempotency-Key`) makes submissions idempotent.
- **API:** `POST /api/matches/{id}/feedback`,
  `GET /api/matches/resume/{id}?rank=engine|blended`,
  `GET /api/matches/resume/{id}/feedback-summary`.
- **Ranking:** `app/services/ranking.py`. `blended = engine + clamp(cap ·
  Σ(wᵢ · signalᵢ), ±cap)` with signals for the job, shared skill categories and
  the company, additive smoothing, normalised weights (default 0.5 / 0.3 / 0.2)
  and a configurable cap (default ±10 points). Every adjustment is returned with
  per-signal evidence and a summary.
- **Zero-feedback guarantee:** no feedback ⇒ every signal is exactly 0 ⇒
  blended equals engine, with the same order and pagination.
- **Frontend:** quick Helpful / Not relevant buttons on each card, optional
  details form (relevance 1–5, outcome, reason), Original vs Feedback-adjusted
  ranking selector, outcome status filter and a "Why this rank moved" section in
  the explanation tab.
- **Tests:** 27 Phase 6 tests covering persistence, history, idempotency,
  validation, auth, ranking modes, category/company/job signals, contradictory
  and insufficient evidence, bounds, explainability, pagination, summary and the
  zero-feedback regression; plus 4 migration tests.

## Phase 7 — JobsForce India (complete)

- **Single provider:** `app/services/providers/jobsforce.py`, registered as
  `jobsforce`; `DEFAULT_PROVIDER = "jobsforce"` in `app/api/jobs.py`. Adzuna
  (module, script, tests and credential requirements) was removed.
- **API:** `GET /api/public/v1/jobs` (page 1–10, per_page 1–20; q, city, state,
  category, job_type, work_mode, fresher, experience_years, min_monthly_pay,
  posted_within_days), `GET /api/public/v1/jobs/{id}` and
  `GET /api/public/v1/catalog` (cached). No authentication.
- **Preserved from the provider:** original id, title, employer/recruiter
  attribution, city/state, work mode, employment type, experience range, salary
  **with currency and period**, description sections, verbatim skill lists,
  listing URL, application URL, posting date and closing date.
- **Agency rule:** when `hiring.type == "agency"` the confidential client is
  never named; the stored company is `hiring.display` (e.g. "JobsForce India
  (recruiting for a confidential client)").
- **Full details:** new rows are enriched from the detail endpoint before they
  are matched; 410/404 marks the row `expired` (never deleted), other failures
  keep the summary row, and 429 stops the enrichment loop.
- **Dedupe:** `(provider, provider_job_id)`, then `(provider, listing_url)`, then
  `(provider, title, company)`; `application_status` and `fetched_at` survive
  re-imports.
- **Schema:** migration `0003_jobsforce_fields` adds `salary_currency`,
  `salary_period`, `listing_url`, `work_mode` (indexed), `category` (indexed),
  `expires_date`, `hiring_type`; existing rows keep NULLs.
- **Matching:** unchanged — provider text feeds the existing taxonomy extractor
  and scoring/explanation/feedback code; there is no second scorer.
- **Frontend:** Live Jobs page rebuilt around the provider filters (city, state,
  category, work mode, fresher, pay, posted-within), catalog-backed options,
  provider pagination, source attribution and original listing links.
- **Verification:** 39 mocked provider tests, 7 import tests, migration tests
  and one controlled live search + detail request (HTTP 200, real listings).

## Out of scope (explicitly not implemented)

- Machine-learning or reinforcement-learning training; model fine-tuning.
- User accounts, registration, OAuth, roles or multi-user identity management
  (Phase 5.1 is a single local operator token).
- LLM-generated explanations, feedback analysis or embeddings from paid APIs.
- Redis, Docker, background workers, queues or scheduled feedback processing.
- Production deployment hardening beyond the local token.

## Verification discipline

Every phase ends with: backend starts, health checks respond, frontend builds,
and the touched flows are exercised for real. Anything not run is reported as
not tested (the Phase 5 Adzuna note above is the historical example).
