"""
Deterministic environment resolution for the console.

The problem this solves: "the script works on my machine" is really "my
machine happens to have the right GDAL/laspy/torch". So environments are
first-class, named, and shared — a tool declares `environment: pointcloud`
and the console guarantees the same interpreter and packages everywhere,
or fails BEFORE the tool starts with a clear error. Nothing here calls a
model or a network service beyond the package index; resolution is pure
mechanism.

Layout (committed to the toolkit repo, versioned with your tools):

    envs/
      pointcloud/
        env.yaml            # the spec (below)
        requirements.lock   # uv-compiled, hash-pinned — THE source of truth
      ml-torch/
        env.yaml            # kind: pixi
        pixi.toml           # conda-forge deps (pytorch, cuda, gdal, pdal…)
        pixi.lock           # committed

env.yaml:

    kind: uv                # uv | pixi | system
    python: "3.12"          # uv fetches it if the machine lacks it
    deps:                   # EITHER inline deps (locked via `sdtools env lock`)
      - laspy[lazrs]>=2.5
      - numpy
    # OR requirements: requirements.in
    env:                    # extra env vars for tools running in this env
      PROJ_NETWORK: "ON"

Determinism model — two layers, both content-addressed:

  1. The LOCKFILE (requirements.lock / pixi.lock) pins every package with
     hashes. It is generated once (`sdtools env lock`), reviewed, committed.
     Two machines with the same lockfile install byte-identical trees.
  2. The PREFIX CACHE key is sha256(spec + lockfile + backend major). Any
     edit to either produces a new prefix — environments are immutable once
     built, never mutated in place. Cache: ~/.sdtools/envs/<name>-<digest>.

Every run records (env_name, env_digest) in telemetry, so the dashboard can
answer "which environment produced this output".
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import HOME, Config

ENV_CACHE = HOME / "envs"
IS_WINDOWS = os.name == "nt"


def _venv_python(prefix: Path) -> Path:
    """uv venv layout differs per OS: bin/python vs Scripts/python.exe."""
    if IS_WINDOWS:
        return prefix / "Scripts" / "python.exe"
    return prefix / "bin" / "python"


_BUILD_TIMEOUT_S = 1800
_LOCK_WAIT_S = 900          # another process building the same env


class EnvironmentError_(Exception):
    """Raised when an environment cannot be resolved. The runner turns this
    into a failed run with error_kind=EnvResolveFailed before the tool starts."""


@dataclass
class EnvSpec:
    name: str
    kind: str                       # uv | pixi | system
    dir: Path
    python: str | None = None
    deps: list[str] = field(default_factory=list)
    requirements: str | None = None      # a .in file relative to dir
    env: dict[str, str] = field(default_factory=dict)

    @property
    def lockfile(self) -> Path:
        return self.dir / ("pixi.lock" if self.kind == "pixi" else "requirements.lock")

    @property
    def manifest(self) -> Path:
        return self.dir / "pixi.toml"


@dataclass
class ResolvedEnv:
    """What the runner needs: how to launch a process inside the env."""

    name: str
    digest: str                     # "" for system
    python: str                     # interpreter path (sys.executable for system)
    exec_prefix: list[str]          # argv prefix (pixi run needs one)
    env_vars: dict[str, str]
    cached: bool                    # False the time it was built
    build_s: float = 0.0


# ------------------------------------------------------------------ loading

def envs_root(cfg: Config) -> Path:
    """envs/ lives beside tools/ in the toolkit repo (override: SDTOOLS_ENVS_DIR)."""
    override = os.environ.get("SDTOOLS_ENVS_DIR")
    if override:
        return Path(override)
    return cfg.tools_dir.parent / "envs"


def load_spec(cfg: Config, name: str) -> EnvSpec:
    if name == "system":
        return EnvSpec(name="system", kind="system", dir=Path("."))
    path = envs_root(cfg) / name / "env.yaml"
    if not path.exists():
        raise EnvironmentError_(
            f"environment {name!r} not found ({path}); "
            f"known: {', '.join(list_envs(cfg)) or '(none)'}")
    data = yaml.safe_load(path.read_text()) or {}
    kind = data.get("kind", "uv")
    if kind not in ("uv", "pixi", "system"):
        raise EnvironmentError_(f"{name}: unknown kind {kind!r}")
    return EnvSpec(
        name=name, kind=kind, dir=path.parent,
        python=str(data["python"]) if data.get("python") else None,
        deps=[str(d) for d in data.get("deps", [])],
        requirements=data.get("requirements"),
        env={k: str(v) for k, v in (data.get("env") or {}).items()},
    )


def list_envs(cfg: Config) -> list[str]:
    root = envs_root(cfg)
    if not root.exists():
        return []
    return sorted(p.parent.name for p in root.glob("*/env.yaml"))


# ------------------------------------------------------------------- digest

def spec_digest(spec: EnvSpec) -> str:
    """Content address: spec + lockfile + backend major version."""
    h = hashlib.sha256()
    h.update(json.dumps({
        "kind": spec.kind, "python": spec.python,
        "deps": spec.deps, "requirements": spec.requirements,
    }, sort_keys=True).encode())
    if spec.lockfile.exists():
        h.update(spec.lockfile.read_bytes())
    if spec.kind == "pixi" and spec.manifest.exists():
        h.update(spec.manifest.read_bytes())
    h.update(_backend_version(spec.kind).encode())
    return h.hexdigest()[:12]


def _backend_version(kind: str) -> str:
    if kind == "system":
        return "system"
    exe = shutil.which(kind)
    if not exe:
        raise EnvironmentError_(
            f"{kind!r} is not installed on this machine — install it once "
            f"({'https://docs.astral.sh/uv' if kind == 'uv' else 'https://pixi.sh'}) "
            f"and every environment resolves from lockfiles after that")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15)
    # major.minor only: patch releases must not invalidate every cached env
    ver = (out.stdout.strip().split() or ["?"])[-1]
    return f"{kind}-{'.'.join(ver.split('.')[:2])}"


# ------------------------------------------------------------------ locking

def lock(cfg: Config, name: str) -> Path:
    """(Re)generate the lockfile from the spec. Run this on ANY dep change,
    review the diff, commit it. This is the only step that hits the index
    with unpinned versions."""
    spec = load_spec(cfg, name)
    if spec.kind == "system":
        raise EnvironmentError_("the system environment has no lockfile")
    if spec.kind == "pixi":
        _run([shutil.which("pixi"), "lock", "--manifest-path", str(spec.manifest)],
             "pixi lock failed")
        return spec.lockfile

    src = spec.dir / (spec.requirements or "requirements.in")
    if spec.requirements is None:
        src.write_text("".join(d + "\n" for d in spec.deps))
    args = [shutil.which("uv"), "pip", "compile", str(src),
            "-o", str(spec.lockfile), "--generate-hashes", "--quiet"]
    if spec.python:
        args += ["--python-version", spec.python]
    _run(args, "uv pip compile failed")
    return spec.lockfile


# ---------------------------------------------------------------- resolving

def resolve(cfg: Config, name: str) -> ResolvedEnv:
    """Return a ready-to-use environment, building it on first use.
    Deterministic: same spec+lock -> same digest -> same prefix, reused."""
    spec = load_spec(cfg, name)
    if spec.kind == "system":
        # sys.executable: the interpreter sdtools itself runs under — the only
        # python guaranteed to exist on every OS (Windows has no `python3`).
        import sys
        return ResolvedEnv(name="system", digest="", python=sys.executable,
                           exec_prefix=[], env_vars={}, cached=True)

    if not spec.lockfile.exists():
        raise EnvironmentError_(
            f"{name}: no lockfile ({spec.lockfile.name}). Run "
            f"`sdtools env lock {name}` once and commit the result — "
            f"unlocked installs would not be reproducible across machines")

    digest = spec_digest(spec)
    prefix = ENV_CACHE / f"{name}-{digest}"
    marker = prefix / ".sdtools-env-ok"

    if marker.exists():
        return _resolved(spec, digest, prefix, cached=True)

    ENV_CACHE.mkdir(parents=True, exist_ok=True)
    guard = ENV_CACHE / f"{name}-{digest}.building"
    t0 = time.monotonic()
    if not _acquire(guard):
        _wait_for(marker, guard)          # someone else is building it
        return _resolved(spec, digest, prefix, cached=True)

    try:
        if marker.exists():               # built while we raced for the guard
            return _resolved(spec, digest, prefix, cached=True)
        tmp = prefix.with_suffix(".partial")
        shutil.rmtree(tmp, ignore_errors=True)

        if spec.kind == "uv":
            uv = shutil.which("uv")
            venv_args = [uv, "venv", str(tmp), "--quiet"]
            if spec.python:
                venv_args += ["--python", spec.python]
            _run(venv_args, f"{name}: creating venv failed")
            _run([uv, "pip", "sync", str(spec.lockfile), "--quiet",
                  "--python", str(_venv_python(tmp)),
                  "--require-hashes"],
                 f"{name}: uv pip sync from lockfile failed")
        else:  # pixi: the prefix is pixi-managed inside our cache dir
            _run([shutil.which("pixi"), "install",
                  "--manifest-path", str(spec.manifest), "--frozen"],
                 f"{name}: pixi install --frozen failed",
                 env={**os.environ, "PIXI_CACHE_DIR": str(ENV_CACHE / ".pixi-cache")})
            tmp.mkdir(parents=True, exist_ok=True)   # marker home; pixi holds the env

        # Atomic publish: a prefix either fully exists or not at all.
        if prefix.exists():
            shutil.rmtree(prefix, ignore_errors=True)
        tmp.rename(prefix)
        marker.write_text(json.dumps({
            "name": name, "digest": digest, "built_at": time.time(),
            "lockfile_sha": hashlib.sha256(spec.lockfile.read_bytes()).hexdigest(),
        }))
        return _resolved(spec, digest, prefix, cached=False,
                         build_s=time.monotonic() - t0)
    finally:
        guard.unlink(missing_ok=True)


def _resolved(spec: EnvSpec, digest: str, prefix: Path, cached: bool,
              build_s: float = 0.0) -> ResolvedEnv:
    if spec.kind == "uv":
        py = _venv_python(prefix)
        bin_dir = py.parent
        return ResolvedEnv(
            name=spec.name, digest=digest,
            python=str(py), exec_prefix=[],
            env_vars={**spec.env,
                      "VIRTUAL_ENV": str(prefix),
                      "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"},
            cached=cached, build_s=build_s)
    # pixi: launch through `pixi run --frozen` so activation is pixi's problem
    return ResolvedEnv(
        name=spec.name, digest=digest, python="python",
        exec_prefix=[shutil.which("pixi") or "pixi", "run", "--frozen",
                     "--manifest-path", str(spec.manifest), "--"],
        env_vars=dict(spec.env), cached=cached, build_s=build_s)


# ------------------------------------------------------------------- admin

def status(cfg: Config) -> list[dict]:
    out = []
    for name in list_envs(cfg):
        spec = load_spec(cfg, name)
        row = {"name": name, "kind": spec.kind, "python": spec.python,
               "locked": spec.lockfile.exists(), "digest": None, "cached": False}
        if row["locked"]:
            try:
                d = spec_digest(spec)
                row["digest"] = d
                row["cached"] = (ENV_CACHE / f"{name}-{d}" / ".sdtools-env-ok").exists()
            except EnvironmentError_ as exc:
                row["error"] = str(exc)
        out.append(row)
    return out


def prune(cfg: Config) -> int:
    """Delete cached prefixes whose digest no longer matches any current spec."""
    keep = set()
    for name in list_envs(cfg):
        spec = load_spec(cfg, name)
        if spec.kind != "system" and spec.lockfile.exists():
            try:
                keep.add(f"{name}-{spec_digest(spec)}")
            except EnvironmentError_:
                pass
    removed = 0
    if ENV_CACHE.exists():
        for p in ENV_CACHE.iterdir():
            if p.is_dir() and p.name not in keep and not p.name.startswith("."):
                shutil.rmtree(p, ignore_errors=True)
                removed += 1
    return removed


# ----------------------------------------------------------------- plumbing

def _run(args: list, msg: str, env: dict | None = None) -> None:
    try:
        r = subprocess.run(args, capture_output=True, text=True,
                           timeout=_BUILD_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired as exc:
        raise EnvironmentError_(f"{msg}: timed out after {_BUILD_TIMEOUT_S}s") from exc
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()[-800:]
        raise EnvironmentError_(f"{msg}:\n{detail}")


def _acquire(guard: Path) -> bool:
    try:
        fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # Stale guard (builder crashed)? Steal it after 30 min of no progress.
        try:
            if time.time() - guard.stat().st_mtime > 1800:
                guard.unlink(missing_ok=True)
                return _acquire(guard)
        except FileNotFoundError:
            return _acquire(guard)
        return False


def _wait_for(marker: Path, guard: Path) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < _LOCK_WAIT_S:
        if marker.exists():
            return
        if not guard.exists() and not marker.exists():
            raise EnvironmentError_(
                "concurrent environment build failed in the other process")
        time.sleep(1)
    raise EnvironmentError_(f"timed out waiting for concurrent build ({marker})")
