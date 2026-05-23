"""
g_b_instagram.py — Genesis Module B: Instagram Reels автопубликация.

Назначение
----------
Принимает готовый MP4 (9:16, 5-90 сек) от видео-pipeline (Higgsfield + ElevenLabs + HeyGen)
и публикует его как Reel в Instagram Business аккаунт Дениса (@ger_denis_sh) через
Meta Graph API v23.0 (актуальная на 2026-05).

Flow
----
1. upload_to_public(): загружает локальный MP4 на public URL
   - Стратегия по умолчанию: scp на DO droplet (206.189.103.48), отдача через nginx
     по адресу https://media.gerdennisai.com/reels/<filename>.mp4
   - Альтернатива (если nginx subdomain не настроен): tmp-отдача через ngrok-туннель receiver
2. create_container(): POST /{ig-user-id}/media с media_type=REELS, video_url, caption
3. wait_until_ready(): poll /{container-id}?fields=status_code пока FINISHED (или ERROR/EXPIRED)
4. publish(): POST /{ig-user-id}/media_publish с creation_id
5. возвращает permalink опубликованного Reel + media_id

UTM
---
В caption ОБЯЗАТЕЛЬНО добавляется ссылка gerdennisai.com/?utm_source=instagram&...
через utm.wrap() — IG-app НЕ пробрасывает Referer, без UTM трафик не атрибутируется в Plausible.

Использование
-------------
    python g_b_instagram.py \\
        --mp4 ~/Obsidian_AI_Brain/Projects/ContentMachine/runs/2026-05-23_heracles/short.mp4 \\
        --caption "Геракл. День 1: Немейский лев." \\
        --topic heracles-saga \\
        --post-id ep-001 \\
        --hashtags "AI агент,автономный агент,Genesis OS,Дешёвый AI,героический нарратив"

Env vars
--------
    IG_ACCESS_TOKEN  — long-lived (60-day) Instagram User Access Token
    IG_USER_ID       — Instagram Business Account ID (НЕ Facebook Page ID, НЕ @username)
    IG_GRAPH_VERSION — опц., по умолчанию v23.0
    IG_PUBLIC_HOST   — опц., base URL для public MP4 (default: https://media.gerdennisai.com/reels)
    IG_DRY_RUN       — если "1": не зовёт Meta API, только печатает что бы сделал

Permissions (нужны на токене)
-----------------------------
    instagram_business_basic
    instagram_business_content_publish
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

# UTM модуль рядом
sys.path.insert(0, str(Path(__file__).resolve().parent))
from utm import wrap as utm_wrap  # noqa: E402


GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "v23.0")
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

# Где будет лежать MP4 публично (nginx на DO droplet)
DEFAULT_PUBLIC_HOST = os.environ.get(
    "IG_PUBLIC_HOST",
    "https://media.gerdennisai.com/reels",
)
DROPLET_SSH_ALIAS = "genesis-do"  # из ~/.ssh/config (см. КЛЮЧИ_ПРОЕКТОВ.md строка 522)
DROPLET_NGINX_DIR = "/opt/genesis/media/reels"  # nginx serves этот путь

POLL_INTERVAL_S = 10        # Meta рекомендует 1/min, мы агрессивнее для коротких Reels
POLL_TIMEOUT_S = 5 * 60     # 5 минут — стандартный потолок для 90-сек видео
CONTAINER_TTL_S = 24 * 3600 # Meta истекает контейнер за 24 часа

# Лимиты Reel (для валидации до загрузки на Meta)
MAX_FILE_SIZE_MB = 100      # практический предел (Meta объявляет 300MB, но реально режет)
MIN_DURATION_S = 5
MAX_DURATION_S = 90


# ─── Exceptions ────────────────────────────────────────────────────────────

class InstagramPublishError(Exception):
    """Базовая ошибка публикации в IG."""


class ContainerFailedError(InstagramPublishError):
    """Meta вернул status_code=ERROR или EXPIRED."""


class ContainerTimeoutError(InstagramPublishError):
    """Контейнер не достиг FINISHED за POLL_TIMEOUT_S."""


class RateLimitedError(InstagramPublishError):
    """Превышен лимит 100 публикаций / 24ч (или мягкий rate limit)."""


class TokenExpiredError(InstagramPublishError):
    """Long-lived токен истёк (60 дней без refresh) — нужен новый OAuth flow."""


# ─── Data classes ──────────────────────────────────────────────────────────

@dataclass
class PublishResult:
    media_id: str
    permalink: str
    container_id: str
    public_video_url: str

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, indent=2)


# ─── Step 1: upload to public URL ──────────────────────────────────────────

def upload_to_public(mp4_path: Path, *, dry_run: bool = False) -> str:
    """Заливает MP4 на DO droplet через scp, возвращает публичный URL.

    Предусловие на droplet (один раз настраивает Денис или Hermes):
        sudo mkdir -p /opt/genesis/media/reels
        sudo chown -R deploy:deploy /opt/genesis/media
        # nginx server block:
        #   server_name media.gerdennisai.com;
        #   location /reels/ { alias /opt/genesis/media/reels/; }
        # + Cloudflare CNAME media → genesis-pipeline + Let's Encrypt cert
    """
    if not mp4_path.exists():
        raise FileNotFoundError(f"MP4 не найден: {mp4_path}")

    size_mb = mp4_path.stat().st_size / 1024 / 1024
    if size_mb > MAX_FILE_SIZE_MB:
        raise InstagramPublishError(
            f"MP4 слишком большой: {size_mb:.1f}MB > {MAX_FILE_SIZE_MB}MB лимит"
        )

    filename = mp4_path.name
    remote_path = f"{DROPLET_NGINX_DIR}/{filename}"
    public_url = f"{DEFAULT_PUBLIC_HOST}/{filename}"

    if dry_run:
        print(f"[dry-run] scp {mp4_path} {DROPLET_SSH_ALIAS}:{remote_path}")
        print(f"[dry-run] public URL → {public_url}")
        return public_url

    cmd = ["scp", "-q", str(mp4_path), f"{DROPLET_SSH_ALIAS}:{remote_path}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise InstagramPublishError(
            f"scp failed (rc={result.returncode}): {result.stderr.strip()}"
        )

    # Smoke-проверка что URL реально отдаётся
    head = requests.head(public_url, timeout=10, allow_redirects=True)
    if head.status_code != 200:
        raise InstagramPublishError(
            f"Public URL не отвечает 200: {public_url} → {head.status_code}"
        )
    return public_url


# ─── Step 2: create container ──────────────────────────────────────────────

def create_container(
    *,
    ig_user_id: str,
    access_token: str,
    video_url: str,
    caption: str,
    cover_url: str | None = None,
    share_to_feed: bool = True,
) -> str:
    """POST /{ig-user-id}/media → возвращает container_id."""
    payload: dict[str, Any] = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": share_to_feed,
        "access_token": access_token,
    }
    if cover_url:
        payload["cover_url"] = cover_url

    url = f"{GRAPH_BASE}/{ig_user_id}/media"
    resp = requests.post(url, data=payload, timeout=30)
    _raise_for_meta_error(resp)

    data = resp.json()
    container_id = data.get("id")
    if not container_id:
        raise InstagramPublishError(f"Не вернулся container id: {data}")
    return container_id


# ─── Step 3: poll status ───────────────────────────────────────────────────

def wait_until_ready(
    *,
    container_id: str,
    access_token: str,
    interval_s: int = POLL_INTERVAL_S,
    timeout_s: int = POLL_TIMEOUT_S,
) -> None:
    """Поллит /{container-id}?fields=status_code, пока FINISHED.

    Поднимает ContainerFailedError на ERROR/EXPIRED, ContainerTimeoutError по таймауту.
    """
    url = f"{GRAPH_BASE}/{container_id}"
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        resp = requests.get(
            url,
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=15,
        )
        _raise_for_meta_error(resp)
        data = resp.json()
        status_code = data.get("status_code", "")
        if status_code == "FINISHED":
            return
        if status_code in {"ERROR", "EXPIRED"}:
            raise ContainerFailedError(
                f"Контейнер {container_id} в состоянии {status_code}: {data.get('status')}"
            )
        # IN_PROGRESS / PUBLISHED (на этом шаге быть не должно) → ждём
        time.sleep(interval_s)

    raise ContainerTimeoutError(
        f"Контейнер {container_id} не достиг FINISHED за {timeout_s}s"
    )


# ─── Step 4: publish ───────────────────────────────────────────────────────

def publish(*, ig_user_id: str, access_token: str, container_id: str) -> str:
    """POST /{ig-user-id}/media_publish → возвращает media_id."""
    url = f"{GRAPH_BASE}/{ig_user_id}/media_publish"
    resp = requests.post(
        url,
        data={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    _raise_for_meta_error(resp)
    data = resp.json()
    media_id = data.get("id")
    if not media_id:
        raise InstagramPublishError(f"Не вернулся media_id: {data}")
    return media_id


def fetch_permalink(*, media_id: str, access_token: str) -> str:
    """GET /{media-id}?fields=permalink — публичная ссылка на Reel."""
    url = f"{GRAPH_BASE}/{media_id}"
    resp = requests.get(
        url,
        params={"fields": "permalink", "access_token": access_token},
        timeout=15,
    )
    _raise_for_meta_error(resp)
    return resp.json().get("permalink", "")


# ─── Caption helper ────────────────────────────────────────────────────────

def build_caption(*, body: str, topic: str, post_id: str, hashtags: list[str]) -> str:
    """Собирает финальный caption: текст + UTM-ссылка + хэштеги.

    Caption limits Meta:
      - 2200 символов
      - 30 хэштегов
      - 20 @-mentions
    """
    link = utm_wrap(
        "https://gerdennisai.com/",
        source="instagram",
        campaign=topic,
        content=post_id,
    )
    tags_block = " ".join(f"#{t.strip().replace(' ', '')}" for t in hashtags[:30])
    caption = f"{body.rstrip()}\n\n→ {link}\n\n{tags_block}".strip()

    if len(caption) > 2200:
        raise InstagramPublishError(
            f"Caption {len(caption)} > 2200 символов лимит Meta"
        )
    return caption


# ─── Internal: error handling ──────────────────────────────────────────────

def _raise_for_meta_error(resp: requests.Response) -> None:
    """Преобразует HTTP ошибки Meta в типизированные исключения."""
    if resp.status_code == 200:
        return
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {"message": resp.text[:500]}

    msg = err.get("message", "")
    code = err.get("code")
    subcode = err.get("error_subcode")

    # Token истёк / невалиден
    if code in {190, 102} or "access token" in msg.lower():
        raise TokenExpiredError(
            f"Token проблема (code={code}, subcode={subcode}): {msg}. "
            f"Обновить через refresh_access_token или новый OAuth flow."
        )
    # Rate limit (4 = app limit, 17 = user limit, 32 = page-level, 613 = custom)
    if code in {4, 17, 32, 613} or "rate" in msg.lower() or "limit" in msg.lower():
        raise RateLimitedError(
            f"Rate limit (code={code}): {msg}. "
            f"Лимит 100 публикаций / 24ч на IG аккаунт."
        )
    # Видео не подходит по формату
    if subcode in {2207026, 2207052} or "format" in msg.lower() or "codec" in msg.lower():
        raise InstagramPublishError(
            f"Видео не прошло валидацию Meta (subcode={subcode}): {msg}. "
            f"Проверь: H264/HEVC, AAC 48kHz, 9:16, 5-90s, <100MB."
        )

    raise InstagramPublishError(
        f"Meta API error HTTP {resp.status_code} (code={code}, subcode={subcode}): {msg}"
    )


# ─── Main orchestration ────────────────────────────────────────────────────

def publish_reel(
    *,
    mp4_path: Path,
    caption_body: str,
    topic: str,
    post_id: str,
    hashtags: list[str],
    cover_url: str | None = None,
    share_to_feed: bool = True,
    dry_run: bool = False,
) -> PublishResult:
    """Полный flow: scp → /media → poll → /media_publish → permalink."""
    access_token = os.environ.get("IG_ACCESS_TOKEN", "")
    ig_user_id = os.environ.get("IG_USER_ID", "")

    if not dry_run and (not access_token or not ig_user_id):
        raise InstagramPublishError(
            "Нет IG_ACCESS_TOKEN или IG_USER_ID в окружении. "
            "См. README раздел 'Setup Meta App' или КЛЮЧИ_ПРОЕКТОВ.md."
        )

    caption = build_caption(
        body=caption_body,
        topic=topic,
        post_id=post_id,
        hashtags=hashtags,
    )

    print(f"[1/4] Загружаю MP4 на public URL ({mp4_path.name})...")
    video_url = upload_to_public(mp4_path, dry_run=dry_run)

    if dry_run:
        print(f"[dry-run] Caption ({len(caption)} chars):\n{caption}\n")
        print(f"[dry-run] Не зову Meta API. Видео доступно: {video_url}")
        return PublishResult(
            media_id="dry-run",
            permalink="dry-run",
            container_id="dry-run",
            public_video_url=video_url,
        )

    print(f"[2/4] Создаю контейнер на Meta...")
    container_id = create_container(
        ig_user_id=ig_user_id,
        access_token=access_token,
        video_url=video_url,
        caption=caption,
        cover_url=cover_url,
        share_to_feed=share_to_feed,
    )
    print(f"      container_id = {container_id}")

    print(f"[3/4] Жду пока Meta переварит видео (до {POLL_TIMEOUT_S}s)...")
    wait_until_ready(container_id=container_id, access_token=access_token)
    print(f"      FINISHED")

    print(f"[4/4] Публикую...")
    media_id = publish(
        ig_user_id=ig_user_id,
        access_token=access_token,
        container_id=container_id,
    )
    permalink = fetch_permalink(media_id=media_id, access_token=access_token)
    print(f"      media_id = {media_id}")
    print(f"      permalink = {permalink}")

    return PublishResult(
        media_id=media_id,
        permalink=permalink,
        container_id=container_id,
        public_video_url=video_url,
    )


# ─── CLI ───────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Publish a Reel to Instagram via Graph API")
    p.add_argument("--mp4", required=True, type=Path, help="Путь к локальному MP4 (9:16, 5-90s)")
    p.add_argument("--caption", required=True, help="Основной текст поста (без UTM/хэштегов)")
    p.add_argument("--topic", required=True, help="Slug темы для utm_campaign (напр. heracles-saga)")
    p.add_argument("--post-id", required=True, help="ID поста для utm_content (напр. ep-001)")
    p.add_argument(
        "--hashtags",
        default="",
        help="Хэштеги через запятую (без #), max 30. Напр. 'AI,автономный,Genesis'",
    )
    p.add_argument("--cover-url", default=None, help="Опц. публичный URL обложки")
    p.add_argument("--no-feed", action="store_true", help="НЕ публиковать в Feed (только Reels tab)")
    p.add_argument("--dry-run", action="store_true", help="Не зовёт Meta API, только печатает")
    args = p.parse_args()

    hashtags = [h for h in (args.hashtags.split(",") if args.hashtags else []) if h.strip()]

    try:
        result = publish_reel(
            mp4_path=args.mp4,
            caption_body=args.caption,
            topic=args.topic,
            post_id=args.post_id,
            hashtags=hashtags,
            cover_url=args.cover_url,
            share_to_feed=not args.no_feed,
            dry_run=args.dry_run or bool(os.environ.get("IG_DRY_RUN")),
        )
        print("\n" + result.to_json())
        return 0
    except TokenExpiredError as e:
        print(f"\nTOKEN EXPIRED: {e}", file=sys.stderr)
        return 2
    except RateLimitedError as e:
        print(f"\nRATE LIMITED: {e}", file=sys.stderr)
        return 3
    except InstagramPublishError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
