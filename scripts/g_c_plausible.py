#!/usr/bin/env python3
"""
G_C Plausible ingestor — daily web-traffic snapshot → metrics_snapshots.

Plausible self-hosted: analytics.gerdennisai.com (site gerdennisai.com).
Это главный сигнал «какая тема привлекает трафик» для эволюционного цикла
(Module D анализирует → влияет на выбор тем в Module A distiller).

Что собирает:
- site_visitors_7d / site_pageviews_7d / site_visit_duration_7d (global, post_id=NULL)
- per utm_campaign (30d): campaign_visitors, campaign_pageviews
    → post_id матчится через posts.slug == utm_campaign (где возможно)
    → utm_campaign в G_B = slug темы (см. utm.py)
- per page (30d, только /blog/*): page_pageviews, page_visitors
    → post_id матчится через slug в URL

Запуск:
    python g_c_plausible.py --once
    python g_c_plausible.py --once --dry-run

Окружение (.g_c_env):
    SUPABASE_URL, SUPABASE_SERVICE_KEY
    PLAUSIBLE_API_KEY     — Bearer token (genesis-metrics key)
    PLAUSIBLE_BASE_URL    — https://analytics.gerdennisai.com
    PLAUSIBLE_SITE_ID     — gerdennisai.com
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
PL_KEY = os.environ["PLAUSIBLE_API_KEY"]
PL_BASE = os.environ.get("PLAUSIBLE_BASE_URL", "https://analytics.gerdennisai.com")
PL_SITE = os.environ.get("PLAUSIBLE_SITE_ID", "gerdennisai.com")

PL_HEADERS = {"Authorization": f"Bearer {PL_KEY}"}
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def pl_aggregate(period: str, metrics: str) -> dict:
    r = requests.get(
        f"{PL_BASE}/api/v1/stats/aggregate",
        params={"site_id": PL_SITE, "period": period, "metrics": metrics},
        headers=PL_HEADERS, timeout=15,
    )
    r.raise_for_status()
    return r.json().get("results", {})


def pl_breakdown(prop: str, period: str, metrics: str, limit: int = 50) -> list[dict]:
    r = requests.get(
        f"{PL_BASE}/api/v1/stats/breakdown",
        params={"site_id": PL_SITE, "period": period, "property": prop,
                "metrics": metrics, "limit": limit},
        headers=PL_HEADERS, timeout=15,
    )
    r.raise_for_status()
    return r.json().get("results", [])


# --- post_id resolution: slug → posts.id ---------------------------------
_slug_cache: dict[str, str] | None = None


def _load_slug_index() -> dict[str, str]:
    """Map slug → post_id (ghost_ru/en/de + любые) для привязки метрик к публикации."""
    global _slug_cache
    if _slug_cache is not None:
        return _slug_cache
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/posts",
        params={"select": "id,slug", "slug": "not.is.null"},
        headers={k: v for k, v in SB_HEADERS.items() if k != "Prefer"},
        timeout=15,
    )
    r.raise_for_status()
    _slug_cache = {row["slug"]: row["id"] for row in r.json() if row.get("slug")}
    return _slug_cache


def resolve_post_id(slug_or_campaign: str | None) -> str | None:
    if not slug_or_campaign:
        return None
    idx = _load_slug_index()
    if slug_or_campaign in idx:
        return idx[slug_or_campaign]
    # частичный матч (campaign может быть префиксом slug или наоборот)
    for slug, pid in idx.items():
        if slug.startswith(slug_or_campaign) or slug_or_campaign.startswith(slug):
            return pid
    return None


def insert_metric(name: str, value: float, post_id: str | None, metadata: dict, dry_run: bool) -> None:
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": "plausible",
        "post_id": post_id,
        "metric_name": name,
        "metric_value": value,
        "metadata": metadata,
    }
    if dry_run:
        tag = f" post={post_id[:8]}" if post_id else ""
        print(f"[DRY] {name}={value}{tag} meta={metadata}")
        return
    r = requests.post(f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
                      json=payload, headers=SB_HEADERS, timeout=10)
    r.raise_for_status()
    print(f"[OK]  {name}={value}")


def run(dry_run: bool) -> int:
    n = 0

    # 1. Global site metrics (7d)
    agg = pl_aggregate("7d", "visitors,pageviews,visit_duration")
    for key, mname in [("visitors", "site_visitors_7d"),
                       ("pageviews", "site_pageviews_7d"),
                       ("visit_duration", "site_visit_duration_7d")]:
        val = agg.get(key, {}).get("value")
        if val is not None:
            insert_metric(mname, val, None, {"period": "7d", "site": PL_SITE}, dry_run)
            n += 1

    # 2. Per utm_campaign (30d) — главный сигнал «какая тема зашла»
    for row in pl_breakdown("visit:utm_campaign", "30d", "visitors,pageviews"):
        camp = row.get("utm_campaign")
        if not camp or camp == "(none)":
            continue
        pid = resolve_post_id(camp)
        meta = {"utm_campaign": camp, "period": "30d", "matched_post": bool(pid)}
        insert_metric("campaign_visitors", row.get("visitors", 0), pid, meta, dry_run)
        insert_metric("campaign_pageviews", row.get("pageviews", 0), pid, meta, dry_run)
        n += 2

    # 3. Per blog page (30d) — какие посты читают
    for row in pl_breakdown("event:page", "30d", "visitors,pageviews"):
        page = row.get("page", "")
        if not page.startswith("/blog/"):
            continue
        slug = page.rstrip("/").split("/")[-1]
        pid = resolve_post_id(slug)
        meta = {"page": page, "slug": slug, "period": "30d", "matched_post": bool(pid)}
        insert_metric("page_pageviews", row.get("pageviews", 0), pid, meta, dry_run)
        insert_metric("page_visitors", row.get("visitors", 0), pid, meta, dry_run)
        n += 2

    print(f"[SUMMARY] plausible: {n} metrics, site={PL_SITE}, "
          f"7d={agg.get('visitors', {}).get('value')} visitors")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
