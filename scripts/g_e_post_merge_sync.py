#!/usr/bin/env python3
"""
G_E post-merge sync — переносит мерджнутые auto/prompt-* PR в prompts table.

Контракт:
  1. gh pr list --state merged --head 'auto/prompt-*' за последние 14 дней.
  2. Для каждого PR:
       a. Найти insight в Supabase где proposed_change.pr_url == pr_url.
       b. Пропустить если proposed_change.merged_synced_at уже есть.
       c. Прочесть новый body из main (git fetch + cat).
       d. SELECT prompts WHERE module=<file_stem> AND is_active=true → old_row.
       e. bump_patch(old.version) → new_version.
       f. UPDATE old_row.is_active=false, deactivated_at=now().
       g. INSERT new row: version=new, body=new, parent_version=old.version,
          rationale=insight.hypothesis, is_active=true, activated_at=now(),
          approved_by="github:<merger>".
       h. PATCH insight.proposed_change.merged_synced_at=now(),
          .prompts_id=new.id.

Idempotent: уже-синканный PR пропускается.

Usage:
    python g_e_post_merge_sync.py --once
    python g_e_post_merge_sync.py --once --dry-run

Env:
    SUPABASE_GENESIS_URL
    SUPABASE_GENESIS_SERVICE_KEY
    GENESIS_REPO_PATH=~/Obsidian_AI_Brain/Projects/genesis-content-os
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
import urllib.request
import urllib.parse


def _load_env_files():
    for p in [
        Path.home() / ".local" / "bin" / ".g_e_env",
        Path.home() / ".local" / "bin" / ".g_d_env",
    ]:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


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
REPO = Path(
    os.environ.get(
        "GENESIS_REPO_PATH",
        str(Path.home() / "Obsidian_AI_Brain" / "Projects" / "genesis-content-os"),
    )
)
LOOKBACK_DAYS = int(os.environ.get("MERGE_SYNC_LOOKBACK_DAYS", "14"))


# ============== Supabase ==============

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


def sb_insert(table: str, rows: list[dict]) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    data = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# ============== gh / git ==============

def gh_json(*args: str) -> Any:
    r = subprocess.run(["gh", *args], cwd=REPO, capture_output=True,
                       text=True, check=True)
    return json.loads(r.stdout)


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                       text=True, check=True)
    return r.stdout


def list_merged_auto_prs() -> list[dict]:
    """Recently merged PRs from auto/prompt-* branches."""
    rows = gh_json("pr", "list", "--state", "merged",
                   "--search", "head:auto/prompt-",
                   "--limit", "50",
                   "--json", "number,title,headRefName,mergedAt,mergeCommit,author,url")
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    out = []
    for r in rows:
        ts = r.get("mergedAt")
        if not ts:
            continue
        merged = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if merged < cutoff:
            continue
        if not r["headRefName"].startswith("auto/prompt-"):
            continue
        out.append(r)
    return out


def read_file_at_main(rel_path: str) -> str:
    git("fetch", "origin", "main")
    return git("show", f"origin/main:{rel_path}")


# ============== Helpers ==============

def bump_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return version + ".1"
    parts[2] = str(int(parts[2]) + 1)
    return ".".join(parts)


def find_changed_prompt_file(pr_number: int) -> str | None:
    """Return relative path of prompts/<file>.md changed in this PR."""
    files = gh_json("pr", "view", str(pr_number), "--json", "files")
    for f in files.get("files", []):
        p = f.get("path", "")
        if p.startswith("prompts/") and p.endswith(".md"):
            return p
    return None


def find_insight_by_pr_url(pr_url: str) -> dict | None:
    rows = sb_get("insights", {
        "select": "id,proposed_change,status,insight_text",
        "proposed_change->>pr_url": f"eq.{pr_url}",
        "limit": "1",
    })
    return rows[0] if rows else None


def find_active_prompt_row(module: str) -> dict | None:
    rows = sb_get("prompts", {
        "select": "id,version,body",
        "module": f"eq.{module}",
        "is_active": "eq.true",
        "limit": "1",
    })
    return rows[0] if rows else None


# ============== Main flow ==============

def sync_one(pr: dict, dry_run: bool) -> str:
    pr_url = pr["url"]
    pr_num = pr["number"]
    head = pr["headRefName"]
    merger = (pr.get("author") or {}).get("login", "github")

    insight = find_insight_by_pr_url(pr_url)
    if not insight:
        return f"  [skip] PR #{pr_num}: no matching insight in DB"
    pc = insight.get("proposed_change") or {}
    if pc.get("merged_synced_at"):
        return f"  [skip] PR #{pr_num}: already synced at {pc['merged_synced_at']}"

    rel_path = find_changed_prompt_file(pr_num)
    if not rel_path:
        return f"  [skip] PR #{pr_num}: no prompts/*.md file changed"
    module = Path(rel_path).stem  # 'scenario_v2.md' -> 'scenario_v2'

    old_row = find_active_prompt_row(module)
    if not old_row:
        return f"  [skip] PR #{pr_num}: no active prompts row for module={module}"

    new_body = read_file_at_main(rel_path)
    if new_body.strip() == old_row["body"].strip():
        return f"  [skip] PR #{pr_num}: body identical to current active"

    new_version = bump_patch(old_row["version"])
    rationale = pc.get("hypothesis") or insight.get("insight_text", "")[:200]
    now_iso = datetime.now(timezone.utc).isoformat()

    if dry_run:
        return (f"  [dry-run] PR #{pr_num}: would bump {module} "
                f"{old_row['version']} → {new_version}, "
                f"approved_by=github:{merger}")

    # 1. Deactivate old
    sb_patch(f"prompts?id=eq.{old_row['id']}", {
        "is_active": False,
        "deactivated_at": now_iso,
    })
    # 2. Insert new
    inserted = sb_insert("prompts", [{
        "module": module,
        "version": new_version,
        "body": new_body,
        "rationale": rationale,
        "is_active": True,
        "activated_at": now_iso,
        "approved_by": f"github:{merger}",
        "parent_version": old_row["version"],
    }])
    new_id = inserted[0]["id"]
    # 3. PATCH insight
    sb_patch(f"insights?id=eq.{insight['id']}", {
        "proposed_change": {
            **pc,
            "merged_synced_at": now_iso,
            "prompts_id": new_id,
            "merged_by": merger,
        },
    })
    return (f"  [sync] PR #{pr_num} → {module} {old_row['version']} → {new_version} "
            f"(approved_by github:{merger}, prompts_id={new_id[:8]})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SUPABASE_KEY:
        sys.exit("Missing SUPABASE_GENESIS_SERVICE_KEY")
    if not (REPO / ".git").exists():
        sys.exit(f"Genesis repo not found: {REPO}")

    prs = list_merged_auto_prs()
    print(f"=== G_E post-merge sync | {len(prs)} merged auto/prompt-* PR(s) "
          f"in last {LOOKBACK_DAYS}d ===")

    if not prs:
        print("  nothing to sync")
        return 0

    for pr in prs:
        msg = sync_one(pr, dry_run=args.dry_run)
        print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
