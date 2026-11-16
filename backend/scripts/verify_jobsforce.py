"""Controlled live check of the JobsForce India provider.

The API is public, so **no credentials are needed**. This script performs
exactly ONE search request (``per_page=2``) and prints what the provider
returned, normalized the same way the backend normalizes it. Pass ``--detail``
to also fetch the full details of the first result (one extra request).

Usage (from ``backend/``)::

    ./.venv/Scripts/python.exe scripts/verify_jobsforce.py
    ./.venv/Scripts/python.exe scripts/verify_jobsforce.py --query "data analyst"
    ./.venv/Scripts/python.exe scripts/verify_jobsforce.py --detail

Output is ASCII-escaped so it prints safely in a Windows cp1252 console.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

# Allow running as ``python scripts/verify_jobsforce.py`` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.providers import ProviderError
from app.services.providers.jobsforce import JobsForceIndiaProvider


def _dump(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=True, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="python developer")
    parser.add_argument("--city", default=None)
    parser.add_argument("--page-size", type=int, default=2)
    parser.add_argument(
        "--detail",
        action="store_true",
        help="also fetch full details for the first result (one extra request)",
    )
    args = parser.parse_args()

    provider = JobsForceIndiaProvider()
    print(f"Provider: {provider.name!r} (credentials required: none)")
    print(f"Query: {args.query!r}  city: {args.city!r}  per_page: {args.page_size}")

    try:
        page = provider.search(
            query=args.query,
            city=args.city,
            page=1,
            page_size=min(max(args.page_size, 1), 20),
        )
    except ProviderError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1

    print(
        f"\nHTTP OK - {len(page.results)} result(s), total={page.total}, "
        f"has_more={page.has_more}, next_page={page.next_page}"
    )
    for note in page.notes:
        print(f"provider note: {note}")

    for result in page.results:
        _dump(asdict(result))

    if args.detail and page.results:
        first = page.results[0]
        print(f"\nFetching full details for {first.provider_job_id!r} ...")
        try:
            detail = provider.get_job(first.provider_job_id)
        except ProviderError as exc:
            print(f"Detail FAILED: {type(exc).__name__}: {exc}")
            return 1
        if detail is None:
            print("Detail returned 404 (listing unknown).")
            return 1
        print(f"Detail OK - has_details={detail.has_details}")
        _dump(asdict(detail))

    return 0


if __name__ == "__main__":
    sys.exit(main())
