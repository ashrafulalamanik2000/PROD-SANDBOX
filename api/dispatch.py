"""
The dispatcher: push work from one computer, execute on many.

This is a Postgres-backed queue, not a message broker, and that is a
deliberate choice at this scale:

  * The queue lives in the SAME database as the telemetry, so a job, its
    run, its log, and its summary are one JOIN — no cross-system stitching.
  * A guarded UPDATE gives exactly-one-claimant semantics with zero extra
    infrastructure. (On Postgres under heavy contention you can upgrade the
    candidate SELECT to FOR UPDATE SKIP LOCKED without changing the API.)
  * Determinism is a total order, stated once: jobs are claimed in
    (priority, created_at, job_id) order, filtered by queue and labels.
    Two identical submissions dispatch identically.

Execution model: at-least-once, exactly-one-live-executor. A claim is a
lease; the agent renews it while the tool runs. If the machine dies, the
lease expires and the job re-queues until max_attempts. Tools that write
should therefore be idempotent per-output-path (yours mostly overwrite,
which is fine) — or set max_attempts=1 / writes:false semantics.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from .db import AgentWorker, Job, SessionLocal, Workflow, utcnow

router = APIRouter()

LEASE_TTL_S = int(os.environ.get("LEASE_TTL_S", "120"))
_PARAM_REF = re.compile(r"\$\{params\.([A-Za-z0-9_]+)\}")

# Wired in main.py: (auth dependencies, plane hook). Set before include_router.
deps: dict[str, Any] = {}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------- submission

def _resolve_params(step_params: dict, wf_params: dict) -> dict:
    """Substitute ${params.x} in string values from the workflow param pool."""
    def sub(v):
        if isinstance(v, str):
            return _PARAM_REF.sub(
                lambda m: str(wf_params.get(m.group(1), m.group(0))), v)
        if isinstance(v, list):
            return [sub(i) for i in v]
        return v
    return {k: sub(v) for k, v in step_params.items()}


@router.post("/v1/jobs", status_code=201)
async def submit_job(request: Request, db: Session = Depends(get_db)):
    key = await deps["operator_check"](request, db)
    d = await request.json()
    if not d.get("tool"):
        raise HTTPException(422, "tool is required")
    job = Job(
        job_id=str(uuid.uuid4()), tool=d["tool"], params=d.get("params") or {},
        project=d.get("project"), tags=d.get("tags") or [],
        queue=d.get("queue", "default"), priority=int(d.get("priority", 100)),
        require_labels=d.get("require_labels") or [],
        max_attempts=int(d.get("max_attempts", 2)),
        submitted_by=key.actor_user or key.name,
    )
    db.add(job)
    db.commit()
    return {"job_id": job.job_id, "state": job.state, "queue": job.queue}


@router.post("/v1/workflows", status_code=201)
async def submit_workflow(request: Request, db: Session = Depends(get_db)):
    """
    Body:
      {name, project?, params?, steps: [
        {key, tool, params?, after?: [keys], queue?, priority?,
         require_labels?, max_attempts?}]}
    Steps are expanded into jobs at submission time; `after` is resolved to
    job ids. The DAG is validated (unknown/duplicate keys, cycles) before
    anything is written.
    """
    key = await deps["operator_check"](request, db)
    d = await request.json()
    steps = d.get("steps") or []
    if not d.get("name") or not steps:
        raise HTTPException(422, "name and steps are required")

    keys = [s.get("key") for s in steps]
    if len(set(keys)) != len(keys) or not all(keys):
        raise HTTPException(422, "every step needs a unique key")
    known = set(keys)
    for s in steps:
        for dep in s.get("after") or []:
            if dep not in known:
                raise HTTPException(422, f"step {s['key']!r}: unknown dependency {dep!r}")

    # Cycle check: repeatedly peel steps whose deps are all peeled.
    remaining = {s["key"]: set(s.get("after") or []) for s in steps}
    peeled: set[str] = set()
    while remaining:
        ready = [k for k, dd in remaining.items() if dd <= peeled]
        if not ready:
            raise HTTPException(422, f"dependency cycle among: {sorted(remaining)}")
        for k in ready:
            peeled.add(k)
            del remaining[k]

    wf_params = d.get("params") or {}
    wf = Workflow(
        workflow_id=str(uuid.uuid4()), name=d["name"], project=d.get("project"),
        params=wf_params, total_jobs=len(steps),
        submitted_by=key.actor_user or key.name,
    )
    db.add(wf)

    ids = {k: str(uuid.uuid4()) for k in keys}
    for s in steps:
        after = s.get("after") or []
        db.add(Job(
            job_id=ids[s["key"]], workflow_id=wf.workflow_id, step_key=s["key"],
            tool=s["tool"], params=_resolve_params(s.get("params") or {}, wf_params),
            project=d.get("project"), tags=(s.get("tags") or []) + [f"wf:{wf.workflow_id[:8]}"],
            queue=s.get("queue", "default"), priority=int(s.get("priority", 100)),
            require_labels=s.get("require_labels") or [],
            depends_on=[ids[a] for a in after],
            state="blocked" if after else "queued",
            max_attempts=int(s.get("max_attempts", 2)),
            submitted_by=key.actor_user or key.name,
        ))
    db.commit()

    hook = deps.get("plane_workflow_created")
    if hook:
        asyncio.create_task(hook(wf.workflow_id))
    return {"workflow_id": wf.workflow_id,
            "jobs": [{"step": k, "job_id": ids[k]} for k in keys]}


# ------------------------------------------------------------ agent surface

@router.post("/v1/agents/poll")
async def poll(request: Request, db: Session = Depends(get_db)):
    """
    Agent heartbeat + claim, one call. Body:
      {agent_id, machine_id, hostname, user?, queues, labels, tools}
    Returns 200 {job} when one was claimed, 200 {"job": null} otherwise.
    """
    await deps["ingest_check"](request, db)
    d = await request.json()
    agent_id = d["agent_id"]
    queues = d.get("queues") or ["default"]
    labels = set(d.get("labels") or [])

    agent = db.get(AgentWorker, agent_id)
    if not agent:
        agent = AgentWorker(agent_id=agent_id, machine_id=d.get("machine_id", "?"),
                            hostname=d.get("hostname", "?"))
        db.add(agent)
    agent.user = d.get("user")
    agent.queues, agent.labels = queues, sorted(labels)
    agent.tools = d.get("tools") or []
    agent.last_seen_at = utcnow()

    # Deterministic claim: total order, guarded UPDATE, first label-match wins.
    candidates = db.scalars(
        select(Job).where(Job.state == "queued", Job.queue.in_(queues))
        .order_by(Job.priority, Job.created_at, Job.job_id).limit(50)
    )
    claimed = None
    for j in candidates:
        if j.cancel_requested:
            # Cancelled while queued (e.g. requeued after a lease expiry
            # raced the cancel): settle instead of handing it out.
            _settle(db, j, "cancelled", {"error_kind": "CancelledByUser"})
            continue
        if set(j.require_labels or []) - labels:
            continue
        if agent.tools and j.tool not in agent.tools:
            continue
        won = db.execute(
            update(Job).where(Job.job_id == j.job_id, Job.state == "queued")
            .values(state="leased", leased_by=agent_id,
                    lease_expires_at=utcnow() + timedelta(seconds=LEASE_TTL_S),
                    attempts=Job.attempts + 1, updated_at=utcnow())
        ).rowcount == 1
        if won:
            claimed = db.get(Job, j.job_id)
            break

    agent.state = "busy" if claimed else "idle"
    agent.current_job_id = claimed.job_id if claimed else None
    db.commit()

    if not claimed:
        return {"job": None}
    return {"job": {
        "job_id": claimed.job_id, "tool": claimed.tool, "params": claimed.params,
        "project": claimed.project, "tags": claimed.tags, "queue": claimed.queue,
        "workflow_id": claimed.workflow_id, "step_key": claimed.step_key,
        "attempt": claimed.attempts, "max_attempts": claimed.max_attempts,
        "lease_ttl_s": LEASE_TTL_S,
    }}


@router.post("/v1/jobs/{job_id}/start")
async def job_start(job_id: str, request: Request, db: Session = Depends(get_db)):
    await deps["ingest_check"](request, db)
    d = await request.json()
    job = _leased_job(db, job_id, d.get("agent_id"))
    job.state = "running"
    job.run_id = d.get("run_id")
    job.started_at = job.started_at or utcnow()
    job.lease_expires_at = utcnow() + timedelta(seconds=LEASE_TTL_S)
    db.commit()
    return {"ok": True}


@router.post("/v1/jobs/{job_id}/lease")
async def job_lease(job_id: str, request: Request, db: Session = Depends(get_db)):
    await deps["ingest_check"](request, db)
    d = await request.json()
    job = _leased_job(db, job_id, d.get("agent_id"))
    job.lease_expires_at = utcnow() + timedelta(seconds=LEASE_TTL_S)
    db.commit()
    return {"lease_ttl_s": LEASE_TTL_S, "cancel_requested": job.cancel_requested}


@router.post("/v1/jobs/{job_id}/finish")
async def job_finish(job_id: str, request: Request, db: Session = Depends(get_db)):
    await deps["ingest_check"](request, db)
    d = await request.json()
    job = _leased_job(db, job_id, d.get("agent_id"))

    status = d.get("status", "failed")
    if status == "ok":
        _settle(db, job, "ok", d)
    elif status == "cancelled" or job.cancel_requested:
        # An operator asked for this job to stop — never retry it, even if
        # the tool's death was reported as a plain failure.
        _settle(db, job, "cancelled", d)
    elif job.attempts < job.max_attempts and status in ("failed", "timeout", "lost"):
        # Retryable: back to the queue. The failed run stays in telemetry.
        job.state = "queued"
        job.leased_by = None
        job.lease_expires_at = None
        job.error_kind = d.get("error_kind")
        job.error_message = d.get("error_message")
        db.commit()
    else:
        _settle(db, job, "failed", d)
    return {"state": db.get(Job, job_id).state}


def _leased_job(db: Session, job_id: str, agent_id: str | None) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if job.state not in ("leased", "running"):
        raise HTTPException(409, f"job is {job.state}, not leased/running")
    if agent_id and job.leased_by != agent_id:
        # The lease moved on (expired and reclaimed). The old agent must stop.
        raise HTTPException(409, "lease is held by another agent")
    return job


# ------------------------------------------------------- settlement + DAG

def _settle(db: Session, job: Job, state: str, d: dict | None = None) -> None:
    """Terminal transition + dependent promotion/cascade + workflow finalise."""
    d = d or {}
    job.state = state
    job.finished_at = utcnow()
    # leased_by is kept on terminal states: "which machine ran this" is data.
    job.lease_expires_at = None
    job.exit_code = d.get("exit_code")
    if state != "ok":
        job.error_kind = d.get("error_kind") or job.error_kind or state
        job.error_message = d.get("error_message") or job.error_message
    db.commit()

    if not job.workflow_id:
        return

    siblings = list(db.scalars(select(Job).where(Job.workflow_id == job.workflow_id)))
    by_id = {j.job_id: j for j in siblings}

    if state == "ok":
        for j in siblings:
            if j.state == "blocked" and all(
                    by_id.get(dep) and by_id[dep].state == "ok" for dep in j.depends_on):
                j.state = "queued"
    else:
        # Cancel the downstream cone.
        doomed = {job.job_id}
        changed = True
        while changed:
            changed = False
            for j in siblings:
                if j.state in ("blocked", "queued") and doomed & set(j.depends_on) \
                        and j.job_id not in doomed:
                    j.state = "cancelled"
                    j.error_kind = "UpstreamFailed"
                    j.error_message = f"dependency {job.step_key or job.job_id[:8]} {state}"
                    j.finished_at = utcnow()
                    doomed.add(j.job_id)
                    changed = True
    db.commit()

    # Finalise the workflow when nothing is live.
    live = {"blocked", "queued", "leased", "running"}
    states = [j.state for j in db.scalars(
        select(Job).where(Job.workflow_id == job.workflow_id))]
    if not any(s in live for s in states):
        wf = db.get(Workflow, job.workflow_id)
        wf.status = ("failed" if "failed" in states
                     else "cancelled" if "cancelled" in states else "ok")
        wf.finished_at = utcnow()
        db.commit()
        hook = deps.get("plane_workflow_finished")
        if hook:
            asyncio.create_task(hook(wf.workflow_id))


def expire_leases(db: Session) -> int:
    """Called by the sweeper. Returns how many leases were reclaimed."""
    stale = list(db.scalars(select(Job).where(
        Job.state.in_(("leased", "running")), Job.lease_expires_at < utcnow())))
    for job in stale:
        if job.cancel_requested:
            # The operator wanted it gone; don't hand it to a rescuer.
            _settle(db, job, "cancelled",
                    {"error_kind": "CancelledByUser",
                     "error_message": "cancel requested; lease expired"})
        elif job.attempts < job.max_attempts:
            job.state = "queued"
            job.leased_by = None
            job.lease_expires_at = None
            job.error_kind = "LeaseExpired"
            job.error_message = "agent stopped renewing; job re-queued"
            db.commit()
        else:
            _settle(db, job, "failed",
                    {"error_kind": "LeaseExpired",
                     "error_message": "agent died and max_attempts reached"})
    return len(stale)


def mark_offline_agents(db: Session, grace_s: int = 90) -> None:
    db.execute(update(AgentWorker)
               .where(AgentWorker.last_seen_at < utcnow() - timedelta(seconds=grace_s),
                      AgentWorker.state != "offline")
               .values(state="offline", current_job_id=None))
    db.commit()


# ------------------------------------------------------------------- reads

@router.get("/v1/jobs")
async def list_jobs(request: Request, db: Session = Depends(get_db),
                    state: str | None = None, queue: str | None = None,
                    workflow_id: str | None = None, limit: int = Query(100, le=1000)):
    await deps["read_check"](request, db)
    stmt = select(Job).order_by(desc(Job.created_at)).limit(limit)
    if state:
        stmt = stmt.where(Job.state.in_(state.split(",")))
    if queue:
        stmt = stmt.where(Job.queue == queue)
    if workflow_id:
        stmt = stmt.where(Job.workflow_id == workflow_id)
    return {"jobs": [_job_dict(j) for j in db.scalars(stmt)]}


@router.get("/v1/workflows")
async def list_workflows(request: Request, db: Session = Depends(get_db),
                         status: str | None = None, limit: int = Query(50, le=500)):
    await deps["read_check"](request, db)
    stmt = select(Workflow).order_by(desc(Workflow.created_at)).limit(limit)
    if status:
        stmt = stmt.where(Workflow.status.in_(status.split(",")))
    out = []
    for wf in db.scalars(stmt):
        jobs = list(db.scalars(select(Job).where(Job.workflow_id == wf.workflow_id)))
        out.append(_wf_dict(wf, jobs))
    return {"workflows": out}


@router.get("/v1/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request, db: Session = Depends(get_db)):
    await deps["read_check"](request, db)
    wf = db.get(Workflow, workflow_id)
    if not wf:
        raise HTTPException(404, "unknown workflow")
    jobs = list(db.scalars(select(Job).where(Job.workflow_id == workflow_id)
                           .order_by(Job.created_at)))
    return {**_wf_dict(wf, jobs), "jobs": [_job_dict(j) for j in jobs]}


@router.get("/v1/agents")
async def list_agents(request: Request, db: Session = Depends(get_db)):
    await deps["read_check"](request, db)
    return {"agents": [{
        "agent_id": a.agent_id, "hostname": a.hostname, "user": a.user,
        "machine_id": a.machine_id, "queues": a.queues, "labels": a.labels,
        "tools": a.tools, "state": a.state, "current_job_id": a.current_job_id,
        "last_seen_at": a.last_seen_at,
    } for a in db.scalars(select(AgentWorker).order_by(desc(AgentWorker.last_seen_at)))]}


@router.post("/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    await deps["operator_check"](request, db)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if job.state in ("blocked", "queued"):
        _settle(db, job, "cancelled", {"error_kind": "CancelledByUser"})
        return {"state": job.state, "cancel_requested": False}
    if job.state in ("leased", "running"):
        # Cooperative: the agent sees the flag on its next lease renewal
        # (~LEASE_TTL_S/3) and terminates the tool, reporting 'cancelled'.
        job.cancel_requested = True
        db.commit()
        return {"state": job.state, "cancel_requested": True}
    raise HTTPException(409, f"cannot cancel a {job.state} job (already terminal)")


@router.post("/v1/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request, db: Session = Depends(get_db)):
    """Re-queue a terminal (failed/cancelled) job: same job_id, fresh attempts."""
    await deps["operator_check"](request, db)
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if job.state not in ("failed", "cancelled"):
        raise HTTPException(409, f"cannot retry a {job.state} job "
                                 "(only failed or cancelled)")
    if job.workflow_id:
        raise HTTPException(409, "workflow jobs cannot be retried individually — "
                                 "resubmit the workflow")
    job.state = "queued"
    job.attempts = 0
    job.cancel_requested = False
    job.leased_by = None
    job.lease_expires_at = None
    job.run_id = None
    job.exit_code = None
    job.error_kind = None
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    db.commit()
    return {"state": "queued"}


def _job_dict(j: Job) -> dict:
    return {
        "job_id": j.job_id, "workflow_id": j.workflow_id, "step_key": j.step_key,
        "tool": j.tool, "params": j.params, "project": j.project, "tags": j.tags,
        "queue": j.queue, "priority": j.priority, "require_labels": j.require_labels,
        "depends_on": j.depends_on, "state": j.state,
        "attempts": j.attempts, "max_attempts": j.max_attempts,
        "cancel_requested": j.cancel_requested,
        "leased_by": j.leased_by, "run_id": j.run_id, "exit_code": j.exit_code,
        "error_kind": j.error_kind, "error_message": j.error_message,
        "submitted_by": j.submitted_by, "created_at": j.created_at,
        "started_at": j.started_at, "finished_at": j.finished_at,
    }


def _wf_dict(wf: Workflow, jobs: list[Job]) -> dict:
    counts: dict[str, int] = {}
    for j in jobs:
        counts[j.state] = counts.get(j.state, 0) + 1
    return {
        "workflow_id": wf.workflow_id, "name": wf.name, "project": wf.project,
        "status": wf.status, "total_jobs": wf.total_jobs, "job_states": counts,
        "done": counts.get("ok", 0), "submitted_by": wf.submitted_by,
        "plane_issue_id": wf.plane_issue_id,
        "created_at": wf.created_at, "finished_at": wf.finished_at,
    }
