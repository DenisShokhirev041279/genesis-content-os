# Contributing to Genesis Content OS

Thanks for your interest. This is a small, active project — your PRs get read.

## Repository layout

```
sql/        001_initial_schema.sql — 7 tables, 2 views, 1 materialized view
workflows/  n8n exports (G_A trend scanner, G_A topic distiller, G_B content gen, YT Shorts pilot)
scripts/    Python entry points for Modules B/C/D/E/F
            g_b_instagram.py        — Reels publisher (Meta Graph v23)
            g_c_metrics.py          — YouTube + LinkedIn ingestor
            g_c_telegram.py         — Telegram channel-level ingestor
            g_c_ghost.py            — Ghost newsletter ingestor
            utm.py                  — UTM helper for Plausible attribution
            g_d_analyzer.py         — weekly insights from metrics
            g_e_auto_decision.py    — opens auto/prompt-* PRs from insights
            g_e_post_merge_sync.py  — reconciles merged PRs into prompts table
            g_f_weekly_report.py    — Sunday digest + Telegram DM
prompts/    scenario_v2.md, scenario_v3.md — the prompt versions Module E manages
docs/       SETUP.md and per-module design notes (MODULE_C/D/F_DESIGN.md)
```

## Getting started

1. Fork the repo and clone your fork.
2. **Provision Supabase** (free tier, `eu-central-1` recommended). Run `sql/001_initial_schema.sql` in the SQL Editor.
3. **Set up Doppler** with `dev` and `prod` configs. Required secrets are listed in [README.md](README.md) under Quickstart step 3.
4. **Install dependencies.** Python pieces (everything in `scripts/`) use `uv sync` or `pip install -r requirements.txt`. TypeScript pieces (planned) will use `bun install`.
5. `doppler run -- uv run pytest` should pass before you start.

You don't need access to the maintainer's production stack to contribute. For most issues a Supabase free-tier project and a single API token (the one the module touches) is enough.

## PR process

1. Create a branch off `main` named `feature/<short-name>` or `fix/<short-name>`.
2. Make the change. Keep diffs surgical — touch only what you need.
3. Add or update tests for the behavior you changed.
4. Run linters and tests locally. Open a PR with a 1-paragraph summary and a checklist of what you tested.
5. One review approval lands the PR. Reviews usually happen within 72 hours.

`auto/prompt-*` PRs are opened by Module E itself — those go through the same review gate.

## Code style

- **Python**: 3.12, type hints on all signatures, `ruff` for lint, `pytest` for tests, `uv` for env.
- **TypeScript**: strict mode on, no `any`, explicit return types on exported functions, `bun` for runtime/test.
- **SQL**: lowercase keywords are fine; UPPERCASE keywords are also fine; pick one per file and stick with it. Number new migrations (`002_...sql`, …) — don't edit the initial schema in place.
- **n8n workflows**: export the JSON, scrub credentials (replace IDs with `${N8N_CRED_*}` placeholders), commit under `workflows/`. Name nodes clearly — they show up in logs.

## Stack

`Supabase Postgres` · `pgvector` · `n8n` · `Doppler` · `OpenAI GPT-4` · `ElevenLabs` · `HeyGen` · `Ghost CMS` · `Astro` (site) · `Plausible Analytics` · `LinkedIn API` · `Telegram Bot API` · `YouTube Data + Analytics API` · `Instagram Graph API v23` · `faster-whisper` (subtitles) · `Python 3.12` · `TypeScript`

## Where help is most needed

These are first-class issues, tagged `good first issue` and `help wanted` on the [issue tracker](https://github.com/DenisShokhirev041279/genesis-content-os/issues):

- **Reddit and ProductHunt scanners** for Module A.
- **`g_c_metrics.py` SQLite mock** so contributors can run the ingestor locally without Supabase access.
- **`utm.py` test coverage** — more edge cases (already-wrapped URLs, internationalised domains, fragment preservation).
- **Module D prompt iteration** — better insight prompts that improve evidence quality on small `n`.
- **Multi-language support** beyond RU/EN.
- **Plausible self-host kit** — Compose file + Caddy config that drops in next to the existing stack.

## Hello-world contribution flow

The fastest way to make your first PR:

1. Pick a `good first issue`. Comment to claim it.
2. Branch off `main` (`fix/utm-fragment` style).
3. Make the change. Add a test under `tests/` (create the dir if it doesn't exist yet — the project is small enough that you'll often be the first).
4. `ruff check . && uv run pytest` locally.
5. Open the PR with a 1-paragraph summary, a checklist of what you tested, and a screenshot or log snippet if the change is observable.

## Asking questions

- **GitHub Discussions** for design questions, feature requests, ideas.
- **GitHub Issues** for bugs and concrete tasks.
- **Telegram** invite link is shared on request — open a Discussion thread or email the maintainer.
- **Security issues**: see [SECURITY.md](SECURITY.md) — do not open a public issue.
