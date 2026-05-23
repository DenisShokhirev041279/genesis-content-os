#!/usr/bin/env python3
"""
G_C Ghost ingestor — daily snapshot Ghost newsletter subscribers.

Что собирает (platform='ghost', post_id=NULL):
- newsletter_members_total — все members
- newsletter_members_paid  — paid only (если есть paid plan)
- newsletter_members_free  - free only
- posts_total              - количество опубликованных Ghost постов

Запуск:
    python g_c_ghost.py --once
    python g_c_ghost.py --once --dry-run

Окружение:
    SUPABASE_URL, SUPABASE_SERVICE_KEY
    GHOST_ADMIN_API_URL    — например https://cms.gerdennisai.com/ghost/api/admin
    GHOST_ADMIN_API_KEY    — формат '<id>:<hex_secret>'
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import jwt
import requests


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GHOST_URL = os.environ.get("GHOST_ADMIN_API_URL", "https://cms.gerdennisai.com/ghost/api/admin")
GHOST_KEY = os.environ["GHOST_ADMIN_API_KEY"]

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def ghost_jwt() -> str:
    kid, secret = GHOST_KEY.split(":", 1)
    iat = int(time.time())
    return jwt.encode(
        {"iat": iat, "exp": iat + 300, "aud": "/admin/"},
        bytes.fromhex(secret),
        algorithm="HS256",
        headers={"kid": kid},
    )


def ghost_count(path: str, filter_q: str | None = None) -> int:
    token = ghost_jwt()
    params = {"limit": "1"}
    if filter_q:
        params["filter"] = filter_q
    r = requests.get(
        f"{GHOST_URL}/{path}/",
        params=params,
        headers={"Authorization": f"Ghost {token}"},
        timeout=10,
    )
    r.raise_for_status()
    return int(r.json()["meta"]["pagination"]["total"])


def insert_metric(name: str, value: float, metadata: dict, dry_run: bool) -> None:
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": "ghost",
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
    members_total = ghost_count("members")
    members_paid = ghost_count("members", filter_q="status:paid")
    members_free = ghost_count("members", filter_q="status:free")
    posts_total = ghost_count("posts", filter_q="status:published")

    meta = {"site": "cms.gerdennisai.com"}

    insert_metric("newsletter_members_total", members_total, meta, dry_run)
    insert_metric("newsletter_members_paid", members_paid, meta, dry_run)
    insert_metric("newsletter_members_free", members_free, meta, dry_run)
    insert_metric("posts_total", posts_total, meta, dry_run)

    print(f"[SUMMARY] members={members_total} (paid={members_paid}, free={members_free}), posts={posts_total}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
