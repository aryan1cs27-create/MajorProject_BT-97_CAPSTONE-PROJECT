# MatchWise: evaluation results

Measured results from `backend/evaluation/run_evaluation.py`, which runs the real matching engine
(`app/services/scoring.py`) offline. Nothing here is estimated or simulated except where explicitly
marked as not measured.

- Date of run: 16 November 2026
- Machine: Intel64 Family 6 Model 140, Windows 11, Python 3.14.6, single process, CPU only
- Benchmark: 10 resumes x 40 jobs = 400 pairs, 42 relevant pairs (grade >= 2)
- Relevance scale: 3 = shortlist strongly, 2 = shortlist (borderline), 1 = adjacent, 0 = irrelevant
- Precision/Recall/F1 use `grade >= 2`; NDCG uses graded gains `2^grade - 1`
- Gold skill labels: 50 documents (10 resumes + 40 job descriptions)

Artifacts: `results/metrics.csv`, `results/report.md`, `results/extraction.csv`, `results/errors.csv`,
`results/ndcg_bar.svg`, `annotation_sheet.csv`.

## 1. Ranking metrics (K = 5 and K = 10)

| System | P@5 | R@5 | F1@5 | NDCG@5 | P@10 | R@10 | F1@10 | NDCG@10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Keyword baseline (TF-IDF) | 0.720 | 0.868 | 0.768 | 0.997 | 0.410 | 0.967 | 0.563 | 0.987 |
| **Hybrid 60/15/25 (shipped)** | **0.780** | **0.955** | **0.838** | 0.997 | 0.420 | 1.000 | 0.579 | 0.994 |
| Ablation: required only | 0.720 | 0.874 | 0.771 | 0.995 | 0.420 | 1.000 | 0.579 | 0.982 |
| Ablation: preferred only | 0.540 | 0.687 | 0.589 | 0.947 | 0.320 | 0.778 | 0.443 | 0.926 |
| Ablation: semantic only | 0.680 | 0.829 | 0.729 | 0.994 | 0.410 | 0.975 | 0.564 | 0.981 |
| Ablation: no semantic (60/15/0) | 0.760 | 0.940 | 0.821 | 0.996 | 0.420 | 1.000 | 0.579 | 0.992 |
| Weights 50/20/30 | 0.760 | 0.940 | 0.821 | 0.997 | 0.420 | 1.000 | 0.579 | 0.995 |
| Weights 70/10/20 | 0.780 | 0.955 | 0.838 | 0.997 | 0.420 | 1.000 | 0.579 | 0.993 |

Hybrid minus keyword baseline on NDCG@10: **+0.007** (paired Wilcoxon signed-rank,
**p = 0.0645**, n = 10 resumes). Improvement is consistent but not significant at alpha = 0.05
on this sample. At K = 5 the gain is larger and more meaningful: **+8.7 points Recall@5**
(0.868 -> 0.955) and **+6.0 points Precision@5** (0.720 -> 0.780).

**Saturation warning.** NDCG is ~0.99 for every system, including the keyword baseline, because
the 40-job pool is small and the top ranks are almost always correct. NDCG does not discriminate
between systems on this benchmark; P@5 / R@5 / F1@5 are the discriminative metrics here. A paper
table that leads with NDCG would overstate near-equality or near-perfection.

### Baseline definition

Keyword baseline = TF-IDF cosine similarity (1-2 grams, sublinear TF, English stop words) between
the raw resume text and the concatenated job title + description + required/preferred labels.

## 2. Weight ablation and robustness

| Weights | P@5 | R@5 | NDCG@5 | NDCG@10 |
|---|---:|---:|---:|---:|
| 60/15/25 (shipped) | 0.780 | 0.955 | 0.997 | 0.994 |
| 50/20/30 | 0.760 | 0.940 | 0.997 | 0.995 |
| 70/10/20 | 0.780 | 0.955 | 0.997 | 0.993 |

Spread across the three weightings is at most 0.02 P@5 and 0.002 NDCG@10, so the ranking is robust
to reasonable reweighting. Component ablations show the weights are not redundant:

- Preferred-only collapses (P@5 0.540, NDCG@10 0.926), so preferred skills alone are a weak signal.
- Required-only loses ~8 points Recall@5 vs hybrid (0.874 vs 0.955), so the preferred and semantic
  components add real recall.
- Removing semantics costs 0.002 NDCG@10 (0.992 vs 0.994) and 1.5 points R@5, so the semantic
  component helps modestly but is not the main driver on this benchmark.

Supported claim: the 60/15/25 choice is not brittle. Only weakly supported claim: semantics is
essential.

## 3. Skill-extraction accuracy

| Scope | Precision | Recall | F1 |
|---|---:|---:|---:|
| Taxonomy dictionary pass only | 0.962 | 0.982 | 0.972 |
| Full extractor (incl. generic discovery) | 0.794 | 0.989 | 0.881 |

The taxonomy pass is the accurate component. The generic "discovered skills" pass raises recall by
0.7 points but sharply lowers precision; on resumes alone precision is 0.50-0.64 because it labels
date fragments and section headings as skills:

- `2020`, `2019`, `education`, `experience`
- `shopstack (2020-2025)`, `cloudworks (2019-2025)`
- `stack developer`, `kafka ev`, `firmware`, `machine learning` (duplicate phrase variants)

Concrete misses:

- **Firebase is not in the 168-skill taxonomy**; recall drops to 0.80 on the documents that mention it.
- `Postman` is missed (resume r9, taxonomy recall 0.833).
- `Communication` is missed in job j28 because the text says "communicate" (verb form).

## 4. Annotation agreement (Cohen's kappa)

**Not computed.** Kappa requires two independent human annotators; producing a value here would be
fabrication. A blinded sheet is provided: `annotation_sheet.csv` (400 shuffled pairs, no system
scores). Procedure:

1. Two annotators independently label `annotator_a` and `annotator_b` with grades 0-3.
2. Run `cd backend && .venv/Scripts/python.exe evaluation/cohen_kappa.py`.
3. Report observed agreement, expected agreement, kappa and the Landis-Koch band.

The grades used in this report were authored by a single annotator (the harness author), so they
represent a ceiling on agreement, not evidence of inter-annotator reliability.

## 5. Explanation usefulness

**Not measured.** This requires 5-10 human raters scoring the generated explanations; it cannot be
produced by an automated run and no such ratings exist. The service implements `explain_match`,
`skill_gap_analysis`, `improvement_plan` and `resume_suggestions`, but this report makes no usefulness
claim about them.

## 6. Runtime

| Stage | Mean | p95 |
|---|---:|---:|
| Semantic model load (warm disk cache) | 1.5 s | - |
| Resume profile extraction | 10.6 ms | 12.2 ms |
| Job profile extraction | 5.8 ms | 7.4 ms |
| Scoring one resume-job pair (400 pairs) | 1.8 ms | 2.8 ms |
| End-to-end: 1 resume vs 40 jobs | 68.1 ms | 92.0 ms |
| Job retrieval (MySQL, read-only, 10 rows) | 2.5 ms | 3.5 ms |

First-ever model load with a cold Hugging Face cache measured ~47.8 s (download + load); the 1.5 s
above is a warm-disk reload. Measurements exclude network and browser time.

## 7. Error analysis (hybrid, top-5)

**False negatives: 3 of 42 relevant pairs (7.1%).** All are grade-2 borderline roles ranked 6th-7th,
i.e. just outside the top 5, and all fail on missing required skills while covering about half of the
requirements:

| Resume | Job | Grade | Rank | Score | Missing required |
|---|---|---:|---:|---:|---|
| r3 | j25 Data Engineer (Azure) | 2 | 7 | 40.88 | Microsoft Azure; ETL |
| r3 | j38 Data Engineer (Spark) | 2 | 6 | 40.95 | Apache Spark; ETL |
| r5 | j12 Cloud Solutions Architect | 2 | 6 | 64.07 | System Design |

This is a ranking/weighting failure, not a retrieval failure. No vocabulary-gap misses surfaced.

**False positives: 11 jobs inside top-5.**

- 8 are grade-1 adjacent roles (e.g. r8 -> Cloud Engineer, r9 -> Backend Engineer). Expected when a
  top-5 must be filled but only 3-4 jobs are truly relevant.
- 3 are genuine grade-0 noise (r6 -> Data Engineer Spark, r7 -> Java Developer, r9 -> Penetration
  Tester), all scoring at or below 32.

**Extraction noise** is the other recurring error source: discovery false positives (dates, company
names, headings) pollute the skill list and the semantic text.

**Provider-data errors: not testable offline.** The evaluated path never calls an external job
provider, so provider-side failures cannot occur or be measured here.

## Limitations

1. Labels are single-annotator (author) and not independently verified; kappa and usefulness ratings
   remain open items.
2. The benchmark is below the recommended size (10 resumes rather than 30-50, 42 relevant pairs), so
   absolute values are optimistic and confidence intervals are wide.
3. NDCG saturates at this pool size; use P/R/F1@K or a larger job pool for system comparison.
4. Runtime is one machine, single process, warm model, CPU only, no network.

## Reproduce

```bash
cd backend
.venv/Scripts/python.exe evaluation/run_evaluation.py
```

Exits 0 and regenerates every artifact under `evaluation/results/`. The harness has no dependency on
the web app and does not modify application code or the database.
