-- GENESIS Content OS — initial schema
-- Apply in Supabase SQL Editor OR via: psql <SUPABASE_DB_URL> -f 001_initial_schema.sql
-- Version: 1.0.0
-- Created: 2026-04-24
-- Target: Supabase project genesis-content-os (Frankfurt)

-- ============================================================
-- EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;    -- для dedup embeddings
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- для fuzzy search topics

-- ============================================================
-- 1. TOPICS — замена TOPICS_DAILY.md
-- ============================================================
CREATE TABLE IF NOT EXISTS topics (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  discovered_at   timestamptz NOT NULL DEFAULT now(),
  source          text NOT NULL,           -- 'github' | 'hn' | 'reddit' | 'producthunt' | 'linkedin' | 'manual'
  source_url      text,
  title_raw       text NOT NULL,
  title_ru        text,
  title_en        text,
  slug_ru         text,
  slug_en         text,
  topic_category  text,                    -- 'claude-code' | 'mcp' | 'agents' | 'rag' | 'llmops' | 'webdev'
  hype_score      numeric DEFAULT 0 CHECK (hype_score >= 0 AND hype_score <= 100),
  priority        int DEFAULT 50 CHECK (priority >= 1 AND priority <= 100),
  status          text DEFAULT 'queued' CHECK (status IN ('queued','drafting','published','skipped','archived')),
  planned_for     date,
  published_at    timestamptz,
  has_video       boolean DEFAULT false,
  video_slug      text,
  embedding       vector(1536),            -- OpenAI text-embedding-3-small
  raw_signals     jsonb DEFAULT '[]'::jsonb,  -- массив trend_signal.id из которых собрана тема
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status);
CREATE INDEX IF NOT EXISTS idx_topics_planned_for ON topics(planned_for) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_topics_discovered_at ON topics(discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_topics_category ON topics(topic_category);
CREATE INDEX IF NOT EXISTS idx_topics_embedding ON topics USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================
-- 2. TREND_SIGNALS — сырой feed из Module A
-- ============================================================
CREATE TABLE IF NOT EXISTS trend_signals (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  captured_at     timestamptz NOT NULL DEFAULT now(),
  source          text NOT NULL,           -- 'github' | 'hn' | 'reddit' | 'producthunt'
  source_id       text,                    -- uniq id в source (для dedup при повторном скане)
  title           text NOT NULL,
  url             text,
  score           numeric,                 -- upvotes / stars / points — нормализованный
  raw             jsonb NOT NULL,          -- полный ответ source API
  processed       boolean DEFAULT false,   -- обработан ли Module A distiller'ом
  topic_id        uuid REFERENCES topics(id) ON DELETE SET NULL,  -- если родил тему
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_trend_signals_captured_at ON trend_signals(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_trend_signals_processed ON trend_signals(processed) WHERE processed = false;
CREATE INDEX IF NOT EXISTS idx_trend_signals_source ON trend_signals(source);

-- ============================================================
-- 3. POSTS — материал на каждый канал
-- ============================================================
CREATE TABLE IF NOT EXISTS posts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  topic_id        uuid NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  platform        text NOT NULL CHECK (platform IN ('ghost_ru','ghost_en','telegram','linkedin','youtube')),
  lang            text CHECK (lang IN ('ru','en')),
  title           text,
  slug            text,
  body_html       text,                    -- для Ghost
  body_md         text,                    -- для Telegram
  body_text       text,                    -- для LinkedIn / generic
  video_script    text,                    -- для YouTube
  hashtags        text[],
  cta             text,
  status          text DEFAULT 'drafting' CHECK (status IN ('drafting','ready','publishing','published','failed','skipped')),
  external_id     text,                    -- ghost post id / tg message id / LI URN / YT video id
  external_url    text,
  published_at    timestamptz,
  attempts        int DEFAULT 0,
  error           text,
  prompt_version  text,                    -- ссылка на prompts.version
  ab_variant      text,                    -- 'A' | 'B' | null
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_posts_topic ON posts(topic_id);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_platform_published ON posts(platform, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_failed ON posts(status, attempts) WHERE status = 'failed';

-- ============================================================
-- 4. METRICS_SNAPSHOTS — time-series метрик
-- ============================================================
CREATE TABLE IF NOT EXISTS metrics_snapshots (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  captured_at     timestamptz NOT NULL DEFAULT now(),
  platform        text NOT NULL,           -- 'ghost' | 'telegram' | 'linkedin' | 'youtube' | 'gsc' | 'plausible'
  post_id         uuid REFERENCES posts(id) ON DELETE CASCADE,  -- null для global метрик (subscribers count)
  metric_name     text NOT NULL,           -- 'impressions' | 'reactions' | 'views' | 'clicks' | 'subscribers' | 'position_avg' etc.
  metric_value    numeric NOT NULL,
  metadata        jsonb DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_metrics_captured_at ON metrics_snapshots(captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_post ON metrics_snapshots(post_id) WHERE post_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_metrics_platform_name ON metrics_snapshots(platform, metric_name, captured_at DESC);

-- ============================================================
-- 5. PROMPTS — версионирование промптов для Module D
-- ============================================================
CREATE TABLE IF NOT EXISTS prompts (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  module          text NOT NULL,           -- 'content_factory_ghost' | 'cf_telegram' | 'cf_linkedin' | 'cf_youtube' | 'topic_distiller' | 'self_improvement'
  version         text NOT NULL,           -- semver: '1.0.0' | '1.1.0'
  body            text NOT NULL,
  rationale       text,                    -- зачем сменили с прошлой версии
  is_active       boolean DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  activated_at    timestamptz,
  deactivated_at  timestamptz,
  parent_version  text,                    -- from which version derived
  approved_by     text,                    -- 'denis' | 'auto' (only for non-destructive)
  UNIQUE (module, version)
);

CREATE INDEX IF NOT EXISTS idx_prompts_module_active ON prompts(module, is_active) WHERE is_active = true;

-- ============================================================
-- 6. INSIGHTS — выводы Module D
-- ============================================================
CREATE TABLE IF NOT EXISTS insights (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  week_iso        text NOT NULL,           -- '2026-W17'
  insight_text    text NOT NULL,
  category        text,                    -- 'topic' | 'tone' | 'timing' | 'structure' | 'cta'
  evidence_posts  uuid[],                  -- массив post.id которые стали evidence
  proposed_change jsonb,                   -- {module, current_prompt_version, new_body, diff}
  status          text DEFAULT 'proposed' CHECK (status IN ('proposed','approved','rejected','applied')),
  approved_by     text,
  applied_at      timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_insights_week ON insights(week_iso);
CREATE INDEX IF NOT EXISTS idx_insights_status ON insights(status);

-- ============================================================
-- 7. WEEKLY_REPORTS — Module F output
-- ============================================================
CREATE TABLE IF NOT EXISTS weekly_reports (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  week_iso        text UNIQUE NOT NULL,    -- '2026-W17'
  period_start    date NOT NULL,
  period_end      date NOT NULL,
  markdown_body   text NOT NULL,
  kpi_summary     jsonb NOT NULL,
  delivered_to    text[],                  -- ['telegram_denis', 'email_denis', 'git_commit']
  delivered_at    timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_weekly_reports_period ON weekly_reports(period_start DESC);

-- ============================================================
-- TRIGGERS — updated_at auto
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS topics_set_updated_at ON topics;
CREATE TRIGGER topics_set_updated_at BEFORE UPDATE ON topics
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS posts_set_updated_at ON posts;
CREATE TRIGGER posts_set_updated_at BEFORE UPDATE ON posts
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- MATERIALIZED VIEW — kpi_daily (для Module E dashboard + Module F report)
-- ============================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS kpi_daily AS
SELECT
  date_trunc('day', captured_at)::date AS day,
  platform,
  metric_name,
  sum(metric_value) AS total,
  avg(metric_value) AS avg,
  count(*) AS n
FROM metrics_snapshots
GROUP BY 1, 2, 3;

CREATE UNIQUE INDEX IF NOT EXISTS idx_kpi_daily_uniq ON kpi_daily(day, platform, metric_name);

-- Рефреш через pg_cron (добавляем в отдельной миграции когда pg_cron включён):
-- SELECT cron.schedule('refresh_kpi_daily', '5 * * * *', $$REFRESH MATERIALIZED VIEW CONCURRENTLY kpi_daily$$);

-- ============================================================
-- VIEWS для быстрого чтения (Astro SSR /genesis)
-- ============================================================
CREATE OR REPLACE VIEW v_recent_posts AS
SELECT
  p.id,
  p.platform,
  p.lang,
  p.title,
  p.external_url,
  p.published_at,
  t.title_ru,
  t.title_en,
  t.topic_category
FROM posts p
LEFT JOIN topics t ON t.id = p.topic_id
WHERE p.status = 'published'
ORDER BY p.published_at DESC
LIMIT 20;

CREATE OR REPLACE VIEW v_hot_topics AS
SELECT
  id,
  title_ru,
  title_en,
  topic_category,
  hype_score,
  discovered_at,
  source
FROM topics
WHERE status = 'queued'
ORDER BY hype_score DESC, discovered_at DESC
LIMIT 10;

-- ============================================================
-- SEED DATA — стартовые промпты (пустые рыбки, заменим в Week 1 Day 2)
-- ============================================================
INSERT INTO prompts (module, version, body, rationale, is_active, approved_by, activated_at) VALUES
  ('topic_distiller', '1.0.0', 'PLACEHOLDER — replace in Week 1 Day 2', 'Initial', true, 'denis', now()),
  ('content_factory_ghost', '1.0.0', 'PLACEHOLDER — replace in Week 1 Day 2', 'Initial', true, 'denis', now()),
  ('content_factory_telegram', '1.0.0', 'PLACEHOLDER — replace in Week 1 Day 2', 'Initial', true, 'denis', now()),
  ('content_factory_linkedin', '1.0.0', 'PLACEHOLDER — replace in Week 1 Day 2', 'Initial', true, 'denis', now()),
  ('self_improvement', '1.0.0', 'PLACEHOLDER — replace in Week 2', 'Initial', true, 'denis', now())
ON CONFLICT (module, version) DO NOTHING;

-- ============================================================
-- SANITY CHECKS
-- ============================================================
-- После apply выполнить:
--   SELECT count(*) FROM information_schema.tables WHERE table_schema='public'; -- ≥ 7
--   SELECT count(*) FROM information_schema.views WHERE table_schema='public';  -- ≥ 2
--   SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm');    -- 2 rows
--   SELECT module, version, is_active FROM prompts;                             -- 5 rows, все active

-- DONE. Verification criterion 1.1 passed если tables >= 7.
