#!/usr/bin/env python3
"""
G_C Google Search Console ingestor — что ищут, где, как часто → metrics_snapshots (platform='gsc').

Это датчик СПРОСА (LeadEngine, Спринт 0): запросы, показы, клики, позиция по странам
DE/AT/CH и по страницам сайта. Модель данных та же, что у остальных g_c_*:
metrics_snapshots(platform='gsc', metric_name, metric_value, post_id?, metadata jsonb).

Что пишет за каждый день окна (GSC отдаёт данные с задержкой ~2 дня):
- site_clicks / site_impressions / site_ctr / site_position   (metadata {date, country?})
- query_clicks / query_impressions / query_position            (metadata {date, query, country, page?})
- page_clicks / page_impressions / page_position               (metadata {date, page, slug}; post_id по slug)

Идемпотентность: перед вставкой проверяется, есть ли уже строки за этот date — если есть, день пропускается.

Запуск:
    python g_c_gsc.py --once [--dry-run] [--days 3]

Окружение:
    SUPABASE_URL, SUPABASE_SERVICE_KEY
    GSC_OAUTH_TOKEN_PATH  — default ~/Obsidian_AI_Brain/.gsc/token.pickle (на дроплете /opt/genesis/gsc/token.pickle)
    GSC_SITE_URL          — default https://gerdennisai.com/
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://czzzdhzzvtewvhcrlryr.supabase.co")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TOKEN_PATH = Path(os.environ.get("GSC_OAUTH_TOKEN_PATH",
                                 str(Path.home() / "Obsidian_AI_Brain" / ".gsc" / "token.pickle")))
SITE_URL = os.environ.get("GSC_SITE_URL", "https://gerdennisai.com/")
ROW_LIMIT = 250
COUNTRIES_OF_INTEREST = {"deu", "aut", "che"}  # GSC отдаёт ISO-3166-1 alpha-3 в нижнем регистре

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


# --- GSC client -----------------------------------------------------------
def gsc_service():
    creds = pickle.load(open(TOKEN_PATH, "rb"))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def gsc_query(svc, day: date, dimensions: list[str]) -> list[dict]:
    body = {
        "startDate": day.isoformat(),
        "endDate": day.isoformat(),
        "dimensions": dimensions,
        "rowLimit": ROW_LIMIT,
        "dataState": "final",
    }
    resp = svc.searchanalytics().query(siteUrl=SITE_URL, body=body).execute()
    return resp.get("rows", [])


# --- Supabase helpers -----------------------------------------------------
_slug_cache: dict[str, str] | None = None


def _load_slug_index() -> dict[str, str]:
    global _slug_cache
    if _slug_cache is not None:
        return _slug_cache
    r = requests.get(f"{SUPABASE_URL}/rest/v1/posts",
                     params={"select": "id,slug", "slug": "not.is.null"},
                     headers={k: v for k, v in SB_HEADERS.items() if k != "Prefer"}, timeout=15)
    r.raise_for_status()
    _slug_cache = {row["slug"]: row["id"] for row in r.json() if row.get("slug")}
    return _slug_cache


def resolve_post_id(page_url: str) -> str | None:
    path = page_url.replace(SITE_URL.rstrip("/"), "").rstrip("/")
    if "/blog/" not in path:
        return None
    slug = path.split("/")[-1]
    idx = _load_slug_index()
    if slug in idx:
        return idx[slug]
    for s, pid in idx.items():
        if s.startswith(slug) or slug.startswith(s):
            return pid
    return None


def day_already_ingested(day: date) -> bool:
    r = requests.get(f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
                     params={"select": "id", "platform": "eq.gsc",
                             "metric_name": "eq.site_impressions",
                             "metadata->>date": f"eq.{day.isoformat()}", "limit": 1},
                     headers={k: v for k, v in SB_HEADERS.items() if k != "Prefer"}, timeout=15)
    r.raise_for_status()
    return bool(r.json())


def insert_rows(rows: list[dict], dry_run: bool) -> int:
    if not rows:
        return 0
    if dry_run:
        for p in rows[:15]:
            print(f"[DRY] {p['metric_name']}={p['metric_value']} meta={p['metadata']}")
        if len(rows) > 15:
            print(f"[DRY] … ещё {len(rows) - 15} строк")
        return len(rows)
    for i in range(0, len(rows), 200):
        r = requests.post(f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
                          json=rows[i:i + 200], headers=SB_HEADERS, timeout=30)
        r.raise_for_status()
    return len(rows)


def snap(name: str, value: float, meta: dict, post_id: str | None = None) -> dict:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": "gsc",
        "post_id": post_id,
        "metric_name": name,
        "metric_value": round(float(value), 4),
        "metadata": meta,
    }


# --- Main -----------------------------------------------------------------
def ingest_day(svc, day: date, dry_run: bool) -> int:
    d = day.isoformat()
    rows: list[dict] = []

    # 1. Site totals + per country
    for r in gsc_query(svc, day, ["country"]):
        c = r["keys"][0]
        base = {"date": d, "country": c}
        rows += [snap("site_clicks", r["clicks"], base), snap("site_impressions", r["impressions"], base),
                 snap("site_ctr", r["ctr"], base), snap("site_position", r["position"], base)]
    total = gsc_query(svc, day, [])
    if total:
        t = total[0]
        base = {"date": d, "country": "all"}
        rows += [snap("site_clicks", t["clicks"], base), snap("site_impressions", t["impressions"], base),
                 snap("site_ctr", t["ctr"], base), snap("site_position", t["position"], base)]
    else:
        rows.append(snap("site_impressions", 0, {"date": d, "country": "all"}))

    # 2. Queries × country (DACH отдельно, остальное агрегатом по запросу)
    for r in gsc_query(svc, day, ["query", "country"]):
        q, c = r["keys"]
        if c not in COUNTRIES_OF_INTEREST:
            continue
        meta = {"date": d, "query": q, "country": c}
        rows += [snap("query_clicks", r["clicks"], meta), snap("query_impressions", r["impressions"], meta),
                 snap("query_position", r["position"], meta)]
    for r in gsc_query(svc, day, ["query"]):
        meta = {"date": d, "query": r["keys"][0], "country": "all"}
        rows += [snap("query_clicks", r["clicks"], meta), snap("query_impressions", r["impressions"], meta),
                 snap("query_position", r["position"], meta)]

    # 3. Pages
    for r in gsc_query(svc, day, ["page"]):
        page = r["keys"][0]
        pid = resolve_post_id(page)
        meta = {"date": d, "page": page.replace(SITE_URL.rstrip("/"), ""), "matched_post": bool(pid)}
        rows += [snap("page_clicks", r["clicks"], meta, pid), snap("page_impressions", r["impressions"], meta, pid),
                 snap("page_position", r["position"], meta, pid)]

    n = insert_rows(rows, dry_run)
    print(f"[OK]  gsc {d}: {n} rows")
    return n


def run(days: int, dry_run: bool) -> int:
    svc = gsc_service()
    end = date.today() - timedelta(days=3)      # проверено 30.08: dataState=final отдаёт данные с лагом 3 дня
    total = 0
    for i in range(days):
        day = end - timedelta(days=i)
        if not dry_run and day_already_ingested(day):
            print(f"[SKIP] gsc {day}: already ingested")
            continue
        total += ingest_day(svc, day, dry_run)
    print(f"[SUMMARY] gsc: {total} metrics, site={SITE_URL}, window={days}d ending {end}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=4)
    args = ap.parse_args()
    return run(args.days, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
