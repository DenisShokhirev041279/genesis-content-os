"""Article quotable-contract validator (Sprint 1.1.x GEO) — DETERMINISTIC / fail-closed.

Language-aware structural check. SAME contract as the n8n G_B "Validate Article Contract"
node (port this to JS). Pure stdlib, no network. Returns a list of violation strings
(empty = pass). Only reliable STRUCTURAL rules live here; semantic checks (own-vs-external
data, real answer-first) stay prompt-level + shadow audit, NOT here.

Contract per article (Hermes-agreed):
  - NO <script>, NO application/ld+json, NO @context+Article/BlogPosting, NO body <link hreflang>
  - total <h2>: 5-7
  - first <h2> = localized Key Takeaways, IMMEDIATELY followed by <ul> with exactly 3-5 <li>
  - >= 1 content <h2> phrased as a question / search-intent (excluding Key-takeaways and FAQ)
  - a separate FAQ <h2>
  - 950-1200 visible words (code blocks excluded)
  - none of the prompt banned phrases (outside <pre>/<code>)
Bundle: RU, EN and DE all required and non-empty (fail-closed).
"""
from __future__ import annotations
import re

REQUIRED_LANGS = ("ru", "en", "de")

# Full banned list from prompts/content_factory.md constraint 3 (case-insensitive).
BANNED = [
    "redefined", "dives into", "dive deep", "level up", "game-changer", "game changer",
    "revolutionize", "revolutionary", "stop relying on luck", "non-optional", "unleash",
    "unlock the power", "in today's fast-paced", "in the modern era", "boost productivity",
    "supercharge", "harness", "leverage cutting-edge", "elevate", "robust", "comprehensive",
    "delve", "navigate the landscape", "ever-evolving", "paradigm shift", "synergy",
    "seamless integration", "крайне важно", "переломный момент", "погружаемся",
    "знание которое изменит мир",
]
KEY_TAKEAWAYS = re.compile(r"key takeaways|коротко|das wichtigste", re.I)
FAQ = re.compile(r"\b(faq|часто задаваем|häufige)", re.I)
QUESTION_START = re.compile(
    r"^\s*(how|why|what|which|when|where|who|wie|warum|was|wann|wo|wer|welche|wieviel|"
    r"как|почему|что|сколько|когда|где|какой|какие|зачем)\b", re.I)
H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.I | re.S)
CODEBLOCK = re.compile(r"<pre\b.*?</pre>|<code\b.*?</code>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")
HREFLANG_LINK = re.compile(r"<link[^>]+rel=[\"']?alternate[\"']?[^>]*hreflang", re.I)
JSONLD_TYPE = re.compile(r"\"@type\"\s*:\s*\"(article|blogposting|techarticle)\"", re.I)


def _strip_tags(s: str) -> str:
    return TAG.sub("", s)


def _is_question(t: str) -> bool:
    return ("?" in t) or bool(QUESTION_START.match(t))


def _key_takeaways_ul_ok(html: str) -> bool:
    """First Key-takeaways <h2> must be IMMEDIATELY followed by a <ul> of 3-5 <li>."""
    m = re.search(
        r"<h2[^>]*>[^<]*?(?:key takeaways|коротко|das wichtigste)[^<]*</h2>(.*?)(?=<h2|\Z)",
        html, re.I | re.S)
    if not m:
        return False
    after = m.group(1)
    ul = re.search(r"<ul[^>]*>(.*?)</ul>", after, re.I | re.S)
    if not ul:
        return False
    # nothing but whitespace (no <p>/text) between the H2 and the <ul>
    if re.search(r"<p\b|\w", after[:ul.start()]):
        return False
    return 3 <= len(re.findall(r"<li\b", ul.group(1), re.I)) <= 5


def validate_article(html: str, lang: str = "en") -> list[str]:
    errs: list[str] = []
    if not html or not html.strip():
        return ["empty article"]
    low = html.lower()
    if "<script" in low:
        errs.append("contains <script> (structured data comes from Astro <head>)")
    if "application/ld+json" in low:
        errs.append("contains application/ld+json")
    if "@context" in low and JSONLD_TYPE.search(html):
        errs.append("contains Article/BlogPosting JSON-LD")
    if HREFLANG_LINK.search(html):
        errs.append("body-level hreflang <link> (belongs in Astro <head>)")

    h2s = [_strip_tags(m.group(1)).strip() for m in H2.finditer(html)]
    if not (5 <= len(h2s) <= 7):
        errs.append(f"H2 count {len(h2s)} out of 5-7")
    if not h2s or not KEY_TAKEAWAYS.search(h2s[0]):
        errs.append("first <h2> is not Key takeaways")
    if not _key_takeaways_ul_ok(html):
        errs.append("Key takeaways <h2> not immediately followed by <ul> of 3-5 <li>")
    if not any(FAQ.search(t) for t in h2s):
        errs.append("no FAQ <h2>")
    content_q = [t for t in h2s if _is_question(t) and not KEY_TAKEAWAYS.search(t) and not FAQ.search(t)]
    if len(content_q) < 1:
        errs.append("no content question/search-intent <h2>")

    visible = _strip_tags(CODEBLOCK.sub(" ", html))
    words = len([w for w in visible.split() if w.strip()])
    if not (950 <= words <= 1200):
        errs.append(f"word count {words} out of 950-1200")

    no_code = CODEBLOCK.sub(" ", html).lower()
    hit = [b for b in BANNED if re.search(r"(?<!\w)" + re.escape(b) + r"(?!\w)", no_code)]
    if hit:
        errs.append("banned phrases: " + ", ".join(hit))
    return errs


def validate_bundle(articles: dict | None) -> dict:
    """Require RU, EN, DE — missing or empty language is a violation (fail-closed)."""
    articles = articles or {}
    report: dict[str, list[str]] = {}
    for lang in REQUIRED_LANGS:
        html = articles.get(lang)
        report[lang] = ["missing or empty article"] if not (html and html.strip()) \
            else validate_article(html, lang)
    return report


def bundle_ok(report: dict) -> bool:
    return set(report) >= set(REQUIRED_LANGS) and all(not v for v in report.values())


if __name__ == "__main__":
    import json, sys
    data = json.load(sys.stdin)  # {"ru": "...", "en": "...", "de": "..."}
    rep = validate_bundle(data)
    ok = bundle_ok(rep)
    print(json.dumps({"contract_ok": ok, "report": rep}, ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)
