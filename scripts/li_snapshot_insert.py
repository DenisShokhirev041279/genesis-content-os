#!/usr/bin/env python3
"""
LinkedIn-снимок → metrics_snapshots (platform='linkedin').

Без Company Page у LinkedIn нет API-аналитики личного профиля, поэтому цифры снимаются раз в неделю
из Creator Analytics (браузер) и вносятся сюда — чтобы попасть в дашборд и недельный отчёт.

Запуск:
    python li_snapshot_insert.py --followers 979 --impressions-7d 842 --reactions-7d 27 \
        --comments-7d 12 --reposts-7d 0 --profile-views-7d 164 [--note "..."] [--dry-run]

Окружение: SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import requests

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://czzzdhzzvtewvhcrlryr.supabase.co")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
SB_HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
              "Content-Type": "application/json", "Prefer": "return=minimal"}

FIELDS = ["followers", "impressions_7d", "reactions_7d", "comments_7d", "reposts_7d", "profile_views_7d",
          "search_appearances_7d"]


def main() -> int:
    ap = argparse.ArgumentParser()
    for f in FIELDS:
        ap.add_argument(f"--{f.replace('_', '-')}", type=float)
    ap.add_argument("--note", default="")
    ap.add_argument("--source", default="creator_analytics_browser")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for f in FIELDS:
        v = getattr(args, f)
        if v is None:
            continue
        rows.append({"captured_at": now, "platform": "linkedin", "post_id": None,
                     "metric_name": f"profile_{f}", "metric_value": v,
                     "metadata": {"source": args.source, "note": args.note, "manual": True}})
    if not rows:
        print("[FAIL] ни одной метрики не передано"); return 1
    if args.dry_run:
        for r in rows:
            print(f"[DRY] {r['metric_name']}={r['metric_value']}")
        return 0
    r = requests.post(f"{SUPABASE_URL}/rest/v1/metrics_snapshots", json=rows, headers=SB_HEADERS, timeout=20)
    r.raise_for_status()
    print(f"[OK] linkedin snapshot: {len(rows)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
