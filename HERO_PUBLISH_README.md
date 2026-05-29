# 🎬 Hero Multi-Channel Publish — пользовательский README

> Как опубликовать героическое видео (ODIN/THOR/MATRIX/etc) на все каналы одной командой.
> Зоны ответственности: см. `~/.claude/.../memory/feedback_subtitles_claudian_only.md` + `feedback_automation_via_n8n.md`.

---

## 0. Кратко (как пользоваться)

```bash
# Реальная публикация — твоя команда в час релиза (T+0)
curl -X POST "http://206.189.103.48:5678/webhook/publish-hero" \
  -H "Content-Type: application/json" \
  -d '{"hero":"ODIN","publish":true}'

# Dry-run (без публикации, проверка цепи)
curl -X POST "http://206.189.30.48:5678/webhook/publish-hero" \
  -H "Content-Type: application/json" \
  -d '{"hero":"ODIN","publish":false}'
```

Webhook сразу вернёт `{"ok":true,"accepted":true}` — дальше workflow идёт **в фоне ~3.5 часа**:
- **T+0:** YouTube Shorts upload (unlisted)
- **+60 мин:** Instagram Reels + Facebook Page (параллельно, +pinned comment на IG авто)
- **+30 мин (T+90):** Telegram канал @ger_dennis_ai
- **+30 мин (T+2h):** Threads (currently skip — cross-post из IG автоматически)
- **Финал:** TG summary в DM Дениса со статусом каждого этапа

LinkedIn — отдельный поток через approval-gate бот, **на следующий день 10:00 CET**.

---

## 1. Что нужно ДО триггера webhook'а

| Файл | Кто делает | Где |
|---|---|---|
| `<HERO>_FINAL_subs.mp4` | **Claudian** (Whisper + ASS burn-in) | `~/Obsidian_AI_Brain/Projects/MotionViral/output/` |
| `<HERO>_PUBLICATION_PLAN.md` | **Claudian** (captions для всех платформ) | `~/Obsidian_AI_Brain/Projects/MotionViral/` |
| Receiver жив | terminal Claude / launchd `ai.contentmachine.receiver` | localhost:8787 |
| ngrok туннель | launchd `ai.contentmachine.ngrok` | `beastliest-luther-dissolutely.ngrok-free.dev` |
| n8n workflow ACTIVE | once setup | `Hero Multi-Channel Publisher` id `1PdEELXeSnVn6dBg` |

Если **что-то из этого отсутствует** — `/publish/hero` упадёт с понятной ошибкой и запишется в `MULTI_CHANNEL_LOG.md`.

---

## 2. Структура `<HERO>_PUBLICATION_PLAN.md`

Парсер `publish_hero.py` ищет секции по заголовкам:

| Heading | Парсится как | Используется |
|---|---|---|
| `## 1. YouTube Shorts` | `youtube` | title (code block), description, tags |
| `## 2. Instagram Reels` | `instagram` | caption, **первый** (=pinned comment) |
| `## 3. Telegram` | `telegram` | текст |
| `## 4. LinkedIn` | `linkedin` | skip (approval-gate) |
| `## 5. Facebook` | `facebook` | если нет — fallback на Instagram caption |
| `## 6. Threads` | `threads` | currently skip (cross-post из IG) |

**Ghost (gerdennisai.com/blog) — НЕ публикуется для героиков.** Блог только для лонгридов (G_B сам публикует 3×/день). Если в плане есть секция `## 4.5. gerdennisai.com/blog` — парсер игнорирует.

---

## 3. Troubleshooting

### 3.1 Webhook возвращает 404 "not registered"

n8n quirk: workflow создан через API, но webhook не в runtime. Fix:
1. Открыть n8n UI → workflow `Hero Multi-Channel Publisher`
2. **Cmd+S (Save)** — webhook регистрируется в runtime навсегда

### 3.2 IG publish таймаут / 403 от fal CDN

**Это не "ключ мёртв".** fal-client v1.0.0 первым пробует fal_v3 endpoint, он часто медленный → fallback на CDN. Первый upload может занять **до 2 минут**.

Действия:
1. Проверить receiver лог: `tail $(ls -t ~/Obsidian_AI_Brain/Projects/ContentMachine/receiver/logs/igupload_*.log | head -1)`
2. Если "upload OK" в логе — публикация прошла, просто медленно
3. Если "MissingCredentialsError" / "FAL_KEY" — рестарт receiver:
   ```bash
   launchctl kickstart -k gui/$(id -u)/ai.contentmachine.receiver
   ```
4. Если 3 retry подряд падают — копать (debug_token IG token, проверить FAL_KEY)

См. `memory/feedback_fal_v3_first_request_slow.md`.

### 3.3 Проверить что IG token живой

```bash
TOKEN=$(grep "^IG_ACCESS_TOKEN" ~/Obsidian_AI_Brain/Projects/MotionViral/.env | cut -d= -f2-)
curl -s "https://graph.facebook.com/v22.0/debug_token?input_token=$TOKEN&access_token=$TOKEN" | python3 -m json.tool
```

Должно вернуть `is_valid: true`, `expires_at: 0` (long-lived), 20 scopes.

**НЕ использовать `graph.instagram.com/me`** — он часто возвращает code 190 ложно. Только `graph.facebook.com/v22.0/debug_token`.

### 3.4 Token refresh

Auto: n8n cron `IG Token Refresh (monthly)` id `SGu07iSi9AqLiDMA` — 1 число каждого месяца 04:00 Berlin → TG alert когда обновится.

Manual:
```bash
SECRET=$(grep "^RECEIVER_SECRET=" ~/Obsidian_AI_Brain/Projects/ContentMachine/receiver/.env | cut -d= -f2-)
APP_SECRET=$(grep "^META_APP_SECRET=" ~/Obsidian_AI_Brain/Projects/MotionViral/.env | cut -d= -f2-)
curl -s -X POST "http://localhost:8787/token/refresh" \
  -H "X-Receiver-Secret: $SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"app_secret\":\"$APP_SECRET\",\"env_path\":\"$HOME/Obsidian_AI_Brain/Projects/MotionViral/.env\"}"
```

Receiver автоматически обновит `IG_ACCESS_TOKEN=` в `.env`.

### 3.5 Где смотреть результаты публикации

| Что | Где |
|---|---|
| Хронологический лог | `~/Obsidian_AI_Brain/Projects/genesis-content-os/MULTI_CHANNEL_LOG.md` |
| TG summary | DM Дениса (id `1357650155`) в конце каждого workflow |
| receiver IG | `receiver/logs/igupload_*.log` |
| receiver FB | `receiver/logs/fbupload_*.log` |
| receiver pinned comment | `receiver/logs/igcomment_*.log` |
| receiver publish_hero subprocess | `receiver/logs/publish_hero_*.log` |
| n8n executions | `http://206.189.103.48:5678/executions` |

---

## 4. Fallback CLI (если n8n не работает)

**Только в случае поломки n8n** (webhook 404 не лечится Save, droplet down, etc):

```bash
# T+0 (час релиза, например 17:00 CET)
~/Obsidian_AI_Brain/Projects/ContentMachine/.venv/bin/python \
  ~/Obsidian_AI_Brain/Projects/genesis-content-os/scripts/publish_hero.py \
  --hero ODIN --platforms youtube --publish

# +60 мин (18:00) — IG + FB
~/Obsidian_AI_Brain/Projects/ContentMachine/.venv/bin/python \
  ~/Obsidian_AI_Brain/Projects/genesis-content-os/scripts/publish_hero.py \
  --hero ODIN --platforms instagram,facebook --publish

# +30 мин (18:30) — TG
~/Obsidian_AI_Brain/Projects/ContentMachine/.venv/bin/python \
  ~/Obsidian_AI_Brain/Projects/genesis-content-os/scripts/publish_hero.py \
  --hero ODIN --platforms telegram --publish
```

Каждая команда логирует в `MULTI_CHANNEL_LOG.md`.

**По умолчанию: всегда через n8n.** CLI = только fallback.

---

## 5. Backup и восстановление workflows

Все 6 Genesis n8n workflows backup'аются в `Outbox/n8n-backups/2026-05-24/`. Откат при поломке:

```bash
~/Obsidian_AI_Brain/Outbox/n8n-backups/restore_genesis_workflows.sh
```

Восстановит: G_A_trend_scanner, G_A_topic_distiller, G_B_content_gen, YT_Shorts_Pilot_v0.4.

Hero_Multi_Channel_Publisher + IG_Token_Refresh_Monthly + Stale_Drafting_Recovery бекапятся в той же папке отдельными JSON.

---

## 6. Memory references (для других агентов)

| Memory | Назначение |
|---|---|
| `feedback_subtitles_claudian_only.md` | subtitles делает только Claudian |
| `feedback_automation_via_n8n.md` | публикация через webhook, CLI=fallback only |
| `feedback_fal_v3_first_request_slow.md` | 403 от fal CDN ≠ "ключ мёртв" |
| `feedback_receiver_diagnostics.md` | сначала читать receiver лог |
| `feedback_linkedin_approval_gate.md` | LinkedIn — отдельная зона |
