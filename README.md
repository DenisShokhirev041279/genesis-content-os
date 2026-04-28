# Genesis Content OS

**Autonomous content publishing pipeline that learns from your audience.**

Genesis Content OS scans technical news sources, distills daily topics with GPT-4.1, generates posts for 4 channels (LinkedIn, YouTube, Telegram, Ghost blog), publishes them on schedule, and reads its own metrics back to tune what it writes next. Voice-cloned audio (ElevenLabs) and avatar video (HeyGen) are produced from the same topic record. From topic discovery to a live post on every channel takes about 3 days.

Live demo: [genesis.gerdennisai.com](https://genesis.gerdennisai.com) *(placeholder — coming Week 4)*

---

## Architecture

```
                        ┌─────────────────────────────────────┐
                        │   Trend Sources (GitHub, HN, …)     │
                        └────────────────┬────────────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  Module A: trend-scanner    │   n8n: G_A_trend_scanner   (every 6h)
                          │  → trend_signals (raw)      │   n8n: G_A_topic_distiller (daily 06:00)
                          │  → topics    (distilled)    │
                          └──────────────┬──────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  Module B: content-gen      │   n8n: G_B_content_gen
                          │  GPT-4.1 → post per channel │   ElevenLabs voice clone
                          │  → posts                    │   HeyGen avatar render
                          └──────────────┬──────────────┘
                                         │
              ┌──────────────┬───────────┼────────────┬──────────────┐
              │              │           │            │              │
       ┌──────▼──────┐ ┌─────▼─────┐ ┌──▼────┐ ┌─────▼──────┐ ┌─────▼─────┐
       │  Ghost RU   │ │ Ghost EN  │ │   TG  │ │  LinkedIn  │ │  YouTube  │
       └──────┬──────┘ └─────┬─────┘ └──┬────┘ └─────┬──────┘ └─────┬─────┘
              │              │          │             │             │
              └──────────────┴──────────┼─────────────┴─────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Module C: metrics         │   Plausible + platform APIs
                          │  → metrics_snapshots       │   (planned, Week 2-3)
                          └─────────────┬──────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Module D: analyzer        │   GPT-4.1 weekly review
                          │  → insights                │   (planned, Week 3-4)
                          └─────────────┬──────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Module E: auto-decision   │   prompt-version bump on
                          │  → prompts (new version)   │   evidence threshold
                          │                            │   (planned, Week 4-5)
                          └─────────────┬──────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Module F: weekly-reports  │
                          │  → weekly_reports          │   (planned, Week 6+)
                          └────────────────────────────┘

  Storage:  Supabase Postgres + pgvector  (7 tables, 2 views, 1 materialized view)
  Secrets:  Doppler                       (10+ secrets, dev/prod configs)
  Workflows: n8n                          (HTTP Request nodes against Supabase REST)
  Analytics: Plausible Cloud              (privacy-first, no cookies)
```

---

## Quickstart

Local development from a clean clone:

1. **Clone and install**
   ```bash
   git clone https://github.com/dennisshokhirev/genesis-content-os.git
   cd genesis-content-os
   ```

2. **Provision Supabase** (free tier is enough)
   - Create project in `eu-central-1` (Frankfurt) or your nearest region.
   - SQL Editor → run `sql/001_initial_schema.sql`. You should see 7 tables, 2 views, 1 materialized view, 5 seeded prompts.
   - Copy Project URL and `service_role` key.

3. **Set up Doppler**
   - Create project `genesis-content-os` with configs `dev` and `prod`.
   - Add secrets: `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GHOST_ADMIN_KEY`, `TELEGRAM_BOT_TOKEN`, `LINKEDIN_TOKEN`, `ELEVENLABS_API_KEY`, `HEYGEN_API_KEY`, `PLAUSIBLE_TOKEN`, `N8N_API_KEY`.
   - `doppler login && doppler setup --project genesis-content-os --config dev`.

4. **Import n8n workflows**
   - Self-host n8n or use n8n Cloud.
   - Import JSON files from `workflows/` (Module A and B in Week 1; later modules added per ROADMAP).
   - Create one HTTP credential `supabase_genesis` with header `apikey: <service_key>`.

5. **Trigger a manual run**
   ```bash
   doppler run -- node scripts/trigger.js trend-scanner
   ```
   Watch `trend_signals` populate in Supabase. Topic distiller fires next at 06:00 in your configured timezone.

---

## Modules

| Module | Purpose | Status |
|---|---|---|
| **A — trend-scanner** | GitHub trending + HN top → `trend_signals` → distilled `topics` | live |
| **B — content-gen** | Topic → post per channel, voice + avatar render | live |
| **C — metrics** | Pull engagement metrics into `metrics_snapshots` | planned (Week 2-3) |
| **D — analyzer** | Weekly GPT-4.1 review → `insights` with evidence | planned (Week 3-4) |
| **E — auto-decision** | Apply approved insights as new prompt versions | planned (Week 4-5) |
| **F — weekly-reports** | Generate `weekly_reports` markdown digest | planned (Week 6+) |

Module E is the point where Genesis becomes autonomous: it reads its own performance and rewrites its own prompts, with Denis only approving the diff.

---

## Stack

`Supabase Postgres` · `pgvector` · `n8n` · `Doppler` · `OpenAI GPT-4.1` · `OpenAI text-embedding-3-small` · `ElevenLabs` · `HeyGen` · `Ghost CMS` · `LinkedIn API` · `Telegram Bot API` · `YouTube Data API` · `Plausible Analytics` · `TypeScript` · `Python 3.12`

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

The Apache-2.0 patent grant matters here: Genesis builds on workflow patterns covered by Rospatent software patent #2025612789 (NextGen Pathways). Apache-2.0 explicitly grants contributors and users a patent license under that patent, so the project stays freely usable.

---

## Author

**Denis Shokhirev** — Enterprise AI architect, founder of DennisCraft AI Studio (Erlangen, Germany).
- Site: [gerdennisai.com](https://gerdennisai.com)
- Patent: Rospatent software patent #2025612789 (NextGen Pathways)

---

## Status

Early. Active development. Open to contributors — see [CONTRIBUTING.md](CONTRIBUTING.md). Modules C through F are first-class issues for new contributors.
