#!/usr/bin/env python3
"""
G_D weekly analyzer — превращает накопленные metrics_snapshots в insights.

Контракт (см. docs/MODULE_D_DESIGN.md):
  1. SELECT metrics_snapshots за 7 дней (или --week YYYY-Www)
  2. JOIN yt_published на video_id → slug
  3. JOIN topics через video_slug → topic_category, hype_score
  4. Обогатить visual_types из локальных runs/<slug>/scenario.json
  5. Aggregate last value по metric (view/like/comment/favorite) на video
  6. GPT-4 → JSON insights (hypothesis, evidence, confidence, n, suggestion)
  7. INSERT в insights table (unless --no-insert)

Usage:
    python g_d_analyzer.py --once                 # последние 7 дней
    python g_d_analyzer.py --week 2026-W20        # конкретная неделя
    python g_d_analyzer.py --once --dry-run       # печать payload, без OpenAI
    python g_d_analyzer.py --once --no-insert     # OpenAI зовём, но не пишем

Env (.env или env vars):
    SUPABASE_GENESIS_URL
    SUPABASE_GENESIS_SERVICE_KEY    (или SUPABASE_SERVICE_KEY)
    OPENAI_API_KEY
    GENESIS_RUNS_DIR                (default: ~/Obsidian_AI_Brain/Projects/ContentMachine/runs)

Зависимости: только stdlib (urllib).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import urllib.request
import urllib.error
import urllib.parse


# --- env load (поддерживаем несколько источников) ---
def _load_env_files():
    candidates = [
        Path.home() / "Obsidian_AI_Brain" / "Projects" / "ContentMachine" / "receiver" / ".env",
        Path.home() / "Obsidian_AI_Brain" / "Projects" / "genesis-content-os" / ".env",
        Path.cwd() / ".env",
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
    or os.environ.get("SUPABASE_URL")
    or "https://czzzdhzzvtewvhcrlryr.supabase.co"
)
SUPABASE_KEY = (
    os.environ.get("SUPABASE_GENESIS_SERVICE_KEY")
    or os.environ.get("SUPABASE_SERVICE_KEY")
    or ""
)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
RUNS_DIR = Path(
    os.environ.get(
        "GENESIS_RUNS_DIR",
        str(Path.home() / "Obsidian_AI_Brain" / "Projects" / "ContentMachine" / "runs"),
    )
)
OPENAI_MODEL = os.environ.get("OPENAI_ANALYZER_MODEL", "gpt-4.1")


# ============== Supabase helpers ==============

def sb_get(path: str, params: dict | None = None) -> Any:
    """GET on Supabase REST. ``path`` like 'metrics_snapshots'."""
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=".=():,*")
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
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


# ============== Data loading ==============

def fetch_metrics(since_iso: str, until_iso: str) -> list[dict]:
    """All youtube metric snapshots in [since, until)."""
    rows = []
    page = 0
    page_size = 1000
    while True:
        chunk = sb_get("metrics_snapshots", {
            "select": "captured_at,metric_name,metric_value,platform,metadata,post_id",
            "captured_at": f"gte.{since_iso}",
            "and": f"(captured_at.lt.{until_iso})",
            "platform": "eq.youtube",
            "order": "captured_at.desc",
            "limit": str(page_size),
            "offset": str(page * page_size),
        })
        rows.extend(chunk)
        if len(chunk) < page_size:
            break
        page += 1
        if page > 20:
            print("[warn] >20k metric rows fetched, capping", file=sys.stderr)
            break
    return rows


def fetch_yt_published() -> dict[str, dict]:
    """Map video_id → {slug, ...}."""
    rows = sb_get("yt_published", {"select": "video_id,slug,published_at,title"})
    return {r["video_id"]: r for r in rows if r.get("video_id")}


def fetch_topics() -> dict[str, dict]:
    """Map video_slug → {topic_category, hype_score, title_en}."""
    rows = sb_get("topics", {
        "select": "video_slug,topic_category,hype_score,title_en,title_ru",
        "video_slug": "not.is.null",
    })
    return {r["video_slug"]: r for r in rows if r.get("video_slug")}


# ============== scenario.json enrichment ==============

def find_scenario_for_slug(slug: str) -> Path | None:
    """Find runs/*/scenario.json that matches the slug.

    Slug examples in scenario.json: 'R8_cursor_vs_claude', 'R12_permission_hardened'.
    Folder names: '20260422_1412_R8_cursor_vs_claude'.
    We match on suffix.
    """
    if not slug or not RUNS_DIR.exists():
        return None
    # Try exact suffix match first
    candidates = list(RUNS_DIR.glob(f"*_{slug}/scenario.json"))
    if not candidates:
        candidates = list(RUNS_DIR.glob(f"*{slug}*/scenario.json"))
    if not candidates:
        return None
    # Pick the newest (last run)
    return max(candidates, key=lambda p: p.stat().st_mtime)


def extract_visual_types(scenario_path: Path) -> list[str]:
    try:
        sc = json.loads(scenario_path.read_text())
    except Exception:
        return []
    segs = sc.get("segments") or sc.get("scenes") or []
    out = []
    for s in segs:
        vt = (
            s.get("visual_type")
            or s.get("layout")
            or s.get("type")
            or (s.get("visual_params") or {}).get("layout")
        )
        if vt:
            out.append(str(vt))
    return out


# ============== Aggregation ==============

def hype_bucket(score: float | int | None) -> str:
    if score is None:
        return "unknown"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if s < 50:
        return "0-50"
    if s < 75:
        return "50-75"
    return "75-100"


def aggregate(metrics: list[dict], yt_map: dict, topic_map: dict) -> list[dict]:
    """Collapse per-video: last value per metric_name + scenario + topic data."""
    # video_id -> {metric: (captured_at, value)}
    per_video: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    for m in metrics:
        meta = m.get("metadata") or {}
        vid = meta.get("video_id") or meta.get("youtube_video_id")
        if not vid:
            continue
        name = m.get("metric_name")
        ts = m.get("captured_at") or ""
        val = m.get("metric_value")
        if val is None:
            continue
        prev = per_video[vid].get(name)
        if prev is None or ts > prev[0]:
            per_video[vid][name] = (ts, float(val))

    rows = []
    for vid, mset in per_video.items():
        yt = yt_map.get(vid, {})
        slug = yt.get("slug")
        topic = topic_map.get(slug, {}) if slug else {}
        scen = find_scenario_for_slug(slug) if slug else None
        visual_types = extract_visual_types(scen) if scen else []

        views = mset.get("view", (None, 0.0))[1]
        likes = mset.get("like", (None, 0.0))[1]
        comments = mset.get("comment", (None, 0.0))[1]
        favorites = mset.get("favorite", (None, 0.0))[1]

        like_ratio = (likes / views) if views > 0 else None
        engagement = ((likes + comments) / views) if views > 0 else None

        rows.append({
            "video_id": vid,
            "slug": slug,
            "title": yt.get("title") or topic.get("title_en") or topic.get("title_ru"),
            "topic_category": topic.get("topic_category"),
            "hype_score": topic.get("hype_score"),
            "hype_bucket": hype_bucket(topic.get("hype_score")),
            "visual_types": visual_types,
            "visual_types_count": dict(Counter(visual_types)),
            "views": int(views),
            "likes": int(likes),
            "comments": int(comments),
            "favorites": int(favorites),
            "like_to_view_ratio": round(like_ratio, 4) if like_ratio is not None else None,
            "engagement_rate": round(engagement, 4) if engagement is not None else None,
            "captured_at_last": max((t for t, _ in mset.values()), default=None),
        })
    rows.sort(key=lambda r: r["views"], reverse=True)
    return rows


def group_summary(rows: list[dict]) -> dict:
    """Sanity numbers for the analyst — sample sizes per group."""
    by_topic = Counter(r["topic_category"] or "unknown" for r in rows)
    by_hype = Counter(r["hype_bucket"] for r in rows)
    by_visual = Counter()
    for r in rows:
        for vt, n in (r["visual_types_count"] or {}).items():
            by_visual[vt] += n
    return {
        "total_videos": len(rows),
        "by_topic_category": dict(by_topic),
        "by_hype_bucket": dict(by_hype),
        "visual_type_total_segments": dict(by_visual),
    }


# ============== GPT call ==============

SYSTEM_PROMPT = """You are Genesis Content OS analyst. Find statistically meaningful patterns in YouTube Shorts performance data. Output ONLY valid JSON.

Rules:
- Use ONLY numbers from the provided data, never invent.
- confidence=low if n<5, medium if 5-9, high if n>=10.
- 1-3 insights max. If sample sizes are tiny, output 1 insight + honest data_quality_notes.
- IMPORTANT: ignore the difference between "has visual_types" vs "missing visual_types" — empty visual_types means legacy data, not a real signal. Compare ONLY among videos that have visual_types populated.

The `suggestion` field MUST be a concrete, mechanical edit to prompts/scenario_v2.md:
  ✓ GOOD: "Increase bias on visual_type=numbered_card from 28% to 55% for hook segments"
  ✓ GOOD: "Reduce comparison_strikethrough share from 30% to 10%, redistribute to project_chips"
  ✓ GOOD: "Add constraint: code_card max 1 per video"
  ✗ BAD:  "Investigate why X performs better"
  ✗ BAD:  "Consider simplifying visual layouts"
  ✗ BAD:  "Review the scenario_v2.md"

The suggestion must contain at least one number/percentage OR an explicit "from X to Y" / "set X to Y" / "max N per …" change. No vague verbs (consider, investigate, review, explore).
"""


def build_user_prompt(rows: list[dict], summary: dict, week_iso: str) -> str:
    payload = {
        "week_iso": week_iso,
        "videos": rows,
        "summary": summary,
        "note_for_analyst": (
            "Field 'visual_types' is the ordered list of segment layouts in the video. "
            "Empty list means scenario.json wasn't found locally — treat as missing data."
        ),
    }
    return (
        f"Genesis week {week_iso} data:\n\n"
        f"{json.dumps(payload, indent=2, default=str, ensure_ascii=False)}\n\n"
        "Return JSON exactly in this shape:\n"
        '{\n'
        '  "insights": [\n'
        '    {"hypothesis": "...", "evidence": "...", "confidence": "low|medium|high", "n": 0, "suggestion": "..."}\n'
        '  ],\n'
        '  "data_quality_notes": "..."\n'
        '}'
    )


def call_openai(system: str, user: str) -> dict:
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 2000,
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
    raw = resp["choices"][0]["message"]["content"]
    return json.loads(raw)


# ============== Insert ==============

def insert_insights(payload: dict, week_iso: str, summary: dict) -> list[str]:
    """Insert into insights table (real schema: insight_text, category, proposed_change jsonb)."""
    rows = []
    for ins in payload.get("insights") or []:
        text = (ins.get("hypothesis") or "").strip()
        suggestion = (ins.get("suggestion") or "").strip()
        if suggestion:
            text = f"{text}\n\nSuggestion: {suggestion}"
        rows.append({
            "week_iso": week_iso,
            "insight_text": text[:4000],
            "category": "performance",
            "proposed_change": {
                "hypothesis": ins.get("hypothesis"),
                "evidence": ins.get("evidence"),
                "confidence": ins.get("confidence"),
                "sample_size": ins.get("n"),
                "suggestion": ins.get("suggestion"),
                "data_quality_notes": payload.get("data_quality_notes"),
                "group_summary": summary,
            },
            "status": "proposed",
        })
    if not rows:
        return []
    res = sb_insert("insights", rows)
    return [r["id"] for r in res if "id" in r]


# ============== Main ==============

def iso_week(d: datetime) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--week", type=str, help="ISO week like 2026-W20")
    ap.add_argument("--dry-run", action="store_true", help="print payload, skip OpenAI")
    ap.add_argument("--no-insert", action="store_true", help="call OpenAI but skip DB insert")
    ap.add_argument("--days", type=int, default=7, help="lookback days when --once")
    args = ap.parse_args()

    if not SUPABASE_KEY:
        sys.exit("Missing SUPABASE_GENESIS_SERVICE_KEY")

    now = datetime.now(timezone.utc)
    if args.week:
        y, w = args.week.split("-W")
        monday = datetime.fromisocalendar(int(y), int(w), 1).replace(tzinfo=timezone.utc)
        since = monday
        until = monday + timedelta(days=7)
        week_label = args.week
    else:
        since = now - timedelta(days=args.days)
        until = now
        week_label = iso_week(since)

    print(f"=== G_D analyzer | week {week_label} | "
          f"{since.isoformat()} → {until.isoformat()} ===")

    metrics = fetch_metrics(since.isoformat(), until.isoformat())
    yt_map = fetch_yt_published()
    topic_map = fetch_topics()
    print(f"  metrics rows: {len(metrics)} | yt_published: {len(yt_map)} | topics: {len(topic_map)}")

    rows = aggregate(metrics, yt_map, topic_map)
    summary = group_summary(rows)
    print(f"  videos with data: {summary['total_videos']}")
    print(f"  visual_type segment counts: {summary['visual_type_total_segments']}")
    print(f"  topic_category counts: {summary['by_topic_category']}")

    if summary["total_videos"] == 0:
        print("[skip] no video metrics in window — nothing to analyze")
        return 0

    user_prompt = build_user_prompt(rows, summary, week_label)

    if args.dry_run:
        print("\n--- DRY RUN: GPT system prompt ---")
        print(SYSTEM_PROMPT)
        print("\n--- DRY RUN: GPT user prompt (first 4000 chars) ---")
        print(user_prompt[:4000])
        print(f"\n  (user_prompt total: {len(user_prompt)} chars)")
        return 0

    if not OPENAI_API_KEY:
        sys.exit("Missing OPENAI_API_KEY")

    print(f"\n--- Calling OpenAI ({OPENAI_MODEL}) ---")
    t0 = time.time()
    result = call_openai(SYSTEM_PROMPT, user_prompt)
    print(f"  done in {time.time() - t0:.1f}s")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.no_insert:
        print("\n--no-insert: skipping DB write")
        return 0

    ids = insert_insights(result, week_label, summary)
    print(f"\n  inserted {len(ids)} insights: {ids}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
