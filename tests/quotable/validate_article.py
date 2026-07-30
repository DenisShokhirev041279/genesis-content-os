"""Article quotable-contract validator (Sprint 1.1.x GEO).

Language-aware structural check for a single RU/EN/DE article HTML.
SAME rules as the n8n G_B "Validate Article Contract" node — port this logic to
that JS Code node (Hermes). Pure stdlib, no network. Returns a list of violation
strings (empty list = pass). Fail-closed: caller must not publish on non-empty.
"""
from __future__ import annotations
import re

BANNED = [
    "redefined", "dives into", "dive deep", "level up", "game-changer", "game changer",
    "revolutionize", "revolutionary", "unleash", "unlock the power", "supercharge",
    "leverage cutting-edge", "delve", "navigate the landscape", "paradigm shift",
    "synergy", "seamless integration", "boost productivity",
    "крайне важно", "переломный момент", "погружаемся",
]
KEY_TAKEAWAYS = re.compile(r"key takeaways|коротко|das wichtigste", re.I)
FAQ = re.compile(r"<h2[^>]*>\s*(faq|часто задаваем|häufige)", re.I)
QUESTION_START = re.compile(
    r"^\s*(how|why|what|which|when|where|who|wie|warum|was|wann|wo|wer|welche|"
    r"как|почему|что|сколько|когда|где|какой|какие|зачем)\b", re.I)
H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.I | re.S)
CODEBLOCK = re.compile(r"<pre\b.*?</pre>|<code\b.*?</code>", re.I | re.S)
TAG = re.compile(r"<[^>]+>")
FILLER = re.compile(
    r"(let's|lets|in this article|in today|давайте|в этой статье|in diesem artikel)", re.I)


def validate_article(html: str, lang: str = "en") -> list[str]:
    errs: list[str] = []
    if not html or "<h2" not in html.lower():
        return ["empty or no <h2>"]
    if re.search(r"<script\b", html, re.I):
        errs.append("contains <script> (JSON-LD comes from the site layer, not the model)")
    h2s = [TAG.sub("", m.group(1)).strip() for m in H2.finditer(html)]
    if not (3 <= len(h2s) <= 6):
        errs.append(f"H2 count {len(h2s)} out of 3-5 (FAQ + Key-takeaways included)")
    if not any(("?" in t) or QUESTION_START.match(t) for t in h2s):
        errs.append("no question / search-intent <h2>")
    if not KEY_TAKEAWAYS.search(html):
        errs.append("no Key-takeaways block")
    if not FAQ.search(html):
        errs.append("no FAQ block")
    visible = TAG.sub(" ", CODEBLOCK.sub(" ", html))
    words = len([w for w in visible.split() if w.strip()])
    if not (900 <= words <= 1300):
        errs.append(f"word count {words} out of 900-1300")
    m = re.search(r"<p[^>]*>(.*?)</p>", html, re.I | re.S)
    if m and FILLER.match(TAG.sub("", m.group(1)).strip()):
        errs.append("opening is a filler intro, not answer-first")
    no_code = CODEBLOCK.sub(" ", html).lower()
    hit = [b for b in BANNED if re.search(r"\b" + re.escape(b) + r"\b", no_code)]
    if hit:
        errs.append("banned phrases: " + ", ".join(hit))
    return errs


def validate_bundle(articles: dict) -> dict:
    """articles = {'ru': html, 'en': html, 'de': html} -> {lang: [errors]}."""
    return {lang: validate_article(html, lang) for lang, html in articles.items()}


if __name__ == "__main__":
    import json, sys
    data = json.load(sys.stdin)  # {"ru": "...", "en": "...", "de": "..."}
    report = validate_bundle(data)
    ok = all(not v for v in report.values())
    print(json.dumps({"contract_ok": ok, "report": report}, ensure_ascii=False, indent=1))
    sys.exit(0 if ok else 1)
