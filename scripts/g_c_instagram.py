#!/usr/bin/env python3
"""
G_C Instagram ingestor — daily snapshot @ger_denis_ai → metrics_snapshots.

ВАЖНО (контекст Дениса 2026-05-29): IG = ключевая ЦА (лиды, русскоговорящий
бизнес Европы/DACH), но рост стопорится (46 подписчиков на 255 постов).
Этот ingestor собирает сигналы для Module D: что залетает / почему не растёт.

Что собирает:
- Account (post_id=NULL): followers_count, media_count, account_reach_day
- Per последние N Reels: reach, likes, comments, saved, shares, total_interactions
    + metadata: media_id, caption (первые 120), media_type, timestamp, permalink
    → engagement_rate = total_interactions / reach (вычисляется в Module D)
    → post_id матчится через posts.external_id == ig_media_id (где есть)

Запуск:
    python g_c_instagram.py --once [--dry-run] [--limit 15]

Окружение (.g_c_env):
    SUPABASE_URL, SUPABASE_SERVICE_KEY
    IG_ACCESS_TOKEN, IG_USER_ID
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
IG_TOKEN = os.environ["IG_ACCESS_TOKEN"]
IG_USER = os.environ.get("IG_USER_ID", "17841468094774199")

GRAPH = "https://graph.facebook.com/v22.0"
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

REEL_METRICS = ["reach", "likes", "comments", "saved", "shares", "total_interactions"]


def ig_account() -> dict:
    r = requests.get(f"{GRAPH}/{IG_USER}",
                     params={"fields": "followers_count,media_count,username",
                             "access_token": IG_TOKEN}, timeout=15)
    r.raise_for_status()
    return r.json()


def ig_account_reach() -> int | None:
    r = requests.get(f"{GRAPH}/{IG_USER}/insights",
                     params={"metric": "reach", "period": "day",
                             "metric_type": "total_value", "access_token": IG_TOKEN}, timeout=15)
    d = r.json()
    if d.get("data"):
        return d["data"][0].get("total_value", {}).get("value")
    return None


def ig_recent_media(limit: int) -> list[dict]:
    r = requests.get(f"{GRAPH}/{IG_USER}/media",
                     params={"fields": "id,caption,media_type,timestamp,permalink",
                             "limit": limit, "access_token": IG_TOKEN}, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def ig_media_insights(media_id: str) -> dict:
    r = requests.get(f"{GRAPH}/{media_id}/insights",
                     params={"metric": ",".join(REEL_METRICS), "access_token": IG_TOKEN}, timeout=15)
    d = r.json()
    if "error" in d:
        return {}
    out = {}
    for m in d.get("data", []):
        vals = m.get("values", [{}])
        out[m["name"]] = vals[0].get("value", 0) if vals else 0
    return out


_ext_cache: dict[str, str] | None = None


def resolve_post_id(media_id: str) -> str | None:
    """Match IG media_id → posts.id через external_id (если mirror записал)."""
    global _ext_cache
    if _ext_cache is None:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/posts",
                         params={"select": "id,external_id", "platform": "eq.instagram",
                                 "external_id": "not.is.null"},
                         headers={k: v for k, v in SB_HEADERS.items() if k != "Prefer"}, timeout=15)
        r.raise_for_status()
        _ext_cache = {row["external_id"]: row["id"] for row in r.json() if row.get("external_id")}
    return _ext_cache.get(media_id)


def insert_metric(name, value, post_id, metadata, dry_run):
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": "instagram", "post_id": post_id,
        "metric_name": name, "metric_value": value, "metadata": metadata,
    }
    if dry_run:
        print(f"[DRY] {name}={value} meta={ {k: metadata[k] for k in list(metadata)[:2]} }")
        return
    r = requests.post(f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
                      json=payload, headers=SB_HEADERS, timeout=10)
    r.raise_for_status()


def run(limit: int, dry_run: bool) -> int:
    acc = ig_account()
    base = {"username": acc.get("username")}
    insert_metric("followers_count", acc.get("followers_count", 0), None, base, dry_run)
    insert_metric("media_count", acc.get("media_count", 0), None, base, dry_run)
    reach = ig_account_reach()
    if reach is not None:
        insert_metric("account_reach_day", reach, None, base, dry_run)

    n = 3
    for media in ig_recent_media(limit):
        mid = media["id"]
        ins = ig_media_insights(mid)
        if not ins:
            continue
        pid = resolve_post_id(mid)
        meta = {
            "ig_media_id": mid,
            "caption": (media.get("caption") or "")[:120],
            "media_type": media.get("media_type"),
            "timestamp": media.get("timestamp"),
            "permalink": media.get("permalink"),
        }
        for metric, val in ins.items():
            insert_metric(f"reel_{metric}", val, pid, meta, dry_run)
            n += 1

    foll = acc.get("followers_count", 0)
    print(f"[SUMMARY] instagram @{acc.get('username')}: {foll} followers, "
          f"{acc.get('media_count')} posts, {n} metrics, reach_day={reach}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()
    return run(limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
