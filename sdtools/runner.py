"""
Run a tool, capture everything, report it.

The runner is deliberately dumb about *what* the tool does. It owns:
  - process lifecycle (spawn, timeout, cancel, exit code)
  - line capture from stdout + stderr, interleaved with a monotonic seq
  - sentinel-line parsing into metrics / progress / artifacts / errors
  - a heartbeat so the dashboard can tell "running" from "died silently"
  - the terminal status and a log digest for summary caching
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import environments, protocol
from .config import CLI_VERSION, Config, actor, toolkit_sha
from .discovery import Tool
from .models import RunStatus
from .telemetry import Telemetry, now

# Timestamps and durations vary between identical reruns; strip them so the
# summary cache actually hits.
_NOISE = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|seconds|min|mins)\b"),
    re.compile(r"0x[0-9a-fA-F]{6,}"),
]
_MAX_KEPT_LINES = 20_000     # hard cap on in-memory log


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    exit_code: int | None
    duration_ms: int
    metrics: dict[str, Any] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    error_kind: str | None = None
    error_message: str | None = None
    log_digest: str | None = None
    log_lines: list[str] = field(default_factory=list)
    spooled: int = 0


def new_run_id() -> str:
    return str(uuid.uuid4())


class _CancelRequested(Exception):
    """Raised out of _wait when the caller's cancel_event is set."""


def _wait(proc: subprocess.Popen, timeout_s: float,
          cancel_event: "threading.Event | None") -> int:
    if cancel_event is None:
        return proc.wait(timeout=timeout_s)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if cancel_event.is_set():
                raise _CancelRequested() from None
            if time.monotonic() >= deadline:
                raise


def _kill_tree(proc: subprocess.Popen) -> None:
    """proc.kill() orphans grandchildren (a tool's own subprocesses keep
    running); on Windows taskkill /T takes the whole tree down."""
    if os.name == "nt":
        subprocess.call(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.kill()


def execute(
    tool: Tool,
    values: dict[str, Any],
    cfg: Config,
    *,
    project: str | None = None,
    tags: list[str] | None = None,
    parent_run_id: str | None = None,
    on_line: Callable[[str, str, str | None], None] | None = None,
    dry_run: bool = False,
    run_id: str | None = None,   # dispatcher supplies one so job<->run link is atomic
    cancel_event: threading.Event | None = None,  # set -> terminate tool, status=cancelled
) -> RunResult:
    run_id = run_id or new_run_id()

    # Resolve the tool's environment BEFORE anything runs. First use of a new
    # lockfile builds the prefix (minutes); every run after reuses it (ms).
    # A resolution failure is a recorded, attributable run — not a stack trace.
    env_error: str | None = None
    try:
        renv = environments.resolve(cfg, tool.environment)
    except environments.EnvironmentError_ as exc:
        env_error = str(exc)
        import sys as _sys
        renv = environments.ResolvedEnv(
            name=tool.environment, digest="", python=_sys.executable,
            exec_prefix=[], env_vars={}, cached=False)

    argv = renv.exec_prefix + tool.build_argv(values, python=renv.python)

    if dry_run:
        print(" ".join(argv))
        return RunResult(run_id, RunStatus.OK, 0, 0)

    tel = Telemetry(cfg, run_id)
    started = now()
    t0 = time.monotonic()

    tel.start({
        "run_id": run_id,
        "tool": tool.name,
        "tool_version": tool.version,
        "toolkit_sha": toolkit_sha(),
        "cli_version": CLI_VERSION,
        "cmdline": tool.redacted_cmdline(values),
        "params": {k: v for k, v in values.items()
                   if k not in {p.name for p in tool.params if p.secret}},
        "project": project or cfg.project,
        "tags": (tags or []) + tool.tags,
        "cwd": os.getcwd(),
        "actor": actor().model_dump(),
        "started_at": started,
        "input_summary": _summarize_inputs(tool, values),
        "parent_run_id": parent_run_id,
        "env_name": tool.environment,
        "env_digest": renv.digest or None,
    })

    if env_error:
        # Fail the run cleanly before the tool starts: visible, summarizable.
        tel.event({"seq": 1, "ts": now(), "level": "error", "stream": "cli",
                   "message": f"environment resolution failed: {env_error[:4000]}",
                   "fields": None})
        duration_ms = int((time.monotonic() - t0) * 1000)
        tel.finish({
            "status": RunStatus.FAILED.value, "exit_code": None,
            "finished_at": now(), "duration_ms": duration_ms, "metrics": {},
            "output_summary": {}, "counts": {"error": 1},
            "error_kind": "EnvResolveFailed", "error_message": env_error[:2000],
            "log_digest": None,
        })
        spooled = tel.close()
        if on_line:
            on_line(f"[error] environment {tool.environment!r}: {env_error}",
                    "error", None)
        return RunResult(
            run_id=run_id, status=RunStatus.FAILED, exit_code=None,
            duration_ms=duration_ms, error_kind="EnvResolveFailed",
            error_message=env_error, spooled=spooled)

    if not renv.cached:
        tel.event({"seq": 0, "ts": now(), "level": "info", "stream": "cli",
                   "message": f"built environment {renv.name} ({renv.digest}) "
                              f"in {renv.build_s:.1f}s", "fields": None})

    seq = 0
    lock = threading.Lock()
    kept: list[str] = []
    level_counts: Counter[str] = Counter()
    metrics: dict[str, Any] = {}
    extra_fields: dict[str, Any] = {}
    artifacts: list[dict] = []
    tool_error: dict[str, str] = {}
    progress_state: dict[str, Any] = {"value": None, "note": None}

    def handle(raw: str, stream: str) -> None:
        nonlocal seq
        line = raw.rstrip("\n")
        kind, payload = protocol.parse_line(line)

        if kind == "metric":
            metrics[payload["name"]] = payload.get("value")
        elif kind == "progress":
            progress_state.update(value=payload.get("value"), note=payload.get("note"))
        elif kind == "artifact":
            artifacts.append({k: v for k, v in payload.items() if k != "type"})
        elif kind == "error":
            tool_error.update(kind=payload.get("kind", "ToolError"),
                              message=payload.get("message", ""))
        elif kind == "field":
            extra_fields[payload["key"]] = payload.get("value")

        level = "info" if kind else protocol.infer_level(line, stream)
        level_counts[level] += 1

        with lock:
            seq += 1
            my_seq = seq
        if len(kept) < _MAX_KEPT_LINES:
            kept.append(f"[{level}] {line}" if level in ("error", "warning") else line)

        tel.event({
            "seq": my_seq, "ts": now(), "level": level,
            "stream": "tool" if kind else stream,
            "message": line[:8000],
            "fields": {k: v for k, v in (payload or {}).items() if k != "type"} or None,
        })
        if on_line:
            # Echoing to the operator's console is cosmetic — a failure there
            # (e.g. an un-encodable character on an exotic terminal) must not
            # kill the pump thread: a dead pump closes the pipe and takes the
            # CHILD down with EINVAL on its next print.
            try:
                on_line(line, level, kind)
            except Exception:
                pass

    env = {**os.environ, **renv.env_vars, **tool.env,
           "SDTOOLS_RUN_ID": run_id, "PYTHONUNBUFFERED": "1",
           # children print UTF-8 (stage banners, checkmarks, non-ASCII paths);
           # without this a python child on Windows writes the locale codepage
           "PYTHONIOENCODING": "utf-8"}

    # Decode as UTF-8 to match; errors="replace" so one stray byte from a
    # non-UTF-8 child can never kill the pump thread (a dead pump closes the
    # pipe, which then kills the CHILD with EINVAL on its next print).
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1, env=env, cwd=os.getcwd(),
    )

    stop = threading.Event()

    def pump(pipe, stream_name: str) -> None:
        try:
            for raw in pipe:
                handle(raw, stream_name)
        finally:
            pipe.close()

    def beat() -> None:
        while not stop.wait(cfg.heartbeat_s):
            tel.heartbeat(progress_state["value"], progress_state["note"])

    threads = [
        threading.Thread(target=pump, args=(proc.stdout, "stdout"), daemon=True),
        threading.Thread(target=pump, args=(proc.stderr, "stderr"), daemon=True),
        threading.Thread(target=beat, daemon=True),
    ]
    for t in threads:
        t.start()

    status = RunStatus.OK
    try:
        exit_code = _wait(proc, tool.timeout_s, cancel_event)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        exit_code = proc.wait()
        status = RunStatus.TIMEOUT
    except (KeyboardInterrupt, _CancelRequested):
        if os.name == "nt":
            # terminate() on Windows is already a hard kill of just the
            # direct child — take the whole tree instead.
            _kill_tree(proc)
            exit_code = proc.wait()
        else:
            proc.terminate()
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _kill_tree(proc)
                exit_code = proc.wait()
        status = RunStatus.CANCELLED

    stop.set()
    for t in threads[:2]:
        t.join(timeout=10)

    if status is RunStatus.OK and exit_code != 0:
        status = RunStatus.FAILED

    digest = _log_digest(kept)
    err_kind = tool_error.get("kind")
    err_msg = tool_error.get("message")
    if status is not RunStatus.OK and not err_kind:
        err_kind = {RunStatus.TIMEOUT: "Timeout", RunStatus.CANCELLED: "Cancelled"}.get(
            status, f"ExitCode{exit_code}")
        err_msg = err_msg or _last_error_line(kept)

    duration_ms = int((time.monotonic() - t0) * 1000)
    tel.artifacts(artifacts)
    tel.finish({
        "status": status.value,
        "exit_code": exit_code,
        "finished_at": now(),
        "duration_ms": duration_ms,
        "metrics": metrics,
        "output_summary": {"artifact_count": len(artifacts), **extra_fields},
        "counts": dict(level_counts),
        "error_kind": err_kind,
        "error_message": (err_msg or None) and err_msg[:2000],
        "log_digest": digest,
    })
    spooled = tel.close()

    return RunResult(
        run_id=run_id, status=status, exit_code=exit_code, duration_ms=duration_ms,
        metrics=metrics, fields=extra_fields, artifacts=artifacts,
        counts=dict(level_counts), error_kind=err_kind, error_message=err_msg,
        log_digest=digest, log_lines=kept, spooled=spooled,
    )


def _summarize_inputs(tool: Tool, values: dict[str, Any]) -> dict[str, Any]:
    """Cheap, no file reads beyond stat(). Gives the dashboard job size."""
    paths: list[Path] = []
    for p in tool.params:
        if p.type != "path":
            continue
        v = values.get(p.name)
        for item in (v if isinstance(v, (list, tuple)) else [v] if v else []):
            path = Path(str(item))
            if path.is_dir():
                paths += [f for f in path.rglob("*") if f.is_file()]
            elif path.exists():
                paths.append(path)
    if not paths:
        return {}
    # Best-effort only — files can vanish between rglob and stat (sync
    # placeholders, transient #chkpt_file# artifacts). A telemetry summary
    # must never kill the run.
    total = 0
    exts: Counter = Counter()
    counted = 0
    for f in paths:
        try:
            total += f.stat().st_size
        except OSError:
            continue
        counted += 1
        exts[f.suffix.lower()] += 1
    if not counted:
        return {}
    return {"file_count": counted, "total_bytes": total, "by_ext": dict(exts)}


def _log_digest(lines: list[str]) -> str:
    norm = []
    for line in lines:
        for pat in _NOISE:
            line = pat.sub("<t>", line)
        norm.append(line)
    return hashlib.sha256("\n".join(norm).encode()).hexdigest()


def _last_error_line(lines: list[str]) -> str | None:
    for line in reversed(lines[-400:]):
        if line.startswith("[error]"):
            return line[len("[error] "):]
    return lines[-1] if lines else None
