"""Tests for g_e_trial_measure — measured evolution / auto-rollback core logic.

Мы тестируем ЧИСТЫЕ функции (без сети): решение о вердикте, оконное усреднение
зрелых постов и свёртку снимков к последнему значению на пост. Это сердце
«эволюции»: система оставляет улучшения и откатывает просадки.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from g_e_trial_measure import (  # noqa: E402
    decide_verdict,
    window_average,
    latest_value_per_post,
    _bump_patch,
)

UTC = timezone.utc


# ---------- decide_verdict ----------

def test_clear_improvement_is_kept():
    v = decide_verdict(before=100, n_before=6, after=140, n_after=6, trial_age_days=20)
    assert v["verdict"] == "kept"
    assert v["change_pct"] == 40.0


def test_small_dip_within_noise_is_kept():
    # -5% при noise 10% — шум, не откатываем
    v = decide_verdict(before=100, n_before=6, after=95, n_after=6, trial_age_days=20)
    assert v["verdict"] == "kept"


def test_real_regression_is_rolled_back():
    v = decide_verdict(before=100, n_before=6, after=80, n_after=6, trial_age_days=20)
    assert v["verdict"] == "rolled_back"
    assert v["severe"] is False  # -20% < 25% severe-порога


def test_severe_regression_flags_severe():
    v = decide_verdict(before=100, n_before=6, after=60, n_after=6, trial_age_days=20)
    assert v["verdict"] == "rolled_back"
    assert v["severe"] is True  # -40% ≥ severe → авто-merge реверта


def test_too_few_after_posts_is_pending_while_young():
    v = decide_verdict(before=100, n_before=6, after=80, n_after=1, trial_age_days=5)
    assert v["verdict"] == "pending"  # не судим по 1 посту


def test_aged_out_low_data_is_kept_not_rolled():
    # мало данных, но испытание перестарело → оставляем, а не откатываем вслепую
    v = decide_verdict(before=100, n_before=6, after=80, n_after=1, trial_age_days=60)
    assert v["verdict"] == "kept_low_data"


def test_no_baseline_cannot_judge():
    v = decide_verdict(before=0, n_before=0, after=120, n_after=8, trial_age_days=10)
    assert v["verdict"] == "insufficient_baseline"


def test_regression_never_triggered_without_enough_baseline():
    # даже при просадке: если baseline тонкий и рано — ждём, НЕ откатываем
    v = decide_verdict(before=100, n_before=2, after=50, n_after=8, trial_age_days=10)
    assert v["verdict"] != "rolled_back"


# ---------- window_average ----------

def _mk(now):
    published = {
        "p_old": now - timedelta(days=40),   # baseline-окно, зрелый
        "p_base": now - timedelta(days=30),  # baseline-окно, зрелый
        "p_new1": now - timedelta(days=15),  # after-окно, зрелый
        "p_new2": now - timedelta(days=12),  # after-окно, зрелый
        "p_fresh": now - timedelta(days=2),  # after-окно, НЕ зрелый (моложе min_age)
    }
    latest = {"p_old": 200, "p_base": 100, "p_new1": 300, "p_new2": 500, "p_fresh": 9999}
    return published, latest


def test_window_excludes_immature_posts():
    now = datetime(2026, 7, 1, tzinfo=UTC)
    published, latest = _mk(now)
    T = now - timedelta(days=21)  # активация 21 день назад
    avg, n = window_average(list(published), published, latest,
                            start=T, end=now, min_age=timedelta(days=7), now=now)
    # p_new1(300)+p_new2(500) считаются; p_fresh (2д) исключён как незрелый
    assert n == 2
    assert avg == 400.0


def test_window_baseline_side():
    now = datetime(2026, 7, 1, tzinfo=UTC)
    published, latest = _mk(now)
    T = now - timedelta(days=21)
    W = timedelta(days=21)
    avg, n = window_average(list(published), published, latest,
                            start=T - W, end=T, min_age=timedelta(days=7), now=now)
    # p_old(200)+p_base(100) в [T-21, T)
    assert n == 2
    assert avg == 150.0


def test_window_empty_returns_zero():
    now = datetime(2026, 7, 1, tzinfo=UTC)
    avg, n = window_average([], {}, {}, now - timedelta(days=1), now,
                            timedelta(days=7), now)
    assert (avg, n) == (0.0, 0)


# ---------- latest_value_per_post ----------

def test_latest_value_picks_newest_snapshot():
    snaps = [
        {"post_id": "a", "metric_value": 10, "captured_at": "2026-06-01T00:00:00Z"},
        {"post_id": "a", "metric_value": 55, "captured_at": "2026-06-10T00:00:00Z"},  # newest
        {"post_id": "a", "metric_value": 30, "captured_at": "2026-06-05T00:00:00Z"},
        {"post_id": "b", "metric_value": 7, "captured_at": "2026-06-02T00:00:00Z"},
        {"post_id": None, "metric_value": 999, "captured_at": "2026-06-02T00:00:00Z"},  # global, ignore
    ]
    out = latest_value_per_post(snaps)
    assert out == {"a": 55.0, "b": 7.0}


# ---------- version bump ----------

def test_bump_patch():
    assert _bump_patch("1.0.1") == "1.0.2"
    assert _bump_patch("2.3.9") == "2.3.10"
    assert _bump_patch("weird") == "weird.1"
