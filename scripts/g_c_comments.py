#!/usr/bin/env python3
"""
G_C comments ingest — собирает comments + engagement из IG / YT (FB позже)
в Supabase metrics_snapshots.

Расширение Module C. НЕ заменяет `g_c_metrics.py` (там views/likes), а
доливает 3 новых metric_name:

    comments_count    — суммарное число комментариев на пост
    comments_top      — metric_value=0, metadata.top_5=[…] (топ-5 по like_count)
    engagement_rate   — (likes + comments) / views, decimal (0.0314 = 3.14%)

Schema metrics_snapshots остаётся без изменений (всё в metadata jsonb).

Источники постов:
  • posts (status=published, platform ∈ {instagram, facebook, youtube}, ≤7d)
  • yt_published (legacy YT, ≤7d) — fallback для YT, как в g_c_metrics

API endpoints:
  YT  : commentThreads().list(videoId=, part=snippet, maxResults=100, order=relevance)
        + videos().list(part=statistics) для views/likes/comments (для ER)
  IG  : GET /{media-id}?fields=comments_count,like_count,media_type
        GET /{media-id}/comments?fields=id,text,username,timestamp,like_count
  FB  : пропускается (нет FB-постов в posts на 2026-05-24; включить когда
        Claudian добавит publisher и расширит CHECK constraint posts.platform).

Usage:
    python g_c_comments.py --once
    python g_c_comments.py --once --platform youtube
    python g_c_comments.py --once --platform instagram
    python g_c_comments.py --once --dry-run

Env (наследуется из ~/.local/bin/.g_c_env + MotionViral/.env):
    SUPABASE_URL, SUPABASE_SERVICE_KEY        — пишем сюда
    YOUTUBE_OAUTH_TOKEN_PATH                  — pickle для YT API
    IG_ACCESS_TOKEN, IG_USER_ID               — long-lived из MotionViral
    COMMENTS_LOOKBACK_DAYS=7                  — окно поиска постов
    COMMENTS_TOP_N=5                          — сколько комментов класть в metadata
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


# ============== Config ==============

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://czzzdhzzvtewvhcrlryr.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
YT_TOKEN_PATH = os.environ.get(
    "YOUTUBE_OAUTH_TOKEN_PATH",
    str(Path.home() / "Obsidian_AI_Brain" / ".youtube" / "token.pickle"),
)
IG_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "")
IG_USER_ID = os.environ.get("IG_USER_ID", "")
IG_API = "https://graph.facebook.com/v23.0"

LOOKBACK_DAYS = int(os.environ.get("COMMENTS_LOOKBACK_DAYS", "7"))
TOP_N = int(os.environ.get("COMMENTS_TOP_N", "5"))
BATCH_LIMIT = int(os.environ.get("COMMENTS_BATCH_LIMIT", "50"))

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
}
SB_WRITE_HEADERS = {
    **SB_HEADERS,
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

# Счётчики для отчёта (особенно для --dry-run)
_STATS = {
    "posts_total": 0,
    "yt_posts": 0,
    "ig_posts": 0,
    "fb_posts": 0,
    "yt_api_calls": 0,
    "ig_api_calls": 0,
    "snapshots_built": 0,
}


@dataclass
class Post:
    id: str | None        # posts.id (uuid) или None для legacy yt_published
    platform: str         # 'youtube' | 'instagram' | 'facebook'
    external_id: str
    published_at: datetime
    slug: str | None = None


def _looks_like_uuid(s: str) -> bool:
    return isinstance(s, str) and len(s) == 36 and s.count("-") == 4


# ============== Supabase: загрузка постов ==============

def fetch_recent_posts() -> list[Post]:
    """Собирает posts опубликованные за LOOKBACK_DAYS для IG/FB/YT.

    Источники:
      1. posts (унифицированная) — platform ∈ {instagram, facebook, youtube}
      2. yt_published (legacy YT) — fallback
    """
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY не задан")

    posts: list[Post] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()

    # 1) posts таблица — IG / FB / YT
    p_params = {
        "select": "id,platform,external_id,published_at,slug",
        "status": "eq.published",
        "platform": "in.(instagram,facebook,youtube)",
        "external_id": "not.is.null",
        "published_at": f"gte.{cutoff}",
        "order": "published_at.desc",
        "limit": str(BATCH_LIMIT),
    }
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/posts",
        params=p_params,
        headers=SB_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    for row in r.json():
        posts.append(Post(
            id=row["id"],
            platform=row["platform"],
            external_id=row["external_id"],
            published_at=datetime.fromisoformat(
                row["published_at"].replace("Z", "+00:00")
            ),
            slug=row.get("slug"),
        ))

    # 2) yt_published (legacy) — YT
    yt_params = {
        "select": "slug,video_id,published_at",
        "video_id": "not.is.null",
        "published_at": f"gte.{cutoff}",
        "order": "published_at.desc",
        "limit": str(BATCH_LIMIT),
    }
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/yt_published",
        params=yt_params,
        headers=SB_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    seen_yt = {p.external_id for p in posts if p.platform == "youtube"}
    for row in r.json():
        vid = row.get("video_id")
        if not vid or vid in seen_yt:
            continue
        posts.append(Post(
            id=None,
            platform="youtube",
            external_id=vid,
            published_at=datetime.fromisoformat(
                row["published_at"].replace("Z", "+00:00")
            ),
            slug=row.get("slug"),
        ))

    _STATS["posts_total"] = len(posts)
    _STATS["yt_posts"] = sum(1 for p in posts if p.platform == "youtube")
    _STATS["ig_posts"] = sum(1 for p in posts if p.platform == "instagram")
    _STATS["fb_posts"] = sum(1 for p in posts if p.platform == "facebook")
    return posts


# ============== post_id lookup (та же логика что в g_c_metrics) ==============

_POST_ID_CACHE: dict[tuple[str, str], str | None] = {}


def lookup_post_id(platform: str, external_id: str | None) -> str | None:
    if not external_id:
        return None
    key = (platform, external_id)
    if key in _POST_ID_CACHE:
        return _POST_ID_CACHE[key]
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/posts",
            params={
                "select": "id",
                "platform": f"eq.{platform}",
                "external_id": f"eq.{external_id}",
                "limit": "1",
            },
            headers=SB_HEADERS,
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        print(f"[lookup] {platform}/{external_id} failed: {e}", file=sys.stderr)
        _POST_ID_CACHE[key] = None
        return None
    pid = rows[0]["id"] if rows else None
    _POST_ID_CACHE[key] = pid
    return pid


# ============== Supabase: insert ==============

def insert_snapshots(snapshots: list[dict], dry_run: bool) -> int:
    if not snapshots:
        return 0
    if dry_run:
        print(f"[DRY-RUN] would insert {len(snapshots)} snapshots:")
        for s in snapshots[:8]:
            preview = {k: v for k, v in s.items() if k != "metadata"}
            meta = s.get("metadata") or {}
            top = meta.get("top_5")
            meta_str = (
                f"top_5=[{len(top)} items]" if top
                else str(meta)[:120]
            )
            print(f"  {preview} | meta: {meta_str}")
        if len(snapshots) > 8:
            print(f"  ... +{len(snapshots) - 8} more")
        return len(snapshots)
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
        json=snapshots,
        headers=SB_WRITE_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return len(snapshots)


# ============== YouTube ==============

def _yt_client():
    from googleapiclient.discovery import build
    from google.auth.transport.requests import Request
    if not Path(YT_TOKEN_PATH).exists():
        raise FileNotFoundError(f"YT token: {YT_TOKEN_PATH}")
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


def fetch_youtube_comments(posts: list[Post], dry_run: bool) -> list[dict]:
    yt_posts = [p for p in posts if p.platform == "youtube"]
    if not yt_posts:
        return []

    snapshots: list[dict] = []
    captured_at = datetime.now(timezone.utc).isoformat()

    # 1. Stats (для ER): videos().list(statistics) — 1 запрос на 50 ID.
    stats_by_id: dict[str, dict] = {}
    if not dry_run:
        yt = _yt_client()
        for i in range(0, len(yt_posts), 50):
            chunk = yt_posts[i:i + 50]
            ids = ",".join(p.external_id for p in chunk)
            try:
                _STATS["yt_api_calls"] += 1
                resp = yt.videos().list(part="statistics", id=ids).execute()
                for item in resp.get("items", []):
                    stats_by_id[item["id"]] = item.get("statistics", {})
            except Exception as e:
                print(f"[YT-stats] {e}", file=sys.stderr)
    else:
        # В dry-run всё равно учтём 1 батч-вызов
        _STATS["yt_api_calls"] += (len(yt_posts) + 49) // 50

    # 2. Comments per video: commentThreads().list() — 1 запрос на видео.
    #    ВАЖНО: текущий OAuth token имеет scopes [youtube, youtube.upload],
    #    но НЕ youtube.force-ssl, который требуется для commentThreads.list
    #    под OAuth. 403 insufficientPermissions перехватываем и graceful-fallback:
    #      → comments_count берём из statistics.commentCount (это число точное)
    #      → top_5 = [] с пометкой "top_unavailable" в metadata
    #    Альтернатива в будущем: добавить YOUTUBE_API_KEY в .g_c_env и
    #    дёргать commentThreads через requests (public endpoint, key= вместо OAuth).
    for p in yt_posts:
        post_id = lookup_post_id("youtube", p.external_id)
        slug = p.slug if p.slug else None

        comments_count = 0
        top: list[dict] = []
        top_unavailable: str | None = None

        # Сначала всегда тянем точную цифру из stats (она тоже нужна для ER)
        stat_cc = stats_by_id.get(p.external_id, {}).get("commentCount")
        if stat_cc is not None:
            try:
                comments_count = int(stat_cc)
            except (TypeError, ValueError):
                pass

        if dry_run:
            _STATS["yt_api_calls"] += 1
        else:
            try:
                _STATS["yt_api_calls"] += 1
                # API key вариант для commentThreads (OAuth scope youtube.force-ssl отсутствует)
                api_key = os.environ.get('YOUTUBE_API_KEY')
                if api_key:
                    import requests as _r
                    r = _r.get(
                        'https://www.googleapis.com/youtube/v3/commentThreads',
                        params={
                            'key': api_key,
                            'videoId': video_id,
                            'part': 'snippet',
                            'maxResults': 100,
                            'order': 'relevance',
                            'textFormat': 'plainText',
                        },
                        timeout=15,
                    )
                    if r.status_code == 200:
                        comments_data = r.json()
                        items = comments_data.get('items', [])
                        top_5 = []
                        for it in items[:5]:
                            sn = it.get('snippet', {}).get('topLevelComment', {}).get('snippet', {})
                            top_5.append({
                                'text': (sn.get('textDisplay') or '')[:300],
                                'author': sn.get('authorDisplayName',''),
                                'likes': sn.get('likeCount', 0),
                                'published_at': sn.get('publishedAt',''),
                            })
                        metadata_top = {'top_5': top_5, 'fetched_via': 'api_key'}
                        metric_value_count = float(len(items))
                        api_calls['yt'] += 1
                        results.append((video_id, 'comments_count', metric_value_count, {'fetched_via':'api_key'}))
                        results.append((video_id, 'comments_top', 0.0, metadata_top))
                        continue
                    else:
                        print(f"  [yt] api_key fail HTTP {r.status_code}: {r.text[:120]}", file=sys.stderr)
                # fallback: OAuth (видимо не сработает с force-ssl)
                resp = yt.commentThreads().list(
                    part="snippet",
                    videoId=p.external_id,
                    maxResults=100,
                    order="relevance",
                    textFormat="plainText",
                ).execute()
                items = resp.get("items", [])
            except Exception as e:
                msg = str(e)
                items = []
                if "commentsDisabled" in msg or "disabled" in msg.lower():
                    comments_count = 0
                    top_unavailable = "commentsDisabled"
                elif "insufficientPermissions" in msg or "403" in msg:
                    # OAuth scope не покрывает commentThreads.list —
                    # comments_count уже есть из stats, top_5 не достать.
                    top_unavailable = "oauth_scope_missing_force-ssl"
                else:
                    print(f"[YT-comments] {p.external_id} {e}", file=sys.stderr)
                    top_unavailable = "api_error"

            # Top-N по likeCount
            scored = []
            for it in items:
                top_lvl = (it.get("snippet") or {}).get("topLevelComment", {})
                sn = top_lvl.get("snippet") or {}
                scored.append({
                    "id": top_lvl.get("id"),
                    "text": (sn.get("textDisplay") or "")[:500],
                    "username": sn.get("authorDisplayName"),
                    "created_time": sn.get("publishedAt"),
                    "like_count": int(sn.get("likeCount") or 0),
                })
            scored.sort(key=lambda x: x["like_count"], reverse=True)
            top = scored[:TOP_N]

        # comments_count
        snapshots.append({
            "captured_at": captured_at,
            "platform": "youtube",
            "post_id": post_id,
            "metric_name": "comments_count",
            "metric_value": float(comments_count),
            "metadata": {"video_id": p.external_id, "slug": slug},
        })
        # comments_top
        top_meta = {"video_id": p.external_id, "slug": slug, "top_5": top}
        if top_unavailable:
            top_meta["top_unavailable"] = top_unavailable
        snapshots.append({
            "captured_at": captured_at,
            "platform": "youtube",
            "post_id": post_id,
            "metric_name": "comments_top",
            "metric_value": 0,
            "metadata": top_meta,
        })
        # engagement_rate (только если есть views и хоть какие-то stats)
        stats = stats_by_id.get(p.external_id, {})
        views = stats.get("viewCount")
        likes = stats.get("likeCount")
        if views and int(views) > 0:
            er = (int(likes or 0) + comments_count) / float(views)
            snapshots.append({
                "captured_at": captured_at,
                "platform": "youtube",
                "post_id": post_id,
                "metric_name": "engagement_rate",
                "metric_value": round(er, 6),
                "metadata": {
                    "video_id": p.external_id,
                    "slug": slug,
                    "views": int(views),
                    "likes": int(likes or 0),
                    "comments": comments_count,
                },
            })

    _STATS["snapshots_built"] += len(snapshots)
    return snapshots


# ============== Instagram ==============

def fetch_instagram_comments(posts: list[Post], dry_run: bool) -> list[dict]:
    ig_posts = [p for p in posts if p.platform == "instagram"]
    if not ig_posts:
        return []
    if not IG_TOKEN:
        print("[IG] IG_ACCESS_TOKEN отсутствует — пропускаю", file=sys.stderr)
        return []

    snapshots: list[dict] = []
    captured_at = datetime.now(timezone.utc).isoformat()

    for p in ig_posts:
        post_id = lookup_post_id("instagram", p.external_id) or p.id
        media_id = p.external_id

        comments_count = 0
        like_count = 0
        media_type = None
        top: list[dict] = []

        if dry_run:
            _STATS["ig_api_calls"] += 2  # media + comments
        else:
            # 1. media-level stats
            try:
                _STATS["ig_api_calls"] += 1
                rm = requests.get(
                    f"{IG_API}/{media_id}",
                    params={
                        "fields": "comments_count,like_count,media_type",
                        "access_token": IG_TOKEN,
                    },
                    timeout=10,
                )
                if rm.status_code == 200:
                    d = rm.json()
                    comments_count = int(d.get("comments_count") or 0)
                    like_count = int(d.get("like_count") or 0)
                    media_type = d.get("media_type")
                else:
                    print(f"[IG-media] {media_id} {rm.status_code} {rm.text[:200]}",
                          file=sys.stderr)
            except Exception as e:
                print(f"[IG-media] {media_id} {e}", file=sys.stderr)

            # 2. comments list (top-N по like_count)
            try:
                _STATS["ig_api_calls"] += 1
                rc = requests.get(
                    f"{IG_API}/{media_id}/comments",
                    params={
                        "fields": "id,text,username,timestamp,like_count",
                        "access_token": IG_TOKEN,
                        "limit": 100,
                    },
                    timeout=10,
                )
                if rc.status_code == 200:
                    items = rc.json().get("data", []) or []
                    scored = [{
                        "id": it.get("id"),
                        "text": (it.get("text") or "")[:500],
                        "username": it.get("username"),
                        "created_time": it.get("timestamp"),
                        "like_count": int(it.get("like_count") or 0),
                    } for it in items]
                    scored.sort(key=lambda x: x["like_count"], reverse=True)
                    top = scored[:TOP_N]
                else:
                    print(f"[IG-comm] {media_id} {rc.status_code} {rc.text[:200]}",
                          file=sys.stderr)
            except Exception as e:
                print(f"[IG-comm] {media_id} {e}", file=sys.stderr)

        meta_base = {"media_id": media_id, "media_type": media_type, "slug": p.slug}

        snapshots.append({
            "captured_at": captured_at,
            "platform": "instagram",
            "post_id": post_id,
            "metric_name": "comments_count",
            "metric_value": float(comments_count),
            "metadata": meta_base,
        })
        snapshots.append({
            "captured_at": captured_at,
            "platform": "instagram",
            "post_id": post_id,
            "metric_name": "comments_top",
            "metric_value": 0,
            "metadata": {**meta_base, "top_5": top},
        })
        # ER для IG — без views (impressions требуют insights API + business account scope).
        # Используем like_count как proxy знаменатель: (likes+comments)/likes — пропускаем.
        # Считаем только когда появятся views из IG Insights (отдельная задача).

    _STATS["snapshots_built"] += len(snapshots)
    return snapshots


# ============== Facebook (skip) ==============

def fetch_facebook_comments(posts: list[Post], dry_run: bool) -> list[dict]:
    fb_posts = [p for p in posts if p.platform == "facebook"]
    if not fb_posts:
        return []
    print(f"[FB] {len(fb_posts)} FB постов — пропуск (publisher ещё не активен)",
          file=sys.stderr)
    return []


# ============== Main ==============

PLATFORM_FETCHERS = {
    "youtube": fetch_youtube_comments,
    "instagram": fetch_instagram_comments,
    "facebook": fetch_facebook_comments,
}


def run_once(platform_filter: str | None, dry_run: bool) -> int:
    posts = fetch_recent_posts()
    print(f"[g_c_comments] fetched {len(posts)} posts "
          f"(lookback={LOOKBACK_DAYS}d) — "
          f"yt={_STATS['yt_posts']} ig={_STATS['ig_posts']} fb={_STATS['fb_posts']}")
    if platform_filter:
        posts = [p for p in posts if p.platform == platform_filter]
        print(f"[g_c_comments] filtered to {len(posts)} {platform_filter}")

    all_snapshots: list[dict] = []
    for platform, fn in PLATFORM_FETCHERS.items():
        if platform_filter and platform_filter != platform:
            continue
        try:
            rows = fn(posts, dry_run)
            print(f"[g_c_comments] {fn.__name__}: {len(rows)} rows")
            all_snapshots.extend(rows)
        except Exception as e:
            print(f"[g_c_comments] {fn.__name__} failed: {e}", file=sys.stderr)

    inserted = insert_snapshots(all_snapshots, dry_run=dry_run)
    print(f"[g_c_comments] inserted={inserted} dry_run={dry_run}")
    print(f"[g_c_comments] api_calls: yt={_STATS['yt_api_calls']} "
          f"ig={_STATS['ig_api_calls']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="один прогон")
    ap.add_argument("--platform", choices=["youtube", "instagram", "facebook"],
                    help="только одна платформа")
    ap.add_argument("--dry-run", action="store_true",
                    help="не писать в Supabase и не дёргать YT/IG API (только посчитать запросы)")
    args = ap.parse_args()
    if not args.once:
        ap.error("--once обязателен")
    return run_once(args.platform, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
