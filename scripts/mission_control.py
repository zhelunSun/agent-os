#!/usr/bin/env python3
"""Agent OS — Mission Control

Generate a one-page project health view.

Usage:
    python mission_control.py
    python mission_control.py --date 2026-06-01
    python mission_control.py --dry-run

Reads .agent-os/findings.jsonl and .agent-os/context-packs/ to build a health
summary. Writes to .agent-os/mission-control/YYYY-MM-DD.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_AGENT_OS_DIR = ".agent-os"
FINDINGS_FILE = "findings.jsonl"
CONTEXT_PACKS_DIR = "context-packs"
MISSION_CONTROL_DIR = "mission-control"
FLIGHT_RECORDER_DIR = "flight-recorder"
EXPERIENCE_LOG_FILE = "experience-log.jsonl"

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_agent_os_root(project_root: str | None = None) -> Path:
    start = Path(project_root) if project_root else Path.cwd()
    candidate = start / DEFAULT_AGENT_OS_DIR
    if candidate.is_dir():
        return candidate
    for parent in start.parents:
        candidate = parent / DEFAULT_AGENT_OS_DIR
        if candidate.is_dir():
            return candidate
    return start / DEFAULT_AGENT_OS_DIR


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _read_findings(agent_os_root: Path) -> list[dict[str, Any]]:
    path = agent_os_root / FINDINGS_FILE
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return items


def _read_experience_log(agent_os_root: Path) -> list[dict[str, Any]]:
    path = agent_os_root / EXPERIENCE_LOG_FILE
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return items


def _count_context_packs(agent_os_root: Path) -> int:
    d = agent_os_root / CONTEXT_PACKS_DIR
    if not d.exists():
        return 0
    return len(list(d.glob("*.md")))


def _count_flight_records(agent_os_root: Path) -> int:
    d = agent_os_root / FLIGHT_RECORDER_DIR
    if not d.exists():
        return 0
    return len(list(d.glob("*.jsonl")))


def _compute_health_score(findings: list[dict[str, Any]]) -> int:
    """Simple health score: 100 minus penalties for open findings."""
    score = 100
    for f in findings:
        status = f.get("status", "")
        if status in ("closed", "false_positive"):
            continue
        sev = f.get("severity", "P3")
        if sev == "P0":
            score -= 15
        elif sev == "P1":
            score -= 8
        elif sev == "P2":
            score -= 3
        elif sev == "P3":
            score -= 1
    return max(0, min(100, score))


def _age_days(date_str: str) -> int:
    """Rough age in days from a date string."""
    try:
        created = datetime.strptime(date_str[:10], "%Y-%m-%d")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return (now - created).days
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Core Logic
# ---------------------------------------------------------------------------

def generate_mission_control(agent_os_root: Path, date: str) -> str:
    """Build mission control content."""
    findings = _read_findings(agent_os_root)
    experience = _read_experience_log(agent_os_root)
    n_packs = _count_context_packs(agent_os_root)
    n_flights = _count_flight_records(agent_os_root)
    health = _compute_health_score(findings)

    # Separate findings by category
    open_findings = [f for f in findings if f.get("status") in ("open", "accepted")]
    open_p0p1 = sorted(
        [f for f in open_findings if f.get("severity") in ("P0", "P1")],
        key=lambda f: SEVERITY_ORDER.get(f.get("severity", "P3"), 99),
    )

    # Group by repo
    repos: dict[str, list[dict]] = {}
    for f in findings:
        repo = f.get("repo", "unspecified")
        if repo not in repos:
            repos[repo] = []
        repos[repo].append(f)

    # Stale items (open, >48h)
    stale = [f for f in open_findings if _age_days(f.get("date", "")) > 2]

    # Metrics
    closed_today = [f for f in findings if f.get("status") == "closed" and f.get("updated_at", "").startswith(date)]
    total_open = len(open_findings)
    avg_age = 0
    if open_findings:
        ages = [_age_days(f.get("date", "")) for f in open_findings]
        avg_age = sum(ages) // len(ages)

    # Build output
    lines = [
        f"# Mission Control — {date}",
        "",
        f"## Overall Health: {health}/100",
        "",
        "## Active Projects",
        "",
        "| Project | Open | P0 | P1 | Status |",
        "|---------|------|----|----|--------|",
    ]

    for repo, repo_findings in sorted(repos.items()):
        r_open = [f for f in repo_findings if f.get("status") in ("open", "accepted")]
        r_p0 = len([f for f in r_open if f.get("severity") == "P0"])
        r_p1 = len([f for f in r_open if f.get("severity") == "P1"])
        if r_p0 > 0:
            status = "crit"
        elif r_p1 > 0:
            status = "warn"
        elif r_open:
            status = "ok"
        else:
            status = "ok"
        lines.append(f"| {repo} | {len(r_open)} | {r_p0} | {r_p1} | {status} |")

    lines.append("")
    lines.append("## Open Findings (P0/P1)")
    lines.append("")

    if open_p0p1:
        lines.append("| ID | Title | Severity | Owner | Age |")
        lines.append("|----|-------|----------|-------|-----|")
        for f in open_p0p1:
            fid = f.get("id", "?")
            ftitle = f.get("title", "?")[:50]
            fsev = f.get("severity", "?")
            fowner = f.get("owner", "?")
            fage = _age_days(f.get("date", ""))
            lines.append(f"| {fid} | {ftitle} | {fsev} | {fowner} | {fage}d |")
    else:
        lines.append("No open P0/P1 findings. System is healthy.")

    lines.append("")
    lines.append("## Stale Items (>48h without update)")
    lines.append("")

    if stale:
        for f in stale:
            fid = f.get("id", "?")
            ftitle = f.get("title", "?")
            fage = _age_days(f.get("date", ""))
            lines.append(f"- {fid}: {ftitle} ({fage}d old)")
    else:
        lines.append("- No stale items")

    lines.append("")
    lines.append("## Human Decisions Needed")
    lines.append("")
    human_owned = [f for f in open_p0p1 if f.get("owner", "").lower() not in ("", "primary-agent", "audit-agent")]
    if human_owned:
        for f in human_owned[:3]:
            fid = f.get("id", "?")
            ftitle = f.get("title", "?")
            fnext = f.get("next_action", "No action specified")
            lines.append(f"### {fid}: {ftitle}")
            lines.append(f"- **Next action**: {fnext}")
            lines.append("")
    else:
        lines.append("- No decisions needed from the human at this time.")

    lines.append("")
    lines.append("## Agent Activity Summary")
    lines.append("")
    lines.append(f"- Context packs available: {n_packs}")
    lines.append(f"- Flight records available: {n_flights}")
    lines.append(f"- Experience log entries: {len(experience)}")

    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Open P0 | {len([f for f in open_findings if f.get('severity')=='P0'])} |")
    lines.append(f"| Open P1 | {len([f for f in open_findings if f.get('severity')=='P1'])} |")
    lines.append(f"| Total open | {total_open} |")
    lines.append(f"| Avg finding age | {avg_age}d |")
    lines.append(f"| Closed today | {len(closed_today)} |")
    lines.append(f"| Health score | {health}/100 |")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mission_control",
        description="Generate a one-page project health view",
    )
    parser.add_argument("--project-root", default=None,
                        help="Project root directory (default: cwd)")
    parser.add_argument("--date", default=None,
                        help="Date for the report (YYYY-MM-DD, default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print report to stdout without writing")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    agent_os_root = _find_agent_os_root(args.project_root)
    date = args.date or _today()
    filename = f"{date}.md"

    content = generate_mission_control(agent_os_root, date)

    if args.dry_run:
        print(f"[DRY RUN] Would write to: {agent_os_root / MISSION_CONTROL_DIR / filename}")
        print("---")
        print(content)
        return 0

    mc_dir = agent_os_root / MISSION_CONTROL_DIR
    mc_dir.mkdir(parents=True, exist_ok=True)
    out_path = mc_dir / filename
    out_path.write_text(content, encoding="utf-8")
    print(f"Mission control written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
