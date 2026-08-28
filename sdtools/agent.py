"""
The worker daemon: `sdtools agent --queue default --labels pdal,bigmem`

One process = one execution slot. For a machine that should run two jobs at
once, start two agents with different --name values — process isolation
beats in-process concurrency for tools that are themselves multi-core.

Loop: poll (heartbeat + claim in one call) -> execute through the normal
runner (so ALL the telemetry behaves exactly as an interactive run: local
NDJSON first, batched upload, spool on failure) -> report finish. A lease
renewal thread runs alongside the tool; if this process dies, renewal stops,
the server expires the lease, and the job is retried elsewhere.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import typer

from .config import Config, machine_id
from .discovery import Tool, discover
from .runner import execute, new_run_id


class StagingError(Exception):
    pass


def _copy_tree(src: Path, dst: Path) -> None:
    """Mirror-free additive copy; robocopy on Windows (exit >= 8 is failure)."""
    dst.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        if src.is_file():
            rc = subprocess.call(["robocopy", str(src.parent), str(dst), src.name,
                                  "/NFL", "/NDL", "/NP", "/NJH", "/NJS",
                                  "/R:2", "/W:5"])
        else:
            rc = subprocess.call(["robocopy", str(src), str(dst), "/E",
                                  "/NFL", "/NDL", "/NP", "/NJH", "/NJS",
                                  "/R:2", "/W:5"])
        if rc >= 8:
            raise StagingError(f"robocopy {src} -> {dst} failed (exit {rc})")
    else:
        if src.is_file():
            shutil.copy2(src, dst / src.name)
        else:
            shutil.copytree(src, dst, dirs_exist_ok=True)


def _stage_in(staging: dict, scratch: Path, values: dict) -> Path:
    """Copy each 'in' source under scratch, point its tool param at the copy.
    Returns the first staged local path (stage-out RELs resolve against it)."""
    first: Path | None = None
    for item in staging.get("in", []):
        src = Path(item["src"])
        if not src.exists():
            raise StagingError(f"stage-in source not reachable from this "
                               f"worker: {src}")
        local = scratch / (src.name or "data")
        typer.secho(f"  stage-in  {src} -> {local}", fg="cyan")
        _copy_tree(src, local)
        values[item["param"]] = str(local if src.is_dir() else local / src.name)
        first = first or Path(values[item["param"]])
    if first is None:
        raise StagingError("staging spec has no 'in' entries")
    return first


def _stage_out(staging: dict, staged_root: Path) -> None:
    for item in staging.get("out", []):
        src = (staged_root / item["rel"]).resolve() if item["rel"] != "." \
            else staged_root
        if not src.exists():
            raise StagingError(f"stage-out path missing after run: {src}")
        typer.secho(f"  stage-out {src} -> {item['dest']}", fg="cyan")
        _copy_tree(src, Path(item["dest"]))


def _coerce(tool: Tool, params: dict) -> dict:
    """JSON/YAML params -> the types the manifest declares."""
    spec = {p.name: p for p in tool.params}

    def one(p, v):
        if p.type == "int":
            return int(v)
        if p.type == "float":
            return float(v)
        if p.type == "bool":
            return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
        return str(v)

    out = {}
    for k, v in params.items():
        p = spec.get(k)
        if p is None:
            out[k] = v
            continue
        if p.multiple:
            items = v if isinstance(v, (list, tuple)) else [v]
            out[k] = [one(p, i) for i in items]
        else:
            out[k] = one(p, v)
    return out


class Agent:
    def __init__(self, cfg: Config, name: str, queues: list[str], labels: list[str],
                 poll_s: float, max_jobs: int | None, idle_exit_s: float | None):
        if not cfg.telemetry_enabled:
            raise typer.BadParameter("agent mode needs api.url and api.key configured")
        self.cfg = cfg
        self.agent_id = f"{machine_id()}:{name}"
        self.queues, self.labels = queues, labels
        self.poll_s, self.max_jobs, self.idle_exit_s = poll_s, max_jobs, idle_exit_s
        self.tools = {t.name: t for t in discover(cfg.tools_dir)[0]}
        self.client = httpx.Client(
            base_url=cfg.api_url.rstrip("/"), timeout=15,
            headers={"Authorization": f"Bearer {cfg.api_key}"})
        self.stop = threading.Event()

    # ------------------------------------------------------------------ api

    def _poll(self) -> dict | None:
        r = self.client.post("/v1/agents/poll", json={
            "agent_id": self.agent_id, "machine_id": machine_id(),
            "hostname": socket.gethostname(), "user": self.cfg.user,
            "queues": self.queues, "labels": self.labels,
            "tools": sorted(self.tools),
        })
        r.raise_for_status()
        return r.json().get("job")

    def _job(self, job_id: str, action: str, body: dict) -> bool:
        r = self.client.post(f"/v1/jobs/{job_id}/{action}",
                             json={"agent_id": self.agent_id, **body})
        if r.status_code == 409:
            # Lease moved on (we were presumed dead). Stop touching this job.
            typer.secho(f"  lease lost on {job_id[:8]} ({r.text[:80]})", fg="yellow")
            return False
        r.raise_for_status()
        return True

    def _renew_lease(self, job_id: str) -> tuple[bool, bool]:
        """(lease still ours, cancel requested by an operator)."""
        r = self.client.post(f"/v1/jobs/{job_id}/lease",
                             json={"agent_id": self.agent_id})
        if r.status_code == 409:
            typer.secho(f"  lease lost on {job_id[:8]} ({r.text[:80]})", fg="yellow")
            return False, False
        r.raise_for_status()
        return True, bool(r.json().get("cancel_requested"))

    # ----------------------------------------------------------------- loop

    def run(self) -> int:
        typer.secho(f"agent {self.agent_id} on {socket.gethostname()} — "
                    f"queues={self.queues} labels={self.labels} "
                    f"tools={sorted(self.tools)}", bold=True)
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())

        done = 0
        idle_since = time.monotonic()
        while not self.stop.is_set():
            try:
                job = self._poll()
            except httpx.HTTPError as exc:
                typer.secho(f"  poll failed ({exc}); retrying", fg="yellow")
                time.sleep(min(self.poll_s * 4, 30))
                continue

            if not job:
                if self.idle_exit_s and time.monotonic() - idle_since > self.idle_exit_s:
                    typer.echo("idle limit reached, exiting")
                    break
                self.stop.wait(self.poll_s)
                continue

            idle_since = time.monotonic()
            self._execute(job)
            done += 1
            if self.max_jobs and done >= self.max_jobs:
                typer.echo(f"max jobs ({self.max_jobs}) reached, exiting")
                break
        return done

    def _execute(self, job: dict) -> None:
        job_id = job["job_id"]
        label = job.get("step_key") or job["tool"]
        typer.secho(f"▸ claimed {label} ({job_id[:8]}) "
                    f"attempt {job['attempt']}/{job['max_attempts']}", fg="blue")

        tool = self.tools.get(job["tool"])
        if tool is None:
            self._job(job_id, "finish", {
                "status": "failed", "error_kind": "ToolNotInstalled",
                "error_message": f"{job['tool']!r} not in this machine's tools dir"})
            return

        run_id = new_run_id()
        if not self._job(job_id, "start", {"run_id": run_id}):
            return

        # Renew the lease while the tool runs. The renewal doubles as the
        # cancel channel: stop the tool if an operator requested cancel, or
        # if the lease moved on (we were presumed dead and a rescuer owns
        # the job now — keeping our copy running would duplicate its work).
        renew_stop = threading.Event()
        cancel_event = threading.Event()
        ttl = float(job.get("lease_ttl_s", 120))

        def renew() -> None:
            while not renew_stop.wait(max(ttl / 3, 2)):
                try:
                    ours, cancel = self._renew_lease(job_id)
                except httpx.HTTPError:
                    continue          # transient API outage: keep trying
                if not ours:
                    renew_stop.set()
                    cancel_event.set()
                elif cancel:
                    typer.secho(f"  cancel requested on {job_id[:8]} — "
                                f"stopping the tool", fg="yellow")
                    cancel_event.set()

        renewer = threading.Thread(target=renew, daemon=True)
        renewer.start()

        params = dict(job.get("params") or {})
        staging = params.pop("_staging", None)
        scratch = (self.cfg.staging_dir / job_id[:8]) if (
            staging and self.cfg.staging_dir) else None

        try:
            values = _coerce(tool, params)
            if staging:
                if scratch is None:
                    raise StagingError(
                        "job carries staging but this worker has no "
                        "[staging] dir in ~/.sdtools/config.toml")
                staged_root = _stage_in(staging, scratch, values)
            if cancel_event.is_set():
                raise KeyboardInterrupt
            result = execute(
                tool, values, self.cfg,
                project=job.get("project"),
                tags=(job.get("tags") or []) + [f"job:{job_id[:8]}",
                                                f"agent:{self.agent_id}"],
                run_id=run_id,
                cancel_event=cancel_event,
            )
            status, exit_code = result.status.value, result.exit_code
            err_kind, err_msg = result.error_kind, result.error_message
            if staging and status == "ok" and not cancel_event.is_set():
                _stage_out(staging, staged_root)
                if not staging.get("keep"):
                    typer.secho(f"  stage-clean {scratch}", fg="cyan")
                    shutil.rmtree(scratch, ignore_errors=True)
        except StagingError as exc:
            status, exit_code = "failed", None
            err_kind, err_msg = "StagingFailed", str(exc)[:500]
        except KeyboardInterrupt:
            status, exit_code = "cancelled", None
            err_kind, err_msg = "Cancelled", "cancelled before the tool started"
        except Exception as exc:  # noqa: BLE001 — report, don't die
            status, exit_code = "failed", None
            err_kind, err_msg = "AgentException", str(exc)[:500]
        finally:
            renew_stop.set()
            renewer.join(timeout=5)

        self._job(job_id, "finish", {
            "status": status, "exit_code": exit_code,
            "error_kind": err_kind, "error_message": err_msg})
        colour = "green" if status == "ok" else "red"
        typer.secho(f"✓ {label} ({job_id[:8]}) -> {status}", fg=colour)


def run_agent(cfg: Config, name: str, queues: list[str], labels: list[str],
              poll_s: float, max_jobs: int | None, idle_exit_s: float | None) -> int:
    return Agent(cfg, name, queues, labels, poll_s, max_jobs, idle_exit_s).run()
