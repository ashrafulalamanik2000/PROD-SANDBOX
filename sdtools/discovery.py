"""
Plugin discovery: any directory under tools/ containing a tool.yaml becomes
a subcommand. No CLI code changes needed to add a tool.

    tools/
      las_info/
        tool.yaml
        run.py

Manifest reference is in ARCHITECTURE.md.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PARAM_TYPES = {"str": str, "int": int, "float": float, "bool": bool, "path": str}

# Options the CLI adds to every tool command; a tool param cannot use these
# names (it can still FEED a child flag of that name via `flag:`).
RESERVED_PARAMS = {"project", "tag", "dry_run", "quiet", "offline", "json_out"}


class ManifestError(Exception):
    pass


@dataclass
class Param:
    name: str
    type: str = "str"
    required: bool = False
    default: Any = None
    help: str = ""
    multiple: bool = False
    choices: list[str] = field(default_factory=list)
    secret: bool = False          # redacted from cmdline before upload
    positional: bool = False      # child takes the value with NO flag, in
                                  # manifest order (argparse positionals)
    flag: str | None = None       # child-process flag when it differs from
                                  # --{name} (e.g. name: tag_arg, flag: --tag)

    @property
    def py_type(self):
        base = PARAM_TYPES.get(self.type)
        if base is None:
            raise ManifestError(f"unknown param type {self.type!r} for {self.name!r}")
        return list[base] if self.multiple else base


@dataclass
class Tool:
    name: str                     # command name, e.g. "las-info"
    version: str
    summary: str
    dir: Path
    runtime: str = "python"       # python | shell | binary
    entry: str | None = None      # script inside dir (python/shell)
    command: list[str] = field(default_factory=list)  # for runtime=binary
    group: str | None = None
    timeout_s: int | None = 3600
    params: list[Param] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    environment: str = "system"   # named env in envs/ resolved before every run
    writes: bool = True           # False -> read-only, safe to auto-retry

    def build_argv(self, values: dict[str, Any], python: str | None = None) -> list[str]:
        """Turn parsed CLI values into the child process argv. `python` is the
        resolved environment's interpreter (system python3 by default)."""
        if self.runtime == "binary":
            if not self.command:
                raise ManifestError(f"{self.name}: runtime=binary needs `command`")
            argv = list(self.command)
        elif self.runtime == "shell":
            argv = ["bash", str(self.dir / self.entry)]   # needs bash/WSL/git-bash on Windows
        else:
            import sys
            argv = [python or sys.executable, str(self.dir / self.entry)]

        # Options first, then positionals in manifest order — argparse accepts
        # that in every version, and it keeps the cmdline readable in logs.
        trailing: list[str] = []
        for p in self.params:
            v = values.get(p.name)
            if v is None:
                continue
            items = v if isinstance(v, (list, tuple)) else [v]
            if p.positional:
                trailing += [str(i) for i in items]
                continue
            flag = p.flag or f"--{p.name.replace('_', '-')}"
            if p.type == "bool":
                if v:
                    argv.append(flag)
                continue
            for item in items:
                argv += [flag, str(item)]
        return argv + trailing

    def redacted_cmdline(self, values: dict[str, Any]) -> str:
        secrets = {p.name for p in self.params if p.secret}
        shown = {k: ("***" if k in secrets else v) for k, v in values.items()}
        positional = {p.name for p in self.params if p.positional}
        parts = [f"sdtools {self.name}"]
        for k, v in shown.items():
            if k in positional:
                continue
            if v is None or v is False:
                continue
            if v is True:
                parts.append(f"--{k.replace('_', '-')}")
            else:
                for item in (v if isinstance(v, (list, tuple)) else [v]):
                    parts.append(f"--{k.replace('_', '-')} {shlex.quote(str(item))}")
        for k in (p.name for p in self.params if p.positional):
            v = shown.get(k)
            if v is None:
                continue
            for item in (v if isinstance(v, (list, tuple)) else [v]):
                parts.append(shlex.quote(str(item)))
        return " ".join(parts)


def _parse(path: Path) -> Tool:
    data = yaml.safe_load(path.read_text()) or {}
    missing = [k for k in ("name", "version", "summary") if k not in data]
    if missing:
        raise ManifestError(f"{path}: missing required key(s) {missing}")

    runtime = data.get("runtime", "python")
    if runtime in ("python", "shell") and not data.get("entry"):
        raise ManifestError(f"{path}: runtime={runtime} requires `entry`")

    params = [Param(**p) for p in data.get("params", [])]
    return Tool(
        name=data["name"],
        version=str(data["version"]),
        summary=data["summary"],
        dir=path.parent,
        runtime=runtime,
        entry=data.get("entry"),
        command=data.get("command", []),
        group=data.get("group"),
        timeout_s=data.get("timeout_s", 3600),
        params=params,
        tags=data.get("tags", []),
        env=data.get("env", {}),
        environment=data.get("environment", "system"),
        writes=data.get("writes", True),
    )


def discover(tools_dir: Path) -> tuple[list[Tool], list[str]]:
    """Returns (tools, errors). A broken manifest must not break the whole CLI."""
    tools: list[Tool] = []
    errors: list[str] = []
    if not tools_dir.exists():
        return tools, [f"tools dir not found: {tools_dir}"]

    for manifest in sorted(tools_dir.glob("*/tool.yaml")):
        try:
            tools.append(_parse(manifest))
        except Exception as exc:            # noqa: BLE001 - report, don't crash
            errors.append(f"{manifest.parent.name}: {exc}")

    seen: dict[str, Path] = {}
    for t in tools:
        if t.name in seen:
            errors.append(f"duplicate tool name {t.name!r} ({seen[t.name]} and {t.dir})")
        seen[t.name] = t.dir
    return tools, errors
