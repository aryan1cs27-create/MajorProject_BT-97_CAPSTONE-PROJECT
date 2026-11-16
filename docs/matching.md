# Matching Engine (Phase 3)

One reusable, deterministic engine scores **any** resume against **any** job —
no per-category algorithms and no fixed list of job titles. Everything below
lives in `backend/app/services/`.

```
resume text ─┐
             ├─▶ extraction.py ─▶ features.py ─▶ scoring.py ─▶ matching.py ─▶ match_results
job fields ──┘        │               │              │              │
                      │               │              ├─ weights     ├─ upsert per (resume, job)
        taxonomy.py ◀─┘               │              ├─ semantic.py  └─ ranking / pagination
       (skill taxonomy)               │              └─ signals
                                      └─ ResumeProfile / JobProfile
```

## 1. Skill extraction (`extraction.py`)

Two passes over the source text:

1. **Dictionary-assisted** — every canonical name and alias in the taxonomy is
   matched with token boundaries, case-insensitively. Longer names win
   (`React Native` consumes the span, so `React` is not matched inside it).
   Ambiguous terms (`Go`, `R`, `C`, `IR`, `PM` …) additionally require either a
   skills cue on the same line (`experience`, `skills`, `engineer`, …) or a list
   position (`Skills: R, Python`).
2. **Generic phrase fallback** — proposes *discovered* skills from contexts where
   skills actually appear: comma/semicolon lists, `experience with X`,
   `built tooling with X`, `X developer`, and technical spellings (`C++`,
   `Node.js`). Each candidate is scored (0–1 confidence) and rejected when it
   looks like a person's name, a responsibility, a qualification, a job title or
   a generic noun.

Every extracted skill carries: original phrase, canonical name (when
normalised), category, confidence, `source` (`taxonomy` | `discovered`) and the
source snippet. **Discovered skills participate in matching immediately**, even
before anyone adds them to the taxonomy.

## 2. Taxonomy (`app/core/skill_taxonomy.json`)

```jsonc
{
  "skills": [
    {
      "canonical": "JavaScript",
      "category": "programming_language",
      "aliases": ["JS", "ECMAScript"],
      "related": ["TypeScript", "React"],   // never scored as a match
      "ambiguous": false                     // true → needs contextual evidence
    }
  ]
}
```

**Adding a technology = adding a JSON entry.** No algorithm change, no restart
logic to write (the file is read lazily). For a second source, point
`SKILL_TAXONOMY_PATH` at another JSON file of the same shape; entries are merged
by canonical name. Moving the taxonomy into a database table is a deliberate
Phase-4 option (see README).

`related` produces a **near-miss signal only** — knowing Next.js never counts as
knowing React.

## 3. Profiles (`features.py`)

`build_resume_profile` / `build_job_profile` produce the structured inputs:
skills + categories, years of experience (**`None` when not stated — never
zero**), titles, education, certifications, seniority, role family, and the
normalised required/preferred skill keys from the job row. Nothing absent from
the source is invented.

## 4. Scoring (`scoring.py`)

```
final = Σ (effective_weight_i × component_i)          components: 0–100
```

| Component  | Default weight | Definition                                            |
| ---------- | -------------- | ----------------------------------------------------- |
| `required` | 0.60           | matched required / total required × 100              |
| `preferred`| 0.15           | matched preferred / total preferred × 100            |
| `semantic` | 0.25           | cosine similarity of resume vs job text × 100        |

* Matching: exact → alias-normalised → fuzzy (`MATCH_FUZZY_THRESHOLD`, default
  0.9, set `0` to disable). Fuzzy matches are reported as `fuzzy`.
* **Fallback rules**: a component that is unavailable (no required skills, no
  preferred skills, semantic disabled/empty text) is dropped and the remaining
  weights are **renormalised proportionally**; the effective weights are always
  returned. With everything unavailable the score is `0` and `confidence` is 0 —
  the engine never invents a number.
* Experience, education and certifications are returned as **signals**, not as
  score components, and missing resume data is `unknown`, never a penalty.
* The function is a pure, deterministic function of its inputs.

### Configuration (env, see `backend/.env.example`)

```
MATCH_WEIGHT_REQUIRED=0.60   MATCH_WEIGHT_PREFERRED=0.15   MATCH_WEIGHT_SEMANTIC=0.25
MATCH_FUZZY_THRESHOLD=0.9
MATCH_SEMANTIC_ENABLED=true  MATCH_SEMANTIC_MODEL=sentence-transformers/all-MiniLM-L6-v2
SKILL_TAXONOMY_PATH=/path/to/extra_skills.json
```

## 5. Semantic similarity (`semantic.py`)

Local Sentence Transformers (no API key). The module loads lazily and reports
`available` + a human-readable `reason`. When unavailable — package missing
(`pip install -r requirements-semantic.txt` not run), model download failure, or
`MATCH_SEMANTIC_ENABLED=false` — `similarity()` returns `None`, the component is
removed and its weight redistributed. **A semantic score is never fabricated.**

## 6. Persistence & API (`matching.py`, `app/api/matches.py`)

* `match_results` has a **unique constraint on `(resume_id, job_id)`**; scoring
  upserts, so recalculating updates instead of duplicating.
* Rows store component scores, matched/missing skills, effective weights,
  signals, details (match types, discovered skills, unavailable components,
  notes) and confidence.
* Endpoints: `POST /api/matches/calculate/{resume_id}`,
  `GET /api/matches/resume/{resume_id}`, `GET /api/matches/job/{job_id}`,
  `GET /api/matches/{match_id}` (all paginated where applicable, 404 on unknown
  ids, JSON numbers for scores).

## 7. Extending the engine

* **Add a skill** → append JSON to the taxonomy (or `SKILL_TAXONOMY_PATH`).
* **Add a scoring component** (e.g. education fit):
  1. compute it in `scoring.py::score_pair` and add it to `components`;
  2. add its default weight to `Settings.match_weights` (env var included);
  3. it is renormalised and reported automatically by `_effective_weights`.
* **Change matching rules** → thresholds live in `Settings`; keep `score_pair`
  pure so tests keep proving determinism.
* **Persist a taxonomy table** → replace `taxonomy.load_taxonomy()` with a
  query; call sites do not change.

## 8. Known limitations (honest list)

* Phrase discovery is heuristic: brand-new skills are found mainly in list or
  cue contexts, so a skill buried in an unusual prose sentence can be missed.
* Fuzzy matching is character-similarity based (difflib) — it catches typos, not
  semantic synonyms (`k8s` is handled by the alias table, not by fuzzy).
* Related-skill knowledge is reported as a signal only, so candidates with
  adjacent skills get no credit towards the required-skill coverage.
* Experience/education signals are not (yet) part of the score, by design: the
  extraction rules are not yet reliable enough to justify penalising anyone.
* The semantic model is English-oriented and adds ~25 s to the first calculation
  (model load) on a laptop.
