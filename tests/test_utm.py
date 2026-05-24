"""Smoke tests for utm.wrap helper."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from utm import wrap, wrap_telegram, wrap_youtube  # noqa: E402


def test_wrap_telegram_with_message_id():
    out = wrap_telegram("https://gerdennisai.com/blog/genesis-os", "launch", 1340)
    assert "utm_source=telegram" in out
    assert "utm_medium=social" in out
    assert "utm_campaign=launch" in out
    assert "utm_content=msg-1340" in out


def test_wrap_external_url_unchanged():
    assert wrap("https://github.com/foo", source="telegram", campaign="x") == "https://github.com/foo"


def test_wrap_preserves_existing_utm():
    src = "https://gerdennisai.com/?utm_source=manual"
    assert wrap(src, source="telegram", campaign="x") == src


def test_wrap_youtube_with_video_id():
    out = wrap_youtube("https://gerdennisai.com/genesis", "shorts-pilot", "abc123")
    assert "utm_source=youtube" in out
    assert "utm_content=vid-abc123" in out
