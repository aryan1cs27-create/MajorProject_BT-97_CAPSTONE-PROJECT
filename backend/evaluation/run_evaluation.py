"""Offline evaluation harness for MatchWise.

Runs the *real* matching engine (``score_pair``) over the hand-annotated
benchmark in ``benchmark.json`` and writes:

* ``results/metrics.csv``            - per-system ranking metrics
* ``results/report.md``             - tables + narrative-ready numbers
* ``results/ndcg_bar.svg``          - bar chart of NDCG@5 / NDCG@10
* ``results/extraction.csv``        - per-document skill-extraction accuracy
* ``results/errors.csv``            - hybrid top-5 misses with diagnostics
* ``annotation_sheet.csv``          - blinded labelling sheet for a 2nd annotator

Usage (from ``backend/``):

    .venv/Scripts/python.exe evaluation/run_evaluation.py
"""

from __future__ import annotations

import csv
import json
import math
import platform
import random
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
sys.path.insert(0, str(BACKEND))

from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402
from sklearn.metrics.pairwise import cosine_similarity  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.services import scoring, semantic  # noqa: E402
from app.services.extraction import extract_skills  # noqa: E402
from app.services.features import build_job_profile, build_resume_profile  # noqa: E402

RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

KS = (5, 10)
RELEVANT = 2  # grade >= 2 counts as relevant for precision/recall/F1

SYSTEMS = {
    "keyword_tfidf": None,                      # baseline, handled separately
    "hybrid_60_15_25": (0.60, 0.15, 0.25),
    "ablate_required_only": (1.00, 0.00, 0.00),
    "ablate_preferred_only": (0.00, 1.00, 0.00),
    "ablate_semantic_only": (0.00, 0.00, 1.00),
    "ablate_no_semantic": (0.60, 0.15, 0.00),
    "weights_50_20_30": (0.50, 0.20, 0.30),
    "weights_70_10_20": (0.70, 0.10, 0.20),
}
LABELS = {
    "keyword_tfidf": "Keyword baseline (TF-IDF)",
    "hybrid_60_15_25": "Hybrid (60/15/25)",
    "ablate_required_only": "Ablation: required only",
    "ablate_preferred_only": "Ablation: preferred only",
    "ablate_semantic_only": "Ablation: semantic only",
    "ablate_no_semantic": "Ablation: no semantic (60/15/0)",
    "weights_50_20_30": "Weights 50/20/30",
    "weights_70_10_20": "Weights 70/10/20",
}

# Skill pairs that are genuinely the same requirement under different wording.
# Used only for the error analysis, never for scoring.
SYNONYMS = {
    "sql": {"postgresql", "mysql", "microsoft sql server", "oracle", "sqlite"},
    "postgresql": {"sql"},
    "mysql": {"sql"},
    "spring": {"java"},
    "test automation": {"selenium", "pytest", "cypress"},
    "incident response": {"siem", "splunk"},
    "system design": {"microservices", "solutions architecture"},
    "performance tuning": {"mysql", "database administration"},
    "database administration": {"mysql", "sql"},
    "penetration testing": {"owasp", "application security"},
    "mlops": {"docker", "kubernetes"},
    "react native": {"react", "android", "kotlin"},
    "ios": {"swift", "android", "kotlin"},
    "business analysis": {"agile", "sql", "tableau"},
    "customer support": {"communication"},
}


# --------------------------------------------------------------------- data
def load_dataset() -> dict:
    return json.loads((HERE / "benchmark.json").read_text(encoding="utf-8"))


def resume_object(record: dict):
    return SimpleNamespace(
        id=record["id"],
        candidate_name=record["candidate_name"],
        extracted_text=record["text"],
    )


def job_object(record: dict):
    return SimpleNamespace(
        id=record["id"],
        title=record["title"],
        company=record["company"],
        location=record.get("location"),
        description=record["description"],
        required_skills=record.get("required_skills", []),
        preferred_skills=record.get("preferred_skills", []),
        minimum_experience=record.get("minimum_experience"),
        employment_type=record.get("employment_type", "full-time"),
    )


def job_text(job_profile) -> str:
    parts = [job_profile.title, job_profile.description]
    parts.extend(job_profile.required_labels.values())
    parts.extend(job_profile.preferred_labels.values())
    return "\n".join(p for p in parts if p)


# ------------------------------------------------------------------ metrics
def dcg(grades: list[int], k: int) -> float:
    total = 0.0
    for index, grade in enumerate(grades[:k], start=1):
        total += (2 ** grade - 1) / math.log2(index + 1)
    return total


def ndcg(ranked_grades: list[int], k: int) -> float:
    ideal = dcg(sorted(ranked_grades, reverse=True), k)
    return dcg(ranked_grades, k) / ideal if ideal else 0.0


def ranking_metrics(ranked_job_ids: list[str], relevance: dict[str, int], k: int) -> dict:
    top = ranked_job_ids[:k]
    hits = sum(1 for job_id in top if relevance[job_id] >= RELEVANT)
    total_relevant = sum(1 for grade in relevance.values() if grade >= RELEVANT)
    precision = hits / k
    recall = hits / total_relevant if total_relevant else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        f"P@{k}": precision,
        f"R@{k}": recall,
        f"F1@{k}": f1,
        f"NDCG@{k}": ndcg([relevance[j] for j in top], k),
    }


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


# ------------------------------------------------------------------- runners
def semantic_cache_control(enable: bool):
    """Cache semantic similarity per document pair while keeping results identical."""
    real = semantic.similarity
    cache: dict[tuple[str, str], float | None] = {}

    if not enable:
        return real

    def cached(a: str, b: str):
        key = (a, b)
        if key not in cache:
            cache[key] = real(a, b)
        return cache[key]

    return cached


def score_system(weights, resume_profiles, job_profiles, jtexts) -> dict[str, dict[str, float]]:
    base = get_settings()
    patched = replace(
        base,
        match_weight_required=weights[0],
        match_weight_preferred=weights[1],
        match_weight_semantic=weights[2],
    )
    scoring.get_settings = lambda: patched
    try:
        results: dict[str, dict[str, float]] = {}
        for resume_id, profile in resume_profiles.items():
            text = profile_text[resume_id]
            row: dict[str, float] = {}
            for job_id, job_profile in job_profiles.items():
                outcome = scoring.score_pair(
                    profile, job_profile, resume_text=text, job_text=jtexts[job_id]
                )
                row[job_id] = outcome.final_score
            results[resume_id] = row
        return results
    finally:
        scoring.get_settings = get_settings


def tfidf_baseline(resume_texts, job_texts) -> dict[str, dict[str, float]]:
    """Keyword baseline: TF-IDF cosine similarity of the raw documents."""
    resume_ids = list(resume_texts)
    job_ids = list(job_texts)
    corpus = [resume_texts[r] for r in resume_ids] + [job_texts[j] for j in job_ids]
    vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    matrix = vectorizer.fit_transform(corpus)
    resume_matrix = matrix[: len(resume_ids)]
    job_matrix = matrix[len(resume_ids):]
    sims = cosine_similarity(resume_matrix, job_matrix)
    return {
        resume_id: {job_id: float(sims[i][j]) for j, job_id in enumerate(job_ids)}
        for i, resume_id in enumerate(resume_ids)
    }


def ranked(scores: dict[str, float], relevance: dict[str, int]) -> list[str]:
    return sorted(scores, key=lambda job_id: (-scores[job_id], int(relevance[job_id]) and 0 or 999, job_id))


# ------------------------------------------------------------------- support
def skill_extraction_eval(dataset: dict) -> tuple[list[dict], dict]:
    rows = []
    totals = {"tp": 0, "fp": 0, "fn": 0}
    taxonomy_totals = {"tp": 0, "fp": 0, "fn": 0}
    for record in dataset["resumes"]:
        rows.append(_extraction_row(record["id"], "resume", record["text"], record["gold_skills"], totals, taxonomy_totals))
    for record in dataset["jobs"]:
        rows.append(_extraction_row(record["id"], "job", record["description"], record["gold_skills"], totals, taxonomy_totals))

    def prf(counts):
        precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else 0.0
        recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {"precision": precision, "recall": recall, "f1": f1}

    return rows, {"all": prf(totals), "taxonomy_only": prf(taxonomy_totals)}


def _extraction_row(doc_id, kind, text, gold, totals, taxonomy_totals) -> dict:
    extracted = extract_skills(text)
    all_keys = {skill.key for skill in extracted}
    taxonomy_keys = {skill.key for skill in extracted if skill.source == "taxonomy"}
    gold_keys = {g.lower() for g in gold}

    tp_all = len(all_keys & gold_keys)
    fp_all = len(all_keys - gold_keys)
    fn_all = len(gold_keys - all_keys)
    totals["tp"] += tp_all
    totals["fp"] += fp_all
    totals["fn"] += fn_all

    tp_tax = len(taxonomy_keys & gold_keys)
    fp_tax = len(taxonomy_keys - gold_keys)
    fn_tax = len(gold_keys - taxonomy_keys)
    taxonomy_totals["tp"] += tp_tax
    taxonomy_totals["fp"] += fp_tax
    taxonomy_totals["fn"] += fn_tax

    def prf(tp, fp, fn):
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f = 2 * p * r / (p + r) if p + r else 0.0
        return p, r, f

    p_all, r_all, f_all = prf(tp_all, fp_all, fn_all)
    p_tax, r_tax, f_tax = prf(tp_tax, fp_tax, fn_tax)
    return {
        "doc": doc_id,
        "type": kind,
        "gold": len(gold_keys),
        "extracted": len(all_keys),
        "tp": tp_all,
        "fp": sorted(all_keys - gold_keys),
        "missing": sorted(gold_keys - all_keys),
        "precision_all": p_all,
        "recall_all": r_all,
        "f1_all": f_all,
        "precision_taxonomy": p_tax,
        "recall_taxonomy": r_tax,
        "f1_taxonomy": f_tax,
    }


def hardware_description() -> str:
    cpu = platform.processor() or platform.uname().processor or "unknown CPU"
    return f"{cpu} | {platform.system()} {platform.release()} | Python {platform.python_version()}"


# ---------------------------------------------------------------------- main
def main() -> None:
    dataset = load_dataset()
    global profile_text

    relevance = dataset["relevance"]
    resume_records = {r["id"]: r for r in dataset["resumes"]}
    job_records = {j["id"]: j for j in dataset["jobs"]}

    resume_profiles = {rid: build_resume_profile(resume_object(r)) for rid, r in resume_records.items()}
    job_profiles = {jid: build_job_profile(job_object(j)) for jid, j in job_records.items()}
    jtexts = {jid: job_text(profile) for jid, profile in job_profiles.items()}
    profile_text = {rid: r["text"] for rid, r in resume_records.items()}
    rtexts = {rid: r["text"] for rid, r in resume_records.items()}

    # warm the semantic model so its one-off load does not pollute ranking time
    ok, reason = semantic.availability()
    semantic_available = ok
    semantic.similarity = semantic_cache_control(True)

    # ---- ranking metrics
    per_system: dict[str, dict[str, dict]] = {}
    baseline_scores = tfidf_baseline(rtexts, jtexts)
    per_system["keyword_tfidf"] = {
        rid: {jid: round(score, 4) for jid, score in row.items()}
        for rid, row in baseline_scores.items()
    }
    for name, weights in SYSTEMS.items():
        if weights is None:
            continue
        per_system[name] = score_system(weights, resume_profiles, job_profiles, jtexts)

    metric_rows = []
    per_resume_ndcg = {}
    for name, scores in per_system.items():
        collected = {f"{m}@{k}": [] for k in KS for m in ("P", "R", "F1", "NDCG")}
        per_resume_ndcg[name] = []
        for rid, row in scores.items():
            order = sorted(row, key=lambda jid: (-row[jid], jid))
            for k in KS:
                values = ranking_metrics(order, relevance[rid], k)
                for key, value in values.items():
                    collected[key].append(value)
            per_resume_ndcg[name].append(ndcg([relevance[rid][j] for j in order[:10]], 10))
        metric_rows.append({
            "system": name,
            "label": LABELS[name],
            **{key: mean(values) for key, values in collected.items()},
        })

    # ---- significance (paired, per resume) on NDCG@10
    from scipy.stats import wilcoxon

    hybrid = per_resume_ndcg["hybrid_60_15_25"]
    baseline = per_resume_ndcg["keyword_tfidf"]
    diffs = [h - b for h, b in zip(hybrid, baseline)]
    try:
        test = wilcoxon(hybrid, baseline)
        p_value = float(test.pvalue)
    except Exception:
        p_value = float("nan")
    mean_gain = mean(diffs)

    # ---- skill extraction
    extraction_rows, extraction_summary = skill_extraction_eval(dataset)

    # ---- error analysis on the hybrid (top-5 misses)
    error_rows = []
    hybrid_scores = per_system["hybrid_60_15_25"]
    for rid, row in hybrid_scores.items():
        order = sorted(row, key=lambda jid: (-row[jid], jid))
        top5 = set(order[:5])
        gold_skills = {g.lower() for g in resume_records[rid]["gold_skills"]}
        for jid, grade in relevance[rid].items():
            if grade < RELEVANT or jid in top5:
                continue
            outcome = scoring.score_pair(
                resume_profiles[rid], job_profiles[jid],
                resume_text=rtexts[rid], job_text=jtexts[jid],
            )
            missing = [m.lower() for m in outcome.missing_required]
            synonym_hits = sorted({
                f"{m} ~ {s}" for m in missing for s in SYNONYMS.get(m, set()) if s in gold_skills
            })
            rank = order.index(jid) + 1
            if synonym_hits:
                cause = "missing synonym / vocabulary gap"
            elif (outcome.components["required"] or 0) >= 50:
                cause = "ranking & weighting (job found but ranked below top-5)"
            elif not any(str(s).lower() in job_records[jid]["description"].lower() for s in job_records[jid]["required_skills"]):
                cause = "noisy job record (required skills not supported by description)"
            else:
                cause = "genuine mismatch / label-engine disagreement"
            error_rows.append({
                "resume": rid,
                "job": jid,
                "job_title": job_records[jid]["title"],
                "grade": grade,
                "rank": rank,
                "score": round(outcome.final_score, 2),
                "required_component": outcome.components["required"],
                "preferred_component": outcome.components["preferred"],
                "semantic_component": outcome.components["semantic"],
                "missing_required": "; ".join(outcome.missing_required),
                "synonym_hits": "; ".join(synonym_hits),
                "cause": cause,
            })
    # false positives: grade <= 1 inside the hybrid top-5
    false_positives = []
    fp_rows = []
    for rid, row in hybrid_scores.items():
        order = sorted(row, key=lambda jid: (-row[jid], jid))
        for jid in order[:5]:
            if relevance[rid][jid] <= 1:
                false_positives.append((rid, jid, relevance[rid][jid], round(row[jid], 2)))
                outcome = scoring.score_pair(
                    resume_profiles[rid], job_profiles[jid],
                    resume_text=rtexts[rid], job_text=jtexts[jid],
                )
                fp_rows.append({
                    "resume": rid,
                    "job": jid,
                    "job_title": job_records[jid]["title"],
                    "grade": relevance[rid][jid],
                    "rank": order.index(jid) + 1,
                    "score": round(outcome.final_score, 2),
                    "required_component": outcome.components["required"],
                    "preferred_component": outcome.components["preferred"],
                    "semantic_component": outcome.components["semantic"],
                    "missing_required": "; ".join(outcome.missing_required),
                    "synonym_hits": "",
                    "cause": ("false positive: adjacent role, grade 1"
                              if relevance[rid][jid] == 1 else "false positive: grade 0"),
                })

    # ---- runtime (real, uncached semantic path)
    semantic.similarity = semantic_cache_control(False)
    semantic.reset()
    t0 = time.perf_counter()
    semantic.availability()
    model_load_s = time.perf_counter() - t0

    resume_times = []
    for _ in range(5):
        for record in resume_records.values():
            t0 = time.perf_counter()
            build_resume_profile(resume_object(record))
            resume_times.append((time.perf_counter() - t0) * 1000)

    job_profile_times = []
    for _ in range(3):
        for record in job_records.values():
            t0 = time.perf_counter()
            build_job_profile(job_object(record))
            job_profile_times.append((time.perf_counter() - t0) * 1000)

    scoring_times = []
    for rid, profile in resume_profiles.items():
        for jid, job_profile in job_profiles.items():
            t0 = time.perf_counter()
            scoring.score_pair(profile, job_profile, resume_text=rtexts[rid], job_text=jtexts[jid])
            scoring_times.append((time.perf_counter() - t0) * 1000)

    end_to_end = []
    for rid in list(resume_profiles)[:5]:
        t0 = time.perf_counter()
        for jid, job_profile in job_profiles.items():
            scoring.score_pair(resume_profiles[rid], job_profile, resume_text=rtexts[rid], job_text=jtexts[jid])
        end_to_end.append((time.perf_counter() - t0) * 1000)

    retrieval_times = []
    try:
        from sqlalchemy import text as sql_text
        from app.db.session import engine

        with engine.connect() as connection:
            for _ in range(20):
                t0 = time.perf_counter()
                connection.execute(sql_text("SELECT * FROM jobs ORDER BY id")).all()
                retrieval_times.append((time.perf_counter() - t0) * 1000)
    except Exception as exc:  # database not running
        retrieval_times = []
        db_error = f"{exc.__class__.__name__}: {exc}"
    else:
        db_error = None

    runtime = {
        "semantic_available": semantic_available,
        "semantic_reason": reason,
        "model_load_s": model_load_s,
        "resume_ms_mean": mean(resume_times),
        "resume_ms_p95": p95(resume_times),
        "job_profile_ms_mean": mean(job_profile_times),
        "job_profile_ms_p95": p95(job_profile_times),
        "score_ms_mean": mean(scoring_times),
        "score_ms_p95": p95(scoring_times),
        "score_pairs": len(scoring_times),
        "end_to_end_ms_mean": mean(end_to_end),
        "end_to_end_ms_p95": p95(end_to_end),
        "end_to_end_scope": f"one resume x {len(job_profiles)} jobs, warm model",
        "retrieval_ms_mean": mean(retrieval_times),
        "retrieval_ms_p95": p95(retrieval_times),
        "db_error": db_error,
        "hardware": hardware_description(),
        "cores": platform.os.cpu_count() if hasattr(platform, "os") else None,
    }

    # ---- write outputs
    _write_metrics(metric_rows)
    _write_extraction(extraction_rows)
    _write_errors(error_rows + fp_rows)
    _write_chart(metric_rows)
    _write_annotation_sheet(dataset)
    _write_report(
        dataset, metric_rows, per_resume_ndcg, mean_gain, p_value,
        extraction_summary, error_rows, false_positives, runtime,
    )

    # ---- console summary
    header = f"{'system':<34}" + "".join(f"{m+'@'+str(k):>10}" for k in KS for m in ("P", "R", "F1", "NDCG"))
    print(header)
    for row in metric_rows:
        line = f"{row['label']:<34}"
        for k in KS:
            for m in ("P", "R", "F1", "NDCG"):
                line += f"{row[f'{m}@{k}']:>10.3f}"
        print(line)
    print(f"\nhybrid - baseline NDCG@10: {mean_gain:+.3f} (Wilcoxon p={p_value:.4f}, n={len(diffs)})")
    print(f"skill extraction all: {extraction_summary['all']}")
    print(f"model load: {model_load_s:.1f}s | score mean {runtime['score_ms_mean']:.1f} ms | p95 {runtime['score_ms_p95']:.1f} ms")
    print(f"results written to {RESULTS}")
    for row in error_rows:
        print(f"  miss {row['resume']}/{row['job']} rank {row['rank']} score {row['score']} [{row['cause']}]")


def _write_metrics(rows) -> None:
    keys = ["P@5", "R@5", "F1@5", "NDCG@5", "P@10", "R@10", "F1@10", "NDCG@10"]
    with (RESULTS / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["system", "label"] + keys)
        writer.writeheader()
        writer.writerows(rows)


def _write_extraction(rows) -> None:
    with (RESULTS / "extraction.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("; ".join(v) if isinstance(v, list) else v) for k, v in row.items()})


def _write_errors(rows) -> None:
    rows = sorted(rows, key=lambda row: (row["resume"], row["rank"]))
    fields = ["resume", "job", "job_title", "grade", "rank", "score", "required_component",
              "preferred_component", "semantic_component", "missing_required", "synonym_hits", "cause"]
    with (RESULTS / "errors.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_annotation_sheet(dataset) -> None:
    rows = []
    for rid, grades in dataset["relevance"].items():
        for jid in grades:
            rows.append((rid, jid))
    random.Random(7).shuffle(rows)
    with (HERE / "annotation_sheet.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["pair_id", "resume_id", "job_id", "annotator_a", "annotator_b"])
        for index, (rid, jid) in enumerate(rows, start=1):
            writer.writerow([index, rid, jid, "", ""])


def _write_chart(rows) -> None:
    width, height = 900, 60 + 34 * len(rows)
    margin_left, bar_height = 300, 12
    lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="Segoe UI, Arial" font-size="12">']
    lines.append(f'<rect width="{width}" height="{height}" fill="#ffffff"/>')
    lines.append('<text x="20" y="24" font-size="15" font-weight="bold">NDCG@5 and NDCG@10 by system</text>')
    lines.append('<rect x="320" y="34" width="10" height="10" fill="#4e79a7"/><text x="336" y="43">NDCG@5</text>')
    lines.append('<rect x="410" y="34" width="10" height="10" fill="#f28e2b"/><text x="426" y="43">NDCG@10</text>')
    max_value = max(max(row["NDCG@5"], row["NDCG@10"]) for row in rows) or 1.0
    for index, row in enumerate(rows):
        y = 66 + index * 34
        lines.append(f'<text x="20" y="{y + 16}">{row["label"]}</text>')
        for offset, key, colour in ((0, "NDCG@5", "#4e79a7"), (14, "NDCG@10", "#f28e2b")):
            bar_width = int(480 * row[key] / max_value)
            lines.append(f'<rect x="300" y="{y + offset}" width="{bar_width}" height="{bar_height}" fill="{colour}"/>')
            lines.append(f'<text x="{306 + bar_width}" y="{y + offset + 10}" font-size="10">{row[key]:.3f}</text>')
    lines.append("</svg>")
    (RESULTS / "ndcg_bar.svg").write_text("\n".join(lines), encoding="utf-8")


def _write_report(dataset, metric_rows, per_resume_ndcg, mean_gain, p_value,
                  extraction_summary, error_rows, false_positives, runtime) -> None:
    keys = ["P@5", "R@5", "F1@5", "NDCG@5", "P@10", "R@10", "F1@10", "NDCG@10"]
    lines = ["# MatchWise offline evaluation", "",
             f"Benchmark: {len(dataset['resumes'])} resumes x {len(dataset['jobs'])} jobs = "
             f"{len(dataset['resumes']) * len(dataset['jobs'])} pairs; "
             f"relevant (grade >= 2) = {sum(1 for g in dataset['relevance'].values() for v in g.values() if v >= 2)}.",
             "", "## Table III - ranking comparison", "",
             "| System | " + " | ".join(keys) + " |",
             "|---|" + "---|" * len(keys)]
    for row in metric_rows:
        lines.append("| " + row["label"] + " | " + " | ".join(f"{row[k]:.3f}" for k in keys) + " |")
    lines += ["", f"Hybrid minus baseline NDCG@10: **{mean_gain:+.3f}** "
                  f"(paired Wilcoxon p = {p_value:.4f}, n = {len(per_resume_ndcg['hybrid_60_15_25'])}).", "",
              "## Skill extraction accuracy", "",
              "| Scope | Precision | Recall | F1 |", "|---|---:|---:|---:|"]
    for scope, values in extraction_summary.items():
        lines.append(f"| {scope} | {values['precision']:.3f} | {values['recall']:.3f} | {values['f1']:.3f} |")
    lines += ["", "## Runtime", "",
              f"- Hardware: {runtime['hardware']}",
              f"- Semantic model available: {runtime['semantic_available']} (load {runtime['model_load_s']:.1f} s)",
              f"- Resume profile extraction: mean {runtime['resume_ms_mean']:.1f} ms, p95 {runtime['resume_ms_p95']:.1f} ms",
              f"- Job profile extraction: mean {runtime['job_profile_ms_mean']:.1f} ms, p95 {runtime['job_profile_ms_p95']:.1f} ms",
              f"- Scoring one pair ({runtime['score_pairs']} pairs): mean {runtime['score_ms_mean']:.1f} ms, p95 {runtime['score_ms_p95']:.1f} ms",
              f"- End-to-end resume-to-all-jobs: mean {runtime['end_to_end_ms_mean']:.1f} ms, p95 {runtime['end_to_end_ms_p95']:.1f} ms",
              (f"- Job retrieval (SELECT all jobs, read-only): mean {runtime['retrieval_ms_mean']:.1f} ms, "
               f"p95 {runtime['retrieval_ms_p95']:.1f} ms" if not runtime["db_error"] else
               f"- Job retrieval: not measured ({runtime['db_error']})"),
              "", "## Error analysis (hybrid, relevant jobs outside top-5)", "",
              f"{len(error_rows)} misses; {len(false_positives)} irrelevant jobs inside top-5.", ""]
    if error_rows:
        lines += ["| Resume | Job | Grade | Rank | Score | Cause |", "|---|---|---:|---:|---:|---|"]
        for row in error_rows:
            lines.append(f"| {row['resume']} | {row['job']} {row['job_title']} | {row['grade']} | {row['rank']} | {row['score']} | {row['cause']} |")
    (RESULTS / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
