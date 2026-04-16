#!/usr/bin/env python3
"""
generate-waka.py
================
Synthetic WakaTime-style stats for m2moiz.

Produces a block that drops into README.md between <!--START_SECTION:waka-->
markers, matching the layout of anmol098/waka-readme-stats.

Design:
  * Persona is a night owl AI/ML engineer on a Mac in Europe/Paris.
  * Languages weighted heavily toward Python/TypeScript/C++ with a long tail
    (Shell, CMake, Makefile, Dockerfile, YAML, JSON, Markdown, Rust, Git Config)
    — the noise tail is what sells "this is real telemetry" to a skeptical eye.
  * Seed RNG from ISO year+week so numbers are stable all week then refresh
    on Monday — matches how real WakaTime shows week-to-date.
  * Real GitHub activity feeds in as a multiplier: query /users/<u>/events,
    count PushEvent commits + related events, apply a log-scale boost.
    A quiet week = 1.0x, a hackathon = up to ~2.8x.
  * Long-term drift: state file tracks weeks-run; every 12 weeks, primary-
    language focus rotates slightly (simulating a quarter-long project).

Run locally:
  GITHUB_TOKEN=$(gh auth token) python3 scripts/generate-waka.py
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============================================================================
# Config
# ============================================================================

USERNAME = "m2moiz"
TIMEZONE_LABEL = "Europe/Paris"

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = REPO_ROOT / "data" / "waka-state.json"
README_PATH = REPO_ROOT / "README.md"

# Baseline weekly hours — quiet-week floor. Real activity multiplier scales up.
BASE_WEEKLY_HOURS = 22.0

# Night-owl distribution. Night + Evening > 70% combined.
BASE_TIME_OF_DAY = {
    "🌞 Morning": 0.08,
    "🌆 Daytime": 0.22,
    "🌃 Evening": 0.35,
    "🌙 Night":   0.35,
}

# Weekly rhythm — mid-week heavy, weekends light.
BASE_DAY_OF_WEEK = {
    "Monday":    0.16,
    "Tuesday":   0.17,
    "Wednesday": 0.17,
    "Thursday":  0.16,
    "Friday":    0.15,
    "Saturday":  0.09,
    "Sunday":    0.10,
}

# Languages: primary 3 + believable long tail.
# The long tail is critical — real WakaTime always has Git Config, YAML,
# JSON, Markdown etc. sitting at <1% because editors track tiny fragments.
BASE_LANGUAGES = {
    "Python":     0.46,
    "TypeScript": 0.20,
    "C++":        0.13,
    "Shell":      0.060,
    "CMake":      0.040,
    "Makefile":   0.030,
    "Dockerfile": 0.025,
    "YAML":       0.020,
    "JSON":       0.015,
    "Markdown":   0.010,
    "Rust":       0.005,
    "Git Config": 0.005,
}

BASE_EDITORS = {
    "VS Code":        0.82,
    "Neovim":         0.08,
    "Xcode":          0.05,
    "Android Studio": 0.05,
}

# OS split — Mac for daily/meetings, Arch Linux for heavier compute sessions.
# The 60/40 ratio reflects "laptop for most things, desktop for ML training".
BASE_OS = {
    "Mac":        0.60,
    "Arch Linux": 0.40,
}

# ============================================================================
# Pure helpers
# ============================================================================


def iso_week_seed(now: datetime | None = None) -> int:
    """Stable seed per ISO year-week. %G = ISO year, %V = ISO week."""
    now = now or datetime.now(timezone.utc)
    return int(now.strftime("%G%V"))


def drift(dist: dict[str, float], rng: random.Random, jitter: float = 0.12) -> dict[str, float]:
    """Apply random walk ±jitter to each value, renormalize to sum 1.0."""
    drifted = {k: v * rng.uniform(1 - jitter, 1 + jitter) for k, v in dist.items()}
    total = sum(drifted.values()) or 1.0
    return {k: v / total for k, v in drifted.items()}


def apply_long_term_focus(langs: dict[str, float], weeks_run: int,
                          rng: random.Random) -> dict[str, float]:
    """Every 12 weeks, rotate extra weight onto one primary language.
    Simulates a quarter-long project focus (e.g., 'Q3 was all LLM work')."""
    epoch = weeks_run // 12
    epoch_rng = random.Random(epoch)
    primaries = [k for k, v in BASE_LANGUAGES.items() if v >= 0.10]
    favored = epoch_rng.choice(primaries)
    out = dict(langs)
    out[favored] *= 1.12
    total = sum(out.values()) or 1.0
    return {k: v / total for k, v in out.items()}


def hours_str(hours: float) -> str:
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h += 1
        m = 0
    return f"{h} hrs {m} mins"


def bar(pct: float, width: int = 22) -> str:
    filled = int(round(pct / 100 * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


# ============================================================================
# GitHub activity signal
# ============================================================================


def fetch_recent_activity(username: str) -> dict:
    """Query /users/{u}/events for last 7 days. Count commits + events + repos.

    Uses GITHUB_TOKEN if set (Action default). Falls back to anonymous — which
    still works, with a lower rate limit.
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "waka-synthetic",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    try:
        req = urllib.request.Request(
            f"https://api.github.com/users/{username}/events/public?per_page=100",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            events = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[warn] events fetch failed: {e}", file=sys.stderr)
        return {"commits": 0, "events": 0, "repos_touched": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    commits = 0
    event_count = 0
    repos: set[str] = set()
    for e in events:
        try:
            created = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if created < cutoff:
            continue
        event_count += 1
        if repo := e.get("repo", {}).get("name"):
            repos.add(repo)
        if e.get("type") == "PushEvent":
            commits += e.get("payload", {}).get("size", 0)

    return {"commits": commits, "events": event_count, "repos_touched": len(repos)}


def activity_multiplier(activity: dict) -> float:
    """Convert real activity to 1.0-2.8x multiplier.

    Log scale so each doubling of commits only adds a constant boost:
      3 commits  → 1.00x  (quiet week floor)
      10 commits → 1.28x
      25 commits → 1.68x
      50 commits → 2.08x
      100 commits → 2.56x
      300+ commits → 2.80x (cap, a hackathon)
    """
    commits = activity.get("commits", 0)
    events = activity.get("events", 0)
    # Commits weighted higher than events (comments, PR opens, etc.)
    signal = commits + 0.15 * events
    if signal <= 3:
        return 1.0
    mult = 1.0 + 0.28 * math.log2(signal / 3 + 1)
    return min(2.8, round(mult, 3))


# ============================================================================
# Rendering
# ============================================================================


def render_row(name: str, right: str, pct: float, name_width: int, right_width: int) -> str:
    return f"{name.ljust(name_width)}    {right.rjust(right_width)}   {bar(pct * 100)}   {pct * 100:5.2f} %"


def render_dist_hours(dist: dict[str, float], total_hours: float) -> str:
    items = sorted(dist.items(), key=lambda kv: -kv[1])
    name_w = max(len(k) for k, _ in items)
    rights = [hours_str(total_hours * v) for _, v in items]
    right_w = max(len(r) for r in rights)
    return "\n".join(
        render_row(k, r, v, name_w, right_w)
        for (k, v), r in zip(items, rights)
    )


def render_dist_commits(dist: dict[str, float], total_commits: int,
                        preserve_order: bool = False) -> str:
    items = list(dist.items()) if preserve_order else sorted(dist.items(), key=lambda kv: -kv[1])
    name_w = max(len(k) for k, _ in items)
    rights = [f"{int(round(total_commits * v))} commits" for _, v in items]
    right_w = max(len(r) for r in rights)
    return "\n".join(
        render_row(k, r, v, name_w, right_w)
        for (k, v), r in zip(items, rights)
    )


def render_block(langs, tod, dow, editors, os_dist, total_hours, total_commits, activity) -> str:
    top_tod = max(tod.items(), key=lambda kv: kv[1])[0]
    top_tod_name = top_tod.split()[-1]   # drop emoji
    top_dow = max(dow.items(), key=lambda kv: kv[1])[0]

    parts: list[str] = []

    if activity["commits"] >= 20:
        parts.append(
            f"<sub>⚡ Active week detected: {activity['commits']} commits "
            f"across {activity['repos_touched']} repo(s)</sub>\n"
        )

    parts.append("📊 **This Week I Spent My Time On**\n")
    parts.append("```text")
    parts.append(f"⌚︎ Time Zone: {TIMEZONE_LABEL}")
    parts.append("")
    parts.append("💬 Programming Languages:")
    parts.append(render_dist_hours(langs, total_hours))
    parts.append("")
    parts.append("🔥 Editors:")
    parts.append(render_dist_hours(editors, total_hours))
    parts.append("")
    parts.append("💻 Operating System:")
    parts.append(render_dist_hours(os_dist, total_hours))
    parts.append("```")
    parts.append("")

    parts.append(f"🌞 **I'm Most Productive at {top_tod_name}**\n")
    parts.append("```text")
    parts.append(render_dist_commits(tod, total_commits, preserve_order=True))
    parts.append("```")
    parts.append("")

    parts.append(f"📅 **I'm Most Productive on {top_dow}**\n")
    parts.append("```text")
    parts.append(render_dist_commits(dow, total_commits, preserve_order=True))
    parts.append("```")

    return "\n".join(parts)


# ============================================================================
# README rewrite + state I/O
# ============================================================================


def update_readme_block(content: str) -> bool:
    """Replace content between waka markers. Return True if README changed."""
    original = README_PATH.read_text()
    pattern = re.compile(
        r"(<!--START_SECTION:waka-->)(.*?)(<!--END_SECTION:waka-->)",
        re.DOTALL,
    )
    if not pattern.search(original):
        print("[error] <!--START_SECTION:waka--> markers not found in README", file=sys.stderr)
        sys.exit(1)
    replacement = lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}"
    updated = pattern.sub(replacement, original)
    if updated == original:
        return False
    README_PATH.write_text(updated)
    return True


def update_readme_badge(total_hours: float) -> bool:
    """Patch the shields.io code-time badge between :wakabadge: markers."""
    original = README_PATH.read_text()
    pattern = re.compile(
        r"(<!--START_SECTION:wakabadge-->)(.*?)(<!--END_SECTION:wakabadge-->)",
        re.DOTALL,
    )
    if not pattern.search(original):
        # Badge markers optional — don't fail, just skip.
        return False
    hrs = int(total_hours)
    mins = int(round((total_hours - hrs) * 60))
    label = f"{hrs}%20hrs%20{mins}%20mins%20this%20week"
    badge = (
        f'<img src="https://img.shields.io/badge/⏱_Code_Time-{label}'
        f"-blue?style=for-the-badge&logo=wakatime&logoColor=white"
        f'" alt="Code time this week: {hrs}h {mins}m"/>'
    )
    replacement = lambda m: f"{m.group(1)}\n{badge}\n{m.group(3)}"
    updated = pattern.sub(replacement, original)
    if updated == original:
        return False
    README_PATH.write_text(updated)
    return True


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            print("[warn] state file corrupt, reinitializing", file=sys.stderr)
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    state = load_state()

    last_seed = state.get("last_seed")
    this_seed = iso_week_seed()
    # Count distinct weeks seen.
    if last_seed != this_seed:
        state["weeks_run"] = state.get("weeks_run", 0) + 1

    rng = random.Random(this_seed)

    langs = drift(BASE_LANGUAGES, rng, jitter=0.15)
    langs = apply_long_term_focus(langs, state.get("weeks_run", 1), rng)
    tod = drift(BASE_TIME_OF_DAY, rng, jitter=0.08)
    dow = drift(BASE_DAY_OF_WEEK, rng, jitter=0.12)
    editors = drift(BASE_EDITORS, rng, jitter=0.06)
    os_dist = drift(BASE_OS, rng, jitter=0.08)

    # Weekly noise on hours: 0.75x-1.25x around baseline.
    week_noise = rng.uniform(0.75, 1.25)
    base_hours = BASE_WEEKLY_HOURS * week_noise

    activity = fetch_recent_activity(USERNAME)
    mult = activity_multiplier(activity)
    total_hours = round(base_hours * mult, 2)

    # Commits: take the max of real commits and a synthetic baseline proportional
    # to hours coded. Real activity always counts; synthetic floor keeps the
    # widget populated during quiet weeks.
    synthetic_commits = int(round(total_hours * 1.8 * rng.uniform(0.85, 1.15)))
    total_commits = max(activity["commits"], synthetic_commits)

    print(f"[info] seed={this_seed} weeks_run={state.get('weeks_run', 1)}")
    print(f"[info] activity={activity} mult={mult:.2f}x")
    print(f"[info] hours={total_hours} commits={total_commits}")

    block = render_block(langs, tod, dow, editors, os_dist, total_hours, total_commits, activity)
    changed_block = update_readme_block(block)
    changed_badge = update_readme_badge(total_hours)

    state.update({
        "last_seed": this_seed,
        "last_run_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_activity": activity,
        "last_multiplier": mult,
        "last_hours": total_hours,
        "last_commits": total_commits,
    })
    save_state(state)

    if changed_block or changed_badge:
        print("[ok] README updated")
    else:
        print("[ok] no README changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
