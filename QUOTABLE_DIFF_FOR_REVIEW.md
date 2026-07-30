# Quotable-diff НА ПРОСМОТР (Claudian, 2026-07-30) — прод НЕ тронут

> Это предложение. Ничего не применено к `content_factory.md`, n8n или прод.
> После твоего «go» — PR + валидатор-нода + EXCLUDE. Откат = `git revert` (baseline hash `a129601e`).

## A. Диф `prompts/content_factory.md` — вставить ПОСЛЕ constraint 8, ПЕРЕД «LANGUAGE-SPECIFIC RULES»

```diff
+═══════════════════════════════════════════════════════════
+<!-- PROTECTED OUTPUT CONTRACT (9–12) — держит цитируемость (GEO).
+     Module E оптимизирует содержание ВНУТРИ этих пунктов, но НЕ удаляет их.
+     Валидатор в n8n G_B отклоняет вывод, нарушающий 9–12. -->
+
+9. **ANSWER-FIRST LEAD (цитируемый).** После hook-абзаца (п.4) ВТОРОЙ абзац —
+   1–2 предложения ПРЯМОГО ОТВЕТА на главный вопрос статьи: самодостаточный, без
+   отсылок «это/такой», который поисковик/LLM может процитировать дословно.
+   Плохо: «Давайте разберём варианты.»
+   Хорошо: «Запрос к production-агенту Claude стоит $0.02–0.15 в зависимости от
+   размера контекста; главный драйвер цены — ретраи, не токены.»
+
+10. **QUESTION / SEARCH-INTENT H2.** Минимум ПОЛОВИНА `<h2>` — в форме вопроса или
+    поискового intent (как человек гуглит), а не ярлык.
+    Плохо: `<h2>Стоимость</h2>`. Хорошо: `<h2>Сколько стоит production AI-агент в месяц?</h2>`.
+    FAQ-блок из п.8 остаётся ДОПОЛНИТЕЛЬНО.
+
+11. **KEY TAKEAWAYS.** Сразу после answer-first lead:
+    `<h2>` — RU «Коротко», EN «Key takeaways», DE «Das Wichtigste in Kürze» —
+    и `<ul>` из 3–5 `<li>`, каждый = САМОДОСТАТОЧНОЕ проверяемое утверждение
+    (LLM может вынуть любой один буллет без соседнего текста). Буллеты не зависят друг от друга.
+
+12. **СВОИ ДАННЫЕ vs ВНЕШНИЙ ФАКТ — явные метки, НИКОГДА не смешивать.**
+    - От первого лица: префикс «В моих проде-развёртываниях…» / «In my production
+      deployments…» / «In meinen Produktivsystemen…».
+    - Внешнее утверждение: ТОЛЬКО named source + год + ссылка (п.5).
+    - НИКОГДА не выдавать внешнее за своё и НЕ выдумывать цифру. Нет источника и
+      нет своего опыта → выкинуть утверждение.
+═══════════════════════════════════════════════════════════
```

И в constraint 8 (HTML structure) добавить строку порядка:
```diff
   - <p> opening hook (2–3 sentences, ground reader)
+  - <p> ANSWER-FIRST lead (1–2 sentences, direct quotable answer)   ← п.9
+  - <h2>Key takeaways / Коротко / Das Wichtigste</h2> + <ul>3–5 li   ← п.11
   - 3–5 <h2> sections ...  (≥ половина — вопросы, п.10)
```

## B. Валидатор — новая n8n Code-нода `Validate Article Contract`
Между `Parse Bilingual Response` и `Publish RU to Ghost`. **Fail-closed:** провал → 1 ретрай генерации; повторно → флаг в Telegram, НЕ публиковать.
⚠️ Ключи входа (`html_ru/en/de`) — подогнать под реальный выход `Parse Bilingual Response` (это уточняю с Гермесом/по факту ноды).

```js
// Validate Article Contract — проверка quotable-инвариантов 9–12 (+8).
const BANNED = ["redefined","dive deep","game-changer","revolutionize","unleash",
  "leverage cutting-edge","delve","paradigm shift","synergy","seamless integration",
  "крайне важно","переломный момент","погружаемся","supercharge","robust"];
function checkArticle(html){
  const e=[];
  const h2=[...html.matchAll(/<h2[^>]*>(.*?)<\/h2>/gis)].map(m=>m[1].replace(/<[^>]+>/g,'').trim());
  const q=h2.filter(t=>/[?？]/.test(t)||/^(how|why|what|which|when|wie|warum|was|wann|как|почему|что|сколько|когда)\b/i.test(t));
  if(h2.length && q.length < Math.ceil(h2.length/2)) e.push(`<50% question-H2 (${q.length}/${h2.length})`);
  if(!/key takeaways|коротко|das wichtigste/i.test(html)) e.push("no Key-takeaways");
  if(!/<h2[^>]*>\s*(faq|часто задаваем|häufige)/i.test(html)) e.push("no FAQ");
  const w=html.replace(/<[^>]+>/g,' ').split(/\s+/).filter(Boolean).length;
  if(w<900||w>1300) e.push(`words ${w} out of 900-1300`);
  const b=BANNED.filter(x=>new RegExp(`\\b${x}\\b`,'i').test(html));
  if(b.length) e.push("banned: "+b.join(","));
  return e;
}
const it=$input.first().json;
const rep={}; let ok=true;
for(const l of ['ru','en','de']){ const h=it['html_'+l]||it[l]||''; const er=checkArticle(h); rep[l]=er; if(er.length) ok=false; }
return [{ json: { contract_ok: ok, contract_report: rep, ...it } }];
// downstream: IF contract_ok=false → ветка retry (1×) → иначе Telegram-флаг + skip publish.
```

## C. Defense-in-depth от Module E (на всякий, хоть он content_factory и не трогает)
```diff
# g_e_auto_decision.py  detect_prompt_file()
+    PROTECTED = {"content_factory.md"}   # article-контракт — авто-переписывать НЕЛЬЗЯ
     if target_module == "topic_distiller":
         return "topic_distiller.md"
     ...
     return "scenario_v2.md"
+    # (+ассерт на вызове: target не в PROTECTED)
```

## D. A-B и замер
- Тег `posts.ab_variant = 'quotable_v1' | 'baseline'` (чередование в `Prepare Prompts + URLs`).
- Метрика — **НЕ YouTube** (E-measure для content_factory = insufficient_data), а: GSC impressions/clicks по статьям + датчик цитирования (`GEO_CITATION_LOG.md`) + Plausible. Окно 3–4 нед, keep/revert вручную.

**Прод-writes в этом файле: 0. Жду твоё «go» на применение.**
