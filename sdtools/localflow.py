"""
Run a workflow DAG entirely on THIS machine — no API, no agents, no model.

Same YAML as the dispatched form, same semantics, one executor:
  * deterministic order: Kahn's algorithm with ready steps sorted by key,
    so the same file always executes in the same sequence
  * a failed step cancels its downstream cone; independent branches continue
  * every step resolves its tool's environment first and runs through the
    normal runner (local NDJSON always; telemetry uploaded if configured)

This is what "the console runs all scripts/workflows" means offline: a
field laptop can execute the whole nightly DAG with zero infrastructure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import Config
from .discovery import Tool
from .models import RunStatus
from .runner import execute

_PARAM_REF = re.compile(r"\$\{params\.([A-Za-z0-9_]+)\}")


class WorkflowError(Exception):
    pass


@dataclass
class StepResult:
    key: str
    state: str                     # ok | failed | cancelled | skipped
    run_id: str | None = None
    duration_ms: int = 0
    error: str | None = None


@dataclass
class LocalWorkflow:
    name: str
    steps: list[dict]
    params: dict[str, Any] = field(default_factory=dict)
    project: str | None = None


def _subst(value, params: dict):
    if isinstance(value, str):
        return _PARAM_REF.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [_subst(v, params) for v in value]
    return value


def validate(wf: LocalWorkflow) -> list[str]:
    """Returns deterministic topological order; raises on bad DAG."""
    keys = [s.get("key") for s in wf.steps]
    if not all(keys) or len(set(keys)) != len(keys):
        raise WorkflowError("every step needs a unique key")
    known = set(keys)
    deps = {}
    for s in wf.steps:
        after = s.get("after") or []
        unknown = [a for a in after if a not in known]
        if unknown:
            raise WorkflowError(f"step {s['key']!r}: unknown dependencies {unknown}")
        deps[s["key"]] = set(after)

    order: list[str] = []
    done: set[str] = set()
    while deps:
        ready = sorted(k for k, dd in deps.items() if dd <= done)
        if not ready:
            raise WorkflowError(f"dependency cycle among: {sorted(deps)}")
        for k in ready:
            order.append(k)
            done.add(k)
            del deps[k]
    return order


def run_local(
    wf: LocalWorkflow,
    tools: dict[str, Tool],
    cfg: Config,
    coerce,                      # (tool, params) -> typed values (agent._coerce)
    echo=None,                   # callable(str, colour|None) for progress lines
) -> tuple[str, list[StepResult]]:
    """Execute the DAG. Returns (workflow_status, per-step results)."""
    order = validate(wf)
    by_key = {s["key"]: s for s in wf.steps}
    results: dict[str, StepResult] = {}
    say = echo or (lambda *_: None)

    def doomed_by(key: str) -> str | None:
        for dep in by_key[key].get("after") or []:
            r = results.get(dep)
            if r and r.state != "ok":
                return dep
        return None

    for key in order:
        step = by_key[key]
        bad_dep = doomed_by(key)
        if bad_dep:
            results[key] = StepResult(key, "cancelled",
                                      error=f"dependency {bad_dep!r} did not succeed")
            say(f"⊘ {key}: cancelled (upstream {bad_dep} failed)", "yellow")
            continue

        tool = tools.get(step["tool"])
        if tool is None:
            results[key] = StepResult(key, "failed",
                                      error=f"tool {step['tool']!r} not found")
            say(f"✕ {key}: tool {step['tool']!r} not installed", "red")
            continue

        say(f"▸ {key} ({tool.name}, env={tool.environment})", "blue")
        params = {k: _subst(v, wf.params) for k, v in (step.get("params") or {}).items()}
        result = execute(
            tool, coerce(tool, params), cfg,
            project=wf.project,
            tags=(step.get("tags") or []) + [f"lwf:{wf.name}", f"step:{key}"],
        )
        ok = result.status is RunStatus.OK
        results[key] = StepResult(
            key, "ok" if ok else "failed", run_id=result.run_id,
            duration_ms=result.duration_ms,
            error=None if ok else f"{result.error_kind}: {result.error_message}")
        say(f"{'✓' if ok else '✕'} {key}: {result.status.value} "
            f"({result.duration_ms / 1000:.1f}s)", "green" if ok else "red")

    ordered = [results[k] for k in order]
    status = ("failed" if any(r.state == "failed" for r in ordered)
              else "cancelled" if any(r.state == "cancelled" for r in ordered)
              else "ok")
    return status, ordered
