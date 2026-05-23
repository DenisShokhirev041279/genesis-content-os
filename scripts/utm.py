"""
UTM helper для Genesis Content OS — оборачивает любую gerdennisai.com ссылку
параметрами Plausible-атрибуции.

Когда использовать:
- ОБЯЗАТЕЛЬНО: ссылки в Telegram-постах (TG-app не пробрасывает Referer)
- ЖЕЛАТЕЛЬНО: ссылки в YouTube description/pinned comment (Referer теряется при copy-paste)
- НЕ НУЖНО: ссылки в LinkedIn (Plausible сам видит referer linkedin.com)
              ссылки в Ghost-постах (внутренние, не нужно атрибутировать самого себя)

Конвенция utm_*:
    utm_source   ∈ {telegram, youtube, linkedin, instagram}
    utm_medium   = 'social' (всегда для соцсетей)
    utm_campaign = topic_slug (для группировки по теме)
    utm_content  = post_id или message_id (для дифференциации между постами одной темы)

Usage:
    from utm import wrap

    url = wrap("https://gerdennisai.com/blog/genesis-os",
               source="telegram",
               campaign="genesis-launch",
               content="post-1340")
    # → https://gerdennisai.com/blog/genesis-os?utm_source=telegram&utm_medium=social&utm_campaign=genesis-launch&utm_content=post-1340
"""
from __future__ import annotations

from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl


VALID_SOURCES = {"telegram", "youtube", "linkedin", "instagram", "twitter", "email"}
OWN_DOMAINS = {"gerdennisai.com", "www.gerdennisai.com", "cms.gerdennisai.com"}


def wrap(
    url: str,
    *,
    source: str,
    campaign: str,
    content: str | None = None,
    medium: str = "social",
) -> str:
    """Добавляет utm_* параметры к URL.

    Возвращает URL без изменений если:
    - источник не gerdennisai.com (внешний линк, не наш трафик)
    - URL уже содержит utm_source (не перетираем)
    """
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown utm_source: {source!r}, expected one of {VALID_SOURCES}")

    parsed = urlparse(url)
    if parsed.netloc not in OWN_DOMAINS:
        return url

    existing = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "utm_source" in existing:
        return url

    utm = {
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": campaign,
    }
    if content:
        utm["utm_content"] = content

    merged = {**existing, **utm}
    return urlunparse(parsed._replace(query=urlencode(merged)))


def wrap_telegram(url: str, campaign: str, message_id: str | int | None = None) -> str:
    """Shortcut для TG-постов."""
    return wrap(
        url,
        source="telegram",
        campaign=campaign,
        content=f"msg-{message_id}" if message_id else None,
    )


def wrap_youtube(url: str, campaign: str, video_id: str | None = None) -> str:
    """Shortcut для YT description/pinned comment."""
    return wrap(
        url,
        source="youtube",
        campaign=campaign,
        content=f"vid-{video_id}" if video_id else None,
    )


if __name__ == "__main__":
    # Smoke tests
    assert wrap_telegram("https://gerdennisai.com/blog/genesis-os", "launch", 1340) == \
        "https://gerdennisai.com/blog/genesis-os?utm_source=telegram&utm_medium=social&utm_campaign=launch&utm_content=msg-1340"
    assert wrap("https://github.com/foo", source="telegram", campaign="x") == "https://github.com/foo"
    assert wrap("https://gerdennisai.com/?utm_source=manual", source="telegram", campaign="x") == \
        "https://gerdennisai.com/?utm_source=manual"
    assert wrap_youtube("https://gerdennisai.com/genesis", "shorts-pilot", "abc123") == \
        "https://gerdennisai.com/genesis?utm_source=youtube&utm_medium=social&utm_campaign=shorts-pilot&utm_content=vid-abc123"
    print("✅ all utm tests pass")
