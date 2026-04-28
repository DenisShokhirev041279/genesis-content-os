# Contributing to Genesis Content OS

Thanks for your interest. This is a small, active project — your PRs get read.

## Getting started

1. Fork the repo and clone your fork.
2. Provision a Supabase project on the free tier (`eu-central-1` recommended). Run `sql/001_initial_schema.sql` in the SQL Editor.
3. Sign up for Doppler (free) and create a project with `dev` and `prod` configs. Add the secrets listed in [README.md](README.md) under Quickstart step 3.
4. Install dependencies. TypeScript pieces use `bun install`; Python pieces use `uv sync`.
5. `doppler run -- bun test` (or `uv run pytest`) should pass before you start.

## PR process

1. Create a branch off `main` named `feature/<short-name>` or `fix/<short-name>`.
2. Make the change. Keep diffs surgical — touch only what you need.
3. Add or update tests for the behavior you changed.
4. Run linters and tests locally. Open a PR with a 1-paragraph summary and a checklist of what you tested.
5. One review approval lands the PR. Reviews usually happen within 72 hours.

## Code style

- **TypeScript**: strict mode on, no `any`, explicit return types on exported functions, `bun` for runtime/test.
- **Python**: 3.12, type hints on all signatures, `ruff` for lint, `pytest` for tests, `uv` for env.
- **SQL**: lowercase keywords are fine; UPPERCASE keywords are also fine; pick one per file and stick with it.
- **n8n workflows**: export the JSON, scrub credentials, commit under `workflows/`. Name nodes clearly — they show up in logs.

## Where help is most needed

These are first-class issues, tagged `good-first-issue` and `help-wanted`:

- **Module C — metrics populator**: pull post engagement from Plausible, LinkedIn, Telegram, YouTube into `metrics_snapshots`. Ghost has no public reads — Plausible covers it.
- **Module D — analyzer**: weekly GPT-4.1 review job that writes `insights` rows with `evidence_posts`.
- **Module E — auto-decision**: turn approved insights into new `prompts` rows, with Denis as the gate.
- **Module F — weekly-reports**: generate the markdown digest, deliver via Telegram and git commit.
- **Reddit and ProductHunt scanners** for Module A.
- **Multi-language support** beyond RU/EN.

## Asking questions

- **GitHub Discussions** for design questions, feature requests, ideas.
- **GitHub Issues** for bugs and concrete tasks.
- **Telegram** invite link is shared on request — open a Discussion thread or email the maintainer.
- **Security issues**: see [SECURITY.md](SECURITY.md) — do not open a public issue.
