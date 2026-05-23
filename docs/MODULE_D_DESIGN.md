# Module D — Analyzer

> Genesis Phase 2, второй из триплета C → D → E.
> Цель: еженедельно превращать сырые `metrics_snapshots` в **insights** —
> структурированные гипотезы с evidence для Module E.

## Статус

- 2026-05-06: design + skeleton helper
- Полный roll-out: после того как Module C соберёт ≥7 days time-series

## Цель MVP

Раз в неделю (понедельник 09:00 Berlin) GPT-4 разбирает накопленные метрики и пишет insights в формате:

> **insight:** *avg_view_duration на видео с visual_type=numbered_macbook на 32% выше чем на comparison_strikethrough*
> **evidence:** R3436 numbered = 28.5s, R7763 comparison = 21.6s, R12 numbered = 31.2s, n=7
> **confidence:** medium (need ≥10 videos per type)
> **suggestion:** prompts.scenario_v2 → bias на numbered_macbook (60% вместо 28%)

Module E (следующий) использует insights для генерации PR с новыми prompt versions.

## Источник: накопленные `metrics_snapshots` JOIN posts JOIN topics

Модель данных:
```sql
SELECT
  ms.captured_at,
  ms.metric_name,
  ms.metric_value,
  ms.metadata->>'video_id' AS video_id,
  ms.metadata->>'slug' AS slug,
  topics.topic_category,
  topics.hype_score,
  -- visual_type/avatar_position/avatar_size — пока не в schema posts,
  -- читаем из scenario.json в run_dir по slug (или future: добавить колонку)
FROM metrics_snapshots ms
JOIN yt_published yp ON yp.video_id = ms.metadata->>'video_id'
LEFT JOIN topics ON topics.video_slug = yp.slug
WHERE ms.captured_at > now() - interval '7 days'
  AND ms.platform = 'youtube'
ORDER BY ms.captured_at DESC;
```

**Проблема:** scenarios (visual_type/composition) хранятся в `runs/<slug>/scenario.json` на диске VPS, не в БД. Module D нужен доступ к ним.

**Решение:**
- Module C v0.2 при insert в metrics_snapshots копирует scenario hash + visual_types в `metadata.scenario` поле
- ИЛИ Module D читает scenario.json через runs/<slug>/scenario.json prefix — нужен SSH доступ к droplet
- ИЛИ добавляем колонку `scenario_summary jsonb` в `yt_published` (visual_type per segment, avatar_size, prompt_version)

Выбор: **колонка scenario_summary**, заполняем при render → publish. Простейшее.

## Анализ — что считаем

### Per-video метрики
- views (last)
- avg_view_duration_sec (last) — нужен YouTube Analytics API enabled
- like_to_view_ratio = likes / views
- engagement_rate = (likes + comments) / views

### Группировки для GPT
- by `visual_type` (hook_layered / numbered_macbook / project_chips / etc.)
- by `avatar_size` (full / half / third / card)
- by `topic_category` (claude-code / mcp / agents / rag / llmops / webdev)
- by `hype_score_bucket` (0-50 / 50-75 / 75-100)
- by `prompt_version` (если будем версионировать)
- by `ab_variant` (A/B test, future)

### GPT-4 промпт

```
Ты analyzer для Genesis Content OS. Тебе даны метрики YouTube Shorts видео за
последние 7 дней. Найди 1-3 statistically meaningful паттерна и сформулируй
гипотезы для улучшения продакшна.

Метрики:
[JSON массив: video_id, slug, visual_types, avatar_size, prompt_version, topic_category,
 hype_score, captured_at, views, avg_view_duration_sec, like_to_view_ratio]

Формат ответа (JSON):
{
  "insights": [
    {
      "hypothesis": "<one sentence>",
      "evidence": "<видео ids + конкретные числа>",
      "confidence": "low|medium|high",
      "n": <sample size>,
      "suggestion": "<actionable change to prompts/scenario>"
    }
  ],
  "data_quality_notes": "<если данных мало — кратко>"
}

ВАЖНО:
- Не выдумывай числа — используй только из data
- confidence=low при n<5
- suggestion должен быть конкретным: "увеличить bias на X в prompts.scenario_v2"
```

## Schema

`sql/001_initial_schema.sql` уже имеет таблицу `insights`:

```sql
CREATE TABLE insights (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  week_iso        text NOT NULL,           -- '2026-W18'
  hypothesis      text NOT NULL,
  evidence        jsonb NOT NULL,
  confidence      text CHECK (confidence IN ('low', 'medium', 'high')),
  sample_size     int,
  suggestion      text,
  status          text DEFAULT 'open' CHECK (status IN ('open', 'accepted', 'rejected', 'applied')),
  applied_to      text,                    -- prompts.version если применено
  created_at      timestamptz DEFAULT now()
);
```

## Cron

- **Понедельник 09:00 Berlin** — раз в неделю
- Один прогон ~1-2 минуты + 1 GPT-4 call (≈$0.30-0.60)
- Annual cost: ~$25/year (52 запуска)

## Helper

`scripts/g_d_analyzer.py` — standalone, как и Module C. Может работать без n8n.

```bash
# Запуск сейчас (последние 7 дней)
python g_d_analyzer.py --once

# Конкретная неделя
python g_d_analyzer.py --week 2026-W18

# Dry-run: печатает GPT prompt и не делает API call
python g_d_analyzer.py --once --dry-run

# Не пишет в insights table
python g_d_analyzer.py --once --no-insert
```

## Связь с Module E

Module E (G_E_auto_decision):
1. SELECT * FROM insights WHERE status='open' AND confidence IN ('medium','high')
2. ORDER BY created_at DESC, suggestion is not null
3. Для каждого insight:
   - GENERATE: новая версия `prompts` row с обновлённым prompt template
   - INSERT: prompts (parent_version=current, version=current+0.0.1, rationale=insight.hypothesis)
   - github API: open PR в `auto/prompt-{week_iso}` branch
   - UPDATE: insights.status='applied', applied_to=prompts.version
4. Денис мерджит PR (или auto-merge при confidence=high)

Без Module D → Module E не имеет input. D — фундамент E.

## Roadmap

- **MVP**: 1 запрос на 1 GPT-4 call (input: 7-day data, output: 1-3 insights)
- **v0.2**: A/B testing — Module D определяет winning variant если ab_variant был
- **v0.3**: monthly retrospective — long-term trends (4 недели агрегата)
- **v0.4**: cross-channel analysis — корреляция между YT views и blog/LinkedIn engagement

## Что нужно перед стартом

1. ⏳ **Накопить данные** — Module C cron должен отработать минимум 7 дней (~28 ticks × 7 видео = 196 datapoints)
2. ⏳ **YouTube Analytics API** включен (для avg_view_duration — самая важная метрика)
3. ⏳ **scenario_summary** колонка в `yt_published` — чтобы знать visual_types per video. Можно ввести retroactively на текущие 7 видео по их runs/.
4. ⏳ **OPENAI_API_KEY** в env — для GPT-4 call

## Итоговая последовательность

| Этап | Когда | Кто |
|---|---|---|
| 1. Module C cron собирает данные | 06.05 → ... | mac LaunchAgent |
| 2. Module C переезжает в n8n на DO | после восстановления droplet | Claudian |
| 3. ≥7 days time-series накопилось | ~13.05 | автомат |
| 4. **Module D первый запуск** | пн 18.05 09:00 | manual run + n8n cron |
| 5. Insights в БД | 18.05 | автомат |
| 6. Module E генерит первый PR с обновлёнными prompts | 18.05 evening | автомат + Денис мерджит |
| 7. Следующий generation цикл с обновлёнными prompts | 19.05 09:00 | автомат |
| 8. **Genesis эволюционирует сама** | 19.05 → ∞ | — |
