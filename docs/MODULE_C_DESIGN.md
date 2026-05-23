# Module C — Metrics Ingest

> Genesis Phase 2, первый из триплета C → D → E.
> Цель: собирать реальные метрики опубликованных материалов в `metrics_snapshots`,
> чтобы Module D мог делать evidence-based weekly review.

## Статус

- 2026-05-06: design + Python helper, n8n workflow ждёт восстановления DO droplet (тикет #12155813)
- Полный roll-out: после миграции pipeline на новый droplet (compose в `Projects/ContentMachine/deploy/`)
- **2026-05-19**: добавлен `lookup_post_id()` перед каждым INSERT + `--backfill` mode.
  Найден gap: Module B не пишет YT-публикации в `posts`, поэтому 1348 existing
  rows остаются orphan даже после backfill. Module B нужно расширить отдельно.
  Подробности — секция «post_id lookup behavior» ниже.

## Цель MVP

Собирать достаточно данных чтобы Module D через 2-3 недели мог сравнить:

- Какие `visual_type` дают больше view minutes на YT
- Какие промпт-версии (`prompt_version` в posts) работают
- Какой `ab_variant` побеждает (если будем A/B-тестить)

Метрика выбора: **average view duration** (sec) и **like-to-view ratio** на YouTube как первичные KPIs. На LinkedIn — **impressions** и **engagement rate**.

## Источники

| Платформа | Метрики MVP | API endpoint | Auth |
|---|---|---|---|
| **YouTube Data API v3** | views, likes, comments, duration | `/videos?part=statistics,contentDetails&id=<videoId>` | OAuth 2.0 (token.pickle, тот же что для upload) |
| **YouTube Analytics API** | avg_view_duration, retention | `/reports?metrics=views,averageViewDuration&filters=video=={id}` | OAuth 2.0 (same token, нужен scope `yt-analytics.readonly`) |
| **LinkedIn API v2** | impressions, likes, comments | `/socialMetadata/{urn}` | OAuth 2.0 (existing creds) |
| Plausible | pageviews, visitors | `/api/v1/stats/aggregate` | API token (если поднимем Plausible на сайте) |
| **Telegram** | view_count, reactions | ❌ Bot API не даёт. Workaround в Phase 3 через MTProto (telethon) | — |
| **Ghost** | views | ❌ Ghost не считает reads через API. Нужен Plausible/GA на /blog | — |

## Schema

`sql/001_initial_schema.sql` уже содержит:

```sql
CREATE TABLE metrics_snapshots (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  captured_at     timestamptz NOT NULL DEFAULT now(),
  platform        text NOT NULL,
  post_id         uuid REFERENCES posts(id) ON DELETE CASCADE,
  metric_name     text NOT NULL,
  metric_value    numeric NOT NULL,
  metadata        jsonb DEFAULT '{}'::jsonb
);
```

Каждый capture → N inserts (по одному на metric_name). Например для YT-видео за 1 cron-tick:
- (youtube, post_id, views, 1234)
- (youtube, post_id, likes, 56)
- (youtube, post_id, comments, 7)
- (youtube, post_id, avg_view_duration, 28.5)

## Cron schedule

- 06:00, 12:00, 18:00, 00:00 (каждые 6 часов, тот же режим что у других Genesis cron)
- Опрашивает только `posts` где `published_at > now() - interval '30 days'`
  - Старше 30 дней — метрики уже почти не меняются, не тратим API quota
- Cap: 50 posts за tick (YT API даёт 10k units/day, /videos?id={id} = 1 unit)

## Workflow nodes (n8n, после восстановления droplet)

```
[Cron 6h]
  └─→ [Supabase: SELECT posts where published_at > now()-30d AND status='published']
      └─→ [Split into batches by platform]
          ├─→ YT branch:
          │   ├─→ [HTTP youtube /videos?part=statistics]
          │   ├─→ [HTTP yt-analytics /reports avg_view_duration]
          │   └─→ [Supabase: INSERT INTO metrics_snapshots (one row per metric)]
          │
          ├─→ LinkedIn branch:
          │   ├─→ [HTTP linkedin /socialMetadata/{urn}]
          │   └─→ [Supabase: INSERT INTO metrics_snapshots]
          │
          └─→ [TG announce success/fail]
```

## Helper script (без n8n)

`scripts/g_c_metrics.py` — Python CLI который умеет то же что workflow без n8n.

```bash
# Прогнать единичный сбор сейчас
python scripts/g_c_metrics.py --once

# Только YT (для quick test)
python scripts/g_c_metrics.py --platform youtube --once

# Test mode: print без INSERT в Supabase
python scripts/g_c_metrics.py --once --dry-run
```

Использование:
- На VPS: можно стартовать через cron вместо n8n до полной миграции
- Локально: для tests + bootstrap данных (опросить prior YT-видео которые уже опубликованы R12, R9, R3436)

## Bootstrap прежних данных

Чтобы Module D имел минимум 14 дней данных уже в день старта, на первом запуске:
1. Опрашиваем все YT-видео из `posts` где `external_id IS NOT NULL` AND platform='youtube'
2. Получаем текущий snapshot (1 row на metric)
3. Module D первую неделю работает с малым объёмом — норм, цикл наладится

## post_id lookup behavior (2026-05-19)

Каждый INSERT в `metrics_snapshots` теперь обязан резолвить `posts.id` ПЕРЕД
записью. Без этого Module D/F не могут JOIN metrics → posts → topics и метрики
выглядят бесхозными (1348 orphan rows на момент верификации 2026-05-19).

### Matching logic

`g_c_metrics.lookup_post_id(platform, external_id)`:

| Платформа | external_id для lookup | Источник в metadata |
|---|---|---|
| youtube | `video_id` | `metadata.video_id` |
| linkedin | URN (`urn:li:share:...` / `urn:li:ugcPost:...`) | `metadata.urn` |
| ghost_ru / ghost_en | Ghost post slug или uuid | `metadata.external_id` (TODO) |
| telegram | message_id | `metadata.external_id` (TODO) |

SQL-эквивалент:
```sql
SELECT id FROM posts WHERE platform = $1 AND external_id = $2 LIMIT 1;
```

Если `posts` row не найден → `post_id=NULL` (как раньше), но печатается
warning в stderr (один раз на уникальный `(platform, external_id)` благодаря
in-memory кэшу `_POST_ID_CACHE`). Кэш живёт на время одного процесса.

### Backfill mode

Однократный режим для починки уже накопленных rows с `post_id IS NULL`:

```bash
# Сухой прогон — печатает counts, ничего не пишет
python scripts/g_c_metrics.py --backfill --dry-run

# Реальный UPDATE
python scripts/g_c_metrics.py --backfill
```

Алгоритм:
1. SELECT batches `metrics_snapshots WHERE post_id IS NULL` (страницы по 1000).
2. Для каждой row: извлечь `external_id` из `metadata` по правилам выше.
3. `lookup_post_id` → если найден, PATCH `metrics_snapshots SET post_id=...`.
4. Если нет — orphan, логируется уникальный `external_id` per platform.

Итоговая статистика: `scanned / matched / orphan / errors`.

**Прогон 2026-05-19** (verified против Supabase czzzdhzzvtewvhcrlryr):
- `scanned=1348 matched=0 orphan=1348 errors=0`
- 13 уникальных YT `video_id` — ни один не присутствует в `posts` с
  `platform='youtube'`.
- **Это значит реальный gap не в Module C** (lookup-логика теперь корректна),
  **а в Module B**, который не записывает YouTube-публикации в `posts`. Сейчас
  YT-публикации живут только в legacy `yt_published(slug, video_id)`.

### Follow-up fix (Module B)

Чтобы backfill стал эффективным, Module B (publisher) или одноразовая миграция
должны создать `posts` rows для YT-публикаций:

```sql
INSERT INTO posts (id, platform, external_id, external_url, slug, status,
                   published_at, topic_id, title)
SELECT gen_random_uuid(), 'youtube', yp.video_id,
       'https://www.youtube.com/watch?='||yp.video_id,
       yp.slug, 'published', yp.published_at,
       <topic_id_from_topics_lookup_via_slug>,
       <title_from_runs_or_yt_metadata>
FROM yt_published yp
WHERE NOT EXISTS (
  SELECT 1 FROM posts p
   WHERE p.platform = 'youtube' AND p.external_id = yp.video_id
);
```

После выполнения этой миграции — повторно `python scripts/g_c_metrics.py
--backfill` и rows станут связаны.

### Inline вставка (ingest path)

Все три fetcher'а (`fetch_youtube_metrics`, `fetch_youtube_analytics`,
`fetch_linkedin_metrics`) теперь вызывают `lookup_post_id` перед формированием
snapshot dict. С момента деплоя этого фикса новые rows получают `post_id` если
он резолвится; иначе пишутся с `NULL` и orphan-warning'ом — это видно сразу.

## Quotas и лимиты

- **YT Data API**: 10,000 units/day. `/videos?id={id}` = 1 unit. Запас огромный — 50 video × 4 ticks/day = 200 calls/day.
- **YT Analytics**: 100 queries/100 sec. Не лимит для нашего объёма.
- **LinkedIn**: ~500 calls/day на personal access token. Достаточно.

## Связь с Module D

Module D (G_D_analyzer) каждый понедельник:
1. SELECT * FROM metrics_snapshots WHERE captured_at > now() - 7 days
2. JOIN posts ON metrics.post_id = posts.id
3. JOIN topics ON posts.topic_id = topics.id
4. GROUP BY visual_type, prompt_version, hype_score_bucket
5. → GPT-4 промпт «вот метрики, найди сильные/слабые сигналы»
6. INSERT INTO insights (week_iso, hypothesis, evidence)

Без Module C → Module D нечего анализировать. Без Module D → Module E (auto-decision) бесполезен. C — фундамент Phase 2.

## Что нужно для деплоя

1. Восстановление DO droplet (тикет #12155813) — БЛОКЕР
2. Доплата Module C credentials в новом n8n:
   - `youtube_oauth` — токен из `~/Obsidian_AI_Brain/.youtube/token.pickle` (уже есть)
   - `linkedin_oauth` — re-auth в новом UI (засветился старый, всё равно надо пересоздать)
3. Импорт `workflows/G_C_metrics_ingest.json` (создаётся после approve этого design)
4. Активация cron
5. Прогон bootstrap для prior YT-видео (R12, R9, R3436, R7763)

## Roadmap после MVP

- **Phase 3 TG metrics** через telethon (user-account) — нужен LegitID logged-in `userbot`
- **Plausible** на gerdennisai.com (бесплатный self-hosted или Cloud $9/mo)
- **A/B тесты**: ab_variant в posts уже есть (column есть), нужен механизм деления топиков 50/50
- **Retention curves** для YT (analytics API более детальные данные)
