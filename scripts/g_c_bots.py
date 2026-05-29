#!/usr/bin/env python3
"""
G_C Bots ingestor — daily DAU/WAU/MAU snapshots для Telegram ботов Дениса.

Поддерживаемые боты:
  - kleshn_junior     — Клешня Младший (Supabase denniscraft-studio: ifbyopnkhyjoubpykhdl)
  - consultant_bot    — @ger_dennis_ai_consultant_bot (SQLite на Koyeb, см. RECON ниже)

Метрики (platform='kleshn_junior' | 'consultant_bot', post_id=NULL):
  - bot_dau              — unique users с активностью за 24h
  - bot_wau              — unique users за 7d
  - bot_mau              — unique users за 30d
  - bot_messages_24h     — общее число событий активности за 24h
  - bot_total_users      — все когда-либо зарегистрированные
  - bot_new_users_24h    — новые регистрации за 24h
  - bot_retention_d7     — % пользователей с активностью обоих в day 0 и day 7 (rolling)

Запуск:
    python g_c_bots.py --once --bot all
    python g_c_bots.py --once --bot kleshn --dry-run
    python g_c_bots.py --once --bot consultant --dry-run

Окружение:
    SUPABASE_URL, SUPABASE_SERVICE_KEY   — Genesis Supabase (target, ingest)
    DENNISCRAFT_SUPABASE_URL             — https://ifbyopnkhyjoubpykhdl.supabase.co
    DENNISCRAFT_SUPABASE_KEY             — service_role key denniscraft-studio (READ ONLY usage)

RECON (см. отчёт):
    consultant_bot хранит данные в SQLite внутри Koyeb контейнера (data/bot.db).
    Извне недоступно без модификации бота. Source = "unavailable" → skip с warning.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Optional

import requests


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

DENNIS_URL = os.environ.get("DENNISCRAFT_SUPABASE_URL", "https://ifbyopnkhyjoubpykhdl.supabase.co")
DENNIS_KEY = os.environ.get("DENNISCRAFT_SUPABASE_KEY", "")

SB_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}


def insert_metric(platform: str, name: str, value: float, metadata: dict, dry_run: bool) -> None:
    payload = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "post_id": None,
        "metric_name": name,
        "metric_value": value,
        "metadata": metadata,
    }
    if dry_run:
        print(f"[DRY] {platform}.{name}={value} meta={metadata}")
        return
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/metrics_snapshots",
        json=payload,
        headers=SB_HEADERS,
        timeout=10,
    )
    r.raise_for_status()
    print(f"[OK]  {platform}.{name}={value}")


# ---------------------------------------------------------------------------
# Клешня Младший — Supabase denniscraft-studio
# ---------------------------------------------------------------------------

def kleshn_rpc_select(query: str) -> list[dict]:
    """Выполняет SQL через PostgREST RPC (read-only) через select-эндпойнты.

    PostgREST не даёт raw SQL — собираем через несколько endpoints.
    """
    raise NotImplementedError("используются прямые requests к /rest/v1")


def _dennis_get(table: str, params: dict) -> tuple[list[dict], int]:
    """GET /rest/v1/<table> + Prefer: count=exact → (rows, total)."""
    headers = {
        "apikey": DENNIS_KEY,
        "Authorization": f"Bearer {DENNIS_KEY}",
        "Prefer": "count=exact",
    }
    r = requests.get(f"{DENNIS_URL}/rest/v1/{table}", params=params, headers=headers, timeout=15)
    r.raise_for_status()
    cr = r.headers.get("Content-Range", "0/0")
    total = int(cr.split("/")[-1])
    return r.json(), total


def kleshn_collect(dry_run: bool) -> int:
    if not DENNIS_KEY:
        print("[WARN] DENNISCRAFT_SUPABASE_KEY not set — skip kleshn_junior", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    iso_24h = (now.replace(microsecond=0)).isoformat()  # для logs
    cutoff_24h = (now.timestamp() - 24 * 3600)
    cutoff_7d = (now.timestamp() - 7 * 24 * 3600)
    cutoff_30d = (now.timestamp() - 30 * 24 * 3600)

    def _iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    # total users
    _, total_users = _dennis_get("users", {"select": "telegram_id", "limit": "1"})

    # new users за 24h
    _, new_users_24h = _dennis_get(
        "users",
        {"select": "telegram_id", "created_at": f"gte.{_iso(cutoff_24h)}", "limit": "1"},
    )

    # активность: DAU/WAU/MAU/messages — через analyses (created_at + user_id)
    # PostgREST не умеет COUNT(DISTINCT) — тянем user_id и считаем в Python.
    rows_24h, msgs_24h = _dennis_get(
        "analyses",
        {"select": "user_id,created_at", "created_at": f"gte.{_iso(cutoff_24h)}", "limit": "10000"},
    )
    dau = len({r["user_id"] for r in rows_24h})

    rows_7d, _ = _dennis_get(
        "analyses",
        {"select": "user_id", "created_at": f"gte.{_iso(cutoff_7d)}", "limit": "20000"},
    )
    wau = len({r["user_id"] for r in rows_7d})

    rows_30d, _ = _dennis_get(
        "analyses",
        {"select": "user_id", "created_at": f"gte.{_iso(cutoff_30d)}", "limit": "50000"},
    )
    mau = len({r["user_id"] for r in rows_30d})

    # retention D7: cohort = users created at day-7±12h, active at day 0
    cohort_start = (now.timestamp() - 8 * 24 * 3600)
    cohort_end = (now.timestamp() - 6 * 24 * 3600)
    cohort_rows, _ = _dennis_get(
        "users",
        {
            "select": "telegram_id",
            "created_at": f"gte.{_iso(cohort_start)}",
            "limit": "10000",
        },
    )
    cohort_rows_filtered = [
        c for c in cohort_rows
        # фильтр по верхней границе на клиенте: PostgREST лимиты по одной колонке несложно ставить через &lt
    ]
    # дублируем верхнюю границу через отдельный фильтр
    cohort_rows2, _ = _dennis_get(
        "users",
        {
            "select": "telegram_id,created_at",
            "created_at": f"gte.{_iso(cohort_start)}",
            "limit": "10000",
        },
    )
    cohort_ids = {
        c["telegram_id"] for c in cohort_rows2
        if c.get("created_at") and c["created_at"] <= _iso(cohort_end)
    }
    if cohort_ids:
        active_d7 = cohort_ids & {r["user_id"] for r in rows_24h}
        retention_d7 = round(100 * len(active_d7) / len(cohort_ids), 2)
    else:
        retention_d7 = 0.0

    base_meta = {
        "source": "supabase",
        "project": "denniscraft-studio",
        "supabase_project_id": "ifbyopnkhyjoubpykhdl",
        "captured_at_iso": iso_24h,
    }

    platform = "kleshn_junior"
    insert_metric(platform, "bot_dau", dau, base_meta, dry_run)
    insert_metric(platform, "bot_wau", wau, base_meta, dry_run)
    insert_metric(platform, "bot_mau", mau, base_meta, dry_run)
    insert_metric(platform, "bot_messages_24h", msgs_24h, base_meta, dry_run)
    insert_metric(platform, "bot_total_users", total_users, base_meta, dry_run)
    insert_metric(platform, "bot_new_users_24h", new_users_24h, base_meta, dry_run)
    insert_metric(
        platform,
        "bot_retention_d7",
        retention_d7,
        {**base_meta, "cohort_size": len(cohort_ids)},
        dry_run,
    )

    print(
        f"[SUMMARY] kleshn_junior: total={total_users} new24h={new_users_24h} "
        f"DAU={dau} WAU={wau} MAU={mau} msgs24h={msgs_24h} retentionD7={retention_d7}%"
    )
    return 0


# ---------------------------------------------------------------------------
# Консультант бот — SQLite на Koyeb (data/bot.db)
# ---------------------------------------------------------------------------

def consultant_collect(dry_run: bool) -> int:
    """Забирает метрики из /admin/metrics endpoint бота на Koyeb.

    Endpoint реализован в bot/admin_metrics.py (PR #1 merged 2026-05-24).
    Env vars (~/.local/bin/.g_c_env):
      CONSULTANT_BOT_URL=https://wild-luisa-shohirev-f0b7f26f.koyeb.app
      CONSULTANT_BOT_ADMIN_TOKEN=<token>
    """
    url = os.environ.get("CONSULTANT_BOT_URL", "").rstrip("/")
    token = os.environ.get("CONSULTANT_BOT_ADMIN_TOKEN", "")
    if not url or not token:
        print(
            "[SKIP] consultant_bot: CONSULTANT_BOT_URL/CONSULTANT_BOT_ADMIN_TOKEN не заданы",
            file=sys.stderr,
        )
        insert_metric("consultant_bot", "bot_data_source_available", 0,
                      {"reason": "env_not_set"}, dry_run)
        return 0

    try:
        r = requests.get(f"{url}/admin/metrics", params={"token": token}, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[ERR] consultant_bot HTTP fetch: {type(e).__name__}: {e}", file=sys.stderr)
        insert_metric("consultant_bot", "bot_data_source_available", 0,
                      {"reason": "http_fail", "error": str(e)[:120]}, dry_run)
        return 0

    meta = {"source": "admin_metrics_endpoint", "captured_at": data.get("captured_at")}
    insert_metric("consultant_bot", "bot_total_users", float(data.get("total_users", 0)), meta, dry_run)
    insert_metric("consultant_bot", "bot_dau", float(data.get("dau", 0)), meta, dry_run)
    insert_metric("consultant_bot", "bot_wau", float(data.get("wau", 0)), meta, dry_run)
    insert_metric("consultant_bot", "bot_mau", float(data.get("mau", 0)), meta, dry_run)
    insert_metric("consultant_bot", "bot_messages_24h", float(data.get("messages_24h", 0)), meta, dry_run)
    insert_metric("consultant_bot", "bot_data_source_available", 1, meta, dry_run)
    return 6


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(bot: str, dry_run: bool) -> int:
    rc = 0
    if bot in ("kleshn", "all"):
        try:
            kleshn_collect(dry_run)
        except Exception as e:
            print(f"[ERR] kleshn_junior: {e}", file=sys.stderr)
            rc = 2
    if bot in ("consultant", "all"):
        try:
            consultant_collect(dry_run)
        except Exception as e:
            print(f"[ERR] consultant_bot: {e}", file=sys.stderr)
            rc = 2
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single run (default)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bot", choices=["kleshn", "consultant", "all"], default="all")
    args = ap.parse_args()
    return run(bot=args.bot, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
