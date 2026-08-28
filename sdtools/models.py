"""
Wire contract shared by the CLI and the API.

This module is the single source of truth for what a "run" looks like.
Everything else -- CLI, API, dashboard, summarizer -- is an implementation
detail that can be replaced. Bump SCHEMA_VERSION when you change a field
meaning; add fields freely (consumers must tolerate unknown fields).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class RunStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    # Set server-side when heartbeats stop without a terminal update.
    LOST = "lost"


class Level(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Actor(BaseModel):
    """Who and where. Resolved once per machine, cached in config."""

    user: str                      # os login or configured display name
    email: str | None = None
    machine_id: str                # stable hash of hostname+MAC, not PII-ish
    hostname: str
    platform: str                  # "Linux-6.1-x86_64"


class RunStart(BaseModel):
    """POST /v1/runs -- emitted the instant a tool begins."""

    schema_version: int = SCHEMA_VERSION
    run_id: str                    # client-generated UUIDv7/4 -> offline-safe identity
    tool: str
    tool_version: str
    toolkit_sha: str | None = None  # git sha of the tools/ repo -> reproducibility
    cli_version: str
    cmdline: str                   # redacted, human-readable
    params: dict[str, Any] = Field(default_factory=dict)
    project: str | None = None      # free-form grouping: client, site, job number
    tags: list[str] = Field(default_factory=list)
    cwd: str
    actor: Actor
    started_at: datetime
    input_summary: dict[str, Any] = Field(default_factory=dict)
    parent_run_id: str | None = None  # for tools that fan out into sub-runs
    env_name: str | None = None       # named environment the tool ran in
    env_digest: str | None = None     # content address of spec+lockfile


class RunFinish(BaseModel):
    """PATCH /v1/runs/{run_id} -- terminal state."""

    status: RunStatus
    exit_code: int | None = None
    finished_at: datetime
    duration_ms: int
    metrics: dict[str, float | int | str] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    counts: dict[str, int] = Field(default_factory=dict)   # {"error": 2, "warning": 11}
    error_kind: str | None = None   # coarse bucket: "MissingCRS", "OOM", "BadInput"
    error_message: str | None = None
    log_digest: str | None = None   # sha256 of normalized log -> summary cache key


class Event(BaseModel):
    """One log line. Batched; never sent one at a time."""

    seq: int                       # monotonic per run -> dedupe + ordering
    ts: datetime
    level: Level = Level.INFO
    stream: Literal["stdout", "stderr", "tool", "cli"] = "stdout"
    message: str
    fields: dict[str, Any] | None = None   # structured extras from ::sdtools:: lines


class EventBatch(BaseModel):
    """POST /v1/runs/{run_id}/events"""

    run_id: str
    events: list[Event]


class Heartbeat(BaseModel):
    """POST /v1/runs/{run_id}/heartbeat -- lets the server detect dead runs."""

    ts: datetime
    progress: float | None = None  # 0..1 if the tool reports it
    note: str | None = None


class Artifact(BaseModel):
    """Metadata only. File bytes never leave the machine unless you opt in."""

    path: str
    kind: str                      # "laz", "geotiff", "gpkg", "report"
    size_bytes: int | None = None
    sha256: str | None = None
    rows: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Summary(BaseModel):
    """What the cheap model produces. Rendered directly by the dashboard."""

    headline: str                  # <= 90 chars, plain language
    what_happened: str             # 2-4 sentences
    outcome: Literal["success", "success_with_warnings", "failure", "unclear"]
    severity: Literal["none", "low", "medium", "high"]
    key_numbers: list[str] = Field(default_factory=list)   # "18.4M points out"
    problems: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"] = "medium"


class SummaryRecord(BaseModel):
    summary: Summary
    model: str
    prompt_version: int
    log_digest: str
    tokens_in: int
    tokens_out: int
    cached: bool = False
    created_at: datetime
