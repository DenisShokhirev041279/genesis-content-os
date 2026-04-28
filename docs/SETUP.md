# Genesis Content OS — Setup

End-to-end setup for the trend-scan -> topic-distill -> bilingual-publish pipeline.

## Prerequisites

- Supabase account (free tier is enough to start)
- Doppler CLI (recommended for secrets) — https://docs.doppler.com/docs/install-cli
- Node.js 20+
- Python 3.12+
- `psql` client (libpq)
- A self-hosted or cloud n8n instance (>= 1.50)
- Optional: Ghost blog, LinkedIn Marketing Developer Platform access, Telegram bot

## Step 1 — Create a Supabase project

1. Sign in at https://supabase.com/dashboard.
2. Create a new project. Region: pick the one closest to your audience (e.g. `eu-central-1` for EU).
3. Tier: Free is fine for the first ~500 MB of trend data.
4. After provisioning (~2 min), grab from `Project Settings -> API`:
   - Project URL (`https://<ref>.supabase.co`)
   - `anon` key
   - `service_role` key (bypasses RLS — keep secret)
5. Grab the database password from `Project Settings -> Database`.

## Step 2 — Apply the schema

```bash
export SUPABASE_DB_URL="postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
psql "$SUPABASE_DB_URL" -f sql/001_initial_schema.sql
```

Verify:

```bash
# Expected: >= 7
psql "$SUPABASE_DB_URL" -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"

# Expected: 2 rows (vector, pg_trgm)
psql "$SUPABASE_DB_URL" -c "SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm');"

# Expected: 5 rows, all is_active=t
psql "$SUPABASE_DB_URL" -c "SELECT module, version, is_active FROM prompts;"
```

## Step 3 — Set up Doppler workspace + project

```bash
doppler login
doppler setup --project genesis-content-os --config dev
```

Create configs `dev` and `prod`. Use `dev` for local n8n / testing, `prod` for the deployed pipeline.

## Step 4 — Configure secrets

Push the values from `.env.example` into Doppler. Required to make the pipeline run end-to-end:

| Variable | Used by | Where to obtain |
|---|---|---|
| `SUPABASE_GENESIS_URL` | every workflow | Supabase Project Settings -> API |
| `SUPABASE_GENESIS_ANON_KEY` | optional read-only consumers | Supabase Project Settings -> API |
| `SUPABASE_GENESIS_SERVICE_KEY` | n8n -> Supabase REST | Supabase Project Settings -> API |
| `OPENAI_API_KEY` | distiller, content gen, scenario gen | https://platform.openai.com/api-keys |
| `ELEVENLABS_API_KEY` | YT shorts voice | https://elevenlabs.io |
| `ELEVENLABS_VOICE_ID` | YT shorts voice | ElevenLabs Voice Library |
| `HEYGEN_API_KEY` | YT shorts avatar | https://heygen.com |
| `HEYGEN_AVATAR_ID` | YT shorts avatar | HeyGen Avatars page |
| `TELEGRAM_BOT_TOKEN` | TG channel post (v0.4) | @BotFather |
| `TELEGRAM_TARGET_CHAT` | system alerts | your own numeric chat ID |
| `TELEGRAM_PUBLIC_CHANNEL` / `TELEGRAM_PUBLIC_CHANNEL_ID` | public announcements | your channel @handle and -100... ID |
| `GHOST_ADMIN_KEY` | bilingual blog publish | Ghost -> Integrations -> custom (format: `<keyId>:<keySecret>`) |
| `GHOST_API_URL` | bilingual blog publish | your blog base URL |
| `LINKEDIN_TOKEN` | LinkedIn post | LinkedIn Developer App, Marketing Developer Platform required for posting |
| `LINKEDIN_PERSON_URN` | LinkedIn post author | `urn:li:person:<id>` from /me API |
| `GITHUB_PAT` | trend scanner | https://github.com/settings/tokens (read:public_repo) |
| `N8N_API_KEY` | bulk import workflows | n8n -> Settings -> n8n API |
| `N8N_BASE_URL` | bulk import workflows | your n8n base URL |
| `PLAUSIBLE_TOKEN` / `PLAUSIBLE_SITE_ID` | analytics ingest | https://plausible.io |
| `RECEIVER_BASE_URL` / `RECEIVER_SECRET` | YT shorts video render (optional) | self-hosted ContentMachine receiver |
| `AUTHOR_NAME` / `AUTHOR_URL` / `AUTHOR_BIO` / `BLOG_BASE_URL` | article author identity | your own |

Verify:

```bash
doppler secrets --only-names | wc -l   # expected: >= 15
```

## Step 5 — Import n8n workflows

In n8n, create three credentials first (you'll reference their IDs in env):

1. `supabase_genesis` (HTTP Header Auth):
   - `apikey` = `${SUPABASE_GENESIS_SERVICE_KEY}`
   - `Authorization` = `Bearer ${SUPABASE_GENESIS_SERVICE_KEY}`
2. `OpenAI` (built-in OpenAI credential type)
3. `Telegram Bot` (built-in Telegram credential, paste `TELEGRAM_BOT_TOKEN`)

Capture each credential's ID from the n8n URL and write them to env:

```bash
doppler secrets set N8N_CRED_SUPABASE_ID=...
doppler secrets set N8N_CRED_OPENAI_ID=...
doppler secrets set N8N_CRED_TELEGRAM_ID=...
```

Bulk-import workflows from `workflows/`:

```bash
for f in workflows/*.json; do
  curl -X POST "$N8N_BASE_URL/api/v1/workflows" \
    -H "X-N8N-API-KEY: $N8N_API_KEY" \
    -H "Content-Type: application/json" \
    -d @"$f"
done
```

After import, open each workflow in the n8n UI and:

- Replace any `${...}` placeholders inside HTTP Request URL fields and credential ID fields with real values (n8n won't expand those automatically — they were added so the JSON makes intent obvious).
- Confirm credentials are bound (re-bind if the UI shows "Credential not found").
- Activate the workflow.

## Step 6 — Configure cron schedules

The workflows ship with these defaults (timezone Europe/Berlin):

| Workflow | Cron | Purpose |
|---|---|---|
| `G_A_trend_scanner` | `0 */6 * * *` | scan GitHub + HN every 6h |
| `G_A_topic_distiller` | `0 6 * * *` | distill 24h of signals into topics |
| `G_B_content_gen v0.1` | `30 9 * * *` | bilingual blog publish + LinkedIn + Telegram |
| `YT Shorts Pilot v0.3` | `0 14 * * 1,2,4,5` | publish queued shorts Mon/Tue/Thu/Fri |
| `YT Shorts Pilot v0.4` | `0 9 * * *` | full GPT scenario -> render -> publish |

Adjust to your timezone if needed.

## Verification

Run each workflow manually once via the n8n UI:

```bash
# 1. trigger G_A_trend_scanner — expect rows in trend_signals
psql "$SUPABASE_DB_URL" -c "SELECT count(*), source FROM trend_signals GROUP BY source;"

# 2. trigger G_A_topic_distiller — expect rows in topics with status='queued'
psql "$SUPABASE_DB_URL" -c "SELECT slug_en, hype_score FROM topics WHERE status='queued' ORDER BY hype_score DESC LIMIT 10;"

# 3. trigger G_B_content_gen — expect 1 topic flipped to status='published' and 2 Ghost posts
psql "$SUPABASE_DB_URL" -c "SELECT slug_en, status, published_at FROM topics WHERE status='published' ORDER BY published_at DESC LIMIT 5;"
```

If `G_B_content_gen` fires the "no topic" Telegram alert, the distiller hasn't seeded the queue yet — re-run scanner + distiller in order.
