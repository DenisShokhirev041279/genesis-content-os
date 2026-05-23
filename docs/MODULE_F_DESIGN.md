# Module F — Weekly Reports

> Genesis Phase 2, замыкающий 6 модулей.
> Цель: каждое воскресенье 09:00 Berlin собирать недельный digest
> (KPI + top/flop posts + insights + очередь тем) и слать Денису в Telegram +
> сохранять snapshot в `weekly_reports`.

## Статус

- 2026-05-18: design + skeleton (`scripts/g_f_weekly_report.py`)
- 2026-05-19: schema adaptation — реальная схема `posts/metrics_snapshots`
  отличается от `research/GENESIS_architecture.md` §1; default window
  сменён на rolling 30d. См. секцию «Schema adaptation 2026-05-19» ниже.
- Активация: после dry-run + ручного подтверждения Дениса

## Цель MVP

Раз в неделю (воскресенье 09:00 Berlin) Денис получает в ТГ структурированный
отчёт-digest. Параллельно snapshot KPI пишется в `weekly_reports` — это:
1. Аудит-журнал для Webby submission ("вот наша система прозрачно сама себя
   отчитывает 52 раза в год").
2. Источник данных для Module D следующего цикла (LongTerm trends).
3. Резерв если TG-доставка упадёт.

## Источники

| Что | Откуда | Зачем |
|---|---|---|
| KPI totals | `kpi_daily` (materialized view) | агрегаты по платформам/метрикам |
| Top-5 / Flop-5 posts | `posts` + `metrics_snapshots` JOIN | какой контент сработал |
| Insights | `insights` created_at в окне | что Module D нашёл |
| Topics queue | `topics` group by status + top by priority | план на след. неделю |

## Composite score

Совпадает по идее с Module D, но универсальный (не только YT):

```
score = impressions × (saves + shares + favorites + reposts + 1) × (CTR + 0.01)
```

Где:
- `impressions` ← `impressions | view | views | pageviews` (любая метрика в `metrics_snapshots.metric_name`)
- `saves+shares` ← `save | share | favorite | repost`
- `CTR` ← `ctr | click_through_rate`, fallback `clicks / impressions`

Логика выбора: модерация платформ — YT/LI/Blog имеют разные первичные KPIs,
score даёт сопоставимое ранжирование. `+1` и `+0.01` чтобы посты с 0 шер/CTR
не обнулялись для top-3 (но всё равно проигрывали тем у кого есть signal).

## Workflow

```
Cron: Sun 09:00 Europe/Berlin (launchd ai.genesis.weekly_report)
  ↓
SELECT kpi_daily WHERE day IN [last_monday, this_monday)
SELECT kpi_daily WHERE day IN [prev_monday, last_monday)   ← для %-change
  ↓
SELECT posts WHERE published_at в окне AND status='published'
SELECT metrics_snapshots WHERE post_id IN (...)
  ↓ aggregate
top-5 by composite score / flop-5 (среди тех у кого есть metrics)
  ↓
SELECT insights WHERE created_at в окне
SELECT COUNT(topics) GROUP BY status
SELECT topics WHERE status='queued' ORDER BY priority DESC LIMIT 10
  ↓
GPT-4 → markdown digest (русский, voice Дениса, ≤1500 chars)
  ↓
INSERT INTO weekly_reports (week_iso UNIQUE, period_start, period_end,
                            markdown_body, kpi_summary jsonb, delivered_to[])
  ↓
Telegram: bot → DM Денис (chat_id 1357650155)
  ↓
PATCH weekly_reports SET delivered_at=now(), delivered_to=['supabase','telegram_denis']
```

## Schema (используем existing)

`sql/001_initial_schema.sql` уже содержит таблицу:

```sql
CREATE TABLE weekly_reports (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  week_iso        text UNIQUE NOT NULL,    -- '2026-W20'
  period_start    date NOT NULL,
  period_end      date NOT NULL,            -- inclusive (last_sunday)
  markdown_body   text NOT NULL,
  kpi_summary     jsonb NOT NULL,
  delivered_to    text[],
  delivered_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);
```

`UNIQUE(week_iso)` → повторный запуск той же недели через
`Prefer: resolution=merge-duplicates` (upsert) не дублирует строки.

`kpi_summary jsonb` содержит:
```json
{
  "totals_current":  {"linkedin.impressions": 1234, ...},
  "totals_previous": {...},
  "posts_count": 14,
  "videos_count": 3,
  "insights_count": 2,
  "topics_by_status": {"queued": 8, "published": 47, ...},
  "top_post_ids": ["uuid1", ...],
  "flop_post_ids": ["uuid2", ...]
}
```

## Формат отчёта (markdown, voice Дениса)

```
📊 Неделя 12.05–18.05
Опубликовано: 14 постов, 3 видео.

Метрики:
- LI: 5,420 impressions (+18% vs прошлой)
- Blog: 1,230 visits (+5%)
- YT: +12 subs

Топ-3 контента:
1. «Language anchoring in LLMs» — Blog, score 84.2, 312 views × 0.06 CTR
2. «Prompt v8 ablation» — LI, score 61.0, 980 impr × 4 saves
3. «MCP cookbook» — TG, score 44.1, 1.2k views

Провал недели:
- «YT Shorts pipeline retro» — YT, score 1.8 (29 views, нет shares).
  Гипотеза: hook+visuals не зацепили — не противоречит scenario_v3.

Что изменилось:
- prompt scenario_v2 → v3 (insight ab12cd: numbered_card +27% retention, applied).

Предложения:
- Reduce comparison_strikethrough share from 30% to 10%, redistribute to project_chips (confidence: medium, n=8).

В очереди на следующую неделю:
- «Claude Code 4.7 vs Codex»  (claude-code, priority 92, hype 71)
- «Memory-aware agents pattern» (agents, priority 85, hype 64)
- ...
```

Длина строго ≤1500 символов чтобы влезть в одно TG-сообщение
(лимит TG = 4096, оставляем запас на эмодзи/escape).

## CLI

```bash
# Прошлая полная неделя (PROD-режим: пишем в БД и TG)
python g_f_weekly_report.py --once

# Конкретная неделя
python g_f_weekly_report.py --week 2026-W20

# Dry-run: ничего не делаем, только печатаем payload
python g_f_weekly_report.py --once --dry-run

# OpenAI зовём, но в БД не пишем (тест промпта)
python g_f_weekly_report.py --once --no-insert

# В БД пишем, в TG не шлём (тест перед активацией)
python g_f_weekly_report.py --once --no-tg
```

## Edge cases / safety

1. **Supabase недоступен** → `urllib.error.URLError` → TG alert
   «Module F: Supabase недоступен» через bot token (если есть) → exit 3.
   Не silent fail — Денис увидит.
2. **`weekly_reports` row уже есть** на эту неделю → `Prefer: resolution=merge-duplicates`
   делает upsert по `week_iso UNIQUE`, переписывает markdown.
3. **OpenAI 429/500** → TG alert + exit 6, БД и TG нетронуты (т.к. INSERT
   и send были после OpenAI). Cron повторит через неделю; для ручного retry —
   запустить `--week YYYY-Www`.
4. **Telegram down** → INSERT в БД уже сделан → следующий запуск не дублирует
   (UNIQUE week_iso). Дениса алертит сам alert-механизм (best-effort).
5. **Posts c 0 метрик** → не попадают во flop (score=0 — мусор), top их видит
   только если других нет. Это намеренно: flop-5 = «провалы среди измеримого».
6. **Нет postов за неделю** → digest всё равно генерится (просто `n=0`), Денис
   получит честный отчёт «пустая неделя». Не падаем.
7. **`metric_name` именования различаются между платформами** — `composite_score`
   ищет несколько алиасов (`view | views | impressions | pageviews`).
   При расширении Module C — пополнять список в `composite_score`.

## Cron / launchd

- **Воскресенье 09:00 Berlin** — `ai.genesis.weekly_report.plist`
- Один прогон: 3-5 сек fetch + 5-10 сек OpenAI = ~15 сек
- Annual cost: ~$15-25 (52 запуска × $0.30-0.50 на GPT-4 weekly digest)

Plist использует тот же wrapper-паттерн что Module D/E:
- `bash ~/.local/bin/g_f_weekly_report_run.sh`
- внутри подгружает `~/.local/bin/.g_f_env` (содержит Supabase / OpenAI /
  Telegram токен) и зовёт скрипт через ContentMachine venv.

`RunAtLoad=false` — НЕ запускается при загрузке plist, только по расписанию.
Это намеренно — Денис включает после ручного review первого dry-run.

## Связь с другими модулями

- **C → F:** F читает `metrics_snapshots` (через `kpi_daily` view) для top/flop scoring.
- **D → F:** F читает `insights` за неделю и кладёт в раздел «Что изменилось» / «Предложения».
- **E → F:** F видит applied insights (Module E пометил `status='applied'`) — что реально докатилось в prompts.
- **F → D (future):** weekly_reports.kpi_summary можно использовать в long-term trends (Module D v0.3 monthly retrospective).

Без D → раздел «Что изменилось» будет пустой. Без C → top/flop посчитается на пустых метриках. F самодостаточен, но без C/D digest пустой.

## Что нужно перед стартом

1. ✅ Schema (`weekly_reports` уже в `001_initial_schema.sql`)
2. ✅ Скрипт `g_f_weekly_report.py` + design doc
3. ✅ Plist `ai.genesis.weekly_report.plist` (создан, не загружен)
4. ⏳ Wrapper-скрипт `~/.local/bin/g_f_weekly_report_run.sh` (создаст Денис или Claudian после approval)
5. ⏳ Env-файл `~/.local/bin/.g_f_env` (Supabase + OpenAI + TG bot token + Denis chat_id)
6. ⏳ Dry-run review Денис → бутстрап launchd через `launchctl bootstrap gui/$UID …`

## Roadmap после MVP

- **v0.2** — attach PDF (html-to-pdf через wkhtmltopdf) с графиками KPI неделя/неделя
- **v0.3** — daily mini-digest по утрам (вместо weekly only)
- **v0.4** — embeddable «public weekly» на `gerdennisai.com/genesis/reports` (Webby артефакт)
- **v0.5** — Slack/Email доставка как fallback если TG молчит

---

## Schema adaptation 2026-05-19

Утром 2026-05-19 при first dry-run на неделе W20 скрипт вернул 0 posts /
0 metrics. Первоначально это интерпретировали как «Module B/C сломаны».
Проверка через anon-key показала: модули работают, но реальная схема
расходится с design.md (`research/GENESIS_architecture.md` §1).

### Расхождение схем

| Что | Design.md (research) | Реальная схема (verified 2026-05-19) |
|---|---|---|
| `posts.channel` | column `channel` | column **`platform`** (значения: `telegram`, `ghost_ru`, `ghost_en`, `linkedin`) |
| `metrics_snapshots` | агрегатные columns: `impressions`, `clicks`, `reactions`, `saves`, `shares` | long-form: `metric_name` + `metric_value` |
| `metrics_snapshots.platform` | n/a | column `platform` (пока только `youtube`) |
| `metrics_snapshots.post_id` | FK posts | nullable; **сейчас все строки `NULL`** — Module C пишет только channel-level YT метрики (1348 строк за 30 дней) |
| Реальные `metric_name` | n/a | `view`, `like`, `comment`, `favorite` (YouTube) |

`kpi_daily` materialized view существует, но на 2026-05-19 пустой
(0 строк за 30 дней). Module D реализует aggregation, но рефреш не настроен.

### Что поправили в `g_f_weekly_report.py`

1. **`channel` → `platform`** — в коде уже было `platform`, проверили все
   места (`fetch_posts_window`, `kpi_totals`, `slim_post`, фильтры).
2. **`composite_score` переписан** под long-form metric_name/metric_value:
   ```
   score = views * (likes + comments + favorites + 1)
   ```
   Алиасы для views: `view | views | impressions | pageviews`.
   Для engagement: `like/likes`, `comment/comments`, `favorite/save/share/repost`.
   Старый множитель `(CTR + 0.01)` убран — Module C пока не пишет clicks
   или CTR. Когда Module C расширится — добавить обратно как опц. фактор.
3. **Default окно — последние 30 дней** (rolling), вместо «прошлая ISO-неделя».
   - `--week YYYY-Www` остался для конкретной недели (cron Sunday 09:00).
   - `--days N` добавлен для произвольного окна.
   - `prev_start/prev_end` теперь = равный по длине предыдущий период.
4. **`week_iso` label** для rolling окна = `YYYY-MM-DD_lastNd`
   (`2026-05-19_last30d`). UNIQUE constraint в `weekly_reports` это
   обрабатывает корректно — каждый снапшот = отдельная строка.

### Verified dry-run (2026-05-19, --once --dry-run --days 30)

- Окно: `2026-04-20 → 2026-05-20 (30d)`
- KPI rows: 0 current / 0 prev (`kpi_daily` пустой)
- Posts in window: **9** (3 ghost_en + 3 ghost_ru + 3 telegram; всё status=published)
- Posts with metrics: **0** (Module C ещё не пишет per-post метрики)
- Insights в окне: 2 (1 applied, 1 proposed)
- Topics by status: 30 published, 1 queued
- Top-5: 5 первых по дате (score=0 у всех, так как метрик нет)
- Flop-5: пусто (корректно — нет постов с метриками для ранжирования)

### Известные gaps (не баги Module F, а downstream)

- **Module C** должен начать писать metrics_snapshots с `post_id` для
  ghost/telegram/linkedin (сейчас только YT channel-level).
- **`kpi_daily` рефреш** — нужен cron `REFRESH MATERIALIZED VIEW kpi_daily`
  (или event-trigger от Module C).

Эти gap'ы фиксируются отдельно. Module F готов работать сразу как только
данные начнут приходить — формула и aggregation корректные.

### Active vs. activated

Скрипт сейчас работает, dry-run ходит в реальную БД, корректно показывает
посты и пустые метрики. До активации launchd plist'а Денис должен:
1. Решить нужен ли первый report «прямо сегодня» (rolling 30d) или ждать
   первого воскресенья с настоящим weekly cycle.
2. Подтвердить готов ли получить report с нулями в Top-5 score (это
   честная картина — нет per-post метрик пока Module C не доедет).
3. После confirm — `launchctl bootstrap gui/$UID ai.genesis.weekly_report.plist`.
