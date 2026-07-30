# Content Factory prompt (G_B: RU/EN/DE блог + LinkedIn + Telegram)

Используется в n8n «G_B_content_gen». Генератор тянет шаблон отсюда с GitHub main
и подставляет: {{IDENTITY}}, {{LI_STRUCTURE}} (Volkov-формат), {{TITLE_RU}},
{{TITLE_EN}}, {{DATE}}. Fallback — прежний inline (вывод идентичен).

## Prompt template

```
{{IDENTITY}}

Write THREE expert blog posts on the SAME topic — Russian, English, German.

RU title: "{{TITLE_RU}}"
EN title: "{{TITLE_EN}}"

═══════════════════════════════════════════════════════════
HARD CONSTRAINTS — break ANY of these → invalid output, regenerate:

1. **LENGTH: 950–1200 words per version (NOT including HTML tags).** Count before emitting. Below 950 = retry. Above 1200 = trim to fit.

2. **NO HALLUCINATED PRODUCT NAMES.** Use only real, named tools/libraries you are CERTAIN exist (Claude Code, Anthropic SDK, OpenAI cookbook, semgrep, bandit, gitleaks, OWASP, postgres, supabase, n8n). If you reference an internal concept, describe the PATTERN, never invent a branded product like "X Code Governor", "Y Sentinel", "Z Pipeline" — the reader will Google it, find nothing, and lose trust.

3. **NO BANNED PHRASES** (case-insensitive): "redefined", "dives into", "dive deep", "level up", "game-changer", "game changer", "revolutionize", "revolutionary", "stop relying on luck", "non-optional", "unleash", "unlock the power", "in today's fast-paced", "in the modern era", "boost productivity", "supercharge", "harness", "leverage cutting-edge", "elevate", "robust" (replace with "stable" / "production-grade"), "comprehensive" (replace with "complete" / specific scope), "delve", "navigate the landscape", "ever-evolving", "paradigm shift", "synergy", "seamless integration", "крайне важно", "переломный момент", "погружаемся", "знание которое изменит мир".

4. **IDENTITY ANCHOR FIRST PARAGRAPH.** Open EACH article with a sentence that grounds the reader: who I am + where + what stack. Reuse from IDENTITY above. Drop generic openings like "Large Language Models have...". Open with a CONCRETE pain or moment from production.

5. **CONCRETE NUMBERS WITH SOURCE.** When citing a stat — name the source AND a year AND ideally link to it. "A 2024 Stanford CodeML paper found 38% of LLM-generated Python contained CWE-89 patterns" beats "studies show LLMs are unsafe". If you don't have a real source, drop the stat and use first-person observation instead: "On three of my recent agent deployments I caught the same SQL-injection pattern in generated DB layer code."

6. **SCHEMA.ORG JSON-LD** prepended to HTML body:
\`<script type="application/ld+json">{"@context":"https://schema.org","@type":"Article","headline":"<title>","author":{"@type":"Person","name":"Denis Shokhirev","url":"https://gerdennisai.com","jobTitle":"Enterprise AI Architect","affiliation":{"@type":"Organization","name":"DennisCraft AI Studio","address":"Erlangen, Germany"}},"datePublished":"{{DATE}}","inLanguage":"<ru|en|de>","wordCount":<actual>,"publisher":{"@type":"Organization","name":"DennisCraft AI Studio"}}</script>\`

7. **CLOSE WITH A QUESTION + CTA.** Last paragraph asks the reader a concrete production question (not "any thoughts?"). Example: "Which stage in your LLM pipeline catches the most issues in prod — static analysis, runtime sandbox, or human review? I'd genuinely like to know." Then ONE line CTA: "I run a free 30-min stack audit for DACH founders building AI in regulated markets. DM me on LinkedIn or write to @ger_dennis_ai."

8. **HTML structure**:
   - JSON-LD <script> first
   - <p> opening hook (2–3 sentences, ground reader)
   - 3–5 <h2> sections with <h3> subsections where useful
   - <pre><code class="language-python|typescript|bash|yaml"> blocks (REAL working code, not pseudo-code, 8–20 lines)
   - <table> when comparing 3+ items
   - <h2>FAQ</h2> block with 4–5 <h3>Question?</h3><p>Answer 2–3 sentences</p>
   - Closing paragraph (question + CTA)

═══════════════════════════════════════════════════════════
LANGUAGE-SPECIFIC RULES:

**RU version** — for Russian-speaking developers (devs, AI engineers, СТО уровня в RU/CIS):
- Прямая, экспертная подача. Без хайпа. Без "вы узнаете, как..." — сразу к делу.
- Английские термины — оставляй в оригинале (Claude Code, n8n, RAG), не транслитерируй.
- Числа писать цифрами, не словами.
- "Я" — единственное число (не "мы строим"), это личная позиция.

**EN version** — for global tech audience (architects, CTOs, founders):
- Direct, no fluff. American English, NOT British.
- Code variable names + technical terms in English.
- Cite at least 1 real external source (Anthropic docs, OpenAI cookbook, OWASP, academic paper) with URL if possible.

**DE version** — for DACH market (German-speaking software architects, CTOs in regulated mid-market):
- **Sie-Form throughout.** No "Du".
- Professional Hochdeutsch. Avoid Anglicisms where good German exists (use "Sicherheitsprüfung" not "Security Check", "Bereitstellung" not "Deployment"). Where the English term is industry-standard (API, Cloud, Token, Pipeline) — keep it.
- Mention at least ONE DACH-relevant compliance anchor where the topic permits: DSGVO (GDPR), BSI Grundschutz, NIS2, ISO 27001, EU AI Act.
- Tone: systematic, regulation-aware, conservative on hype. "Erprobt im Produktivbetrieb" beats "innovativ".
- Author byline format: "von Denis Shokhirev, Enterprise AI Architect aus Erlangen"

═══════════════════════════════════════════════════════════

═══════════════════════════════════════════════════════════
LINKEDIN POST (отдельный deliverable — в блоке ===LINKEDIN===):

Also write ONE LinkedIn post (120-180 words) for Denis's DACH B2B audience (CMOs, founders, tech leads).
Use EXACTLY ONE structure - the following - and do NOT mix in any other format:
{{LI_STRUCTURE}}

LinkedIn post RULES:
- Deutsch, Sie-Form (formal), professioneller DACH-Ton. Keine Hype-Wörter.
- NO link/URL in the body (link goes to first comment separately). End on a CTA-flex, not "read the article".
- Follow the structure above EXACTLY, beat by beat. Do NOT write it as a first-person case study or a "just published" blog summary.
- For the provocation structure you MUST include all of: an analogy from a NON-tech industry (cars, sports, aviation, construction); two explicitly contrasted camps; one concrete number used as a threat; a "the window is closing" beat; and a CTA-flex ("I work with...").
- Keep it tight: 110-160 words.
- Max 5 hashtags.

═══════════════════════════════════════════════════════════
TELEGRAM POST (Russian, отдельно — в блоке ===TELEGRAM_RU===):

Also write ONE Telegram post in RUSSIAN (90-150 words) for a Russian dev/founder audience, using the SAME structure as the LinkedIn one ({{LI_STRUCTURE}}), but:
- Natural Russian, sharper and more direct than LinkedIn (Telegram allows directness).
- NO link in the text (the system appends the blog link automatically).
- End with a forward-CTA: «Перешли тому, кому это сейчас нужно.»
- 0-2 hashtags max. Plain text, NO markdown, NO HTML tags.

OUTPUT FORMAT (strict, no preamble, no markdown fences):

===RU===
TITLE: <Russian title without quotes, drop trailing punctuation>
CONTENT:
<HTML body in Russian, includes JSON-LD prepended>

===EN===
TITLE: <English title without quotes>
CONTENT:
<HTML body in English, includes JSON-LD>

===DE===
TITLE: <German title without quotes>
CONTENT:
<HTML body in German, includes JSON-LD>

===LINKEDIN===
<LinkedIn-Post AUF DEUTSCH, Sie-Form, Hochdeutsch, plain text, kein Markdown, kein Link im Body, max 5 Hashtags #KI #EnterpriseAI #Automatisierung #DACH #B2B>

===TELEGRAM_RU===
<Telegram post in RUSSIAN, plain text, no link>
```
