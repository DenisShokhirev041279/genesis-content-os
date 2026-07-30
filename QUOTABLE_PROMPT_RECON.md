# Quotable-промпт — READ-ONLY разведка (Claudian, 2026-07-30)

> Никаких production writes. Только чтение: git log, n8n API GET, raw GitHub, shasum.
> Это ПРЕДЛОЖЕНИЕ на ревью. Перед любой прод-правкой — отдельное подтверждение Дениса.

## 1. Точный источник активного article-промпта  ✅ определено
- **Файл:** `prompts/content_factory.md`
- **Живёт на:** GitHub `DenisShokhirev041279/genesis-content-os` **main** (raw).
- **Кто читает:** n8n workflow **`G_B_content_gen v0.1`** (`pcm9D4z1Yw2zy29t`, **ACTIVE**), нода **`Prepare Prompts + URLs`** (Code) тянет `raw.githubusercontent.com/.../main/prompts/content_factory.md` в рантайме и подставляет `{{IDENTITY}}/{{TITLE_RU}}/{{TITLE_EN}}/{{DATE}}/{{LI_STRUCTURE}}`. **Fallback** — прежний inline-промпт в ноде (вывод идентичен).
- **Hash (live):** `a129601eb4b0fc78192b236ba0007713f0f36aa9735bff25dc15091102889f9c` — vault-репо == GitHub main (совпали).
- **Git-версия:** commit `d71b9b5` (2026-07-12), с тех пор НЕ менялся.

## 2. Какой промпт/версию использовала последняя статья  ✅
- Генератор тянет `content_factory.md` с main при КАЖДОМ прогоне → последняя статья использовала текущий main = `d71b9b5` / hash `a129601e`.
- `posts.prompt_version` генератором **не заполняется** (подтверждено в коде) → атрибуция версии = по git-коммиту `content_factory.md`, не по колонке БД.

## 3. Data flow: source → generation → measurement → auto-PR → activation/rollback
```
topics (Supabase, Module A distiller)
      │
      ▼
n8n G_B: Prepare Prompts+URLs ──fetch──▶ content_factory.md @ GitHub main
      │                                   (fallback: inline)
      ▼
GPT-4.1 Write Bilingual → Parse Bilingual Response → Publish RU/EN/DE (Ghost) → Mirror to posts
      │  ⚠️ НЕТ ноды-валидатора между Parse и Publish
      ▼
metrics (Module C) → analyzer (Module D, insights)
      ▼
Module E  g_e_auto_decision.py → PR к prompts/*.md
      │   detect_prompt_file() возвращает ТОЛЬКО:
      │     topic_distiller.md | scenario_v2.md | scenario_v3.md
      │   (REWRITE_SYSTEM = «vertical short-video scenario». content_factory НЕ таргетится)
      ▼
merge → g_e_post_merge_sync.py → Supabase `prompts` table (active version swap)
      ▼
E-measure g_e_trial_measure.py:
   MODULE_METRIC = { scenario_v2/v3/topic_distiller → (youtube, view) }
   content_factory_* → «тонкие данные → insufficient_data» (НЕ мерится, НЕ откатывается)
   регресс ≥25% YT-view → авто-merge реверт-PR + swap версий в table
```

## 4. Может ли Module E перезаписать ручную правку content_factory.md?  → **НЕТ (сегодня)**
Три независимых барьера в текущем коде:
1. `g_e_auto_decision.detect_prompt_file()` **никогда не возвращает `content_factory.md`** (только scenario_v2/v3 + topic_distiller).
2. `REWRITE_SYSTEM` явно про short-video scenario — не про блог-статью.
3. `MODULE_METRIC` для content_factory = `insufficient_data` → E-measure ничего не мерит и не откатывает.
**Риск только теоретический** — если кто-то расширит scope Module E или Module-D-insight укажет на content_factory. → закрываем defense-in-depth (см. §7).

## 5. Что проверяет E-measure и какие инварианты можно сделать immutable
- **E-measure меряет только YouTube views (engagement), не качество/структуру.** Значит quality-инварианты **нельзя** доверять E-measure — их нужно держать **структурным ВАЛИДАТОРОМ** (нода после генерации) + защищённой секцией промпта.
- В n8n G_B **валидатора вывода нет** (Parse → сразу Publish). Точка внедрения = новая Code-нода между `Parse Bilingual Response` и `Publish RU to Ghost`, либо расширить `Parse Bilingual Response`.

## 6. Что промпт УЖЕ содержит (не дублировать)
content_factory.md уже требует: 950-1200 слов; **нет выдуманных названий**; **банлист hype-фраз**; identity-anchor в первом абзаце; **цифры только с источником+годом+ссылкой ИЛИ от первого лица**; JSON-LD Article (⚠️ Ghost вырезает `<script>` из тела поста — поэтому head-schema теперь даёт мой Astro-шаблон, G2); FAQ-блок (`<h2>FAQ</h2>` + Q&A); `<table>` при сравнении 3+; закрытие вопросом+CTA; per-language правила (RU/EN/DE, Sie-Form, DSGVO/BSI/NIS2).

## 7. PROPOSED DIFF — quotable output contract (на ревью, НЕ применено)

### 7a. Добавить в `content_factory.md` защищённую секцию (constraints 9-12)
Помеченную `<!-- PROTECTED OUTPUT CONTRACT — Module E optimizes WITHIN, never removes -->`:

- **9. ANSWER-FIRST LEAD.** Сразу после hook-абзаца — 1-2 предложения **прямого ответа** на главный вопрос статьи: самодостаточное, цитируемое утверждение, которое LLM может вынуть дословно. (Hook из п.4 остаётся, затем — ответ.)
- **10. QUESTION / SEARCH-INTENT H2.** Минимум половина `<h2>` — в форме вопроса или поискового intent, как человек гуглит («Wie viel kostet ein Claude-Agent im Produktivbetrieb?», не «Kosten»). FAQ-блок остаётся.
- **11. KEY TAKEAWAYS.** Сразу после lead — `<h2>Key takeaways / Итоги / Das Wichtigste</h2>` + 3-5 `<li>` самодостаточных выдираемых буллетов (каждый = отдельное цитируемое утверждение). Это quotable-summary.
- **12. OWN DATA vs EXTERNAL — явные метки.** First-hand: префикс «In my production deployments / В моих проде-развёртываниях / In meinen Produktivsystemen…». External: только named source + год + ссылка (уже п.5). НИКОГДА не смешивать. Выдуманных цифр нет (п.2+5).
- Усилить п.8 (HTML structure): добавить Key-takeaways сразу после lead; списки/таблицы «везде, где утверждение извлекается лучше как список».

### 7b. Валидатор (реальная гарантия, не зависит от дрейфа промпта)
Новая n8n Code-нода `Validate Article Contract` (между `Parse Bilingual Response` и `Publish RU to Ghost`) проверяет каждую из RU/EN/DE:
- ≥ 2 вопрос-H2; наличие Key-takeaways `<ul>` в верхней трети; FAQ-блок; lead-абзац декларативный (answer-first эвристика); отсутствие банлист-фраз (грепом — список уже в промпте); длина 950-1200.
- Провал → 1 ретрай генерации; повторный провал → флаг в Telegram (как approval-gate) + не публиковать. Fail-closed.

### 7c. Defense-in-depth от Module E
- В `content_factory.md` пометить секцию `PROTECTED`.
- В `g_e_auto_decision.detect_prompt_file()` добавить явный EXCLUDE `content_factory.md` (чтобы никогда не мог стать целью, даже если scope расширят).

## 8. Shadow / A-B rollout
- Колонка `posts.ab_variant` уже существует. A-B: генерить статьи с тегом `ab_variant='quotable_v1'` vs `'baseline'` (чередование дней / coin-flip в `Prepare Prompts`).
- Метрика — НЕ YouTube (E-measure для content_factory = insufficient_data), а **retrieval-сигналы**: GSC impressions/clicks по статьям + датчик цитирования (`GEO_CITATION_LOG.md`) + Plausible engagement, окно 3-4 нед, `quotable_v1` vs `baseline`.
- Т.к. авто-rollback E-measure content_factory не покрывает — мониторим вручную, keep/revert руками.

## 9. Rollback
- `content_factory.md` git-версионирован. Откат = `git revert` quotable-коммита на main → следующий прогон тянет откат (мгновенно). Валидатор-ноду — disable/remove в n8n. Baseline-якорь: hash `a129601e` / commit `d71b9b5`.

## 10. Порядок
1. Denis ревьюит §7 diff.
2. Отдельное подтверждение на прод.
3. Тогда: PR к content_factory.md (protected секция) + валидатор-нода + EXCLUDE в auto_decision + A-B тег.
4. 3-4 нед замер retrieval vs baseline → keep/revert.

**Production writes в этой разведке: 0.**
