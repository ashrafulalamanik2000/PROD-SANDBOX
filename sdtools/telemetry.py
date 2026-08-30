"""
Telemetry transport.

Design rules, in priority order:

1. A run must never fail because the API is down. Local NDJSON is written
   first and is the source of truth; upload is best-effort.
2. Uploads never block the tool. A background worker drains a queue.
3. Nothing is lost. Failed payloads are spooled to disk and replayed by the
   next invocation (or `sdtools flush`).
4. Ordering is recoverable server-side from (run_id, seq), so out-of-order
   or duplicate delivery is fine. Ingest is idempotent.
"""

from __future__ import annotations

import gzip
import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import RUNS_DIR, SPOOL_DIR, Config, ensure_dirs

_MAX_SPOOL_FILES = 2000
_MAX_REPLAY_PER_RUN = 200


def now() -> datetime:
    return datetime.now(timezone.utc)


def _dump(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"))


class Telemetry:
    """One instance per run."""

    def __init__(self, cfg: Config, run_id: str):
        ensure_dirs()
        self.cfg = cfg
        self.run_id = run_id
        self.local_path = RUNS_DIR / f"{run_id}.ndjson"
        self._local = self.local_path.open("a", encoding="utf-8")
        self._q: queue.Queue[tuple[str, str, Any] | None] = queue.Queue(maxsize=10_000)
        self._buf: list[dict] = []
        self._last_flush = time.monotonic()
        self._client: httpx.Client | None = None
        self._dropped = 0
        self._worker: threading.Thread | None = None

        if cfg.telemetry_enabled:
            self._client = httpx.Client(
                base_url=cfg.api_url.rstrip("/"),
                timeout=cfg.upload_timeout_s,
                headers={
                    "Authorization": f"Bearer {cfg.api_key}",
                    "Content-Encoding": "gzip",
                    "Content-Type": "application/json",
                    "X-Sdtools-Run": run_id,
                },
            )
            self._worker = threading.Thread(target=self._drain, daemon=True)
            self._worker.start()

    # ---------- public API ----------

    def start(self, payload: dict) -> None:
        self._write_local("run.start", payload)
        self._enqueue("POST", "/v1/runs", payload)

    def event(self, payload: dict) -> None:
        self._write_local("event", payload)
        if not self._client:
            return
        self._buf.append(payload)
        due = (
            len(self._buf) >= self.cfg.batch_lines
            or (time.monotonic() - self._last_flush) >= self.cfg.batch_interval_s
        )
        if due:
            self.flush_events()

    def flush_events(self) -> None:
        if not self._buf:
            return
        batch = {"run_id": self.run_id, "events": self._buf}
        self._buf = []
        self._last_flush = time.monotonic()
        self._enqueue("POST", f"/v1/runs/{self.run_id}/events", batch)

    def heartbeat(self, progress: float | None = None, note: str | None = None) -> None:
        payload = {"ts": now(), "progress": progress, "note": note}
        self._enqueue("POST", f"/v1/runs/{self.run_id}/heartbeat", payload)

    def artifacts(self, items: list[dict]) -> None:
        if not items:
            return
        self._write_local("artifacts", {"items": items})
        self._enqueue("POST", f"/v1/runs/{self.run_id}/artifacts", {"artifacts": items})

    def finish(self, payload: dict) -> None:
        self.flush_events()
        self._write_local("run.finish", payload)
        self._enqueue("PATCH", f"/v1/runs/{self.run_id}", payload)

    def close(self, grace_s: float = 8.0) -> int:
        """Returns count of payloads that had to be spooled."""
        self.flush_events()
        self._local.close()
        if self._worker:
            self._q.put(None)
            self._worker.join(timeout=grace_s)
        if self._client:
            self._client.close()
        return self._dropped

    # ---------- internals ----------

    def _write_local(self, kind: str, payload: dict) -> None:
        self._local.write(_dump({"kind": kind, "run_id": self.run_id, "data": payload}) + "\n")
        self._local.flush()

    def _enqueue(self, method: str, path: str, payload: Any) -> None:
        if not self._client:
            return
        try:
            self._q.put_nowait((method, path, payload))
        except queue.Full:
            # Backpressure: spool rather than block the tool.
            _spool(method, path, payload)
            self._dropped += 1

    def _drain(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            method, path, payload = item
            if not self._send(method, path, payload):
                _spool(method, path, payload)
                self._dropped += 1

    def _send(self, method: str, path: str, payload: Any, attempts: int = 3) -> bool:
        body = gzip.compress(_dump(payload).encode())
        for i in range(attempts):
            try:
                r = self._client.request(method, path, content=body)
                if r.status_code < 300:
                    return True
                if 400 <= r.status_code < 500 and r.status_code not in (408, 429):
                    return False       # our bug or bad key -- retrying won't help
            except httpx.HTTPError:
                pass
            time.sleep(0.4 * (2 ** i))
        return False


# ---------- spool: durable at-least-once delivery ----------

def _spool(method: str, path: str, payload: Any) -> None:
    ensure_dirs()
    existing = sorted(SPOOL_DIR.glob("*.json.gz"))
    if len(existing) >= _MAX_SPOOL_FILES:
        for stale in existing[: len(existing) - _MAX_SPOOL_FILES + 1]:
            stale.unlink(missing_ok=True)
    name = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.json.gz"
    envelope = {"method": method, "path": path, "payload": payload}
    (SPOOL_DIR / name).write_bytes(gzip.compress(_dump(envelope).encode()))


def flush_spool(cfg: Config, limit: int = _MAX_REPLAY_PER_RUN) -> tuple[int, int, int]:
    """Replay spooled payloads. Returns (sent, dropped, remaining)."""
    if not cfg.telemetry_enabled or not SPOOL_DIR.exists():
        return 0, 0, 0
    files = sorted(SPOOL_DIR.glob("*.json.gz"))[:limit]
    if not files:
        return 0, 0, 0

    sent = 0
    dropped = 0
    with httpx.Client(
        base_url=cfg.api_url.rstrip("/"),
        timeout=cfg.upload_timeout_s,
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Encoding": "gzip",
            "Content-Type": "application/json",
        },
    ) as client:
        for f in files:
            try:
                env = json.loads(gzip.decompress(f.read_bytes()))
                r = client.request(
                    env["method"], env["path"],
                    content=gzip.compress(_dump(env["payload"]).encode()),
                )
                if r.status_code < 300:
                    f.unlink(missing_ok=True)
                    sent += 1
                elif r.status_code in (408, 429):
                    break                        # throttled/timed out: try later
                elif r.status_code == 404:
                    # dependency order: an event for a run whose start hasn't
                    # landed yet — keep the file, move on to later items
                    continue
                elif 400 <= r.status_code < 500:
                    f.unlink(missing_ok=True)   # permanently rejected (bad key/scope/payload)
                    dropped += 1
                else:
                    break                        # server unhealthy, try later
            except Exception:                     # noqa: BLE001
                break
    return sent, dropped, len(list(SPOOL_DIR.glob("*.json.gz")))


def prune_local_logs(days: int) -> int:
    cutoff = time.time() - days * 86400
    removed = 0
    for p in RUNS_DIR.glob("*.ndjson"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
            removed += 1
    return removed


def local_log_path(run_id: str) -> Path:
    return RUNS_DIR / f"{run_id}.ndjson"
