"""
Storage layer. SQLAlchemy models so the same code runs on Postgres
(production) and SQLite (local dev / CI). See schema.sql for the
Postgres-native DDL with the indexes and partitioning you actually want
in production.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY, JSON, BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, Uuid, create_engine, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./sdtools.db")

# SQLite only autoincrements a plain INTEGER PRIMARY KEY, so vary the type.
PK_BIGINT = BigInteger().with_variant(Integer, "sqlite")

# run ids are native uuid in the production DDL (schema.sql); a String column
# makes Postgres reject every comparison with "operator does not exist:
# uuid = character varying". SQLite keeps plain strings.
RUN_UUID = Uuid(as_uuid=False).with_variant(String(64), "sqlite")

# runs.tags is text[] in the production DDL; SQLite has no arrays.
TAGS = ARRAY(Text).with_variant(JSON, "sqlite")

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))          # "anik-laptop", "ci"
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))          # shown in UI
    scope: Mapped[str] = mapped_column(
        Enum("ingest", "read", "submit", "admin", name="key_scope"),
        default="ingest",
    )
    actor_user: Mapped[str | None] = mapped_column(String(120), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @staticmethod
    def mint(name: str, scope: str = "ingest", actor_user: str | None = None):
        raw = f"sdt_{scope[:4]}_{secrets.token_urlsafe(32)}"
        return raw, ApiKey(
            name=name, scope=scope, actor_user=actor_user,
            key_hash=hashlib.sha256(raw.encode()).hexdigest(), prefix=raw[:14],
        )


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(RUN_UUID, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    tool: Mapped[str] = mapped_column(String(120), index=True)
    tool_version: Mapped[str] = mapped_column(String(40))
    toolkit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cli_version: Mapped[str] = mapped_column(String(40))
    cmdline: Mapped[str] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    project: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    tags: Mapped[list] = mapped_column(TAGS, default=list)
    cwd: Mapped[str] = mapped_column(Text)

    actor_user: Mapped[str] = mapped_column(String(120), index=True)
    actor_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    machine_id: Mapped[str] = mapped_column(String(64), index=True)
    hostname: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(200))

    status: Mapped[str] = mapped_column(
        Enum("running", "ok", "failed", "cancelled", "timeout", "lost",
             name="run_status"),
        index=True,
    )
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                               nullable=True, index=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress_note: Mapped[str | None] = mapped_column(String(300), nullable=True)

    input_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    output_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    counts: Mapped[dict] = mapped_column(JSON, default=dict)
    error_kind: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    parent_run_id: Mapped[str | None] = mapped_column(RUN_UUID, nullable=True, index=True)
    env_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    env_digest: Mapped[str | None] = mapped_column(String(24), nullable=True)

    event_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, onupdate=utcnow)


Index("ix_runs_started_desc", Run.started_at.desc())
Index("ix_runs_project_started", Run.project, Run.started_at.desc())
Index("ix_runs_status_started", Run.status, Run.started_at.desc())


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        # Idempotent ingest: replaying a spooled batch is a no-op.
        UniqueConstraint("run_id", "seq", name="uq_run_event_seq"),
        Index("ix_events_run_seq", "run_id", "seq"),
        Index("ix_events_level", "run_id", "level"),
    )

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(RUN_UUID, ForeignKey("runs.run_id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    level: Mapped[str] = mapped_column(String(12))
    stream: Mapped[str] = mapped_column(String(12))
    message: Mapped[str] = mapped_column(Text)
    fields: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RunArtifact(Base):
    __tablename__ = "run_artifacts"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(RUN_UUID, ForeignKey("runs.run_id", ondelete="CASCADE"),
                                        index=True)
    path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


class RunSummary(Base):
    """
    Keyed by (log_digest, prompt_version) rather than run_id -- an identical
    rerun costs nothing, which is most of why this stays cheap.
    """

    __tablename__ = "run_summaries"
    __table_args__ = (
        UniqueConstraint("log_digest", "prompt_version", name="uq_summary_digest"),
    )

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    log_digest: Mapped[str] = mapped_column(String(64), index=True)
    prompt_version: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SummaryJob(Base):
    __tablename__ = "summary_jobs"

    id: Mapped[int] = mapped_column(PK_BIGINT, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(RUN_UUID, index=True)
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Dispatcher: workflows -> jobs -> agents.
#
# Semantics (the deterministic core):
#   * Claim order is a TOTAL order: (priority ASC, created_at ASC, job_id ASC).
#   * A job is claimed with a guarded UPDATE (state='queued' in the WHERE),
#     so exactly one agent wins even with concurrent pollers.
#   * A claim is a LEASE, not ownership. The agent renews it while executing;
#     a lease that expires puts the job back in the queue (attempts capped).
#     Net effect: at-least-once execution, exactly-one live executor.
#   * A job with dependencies starts 'blocked' and is promoted to 'queued'
#     only when every dependency is 'ok'. A permanently failed dependency
#     cancels its whole downstream cone (error_kind=UpstreamFailed).
# ---------------------------------------------------------------------------

JOB_STATES = ("blocked", "queued", "leased", "running",
              "ok", "failed", "cancelled")


class Workflow(Base):
    __tablename__ = "workflows"

    workflow_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    project: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)     # ${params.x} pool
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    total_jobs: Mapped[int] = mapped_column(Integer, default=0)
    submitted_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plane_issue_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # The claim query's exact shape.
        Index("ix_jobs_claim", "state", "queue", "priority", "created_at"),
        Index("ix_jobs_workflow", "workflow_id"),
        Index("ix_jobs_lease", "lease_expires_at"),
    )

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("workflows.workflow_id", ondelete="CASCADE"), nullable=True)
    step_key: Mapped[str | None] = mapped_column(String(120), nullable=True)

    tool: Mapped[str] = mapped_column(String(120))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    project: Mapped[str | None] = mapped_column(String(160), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    queue: Mapped[str] = mapped_column(String(80), default="default")
    priority: Mapped[int] = mapped_column(Integer, default=100)   # lower = sooner
    require_labels: Mapped[list] = mapped_column(JSON, default=list)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)  # job_ids

    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)     # bumped at claim
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    # Cooperative cancel: operators set this on a leased/running job; the
    # agent sees it on its next lease renewal and stops the tool.
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    leased_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                              nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # latest attempt
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_kind: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    submitted_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentWorker(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    machine_id: Mapped[str] = mapped_column(String(64), index=True)
    hostname: Mapped[str] = mapped_column(String(200))
    user: Mapped[str | None] = mapped_column(String(120), nullable=True)
    queues: Mapped[list] = mapped_column(JSON, default=list)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    tools: Mapped[list] = mapped_column(JSON, default=list)      # what it can run
    state: Mapped[str] = mapped_column(String(20), default="idle")  # idle | busy | offline
    current_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   default=utcnow, index=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


__all__ = [
    "AgentWorker", "ApiKey", "Base", "Job", "JOB_STATES", "Run", "RunArtifact",
    "RunEvent", "RunSummary", "SummaryJob", "Workflow",
    "SessionLocal", "engine", "func", "hash_key", "init_db", "utcnow",
]
