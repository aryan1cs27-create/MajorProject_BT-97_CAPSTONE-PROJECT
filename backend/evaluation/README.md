# MatchWise offline evaluation

Reproducible harness for the paper's evaluation section. Everything runs
offline against the real engine in `app/services/scoring.py`; no external API
and no LLM is involved.

Measured results of the latest run are written up in [`RESULTS.md`](RESULTS.md).

## Run

```bash
cd backend
.venv/Scripts/python.exe evaluation/run_evaluation.py
```

Outputs land in `evaluation/results/`:

| File | Contents |
|---|---|
| `metrics.csv` | Precision/Recall/F1/NDCG at K=5 and K=10 for eight systems |
| `report.md` | Tables + runtime + error analysis |
| `ndcg_bar.svg` | Bar chart of NDCG@5 / NDCG@10 |
| `extraction.csv` | Per-document skill-extraction precision/recall |
| `errors.csv` | Top-5 false negatives and false positives with score components |

`annotation_sheet.csv` is the blinded two-annotator labelling sheet (400 pairs,
shuffled, no system scores). Fill `annotator_a` / `annotator_b`, then:

```bash
.venv/Scripts/python.exe evaluation/cohen_kappa.py
```

## Benchmark

* 10 resumes and 40 jobs (400 pairs), synthetic but realistic, covering backend,
  frontend, data, ML, DevOps, security, mobile, QA and support roles.
* Relevance grades: 3 = shortlist strongly, 2 = shortlist (borderline),
  1 = adjacent but not qualified, 0 = irrelevant. Precision/Recall/F1 use
  `grade >= 2`; NDCG uses graded gains `2^grade - 1`.
* 42 of 400 pairs are relevant. The first version used 26 jobs and saturated at
  `Recall@5 = 1.0`; 14 hard distractor roles were added so that ranking between
  adjacent roles actually matters.
* Gold skill labels are the skills a human annotator sees stated in each
  document (50 documents: 10 resumes + 40 job descriptions).

## Systems compared

* Keyword baseline: TF-IDF (1-2 grams, sublinear TF) cosine similarity.
* Hybrid: 60% required / 15% preferred / 25% semantic (the shipped default).
* Ablations: required only, preferred only, semantic only, no semantic
  (60/15/0).
* Weight variants: 50/20/30 and 70/10/20.

Weights are applied by patching `scoring.get_settings()` for the duration of a
system; the scoring code itself is unchanged.

## Caveats

* Labels were authored by a single annotator (the evaluation harness author),
  so no inter-annotator agreement exists yet. Item 4 of the plan needs a second
  independent human on `annotation_sheet.csv`.
* The benchmark has 10 resumes, not the 30-50 recommended in the plan. Treat the
  absolute numbers as indicative; the relative ordering of systems is the useful
  signal.
* Explanation usefulness (item 5) requires 5-10 human raters and is not measured
  here.
