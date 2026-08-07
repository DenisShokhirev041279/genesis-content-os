#!/usr/bin/env python3
"""genesis_linkedin_selfpromo — «Genesis рекламирует сам себя в LinkedIn» (EN + DE).

Строит бренд-карусель про Genesis (тёмный #0A0E1A + золото #F6B63B, движок
carousel_template.py из ContentMachine/LINKEDIN), собирает LinkedIn-caption,
и ОТПРАВЛЯЕТ Денису в Telegram документ + кнопку «🚀 Public → LinkedIn».

НИЧЕГО НЕ ПУБЛИКУЕТ САМ. Публикация — только по нажатию кнопки Денисом.
Кнопку ловит aidrops_gate_listener.py (callback_data = "lipub:<id>"), который
читает pending-JSON и зовёт publish_linkedin_doc.py.

Usage:
    python genesis_linkedin_selfpromo.py --lang en --topic 1
    python genesis_linkedin_selfpromo.py --lang de --rotate       # тема из ротации
    python genesis_linkedin_selfpromo.py --lang en --topic 3 --no-send   # только PDF

Темы (--topic):
    1  автономная система «сама себя переписывает»
    2  live-дашборд + road to 1000 stars
    3  open source «забери и запусти»
    4  история основателя + флот агентов (Fafnir 490k Rust)
    5  провокация «prompt engineering мёртв»
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# --- пути / бренд ---
ROOT = Path.home() / "Obsidian_AI_Brain"
LN_DIR = ROOT / "Projects/ContentMachine/LINKEDIN"
OUT_DIR = ROOT / "Projects/genesis-content-os/output/selfpromo"
PENDING_DIR = ROOT / "Projects/genesis-content-os/output/selfpromo_pending"
STATE_FILE = ROOT / "Projects/genesis-content-os/output/.selfpromo_rotation.json"

LIVE = "https://live.gerdennisai.com"
GH = "https://github.com/DenisShokhirev041279/genesis-content-os"
DENIS_ID = 1357650155

sys.path.insert(0, str(LN_DIR))

# --- шрифты с кириллицей/умлаутами (как build_autopost_pdf.py) ---
from reportlab.pdfbase import pdfmetrics                      # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont                  # noqa: E402


def _pick(cands):
    for p in cands:
        if os.path.exists(p):
            return p
    return None


def _register_fonts():
    reg = _pick(["/System/Library/Fonts/Supplemental/Arial.ttf",
                 os.path.expanduser("~/Library/Fonts/InterVariable.ttf")])
    bold = _pick(["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                  os.path.expanduser("~/Library/Fonts/InterVariable.ttf")])
    mono = _pick([os.path.expanduser("~/Library/Fonts/JetBrainsMono-Regular.ttf"),
                  "/System/Library/Fonts/Menlo.ttc"])
    if reg:
        pdfmetrics.registerFont(TTFont("CyrReg", reg))
    if bold:
        pdfmetrics.registerFont(TTFont("CyrBold", bold))
    if mono:
        pdfmetrics.registerFont(TTFont("CyrMono", mono))
    import carousel_template as ct
    if reg:
        ct.F = "CyrReg"
    if bold:
        ct.FB = "CyrBold"
    if mono:
        ct.FM = "CyrMono"
    return ct


FOOTER_MICRO = {
    "en": "Agentic AI Systems Architect  ·  DennisCraft AI Studio  ·  live.gerdennisai.com",
    "de": "Agentic-AI-Systemarchitekt  ·  DennisCraft AI Studio  ·  live.gerdennisai.com",
}
CTA_ROLE = {
    "en": "Enterprise AI Architect  ·  DennisCraft AI Studio",
    "de": "Enterprise-AI-Architekt  ·  DennisCraft AI Studio",
}
CTA_LOC = {
    "en": "Freiburg im Breisgau, Germany",
    "de": "Freiburg im Breisgau, Deutschland",
}


def _strip_emoji(s: str) -> str:
    """Убирает эмодзи (Arial TTF их не рендерит → tofu-квадраты). Стрелки/точки остаются."""
    out = []
    for ch in s:
        o = ord(ch)
        if (0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF
                or 0x2B00 <= o <= 0x2BFF or 0x1F1E6 <= o <= 0x1F1FF or o == 0xFE0F):
            continue
        out.append(ch)
    return "".join(out).strip()


def _cta_slide(lang, headline, body, hook_a, act_a, hook_b, act_b, section="CTA"):
    return {
        "type": "cta", "section": section, "accent": "blue",
        "headline": headline, "body": body,
        "cta_a": {"hook": _strip_emoji(hook_a), "action": act_a},
        "cta_b": {"hook": _strip_emoji(hook_b), "action": act_b},
        "name": "Denis Shokhirev", "role": CTA_ROLE[lang], "location": CTA_LOC[lang],
        "link": "live.gerdennisai.com  ·  github.com/…/genesis-content-os",
    }


# ============================================================
#  ТЕМЫ: (topic, lang) -> spec dict
# ============================================================

def build_spec(topic: int, lang: str) -> dict:
    fm = FOOTER_MICRO[lang]
    footer_url = ("live.gerdennisai.com  ·  github.com/DenisShokhirev041279/genesis-content-os")

    def cover(eyebrow, headline, sub, body):
        return {"type": "cover", "eyebrow": eyebrow, "headline": headline,
                "subheadline": sub, "body": body, "author": "Denis Shokhirev",
                "footer_micro": fm}

    T = (topic, lang)

    # ---------- ТЕМА 1: автономная система, сама себя переписывает ----------
    if topic == 1 and lang == "en":
        slides = [
            cover("AUTONOMOUS AI  ·  01", "It rewrites itself.",
                  "A content system with no human in the loop.",
                  ["I don't make this content. My system does —",
                   "and it edits its own prompts based on results."]),
            {"type": "numbered_list", "section": "01  ·  THE LOOP", "accent": "gold",
             "headline": "Six modules, zero humans",
             "items": [
                 {"num": 1, "title": "Scan", "desc": "3,300+ trends parsed, picks topics itself"},
                 {"num": 2, "title": "Render", "desc": "Writes, voices (my cloned voice), edits"},
                 {"num": 3, "title": "Publish", "desc": "Ships to 5 channels on schedule"},
                 {"num": 4, "title": "Measure", "desc": "Reads its own audience metrics"},
                 {"num": 5, "title": "Self-improve", "desc": "Opens a PR to rewrite its own prompt"}],
             "footer_line": "scan → render → publish → measure → self-improve → repeat"},
            {"type": "definition", "section": "02  ·  THE SHIFT", "accent": "blue",
             "title_lines": ["Prompt engineering", "is not the system."],
             "card_a": {"title": "Most people", "lines": ["Use AI to make one post.",
                        "Human prompts every step."], "caption": "manual, one-shot"},
             "card_b": {"title": "Genesis", "lines": ["A system that steers itself.",
                        "Reads metrics, forms a hypothesis,",
                        "rewrites its own strategy."], "caption": "autonomous, compounding"}},
            {"type": "case_study", "section": "03  ·  MODULE E", "accent": "gold",
             "headline": "It edits its own code",
             "subheadline": "Real merged pull requests",
             "body": ["Module E reads what worked, forms a hypothesis,",
                      "and opens a PR to rewrite its prompt. I review the diff."],
             "stats": [{"value": "6", "label": "modules"}, {"value": "5", "label": "channels"},
                       {"value": "3,300+", "label": "trends scanned"}, {"value": "24/7", "label": "on a tiny droplet"}]},
            _cta_slide("en", "Watch it run.",
                       ["It's live right now — the system + traffic + a globe of readers.",
                        "Open source. Clone it, watch it rewrite itself."],
                       "🔴 Live dashboard:", LIVE, "⭐ Star the repo:", GH),
        ]
        title = "Genesis — the content system that rewrites itself"
    elif topic == 1 and lang == "de":
        slides = [
            cover("AUTONOME KI  ·  01", "Es schreibt sich selbst um.",
                  "Ein Content-System ohne Mensch in der Schleife.",
                  ["Ich drehe diesen Content nicht. Mein System macht ihn —",
                   "und schreibt seine eigenen Prompts anhand der Ergebnisse um."]),
            {"type": "numbered_list", "section": "01  ·  DER KREISLAUF", "accent": "gold",
             "headline": "Sechs Module, null Menschen",
             "items": [
                 {"num": 1, "title": "Scan", "desc": "3.300+ Trends geparst, wählt Themen selbst"},
                 {"num": 2, "title": "Render", "desc": "Schreibt, vertont (geklonte Stimme), schneidet"},
                 {"num": 3, "title": "Publish", "desc": "Auf 5 Kanäle nach Zeitplan"},
                 {"num": 4, "title": "Measure", "desc": "Liest die eigenen Metriken"},
                 {"num": 5, "title": "Self-improve", "desc": "Öffnet PR, um eigenen Prompt umzuschreiben"}],
             "footer_line": "scan → render → publish → measure → self-improve → repeat"},
            {"type": "definition", "section": "02  ·  DER WANDEL", "accent": "blue",
             "title_lines": ["Prompt Engineering", "ist nicht das System."],
             "card_a": {"title": "Die meisten", "lines": ["Nutzen KI für einen Post.",
                        "Mensch promptet jeden Schritt."], "caption": "manuell, einmalig"},
             "card_b": {"title": "Genesis", "lines": ["Ein System, das sich selbst steuert.",
                        "Liest Metriken, bildet Hypothese,",
                        "schreibt eigene Strategie um."], "caption": "autonom, kumulativ"}},
            {"type": "case_study", "section": "03  ·  MODUL E", "accent": "gold",
             "headline": "Es bearbeitet den eigenen Code",
             "subheadline": "Echte gemergte Pull Requests",
             "body": ["Modul E liest, was funktioniert hat, bildet eine Hypothese",
                      "und öffnet einen PR für seinen Prompt. Ich prüfe den Diff."],
             "stats": [{"value": "6", "label": "Module"}, {"value": "5", "label": "Kanäle"},
                       {"value": "3.300+", "label": "Trends gescannt"}, {"value": "24/7", "label": "auf Mini-Droplet"}]},
            _cta_slide("de", "Sieh es live.",
                       ["Es läuft gerade jetzt — System + Traffic + Globus der Leser.",
                        "Open Source. Klone es, sieh zu, wie es sich umschreibt."],
                       "🔴 Live-Dashboard:", LIVE, "⭐ Repo sternen:", GH),
        ]
        title = "Genesis — das Content-System, das sich selbst umschreibt"

    # ---------- ТЕМА 2: live-дашборд + road to 1000 stars ----------
    elif topic == 2 and lang == "en":
        slides = [
            cover("BUILD IN PUBLIC  ·  02", "Road to 1,000 stars.",
                  "The growth itself is the show.",
                  ["A live dashboard of an autonomous system —",
                   "watch the star counter climb in real time."]),
            {"type": "chart", "section": "01  ·  THE COUNTER", "accent": "gold",
             "headline": "The goal is public",
             "label_a": "Target", "label_b": "Live now",
             "val_a": 1000, "val_b": 1000, "val_a_text": "1,000 ⭐",
             "val_b_text": "tracked live", "callout": "=  a progress bar people return to watch",
             "body": ["Every milestone becomes a post:",
                      "«the system decided X this week», «+50 stars overnight — here's what worked»."],
             "emphasis": "Nobody quits a series halfway.",
             "footer_line": "curiosity + social proof + serialization"},
            {"type": "numbered_list", "section": "02  ·  WHAT'S ON IT", "accent": "blue",
             "headline": "One screen, the whole system",
             "items": [
                 {"num": 1, "title": "System state", "desc": "Which module is running, right now"},
                 {"num": 2, "title": "Traffic", "desc": "Live reads + a globe of where it's read"},
                 {"num": 3, "title": "Star progress", "desc": "X / 1,000 ⭐ pulled from the GitHub API"}],
             "footer_line": "The dashboard is the point of return."},
            _cta_slide("en", "Watch the number grow.",
                       ["It's live and open source. Star it, then come back",
                        "and watch an autonomous system chase 1,000."],
                       "🔴 Live dashboard:", LIVE, "⭐ Star the repo:", GH),
        ]
        title = "Genesis — road to 1,000 stars, live"
    elif topic == 2 and lang == "de":
        slides = [
            cover("BUILD IN PUBLIC  ·  02", "Weg zu 1.000 Sternen.",
                  "Das Wachstum selbst ist die Show.",
                  ["Ein Live-Dashboard eines autonomen Systems —",
                   "sieh den Sternezähler in Echtzeit steigen."]),
            {"type": "chart", "section": "01  ·  DER ZÄHLER", "accent": "gold",
             "headline": "Das Ziel ist öffentlich",
             "label_a": "Ziel", "label_b": "Jetzt live",
             "val_a": 1000, "val_b": 1000, "val_a_text": "1.000 ⭐",
             "val_b_text": "live getrackt", "callout": "=  ein Fortschrittsbalken, zu dem man zurückkehrt",
             "body": ["Jeder Meilenstein wird ein Post:",
                      "«das System entschied X diese Woche», «+50 Sterne über Nacht»."],
             "emphasis": "Niemand bricht eine Serie in der Mitte ab.",
             "footer_line": "Neugier + Social Proof + Serialität"},
            {"type": "numbered_list", "section": "02  ·  WAS DRAUF IST", "accent": "blue",
             "headline": "Ein Screen, das ganze System",
             "items": [
                 {"num": 1, "title": "Systemstatus", "desc": "Welches Modul läuft, gerade jetzt"},
                 {"num": 2, "title": "Traffic", "desc": "Live-Reads + Globus der Leser-Länder"},
                 {"num": 3, "title": "Stern-Fortschritt", "desc": "X / 1.000 ⭐ aus der GitHub-API"}],
             "footer_line": "Das Dashboard ist der Rückkehrpunkt."},
            _cta_slide("de", "Sieh die Zahl wachsen.",
                       ["Es ist live und Open Source. Sterne es, dann komm zurück",
                        "und sieh einem autonomen System bei der Jagd auf 1.000 zu."],
                       "🔴 Live-Dashboard:", LIVE, "⭐ Repo sternen:", GH),
        ]
        title = "Genesis — Weg zu 1.000 Sternen, live"

    # ---------- ТЕМА 3: open source «забери и запусти» ----------
    elif topic == 3 and lang == "en":
        slides = [
            cover("OPEN SOURCE  ·  03", "Take it. Run it.",
                  "An autonomous content system, MIT-licensed.",
                  ["Not a demo. Not a waitlist. Clone the repo",
                   "and point it at your own content."]),
            {"type": "code_block", "section": "01  ·  ONE COMMAND", "accent": "blue",
             "headline": "From clone to running",
             "code_lines": [
                 "git clone \\",
                 "  github.com/DenisShokhirev041279/genesis-content-os",
                 "cd genesis-content-os",
                 " ",
                 "cp .env.example .env    # your keys",
                 "docker compose up -d    # the whole loop"],
             "footer_a": "n8n + GPT + ElevenLabs + HeyGen + Supabase",
             "footer_b": "24/7 on a tiny droplet"},
            {"type": "numbered_list", "section": "02  ·  WHY CLONE IT", "accent": "gold",
             "headline": "It's built to be yours",
             "items": [
                 {"num": 1, "title": "good-first-issue", "desc": "Tagged for contributors from day one"},
                 {"num": 2, "title": "docker-compose", "desc": "One-command setup, no yak-shaving"},
                 {"num": 3, "title": "Your niche", "desc": "Point it at your topics — it scans and ships"}],
             "footer_line": "Users become advocates and contributors."},
            _cta_slide("en", "Steal this system.",
                       ["It's open source and running in public.",
                        "Clone it, break it, send a PR."],
                       "⭐ The repo:", GH, "🔴 See it live first:", LIVE),
        ]
        title = "Genesis — open source, take it and run it"
    elif topic == 3 and lang == "de":
        slides = [
            cover("OPEN SOURCE  ·  03", "Nimm es. Starte es.",
                  "Ein autonomes Content-System, MIT-lizenziert.",
                  ["Keine Demo. Keine Warteliste. Klone das Repo",
                   "und richte es auf deinen eigenen Content."]),
            {"type": "code_block", "section": "01  ·  EIN BEFEHL", "accent": "blue",
             "headline": "Vom Klon zum Betrieb",
             "code_lines": [
                 "git clone \\",
                 "  github.com/DenisShokhirev041279/genesis-content-os",
                 "cd genesis-content-os",
                 " ",
                 "cp .env.example .env    # deine Keys",
                 "docker compose up -d    # der ganze Kreislauf"],
             "footer_a": "n8n + GPT + ElevenLabs + HeyGen + Supabase",
             "footer_b": "24/7 auf einem Mini-Droplet"},
            {"type": "numbered_list", "section": "02  ·  WARUM KLONEN", "accent": "gold",
             "headline": "Gebaut, um deins zu sein",
             "items": [
                 {"num": 1, "title": "good-first-issue", "desc": "Von Tag eins für Beitragende getaggt"},
                 {"num": 2, "title": "docker-compose", "desc": "Ein-Befehl-Setup, kein Gefrickel"},
                 {"num": 3, "title": "Deine Nische", "desc": "Richte es auf deine Themen — es scannt und liefert"}],
             "footer_line": "Nutzer werden zu Fürsprechern und Beitragenden."},
            _cta_slide("de", "Klau dieses System.",
                       ["Es ist Open Source und läuft öffentlich.",
                        "Klone es, brich es, schick einen PR."],
                       "⭐ Das Repo:", GH, "🔴 Erst live ansehen:", LIVE),
        ]
        title = "Genesis — Open Source, nimm es und starte es"

    # ---------- ТЕМА 4: история основателя + флот агентов ----------
    elif topic == 4 and lang == "en":
        slides = [
            cover("FOUNDER  ·  04", "One founder, a fleet.",
                  "How a single architect runs an army of agents.",
                  ["No team. A patent, a fleet of 7 agents,",
                   "and a 490k-line Rust codebase behind it."]),
            {"type": "case_study", "section": "01  ·  THE FLEET", "accent": "gold",
             "headline": "The agents behind Genesis",
             "subheadline": "Fafnir — the guardian",
             "body": ["Fafnir: 490k lines of Rust, 5 defense layers, 200+ sessions.",
                      "Around it, a fleet of 7 agents coordinate through the filesystem."],
             "stats": [{"value": "490k", "label": "lines of Rust"}, {"value": "7", "label": "agents in the fleet"},
                       {"value": "5", "label": "defense layers"}, {"value": "200+", "label": "sessions"}]},
            {"type": "strategic", "section": "02  ·  THE POINT", "accent": "blue",
             "headline": "One person can be an army",
             "body": ["The moat isn't a bigger team.",
                      "It's a founder who architects agents that do the work —",
                      "and a system that improves itself while you sleep."],
             "emphasis": "Leverage is the new headcount.",
             "quote_lines": ["Don't hire the work.", "Architect the agents that do it."]},
            _cta_slide("en", "See the system it runs.",
                       ["The fleet's public output is an autonomous content system.",
                        "It's live, and it's open source."],
                       "🔴 Live dashboard:", LIVE, "⭐ The repo:", GH),
        ]
        title = "One founder, a fleet of agents — the Genesis story"
    elif topic == 4 and lang == "de":
        slides = [
            cover("GRÜNDER  ·  04", "Ein Gründer, eine Flotte.",
                  "Wie ein einzelner Architekt eine Armee von Agenten steuert.",
                  ["Kein Team. Ein Patent, eine Flotte von 7 Agenten",
                   "und 490k Zeilen Rust dahinter."]),
            {"type": "case_study", "section": "01  ·  DIE FLOTTE", "accent": "gold",
             "headline": "Die Agenten hinter Genesis",
             "subheadline": "Fafnir — der Wächter",
             "body": ["Fafnir: 490k Zeilen Rust, 5 Verteidigungsebenen, 200+ Sessions.",
                      "Darum koordiniert eine Flotte von 7 Agenten über das Dateisystem."],
             "stats": [{"value": "490k", "label": "Zeilen Rust"}, {"value": "7", "label": "Agenten in der Flotte"},
                       {"value": "5", "label": "Verteidigungsebenen"}, {"value": "200+", "label": "Sessions"}]},
            {"type": "strategic", "section": "02  ·  DER PUNKT", "accent": "blue",
             "headline": "Eine Person kann eine Armee sein",
             "body": ["Der Burggraben ist kein größeres Team.",
                      "Es ist ein Gründer, der Agenten architektiert, die die Arbeit tun —",
                      "und ein System, das sich verbessert, während du schläfst."],
             "emphasis": "Hebel ist die neue Kopfzahl.",
             "quote_lines": ["Stelle nicht die Arbeit ein.", "Architektiere die Agenten, die sie tun."]},
            _cta_slide("de", "Sieh das System der Flotte.",
                       ["Der öffentliche Output der Flotte ist ein autonomes Content-System.",
                        "Es ist live und Open Source."],
                       "🔴 Live-Dashboard:", LIVE, "⭐ Das Repo:", GH),
        ]
        title = "Ein Gründer, eine Flotte von Agenten — die Genesis-Story"

    # ---------- ТЕМА 5: провокация «prompt engineering мёртв» ----------
    elif topic == 5 and lang == "en":
        slides = [
            cover("HOT TAKE  ·  05", "Prompt engineering is dead.",
                  "The skill isn't writing prompts. It's building systems.",
                  ["If a human still prompts every step,",
                   "you built a tool — not a system."]),
            {"type": "definition", "section": "01  ·  THE LINE", "accent": "blue",
             "title_lines": ["Prompting is a task.", "Systems compound."],
             "card_a": {"title": "Prompt engineering", "lines": ["One clever prompt.",
                        "A human in every loop.", "Value stops when you stop."],
                        "caption": "linear, fragile"},
             "card_b": {"title": "System engineering", "lines": ["Agents that scan, decide, ship.",
                        "Metrics feed back into strategy.", "It improves without you."],
                        "caption": "compounding, durable"}},
            {"type": "antipatterns", "section": "02  ·  RED FLAGS", "accent": "red",
             "headline": "You're still prompting if…",
             "subheadline": "three tells it's a tool, not a system",
             "items": [
                 {"num": 1, "title": "Human triggers every run", "desc": "Nothing happens unless you type"},
                 {"num": 2, "title": "No feedback loop", "desc": "Metrics never change the next prompt"},
                 {"num": 3, "title": "It can't improve itself", "desc": "Same output next month, no learning"}],
             "footer_line": "Genesis fails none of these — it opens PRs on itself."},
            _cta_slide("en", "Prove me wrong.",
                       ["Here's a system that rewrites its own prompts, in public.",
                        "Watch it, then tell me prompt engineering is the skill."],
                       "🔴 Live dashboard:", LIVE, "⭐ The proof (repo):", GH),
        ]
        title = "Prompt engineering is dead — build systems"
    elif topic == 5 and lang == "de":
        slides = [
            cover("HOT TAKE  ·  05", "Prompt Engineering ist tot.",
                  "Die Fähigkeit ist nicht Prompten. Es ist Systeme bauen.",
                  ["Wenn ein Mensch noch jeden Schritt promptet,",
                   "hast du ein Tool gebaut — kein System."]),
            {"type": "definition", "section": "01  ·  DIE GRENZE", "accent": "blue",
             "title_lines": ["Prompten ist eine Aufgabe.", "Systeme kumulieren."],
             "card_a": {"title": "Prompt Engineering", "lines": ["Ein cleverer Prompt.",
                        "Ein Mensch in jeder Schleife.", "Wert endet, wenn du aufhörst."],
                        "caption": "linear, fragil"},
             "card_b": {"title": "System Engineering", "lines": ["Agenten, die scannen, entscheiden, liefern.",
                        "Metriken fließen in die Strategie.", "Es verbessert sich ohne dich."],
                        "caption": "kumulativ, dauerhaft"}},
            {"type": "antipatterns", "section": "02  ·  ROTE FLAGGEN", "accent": "red",
             "headline": "Du promptest noch, wenn…",
             "subheadline": "drei Zeichen: es ist ein Tool, kein System",
             "items": [
                 {"num": 1, "title": "Mensch startet jeden Lauf", "desc": "Nichts passiert, ohne dass du tippst"},
                 {"num": 2, "title": "Keine Feedback-Schleife", "desc": "Metriken ändern nie den nächsten Prompt"},
                 {"num": 3, "title": "Es verbessert sich nicht", "desc": "Gleicher Output nächsten Monat, kein Lernen"}],
             "footer_line": "Genesis besteht keinen davon — es öffnet PRs auf sich selbst."},
            _cta_slide("de", "Beweis das Gegenteil.",
                       ["Hier ein System, das seine Prompts öffentlich umschreibt.",
                        "Sieh zu — und sag mir dann, Prompten sei die Fähigkeit."],
                       "🔴 Live-Dashboard:", LIVE, "⭐ Der Beweis (Repo):", GH),
        ]
        title = "Prompt Engineering ist tot — baue Systeme"

    else:
        raise SystemExit(f"Нет контента для topic={topic} lang={lang}")

    return {"title": title, "author": "Denis Shokhirev",
            "footer_url": footer_url, "slides": slides, "_topic": topic, "_lang": lang}


# ============================================================
#  CAPTION (LinkedIn body)
# ============================================================

CAPTIONS = {
    (1, "en"): (
        "I don't make this content. My system does — and it decides what, itself.\n\n"
        "Genesis is a fully autonomous, open-source content system:\n"
        "→ scans 3,300+ trends and picks the topics itself\n"
        "→ writes, voices (my cloned voice) and edits\n"
        "→ publishes to 5 channels\n"
        "→ reads its own metrics and rewrites its own strategy\n\n"
        "The real shift isn't \"AI made a post.\" It's a system that steers and improves "
        "itself — from prompt engineering to autonomous systems.\n\n"
        "How autonomous should AI be in content production — where's the line?\n\n"
        "#AI #OpenSource #AgentEngineering #Automation #BuildInPublic"),
    (1, "de"): (
        "Ich drehe diesen Content nicht. Mein System macht ihn — und entscheidet selbst, was.\n\n"
        "Genesis ist ein vollständig autonomes, quelloffenes Content-System:\n"
        "→ scannt 3.300+ Trends und wählt selbst die Themen\n"
        "→ schreibt, vertont (meine geklonte Stimme) und schneidet\n"
        "→ publiziert auf 5 Kanäle\n"
        "→ liest die eigenen Metriken und schreibt seine Strategie um\n\n"
        "Der eigentliche Wandel: nicht \"KI macht einen Post\", sondern ein System, das sich "
        "selbst steuert und verbessert — von Prompt Engineering zu autonomen Systemen.\n\n"
        "Wie autonom sollte KI in der Content-Produktion sein — wo ist die Grenze?\n\n"
        "#KI #OpenSource #AgentEngineering #Automatisierung #BuildInPublic"),
    (2, "en"): (
        "I put my open-source system's growth on a live dashboard — and made the number the show.\n\n"
        "Road to 1,000 GitHub stars, in public. The dashboard shows the system running, live "
        "traffic, a globe of readers, and a progress bar pulled from the GitHub API.\n\n"
        "Every milestone becomes a post: \"the system decided X this week\", \"+50 stars overnight — "
        "here's what worked.\" Curiosity + social proof + serialization. Nobody quits a series halfway.\n\n"
        "What would you put on a live dashboard of your own build?\n\n"
        "#BuildInPublic #OpenSource #AI #Automation #Startup"),
    (2, "de"): (
        "Ich habe das Wachstum meines Open-Source-Systems auf ein Live-Dashboard gebracht — und die Zahl zur Show gemacht.\n\n"
        "Weg zu 1.000 GitHub-Sternen, öffentlich. Das Dashboard zeigt das laufende System, Live-Traffic, "
        "einen Globus der Leser und einen Fortschrittsbalken aus der GitHub-API.\n\n"
        "Jeder Meilenstein wird ein Post: \"das System entschied X diese Woche\", \"+50 Sterne über Nacht\". "
        "Neugier + Social Proof + Serialität. Niemand bricht eine Serie in der Mitte ab.\n\n"
        "Was würdest du auf ein Live-Dashboard deines Builds packen?\n\n"
        "#BuildInPublic #OpenSource #KI #Automatisierung #Startup"),
    (3, "en"): (
        "Most \"AI content\" projects are a demo and a waitlist. This one you can clone and run tonight.\n\n"
        "Genesis is an autonomous content system, open source. Copy .env, docker compose up, point it at "
        "your own niche — it scans trends, writes, voices, edits and publishes to 5 channels, 24/7 on a tiny droplet.\n\n"
        "Stack: n8n + GPT + ElevenLabs + HeyGen + Supabase. good-first-issue tags, one-command setup, "
        "built to be forked.\n\n"
        "Clone it, break it, send a PR.\n\n"
        "#OpenSource #SelfHosted #AI #Automation #DevTools"),
    (3, "de"): (
        "Die meisten \"KI-Content\"-Projekte sind eine Demo und eine Warteliste. Dieses kannst du heute Abend klonen und starten.\n\n"
        "Genesis ist ein autonomes Content-System, Open Source. .env kopieren, docker compose up, auf deine "
        "Nische richten — es scannt Trends, schreibt, vertont, schneidet und publiziert auf 5 Kanäle, 24/7 auf einem Mini-Droplet.\n\n"
        "Stack: n8n + GPT + ElevenLabs + HeyGen + Supabase. good-first-issue-Tags, Ein-Befehl-Setup, "
        "zum Forken gebaut.\n\n"
        "Klone es, brich es, schick einen PR.\n\n"
        "#OpenSource #SelfHosted #KI #Automatisierung #DevTools"),
    (4, "en"): (
        "No team. One founder, a patent, and a fleet of 7 AI agents that coordinate through the filesystem.\n\n"
        "At the core: Fafnir — 490k lines of Rust, 5 defense layers, 200+ sessions. Around it, agents that "
        "scan, decide and ship. Their public output is Genesis: an autonomous, open-source content system that runs 24/7.\n\n"
        "The point isn't a bigger team. It's leverage — architecting agents that do the work, and a system "
        "that improves itself while you sleep.\n\n"
        "Don't hire the work. Architect the agents that do it.\n\n"
        "#AI #AgentEngineering #Startup #OpenSource #BuildInPublic"),
    (4, "de"): (
        "Kein Team. Ein Gründer, ein Patent und eine Flotte von 7 KI-Agenten, die über das Dateisystem koordinieren.\n\n"
        "Im Kern: Fafnir — 490k Zeilen Rust, 5 Verteidigungsebenen, 200+ Sessions. Darum Agenten, die scannen, "
        "entscheiden und liefern. Ihr öffentlicher Output ist Genesis: ein autonomes, quelloffenes Content-System, 24/7.\n\n"
        "Der Punkt ist kein größeres Team. Es ist Hebel — Agenten architektieren, die die Arbeit tun, und ein "
        "System, das sich verbessert, während du schläfst.\n\n"
        "Stelle nicht die Arbeit ein. Architektiere die Agenten, die sie tun.\n\n"
        "#KI #AgentEngineering #Startup #OpenSource #BuildInPublic"),
    (5, "en"): (
        "Hot take: prompt engineering is dead. The skill was never writing prompts — it's building systems.\n\n"
        "If a human still triggers every run, if metrics never change the next prompt, if it can't improve "
        "itself — you built a tool, not a system.\n\n"
        "Genesis fails none of those tests. It scans, decides and ships on its own, reads its own metrics, "
        "and opens pull requests to rewrite its own prompts. In public. Open source.\n\n"
        "Watch it, then tell me prompt engineering is the skill.\n\n"
        "#AI #AgentEngineering #PromptEngineering #OpenSource #BuildInPublic"),
    (5, "de"): (
        "Hot Take: Prompt Engineering ist tot. Die Fähigkeit war nie Prompten — es ist Systeme bauen.\n\n"
        "Wenn ein Mensch noch jeden Lauf startet, wenn Metriken nie den nächsten Prompt ändern, wenn es sich "
        "nicht selbst verbessert — hast du ein Tool gebaut, kein System.\n\n"
        "Genesis besteht keinen dieser Tests. Es scannt, entscheidet und liefert selbst, liest die eigenen "
        "Metriken und öffnet Pull Requests, um seine Prompts umzuschreiben. Öffentlich. Open Source.\n\n"
        "Sieh es dir an — und sag mir dann, Prompten sei die Fähigkeit.\n\n"
        "#KI #AgentEngineering #PromptEngineering #OpenSource #BuildInPublic"),
}

from utm import wrap as _utm_wrap  # UTM-атрибуция first-comment (audit 2026-08-07)
FIRST_COMMENT = (f"Links:  🔴 Live dashboard: {_utm_wrap(LIVE, source='linkedin', campaign='selfpromo')}   ·   ⭐ Code: {GH}")


# ============================================================
#  Telegram send (gate)
# ============================================================

def _tg_token():
    for env in (ROOT / "Projects/ContentMachine/receiver/.env",
                ROOT / "Projects/genesis-content-os/.env"):
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("TG_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    raise SystemExit("TG_BOT_TOKEN не найден (receiver/.env)")


def _multipart(fields: dict, files: dict):
    boundary = "----genesisselfpromo" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    for k, path in files.items():
        p = Path(path)
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{k}"; '
                 f'filename="{p.name}"\r\n').encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += p.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return boundary, body


def _tg_api(token, method, fields, files=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        boundary, body = _multipart(fields, files)
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())


def send_to_denis(pdf_path: Path, caption: str, title: str, topic: int, lang: str):
    """Заливает карусель на дроплет и шлёт Денису approve+кнопку через @gerdennisapprove_bot.

    Публикацию по кнопке обслуживает дроплетный li_callback_listener (gpub:<id>), а НЕ
    Клешня/MyOpenClaw — иначе тап ловит Клешня и публикации нет.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from genesis_droplet_gate import send_via_droplet  # noqa: E402
    pend_id = uuid.uuid4().hex[:12]
    ok, out = send_via_droplet(pend_id, pdf_path, "doc", caption,
                               title, FIRST_COMMENT, lang)
    print(out)
    return pend_id, ok


# ============================================================
#  Ротация
# ============================================================

ROTATION = [(1, "en"), (2, "de"), (3, "en"), (4, "de"), (5, "en"),
            (1, "de"), (2, "en"), (3, "de"), (4, "en"), (5, "de")]


def next_rotation():
    idx = 0
    if STATE_FILE.exists():
        try:
            idx = int(json.loads(STATE_FILE.read_text()).get("idx", 0))
        except Exception:
            idx = 0
    topic, lang = ROTATION[idx % len(ROTATION)]
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"idx": (idx + 1) % len(ROTATION),
                                      "last": f"topic{topic}_{lang}"}))
    return topic, lang


# ============================================================
#  main
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", choices=["en", "de"])
    ap.add_argument("--topic", type=int, choices=[1, 2, 3, 4, 5])
    ap.add_argument("--rotate", action="store_true",
                    help="взять тему+язык из ротации (игнорирует --lang/--topic)")
    ap.add_argument("--out", help="путь к PDF (по умолчанию output/selfpromo/)")
    ap.add_argument("--no-send", action="store_true", help="только собрать PDF, не слать в Telegram")
    args = ap.parse_args()

    if args.rotate:
        topic, lang = next_rotation()
    else:
        if not args.lang or not args.topic:
            ap.error("нужны --lang и --topic (или --rotate)")
        topic, lang = args.topic, args.lang

    ct = _register_fonts()
    spec = build_spec(topic, lang)
    caption = CAPTIONS[(topic, lang)]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else (
        OUT_DIR / f"genesis_li_{lang}_topic{topic}_{int(time.time())}.pdf")
    ct.build_pdf(spec, str(out))
    size = out.stat().st_size
    print(f"PDF → {out} ({size} bytes, {len(spec['slides'])} slides, topic={topic} lang={lang})")

    if args.no_send:
        print("--no-send: в Telegram не отправлял.")
        print("\n=== LinkedIn caption ===\n" + caption)
        return

    pend_id, ok = send_to_denis(out, caption, spec["title"], topic, lang)
    print(f"Telegram → Denis (@gerdennisapprove_bot): ok={ok}, pending_id={pend_id}")
    print("Кнопка «Public» ждёт нажатия. Ловит дроплетный li_callback_listener.py (gpub:<id>).")


if __name__ == "__main__":
    main()
