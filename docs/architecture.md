# Architecture — MatchWise (Phase 1)

MatchWise is an expandable job and resume matching recommendation system. Phase 1
establishes the foundation only: one FastAPI backend, one MySQL database, one React
frontend, with clear seams for later phases.

## High-level structure

```
┌──────────────────────────────┐        HTTP/JSON (REST)        ┌───────────────────────────┐
│  frontend/  (React + TS)     │ ─────────────────────────────▶ │  backend/  (FastAPI)      │
│  Vite dev server :5173       │ ◀───────────────────────────── │  uvicorn   :8000          │
│                              │                                │                           │
│  pages/     route screens    │                                │  api/     route handlers  │
│  components/ UI building     │                                │  services/ business logic │
│             blocks           │                                │  schemas/  pydantic I/O   │
│  services/  API client       │                                │  models/   SQLAlchemy     │
│  types/     shared types     │                                │           tables          │
└──────────────────────────────┘                                │  core/     config         │
                                                                │  db/       engine/session │
                                                                └─────────────┬─────────────┘
                                                                              │ SQLAlchemy
                                                                              ▼
                                                                    ┌──────────────────┐
                                                                    │ MySQL  :3306     │
                                                                    │ matchwise_db     │
                                                                    └──────────────────┘
```

## Backend layers

| Layer        | Location             | Responsibility                                                    |
| ------------ | -------------------- | ----------------------------------------------------------------- |
| `core/`      | `app/core/config.py` | Reads environment variables into a cached `Settings` object.      |
|              | `app/core/errors.py` | `APIError` → consistent `{"error": {code, status, message}}` body. |
| `db/`        | `app/db/session.py`  | Engine, `SessionLocal`, `get_db()` dependency, declarative `Base`.|
|              | `app/db/init.py`     | `init_db()` — idempotent `create_all` at startup.                 |
| `models/`    | `app/models/`        | ORM tables: `Resume`, `Job`, `MatchResult`.                       |
| `schemas/`   | `app/schemas/`       | Pydantic request/response contracts (resume, job, health).        |
| `api/`       | `app/api/`           | Routers: `/api/health`, `/api/resumes`, `/api/jobs`, `/api/matches`. |
| `services/`  | `app/services/`      | `storage`, `resume_parser`, `resumes`, `jobs`, and the Phase 3 |
|              |                      | matching stack: `taxonomy`, `extraction`, `features`, `scoring`, |
|              |                      | `semantic`, `matching`.                                         |
| `main.py`    | `app/`               | App factory, CORS, error handlers, router registration.           |

## Request flow

1. Browser calls the Vite dev server or the API directly.
2. `src/services/api.ts` builds the URL from `VITE_API_BASE_URL`.
3. FastAPI router validates input, delegates to `services/` (later phases),
   which uses the `get_db()` session dependency.
4. Response is serialised through a Pydantic schema.

## Health checks

- `GET /api/health` — application liveness: name, version, environment.
- `GET /api/health/db` — executes a real `SELECT 1` through the SQLAlchemy
  engine. Returns HTTP 503 with `{"status": "error", "database": {"connected": false}}`
  when MySQL is unreachable. Error details are logged server-side only;
  credentials never appear in a response body.

## Data model (Phase 2)

| `resumes`                          | `jobs`                                  |
| ---------------------------------- | --------------------------------------- |
| `id`, `candidate_name?`, `original_filename`, `stored_filename` | `id`, `title`, `company`, `location?` |
| `file_type` (pdf/docx), `file_size`| `description`                           |
| `extracted_text` (Text), `skills` (JSON, empty for now) | `required_skills`, `preferred_skills` (JSON) |
| `uploaded_at`, `processing_status` (indexed) | `minimum_experience`, `employment_type`, `is_sample` |
|                                    | `created_at`, `updated_at`              |

## Resume upload flow

```
POST /api/resumes/upload (multipart)
  1. extension check (.pdf/.docx)                    → 415
  2. stream body, enforce UPLOAD_MAX_BYTES            → 413
  3. magic-byte check (%PDF / DOCX-zip)               → 415 (mismatch/corrupt)
  4. save as <uuid>.<ext> in backend/uploads/         (never the client name)
  5. extract text (PyMuPDF / python-docx, from memory) → 415 on failure,
     file removed, no DB row possible
  6. INSERT row (processing_status = processed|no_text), commit
     → on commit failure: rollback + delete the file

DELETE /api/resumes/{id} → row deleted first, then the stored file
```

Uploaded files are **not** exposed by any static route; only the extracted text
is served, through `GET /api/resumes/{id}`.

## Matching flow (Phase 3)

```
POST /api/matches/calculate/{resume_id}
  1. load resume + jobs (MySQL)                         → 404 for unknown ids
  2. extract skills from resume text and job fields      (taxonomy + fallback)
  3. score each pair: required / preferred coverage + semantic similarity
  4. renormalise weights over available components       (never fabricate)
  5. upsert match_results (unique on resume_id+job_id)   → no duplicates
  6. return ranked list (score desc) + semantic status + signals

GET /api/matches/resume/{id}  → ranked jobs   GET /api/matches/job/{id} → ranked resumes
```

Full design, configuration and extension points: `docs/matching.md`.

## Security posture (Phase 5.1)

Single-operator token auth lives in `app/core/security.py`. When `API_TOKEN` is
set, the resume, job and match routers (including feedback) require
`Authorization: Bearer <token>` or `X-API-Token`; missing → `401 unauthorized`,
wrong → `401 invalid_token`. Health checks remain open. With no `API_TOKEN` the
API stays open for local development and logs a startup warning — there are no
accounts, roles or sessions.

`.env` (with `DATABASE_URL`, `API_TOKEN`; the JobsForce India provider needs no
credentials) is git-ignored, filenames
are sanitised to a basename, and no credentials or raw driver errors are ever
serialised.

## Feedback-aware ranking (Phase 6)

`match_feedback` stores append-only events (one row per submission) linked to a
`match_results` row. `app/services/ranking.py` aggregates them per resume into
three signals (job, shared skill category, company), applies additive smoothing
and normalised weights, and returns a capped adjustment plus a per-signal
explanation. The stored engine score is never recalculated or modified; with no
feedback the blended ranking is identical to the engine ranking.

```
POST /api/matches/{id}/feedback     append (idempotent via payload hash/Idempotency-Key)
GET  /api/matches/resume/{id}?rank=engine|blended
GET  /api/matches/resume/{id}/feedback-summary
```

## Configuration & secrets

- Backend reads `DATABASE_URL` and `ALLOWED_ORIGINS` from `backend/.env`
  (loaded via `python-dotenv`); `.env` is git-ignored, `.env.example` holds
  placeholders only.
- Frontend reads `VITE_API_BASE_URL` from `frontend/.env.local`.
- CORS allows only the local Vite origins by default.

## Extension points for later phases

| Phase feature            | Where it plugs in                                        |
| ------------------------ | -------------------------------------------------------- |
| New tables               | `app/models/` (inherit `app.db.Base`) + Alembic revision |
| New skills / aliases     | `app/core/skill_taxonomy.json` or `SKILL_TAXONOMY_PATH`  |
| New scoring component    | `app/services/scoring.py` + a weight in `Settings`       |
| LLM assistance (Qwen)    | `app/services/` — optional, local only                   |
| Access control           | dependency on the routers (`app/api/`)                   |

No Docker, Redis, queues or microservices: one FastAPI process and one MySQL
database keep the MVP simple and reliable.
