"""Replay Landing Part 3b's served-revision rule over real production data. Read-only.

Task 0 of `docs/superpowers/plans/2026-10-30-landing-p3b-revert-deploying.md`. For every
estate-lane Dependabot landing (merged by `app/alobar-sds-dispatch`) in the deploying
repositories, it asks what Task 2's served-revision rule would have judged, and prints that beside
change-manager's rollout verdict. The result is recorded in
`docs/superpowers/plans/2026-10-30-landing-p3b-signal-replay.md`.

THE RULE, as replayed, is the orchestrator's own: `revert_watch.served_verdict`, imported, never
copied (Task 0 replayed a first, row-counting draft; Task 2 replaced it with the time-based rule
and this script with it). It is asked at every instant a reading arrived within `WATCH_HOURS` of
the merge, and at every hour, as the store stood then: the applications are the member record's
`apps` for the repository, the readings those received by that instant, and the watcher's last
word the newest reading of any application by then (the store kept no heartbeat before
`observation_sources`, so the newest row stands in for it). The replay verdict is the first `bad`
or `clean` it answers, else `pending`. Ancestry is GitHub's compare API (`relation_of`), not the
row's own `state`, which is relative to the branch tip at the time.

THE READING TIME IS `received_at`, NEVER `observed_at`. The watcher sets `observed_at` to the
served commit's committer date (a fact-derived clock), so it says nothing about when production was
asked. `received_at` is the server's clock on the row's first write; the rows are content-addressed,
so an unchanged answer on a later pass replays the existing row and adds nothing.

EVERY CALL IS A GET. Secrets are read in-process and never printed or passed as an argument: the
Keychain item is read by `security` into this process, then handed to `bws` only in that one
subprocess's environment. The observer bearer comes from the narrow identity
(`BWS_ACCESS_TOKEN_SDS`), change-manager's read bearer from the broad one
(`BWS_ACCESS_TOKEN_VPS_BACKUP`); the two are never in the same environment. GitHub is read with the
operator's `gh`.

    .venv/bin/python scripts/replay_deploy_signal.py --since 2026-09-01 [--cache /tmp/replay.json]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orchestrator.services.landing.revert_watch import (
    BAD,
    FINE,
    PENDING,
    WATCH_HOURS,
    Production,
    ServedReading,
    relation_of,
    served_verdict,
)

REPOSITORIES = ("AlobarQuest/brain", "AlobarQuest/change-manager")
LANDER = "app/alobar-sds-dispatch"
SDS = "https://sds.alobar.net"
CHANGE_MANAGER = "https://change-mgr.alobar.net"
USER_AGENT = "landing-signal-replay/1 (+AlobarQuest/orchestrator)"
OBSERVER_SECRET = "f793576f-e9aa-4f9d-8089-b4a000b9e2d5"
OBSERVER_IDENTITY = "BWS_ACCESS_TOKEN_SDS"
OBSERVER_KEY_ID = "orchestrator-observer"
CM_READ_SECRET = "314f276d-55ca-4ddc-a24d-b4a3013508cd"
CM_IDENTITY = "BWS_ACCESS_TOKEN_VPS_BACKUP"
GITHUB_PAUSE_SECONDS = 0.5


# -- reading ---------------------------------------------------------------------------------


def _secret(uuid: str, identity: str) -> str:
    access = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude", "-a", identity, "-w"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    env = {k: v for k, v in os.environ.items() if k not in ("FORCE_COLOR", "CLICOLOR_FORCE")}
    env["BWS_ACCESS_TOKEN"] = access
    out = subprocess.run(
        ["bws", "secret", "get", uuid, "--output", "json", "--color", "no"],
        capture_output=True,
        text=True,
        env=env,
    )
    if out.returncode != 0:
        raise SystemExit(f"bws could not read {uuid} with {identity}")
    return str(json.loads(out.stdout)["value"])


def _get(url: str, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def _gh(*args: str) -> Any:
    time.sleep(GITHUB_PAUSE_SECONDS)
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def fetch(since: str) -> dict[str, Any]:
    observer = _secret(OBSERVER_SECRET, OBSERVER_IDENTITY)
    sds_headers = {"Authorization": f"Bearer {observer}", "X-Credential-Key-Id": OBSERVER_KEY_ID}
    members = _get(f"{SDS}/api/v1/members", sds_headers)
    query = urllib.parse.urlencode(
        {
            "source_system": "revision_watcher",
            "observation_type": "production_revision",
            "subject_type": "service",
        }
    )
    observations = _get(f"{SDS}/api/v1/observations?{query}", sds_headers)
    del observer, sds_headers

    reader = _secret(CM_READ_SECRET, CM_IDENTITY)
    cm_headers = {"Authorization": f"Bearer {reader}"}
    items = _get(f"{CHANGE_MANAGER}/api/items?source=deploy", cm_headers)
    rollouts: dict[str, Any] = {}
    for item in items:
        repository = str(item.get("target_repository") or "")
        if repository.lower() not in {r.lower() for r in REPOSITORIES}:
            continue
        time.sleep(0.2)
        body = _get(f"{CHANGE_MANAGER}/api/items/{int(item['id'])}/deploy-observations", cm_headers)
        rollouts[f"{repository.lower()}#{item.get('pull_request_number')}"] = {
            "item_id": item["id"],
            "status": item.get("status"),
            "current": body.get("current"),
        }
    del reader, cm_headers

    landings = []
    for repository in REPOSITORIES:
        search = f"merged:>={since} author:app/dependabot"
        fields = "number,mergedAt,mergedBy,mergeCommit,title"
        prs = _gh(
            "pr",
            "list",
            "-R",
            repository,
            "--state",
            "merged",
            "--limit",
            "300",
            "--search",
            search,
            "--json",
            fields,
        )
        for pr in prs:
            if (pr.get("mergedBy") or {}).get("login") != LANDER:
                continue
            landings.append(
                {
                    "repository": repository,
                    "number": pr["number"],
                    "merged_at": pr["mergedAt"],
                    "merge_sha": pr["mergeCommit"]["oid"],
                    "title": pr["title"],
                }
            )
    return {
        "fetched_at": datetime.now(UTC).isoformat(),
        "members": members,
        "observations": observations,
        "rollouts": rollouts,
        "landings": landings,
        "compares": {},
    }


# -- judging ---------------------------------------------------------------------------------


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compare(cache: dict[str, Any], repository: str, base: str, served: str) -> str:
    """GitHub's compare status of the served commit against `base`."""
    key = f"{repository}:{base}...{served}"
    if key not in cache["compares"]:
        body = _gh("api", f"repos/{repository}/compare/{base}...{served}", "--jq", "{status}")
        cache["compares"][key] = body["status"]
    return str(cache["compares"][key])


def relation(cache: dict[str, Any], repository: str, merge_sha: str, served: str) -> str:
    """Where the served commit stands relative to the merge commit."""
    return relation_of(compare(cache, repository, merge_sha, served))


def parent_of(cache: dict[str, Any], repository: str, merge_sha: str) -> str:
    """The squash merge's single parent: the default branch before the bump."""
    parents = cache.setdefault("parents", {})
    key = f"{repository}:{merge_sha}"
    if key not in parents:
        parents[key] = _gh(
            "api", f"repos/{repository}/commits/{merge_sha}", "--jq", "{p: .parents}"
        )["p"][0]["sha"]
    return str(parents[key])


def apps_of(members: dict[str, Any], repository: str) -> list[str]:
    for member in members["members"]:
        if str(member["repository"]).lower() == repository.lower():
            return [str(app["name"]) for app in member["apps"]]
    return []


def _readings(cache: dict[str, Any], apps: list[str]) -> tuple[ServedReading, ...]:
    return tuple(
        ServedReading(
            application=o["subject_reference"],
            serving=(o.get("facts") or {}).get("serving") or None,
            received_at=_ts(o["received_at"]),
        )
        for o in cache["observations"]
        if o["subject_reference"] in apps
    )


def judge(cache: dict[str, Any], landing: dict[str, Any]) -> dict[str, Any]:
    """The orchestrator's served rule, asked at each instant the store changed and every hour."""
    repository = landing["repository"]
    merged = _ts(landing["merged_at"])
    end = merged + timedelta(hours=WATCH_HOURS)
    apps = apps_of(cache["members"], repository)
    every = _readings(cache, apps)
    arrivals = sorted(_ts(o["received_at"]) for o in cache["observations"])
    instants = sorted(
        {at for at in arrivals if merged <= at <= end}
        | {merged + timedelta(hours=hour) for hour in range(1, WATCH_HOURS + 1)}
    )
    verdict, decided_at, detail = PENDING, None, ""
    for now in instants:
        seen = [at for at in arrivals if at <= now]
        production = Production(
            applications=tuple(apps),
            readings=tuple(r for r in every if r.received_at <= now),
            heard={app: seen[-1] for app in apps} if seen else {},
            landed_at=merged,
            now=now,
        )
        answer = served_verdict(
            production,
            lambda served: relation(cache, repository, landing["merge_sha"], served),
            lambda served: (
                compare(
                    cache, repository, parent_of(cache, repository, landing["merge_sha"]), served
                )
                == "behind"
            ),
        )
        if answer.kind != PENDING:
            verdict, decided_at, detail = answer.kind, now.isoformat(), answer.detail
            break
    per_app = {
        app: [
            {
                "received_at": r.received_at.isoformat(),
                "serving": (r.serving or "")[:10],
                "relation": relation(cache, repository, landing["merge_sha"], r.serving),
            }
            for r in sorted(every, key=lambda r: r.received_at)
            if r.application == app and r.serving and merged <= r.received_at <= end
        ]
        for app in apps
    }
    return {
        "apps": apps,
        "readings": per_app,
        "verdict": verdict,
        "decided_at": decided_at,
        "detail": detail,
    }


def all_behind_hours(judged: dict[str, Any], landing: dict[str, Any]) -> float:
    """Hours, within the window, that EVERY application's latest counted reading was not fine.

    The rule above counts rows; this counts time. Rows are content-addressed, so an application
    stuck on one wrong commit yields one row however many hourly passes repeat it, and the rule
    above can only see it as `pending`. Here a row stands until the application's next row (or the
    window's end), which is what the store actually says. A gap in the watcher's passes (it runs on
    the operator's machine) also lets a row stand, so a long figure is a lead, not a verdict.
    """
    end = _ts(landing["merged_at"]) + timedelta(hours=WATCH_HOURS)
    readings = judged["readings"]
    if not judged["apps"] or any(not readings[a] for a in judged["apps"]):
        return 0.0
    times = sorted({_ts(r["received_at"]) for a in judged["apps"] for r in readings[a]})
    total = 0.0
    for at, until in zip(times, [*times[1:], end], strict=True):
        standing = [[r for r in readings[a] if _ts(r["received_at"]) <= at] for a in judged["apps"]]
        if all(rows and rows[-1]["relation"] != FINE for rows in standing):
            total += (until - at).total_seconds() / 3600
    return round(total, 1)


def later_served(cache: dict[str, Any], landing: dict[str, Any], app: str) -> str | None:
    """The first reading after the window that serves the merge commit or a descendant."""
    end = _ts(landing["merged_at"]) + timedelta(hours=WATCH_HOURS)
    rows = sorted(
        (
            o
            for o in cache["observations"]
            if o["subject_reference"] == app
            and _ts(o["received_at"]) > end
            and (o.get("facts") or {}).get("serving")
        ),
        key=lambda o: o["received_at"],
    )
    for o in rows:
        served = o["facts"]["serving"]
        if relation(cache, landing["repository"], landing["merge_sha"], served) == FINE:
            return f"{o['received_at']} {served[:10]}"
    return None


def replay(cache: dict[str, Any], since: str) -> list[dict[str, Any]]:
    results = []
    for landing in sorted(cache["landings"], key=lambda la: (la["repository"], la["merged_at"])):
        if landing["merged_at"] < since:
            continue
        judged = judge(cache, landing)
        rollout = cache["rollouts"].get(f"{landing['repository'].lower()}#{landing['number']}")
        current = (rollout or {}).get("current") or {}
        row = {
            **landing,
            **judged,
            "rollout": current.get("verdict") if isinstance(current, dict) else current,
            "all_behind_hours": all_behind_hours(judged, landing),
        }
        if judged["verdict"] == BAD:
            row["later_served"] = {app: later_served(cache, landing, app) for app in judged["apps"]}
        results.append(row)
    return results


def show(results: list[dict[str, Any]], observations: list[dict[str, Any]]) -> None:
    earliest = min((o["received_at"] for o in observations), default=None)
    print(f"production_revision rows: {len(observations)}, earliest received: {earliest}")
    for r in results:
        counts = ",".join(str(len(r["readings"][a])) for a in r["apps"])
        print(
            f"{r['repository'].split('/')[1]:<15} #{r['number']:<4} {r['merge_sha'][:10]} "
            f"{r['merged_at']} rollout={r['rollout']!s:<10} readings=[{counts}] -> {r['verdict']}"
            f" all-behind={r['all_behind_hours']}h"
        )
        if r["verdict"] == BAD:
            print(f"    at {r['decided_at']}: {r['detail']}")
            for app in r["apps"]:
                print(f"    {app}: {r['readings'][app][-2:]} later fine: {r['later_served'][app]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the served-revision rule.")
    parser.add_argument("--since", default="2026-09-01")
    parser.add_argument("--cache", type=Path, help="reuse (or write) the fetched data here")
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args()

    if args.cache and args.cache.exists():
        cache = json.loads(args.cache.read_text())
    else:
        cache = fetch(args.since)
    results = replay(cache, args.since)
    if args.cache:
        args.cache.write_text(json.dumps(cache, indent=1))
    if args.json:
        json.dump({"results": results}, sys.stdout, indent=1)
    else:
        show(results, cache["observations"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
