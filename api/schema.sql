-- Postgres-native DDL. The SQLAlchemy models in db.py will create a working
-- schema anywhere; this file is what you actually want in production --
-- correct types, the indexes that matter, and monthly partitioning on the
-- event table so retention is a DROP rather than a DELETE.

CREATE TYPE run_status AS ENUM
    ('running', 'ok', 'failed', 'cancelled', 'timeout', 'lost');

CREATE TYPE key_scope AS ENUM ('ingest', 'read', 'submit', 'admin');

-- Required by the cmdline GIN trigram index below. The official postgres:16
-- image includes the extension; it must be enabled in each database.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- api keys

CREATE TABLE api_keys (
    id            bigserial PRIMARY KEY,
    name          text        NOT NULL,
    key_hash      char(64)    NOT NULL UNIQUE,   -- sha256(raw); raw is never stored
    prefix        text        NOT NULL,
    scope         key_scope   NOT NULL DEFAULT 'ingest',
    actor_user    text,
    active        boolean     NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz
);

-- -------------------------------------------------------------------- runs

CREATE TABLE runs (
    run_id            uuid        PRIMARY KEY,   -- client-generated: offline-safe
    schema_version    smallint    NOT NULL DEFAULT 1,
    tool              text        NOT NULL,
    tool_version      text        NOT NULL,
    toolkit_sha       text,
    cli_version       text        NOT NULL,
    cmdline           text        NOT NULL,
    params            jsonb       NOT NULL DEFAULT '{}',
    project           text,
    tags              text[]      NOT NULL DEFAULT '{}',
    cwd               text        NOT NULL DEFAULT '',

    actor_user        text        NOT NULL,
    actor_email       text,
    machine_id        text        NOT NULL,
    hostname          text        NOT NULL,
    platform          text        NOT NULL,

    status            run_status  NOT NULL,
    exit_code         integer,
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz,
    duration_ms       bigint,
    last_heartbeat_at timestamptz,
    progress          real,
    progress_note     text,

    input_summary     jsonb       NOT NULL DEFAULT '{}',
    output_summary    jsonb       NOT NULL DEFAULT '{}',
    metrics           jsonb       NOT NULL DEFAULT '{}',
    counts            jsonb       NOT NULL DEFAULT '{}',
    error_kind        text,
    error_message     text,
    log_digest        char(64),
    parent_run_id     uuid REFERENCES runs(run_id) ON DELETE SET NULL,
    env_name          text,          -- named environment (envs/<name>)
    env_digest        text,          -- sha256[:12] of spec+lockfile+backend

    event_count       integer     NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- The dashboard's default query is "recent runs, newest first", optionally
-- narrowed. Partial + composite indexes cover every filter combination the
-- UI offers without a table scan.
CREATE INDEX runs_started_desc     ON runs (started_at DESC);
CREATE INDEX runs_project_started  ON runs (project, started_at DESC);
CREATE INDEX runs_tool_started     ON runs (tool, started_at DESC);
CREATE INDEX runs_actor_started    ON runs (actor_user, started_at DESC);
CREATE INDEX runs_status_started   ON runs (status, started_at DESC);
CREATE INDEX runs_digest           ON runs (log_digest);
CREATE INDEX runs_tags_gin         ON runs USING gin (tags);
CREATE INDEX runs_metrics_gin      ON runs USING gin (metrics jsonb_path_ops);
-- Heartbeat sweeper only ever scans live rows.
CREATE INDEX runs_live_heartbeat   ON runs (last_heartbeat_at)
    WHERE status = 'running';
-- Full-text-ish search on the command line.
CREATE INDEX runs_cmdline_trgm     ON runs USING gin (cmdline gin_trgm_ops);
-- requires: CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER runs_touch BEFORE UPDATE ON runs
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ------------------------------------------------------------------ events
-- Partitioned monthly: retention becomes DROP PARTITION, which is instant
-- and does not bloat. This is the only table that grows without bound.

CREATE TABLE run_events (
    id       bigserial,
    run_id   uuid        NOT NULL,
    seq      integer     NOT NULL,
    ts       timestamptz NOT NULL,
    level    text        NOT NULL,
    stream   text        NOT NULL,
    message  text        NOT NULL,
    fields   jsonb,
    PRIMARY KEY (id, ts),
    UNIQUE (run_id, seq, ts)          -- idempotent replay of spooled batches
) PARTITION BY RANGE (ts);

CREATE INDEX run_events_run_seq ON run_events (run_id, seq);
CREATE INDEX run_events_problems ON run_events (run_id, seq)
    WHERE level IN ('error', 'warning');

-- Create partitions ahead of time (pg_partman, or a monthly cron):
CREATE TABLE run_events_2026_08 PARTITION OF run_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE run_events_2026_09 PARTITION OF run_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');

-- --------------------------------------------------------------- artifacts

CREATE TABLE run_artifacts (
    id         bigserial PRIMARY KEY,
    run_id     uuid NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    path       text NOT NULL,
    kind       text NOT NULL,
    size_bytes bigint,
    sha256     char(64),
    rows       bigint,
    extra      jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX run_artifacts_run ON run_artifacts (run_id);
CREATE INDEX run_artifacts_sha ON run_artifacts (sha256);  -- find duplicate outputs

-- --------------------------------------------------------------- summaries
-- Keyed on the log digest, not the run: an identical rerun reuses the
-- summary and costs nothing.

CREATE TABLE run_summaries (
    id             bigserial PRIMARY KEY,
    log_digest     char(64)    NOT NULL,
    prompt_version smallint    NOT NULL,
    model          text        NOT NULL,
    payload        jsonb       NOT NULL,
    tokens_in      integer     NOT NULL DEFAULT 0,
    tokens_out     integer     NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (log_digest, prompt_version)
);

CREATE TABLE summary_jobs (
    id         bigserial PRIMARY KEY,
    run_id     uuid        NOT NULL,
    state      text        NOT NULL DEFAULT 'queued',
    attempts   smallint    NOT NULL DEFAULT 0,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX summary_jobs_queued ON summary_jobs (id) WHERE state = 'queued';

-- ------------------------------------------------------------------- views

-- What the dashboard's main table reads.
CREATE VIEW v_recent_runs AS
SELECT r.*, s.payload AS summary
FROM runs r
LEFT JOIN run_summaries s
       ON s.log_digest = r.log_digest AND s.prompt_version = 3
ORDER BY r.started_at DESC;

-- Per-tool reliability, for the "what keeps breaking" panel.
CREATE VIEW v_tool_health AS
SELECT tool,
       count(*)                                             AS runs,
       count(*) FILTER (WHERE status <> 'ok')                AS not_ok,
       round(avg(duration_ms) / 1000.0, 1)                   AS avg_s,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms) / 1000.0 AS p95_s,
       mode() WITHIN GROUP (ORDER BY error_kind)             AS top_error
FROM runs
WHERE started_at > now() - interval '30 days'
GROUP BY tool
ORDER BY not_ok DESC, runs DESC;
