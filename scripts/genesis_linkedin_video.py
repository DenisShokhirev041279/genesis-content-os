#!/usr/bin/env python3
"""genesis_linkedin_video — «Genesis сам себя рекламирует» ВИДЕО-режим для LinkedIn.

Аналог genesis_linkedin_selfpromo.py, но вместо PDF-карусели отправляет Денису
в Telegram нативное ВИДЕО (mp4) + caption + inline-кнопку «🚀 Public → LinkedIn».

НИЧЕГО НЕ ПУБЛИКУЕТ САМ. Публикация — только по нажатию кнопки Денисом.
Кнопку ловит aidrops_gate_listener.py (callback_data = "livideo:<id>"), который
читает pending-JSON и зовёт publish_linkedin_video.py (нативное видео в LinkedIn).
Отмена — callback_data = "licancel:<id>" (тот же handler, что у карусели).

Usage:
    python genesis_linkedin_video.py --video EN_B02_autonomous_system.mp4 --lang en --caption-topic 1
    python genesis_linkedin_video.py --video reel.mp4 --lang de --caption-topic 5
    python genesis_linkedin_video.py --video reel.mp4 --lang en --caption "свой текст"
    python genesis_linkedin_video.py --video reel.mp4 --lang en --no-send   # только pending, без Telegram

caption-topic N (1-5) берёт готовый LinkedIn-caption из genesis_linkedin_selfpromo.CAPTIONS
(тема 1 = автономная система). Если тот модуль недоступен — встроенный fallback-caption.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path.home() / "Obsidian_AI_Brain"
PENDING_DIR = ROOT / "Projects/genesis-content-os/output/selfpromo_pending"
DENIS_ID = 1357650155

LIVE = "https://live.gerdennisai.com"
GH = "https://github.com/DenisShokhirev041279/genesis-content-os"

# fallback-caption (тема 1, автономная система) если импорт CAPTIONS не удался
_FALLBACK_CAPTION = {
    "en": (
        "I don't make this content. My system does — and it decides what, itself.\n\n"
        "Genesis is an autonomous, open-source content system. It scans 3,300+ trends, "
        "picks the topics, writes the script, voices and edits it, then publishes to 5 "
        "channels — 24/7, with zero humans in the loop. Then it reads its own metrics and "
        "rewrites its own strategy.\n\n"
        "The real shift isn't \"AI makes a post\". It's a system that runs and improves "
        "itself — from prompt engineering to autonomous systems.\n\n"
        "How autonomous should content AI get — where's the line?\n\n"
        "#AI #OpenSource #AgentEngineering #Automation #BuildInPublic"),
    "de": (
        "Ich drehe diesen Content nicht. Mein System macht ihn — und entscheidet selbst, was.\n\n"
        "Genesis ist ein autonomes, quelloffenes Content-System. Es scannt 3.300+ Trends, "
        "wählt die Themen, schreibt das Skript, vertont und schneidet, publiziert dann auf 5 "
        "Kanäle — 24/7, ohne einen Menschen im Loop. Danach liest es die eigenen Metriken und "
        "schreibt seine Strategie selbst um.\n\n"
        "Der eigentliche Wandel ist nicht \"KI macht einen Post\", sondern ein System, das sich "
        "selbst steuert und verbessert — von Prompt Engineering zu autonomen Systemen.\n\n"
        "Wie autonom sollte Content-KI werden — wo ist die Grenze?\n\n"
        "#KI #OpenSource #AgentEngineering #Automatisierung #BuildInPublic"),
}

from utm import wrap as _utm_wrap  # UTM-атрибуция first-comment (audit 2026-08-07)
FIRST_COMMENT = f"Links:  🔴 Live dashboard: {_utm_wrap(LIVE, source='linkedin', campaign='li-video')}   ·   ⭐ Code: {GH}"


def _load_caption(topic: int, lang: str, override: str | None) -> str:
    if override:
        return override
    # переиспользуем готовые LinkedIn-caption'ы из selfpromo, если модуль импортируется
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from genesis_linkedin_selfpromo import CAPTIONS  # type: ignore
        cap = CAPTIONS.get((topic, lang))
        if cap:
            return cap
    except Exception as e:
        print(f"[caption] selfpromo CAPTIONS недоступны ({e}); fallback", file=sys.stderr)
    return _FALLBACK_CAPTION.get(lang, _FALLBACK_CAPTION["en"])


# ---------- Telegram ----------
def _tg_token() -> str:
    for env in (ROOT / "Projects/ContentMachine/receiver/.env",
                ROOT / "Projects/genesis-content-os/.env"):
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("TG_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    raise SystemExit("TG_BOT_TOKEN не найден (receiver/.env)")


def _multipart(fields: dict, files: dict):
    boundary = "----genesisvideo" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    for k, path in files.items():
        p = Path(path)
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{k}"; '
                 f'filename="{p.name}"\r\n').encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += p.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return boundary, body


def _tg_api(token, method, fields, files=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        boundary, body = _multipart(fields, files)
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def send_to_denis(video_path: Path, caption: str, title: str, topic: int, lang: str):
    """Заливает видео на дроплет и шлёт Денису approve+кнопку через @gerdennisapprove_bot.

    Публикацию по кнопке обслуживает дроплетный li_callback_listener (gvid:<id>), а НЕ
    Клешня/MyOpenClaw — иначе тап ловит Клешня и публикации нет.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from genesis_droplet_gate import send_via_droplet  # noqa: E402
    pend_id = uuid.uuid4().hex[:12]
    ok, out = send_via_droplet(pend_id, video_path, "video", caption,
                               title, FIRST_COMMENT, lang)
    print(out)
    return pend_id, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="путь к mp4")
    ap.add_argument("--lang", choices=["en", "de"], required=True)
    ap.add_argument("--caption-topic", type=int, choices=[1, 2, 3, 4, 5], default=1,
                    help="какой готовый caption взять (по умолчанию 1 = автономная система)")
    ap.add_argument("--caption", help="переопределить текст caption целиком")
    ap.add_argument("--title", default="Genesis — autonomous content system")
    ap.add_argument("--no-send", action="store_true",
                    help="создать pending-JSON, но не слать в Telegram")
    args = ap.parse_args()

    video = Path(args.video).expanduser()
    if not video.exists():
        ap.error(f"видео не найдено: {video}")

    caption = _load_caption(args.caption_topic, args.lang, args.caption)

    if args.no_send:
        pend_id = uuid.uuid4().hex[:12]
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        (PENDING_DIR / f"{pend_id}.json").write_text(json.dumps({
            "id": pend_id, "kind": "video", "video_path": str(video),
            "caption": caption, "title": args.title, "topic": args.caption_topic,
            "lang": args.lang, "first_comment": FIRST_COMMENT,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"--no-send: pending создан {pend_id}, в Telegram не слал.")
        print("\n=== LinkedIn caption ===\n" + caption)
        return

    pend_id, ok = send_to_denis(video, caption, args.title, args.caption_topic, args.lang)
    print(f"Telegram → Denis (@gerdennisapprove_bot): ok={ok}, pending_id={pend_id}")
    print("Кнопка «Public» ждёт нажатия. Ловит дроплетный li_callback_listener.py (gvid:<id>).")


if __name__ == "__main__":
    main()
