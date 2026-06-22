#!/usr/bin/env python3
"""
publish_hero.py — multi-channel publisher для героических видео Claudian.

Зоны:
  - Claudian: генерирует `<HERO>_FINAL_subs.mp4` + `<HERO>_PUBLICATION_PLAN.md`
  - publish_hero.py (этот скрипт): только публикует по plan + лог.
    Не трогает captions, не генерит, не делает subtitles.

Usage:
    # Dry-run (по умолчанию — показывает что бы сделал, не публикует):
    python publish_hero.py --hero ODIN --platforms instagram,telegram,youtube,ghost

    # Реальная публикация одной платформы:
    python publish_hero.py --hero ODIN --platforms instagram --publish

    # Все платформы (последовательно, без auto-delay — таймлайн T+60 etc делает n8n wrapper):
    python publish_hero.py --hero ODIN --platforms ghost,youtube,instagram,telegram --publish

Поддерживаемые платформы:
    ghost     — gerdennisai.com/blog (Admin API)
    youtube   — youtube_uploader.py CLI (OAuth token.pickle)
    instagram — receiver /instagram/upload (через fal.ai CDN)
    telegram  — Bot API sendVideo прямо
    facebook  — receiver /facebook/upload (нужен FB_PAGE_ACCESS_TOKEN)
    threads   — skeleton (tokens pending от Дениса)
    linkedin  — НЕ делает (approval-gate бот, отдельная зона)

Env vars (~/Obsidian_AI_Brain/Projects/MotionViral/.env):
    IG_ACCESS_TOKEN, IG_USER_ID
    META_APP_SECRET (для FB)
    FB_PAGE_ACCESS_TOKEN, FB_PAGE_ID  (если есть)
    THREADS_ACCESS_TOKEN, THREADS_USER_ID  (когда дадут)
    RECEIVER_SECRET (берём из receiver/.env)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


# ============== Paths & Config ==============

HOME = Path.home()
MOTIONVIRAL = HOME / "Obsidian_AI_Brain/Projects/MotionViral"
LOG_FILE = HOME / "Obsidian_AI_Brain/Projects/genesis-content-os/MULTI_CHANNEL_LOG.md"
RECEIVER_URL = os.environ.get("RECEIVER_BASE_URL", "http://localhost:8787")
RECEIVER_SECRET = os.environ.get(
    "RECEIVER_SECRET",
    "***REMOVED***",
)
TG_CHANNEL_ID = "-1002216661152"
LINKEDIN_SKIP_REASON = (
    "linkedin idет через approval-gate бот в TG (feedback_linkedin_approval_gate). "
    "Не публикуем напрямую."
)


def _load_env(path: Path) -> None:
    """Минимальный loader: KEY=VALUE строки → os.environ.setdefault."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


# Подгружаем .env'ы при импорте (можно и в main, но удобнее так)
_load_env(MOTIONVIRAL / ".env")
_load_env(HOME / "Obsidian_AI_Brain/Projects/ContentMachine/receiver/.env")


# ============== Plan parser ==============

# Map heading patterns → platform key
HEADING_TO_PLATFORM = {
    r"^##\s+1\.\s+YouTube": "youtube",
    r"^##\s+2\.\s+Instagram": "instagram",
    r"^##\s+3\.\s+Telegram": "telegram",
    r"^##\s+4\.\s+LinkedIn": "linkedin",
    r"^##\s+5\.\s+Facebook": "facebook",
    r"^##\s+6\.\s+Threads": "threads",
}


def parse_plan(plan_path: Path) -> dict[str, dict[str, str]]:
    """Разбивает MD на словарь {platform: {sub_heading: text_block}}.

    Например для Instagram returns:
        {"instagram": {"caption": "...", "pinned_comment": "..."}}

    Парсер ищет ## N. PlatformName, потом разбивает на ### Subheading,
    извлекает первый ```code block``` под каждым subheading (или весь текст
    если кода нет).
    """
    text = plan_path.read_text()
    lines = text.split("\n")

    sections: dict[str, dict[str, str]] = {}
    cur_platform: str | None = None
    cur_sub: str | None = None
    cur_buffer: list[str] = []
    in_code_block = False
    code_buffer: list[str] = []
    code_done_for_sub: set[tuple[str, str]] = set()

    def flush_subsection():
        nonlocal cur_buffer
        if cur_platform and cur_sub and cur_buffer:
            text_block = "\n".join(cur_buffer).strip()
            if text_block:
                sections.setdefault(cur_platform, {})
                # Не перетираем если уже задано из code block
                if cur_sub not in sections[cur_platform]:
                    sections[cur_platform][cur_sub] = text_block
        cur_buffer = []

    for line in lines:
        # Platform heading
        platform_match = None
        for pattern, plat in HEADING_TO_PLATFORM.items():
            if re.match(pattern, line):
                platform_match = plat
                break
        if platform_match:
            flush_subsection()
            cur_platform = platform_match
            cur_sub = "_intro"  # текст до первого ### sub
            continue

        # Subsection heading ### Foo
        if line.startswith("### ") and cur_platform:
            flush_subsection()
            cur_sub = _slugify_sub(line[4:].strip())
            continue

        # Code block tracking
        if line.startswith("```"):
            if in_code_block:
                # Конец блока — это значение для cur_sub
                if cur_platform and cur_sub:
                    sections.setdefault(cur_platform, {})
                    sections[cur_platform][cur_sub] = "\n".join(code_buffer).strip()
                    code_done_for_sub.add((cur_platform, cur_sub))
                code_buffer = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_buffer.append(line)
            continue

        if cur_platform:
            cur_buffer.append(line)

    flush_subsection()
    return sections


def _slugify_sub(s: str) -> str:
    """### Caption (ссылки в Reels НЕ работают...) → 'caption'"""
    s = re.sub(r"\(.*?\)", "", s)  # remove parenthetical
    s = re.sub(r"[^\w\s]", " ", s)
    parts = s.split()
    if not parts:
        return "_unknown"
    # Берём первое значимое слово
    return parts[0].lower()


# ============== Helpers ==============

def find_video(hero: str, platform: str | None = None) -> Path | None:
    """Ищет mp4 для героя/платформы.

    Приоритет:
      1. Если platform указан и есть `<HERO>*_FINAL*_<platform>.mp4` — берём его
         (старые герои THOR/MATRIX/ALEXANDER имеют отдельные форматы).
      2. Иначе fallback на `<HERO>*_subs.mp4` (для новых героев типа ODIN —
         Claudian упростил до единой сборки).
    """
    if platform:
        platform_specific = sorted(
            MOTIONVIRAL.glob(f"output/{hero}*FINAL*_{platform}.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if platform_specific:
            return platform_specific[0]
    fallback = sorted(
        MOTIONVIRAL.glob(f"output/{hero}*_subs.mp4"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return fallback[0] if fallback else None


def find_plan(hero: str) -> Path | None:
    """Ищет <HERO>_PUBLICATION_PLAN.md."""
    p = MOTIONVIRAL / f"{hero}_PUBLICATION_PLAN.md"
    return p if p.exists() else None


def append_log(hero: str, platform: str, status: str, url: str | None,
               post_id: str | None, notes: str = "") -> None:
    """Append одной строки в MULTI_CHANNEL_LOG.md."""
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    icon = "✅" if status == "ok" else "❌" if status == "fail" else "⏳"
    url_display = url or "—"
    pid_display = post_id or "—"
    notes_display = (notes or "")[:120]
    row = (
        f"| {ts} | {hero} | {platform} | {icon} {status} "
        f"| {url_display} | {pid_display} | {notes_display} |\n"
    )
    # Создаём заголовок таблицы для нового hero если ещё нет
    txt = LOG_FILE.read_text() if LOG_FILE.exists() else ""
    marker = f"\n## {hero} ("
    if marker not in txt:
        # Добавляем новый раздел в КОНЕЦ
        section = (
            f"\n## {hero} ({datetime.now().strftime('%Y-%m-%d')})\n\n"
            f"| When | Hero | Channel | Status | URL | post_id | Notes |\n"
            f"|---|---|---|---|---|---|---|\n"
        )
        with LOG_FILE.open("a") as f:
            f.write(section)
    with LOG_FILE.open("a") as f:
        f.write(row)


# ============== Publishers ==============

def publish_instagram(mp4: Path, plan: dict[str, str], dry: bool) -> dict:
    caption = plan.get("caption", "")
    if not caption:
        return {"ok": False, "error": "no caption in plan section 2"}
    ig_user = os.environ.get("IG_USER_ID")
    ig_token = os.environ.get("IG_ACCESS_TOKEN")
    if not ig_user or not ig_token:
        return {"ok": False, "error": "IG_USER_ID/IG_ACCESS_TOKEN not in env"}
    pinned_text = plan.get("первый") or plan.get("pinned") or ""
    payload = {
        "mp4_path": str(mp4),
        "caption": caption,
        "ig_user_id": ig_user,
        "access_token": ig_token,
    }
    if dry:
        return {"ok": True, "dry": True, "would_post": {
            "endpoint": "/instagram/upload",
            "mp4_size_mb": round(mp4.stat().st_size / 1e6, 1),
            "caption_chars": len(caption),
            "pinned_chars": len(pinned_text),
            "will_pin": bool(pinned_text),
        }}
    r = requests.post(
        f"{RECEIVER_URL}/instagram/upload",
        json=payload,
        headers={"X-Receiver-Secret": RECEIVER_SECRET},
        timeout=600,
    )
    if r.status_code != 200:
        return {"ok": False, "http": r.status_code, "body": r.text[:200]}
    result = r.json()
    media_id = result.get("post_id") or result.get("media_id")

    # После успешного publish — пишем pinned комментарий (важно для IG алгоритма!)
    if result.get("ok") and media_id and pinned_text:
        try:
            cr = requests.post(
                f"{RECEIVER_URL}/instagram/comment",
                json={
                    "media_id": media_id,
                    "message": pinned_text[:2200],
                    "access_token": ig_token,
                    "pin": True,
                },
                headers={"X-Receiver-Secret": RECEIVER_SECRET},
                timeout=30,
            )
            result["pinned_comment"] = cr.json() if cr.status_code == 200 else \
                {"ok": False, "http": cr.status_code}
        except Exception as e:
            result["pinned_comment"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return result


def publish_telegram(mp4: Path, plan: dict[str, str], dry: bool) -> dict:
    """Telegram канал @ger_dennis_ai через sendMessage (text + YT link preview).

    sendVideo с 50МБ mp4 через bot ratelimit'ится / fails 'chat not found'.
    Решение: sendMessage с текстом из plan + ссылкой на YT URL (через env
    HERO_YT_URL=https://www.youtube.com/watch?v=... который передаётся при
    publish, или скрипт fetch'ит из Supabase posts mirror).
    """
    text = plan.get("текст", "") or plan.get("_intro", "")
    if not text:
        return {"ok": False, "error": "no text in plan section 3"}
    yt_url = os.environ.get("HERO_YT_URL", "")  # передаётся при вызове, опционально
    full_text = text
    if yt_url:
        full_text = f"{text}\n\n🎬 {yt_url}"
    bot_token = os.environ.get("TG_BOT_TOKEN")
    if dry:
        return {"ok": True, "dry": True, "would_post": {
            "endpoint": "Bot API sendMessage (text + YT preview)",
            "chat_id": TG_CHANNEL_ID,
            "text_chars": len(full_text),
            "has_yt_url": bool(yt_url),
        }}
    r = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data={
            "chat_id": TG_CHANNEL_ID,
            "text": full_text[:4096],  # TG sendMessage limit
            "parse_mode": "Markdown",
            "disable_web_page_preview": "false",  # YT preview activated
        },
        timeout=30,
    )
    return r.json() if r.status_code == 200 else {"ok": False, "http": r.status_code, "body": r.text[:200]}


def publish_youtube(mp4: Path, plan: dict[str, str], dry: bool) -> dict:
    title = plan.get("title", "")
    description = plan.get("description", "")
    tags = plan.get("tags", "")
    # tags из MD code block — типа "#Shorts #AI #ClaudeCode" — парсим в list
    tag_list = [t.strip("# ") for t in re.findall(r"#\w+", tags)][:5]
    if not title:
        return {"ok": False, "error": "no title in plan section 1"}
    if dry:
        return {"ok": True, "dry": True, "would_post": {
            "endpoint": "youtube_uploader.py CLI",
            "title": title,
            "description_chars": len(description),
            "tags": tag_list,
            "privacy": "unlisted (по умолчанию — Денис делает public позже)",
        }}
    py = HOME / "Obsidian_AI_Brain/Projects/ContentMachine/.venv/bin/python"
    uploader = HOME / "Obsidian_AI_Brain/youtube_uploader.py"
    cmd = [
        str(py), str(uploader), str(mp4),
        "-t", title,
        "-d", description,
        "--privacy", "unlisted",
        "--lang", "ru",
    ]
    if tag_list:
        cmd.append("--tags")
        cmd.extend(tag_list)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        return {"ok": False, "error": "subprocess fail", "stderr": proc.stderr[-300:]}
    # Парсим URL из stdout
    url_match = re.search(r"https://www\.youtube\.com/watch\?v=(\S+)", proc.stdout)
    if not url_match:
        return {"ok": False, "error": "no URL in stdout", "stdout": proc.stdout[-300:]}
    return {"ok": True, "url": url_match.group(0), "video_id": url_match.group(1)}


def publish_ghost(mp4: Path, plan: dict[str, str], dry: bool) -> dict:
    """Ghost для героических видео НЕ публикуется.

    G_B workflow уже публикует Ghost blog 3 раза в день (RU/EN/DE) для
    Genesis-сгенерированных тем. Героические видео идут только в video-каналы
    (YT/IG/FB/Threads/TG). Если в плане есть секция 4.5 — просто игнорируем.
    """
    return {"ok": False, "error": "ghost: героические видео не публикуются в блог (G_B сам ведёт блог)"}


def publish_facebook(mp4: Path, plan: dict[str, str], dry: bool) -> dict:
    """FB Page video через receiver /facebook/upload (fal CDN → Graph API /videos).

    Caption берётся из плана секции '## 5. Facebook'. Если её нет — берём caption
    из Instagram (т.к. FB Reels часто = IG Reels cross-post).
    """
    description = plan.get("текст") or plan.get("caption") or plan.get("_intro", "")
    if not description:
        # Fallback: возьмём IG caption если FB секции нет
        return {"ok": False, "error": "no FB section in plan & no IG fallback"}
    page_token = os.environ.get("FB_PAGE_ACCESS_TOKEN")
    page_id = os.environ.get("FB_PAGE_ID", "598309940035981")
    if not page_token:
        return {"ok": False, "error": "FB_PAGE_ACCESS_TOKEN not in env"}
    payload = {
        "mp4_path": str(mp4),
        "description": description,
        "page_id": page_id,
        "access_token": page_token,
    }
    if dry:
        return {"ok": True, "dry": True, "would_post": {
            "endpoint": "/facebook/upload",
            "page_id": page_id,
            "description_chars": len(description),
            "mp4_size_mb": round(mp4.stat().st_size / 1e6, 1),
        }}
    r = requests.post(
        f"{RECEIVER_URL}/facebook/upload",
        json=payload,
        headers={"X-Receiver-Secret": RECEIVER_SECRET},
        timeout=600,
    )
    return r.json() if r.status_code == 200 else {"ok": False, "http": r.status_code, "body": r.text[:200]}


def publish_threads(mp4: Path, plan: dict[str, str], dry: bool) -> dict:
    return {"ok": False, "error": "threads: tokens pending (Денис ждёт Threads tester invite)"}


def publish_linkedin(mp4: Path, plan: dict[str, str], dry: bool) -> dict:
    return {"ok": False, "error": LINKEDIN_SKIP_REASON}


PUBLISHERS = {
    "instagram": publish_instagram,
    "telegram": publish_telegram,
    "youtube": publish_youtube,
    "facebook": publish_facebook,
    "threads": publish_threads,
    "linkedin": publish_linkedin,
}


# ============== Main ==============

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--hero", required=True, help="имя героя в UPPER (ODIN, THOR, etc.)")
    ap.add_argument("--platforms", required=True,
                    help="через запятую: instagram,telegram,youtube,ghost,facebook,threads")
    ap.add_argument("--publish", action="store_true",
                    help="реально публиковать (без флага — dry-run)")
    args = ap.parse_args()

    hero = args.hero.upper()
    platforms = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]

    # Базовый mp4 для проверки существования; для каждой платформы внизу find_video
    # позовётся ещё раз с platform-аргументом (для старых героев с _instagram/_youtube вариантами).
    mp4 = find_video(hero)
    plan_path = find_plan(hero)
    if not mp4:
        print(f"❌ нет видео {hero}_FINAL_subs.mp4 в {MOTIONVIRAL}/output/", file=sys.stderr)
        return 2
    if not plan_path:
        print(f"❌ нет плана {hero}_PUBLICATION_PLAN.md в {MOTIONVIRAL}/", file=sys.stderr)
        return 2
    print(f"[hero] {hero}")
    print(f"[mp4]  {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")
    print(f"[plan] {plan_path}")
    print(f"[mode] {'PUBLISH' if args.publish else 'DRY-RUN'}")
    print()

    sections = parse_plan(plan_path)
    print(f"[parsed sections] {sorted(sections.keys())}")
    for plat in sorted(sections.keys()):
        keys = sorted(sections[plat].keys())
        print(f"  {plat}: {keys}")
    print()

    for plat in platforms:
        if plat not in PUBLISHERS:
            print(f"[{plat}] ❌ unknown platform")
            continue
        plan = sections.get(plat, {})
        # Facebook: если своей секции нет — fallback на Instagram (FB Reels = IG cross-post)
        if plat == "facebook" and not plan:
            plan = sections.get("instagram", {})
            if plan:
                print(f"[{plat}] fallback на Instagram caption")
        if not plan:
            print(f"[{plat}] ⚠️ нет секции в plan, skip")
            append_log(hero, plat, "skip", None, None, "no plan section")
            continue
        # platform-specific mp4 (для старых героев THOR/MATRIX есть _instagram.mp4 / _youtube.mp4)
        plat_mp4 = find_video(hero, platform=plat) or mp4
        print(f"[{plat}] публикую... ({plat_mp4.name})")
        try:
            result = PUBLISHERS[plat](plat_mp4, plan, dry=not args.publish)
        except Exception as e:
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        ok = bool(result.get("ok"))
        status = "ok" if ok else "fail"
        if args.publish and ok:
            print(f"  ✅ {json.dumps({k: v for k, v in result.items() if k != 'access_token'}, ensure_ascii=False)[:300]}")
            url = result.get("url") or (result.get("would_post", {}).get("endpoint", ""))
            post_id = result.get("post_id") or result.get("video_id") or result.get("message_id")
            append_log(hero, plat, status, url, str(post_id) if post_id else None,
                       notes=json.dumps({k: v for k, v in result.items()
                                         if k in ("video_id", "url")},
                                        ensure_ascii=False)[:100])
        elif args.publish:
            print(f"  ❌ {result}")
            append_log(hero, plat, status, None, None, str(result.get("error", ""))[:120])
        else:
            print(f"  [DRY] {json.dumps(result, ensure_ascii=False)[:500]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
