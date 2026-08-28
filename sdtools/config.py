"""Config + machine identity. Everything lives under ~/.sdtools/."""

from __future__ import annotations

import hashlib
import os
import platform
import socket
import subprocess
import tomllib
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .models import Actor

HOME = Path(os.environ.get("SDTOOLS_HOME", Path.home() / ".sdtools"))
CONFIG_PATH = HOME / "config.toml"
SPOOL_DIR = HOME / "spool"        # failed uploads park here
RUNS_DIR = HOME / "runs"          # local NDJSON log, always written first
CLI_VERSION = "0.4.1"   # bump per handover so `sdtools version` proves which code is live


@dataclass
class Config:
    api_url: str | None
    api_key: str | None
    tools_dir: Path
    project: str | None
    user: str
    email: str | None
    offline: bool
    upload_timeout_s: float
    batch_lines: int
    batch_interval_s: float
    heartbeat_s: float
    log_retention_days: int
    staging_dir: Path | None    # worker-local scratch for staged job data

    @property
    def telemetry_enabled(self) -> bool:
        return bool(self.api_url and self.api_key and not self.offline)


def _read_layer(path: Path) -> dict:
    if not path.exists():
        return {}
    # utf-8-sig: tolerate the BOM Windows editors/PowerShell prepend
    return tomllib.loads(path.read_text(encoding="utf-8-sig"))


@lru_cache(maxsize=1)
def load_config() -> Config:
    """Precedence: env var > ~/.sdtools/config.toml > <toolkit>/config.toml > default.

    The toolkit-level config lives beside tools/ (same anchor as envs/):
    C:\\sdtools\\config.toml on a standard install. The installer payload
    ships it so every machine comes pre-pointed at the dispatcher (api.url,
    project, telemetry tuning). The per-machine ~/.sdtools/config.toml
    overrides it — and is where the machine's api key belongs; never ship
    keys in the shared payload. tools.dir itself is the anchor, so it can
    only come from env / machine config / the packaged default.
    """
    machine = _read_layer(CONFIG_PATH)

    def env(key: str, default=None):
        return os.environ.get(key, default)

    default_tools = Path(__file__).resolve().parent.parent / "tools"
    tools_dir = Path(env("SDTOOLS_TOOLS_DIR",
                         machine.get("tools", {}).get("dir", default_tools))
                     ).expanduser()

    raw: dict = _read_layer(tools_dir.parent / "config.toml")   # fleet layer
    for key, value in machine.items():                          # machine wins
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            raw[key].update(value)
        else:
            raw[key] = value

    api = raw.get("api", {})
    tel = raw.get("telemetry", {})

    return Config(
        api_url=env("SDTOOLS_API_URL", api.get("url")),
        api_key=env("SDTOOLS_API_KEY", api.get("key")),
        tools_dir=tools_dir,
        project=env("SDTOOLS_PROJECT", raw.get("project")),
        user=env("SDTOOLS_USER", raw.get("user") or os.environ.get("USER") or "unknown"),
        email=env("SDTOOLS_EMAIL", raw.get("email")),
        offline=env("SDTOOLS_OFFLINE", "") not in ("", "0", "false"),
        upload_timeout_s=float(tel.get("upload_timeout_s", 5.0)),
        batch_lines=int(tel.get("batch_lines", 256)),
        batch_interval_s=float(tel.get("batch_interval_s", 2.0)),
        heartbeat_s=float(tel.get("heartbeat_s", 15.0)),
        log_retention_days=int(tel.get("log_retention_days", 30)),
        staging_dir=(Path(p).expanduser() if (p := env(
            "SDTOOLS_STAGING_DIR", raw.get("staging", {}).get("dir"))) else None),
    )


@lru_cache(maxsize=1)
def machine_id() -> str:
    """Stable per-machine id. Survives reboots, not a hardware serial."""
    seed = f"{socket.gethostname()}:{uuid.getnode()}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


@lru_cache(maxsize=1)
def actor() -> Actor:
    cfg = load_config()
    return Actor(
        user=cfg.user,
        email=cfg.email,
        machine_id=machine_id(),
        hostname=socket.gethostname(),
        platform=platform.platform(),
    )


@lru_cache(maxsize=1)
def toolkit_sha() -> str | None:
    """Git sha of the tools/ dir, so a dashboard row is reproducible."""
    cfg = load_config()
    try:
        out = subprocess.run(
            ["git", "-C", str(cfg.tools_dir), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:
        return None


def ensure_dirs() -> None:
    for d in (HOME, SPOOL_DIR, RUNS_DIR):
        d.mkdir(parents=True, exist_ok=True)
