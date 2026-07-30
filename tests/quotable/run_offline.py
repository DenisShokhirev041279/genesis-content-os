"""Offline harness for the quotable content_factory prompt (Sprint 1.1.x).

Step 1 of the rollout (Hermes): run the PROPOSED prompt on N historical topics and
validate the output WITHOUT publishing anything. The GPT call is OPT-IN because it
costs money — set GENESIS_OFFLINE_GPT=1 to actually call OpenAI; otherwise it renders
the prompt per topic and reports (dry-run).

Env:
  SUPABASE_GENESIS_URL, SUPABASE_GENESIS_KEY   (read-only topic fetch)
  GENESIS_OFFLINE_GPT=1                         (enable the paid GPT call)
  OPENAI_API_KEY, OPENAI_MODEL (default gpt-4.1)

Usage:
  python tests/quotable/run_offline.py --n 10
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_article import validate_bundle  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "prompts" / "content_factory.md"
IDENTITY = ("Denis Shokhirev, Enterprise AI Architect, Freiburg, Germany. "
            "Stack: Claude Code, n8n, Supabase, FastAPI, Astro, Ghost.")


def fetch_topics(n: int) -> list[dict]:
    url = os.environ.get("SUPABASE_GENESIS_URL")
    key = os.environ.get("SUPABASE_GENESIS_KEY")
    if not (url and key):
        print("[warn] no Supabase creds -> placeholder topics", file=sys.stderr)
        return [{"title_ru": f"Тема {i}", "title_en": f"Topic {i}", "slug_en": f"topic-{i}"}
                for i in range(1, n + 1)]
    q = (f"{url}/rest/v1/posts?select=title_ru,title_en,slug_en"
         f"&platform=eq.ghost_en&order=published_at.desc&limit={n}")
    req = urllib.request.Request(q, headers={"apikey": key, "authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def render_prompt(topic: dict) -> str:
    tpl = PROMPT_FILE.read_text(encoding="utf-8")
    m = re.search(r"## Prompt template\s*```(.*?)```", tpl, re.S)
    body = m.group(1) if m else tpl
    return (body
            .replace("{{IDENTITY}}", IDENTITY)
            .replace("{{TITLE_RU}}", topic.get("title_ru", ""))
            .replace("{{TITLE_EN}}", topic.get("title_en", ""))
            .replace("{{DATE}}", "2026-07-30")
            .replace("{{LI_STRUCTURE}}", "(provocation format)"))


def call_gpt(prompt: str) -> str:
    key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1")
    body = json.dumps({"model": model, "temperature": 0.7,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body,
                                 headers={"authorization": f"Bearer {key}",
                                          "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def parse_output(text: str) -> dict:
    out = {}
    for lang, marker in (("ru", "===RU==="), ("en", "===EN==="), ("de", "===DE===")):
        m = re.search(re.escape(marker) + r"(.*?)(?:===[A-Z_]+===|\Z)", text, re.S)
        if m:
            seg = m.group(1)
            c = re.search(r"CONTENT:\s*(.*)", seg, re.S)
            out[lang] = (c.group(1) if c else seg).strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    topics = fetch_topics(args.n)
    run_gpt = os.environ.get("GENESIS_OFFLINE_GPT") == "1"
    print(f"topics={len(topics)} | GPT={'ON' if run_gpt else 'OFF (dry-run; set GENESIS_OFFLINE_GPT=1)'}")
    results, failures = [], 0
    for i, t in enumerate(topics, 1):
        prompt = render_prompt(t)
        slug = t.get("slug_en", i)
        if not run_gpt:
            results.append({"topic": slug, "rendered_chars": len(prompt), "validated": False})
            continue
        rep = validate_bundle(parse_output(call_gpt(prompt)))
        ok = all(not v for v in rep.values())
        failures += 0 if ok else 1
        results.append({"topic": slug, "contract_ok": ok, "report": rep})
        print(f"[{i}/{len(topics)}] {slug}: {'OK' if ok else 'FAIL ' + json.dumps(rep, ensure_ascii=False)}")
    print(json.dumps({"n": len(topics), "gpt": run_gpt, "failures": failures, "results": results},
                     ensure_ascii=False, indent=1))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
