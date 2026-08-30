#!/usr/bin/env python3
"""
Одноразовая OAuth-авторизация Google Search Console для g_c_gsc.py.

Использует СУЩЕСТВУЮЩИЙ OAuth-клиент `denniscraft-youtube`
(~/Obsidian_AI_Brain/.youtube/client_secret.json) — новый клиент не создаём.
Токен пишется ОТДЕЛЬНО: ~/Obsidian_AI_Brain/.gsc/token.pickle (YouTube-токен не трогаем).

Перед запуском (Денис, один раз): Google Cloud → проект denniscraft-youtube →
APIs & Services → Library → «Google Search Console API» → Enable.

Запуск (откроет браузер, войти как shohirevdenis@gmail.com):
    python gsc_auth.py
    python gsc_auth.py --check     # только проверить токен и список property
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
BRAIN = Path.home() / "Obsidian_AI_Brain"
CLIENT_SECRET = BRAIN / ".youtube" / "client_secret.json"
TOKEN_PATH = BRAIN / ".gsc" / "token.pickle"


def load_or_auth(check_only: bool):
    creds = None
    if TOKEN_PATH.exists():
        creds = pickle.load(open(TOKEN_PATH, "rb"))
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif check_only:
        print(f"[FAIL] нет валидного токена в {TOKEN_PATH}; запусти без --check")
        sys.exit(1)
    else:
        if not CLIENT_SECRET.exists():
            print(f"[FAIL] нет {CLIENT_SECRET}")
            sys.exit(1)
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_PATH, "wb") as f:
        pickle.dump(creds, f)
    TOKEN_PATH.chmod(0o600)
    print(f"[OK] токен сохранён: {TOKEN_PATH}")
    return creds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    creds = load_or_auth(args.check)
    svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    sites = svc.sites().list().execute().get("siteEntry", [])
    if not sites:
        print("[WARN] property не найдены — проверь, что аккаунт владеет https://gerdennisai.com/")
        return 1
    for s in sites:
        print(f"[SITE] {s['siteUrl']}  ({s.get('permissionLevel')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
