# MajorProject_BT-97_CAPSTONE-PROJECT
# MatchWise — AI-Powered Job & Resume Matching System

**MatchWise** is a job recommendation platform designed to help job seekers discover relevant opportunities, understand how well their resumes align with job requirements, identify skill gaps, and receive actionable career-improvement recommendations.

The system combines a hybrid job-matching engine with evidence-based explanations, skill-gap analysis, and personalized improvement suggestions.

> **Project status:** Phases 1–4 are implemented and verified. Phase 5 — live job discovery and ingestion — is currently under development. Live-provider functionality should be considered unverified until a real provider request succeeds.

## Key Features

### 1. Resume Management
- Upload resumes in PDF and DOCX formats.
- Extract resume text and identify relevant skills and qualifications.
- Store resume records in MySQL.
- Validate uploaded files and handle invalid inputs.

### 2. Job Management
- Create, retrieve, filter, and delete job listings.
- Store job descriptions and requirements in MySQL.
- Support required and preferred skills.
- Import existing job data through supported import workflows.

### 3. Hybrid Job-Matching Engine
MatchWise evaluates resume-job compatibility using a weighted scoring model:

| Component | Weight |
|---|---:|
| Required skills | 60% |
| Preferred skills | 15% |
| Semantic similarity | 25% |

The effective weights are adjusted when a scoring component is unavailable.

The matching engine includes:
- Canonical skill matching and aliases.
- Fuzzy matching for minor spelling differences.
- Semantic similarity using a locally available sentence-transformer model.
- Matched and missing skill identification.
- Related-skill handling to avoid treating adjacent skills as exact matches.
- Persistent match results and ranked job recommendations.

**Important:** The match percentage estimates resume-job alignment. It is not a probability of getting hired.

### 4. Explainable Recommendations

MatchWise explains the factors behind each stored match rather than presenting only a percentage.

Features include:
- Overall match interpretation.
- Score-component contributions and effective weights.
- Evidence-based strengths and gaps.
- Required versus preferred skill-gap analysis.
- Unknown or unavailable qualification information.
- Prioritized learning and practice recommendations.
- Portfolio project suggestions.
- Resume improvement and keyword suggestions.

Resume suggestions are designed to avoid fabricating skills, certifications, achievements, or experience. Missing extracted skills are not treated as definitive proof that a candidate lacks them.

The explanation and recommendation layer uses deterministic templates and does not require a paid LLM API.

### 5. Live Job Discovery — In Development

The planned Phase 5 adds integration with a legitimate job-provider API.

Intended capabilities include:
- Search jobs by title, keyword, and location.
- Display company, job description, source, posting date, and application URL when provided.
- Import provider results into the existing job database.
- Prevent duplicate listings.
- Match imported jobs against existing resumes.
- Reuse the existing explanation and skill-gap endpoints.
- Support manual job imports alongside provider listings.

Live listings must not be represented as verified or currently open unless their availability has actually been checked.

## Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite |
| Backend | Python, FastAPI |
| Database | MySQL |
| ORM | SQLAlchemy |
| API validation | Pydantic |
| Resume processing | PDF/DOCX text extraction |
| Semantic matching | Sentence Transformers |
| Semantic model | `all-MiniLM-L6-v2` |
| Testing | pytest, TypeScript checks |
| Development environment | Windows, PowerShell, VS Code |

The project is designed to run without Docker. The core matching and explanation functionality does not require a paid generative-AI API.

## Architecture

```text
                    ┌────────────────────────┐
                    │ React + TypeScript UI  │
                    └───────────┬────────────┘
                                │ HTTP / REST
                    ┌───────────▼────────────┐
                    │    FastAPI Backend     │
                    ├────────────────────────┤
                    │ Resume & Job Services  │
                    │ Skill Extraction       │
                    │ Matching Engine        │
                    │ Explanation Services   │
                    │ Job Import / Provider  │
                    └───────┬─────────┬──────┘
                            │         │
                  ┌─────────▼───┐ ┌───▼────────────┐
                  │    MySQL    │ │ Local Semantic │
                  │             │ │ Model (optional│
                  └─────────────┘ │ when available)│
                                  └────────────────┘
```

The job-provider integration is under development. The diagram describes the intended architecture, not proof that every component is currently operational.

## Project Structure

```text
job recommendation system/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── health.py
│   │   │   ├── jobs.py
│   │   │   ├── matches.py
│   │   │   └── resumes.py
│   │   ├── core/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── extraction.py
│   │   │   ├── features.py
│   │   │   ├── scoring.py
│   │   │   ├── semantic.py
│   │   │   ├── taxonomy.py
│   │   │   └── explanation.py
│   │   ├── main.py
│   │   └── ...
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── src/
    │   ├── pages/
    │   ├── services/
    │   ├── types/
    │   └── ...
    ├── package.json
    └── ...
```

*The structure is representative. Check the current repository for the exact files and names.*

## Getting Started

### Prerequisites

Install the following:
- Python with a version supported by the backend dependencies.
- Node.js and npm.
- MySQL Server.
- Git, if you intend to clone the repository.

### 1. Configure the database

Create a MySQL database for MatchWise and configure its connection using the environment variables supported by the backend.

Check `backend/.env.example` for the expected configuration names.

Do not commit database passwords, API keys, or other secrets to version control.

### 2. Start the backend

Open PowerShell:

```powershell
cd "C:\projects\job recommendation system\backend"
```

Activate the virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

If the virtual environment does not exist, create it and install the project's dependencies according to the repository instructions:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Start FastAPI on port 8001:

```powershell
uvicorn app.main:app --reload --port 8001
```

If Windows reports that the port is unavailable or forbidden, diagnose the port issue and choose an available port. Update the frontend API configuration if the backend port changes.

### 3. Verify the backend

Once the server starts, open:

- Health check: `http://127.0.0.1:8001/api/health`
- Interactive API documentation: `http://127.0.0.1:8001/docs`

These URLs are valid only when the backend is successfully running on port 8001 and the corresponding routes are configured.

### 4. Start the frontend

Open a second PowerShell terminal:

```powershell
cd "C:\projects\job recommendation system\frontend"
npm install
npm run dev
```

Open the local URL printed by Vite, typically `http://localhost:5173`.

Ensure the frontend API base URL matches the actual backend address and port.

## API Overview

The following endpoints have been implemented and tested in the earlier development phases.

### Health
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Check backend health |

### Resumes
The `/api/resumes` route family supports resume management and upload. Consult `/docs` for the exact methods and request schemas.

### Jobs
The `/api/jobs` route family supports job management. Import and live-provider endpoints depend on the final Phase 5 implementation.

### Matching
| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/matches/calculate/{resume_id}` | Calculate and persist resume-job matches |
| GET | `/api/matches/resume/{id}` | Retrieve matches for a resume |
| GET | `/api/matches/job/{id}` | Retrieve matches for a job |
| GET | `/api/matches/{id}` | Retrieve an individual match |

### Explainability
| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/matches/{match_id}/explanation` | Explain the match score |
| GET | `/api/matches/{match_id}/skill-gaps` | Analyze required and preferred skill gaps |
| GET | `/api/matches/{match_id}/improvement-plan` | Generate a prioritized improvement plan |
| GET | `/api/matches/{match_id}/resume-suggestions` | Provide evidence-based resume suggestions |

The live API documentation at `/docs` is the authoritative reference for the currently registered routes.

## Testing

The reported Phase 4 verification results were:

- **46 backend tests passed**.
- TypeScript type checking completed successfully.
- The frontend production build completed successfully.
- The four explainability endpoints returned expected responses for existing MySQL match records.
- Unknown match IDs returned `404`.
- Score contributions summed to the stored match score.
- Browser verification confirmed that the explanation, skill-gap, improvement-plan, and resume-suggestion views rendered real data.

These are results reported at the completion of Phase 4. Re-run the tests against the current codebase after Phase 5 changes, particularly because the live-job integration is still being developed.

Typical verification commands:

```powershell
# Backend
cd "C:\projects\job recommendation system\backend"
.\.venv\Scripts\Activate.ps1
pytest tests/
```

```powershell
# Frontend
cd "C:\projects\job recommendation system\frontend"
npx tsc --noEmit
npm run build
```

## Limitations

- Resume skill extraction is heuristic and can miss skills expressed in unusual language.
- Semantic similarity measures textual relevance; it does not establish candidate competence.
- Experience, education, and certifications may be unknown if the extracted information is insufficient.
- Improvement-plan priorities are deterministic rather than learned from hiring outcomes.
- Recommendation timelines are rough guidance, not personalized predictions.
- Live-job freshness and availability depend on the provider and the checks implemented.
- A listed job's presence in the database does not guarantee that applications remain open.

## Roadmap

- [x] Foundation and backend setup
- [x] Resume upload and job management
- [x] Hybrid resume-job matching engine
- [x] Explainability and skill-gap analysis
- [ ] Live job-provider integration
- [ ] Job import deduplication and freshness handling
- [ ] End-to-end testing of live jobs and matching
- [ ] Final UI polish and demo preparation

## Responsible Use

MatchWise is a decision-support tool, not an automated hiring authority. Match scores are estimates, and missing information can affect results. Users should verify resume details, job requirements, application links, and listing availability before applying.

## License

Add a license before publicly distributing this project. Until one is specified, do not assume that others have permission to redistribute or reuse the code.

---

**MatchWise — Discover relevant opportunities. Understand your fit. Improve with purpose.**
