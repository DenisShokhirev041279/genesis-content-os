"""Deterministic tests for the quotable article contract validator + Module E denylist.

Run: python tests/quotable/test_validate.py   (or: pytest tests/quotable/test_validate.py)
Covers: valid RU/EN/DE, empty bundle, missing language, <script>/JSON-LD, wrong
Key-takeaways, 0 question-H2, H2 boundaries, word-count boundaries, banned phrase,
denylist case variants.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "scripts"))

from validate_article import validate_article, validate_bundle, bundle_ok  # noqa: E402


def valid_html(kt="Key takeaways", faq="FAQ", pad_words=1000):
    pad = "word " * pad_words
    return (
        "<p>A production agent request costs $0.05; retries drive the cost. "
        "In my production deployments I run Claude Code and n8n.</p>"
        f"<h2>{kt}</h2><ul><li>one</li><li>two</li><li>three</li></ul>"
        "<h2>How much does a production AI agent cost per month?</h2>"
        f"<p>{pad}</p>"
        "<h2>What breaks first in production?</h2><p>Retries and context growth.</p>"
        "<h2>Stack overview</h2><p>Claude Code, n8n, Supabase, FastAPI.</p>"
        f"<h2>{faq}</h2><h3>Question?</h3><p>A short answer here for the reader.</p>"
    )


def valid_bundle():
    return {
        "ru": valid_html("Коротко"),
        "en": valid_html("Key takeaways"),
        "de": valid_html("Das Wichtigste in Kürze"),
    }


def test_valid_bundle_passes():
    rep = validate_bundle(valid_bundle())
    assert bundle_ok(rep), rep


def test_empty_bundle_fails():
    assert not bundle_ok(validate_bundle({}))
    assert not bundle_ok(validate_bundle(None))


def test_missing_language_fails():
    b = valid_bundle(); del b["de"]
    rep = validate_bundle(b)
    assert not bundle_ok(rep)
    assert rep["de"] == ["missing or empty article"]


def test_en_only_fails():
    rep = validate_bundle({"en": valid_html()})
    assert not bundle_ok(rep)


def test_script_rejected():
    h = valid_html().replace("<p>A production", "<script>x=1</script><p>A production")
    assert any("script" in e for e in validate_article(h))


def test_jsonld_without_script_rejected():
    h = valid_html().replace(
        "<p>A production",
        '<p>{"@context":"https://schema.org","@type":"Article"}</p><p>A production')
    assert any("Article/BlogPosting" in e for e in validate_article(h))


def test_body_hreflang_rejected():
    h = '<link rel="alternate" hreflang="de" href="x"/>' + valid_html()
    assert any("hreflang" in e for e in validate_article(h))


def test_reversed_attr_hreflang_rejected():
    # hreflang BEFORE rel — must still be rejected (attribute-order independent)
    h = '<link hreflang="de" href="x" rel="alternate"/>' + valid_html()
    assert any("hreflang" in e for e in validate_article(h))


def test_wrong_localization_rejected():
    # RU article using the English "Key takeaways" heading must fail the language-aware check
    errs = validate_article(valid_html("Key takeaways"), "ru")
    assert any("localized Key takeaways" in e for e in errs)
    # correct localized heading passes that specific check for each language
    assert not any("localized Key takeaways" in e for e in validate_article(valid_html("Коротко"), "ru"))
    assert not any("localized Key takeaways" in e for e in validate_article(valid_html("Das Wichtigste in Kürze"), "de"))
    assert not any("localized Key takeaways" in e for e in validate_article(valid_html("Key takeaways"), "en"))


def test_key_takeaways_as_p_rejected():
    # Key-takeaways only as a <p>, not <h2>+<ul>
    h = valid_html().replace("<h2>Key takeaways</h2><ul><li>one</li><li>two</li><li>three</li></ul>",
                             "<p>Key takeaways: a, b, c</p><h2>Intro</h2><p>x</p>")
    errs = validate_article(h)
    assert any("first <h2> is not Key takeaways" in e or "Key takeaways" in e for e in errs)


def test_zero_question_h2_rejected():
    h = valid_html()
    h = h.replace("How much does a production AI agent cost per month?", "Cost overview")
    h = h.replace("What breaks first in production?", "Failure modes")
    assert any("question" in e for e in validate_article(h))


def test_h2_boundaries():
    too_few = valid_html().replace("<h2>Stack overview</h2><p>Claude Code, n8n, Supabase, FastAPI.</p>", "")
    too_few = too_few.replace("<h2>What breaks first in production?</h2><p>Retries and context growth.</p>", "")
    assert any("H2 count" in e for e in validate_article(too_few))
    too_many = valid_html() + "".join(f"<h2>Extra {i}</h2><p>x</p>" for i in range(4))
    assert any("H2 count" in e for e in validate_article(too_many))


def test_word_count_boundaries():
    short = valid_html(pad_words=900)
    assert any("word count" in e for e in validate_article(short))
    long = valid_html(pad_words=1400)
    assert any("word count" in e for e in validate_article(long))


def test_banned_phrase_rejected():
    h = valid_html().replace("Retries and context growth.", "This is a robust and comprehensive system.")
    errs = validate_article(h)
    assert any("banned" in e for e in errs)


def test_banned_ignored_in_code():
    # 'robust' inside a code block must NOT trip the banned check
    h = valid_html().replace("<p>Claude Code, n8n, Supabase, FastAPI.</p>",
                             "<pre><code>robust = True</code></pre>")
    assert not any("banned" in e for e in validate_article(h))


def test_denylist_case_variants():
    from g_e_auto_decision import detect_prompt_file, ProtectedPromptError
    for tm in ("content_factory", "Content_Factory", "  CONTENT_FACTORY.MD  ", "content_factory.md"):
        try:
            detect_prompt_file("tweak", tm); assert False, f"not blocked: {tm}"
        except ProtectedPromptError:
            pass
    # suggestion mentioning content_factory is blocked
    try:
        detect_prompt_file("please improve content_factory prompt", None); assert False
    except ProtectedPromptError:
        pass
    # normal targets still work
    assert detect_prompt_file("scenario tweak", None) == "scenario_v2.md"
    assert detect_prompt_file("v3.md change", None) == "scenario_v3.md"
    assert detect_prompt_file("x", "topic_distiller") == "topic_distiller.md"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1; print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
