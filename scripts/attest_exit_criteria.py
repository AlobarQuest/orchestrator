#!/usr/bin/env python3
"""Attest the exit-criteria scorecard's route claims against live production.

Remediation item 0.5 (2026-07-12 remediation order): exit criteria #5/#7 were marked MET citing
routes production did not serve, because "merged" was recorded as "done" and nothing checked.
This is the check. It reads docs/operations/exit-criteria-claims.toml -- the machine-checkable
projection of every route-citing scorecard cell -- fetches production's OpenAPI document, and
fails when any claimed route is not served.

Usage:
    python scripts/attest_exit_criteria.py
    python scripts/attest_exit_criteria.py --openapi-url http://127.0.0.1:8000/openapi.json

Exit 0: every claimed route is served. Exit 1: at least one claim cites an unserved route.
Runs quarterly with the recovery drills, and after any production image swap.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import urllib.request
from pathlib import Path

DEFAULT_MANIFEST = (
    Path(__file__).resolve().parent.parent / "docs/operations/exit-criteria-claims.toml"
)
DEFAULT_OPENAPI_URL = "https://sds.alobar.net/openapi.json"


def load_claims(manifest_path: Path) -> list[dict]:
    with manifest_path.open("rb") as handle:
        manifest = tomllib.load(handle)
    claims = manifest.get("claim", [])
    if not claims:
        raise SystemExit(f"no [[claim]] entries found in {manifest_path}")
    return claims


def fetch_served_paths(openapi_url: str) -> set[str]:
    with urllib.request.urlopen(openapi_url, timeout=30) as response:
        document = json.load(response)
    return set(document["paths"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--openapi-url", default=DEFAULT_OPENAPI_URL)
    args = parser.parse_args(argv)

    claims = load_claims(args.manifest)
    served = fetch_served_paths(args.openapi_url)

    failures: list[str] = []
    for claim in claims:
        criterion = claim["criterion"]
        missing = [route for route in claim["routes"] if route not in served]
        if missing:
            failures.append(f"criterion #{criterion} ({claim['status']}): NOT SERVED: {missing}")
            print(f"FAIL criterion #{criterion}: {claim['description']} -- missing {missing}")
        else:
            route_count = len(claim["routes"])
            print(f"PASS criterion #{criterion}: {claim['description']} ({route_count} routes)")

    if failures:
        print(
            f"\nATTESTATION FAILED against {args.openapi_url}: {len(failures)} claim(s) cite "
            "routes production does not serve. Either production regressed or the scorecard "
            "claims more than production provides -- reconcile before trusting the scorecard.",
            file=sys.stderr,
        )
        return 1

    print(f"\nATTESTATION PASSED against {args.openapi_url}: {len(claims)} claims verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
