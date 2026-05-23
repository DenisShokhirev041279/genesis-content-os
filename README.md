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
                          │  Module C: metrics         │   Plausible + YouTube + LinkedIn
                          │  → metrics_snapshots       │   + Telegram + Ghost ingestors
                          └─────────────┬──────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Module D: analyzer        │   GPT-4 weekly review
                          │  → insights                │   (skeleton, activating after C ≥7d)
                          └─────────────┬──────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Module E: auto-decision   │   prompt-version bump on
                          │  → prompts (new version)   │   evidence threshold
                          │                            │   + post-merge reconciler
                          └─────────────┬──────────────┘
                                        │
                          ┌─────────────▼──────────────┐
                          │  Module F: weekly-reports  │   Sunday 09:00 Berlin digest
                          │  → weekly_reports          │   → Telegram DM to Denis
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
   git clone https://github.com/DenisShokhirev041279/genesis-content-os.git
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
| **B — instagram** | Reels publisher via Meta Graph v23 (`scripts/g_b_instagram.py`) | live (prototype) |
| **C — metrics** | YT + LinkedIn + Telegram + Ghost ingestors → `metrics_snapshots` | live |
| **D — analyzer** | Weekly GPT-4 review → `insights` with evidence | live (skeleton, activates after C ≥7d) |
| **E — auto-decision** | Apply approved insights as new prompt versions, with PR + post-merge sync | live (skeleton) |
| **F — weekly-reports** | Sunday 09:00 Berlin digest → `weekly_reports` + Telegram DM | live |

Module E is the point where Genesis becomes autonomous: it reads its own performance and rewrites its own prompts, with Denis only approving the diff on `auto/prompt-*` PRs.

### Metrics ingestors

Module C is now three separate processes you can schedule independently:

- `scripts/g_c_metrics.py` — YouTube Data + Analytics API and LinkedIn `/socialMetadata`, with `--bootstrap` and `--backfill` modes.
- `scripts/g_c_telegram.py` — channel-level (`post_id=NULL`) subscriber count and 24h post velocity from the Bot API.
- `scripts/g_c_ghost.py` — newsletter `members_{total,paid,free}` and `posts_total` via Ghost Admin API.
- `scripts/utm.py` — UTM helper for clients that strip `Referer` (Telegram in-app, Instagram, YouTube copy-paste). Wraps own-domain URLs with `utm_source/medium/campaign/content`; leaves external links untouched.

Per-module design notes live in [`docs/MODULE_C_DESIGN.md`](docs/MODULE_C_DESIGN.md), [`docs/MODULE_D_DESIGN.md`](docs/MODULE_D_DESIGN.md), and [`docs/MODULE_F_DESIGN.md`](docs/MODULE_F_DESIGN.md).

---

## Stack

`Supabase Postgres` · `pgvector` · `n8n` · `Doppler` · `OpenAI GPT-4.1` · `OpenAI text-embedding-3-small` · `ElevenLabs` · `HeyGen` · `Ghost CMS` · `LinkedIn API` · `Telegram Bot API` · `YouTube Data + Analytics API` · `Instagram Graph API v23` · `Plausible Analytics` · `faster-whisper` (subtitles) · `Astro` (site) · `TypeScript` · `Python 3.12`

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

Active development. All six modules (A–F) are live; D and E are running as skeletons and graduate to full autonomy once Module C has accumulated ≥7 days of metrics. Open to contributors — see [CONTRIBUTING.md](CONTRIBUTING.md) and the `good first issue` label on the [issue tracker](https://github.com/DenisShokhirev041279/genesis-content-os/issues).
