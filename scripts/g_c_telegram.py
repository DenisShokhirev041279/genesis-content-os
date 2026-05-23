#!/usr/bin/env python3
"""
G_C Telegram channel ingestor — daily snapshot @ger_dennis_ai в metrics_snapshots.

Что собирает (channel-level, post_id=NULL):
- channel_subscribers — getChatMemberCount
- channel_posts_total — COUNT posts WHERE platform='telegram'
- channel_posts_24h   — то же, но published_at >= now-24h

Запуск:
    python g_c_telegram.py --once
    python g_c_telegram.py --once --dry-run

Окружение:
    SUPABASE_URL, SUPABASE_SERVICE_KEY (как у g_c_metrics)
    TG_BOT_TOKEN — бот должен быть admin в канале
    TG_CHANNEL_CHAT_ID — например -1002216661152
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
TG_TOKEN = os.environ["TG_BOT_TOKEN"]
TG_CHAT = os.environ.get("TG_CHANNEL_CHAT_ID", "-1002216661152")

TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"
SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def tg_subscribers() -> int:
    r = requests.get(f"{TG_API}/getChatMemberCount", params={"chat_id": TG_CHAT}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return int(data["result"])


def tg_channel_meta() -> dict:
    r = requests.get(f"{TG_API}/getChat", params={"chat_id": TG_CHAT}, timeout=10)
    r.raise_for_status()
    d = r.json().get("result", {})
    return {
        "username": d.get("username"),
        "title": d.get("title"),
        "chat_id": d.get("id"),
    }


def sb_count_posts(since_iso: str | None = None) -> int:
    params = {"select": "id", "platform": "eq.telegram"}
    if since_iso:
        params["published_at"] = f"gte.{since_iso}"
    headers = {**SB_HEADERS, "Prefer": "count=exact"}
    r = requests.head(
        f"{SUPABASE_URL}/rest/v1/posts",
        params=params,
        headers=headers,
        timeout=10,
    )
    r.raise_for_status()
    cr = r.headers.get("Content-Range", "0/0")
    return int(cr.split("/")[-1])


def insert_metric(name: str, value: float, metadata: dict, dry_run: bool) -> None:
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": "telegram",
        "post_id": None,
        "metric_name": name,
        "metric_value": value,
        "metadata": metadata,
    }
    if dry_run:
        print(f"[DRY] {name}={value} meta={metadata}")
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
        json=payload,
        headers=SB_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    print(f"[OK]  {name}={value}")


def run(dry_run: bool) -> int:
    meta = tg_channel_meta()
    subs = tg_subscribers()
    total = sb_count_posts()
    since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    last_24h = sb_count_posts(since_iso=since_24h)

    base_meta = {"channel": meta["username"], "title": meta["title"]}

    insert_metric("channel_subscribers", subs, base_meta, dry_run)
    insert_metric("channel_posts_total", total, base_meta, dry_run)
    insert_metric("channel_posts_24h", last_24h, base_meta, dry_run)

    print(f"[SUMMARY] @{meta['username']}: {subs} subs, {total} total posts, {last_24h} in 24h")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single run (default)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
