#!/usr/bin/env python3
"""
G_C metrics ingest — собирает метрики YT/LinkedIn в Supabase metrics_snapshots.

Replacement-ready для n8n workflow: тот же контракт (читает posts → пишет
metrics_snapshots), но без n8n — вызывается локально или через cron на VPS.

Usage:
    # Прогон сейчас (по всем платформам)
    python g_c_metrics.py --once

    # Только YouTube
    python g_c_metrics.py --once --platform youtube

    # Dry-run (печатает что бы вставил, но не пишет в Supabase)
    python g_c_metrics.py --once --dry-run

    # Bootstrap (опрос всех posts любого возраста, не только 30 дней)
    python g_c_metrics.py --once --bootstrap

    # Backfill post_id для существующих rows c post_id IS NULL
    python g_c_metrics.py --backfill --dry-run   # сухой прогон, печатает counts
    python g_c_metrics.py --backfill              # реальный UPDATE в Supabase

Окружение (.env или env vars):
    SUPABASE_URL
    SUPABASE_SERVICE_KEY
    YOUTUBE_OAUTH_TOKEN_PATH=~/Obsidian_AI_Brain/.youtube/token.pickle
    LINKEDIN_ACCESS_TOKEN

Зависимости:
    pip install google-api-python-client google-auth requests python-dateutil

Schema notes (verified 2026-05-19):
    posts(id uuid, platform text, external_id text, ...)
        platform ∈ {ghost_ru, ghost_en, telegram, linkedin, youtube}
        external_id для youtube = YouTube video_id (например 'xY5NwQBpznc').
    metrics_snapshots(id, post_id uuid REFERENCES posts(id), platform, metric_name,
                      metric_value, metadata jsonb {slug, video_id, urn, ...})

    Module C должен резолвить post_id ПЕРЕД INSERT, иначе Module D/F не могут
    JOIN-ить metrics → posts → topics. См. lookup_post_id() ниже.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import requests


# ============== Config ==============

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://czzzdhzzvtewvhcrlryr.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
YT_TOKEN_PATH = os.environ.get(
    "YOUTUBE_OAUTH_TOKEN_PATH",
    str(Path.home() / "Obsidian_AI_Brain" / ".youtube" / "token.pickle"),
)
LINKEDIN_TOKEN = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")
LOOKBACK_DAYS = int(os.environ.get("METRICS_LOOKBACK_DAYS", "30"))
BATCH_LIMIT = int(os.environ.get("METRICS_BATCH_LIMIT", "50"))


@dataclass
class Post:
    id: str               # posts.id (uuid) или slug для legacy yt_published
    platform: str
    external_id: str      # YT video_id / LI URN / TG message_id
    published_at: datetime


def _looks_like_uuid(s: str) -> bool:
    return len(s) == 36 and s.count("-") == 4


# ============== Supabase ==============

def supabase_select_recent_posts(bootstrap: bool = False) -> list[Post]:
    """Собирает posts для опроса.

    Источники:
    - `posts` (унифицированная таблица): platform=linkedin/telegram/ghost (где есть external_id)
    - `yt_published` (legacy table для YT): id-нет, post_id заменяется на video_id в metadata
    """
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY не задан")

    posts: list[Post] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # 1. posts (стандартная таблица) — LinkedIn / Telegram / etc.
    p_params: dict = {
        "select": "id,platform,external_id,published_at",
        "status": "eq.published",
        "external_id": "not.is.null",
        "order": "published_at.desc",
        "limit": str(BATCH_LIMIT),
    }
    if not bootstrap:
        p_params["published_at"] = f"gte.{cutoff}"
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/posts",
        params=p_params,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    for row in r.json():
        if not row.get("external_id"):
            continue
        posts.append(Post(
            id=row["id"],
            platform=row["platform"],
            external_id=row["external_id"],
            published_at=datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")),
        ))

    # 2. yt_published (legacy YT table) — id заменяется на slug, post_id будет null
    yt_params: dict = {
        "select": "slug,video_id,published_at",
        "video_id": "not.is.null",
        "order": "published_at.desc",
        "limit": str(BATCH_LIMIT),
    }
    if not bootstrap:
        yt_params["published_at"] = f"gte.{cutoff}"
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/yt_published",
        params=yt_params,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    for row in r.json():
        if not row.get("video_id"):
            continue
        posts.append(Post(
            id=row["slug"],  # slug используем как identifier (нет post_id в yt_published)
            platform="youtube",
            external_id=row["video_id"],
            published_at=datetime.fromisoformat(row["published_at"].replace("Z", "+00:00")),
        ))

    return posts


# ============== post_id lookup ==============
#
# Все INSERT'ы в metrics_snapshots обязаны делать lookup posts.id перед записью,
# иначе строки попадают с post_id=NULL и Module D/F не могут JOIN-ить
# metrics_snapshots → posts → topics. Кэш живёт на время одного прогона.

# (platform, external_id) → posts.id (uuid) | None (если не нашли)
_POST_ID_CACHE: dict[tuple[str, str], str | None] = {}
# для статистики/отчёта
_LOOKUP_STATS = {"hits": 0, "misses": 0, "queries": 0}


def lookup_post_id(platform: str, external_id: str | None) -> str | None:
    """Возвращает posts.id для платформы и внешнего идентификатора.

    Для YouTube external_id = video_id (то что лежит в metadata.video_id метрики).
    Для Ghost/TG/LinkedIn external_id = тот же external_id что хранит posts.

    Если posts row не найден — возвращает None и логирует warning (один раз
    на уникальную пару).
    """
    if not external_id:
        return None
    key = (platform, external_id)
    if key in _POST_ID_CACHE:
        _LOOKUP_STATS["hits"] += 1
        return _POST_ID_CACHE[key]

    _LOOKUP_STATS["queries"] += 1
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/posts",
            params={
                "select": "id",
                "platform": f"eq.{platform}",
                "external_id": f"eq.{external_id}",
                "limit": "1",
            },
            headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        print(f"[lookup] {platform}/{external_id} request failed: {e}", file=sys.stderr)
        _POST_ID_CACHE[key] = None
        _LOOKUP_STATS["misses"] += 1
        return None

    post_id = rows[0]["id"] if rows else None
    _POST_ID_CACHE[key] = post_id
    if post_id is None:
        _LOOKUP_STATS["misses"] += 1
        print(
            f"[lookup] WARN orphan metric — posts row not found "
            f"(platform={platform}, external_id={external_id})",
            file=sys.stderr,
        )
    return post_id


def supabase_insert_snapshots(snapshots: list[dict], dry_run: bool = False) -> int:
    """Bulk insert в metrics_snapshots. Возвращает количество вставленных."""
    if not snapshots:
        return 0
    if dry_run:
        print(f"[DRY-RUN] would insert {len(snapshots)} snapshots:")
        for s in snapshots[:5]:
            print(f"  {s}")
        if len(snapshots) > 5:
            print(f"  ... +{len(snapshots) - 5} more")
        return len(snapshots)

    url = f"{SUPABASE_URL}/rest/v1/metrics_snapshots"
    r = requests.post(
        url,
        json=snapshots,
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        timeout=15,
    )
    r.raise_for_status()
    return len(snapshots)


# ============== YouTube ==============

def _yt_client():
    """Возвращает discovery client для YouTube Data API. Auto-refresh expired token."""
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    if not Path(YT_TOKEN_PATH).exists():
        raise FileNotFoundError(f"YouTube token: {YT_TOKEN_PATH}")
    with open(YT_TOKEN_PATH, "rb") as f:
        creds = pickle.load(f)
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(YT_TOKEN_PATH, "wb") as f:
                pickle.dump(creds, f)
        except Exception as e:
            print(f"[YT-auth] refresh failed: {e}", file=sys.stderr)
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def fetch_youtube_metrics(posts: list[Post]) -> list[dict]:
    """Получает statistics для batch posts. Возвращает rows для metrics_snapshots."""
    yt_posts = [p for p in posts if p.platform == "youtube"]
    if not yt_posts:
        return []
    yt = _yt_client()
    snapshots: list[dict] = []
    captured_at = datetime.now(timezone.utc).isoformat()
    # YT API позволяет до 50 video IDs за запрос
    for i in range(0, len(yt_posts), 50):
        chunk = yt_posts[i:i + 50]
        ids = ",".join(p.external_id for p in chunk)
        try:
            resp = yt.videos().list(part="statistics,contentDetails", id=ids).execute()
        except Exception as e:
            print(f"[YT] error: {e}", file=sys.stderr)
            continue
        by_id: dict[str, dict] = {item["id"]: item for item in resp.get("items", [])}
        for p in chunk:
            item = by_id.get(p.external_id)
            if not item:
                continue
            stats = item.get("statistics", {})
            # post_id в metrics_snapshots — uuid REFERENCES posts(id).
            # Резолвим всегда через lookup_post_id по video_id, не доверяя
            # p.id (там может быть slug из legacy yt_published). Если в posts
            # YT-публикация ещё не записана (gap в Module B) — post_id=None.
            post_id = lookup_post_id("youtube", p.external_id)
            slug = p.id if not _looks_like_uuid(p.id) else None
            for metric in ("viewCount", "likeCount", "commentCount", "favoriteCount"):
                val = stats.get(metric)
                if val is None:
                    continue
                snapshots.append({
                    "captured_at": captured_at,
                    "platform": "youtube",
                    "post_id": post_id,
                    "metric_name": metric.replace("Count", "").lower(),  # views, likes, comments
                    "metric_value": float(val),
                    "metadata": {
                        "video_id": p.external_id,
                        "slug": slug,
                    },
                })
    return snapshots


def fetch_youtube_analytics(posts: list[Post]) -> list[dict]:
    """Avg view duration через YouTube Analytics API. Требует scope yt-analytics.readonly."""
    yt_posts = [p for p in posts if p.platform == "youtube"]
    if not yt_posts:
        return []
    try:
        from googleapiclient.discovery import build
        with open(YT_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
        yt_a = build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False)
    except Exception as e:
        print(f"[YT-A] init error: {e}", file=sys.stderr)
        return []
    snapshots: list[dict] = []
    captured_at = datetime.now(timezone.utc).isoformat()
    end = datetime.now(timezone.utc).date().isoformat()
    for p in yt_posts:
        start = p.published_at.date().isoformat()
        try:
            resp = yt_a.reports().query(
                ids="channel==MINE",
                startDate=start, endDate=end,
                metrics="views,averageViewDuration,averageViewPercentage",
                filters=f"video=={p.external_id}",
            ).execute()
        except Exception as e:
            print(f"[YT-A] {p.external_id} error: {e}", file=sys.stderr)
            continue
        rows = resp.get("rows", [])
        if not rows:
            continue
        # rows[0] = [views, avgViewDuration_sec, avgViewPct]
        _views, avg_dur, avg_pct = rows[0]
        post_id = lookup_post_id("youtube", p.external_id)
        snapshots.append({
            "captured_at": captured_at, "platform": "youtube", "post_id": post_id,
            "metric_name": "avg_view_duration_sec", "metric_value": float(avg_dur),
            "metadata": {"video_id": p.external_id, "since": start},
        })
        snapshots.append({
            "captured_at": captured_at, "platform": "youtube", "post_id": post_id,
            "metric_name": "avg_view_percentage", "metric_value": float(avg_pct),
            "metadata": {"video_id": p.external_id, "since": start},
        })
    return snapshots


# ============== LinkedIn ==============

def fetch_linkedin_metrics(posts: list[Post]) -> list[dict]:
    """Likes/comments через LinkedIn social-metadata API."""
    li_posts = [p for p in posts if p.platform == "linkedin"]
    if not li_posts or not LINKEDIN_TOKEN:
        return []
    snapshots: list[dict] = []
    captured_at = datetime.now(timezone.utc).isoformat()
    for p in li_posts:
        urn = p.external_id  # ожидаем urn:li:share:... или urn:li:ugcPost:...
        try:
            r = requests.get(
                f"https://api.linkedin.com/v2/socialMetadata/{urn}",
                headers={
                    "Authorization": f"Bearer {LINKEDIN_TOKEN}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                timeout=10,
            )
            if r.status_code != 200:
                print(f"[LI] {urn} {r.status_code} {r.text[:200]}", file=sys.stderr)
                continue
            data = r.json()
        except Exception as e:
            print(f"[LI] {urn} error: {e}", file=sys.stderr)
            continue
        # likes
        likes = data.get("totalSocialActivityCounts", {}).get("numLikes")
        comments = data.get("totalSocialActivityCounts", {}).get("numComments")
        shares = data.get("totalSocialActivityCounts", {}).get("numShares")
        # p.id уже uuid из posts (LinkedIn-posts всегда из таблицы posts),
        # но прогоняем через lookup для единообразия и кэша.
        post_id = lookup_post_id("linkedin", urn) or (p.id if _looks_like_uuid(p.id) else None)
        for name, val in (("likes", likes), ("comments", comments), ("shares", shares)):
            if val is None:
                continue
            snapshots.append({
                "captured_at": captured_at, "platform": "linkedin", "post_id": post_id,
                "metric_name": name, "metric_value": float(val),
                "metadata": {"urn": urn},
            })
    return snapshots


# ============== Backfill (one-shot) ==============

def run_backfill(dry_run: bool) -> None:
    """Догоняет post_id для существующих metrics_snapshots с post_id IS NULL.

    Алгоритм:
      1. SELECT id, platform, metadata FROM metrics_snapshots WHERE post_id IS NULL
         (батчами по 1000, обходим Supabase REST default limit).
      2. Для каждой row: external_id берём из metadata —
         youtube → metadata.video_id
         linkedin → metadata.urn
      3. lookup_post_id(platform, external_id). Если найден — UPDATE.
      4. Логируем counts: matched / orphan / errors.
    """
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY не задан")

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

    matched = 0
    orphan = 0
    errors = 0
    scanned = 0
    by_platform_orphan: dict[str, set[str]] = {}

    page_size = 1000
    offset = 0
    while True:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
            params={
                "select": "id,platform,metadata",
                "post_id": "is.null",
                "order": "captured_at.asc",
                "limit": str(page_size),
                "offset": str(offset),
            },
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for row in rows:
            scanned += 1
            platform = row.get("platform") or ""
            meta = row.get("metadata") or {}
            if platform == "youtube":
                ext = meta.get("video_id")
            elif platform == "linkedin":
                ext = meta.get("urn")
            else:
                # ghost/telegram — пока в metadata ничего не пишем, нужно
                # расширять metadata если добавим эти платформы в C.
                ext = meta.get("external_id") or meta.get("video_id") or meta.get("urn")

            post_id = lookup_post_id(platform, ext)
            if post_id is None:
                orphan += 1
                by_platform_orphan.setdefault(platform, set()).add(str(ext))
                continue

            if dry_run:
                matched += 1
                continue

            # Реальный UPDATE
            try:
                up = requests.patch(
                    f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
                    params={"id": f"eq.{row['id']}"},
                    json={"post_id": post_id},
                    headers={**headers, "Prefer": "return=minimal"},
                    timeout=10,
                )
                up.raise_for_status()
                matched += 1
            except Exception as e:
                errors += 1
                print(f"[backfill] UPDATE {row['id']} failed: {e}", file=sys.stderr)

        if len(rows) < page_size:
            break
        offset += page_size

    mode = "DRY-RUN" if dry_run else "APPLIED"
    print(f"[backfill][{mode}] scanned={scanned} matched={matched} "
          f"orphan={orphan} errors={errors}")
    for plat, extids in by_platform_orphan.items():
        sample = list(sorted(extids))[:10]
        print(f"[backfill] orphan platform={plat} unique_external_ids={len(extids)} "
              f"sample={sample}")
    if dry_run:
        print("[backfill] dry-run — изменений не сделано. Для apply: "
              "запусти без --dry-run.")


# ============== Main ==============

PLATFORM_FETCHERS = {
    "youtube": [fetch_youtube_metrics, fetch_youtube_analytics],
    "linkedin": [fetch_linkedin_metrics],
}


def _print_lookup_stats() -> None:
    s = _LOOKUP_STATS
    print(f"[g_c] post_id lookup: queries={s['queries']} cache_hits={s['hits']} "
          f"misses(orphans)={s['misses']} cached_pairs={len(_POST_ID_CACHE)}")


def run_once(platform_filter: str | None, dry_run: bool, bootstrap: bool) -> None:
    posts = supabase_select_recent_posts(bootstrap=bootstrap)
    print(f"[g_c] fetched {len(posts)} posts (lookback={LOOKBACK_DAYS}d, bootstrap={bootstrap})")
    if platform_filter:
        posts = [p for p in posts if p.platform == platform_filter]
        print(f"[g_c] filtered to {len(posts)} {platform_filter} posts")
    all_snapshots: list[dict] = []
    for platform, fetchers in PLATFORM_FETCHERS.items():
        if platform_filter and platform_filter != platform:
            continue
        for fn in fetchers:
            try:
                rows = fn(posts)
                print(f"[g_c] {fn.__name__}: {len(rows)} rows")
                all_snapshots.extend(rows)
            except Exception as e:
                print(f"[g_c] {fn.__name__} failed: {e}", file=sys.stderr)
    inserted = supabase_insert_snapshots(all_snapshots, dry_run=dry_run)
    print(f"[g_c] inserted {inserted} snapshots into metrics_snapshots")
    _print_lookup_stats()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Один прогон ingest")
    ap.add_argument("--platform", choices=["youtube", "linkedin"],
                    help="Только одна платформа")
    ap.add_argument("--dry-run", action="store_true",
                    help="Не писать в Supabase, только печатать")
    ap.add_argument("--bootstrap", action="store_true",
                    help="Опрашивать все posts, не только за lookback")
    ap.add_argument("--backfill", action="store_true",
                    help="One-shot: догнать post_id у existing snapshots где post_id IS NULL")
    args = ap.parse_args()

    if args.backfill:
        run_backfill(dry_run=args.dry_run)
        _print_lookup_stats()
        return

    if not args.once:
        ap.error("--once или --backfill обязателен")
    run_once(args.platform, args.dry_run, args.bootstrap)


if __name__ == "__main__":
    main()
