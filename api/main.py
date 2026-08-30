"""
Ingest + query API.

    uvicorn api.main:app --reload --port 8080

Two audiences, two auth scopes:
  * scope=ingest  -- the CLI. Write-only on runs/events. One key per machine.
  * scope=read    -- the dashboard. Read-only.
  * scope=admin   -- minting keys.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from . import dispatch, plane, summarizer
from .db import (
    ApiKey, Run, RunArtifact, RunEvent, RunSummary, SessionLocal, SummaryJob,
    hash_key, init_db, utcnow,
)
from .logreduce import reduce_log

HEARTBEAT_GRACE_S = int(os.environ.get("HEARTBEAT_GRACE_S", "120"))
SUMMARIZE_ALL = os.environ.get("SUMMARIZE_ALL", "0") == "1"
EVENT_RETENTION_DAYS = int(os.environ.get("EVENT_RETENTION_DAYS", "45"))
SWEEP_INTERVAL_S = int(os.environ.get("SWEEP_INTERVAL_S", "30"))


# ---------------------------------------------------------------- lifecycle

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_admin_key()
    tasks = [asyncio.create_task(_sweeper()), asyncio.create_task(_summary_worker())]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


app = FastAPI(title="sdtools run API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"], allow_headers=["*"],
)


class GunzipMiddleware:
    """
    The CLI always gzips its payloads (logs compress ~10x, and this runs over
    field VPNs). Pure ASGI rather than BaseHTTPMiddleware because we have to
    replace the receive channel, not just the Request object.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or dict(scope["headers"]).get(b"content-encoding") != b"gzip":
            return await self.app(scope, receive, send)

        chunks: list[bytes] = []
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body"):
                break

        raw = b"".join(chunks)
        try:
            body = gzip.decompress(raw) if raw else b""
        except OSError:
            body = raw          # not actually gzipped; pass through

        scope = {**scope, "headers": [
            (k, v) for k, v in scope["headers"]
            if k not in (b"content-encoding", b"content-length")
        ] + [(b"content-length", str(len(body)).encode())]}

        done = False

        async def rcv():
            nonlocal done
            if done:
                return {"type": "http.disconnect"}
            done = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, rcv, send)


app.add_middleware(GunzipMiddleware)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# -------------------------------------------------------------------- auth

def _bootstrap_admin_key() -> None:
    """First boot: print an admin key so you can mint the real ones."""
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(ApiKey)):
            return
        raw, rec = ApiKey.mint("bootstrap-admin", scope="admin")
        db.add(rec)
        db.commit()
        print("\n" + "=" * 62)
        print("  sdtools bootstrap admin key (shown once):")
        print(f"    {raw}")
        print("  Mint per-machine ingest keys with POST /v1/keys")
        print("=" * 62 + "\n", flush=True)


def _auth(scopes: tuple[str, ...]):
    def dep(authorization: str = Header(None), db: Session = Depends(get_db)) -> ApiKey:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        raw = authorization.split(" ", 1)[1].strip()
        key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw)))
        if not key or not key.active:
            raise HTTPException(401, "invalid key")
        if key.scope != "admin" and key.scope not in scopes:
            raise HTTPException(403, f"key scope {key.scope!r} cannot do this")
        key.last_seen_at = utcnow()
        db.commit()
        return key
    return dep


# An ingest key leaked from a worker machine can write junk runs but cannot
# read the team's history. `submit` (operators) can also read, since they
# watch what they dispatched — and write telemetry, since operators run tools
# locally too ("every run is logged"). Only `admin` bypasses (see _auth).
ingest = _auth(("ingest", "submit"))
read = _auth(("read", "submit"))
admin = _auth(("admin",))


def _check(scopes: tuple[str, ...]):
    """Request-level variant of _auth for the dispatch router."""
    async def check(request: Request, db: Session) -> ApiKey:
        authorization = request.headers.get("authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token")
        raw = authorization.split(" ", 1)[1].strip()
        key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_key(raw)))
        if not key or not key.active:
            raise HTTPException(401, "invalid key")
        if key.scope != "admin" and key.scope not in scopes:
            raise HTTPException(403, f"key scope {key.scope!r} cannot do this")
        key.last_seen_at = utcnow()
        db.commit()
        return key
    return check


dispatch.deps.update(
    # submit (operator) keys may also act as workers — useful for local/dev
    # agents on an operator machine; ingest keys stay write/worker-only.
    ingest_check=_check(("ingest", "submit")),
    operator_check=_check(("submit",)),
    read_check=_check(("read", "submit")),
    plane_workflow_created=plane.workflow_created if plane.ENABLED else None,
    plane_workflow_finished=plane.workflow_finished if plane.ENABLED else None,
)
app.include_router(dispatch.router)


# ------------------------------------------------------------------ ingest

@app.get("/v1/health")
def health(db: Session = Depends(get_db)):
    running = db.scalar(select(func.count()).select_from(Run).where(Run.status == "running"))
    return {"ok": True, "running": running, "time": utcnow()}


@app.post("/v1/runs", status_code=202)
async def create_run(request: Request, db: Session = Depends(get_db), _: ApiKey = Depends(ingest)):
    d = await request.json()
    existing = db.get(Run, d["run_id"])
    if existing:
        return {"run_id": existing.run_id, "duplicate": True}
    a = d.get("actor", {})
    db.add(Run(
        run_id=d["run_id"], schema_version=d.get("schema_version", 1),
        tool=d["tool"], tool_version=d["tool_version"], toolkit_sha=d.get("toolkit_sha"),
        cli_version=d["cli_version"], cmdline=d["cmdline"], params=d.get("params") or {},
        project=d.get("project"), tags=d.get("tags") or [], cwd=d.get("cwd", ""),
        actor_user=a.get("user", "unknown"), actor_email=a.get("email"),
        machine_id=a.get("machine_id", "?"), hostname=a.get("hostname", "?"),
        platform=a.get("platform", "?"),
        status="running", started_at=_dt(d["started_at"]), last_heartbeat_at=utcnow(),
        input_summary=d.get("input_summary") or {}, parent_run_id=d.get("parent_run_id"),
        env_name=d.get("env_name"), env_digest=d.get("env_digest"),
    ))
    db.commit()
    return {"run_id": d["run_id"]}


@app.patch("/v1/runs/{run_id}")
async def finish_run(run_id: str, request: Request,
                     db: Session = Depends(get_db), _: ApiKey = Depends(ingest)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    d = await request.json()
    run.status = d["status"]
    run.exit_code = d.get("exit_code")
    run.finished_at = _dt(d["finished_at"])
    run.duration_ms = d.get("duration_ms")
    run.metrics = d.get("metrics") or {}
    run.output_summary = d.get("output_summary") or {}
    run.counts = d.get("counts") or {}
    run.error_kind = d.get("error_kind")
    run.error_message = d.get("error_message")
    run.log_digest = d.get("log_digest")
    db.commit()

    # Failures are always summarized. Successes wait for someone to look,
    # unless SUMMARIZE_ALL is on.
    if SUMMARIZE_ALL or run.status not in ("ok",):
        db.add(SummaryJob(run_id=run_id))
        db.commit()
    return {"ok": True}


@app.post("/v1/runs/{run_id}/events", status_code=202)
async def post_events(run_id: str, request: Request,
                      db: Session = Depends(get_db), _: ApiKey = Depends(ingest)):
    d = await request.json()
    events = d.get("events", [])
    if not events:
        return {"accepted": 0}
    if not db.get(Run, run_id):
        raise HTTPException(404, "unknown run")

    existing = set(db.scalars(
        select(RunEvent.seq).where(RunEvent.run_id == run_id,
                                   RunEvent.seq.in_([e["seq"] for e in events]))
    ))
    fresh = [e for e in events if e["seq"] not in existing]
    db.add_all([RunEvent(
        run_id=run_id, seq=e["seq"], ts=_dt(e["ts"]), level=e.get("level", "info"),
        stream=e.get("stream", "stdout"), message=e.get("message", "")[:8000],
        fields=e.get("fields"),
    ) for e in fresh])
    run = db.get(Run, run_id)
    run.event_count = (run.event_count or 0) + len(fresh)
    run.last_heartbeat_at = utcnow()
    db.commit()
    return {"accepted": len(fresh), "duplicates": len(events) - len(fresh)}


@app.post("/v1/runs/{run_id}/heartbeat", status_code=204)
async def heartbeat(run_id: str, request: Request,
                    db: Session = Depends(get_db), _: ApiKey = Depends(ingest)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    d = await request.json()
    run.last_heartbeat_at = utcnow()
    if d.get("progress") is not None:
        run.progress = d["progress"]
    run.progress_note = d.get("note")
    db.commit()
    return Response(status_code=204)


@app.post("/v1/runs/{run_id}/artifacts", status_code=202)
async def post_artifacts(run_id: str, request: Request,
                         db: Session = Depends(get_db), _: ApiKey = Depends(ingest)):
    d = await request.json()
    items = d.get("artifacts", [])
    db.add_all([RunArtifact(
        run_id=run_id, path=a.get("path", ""), kind=a.get("kind", "unknown"),
        size_bytes=a.get("size_bytes"), sha256=a.get("sha256"), rows=a.get("rows"),
        extra={k: v for k, v in a.items()
               if k not in ("path", "kind", "size_bytes", "sha256", "rows")},
    ) for a in items])
    db.commit()
    return {"accepted": len(items)}


# ------------------------------------------------------------------- query

@app.get("/v1/runs")
def list_runs(
    db: Session = Depends(get_db), _: ApiKey = Depends(read),
    project: str | None = None, tool: str | None = None, status: str | None = None,
    actor: str | None = None, since_hours: int | None = None,
    q: str | None = Query(None, description="substring match on cmdline"),
    limit: int = Query(50, le=500), before: str | None = None,
):
    stmt = select(Run).order_by(desc(Run.started_at)).limit(limit)
    if project:
        stmt = stmt.where(Run.project == project)
    if tool:
        stmt = stmt.where(Run.tool == tool)
    if status:
        stmt = stmt.where(Run.status.in_(status.split(",")))
    if actor:
        stmt = stmt.where(Run.actor_user == actor)
    if since_hours:
        stmt = stmt.where(Run.started_at >= utcnow() - timedelta(hours=since_hours))
    if q:
        stmt = stmt.where(Run.cmdline.ilike(f"%{q}%"))
    if before:
        stmt = stmt.where(Run.started_at < _dt(before))

    rows = list(db.scalars(stmt))
    digests = [r.log_digest for r in rows if r.log_digest]
    summaries = {
        s.log_digest: s.payload for s in db.scalars(
            select(RunSummary).where(RunSummary.log_digest.in_(digests),
                                     RunSummary.prompt_version == summarizer.PROMPT_VERSION)
        )
    } if digests else {}

    return {
        "runs": [{**_run_dict(r), "summary": summaries.get(r.log_digest)} for r in rows],
        "next_before": rows[-1].started_at.isoformat() if len(rows) == limit else None,
    }


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), _: ApiKey = Depends(read)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    arts = db.scalars(select(RunArtifact).where(RunArtifact.run_id == run_id).limit(500))
    return {
        **_run_dict(run),
        "artifacts": [{"path": a.path, "kind": a.kind, "size_bytes": a.size_bytes,
                       "rows": a.rows, **a.extra} for a in arts],
        "summary": _cached_summary(db, run),
    }


@app.get("/v1/runs/{run_id}/events")
def get_events(run_id: str, db: Session = Depends(get_db), _: ApiKey = Depends(read),
               after_seq: int = 0, level: str | None = None, limit: int = Query(500, le=5000)):
    stmt = (select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
            .order_by(RunEvent.seq).limit(limit))
    if level:
        stmt = stmt.where(RunEvent.level.in_(level.split(",")))
    rows = list(db.scalars(stmt))
    return {"events": [{"seq": e.seq, "ts": e.ts, "level": e.level, "stream": e.stream,
                        "message": e.message, "fields": e.fields} for e in rows],
            "last_seq": rows[-1].seq if rows else after_seq}


@app.get("/v1/runs/{run_id}/summary")
def get_summary(run_id: str, db: Session = Depends(get_db), _: ApiKey = Depends(read),
                refresh: bool = False):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "unknown run")
    if not refresh:
        cached = _cached_summary(db, run)
        if cached:
            return {"summary": cached, "cached": True}
    result = _generate_summary(db, run_id)
    if result is None:
        raise HTTPException(409, "run has not finished yet")
    return result


@app.get("/v1/stats")
def stats(db: Session = Depends(get_db), _: ApiKey = Depends(read), hours: int = 24):
    since = utcnow() - timedelta(hours=hours)
    base = select(Run).where(Run.started_at >= since).subquery()

    by_status = dict(db.execute(
        select(base.c.status, func.count()).group_by(base.c.status)).all())
    by_tool = dict(db.execute(
        select(base.c.tool, func.count()).group_by(base.c.tool)
        .order_by(desc(func.count())).limit(15)).all())
    by_actor = dict(db.execute(
        select(base.c.actor_user, func.count()).group_by(base.c.actor_user)
        .order_by(desc(func.count())).limit(20)).all())
    total_ms = db.scalar(select(func.sum(base.c.duration_ms))) or 0
    failed = by_status.get("failed", 0) + by_status.get("timeout", 0) + by_status.get("lost", 0)
    total = sum(by_status.values()) or 1

    from .db import AgentWorker, Job
    queue_depth = db.scalar(select(func.count()).select_from(Job)
                            .where(Job.state.in_(("queued", "blocked")))) or 0
    agents_online = db.scalar(select(func.count()).select_from(AgentWorker)
                              .where(AgentWorker.state != "offline")) or 0
    return {
        "window_hours": hours,
        "total_runs": sum(by_status.values()),
        "by_status": by_status, "by_tool": by_tool, "by_actor": by_actor,
        "failure_rate": round(failed / total, 4),
        "compute_hours": round(total_ms / 3_600_000, 2),
        "active_now": db.scalar(select(func.count()).select_from(Run)
                                .where(Run.status == "running")),
        "queue_depth": queue_depth,
        "agents_online": agents_online,
    }


@app.get("/v1/stream")
async def stream(_: ApiKey = Depends(read)):
    """SSE feed of run state changes. Good enough for a live dashboard;
    swap for Postgres LISTEN/NOTIFY or websockets if you outgrow polling."""
    async def gen():
        cursor = utcnow() - timedelta(seconds=10)
        while True:
            with SessionLocal() as db:
                rows = list(db.scalars(
                    select(Run).where(Run.updated_at > cursor)
                    .order_by(Run.updated_at).limit(100)))
            if rows:
                cursor = rows[-1].updated_at
                for r in rows:
                    yield f"data: {json.dumps(_run_dict(r), default=str)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------- admin

@app.post("/v1/keys", status_code=201)
async def mint_key(request: Request, db: Session = Depends(get_db), _: ApiKey = Depends(admin)):
    d = await request.json()
    raw, rec = ApiKey.mint(d["name"], scope=d.get("scope", "ingest"),
                           actor_user=d.get("actor_user"))
    db.add(rec)
    db.commit()
    return {"key": raw, "name": rec.name, "scope": rec.scope, "prefix": rec.prefix}


@app.get("/v1/keys")
def list_keys(db: Session = Depends(get_db), _: ApiKey = Depends(admin)):
    return {"keys": [{"name": k.name, "prefix": k.prefix, "scope": k.scope,
                      "active": k.active, "last_seen_at": k.last_seen_at}
                     for k in db.scalars(select(ApiKey))]}


# --------------------------------------------------------------- internals

def _dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _run_dict(r: Run) -> dict:
    return {
        "run_id": r.run_id, "tool": r.tool, "tool_version": r.tool_version,
        "toolkit_sha": r.toolkit_sha, "cmdline": r.cmdline, "params": r.params,
        "project": r.project, "tags": r.tags, "status": r.status, "exit_code": r.exit_code,
        "actor_user": r.actor_user, "hostname": r.hostname, "machine_id": r.machine_id,
        "started_at": r.started_at, "finished_at": r.finished_at,
        "last_heartbeat_at": r.last_heartbeat_at,
        "duration_ms": r.duration_ms, "progress": r.progress, "progress_note": r.progress_note,
        "input_summary": r.input_summary, "output_summary": r.output_summary,
        "metrics": r.metrics, "counts": r.counts,
        "error_kind": r.error_kind, "error_message": r.error_message,
        "log_digest": r.log_digest, "event_count": r.event_count,
        "env_name": r.env_name, "env_digest": r.env_digest,
        "updated_at": r.updated_at,
    }


def _cached_summary(db: Session, run: Run) -> dict | None:
    if not run.log_digest:
        return None
    rec = db.scalar(select(RunSummary).where(
        RunSummary.log_digest == run.log_digest,
        RunSummary.prompt_version == summarizer.PROMPT_VERSION))
    return rec.payload if rec else None


def _generate_summary(db: Session, run_id: str) -> dict | None:
    run = db.get(Run, run_id)
    if not run or run.status == "running":
        return None

    events = [{"seq": e.seq, "level": e.level, "stream": e.stream, "message": e.message}
              for e in db.scalars(select(RunEvent).where(RunEvent.run_id == run_id)
                                  .order_by(RunEvent.seq))]
    reduced = reduce_log(events)
    payload, model, tin, tout = summarizer.summarize(_run_dict(run), reduced.text)

    digest = run.log_digest or f"run:{run_id}"
    if not db.scalar(select(RunSummary).where(
            RunSummary.log_digest == digest,
            RunSummary.prompt_version == summarizer.PROMPT_VERSION)):
        db.add(RunSummary(log_digest=digest, prompt_version=summarizer.PROMPT_VERSION,
                          model=model, payload=payload, tokens_in=tin, tokens_out=tout))
        db.commit()
    return {"summary": payload, "cached": False, "model": model,
            "tokens_in": tin, "tokens_out": tout,
            "log_lines_in": reduced.lines_in, "log_lines_sent": reduced.lines_out}


async def _summary_worker() -> None:
    """In-process queue drain. Move to a real worker when volume warrants."""
    while True:
        try:
            with SessionLocal() as db:
                job = db.scalar(select(SummaryJob).where(SummaryJob.state == "queued")
                                .order_by(SummaryJob.id).limit(1))
                if job:
                    job.state, job.attempts = "working", job.attempts + 1
                    db.commit()
                    try:
                        await asyncio.to_thread(_generate_summary_isolated, job.run_id)
                        job.state = "done"
                    except Exception as exc:      # noqa: BLE001
                        job.last_error = str(exc)[:1000]
                        job.state = "queued" if job.attempts < 3 else "failed"
                    db.commit()
            await asyncio.sleep(0.5 if job else 3)
        except asyncio.CancelledError:
            return
        except Exception:                          # noqa: BLE001
            await asyncio.sleep(5)


def _generate_summary_isolated(run_id: str) -> None:
    with SessionLocal() as db:
        _generate_summary(db, run_id)


async def _sweeper() -> None:
    """Lost runs, expired job leases, offline agents, event retention."""
    while True:
        try:
            with SessionLocal() as db:
                dispatch.expire_leases(db)
                dispatch.mark_offline_agents(db)
                cutoff = utcnow() - timedelta(seconds=HEARTBEAT_GRACE_S)
                stale = list(db.scalars(select(Run).where(
                    Run.status == "running", Run.last_heartbeat_at < cutoff)))
                for r in stale:
                    r.status = "lost"
                    r.error_kind = "NoHeartbeat"
                    r.error_message = (f"no heartbeat since "
                                       f"{r.last_heartbeat_at.isoformat()}")
                    r.finished_at = r.last_heartbeat_at
                    db.add(SummaryJob(run_id=r.run_id))
                if stale:
                    db.commit()

                old = utcnow() - timedelta(days=EVENT_RETENTION_DAYS)
                doomed = [r.run_id for r in db.scalars(
                    select(Run).where(Run.started_at < old, Run.event_count > 0).limit(200))]
                if doomed:
                    db.query(RunEvent).filter(RunEvent.run_id.in_(doomed)).delete(
                        synchronize_session=False)
                    db.query(Run).filter(Run.run_id.in_(doomed)).update(
                        {"event_count": 0}, synchronize_session=False)
                    db.commit()
            await asyncio.sleep(SWEEP_INTERVAL_S)
        except asyncio.CancelledError:
            return
        except Exception:                          # noqa: BLE001
            await asyncio.sleep(SWEEP_INTERVAL_S)
