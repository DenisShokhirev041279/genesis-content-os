#!/usr/bin/env python3
"""
G_E auto-decision — превращает actionable insights в Pull Requests.

Контракт:
  1. SELECT insights WHERE status='proposed' AND confidence IN ('medium','high')
     AND proposed_change->>'suggestion' is not empty.
  2. Фильтр actionable: suggestion должен содержать конкретное изменение
     (числа/проценты или phrase "increase/reduce/set X to Y").
  3. Для каждого actionable insight:
       a. Прочесть текущий prompts/<file>.md из локальной genesis-content-os repo.
       b. GPT-4: переписать prompt с минимальным diff под suggestion.
       c. git checkout -b auto/prompt-{week}-{short_id}
       d. Закоммитить + push.
       e. gh pr create (title + body с insight context + diff explanation).
       f. UPDATE insights.status='applied', proposed_change.pr_url=...

Usage:
    python g_e_auto_decision.py --once                # обработать все актуальные
    python g_e_auto_decision.py --once --dry-run      # ничего не делать, только log
    python g_e_auto_decision.py --once --no-pr        # GPT зовём, PR не создаём
    python g_e_auto_decision.py --insight-id <uuid>   # обработать конкретный

Env (.env или env vars):
    SUPABASE_GENESIS_URL
    SUPABASE_GENESIS_SERVICE_KEY
    OPENAI_API_KEY
    GENESIS_REPO_PATH=~/Obsidian_AI_Brain/Projects/genesis-content-os

Зависит от: gh CLI (auth status), git CLI.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import urllib.request
import urllib.parse


# --- env load ---
def _load_env_files():
    candidates = [
        Path.home() / ".local" / "bin" / ".g_e_env",
        Path.home() / ".local" / "bin" / ".g_d_env",
        Path.home() / "Obsidian_AI_Brain" / "Projects" / "ContentMachine" / "receiver" / ".env",
    ]
    for p in candidates:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)


_load_env_files()

SUPABASE_URL = (
    os.environ.get("SUPABASE_GENESIS_URL")
    or "https://czzzdhzzvtewvhcrlryr.supabase.co"
)
SUPABASE_KEY = (
    os.environ.get("SUPABASE_GENESIS_SERVICE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or ""
)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
REPO = Path(
    os.environ.get(
        "GENESIS_REPO_PATH",
        str(Path.home() / "Obsidian_AI_Brain" / "Projects" / "genesis-content-os"),
    )
)
OPENAI_MODEL = os.environ.get("OPENAI_DECISION_MODEL", "gpt-4.1")
PROMPTS_DIR_RELATIVE = "prompts"


# ============== Supabase helpers ==============

def sb_get(path: str, params: dict | None = None) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=".=():,*&")
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def sb_patch(path: str, body: dict) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PATCH", headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# ============== Insight filtering ==============

ACTIONABLE_PATTERNS = [
    re.compile(r"\b\d+\s*%"),                      # "55%", "from 28%"
    re.compile(r"\bfrom\s+\d+.*\bto\s+\d+", re.I), # "from 28 to 55"
    re.compile(r"\b(increase|reduce|decrease|bump|lower|raise)\b.*\b(bias|weight|share|to|by|frequency)", re.I),
    re.compile(r"\bset\s+\w+.*\bto\s+\d+", re.I),  # "set X to Y"
    re.compile(r"\bswap\s+\w+\s+(for|with)\s+\w+", re.I),
    re.compile(r"\badd\s+\w+\s+constraint", re.I),
    re.compile(r"\bremove\s+\w+\s+constraint", re.I),
]


def is_actionable(suggestion: str) -> tuple[bool, str]:
    if not suggestion or len(suggestion.strip()) < 20:
        return False, "too_short"
    for pat in ACTIONABLE_PATTERNS:
        if pat.search(suggestion):
            return True, f"matched:{pat.pattern[:40]}"
    return False, "no_concrete_change"


def detect_prompt_file(suggestion: str) -> str:
    """Decide which prompts/<file>.md is targeted. Default scenario_v2.md."""
    s = suggestion.lower()
    if "scenario_v3" in s or "v3.md" in s:
        return "scenario_v3.md"
    return "scenario_v2.md"


# ============== GPT rewrite ==============

REWRITE_SYSTEM = """You are an editor of an LLM prompt template that lives in a Git repo.
The prompt instructs another model how to compose a vertical short-video scenario.

You will receive:
  • the FULL current prompt text
  • a single concrete CHANGE_REQUEST derived from production analytics

Your job:
  • Apply the CHANGE_REQUEST with the MINIMAL possible diff.
  • Preserve markdown formatting, headings, examples, line order whenever possible.
  • If a numeric distribution must change, edit only the relevant lines.
  • Do not refactor, do not add commentary, do not rewrite intact paragraphs.
  • Do not add a header explaining the change — that belongs in the PR body.

Output: the FULL new prompt body only, no fences, no preamble, no trailing notes."""


def gpt_rewrite(current_body: str, suggestion: str) -> str:
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": REWRITE_SYSTEM},
            {"role": "user", "content": (
                f"CURRENT_PROMPT:\n```\n{current_body}\n```\n\n"
                f"CHANGE_REQUEST: {suggestion}\n\n"
                "Output the full updated prompt body only."
            )},
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode("utf-8"))
    raw = resp["choices"][0]["message"]["content"].strip()
    # Strip optional code fences if GPT ignored instructions
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)
    return raw.strip() + "\n"


# ============== Git / GH ==============

def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                          text=True, check=check)


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=REPO, capture_output=True,
                          text=True, check=check)


def ensure_clean_repo():
    r = git("status", "--porcelain", check=False)
    if r.stdout.strip():
        print("[warn] repo has uncommitted changes:", file=sys.stderr)
        print(r.stdout, file=sys.stderr)
        # Not fatal — we'll work on a fresh branch from main


def short_id(uid: str) -> str:
    return uid.replace("-", "")[:8]


def make_branch_and_pr(insight: dict, prompt_file: str, new_body: str, dry_run: bool):
    iid = insight["id"]
    week = insight.get("week_iso", "Wxx")
    branch = f"auto/prompt-{week}-{short_id(iid)}"
    prompt_path = REPO / PROMPTS_DIR_RELATIVE / prompt_file
    suggestion = (insight.get("proposed_change") or {}).get("suggestion", "")
    hypothesis = (insight.get("proposed_change") or {}).get("hypothesis", "")
    evidence = (insight.get("proposed_change") or {}).get("evidence", "")
    confidence = (insight.get("proposed_change") or {}).get("confidence", "")
    n = (insight.get("proposed_change") or {}).get("sample_size") or \
        (insight.get("proposed_change") or {}).get("n")

    if dry_run:
        # Diff preview
        from difflib import unified_diff
        old = prompt_path.read_text().splitlines(keepends=True)
        new = new_body.splitlines(keepends=True)
        diff = "".join(unified_diff(old, new,
                                     fromfile=f"a/{prompt_file}",
                                     tofile=f"b/{prompt_file}",
                                     n=2))
        print(f"\n=== DRY-RUN: branch {branch}, file {prompt_file} ===")
        print(diff or "(no diff)")
        return None

    # Save current branch to return to
    r = git("rev-parse", "--abbrev-ref", "HEAD")
    prev_branch = r.stdout.strip()
    try:
        # Make sure main is current — but DO NOT push
        git("checkout", "main", check=False)
        git("checkout", "-b", branch)
        prompt_path.write_text(new_body)
        git("add", str(prompt_path.relative_to(REPO)))
        commit_msg = (
            f"prompts: auto-apply insight {short_id(iid)} ({week})\n\n"
            f"{hypothesis[:200]}\n\n"
            f"Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
        )
        git("commit", "-m", commit_msg)
        # Push the auto-branch (and any baseline commits from main needed for diff)
        git("push", "-u", "origin", branch)
        # Also make sure main is up to date so PR has a clean base
        git("checkout", "main")
        git("push", "origin", "main", check=False)
        git("checkout", branch)

        pr_body = build_pr_body(insight, prompt_file, hypothesis, evidence,
                                 confidence, n, suggestion)
        title = f"auto/prompt {week} — {hypothesis[:60].rstrip()}"
        r = gh("pr", "create", "--title", title, "--body", pr_body,
               "--base", "main", "--head", branch)
        pr_url = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else None
        return pr_url
    finally:
        git("checkout", prev_branch or "main", check=False)


def build_pr_body(insight, prompt_file, hypothesis, evidence, confidence, n, suggestion) -> str:
    week = insight.get("week_iso", "")
    iid = insight["id"]
    return (
        f"## Source insight\n\n"
        f"**Week:** `{week}`\n"
        f"**Insight ID:** `{iid}`\n"
        f"**Confidence:** `{confidence}` · **n=** {n}\n\n"
        f"### Hypothesis\n> {hypothesis}\n\n"
        f"### Evidence\n```\n{evidence}\n```\n\n"
        f"### Suggested change\n> {suggestion}\n\n"
        f"---\n\n"
        f"## What changed\n\n"
        f"Auto-applied to `prompts/{prompt_file}` by `g_e_auto_decision.py`.\n"
        f"This is a **minimal diff** edit — only the section relevant to the\n"
        f"suggestion was touched. Review the `Files changed` tab.\n\n"
        f"## Review checklist\n\n"
        f"- [ ] Diff is minimal — no incidental refactors\n"
        f"- [ ] Numbers / percentages match the insight's evidence\n"
        f"- [ ] No regressions to other unrelated rules in the prompt\n"
        f"- [ ] Sample size (n=" + str(n) + ") is high enough to justify rollout\n\n"
        f"After merge: update `prompts` table in Supabase to mark new version\n"
        f"`is_active=true` and bump version of the previous row to inactive.\n"
    )


# ============== Main ==============

def fetch_proposed_insights() -> list[dict]:
    rows = sb_get("insights", {
        "select": "id,week_iso,insight_text,category,proposed_change,status,created_at",
        "status": "eq.proposed",
        "order": "created_at.desc",
        "limit": "50",
    })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-pr", action="store_true", help="GPT call but skip PR")
    ap.add_argument("--insight-id", type=str)
    args = ap.parse_args()

    if not SUPABASE_KEY:
        sys.exit("Missing SUPABASE_GENESIS_SERVICE_KEY")
    if not REPO.exists() or not (REPO / ".git").exists():
        sys.exit(f"Genesis repo not found: {REPO}")

    insights = fetch_proposed_insights()
    if args.insight_id:
        insights = [r for r in insights if r["id"] == args.insight_id]
    if not insights:
        print("=== G_E: no proposed insights to process ===")
        return 0

    print(f"=== G_E auto-decision | {len(insights)} proposed insight(s) ===")

    actionable = []
    skipped = []
    for ins in insights:
        pc = ins.get("proposed_change") or {}
        confidence = pc.get("confidence", "")
        suggestion = pc.get("suggestion") or ""
        if confidence not in ("medium", "high"):
            skipped.append((ins, f"confidence={confidence}"))
            continue
        ok, reason = is_actionable(suggestion)
        if not ok:
            skipped.append((ins, reason))
            continue
        actionable.append(ins)

    print(f"  actionable: {len(actionable)} | skipped: {len(skipped)}")
    for ins, reason in skipped:
        print(f"  - SKIP {ins['id'][:8]} ({reason}): "
              f"{(ins.get('proposed_change') or {}).get('suggestion','')[:80]}...")

    if not actionable:
        print("\n=== nothing to do — no actionable insights ===")
        return 0

    ensure_clean_repo()

    for ins in actionable:
        pc = ins.get("proposed_change") or {}
        suggestion = pc["suggestion"]
        prompt_file = detect_prompt_file(suggestion)
        prompt_path = REPO / PROMPTS_DIR_RELATIVE / prompt_file
        if not prompt_path.exists():
            print(f"  [skip] {prompt_path} not in repo")
            continue

        print(f"\n--- Insight {ins['id'][:8]} → {prompt_file} ---")
        print(f"  Suggestion: {suggestion[:200]}")

        current = prompt_path.read_text()

        if args.dry_run:
            print("  [dry-run] would call GPT, build branch, open PR")
            # Show what filter approved
            continue

        if not OPENAI_API_KEY:
            sys.exit("Missing OPENAI_API_KEY")

        print("  Calling OpenAI for prompt rewrite...")
        t0 = time.time()
        new_body = gpt_rewrite(current, suggestion)
        print(f"    done in {time.time() - t0:.1f}s, "
              f"{len(current)} chars → {len(new_body)} chars")

        if new_body.strip() == current.strip():
            print("  [skip] GPT returned identical body — no PR")
            continue

        if args.no_pr:
            print("  [no-pr] skipping branch/PR creation")
            continue

        pr_url = make_branch_and_pr(ins, prompt_file, new_body, dry_run=False)
        print(f"  PR: {pr_url}")

        sb_patch(f"insights?id=eq.{ins['id']}", {
            "status": "applied",
            "proposed_change": {**pc, "pr_url": pr_url,
                                "applied_at": datetime.now(timezone.utc).isoformat()},
        })

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
