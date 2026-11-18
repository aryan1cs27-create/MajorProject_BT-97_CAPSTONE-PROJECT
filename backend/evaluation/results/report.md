# MatchWise offline evaluation

Benchmark: 10 resumes x 40 jobs = 400 pairs; relevant (grade >= 2) = 42.

## Table III - ranking comparison

| System | P@5 | R@5 | F1@5 | NDCG@5 | P@10 | R@10 | F1@10 | NDCG@10 |
|---|---|---|---|---|---|---|---|---|
| Keyword baseline (TF-IDF) | 0.720 | 0.868 | 0.768 | 0.997 | 0.410 | 0.967 | 0.563 | 0.987 |
| Hybrid (60/15/25) | 0.780 | 0.955 | 0.838 | 0.997 | 0.420 | 1.000 | 0.579 | 0.994 |
| Ablation: required only | 0.720 | 0.874 | 0.771 | 0.995 | 0.420 | 1.000 | 0.579 | 0.982 |
| Ablation: preferred only | 0.540 | 0.687 | 0.589 | 0.947 | 0.320 | 0.778 | 0.443 | 0.926 |
| Ablation: semantic only | 0.680 | 0.829 | 0.729 | 0.994 | 0.410 | 0.975 | 0.564 | 0.981 |
| Ablation: no semantic (60/15/0) | 0.760 | 0.940 | 0.821 | 0.996 | 0.420 | 1.000 | 0.579 | 0.992 |
| Weights 50/20/30 | 0.760 | 0.940 | 0.821 | 0.997 | 0.420 | 1.000 | 0.579 | 0.995 |
| Weights 70/10/20 | 0.780 | 0.955 | 0.838 | 0.997 | 0.420 | 1.000 | 0.579 | 0.993 |

Hybrid minus baseline NDCG@10: **+0.007** (paired Wilcoxon p = 0.0645, n = 10).

## Skill extraction accuracy

| Scope | Precision | Recall | F1 |
|---|---:|---:|---:|
| all | 0.794 | 0.989 | 0.881 |
| taxonomy_only | 0.962 | 0.982 | 0.972 |

## Runtime

- Hardware: Intel64 Family 6 Model 140 Stepping 1, GenuineIntel | Windows 11 | Python 3.14.6
- Semantic model available: True (load 1.5 s)
- Resume profile extraction: mean 10.6 ms, p95 12.5 ms
- Job profile extraction: mean 5.6 ms, p95 7.1 ms
- Scoring one pair (400 pairs): mean 1.8 ms, p95 2.8 ms
- End-to-end resume-to-all-jobs: mean 68.1 ms, p95 92.0 ms
- Job retrieval (SELECT all jobs, read-only): mean 2.6 ms, p95 3.6 ms

## Error analysis (hybrid, relevant jobs outside top-5)

3 misses; 11 irrelevant jobs inside top-5.

| Resume | Job | Grade | Rank | Score | Cause |
|---|---|---:|---:|---:|---|
| r3 | j25 Data Engineer (Azure) | 2 | 7 | 40.88 | ranking & weighting (job found but ranked below top-5) |
| r3 | j38 Data Engineer (Spark) | 2 | 6 | 40.95 | ranking & weighting (job found but ranked below top-5) |
| r5 | j12 Cloud Solutions Architect | 2 | 6 | 64.07 | ranking & weighting (job found but ranked below top-5) |
