#!/usr/bin/env python3
"""
G_F weekly report — превращает данные за окно в digest для Дениса.

Контракт (см. docs/MODULE_F_DESIGN.md, секция «Schema adaptation 2026-05-19»):
  1. Окно по умолчанию — последние 30 дней (rolling).
     --week YYYY-Www → конкретная ISO-неделя.
     --days N → произвольное окно последних N дней.
  2. SELECT kpi_daily WHERE day in [period_start, period_end)
     + аналогичные агрегаты за предыдущий равный период для %-change.
  3. SELECT posts (status=published) опубликованные в окне → top-5 + flop-5
     по composite score. Реальная схема metrics_snapshots — long-form
     (metric_name/metric_value), агрегируем по post_id+metric_name (MAX от
     последнего snapshot'а). Composite score:
        views * (likes + comments + favorites + 1).
  4. SELECT insights WHERE created_at в окне.
  5. SELECT COUNT(topics) GROUP BY status.
  6. GPT-4 → markdown digest по строгому шаблону (русский, voice Дениса).
  7. INSERT в weekly_reports (если не --no-insert и не --dry-run).
  8. TG send в DM Дениса (если не --no-tg и не --dry-run).

Usage:
    python g_f_weekly_report.py --once                 # последние 30 дней
    python g_f_weekly_report.py --days 7               # последние 7 дней
    python g_f_weekly_report.py --week 2026-W20        # конкретная ISO-неделя
    python g_f_weekly_report.py --once --dry-run       # печать payload + skip OpenAI/TG/DB
    python g_f_weekly_report.py --once --no-insert     # OpenAI зовём, в БД не пишем
    python g_f_weekly_report.py --once --no-tg         # в БД пишем, в TG не шлём

Env (.env или env vars):
    SUPABASE_GENESIS_URL
    SUPABASE_GENESIS_SERVICE_KEY     (или SUPABASE_SERVICE_KEY)
    OPENAI_API_KEY
    TELEGRAM_BOT_TOKEN                — bot который пишет Денису
    TELEGRAM_DENIS_CHAT_ID            — default 1357650155

Зависимости: только stdlib (urllib).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Any
import urllib.request
import urllib.error
import urllib.parse


# --- env load ---
def _load_env_files():
    candidates = [
        Path.home() / ".local" / "bin" / ".g_f_env",
        Path.home() / ".local" / "bin" / ".g_e_env",
        Path.home() / ".local" / "bin" / ".g_d_env",
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
OPENAI_MODEL = os.environ.get("OPENAI_REPORT_MODEL", "gpt-4.1")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_DENIS_CHAT_ID = os.environ.get("TELEGRAM_DENIS_CHAT_ID", "1357650155")


# ============== Supabase helpers ==============

def sb_get(path: str, params: dict | None = None) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=".=():,*&")
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
        "Prefer": "return=representation,resolution=merge-duplicates",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# ============== Telegram helpers ==============

def tg_send(text: str, chat_id: str | int, parse_mode: str | None = None) -> dict:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": str(chat_id),
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def tg_alert_safe(text: str) -> None:
    """Best-effort TG alert. Не падает если TG недоступен."""
    if not TELEGRAM_BOT_TOKEN:
        print(f"[alert-skip-no-token] {text}", file=sys.stderr)
        return
    try:
        tg_send(text, TELEGRAM_DENIS_CHAT_ID)
    except Exception as e:
        print(f"[alert-fail] {e}: {text}", file=sys.stderr)


# ============== Week math ==============

def parse_week(week_iso: str) -> tuple[date, date, str]:
    """'2026-W20' → (period_start_mon, period_end_next_mon, label)."""
    y, w = week_iso.split("-W")
    monday = date.fromisocalendar(int(y), int(w), 1)
    next_monday = monday + timedelta(days=7)
    return monday, next_monday, week_iso


def previous_full_week() -> tuple[date, date, str]:
    """Прошлая полная ISO-неделя относительно сегодня."""
    today = datetime.now(timezone.utc).date()
    # Текущий понедельник
    current_monday = today - timedelta(days=today.weekday())
    last_monday = current_monday - timedelta(days=7)
    y, w, _ = last_monday.isocalendar()
    return last_monday, current_monday, f"{y}-W{w:02d}"


def last_n_days(n: int) -> tuple[date, date, str]:
    """Rolling окно последних N дней. period_end эксклюзивный (= завтра, чтобы
    включить сегодня), label вида '2026-05-19_last30d'."""
    today = datetime.now(timezone.utc).date()
    period_end = today + timedelta(days=1)
    period_start = period_end - timedelta(days=n)
    return period_start, period_end, f"{today.isoformat()}_last{n}d"


# ============== Data fetchers ==============

def fetch_kpi_window(start: date, end: date) -> list[dict]:
    """SELECT kpi_daily WHERE day in [start, end)."""
    return sb_get("kpi_daily", {
        "select": "day,platform,metric_name,total,avg,n",
        "day": f"gte.{start.isoformat()}",
        "and": f"(day.lt.{end.isoformat()})",
        "order": "day.asc",
        "limit": "5000",
    })


def fetch_posts_window(start: date, end: date) -> list[dict]:
    """Posts published в окне."""
    start_iso = datetime(start.year, start.month, start.day, tzinfo=timezone.utc).isoformat()
    end_iso = datetime(end.year, end.month, end.day, tzinfo=timezone.utc).isoformat()
    return sb_get("posts", {
        "select": "id,topic_id,platform,lang,title,external_url,published_at,prompt_version,ab_variant",
        "published_at": f"gte.{start_iso}",
        "and": f"(published_at.lt.{end_iso})",
        "status": "eq.published",
        "order": "published_at.desc",
        "limit": "1000",
    })


def fetch_metrics_for_posts(post_ids: list[str]) -> dict[str, dict[str, float]]:
    """Берём last value per (post_id, metric_name)."""
    if not post_ids:
        return {}
    out: dict[str, dict[str, float]] = defaultdict(dict)
    # PostgREST: post_id=in.(uuid1,uuid2,...)
    chunk_size = 100
    for i in range(0, len(post_ids), chunk_size):
        chunk = post_ids[i:i + chunk_size]
        in_clause = "(" + ",".join(chunk) + ")"
        rows = sb_get("metrics_snapshots", {
            "select": "post_id,metric_name,metric_value,captured_at",
            "post_id": f"in.{in_clause}",
            "order": "captured_at.desc",
            "limit": "10000",
        })
        for r in rows:
            pid = r["post_id"]
            name = r["metric_name"]
            # last (descending order — первый встреченный = последний по времени)
            if name not in out[pid]:
                out[pid][name] = float(r["metric_value"])
    return out


def fetch_insights_window(start: date, end: date) -> list[dict]:
    start_iso = datetime(start.year, start.month, start.day, tzinfo=timezone.utc).isoformat()
    end_iso = datetime(end.year, end.month, end.day, tzinfo=timezone.utc).isoformat()
    return sb_get("insights", {
        "select": "id,week_iso,insight_text,category,proposed_change,status,created_at",
        "created_at": f"gte.{start_iso}",
        "and": f"(created_at.lt.{end_iso})",
        "order": "created_at.desc",
        "limit": "100",
    })


def fetch_topics_counts() -> dict[str, int]:
    """COUNT(topics) GROUP BY status — берём через select count и group."""
    # PostgREST не умеет нативный GROUP BY count без RPC. Просто читаем status поле.
    rows = sb_get("topics", {
        "select": "status",
        "limit": "10000",
    })
    return dict(Counter(r["status"] for r in rows if r.get("status")))


def fetch_queued_topics(limit: int = 10) -> list[dict]:
    return sb_get("topics", {
        "select": "title_ru,title_en,topic_category,hype_score,priority,planned_for",
        "status": "eq.queued",
        "order": "priority.desc,hype_score.desc",
        "limit": str(limit),
    })


# ============== Scoring ==============

def composite_score(metrics: dict[str, float]) -> float:
    """Composite score, адаптированный под реальную схему metrics_snapshots
    (long-form: metric_name/metric_value, агрегация MAX по post_id+metric_name).

    Формула:  views * (likes + comments + favorites + 1)

    Реальные metric_name в БД (verified 2026-05-19): view, like, comment,
    favorite. CTR/impressions/clicks Module C пока не пишет — оставляем
    обратно-совместимые алиасы на будущее.

    Алиасы:
      views: view / views / impressions / pageviews
      engagement: like / likes / comment / comments / favorite / save /
                  share / repost
    """
    def m(*names: str) -> float:
        for n in names:
            v = metrics.get(n)
            if v is not None:
                return float(v)
        return 0.0

    views = m("view", "views", "impressions", "pageviews")
    likes = m("like", "likes")
    comments = m("comment", "comments")
    favorites = m("favorite", "save", "share", "repost")
    engagement = likes + comments + favorites
    return views * (engagement + 1.0)


def score_posts(posts: list[dict], metrics_map: dict[str, dict[str, float]]) -> list[dict]:
    scored = []
    for p in posts:
        m = metrics_map.get(p["id"], {})
        s = composite_score(m)
        scored.append({
            **p,
            "metrics": m,
            "score": round(s, 3),
        })
    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored


# ============== KPI aggregates ==============

def kpi_totals(kpi_rows: list[dict]) -> dict[str, float]:
    """Свод totals per (platform, metric_name)."""
    out: dict[str, float] = defaultdict(float)
    for r in kpi_rows:
        key = f"{r['platform']}.{r['metric_name']}"
        out[key] += float(r["total"] or 0)
    return dict(out)


def pct_change(curr: float, prev: float) -> float | None:
    if prev <= 0:
        return None
    return round((curr - prev) / prev * 100.0, 1)


# ============== Prompt building ==============

SYSTEM_PROMPT = """Ты — Денис Шохирев, Enterprise AI архитектор, основатель DennisCraft AI Studio. Пишешь себе в Telegram weekly-отчёт по своему контент-проекту Genesis Content OS. Голос: краткий, точный, без воды, без эмодзи-спама, технарь, ирония допустима но аккуратно.

Тебе дают JSON с реальными данными за неделю. Твоя задача — собрать markdown-digest строго по шаблону. Использовать ТОЛЬКО факты из данных, ничего не выдумывать.

Формат вывода (markdown, русский):

```
📊 Неделя {dates}
Опубликовано: {n_posts} постов, {n_videos} видео.

Метрики:
- LI: {X} impressions ({±Y}% vs прошлой)
- Blog: {X} visits ({±Y}%)
- YT: +{X} subs

Топ-3 контента:
1. {title} — {platform}, score {S}, {key_metric_breakdown}
2. ...

Провал недели:
- {title} — {platform}, score {S}, {почему просел}

Что изменилось:
- {prompt update / insight applied}

Предложения:
- {tool_suggestions из insights}

В очереди на следующую неделю:
- {next_topics}
```

Правила:
- Если данных по метрике 0 — пиши «—» вместо числа, не выдумывай.
- %-change пиши только если есть данные за прошлую неделю.
- «Что изменилось» — только реальные insights status='applied' или 'proposed' из payload.
- «Предложения» — берёшь suggestion из proposed_change.suggestion в insights.
- Длина: до 1500 символов чтобы влезло в одно TG-сообщение.
- Markdown — обычный, без HTML.
- Подписи platform: LI / Blog / TG / YT.
"""


def build_user_payload(week_iso: str, period_start: date, period_end: date,
                       kpi_curr: dict, kpi_prev: dict,
                       top_posts: list[dict], flop_posts: list[dict],
                       insights: list[dict], topics_counts: dict,
                       queued_topics: list[dict]) -> dict:
    def slim_post(p: dict) -> dict:
        return {
            "id": p["id"][:8],
            "platform": p.get("platform"),
            "title": (p.get("title") or "")[:120],
            "url": p.get("external_url"),
            "published_at": p.get("published_at"),
            "score": p["score"],
            "metrics": {k: v for k, v in (p.get("metrics") or {}).items() if v},
            "prompt_version": p.get("prompt_version"),
        }

    def slim_insight(i: dict) -> dict:
        pc = i.get("proposed_change") or {}
        return {
            "id": i["id"][:8],
            "status": i.get("status"),
            "category": i.get("category"),
            "insight_text": (i.get("insight_text") or "")[:300],
            "suggestion": (pc.get("suggestion") or "")[:300],
            "confidence": pc.get("confidence"),
        }

    # %-change на ключевых метриках
    keys_of_interest = [
        "linkedin.impressions", "linkedin.likes",
        "plausible.pageviews", "plausible.visitors", "ghost.views",
        "youtube.view", "youtube.subscribers",
        "telegram.view",
    ]
    deltas = {}
    for k in keys_of_interest:
        curr = kpi_curr.get(k, 0.0)
        prev = kpi_prev.get(k, 0.0)
        deltas[k] = {"curr": curr, "prev": prev, "pct": pct_change(curr, prev)}

    return {
        "week_iso": week_iso,
        "period_start": period_start.isoformat(),
        "period_end": (period_end - timedelta(days=1)).isoformat(),  # inclusive end
        "kpi_deltas": deltas,
        "kpi_totals_current_week": kpi_curr,
        "top_posts": [slim_post(p) for p in top_posts[:5]],
        "flop_posts": [slim_post(p) for p in flop_posts[:5]],
        "insights": [slim_insight(i) for i in insights],
        "topics_counts_by_status": topics_counts,
        "queue_for_next_week": [
            {"title": t.get("title_ru") or t.get("title_en"),
             "category": t.get("topic_category"),
             "priority": t.get("priority"),
             "hype": t.get("hype_score")}
            for t in queued_topics
        ],
    }


def call_openai(system: str, user_json: dict) -> str:
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                "Данные за неделю (JSON):\n\n"
                + json.dumps(user_json, indent=2, default=str, ensure_ascii=False)
                + "\n\nСобери markdown-отчёт строго по шаблону, ≤1500 символов."
            )},
        ],
        "temperature": 0.3,
        "max_tokens": 1500,
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
    return resp["choices"][0]["message"]["content"].strip()


# ============== Main ==============

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true",
                    help="один прогон с дефолтным окном (последние 30 дней)")
    ap.add_argument("--week", type=str, help="ISO week like 2026-W20")
    ap.add_argument("--days", type=int, default=None,
                    help="произвольное окно: последние N дней (rolling)")
    ap.add_argument("--dry-run", action="store_true",
                    help="печать payload, skip OpenAI/TG/INSERT")
    ap.add_argument("--no-insert", action="store_true",
                    help="OpenAI зовём, в weekly_reports не пишем")
    ap.add_argument("--no-tg", action="store_true",
                    help="не отправляем сообщение в TG")
    args = ap.parse_args()

    if not SUPABASE_KEY:
        msg = "Module F: Missing SUPABASE_GENESIS_SERVICE_KEY"
        print(msg, file=sys.stderr)
        tg_alert_safe(msg)
        return 2

    # === Окно ===
    # Приоритет: --week > --days > --once (default = 30 дней).
    if args.week:
        period_start, period_end, week_label = parse_week(args.week)
    elif args.days:
        period_start, period_end, week_label = last_n_days(args.days)
    else:
        # Default — последние 30 дней (rolling). Если нужна именно прошлая
        # ISO-неделя, явно укажи --week YYYY-Www.
        period_start, period_end, week_label = last_n_days(30)
    # Предыдущий равный по длине период — для %-change.
    window_days = (period_end - period_start).days
    prev_end = period_start
    prev_start = prev_end - timedelta(days=window_days)

    print(f"=== G_F weekly report | label {week_label} | "
          f"{period_start.isoformat()} → {period_end.isoformat()} "
          f"({window_days}d) ===")

    # === Fetch ===
    try:
        kpi_curr_rows = fetch_kpi_window(period_start, period_end)
        kpi_prev_rows = fetch_kpi_window(prev_start, prev_end)
        posts = fetch_posts_window(period_start, period_end)
        post_ids = [p["id"] for p in posts]
        metrics_map = fetch_metrics_for_posts(post_ids)
        insights = fetch_insights_window(period_start, period_end)
        topics_counts = fetch_topics_counts()
        queued = fetch_queued_topics(limit=10)
    except urllib.error.URLError as e:
        msg = f"Module F: Supabase недоступен — {e}"
        print(msg, file=sys.stderr)
        tg_alert_safe(msg)
        return 3
    except Exception as e:
        msg = f"Module F: fetch error — {type(e).__name__}: {e}"
        print(msg, file=sys.stderr)
        tg_alert_safe(msg)
        return 4

    kpi_curr = kpi_totals(kpi_curr_rows)
    kpi_prev = kpi_totals(kpi_prev_rows)
    scored = score_posts(posts, metrics_map)
    top = scored[:5]
    # flop: только посты с какими-то метриками (score > 0), затем берём 5 худших
    with_data = [s for s in scored if s["score"] > 0]
    flop = list(reversed(with_data))[:5] if with_data else []

    n_videos = sum(1 for p in posts if p.get("platform") == "youtube")
    n_posts_non_video = len(posts) - n_videos

    print(f"  kpi rows current week: {len(kpi_curr_rows)}, "
          f"prev week: {len(kpi_prev_rows)}")
    print(f"  posts in window: {len(posts)} "
          f"({n_posts_non_video} text, {n_videos} video)")
    print(f"  posts with metrics: {len(with_data)}")
    print(f"  insights in window: {len(insights)}")
    print(f"  topics by status: {topics_counts}")
    print(f"  queued ahead: {len(queued)}")

    payload = build_user_payload(
        week_label, period_start, period_end,
        kpi_curr, kpi_prev,
        top, flop, insights, topics_counts, queued,
    )

    if args.dry_run:
        print("\n--- DRY RUN: GPT system prompt (excerpt) ---")
        print(SYSTEM_PROMPT[:600] + ("..." if len(SYSTEM_PROMPT) > 600 else ""))
        print("\n--- DRY RUN: GPT user payload ---")
        print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
        print("\n[dry-run] OpenAI / Telegram / Supabase INSERT skipped.")
        return 0

    if not OPENAI_API_KEY:
        msg = "Module F: Missing OPENAI_API_KEY"
        print(msg, file=sys.stderr)
        tg_alert_safe(msg)
        return 5

    print(f"\n--- Calling OpenAI ({OPENAI_MODEL}) ---")
    t0 = time.time()
    try:
        markdown = call_openai(SYSTEM_PROMPT, payload)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        msg = f"Module F: OpenAI HTTP {e.code} — {body}"
        print(msg, file=sys.stderr)
        tg_alert_safe(msg)
        return 6
    except Exception as e:
        msg = f"Module F: OpenAI error — {type(e).__name__}: {e}"
        print(msg, file=sys.stderr)
        tg_alert_safe(msg)
        return 6
    print(f"  done in {time.time() - t0:.1f}s, {len(markdown)} chars")
    print("\n--- Generated digest ---")
    print(markdown)

    # === INSERT weekly_reports ===
    delivered: list[str] = []
    if not args.no_insert:
        try:
            res = sb_insert("weekly_reports", [{
                "week_iso": week_label,
                "period_start": period_start.isoformat(),
                "period_end": (period_end - timedelta(days=1)).isoformat(),
                "markdown_body": markdown,
                "kpi_summary": {
                    "totals_current": kpi_curr,
                    "totals_previous": kpi_prev,
                    "posts_count": len(posts),
                    "videos_count": n_videos,
                    "insights_count": len(insights),
                    "topics_by_status": topics_counts,
                    "top_post_ids": [p["id"] for p in top],
                    "flop_post_ids": [p["id"] for p in flop],
                },
                "delivered_to": [],
                "delivered_at": None,
            }])
            print(f"\n  inserted weekly_reports row: "
                  f"{res[0]['id'] if res else 'unknown'}")
            delivered.append("supabase")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            msg = f"Module F: weekly_reports INSERT failed HTTP {e.code} — {body}"
            print(msg, file=sys.stderr)
            tg_alert_safe(msg)
            # Не выходим — TG-доставка ещё имеет смысл
    else:
        print("\n--no-insert: skipping weekly_reports INSERT")

    # === TG send ===
    if not args.no_tg:
        if not TELEGRAM_BOT_TOKEN:
            msg = "Module F: TELEGRAM_BOT_TOKEN не задан — отчёт сохранён в БД, TG пропущен"
            print(msg, file=sys.stderr)
            tg_alert_safe(msg)
        else:
            try:
                tg_send(markdown, TELEGRAM_DENIS_CHAT_ID)
                print(f"\n  sent to TG chat {TELEGRAM_DENIS_CHAT_ID}")
                delivered.append(f"telegram_denis:{TELEGRAM_DENIS_CHAT_ID}")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")[:300]
                msg = f"Module F: TG send failed HTTP {e.code} — {body}"
                print(msg, file=sys.stderr)
                tg_alert_safe(msg)
            except Exception as e:
                msg = f"Module F: TG send error — {type(e).__name__}: {e}"
                print(msg, file=sys.stderr)
                tg_alert_safe(msg)
    else:
        print("\n--no-tg: skipping Telegram send")

    # === Update delivered_at if we both inserted and delivered ===
    if delivered and not args.no_insert:
        try:
            url = (f"{SUPABASE_URL}/rest/v1/weekly_reports"
                   f"?week_iso=eq.{week_label}")
            data = json.dumps({
                "delivered_to": delivered,
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="PATCH", headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
            })
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:
            print(f"  [warn] delivered_at update failed: {e}", file=sys.stderr)

    print("\n=== G_F done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
