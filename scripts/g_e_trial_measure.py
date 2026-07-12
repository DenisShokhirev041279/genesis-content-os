#!/usr/bin/env python3
"""
G_E trial-measure — замыкает эволюционный цикл: МЕРЯЕТ каждое авто-изменение
промпта против реальных метрик и ОТКАТЫВАЕТ провалы.

Зачем: g_e_auto_decision.py открывает PR, переписывающие промпты, а
g_e_post_merge_sync.py активирует новую версию в таблице `prompts`
(parent_version = baseline). Но до сих пор никто не проверял, стало ли
после изменения ЛУЧШЕ. Этот скрипт — недостающее звено: он превращает
«система себя переписывает» в «система себя переписывает, проверяет против
реальности и откатывает то, что не сработало». Дарвин, а не слепая правка.

Контракт:
  1. Найти «промпт-версии на испытании»: prompts.is_active=true,
     parent_version IS NOT NULL, approved_by LIKE 'github:%' | 'auto%'
     (т.е. активированы машиной, а не Денисом руками), у которых ещё нет
     вердикта (в связанном insight.proposed_change.trial_verdict).
  2. Для каждой версии V (module M, активирована в момент T, baseline = parent P):
       • target metric берётся из MODULE_METRIC (по умолчанию youtube/view).
       • metric_after = средняя «зрелая» метрика постов, опубликованных
         в окне [T, now] (возраст поста ≥ POST_MIN_AGE_DAYS — чтобы просмотры
         успели набраться и не занижали свежими постами).
       • metric_before = то же для baseline-окна [T-W, T] (жизнь под P).
  3. Вердикт:
       • n_after < MIN_SAMPLE и возраст(T) < TRIAL_MAX_DAYS → 'pending' (ждём данных).
       • n_after < MIN_SAMPLE и возраст(T) ≥ TRIAL_MAX_DAYS → 'kept_low_data'.
       • metric_after ≥ metric_before*(1-NOISE)             → 'kept' (лучше/нейтрально).
       • иначе (просадка глубже NOISE)                      → 'rolled_back'.
  4. При 'rolled_back' (только с --apply):
       a. Восстановить body baseline-версии P в prompts/<M>.md,
          ветка auto/prompt-revert-*, PR, авто-merge при явной просадке.
       b. В таблице prompts: деактивировать V, вставить новую версию
          (body = P.body, approved_by='auto:rollback', parent_version=V.version,
          rationale='rolled back: -X% <metric> over n posts').
  5. Записать вердикт + числа в insight.proposed_change (идемпотентность).

Usage:
    python g_e_trial_measure.py --once                 # измерить, вердикт в БД, откат НЕ применять
    python g_e_trial_measure.py --once --apply          # + реально откатывать провалы (PR+merge+prompts)
    python g_e_trial_measure.py --once --dry-run         # только печать, ничего не писать
    python g_e_trial_measure.py --module scenario_v2     # только один модуль

Env (.env или env vars):
    SUPABASE_GENESIS_URL
    SUPABASE_GENESIS_SERVICE_KEY   (или SUPABASE_SERVICE_KEY)
    GENESIS_REPO_PATH=~/Obsidian_AI_Brain/Projects/genesis-content-os
    TRIAL_WINDOW_DAYS=21    POST_MIN_AGE_DAYS=7   MIN_SAMPLE=4
    REGRESSION_NOISE=0.10   SEVERE_DROP=0.25      TRIAL_MAX_DAYS=45

Зависимости: stdlib (urllib) + gh/git CLI (только при --apply).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
import urllib.request
import urllib.parse


# ============== env ==============

def _load_env_files():
    for p in [
        Path.home() / "Obsidian_AI_Brain" / "Projects" / "ContentMachine" / "receiver" / ".env",
        Path.home() / ".local" / "bin" / ".g_e_env",
        Path.home() / ".local" / "bin" / ".g_d_env",
        Path.home() / "Obsidian_AI_Brain" / "Projects" / "genesis-content-os" / ".env",
    ]:
        if not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


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
REPO = Path(os.environ.get(
    "GENESIS_REPO_PATH",
    str(Path.home() / "Obsidian_AI_Brain" / "Projects" / "genesis-content-os"),
))
PROMPTS_DIR_RELATIVE = "prompts"

# --- тюнинг ---
TRIAL_WINDOW_DAYS = int(os.environ.get("TRIAL_WINDOW_DAYS", "21"))   # ширина before/after окна
POST_MIN_AGE_DAYS = int(os.environ.get("POST_MIN_AGE_DAYS", "7"))    # пост «зрелый» после N дней
MIN_SAMPLE = int(os.environ.get("MIN_SAMPLE", "4"))                  # мин. постов на каждую сторону
REGRESSION_NOISE = float(os.environ.get("REGRESSION_NOISE", "0.10"))  # <10% просадки = шум → keep
SEVERE_DROP = float(os.environ.get("SEVERE_DROP", "0.25"))          # ≥25% просадки → авто-merge реверта
TRIAL_MAX_DAYS = int(os.environ.get("TRIAL_MAX_DAYS", "45"))        # после — не ждём больше данных

# какую метрику мерить для каждого модуля промпта.
# Честное ограничение MVP: измерение на уровне ВЫХОДА платформы (все youtube-посты
# в окне), а не по posts.prompt_version (колонка пока не заполняется генератором).
# Если несколько промптов активированы в один день — они делят окно; вердикт
# относится к их совокупному эффекту. Точечную атрибуцию включит заполнение
# posts.prompt_version на генерации (следующий шаг).
MODULE_METRIC = {
    "scenario_v2": ("youtube", "view"),
    "scenario_v3": ("youtube", "view"),
    "topic_distiller": ("youtube", "view"),
    # content_factory_* пока с тонкими данными → мерить нечего, вернём insufficient_data
}

# Модули с ЗАМКНУТЫМ циклом: генератор тянет промпт с GitHub main в рантайме,
# значит merge реально доезжает до прода и метрику ПОСЛЕ можно приписать правке.
# Остальные держат промпт inline (см. AUDIT #1) — правка в прод не попадает,
# поэтому мерить их = мерить шум. Для них вердикт 'loop_open', без отката.
#
# Значение = дата замыкания (ISO) или None (замкнут всегда). Версии, активированные
# ДО замыкания, мерить нельзя (тогда правка ещё не доезжала в прод) → loop_open.
LOOP_CLOSED = {
    "topic_distiller": None,          # с рождения тянет .md с GitHub raw
    "scenario_v2": "2026-07-12T00:00:00+00:00",  # цикл замкнут 12.07 (YT Pilot fetch)
}

ENGAGEMENT_CRATER = float(os.environ.get("ENGAGEMENT_CRATER", "0.40"))  # >40% падения eng → флаг
COOLDOWN_DAYS = int(os.environ.get("TRIAL_COOLDOWN_DAYS", "14"))        # не дёргать модуль после отката


# ============== Supabase ==============

def sb_get(path: str, params: dict | None = None) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=".=():,*&")
    req = urllib.request.Request(url, headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def sb_patch(path: str, body: dict) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PATCH", headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json", "Prefer": "return=representation",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def sb_insert(table: str, rows: list[dict]) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    data = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json", "Prefer": "return=representation",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# ============== time helpers ==============

def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============== measurement core (чистые функции — тестируемы) ==============

def latest_value_per_post(snapshots: list[dict]) -> dict[str, float]:
    """Из снимков (уже отфильтрованных по platform+metric) → последнее значение
    на каждый post_id. Снимки могут идти в любом порядке."""
    best: dict[str, tuple[datetime, float]] = {}
    for s in snapshots:
        pid = s.get("post_id")
        if not pid:
            continue
        ts = _parse_ts(s["captured_at"])
        val = float(s["metric_value"])
        if pid not in best or ts > best[pid][0]:
            best[pid] = (ts, val)
    return {pid: v for pid, (_, v) in best.items()}


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _mature_ids(post_ids, published_at, latest_metric, start, end, min_age, now):
    out = []
    for pid in post_ids:
        pub = published_at.get(pid)
        if pub is None or pid not in latest_metric:
            continue
        if not (start <= pub < end):
            continue
        if (now - pub) < min_age:
            continue  # слишком свежий — метрика ещё не вызрела
        out.append(pid)
    return out


def window_stats(post_ids, published_at, latest_metric,
                 start, end, min_age, now) -> dict:
    """Статистика зрелых постов окна [start,end): mean, median, n.
    Медиана — устойчива к вирусным выбросам (один залетевший ролик не
    красит провальную неделю)."""
    ids = _mature_ids(post_ids, published_at, latest_metric, start, end, min_age, now)
    vals = [latest_metric[p] for p in ids]
    if not vals:
        return {"mean": 0.0, "median": 0.0, "n": 0}
    return {"mean": sum(vals) / len(vals), "median": _median(vals), "n": len(vals)}


def window_average(post_ids, published_at, latest_metric,
                   start, end, min_age, now) -> tuple[float, int]:
    """Обёртка над window_stats — (mean, n). Оставлена для совместимости."""
    st = window_stats(post_ids, published_at, latest_metric, start, end, min_age, now)
    return st["mean"], st["n"]


_EPOCH = datetime(2000, 1, 1, tzinfo=timezone.utc)


def version_windows(v_activated: str, parent_activated: Optional[str],
                    closure: Optional[str], now: datetime):
    """Окна атрибуции по РЕАЛЬНЫМ границам активности версий (не фикс-окно).

    before = пока была активна baseline-версия P: [P.activated, V.activated).
    after  = пока активна испытуемая V:            [V.activated, now).
    Оба пересекаются с closed-era (>= closure) — до замыкания цикла правка
    промпта в прод не доезжала, такие посты в атрибуцию не берём.
    Возвращает (before_start, before_end), (after_start, after_end).
    """
    v_act = _parse_ts(v_activated)
    p_act = _parse_ts(parent_activated) if parent_activated else _EPOCH
    clo = _parse_ts(closure) if closure else _EPOCH
    before = (max(p_act, clo), v_act)     # P активна до активации V
    after = (max(v_act, clo), now)        # V активна с активации до сейчас
    return before, after


def decide_verdict(before: float, n_before: int, after: float, n_after: int,
                   trial_age_days: float,
                   min_sample: int = MIN_SAMPLE,
                   noise: float = REGRESSION_NOISE,
                   severe: float = SEVERE_DROP,
                   max_days: int = TRIAL_MAX_DAYS) -> dict:
    """Чистое решение: kept | rolled_back | pending | kept_low_data | insufficient_baseline.
    Возвращает dict с verdict, severe(bool), change_pct, и человекочитаемым reason."""
    if n_before < min_sample:
        # нет базы для сравнения — не можем судить честно
        if trial_age_days >= max_days:
            return {"verdict": "kept_low_data", "severe": False, "change_pct": None,
                    "reason": f"baseline n={n_before}<{min_sample}, trial aged out → keep"}
        return {"verdict": "insufficient_baseline", "severe": False, "change_pct": None,
                "reason": f"baseline n={n_before}<{min_sample}, wait"}
    if n_after < min_sample:
        if trial_age_days >= max_days:
            return {"verdict": "kept_low_data", "severe": False, "change_pct": None,
                    "reason": f"after n={n_after}<{min_sample}, trial aged out → keep"}
        return {"verdict": "pending", "severe": False, "change_pct": None,
                "reason": f"after n={n_after}<{min_sample}, need more posts"}
    if before <= 0:
        return {"verdict": "kept_low_data", "severe": False, "change_pct": None,
                "reason": "baseline avg is zero → cannot compute ratio, keep"}
    change = (after - before) / before  # +0.2 = +20%
    if change >= -noise:
        return {"verdict": "kept", "severe": False, "change_pct": round(change * 100, 1),
                "reason": f"after {after:.0f} vs before {before:.0f} ({change*100:+.1f}%) ≥ -{noise*100:.0f}%"}
    is_severe = change <= -severe
    return {"verdict": "rolled_back", "severe": is_severe, "change_pct": round(change * 100, 1),
            "reason": f"regression {change*100:+.1f}% (n_after={n_after}, n_before={n_before})"
                      + (" — SEVERE" if is_severe else "")}


# ============== data loading ==============

def fetch_trial_versions(module_filter: Optional[str]) -> list[dict]:
    """Активные версии, активированные машиной, с baseline-родителем."""
    rows = sb_get("prompts", {
        "select": "id,module,version,body,parent_version,activated_at,approved_by,is_active",
        "is_active": "eq.true",
        "parent_version": "not.is.null",
        "order": "activated_at.desc",
        "limit": "50",
    })
    out = []
    for r in rows:
        ab = r.get("approved_by") or ""
        if ab.startswith("auto:rollback"):
            continue  # анти-thrash: восстановленный откатом baseline не судим заново
        if not (ab.startswith("github:") or ab.startswith("auto")):
            continue  # активировано Денисом руками — не машинное испытание
        if not r.get("activated_at"):
            continue
        if module_filter and r["module"] != module_filter:
            continue
        out.append(r)
    return out


def fetch_parent_row(module: str, parent_version: str) -> Optional[dict]:
    rows = sb_get("prompts", {
        "select": "id,module,version,body,activated_at",
        "module": f"eq.{module}",
        "version": f"eq.{parent_version}",
        "limit": "1",
    })
    return rows[0] if rows else None


def find_insight_for_prompt(prompt_id: str) -> Optional[dict]:
    rows = sb_get("insights", {
        "select": "id,proposed_change",
        "proposed_change->>prompts_id": f"eq.{prompt_id}",
        "limit": "1",
    })
    return rows[0] if rows else None


def fetch_platform_posts(platform: str) -> dict[str, datetime]:
    """post_id → published_at для платформы (только опубликованные)."""
    rows = sb_get("posts", {
        "select": "id,published_at",
        "platform": f"eq.{platform}",
        "published_at": "not.is.null",
        "order": "published_at.desc",
        "limit": "1000",
    })
    return {r["id"]: _parse_ts(r["published_at"]) for r in rows if r.get("published_at")}


def fetch_metric_snapshots(platform: str, metric: str, post_ids: list[str]) -> list[dict]:
    """Все снимки нужной метрики для набора постов (батчами по post_id)."""
    out: list[dict] = []
    B = 40
    for i in range(0, len(post_ids), B):
        chunk = post_ids[i:i + B]
        inlist = "(" + ",".join(chunk) + ")"
        rows = sb_get("metrics_snapshots", {
            "select": "post_id,metric_value,captured_at",
            "platform": f"eq.{platform}",
            "metric_name": f"eq.{metric}",
            "post_id": f"in.{inlist}",
            "limit": "10000",
        })
        out.extend(rows)
    return out


# ============== rollback action (git + prompts table) ==============

def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=check)


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], cwd=REPO, capture_output=True, text=True, check=check)


def perform_rollback(trial: dict, parent: dict, measure: dict, verdict: dict) -> Optional[str]:
    """Восстановить baseline-промпт: PR с реверт-диффом + swap версий в prompts table."""
    module = trial["module"]
    prompt_file = f"{module}.md"
    prompt_path = REPO / PROMPTS_DIR_RELATIVE / prompt_file
    if not prompt_path.exists():
        print(f"    [rollback] {prompt_file} нет в репо — пропуск git-части, делаю только swap в БД")
    else:
        prev = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        branch = f"auto/prompt-revert-{module}-{trial['version'].replace('.', '')}"
        try:
            git("checkout", "main", check=False)
            git("fetch", "origin", "main", check=False)
            git("reset", "--hard", "origin/main", check=False)
            git("checkout", "-b", branch)
            prompt_path.write_text(parent["body"])
            git("add", str(prompt_path.relative_to(REPO)))
            pct = verdict.get("change_pct")
            msg = (f"revert({module}): auto-rollback v{trial['version']} → v{parent['version']}\n\n"
                   f"Measured regression {pct:+}% on {measure['platform']}/{measure['metric']} "
                   f"(after {measure['after']:.0f} vs before {measure['before']:.0f}, "
                   f"n_after={measure['n_after']}, n_before={measure['n_before']}).\n"
                   f"Prompt change did not improve reality → restoring baseline.\n\n"
                   f"Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>")
            git("commit", "-m", msg)
            git("push", "-u", "origin", branch)
            title = f"revert/prompt — {module} v{trial['version']} underperformed ({pct:+}%)"
            body = _rollback_pr_body(trial, parent, measure, verdict)
            r = gh("pr", "create", "--title", title, "--body", body,
                   "--base", "main", "--head", branch, check=False)
            pr_url = (r.stdout.strip().splitlines() or [None])[-1] if r.stdout.strip() else None
            merged = False
            if pr_url and verdict.get("severe"):
                mr = gh("pr", "merge", pr_url, "--squash", "--delete-branch", check=False)
                merged = mr.returncode == 0
                print(f"    [rollback] PR {pr_url} — {'✓ MERGED (severe)' if merged else 'merge FAILED: ' + mr.stderr[:100]}")
            elif pr_url:
                print(f"    [rollback] PR открыт (не severe, оставлен на ревью Дениса): {pr_url}")
            # Прод замкнутого модуля = .md на main. Таблицу двигаем ТОЛЬКО когда
            # реверт реально смержен — иначе БД и прод разойдутся. Мягкий откат =
            # PR ждёт ревью, прод и таблица не меняются.
            if merged:
                _swap_prompts_table(trial, parent, measure, verdict)
            return pr_url
        finally:
            git("checkout", prev or "main", check=False)
    # ветка без git-файла (чистый DB-модуль): git-гейта нет, swap как есть
    _swap_prompts_table(trial, parent, measure, verdict)
    return None


def _rollback_pr_body(trial, parent, measure, verdict) -> str:
    return (
        f"## Auto-rollback — measured regression\n\n"
        f"Module `{trial['module']}` version **v{trial['version']}** was activated by the "
        f"self-improvement loop and measured against reality. It **underperformed** its "
        f"baseline **v{parent['version']}**, so this PR restores the baseline prompt body.\n\n"
        f"| | before (v{parent['version']}) | after (v{trial['version']}) |\n"
        f"|---|---|---|\n"
        f"| {measure['platform']}/{measure['metric']} avg | {measure['before']:.0f} | {measure['after']:.0f} |\n"
        f"| posts (n) | {measure['n_before']} | {measure['n_after']} |\n\n"
        f"**Change:** {verdict['change_pct']:+}%  ·  threshold: -{int(REGRESSION_NOISE*100)}% "
        f"·  severe (auto-merge): {'yes' if verdict.get('severe') else 'no'}\n\n"
        f"> Это не человек решил откатить. Система измерила собственное изменение против "
        f"реальных просмотров и откатила то, что не сработало.\n"
    )


def _swap_prompts_table(trial, parent, measure, verdict):
    now_iso = _now().isoformat()
    # деактивировать провалившуюся версию
    sb_patch(f"prompts?id=eq.{trial['id']}", {"is_active": False, "deactivated_at": now_iso})
    # вставить восстановленную baseline как новую версию (честная родословная)
    new_ver = _bump_patch(trial["version"])
    sb_insert("prompts", [{
        "module": trial["module"], "version": new_ver, "body": parent["body"],
        "rationale": f"auto-rollback: v{trial['version']} gave {verdict['change_pct']:+}% "
                     f"{measure['metric']} vs baseline → restored v{parent['version']} body",
        "is_active": True, "activated_at": now_iso,
        "approved_by": "auto:rollback", "parent_version": trial["version"],
    }])


def _bump_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return version + ".1"
    parts[2] = str(int(parts[2]) + 1)
    return ".".join(parts)


# ============== per-trial pipeline ==============

def _engagement_per_post(likes, comments, views) -> dict[str, float]:
    """(like+comment)/view на каждый пост — качество вовлечения, не только охват."""
    out = {}
    for pid, v in views.items():
        if v and v > 0:
            out[pid] = (likes.get(pid, 0) + comments.get(pid, 0)) / v
    return out


def measure_trial(trial: dict) -> dict:
    module = trial["module"]
    mm = MODULE_METRIC.get(module)
    T = _parse_ts(trial["activated_at"])
    now = _now()
    trial_age_days = (now - T).total_seconds() / 86400.0

    if mm is None:
        return {"trial": trial, "measure": None,
                "verdict": {"verdict": "insufficient_data", "severe": False, "change_pct": None,
                            "reason": f"no metric mapping for module {module}"},
                "trial_age_days": trial_age_days}

    # loop-гард (вывод №1): по РАЗОМКНУТЫМ модулям правка в прод не доезжает →
    # мерить нельзя. Pre-closure версии сами дадут insufficient_baseline ниже
    # (их baseline-окно ∩ closed-era пустое) — отдельная ветка не нужна.
    if module not in LOOP_CLOSED:
        return {"trial": trial,
                "measure": {"platform": mm[0], "metric": mm[1], "loop": "open"},
                "verdict": {"verdict": "loop_open", "severe": False, "change_pct": None,
                            "reason": f"{module}: генератор держит промпт inline — "
                                      f"merge не доезжает до прода (см. AUDIT #1)"},
                "trial_age_days": trial_age_days}

    platform, metric = mm
    # Точечная атрибуция по РЕАЛЬНЫМ окнам активности версий ∩ closed-era
    # (не фикс-окно ±N дней): каждый пост принадлежит версии, активной в момент
    # его публикации. Причинно-корректно для последовательных версий модуля.
    parent = fetch_parent_row(module, trial["parent_version"])
    closure = LOOP_CLOSED.get(module)
    (b_start, b_end), (a_start, a_end) = version_windows(
        trial["activated_at"], (parent or {}).get("activated_at"), closure, now)
    published = fetch_platform_posts(platform)
    post_ids = list(published.keys())
    min_age = timedelta(days=POST_MIN_AGE_DAYS)

    # первичный сигнал: медиана метрики (робастна к вирусным выбросам)
    views = latest_value_per_post(fetch_metric_snapshots(platform, metric, post_ids))
    b = window_stats(post_ids, published, views, b_start, b_end, min_age, now)
    a = window_stats(post_ids, published, views, a_start, a_end, min_age, now)
    verdict = decide_verdict(b["median"], b["n"], a["median"], a["n"], trial_age_days)

    # вторичный сигнал: engagement (like+comment)/view — не «просмотры любой ценой»
    eng_change = None
    if platform == "youtube":
        likes = latest_value_per_post(fetch_metric_snapshots(platform, "like", post_ids))
        comments = latest_value_per_post(fetch_metric_snapshots(platform, "comment", post_ids))
        eng = _engagement_per_post(likes, comments, views)
        eb = window_stats(post_ids, published, eng, b_start, b_end, min_age, now)
        ea = window_stats(post_ids, published, eng, a_start, a_end, min_age, now)
        if eb["median"] > 0 and eb["n"] >= MIN_SAMPLE and ea["n"] >= MIN_SAMPLE:
            eng_change = (ea["median"] - eb["median"]) / eb["median"]
            # если охват «вырос», но вовлечённость обвалилась — это не победа
            if verdict["verdict"] == "kept" and eng_change <= -ENGAGEMENT_CRATER:
                verdict = {"verdict": "kept_watch", "severe": False,
                           "change_pct": verdict["change_pct"],
                           "reason": verdict["reason"] +
                                     f" — НО engagement {eng_change*100:+.0f}% (обвал) → под наблюдением"}

    measure = {"platform": platform, "metric": metric, "loop": "closed",
               "before": b["median"], "after": a["median"],
               "before_mean": round(b["mean"], 1), "after_mean": round(a["mean"], 1),
               "n_before": b["n"], "n_after": a["n"],
               "engagement_change_pct": None if eng_change is None else round(eng_change * 100, 1),
               "attribution": "version-activation-windows",
               "before_window": [b_start.isoformat(), b_end.isoformat()],
               "after_window": [a_start.isoformat(), a_end.isoformat()],
               "post_min_age_days": POST_MIN_AGE_DAYS, "stat": "median"}
    return {"trial": trial, "measure": measure, "verdict": verdict, "trial_age_days": trial_age_days}


def persist_verdict(trial: dict, result: dict, insight: Optional[dict]):
    """Записать вердикт+числа в связанный insight (идемпотентность + timeline)."""
    if not insight:
        return
    pc = insight.get("proposed_change") or {}
    m = result["measure"] or {}
    sb_patch(f"insights?id=eq.{insight['id']}", {
        "proposed_change": {
            **pc,
            "trial_verdict": result["verdict"]["verdict"],
            "trial_change_pct": result["verdict"].get("change_pct"),
            "trial_measure": m,
            "trial_measured_at": _now().isoformat(),
        },
    })


TERMINAL_VERDICTS = {"kept", "rolled_back", "kept_low_data", "insufficient_data", "loop_open"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="ничего не писать, только печать")
    ap.add_argument("--apply", action="store_true", help="реально откатывать провалы (git PR + prompts swap)")
    ap.add_argument("--module", type=str, help="ограничиться одним модулем")
    ap.add_argument("--force", action="store_true", help="перемерить даже те, у кого уже есть терминальный вердикт")
    args = ap.parse_args()

    if not SUPABASE_KEY:
        sys.exit("Missing SUPABASE_GENESIS_SERVICE_KEY")

    trials = fetch_trial_versions(args.module)
    print(f"=== G_E trial-measure | {len(trials)} машинных промпт-версий на испытании ===")
    if not trials:
        print("  нечего мерить (нет машинно-активированных версий с baseline)")
        return 0

    rolled, kept, pending = 0, 0, 0
    for t in trials:
        insight = find_insight_for_prompt(t["id"])
        prev_verdict = ((insight or {}).get("proposed_change") or {}).get("trial_verdict")
        if prev_verdict in TERMINAL_VERDICTS and not args.force:
            print(f"  [skip] {t['module']} v{t['version']}: уже '{prev_verdict}'")
            continue

        result = measure_trial(t)
        v = result["verdict"]
        m = result["measure"]
        line = (f"  {t['module']} v{t['version']} (baseline v{t['parent_version']}, "
                f"age {result['trial_age_days']:.0f}d) → {v['verdict'].upper()}")
        if m and "after" in m:
            line += (f" | {m['metric']} (median) after={m['after']:.0f}(n{m['n_after']}) "
                     f"before={m['before']:.0f}(n{m['n_before']})")
            if m.get("engagement_change_pct") is not None:
                line += f" · eng {m['engagement_change_pct']:+.0f}%"
        print(line)
        print(f"     reason: {v['reason']}")

        if v["verdict"] == "rolled_back":
            rolled += 1
        elif v["verdict"] == "kept":
            kept += 1
        else:
            pending += 1

        if args.dry_run:
            continue

        parent = fetch_parent_row(t["module"], t["parent_version"])
        if v["verdict"] == "rolled_back":
            applied = False
            if args.apply and parent:
                perform_rollback(t, parent, m, v)
                applied = True
            elif args.apply and not parent:
                print("     [rollback] baseline row не найден — пропуск")
            else:
                print("     [rollback] измерено, но --apply не задан → откат НЕ выполнен")
            # не терминалим, пока откат реально не выполнен — иначе cron пропустит
            if not applied:
                v["verdict"] = "rollback_pending"

        persist_verdict(t, result, insight)

    print(f"\n=== итог: rolled_back={rolled} kept={kept} pending/other={pending} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
