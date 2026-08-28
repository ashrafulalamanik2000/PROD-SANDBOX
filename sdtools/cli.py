"""
sdtools -- the console entrypoint.

Tool subcommands are generated at import time from tools/*/tool.yaml, so
`sdtools --help` always reflects what is actually on disk. Built-in commands
(list, doctor, flush, logs, runs) are prefixed-free but reserved.
"""

from __future__ import annotations

import inspect
import json
import sys
from typing import Any

import typer

# The console must never crash printing a child's output: on Windows a
# redirected/legacy-console stdout defaults to the locale codepage (cp1252),
# which cannot encode the tools' UTF-8 banners/checkmarks. Force UTF-8 with
# replacement so echoing is always safe.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from . import protocol, telemetry
from .config import CLI_VERSION, RUNS_DIR, load_config, machine_id
from .discovery import Tool, discover
from .models import RunStatus
from .runner import execute

from .discovery import RESERVED_PARAMS as RESERVED  # noqa: E402

app = typer.Typer(
    name="sdtools",
    help="Spatialdata internal processing toolkit. Every run is logged and uploaded.",
    no_args_is_help=True,
    add_completion=True,
)

_cfg = load_config()
_tools, _errors = discover(_cfg.tools_dir)


# --------------------------------------------------------------------------
# built-ins
# --------------------------------------------------------------------------

@app.command("list")
def list_tools(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Show every discovered tool."""
    if not _tools:
        typer.secho(f"No tools found in {_cfg.tools_dir}", fg="yellow")
    groups: dict[str, list[Tool]] = {}
    for t in _tools:
        groups.setdefault(t.group or "ungrouped", []).append(t)
    for group, items in sorted(groups.items()):
        typer.secho(f"\n{group}", bold=True)
        for t in items:
            typer.echo(f"  {t.name:<22} {t.version:<8} {t.summary}")
            if verbose:
                for p in t.params:
                    req = "required" if p.required else f"default={p.default!r}"
                    typer.echo(f"      --{p.name.replace('_','-'):<18} {p.type:<6} {req}")
    for err in _errors:
        typer.secho(f"  ! {err}", fg="red")


@app.command()
def version() -> None:
    """Print the console version (use this to confirm an update took effect)."""
    typer.echo(CLI_VERSION)


@app.command()
def doctor() -> None:
    """Check config, API reachability, and spool depth."""
    ok = True
    typer.echo(f"cli            {CLI_VERSION}")
    typer.echo(f"tools dir      {_cfg.tools_dir} ({len(_tools)} tools)")
    typer.echo(f"machine id     {machine_id()}")
    typer.echo(f"user           {_cfg.user}")
    typer.echo(f"api url        {_cfg.api_url or '(unset)'}")
    typer.echo(f"api key        {'set' if _cfg.api_key else '(unset)'}")
    typer.echo(f"telemetry      {'on' if _cfg.telemetry_enabled else 'OFF (local only)'}")

    spool = (list(telemetry.SPOOL_DIR.glob("*.json.gz"))
             if telemetry.SPOOL_DIR.exists() else [])
    typer.echo(f"spool backlog  {len(spool)}")

    if _cfg.telemetry_enabled:
        import httpx
        try:
            r = httpx.get(f"{_cfg.api_url.rstrip('/')}/v1/health",
                          headers={"Authorization": f"Bearer {_cfg.api_key}"}, timeout=5)
            typer.secho(f"api health     {r.status_code} {r.text[:120]}",
                        fg="green" if r.status_code == 200 else "red")
            ok = r.status_code == 200
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"api health     unreachable: {exc}", fg="red")
            ok = False
    for err in _errors:
        typer.secho(f"manifest error {err}", fg="red")
        ok = False
    raise typer.Exit(0 if ok else 1)


@app.command()
def flush() -> None:
    """Replay any spooled telemetry that failed to upload earlier."""
    sent, dropped, remaining = telemetry.flush_spool(_cfg, limit=100_000)
    msg = f"uploaded {sent}, remaining {remaining}"
    if dropped:
        msg += f" (DROPPED {dropped}: the server rejected them permanently -- check key scope)"
    typer.echo(msg)
    pruned = telemetry.prune_local_logs(_cfg.log_retention_days)
    if pruned:
        typer.echo(f"pruned {pruned} local logs older than {_cfg.log_retention_days}d")


@app.command()
def runs(limit: int = 20) -> None:
    """List recent local runs (works with no network)."""
    files = sorted(RUNS_DIR.glob("*.ndjson"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[:limit]:
        head = tail = None
        for line in f.read_text(errors="replace").splitlines():
            rec = json.loads(line) if line.strip().startswith("{") else None
            if not rec:
                continue
            if rec["kind"] == "run.start":
                head = rec["data"]
            elif rec["kind"] == "run.finish":
                tail = rec["data"]
        if not head:
            continue
        status = (tail or {}).get("status", "running")
        dur = (tail or {}).get("duration_ms", 0) / 1000
        typer.echo(f"{f.stem[:8]}  {status:<9} {dur:7.1f}s  {head['tool']:<18} {head['cmdline']}")


@app.command()
def logs(run_id: str, errors_only: bool = typer.Option(False, "--errors")) -> None:
    """Print the local log for a run id (prefix match allowed)."""
    matches = [p for p in RUNS_DIR.glob(f"{run_id}*.ndjson")]
    if not matches:
        typer.secho(f"no local log for {run_id}", fg="red")
        raise typer.Exit(1)
    for line in matches[0].read_text(errors="replace").splitlines():
        rec = json.loads(line)
        if rec["kind"] != "event":
            continue
        d = rec["data"]
        if errors_only and d.get("level") not in ("error", "warning"):
            continue
        typer.echo(f"{d['seq']:>6} {d['level']:<7} {d['message']}")


@app.command()
def wrap(
    source: str = typer.Argument(help="Folder containing the existing script/skill "
                                      "(must be readable from THIS machine)."),
    name: str = typer.Option(None, help="Tool name (default: folder name, kebab-cased)."),
    entry: str = typer.Option(None, help="Entry script relative to the folder "
                                         "(auto-detected when obvious)."),
    in_place: bool = typer.Option(False, "--in-place",
                                  help="Reference the folder where it is (network "
                                       "share) instead of copying into tools/."),
    environment: str = typer.Option(None, help="Use an existing env instead of "
                                               "drafting one from requirements."),
    force: bool = typer.Option(False, "--force",
                               help="Redraft over an existing tool of this name "
                                    "(a reviewed env.yaml is never clobbered)."),
) -> None:
    """Scaffold a console tool from an existing script or agent-skill folder."""
    from pathlib import Path as _P
    from .wrap import WrapError, wrap as _wrap
    try:
        rep = _wrap(_cfg, _P(source), name=name, entry=entry,
                    in_place=in_place, environment=environment, force=force)
    except WrapError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from exc

    typer.secho(f"drafted {rep.manifest}", fg="green", bold=True)
    typer.echo(f"  entry:  {rep.entry}")
    typer.echo(f"  params: {', '.join(p.name for p in rep.params) or '(none found)'}")
    if rep.env_file:
        typer.echo(f"  env:    {rep.env_file}  (deps: {', '.join(rep.deps)})")
    for f in rep.agentic_ignored:
        typer.secho(f"  ignored (agentic, not part of the tool): {f}", fg="yellow")
    for n in rep.notes:
        typer.secho(f"  note: {n}", fg="yellow")
    typer.echo("\nnext:")
    step = 1
    typer.echo(f"  {step}. review {rep.manifest.name} — every guess is marked")
    if rep.env_file:
        step += 1
        typer.echo(f"  {step}. sdtools env lock {rep.env_file.parent.name}  "
                   f"&& commit the lockfile")
    step += 1
    typer.echo(f"  {step}. sdtools {rep.manifest.parent.name.replace('_', '-')} "
               f"--dry-run …  then drop --dry-run")


# --------------------------------------------------------------------------
# environments: the console resolves them before anything runs
# --------------------------------------------------------------------------

env_app = typer.Typer(help="Named, lockfile-pinned tool environments (envs/).",
                      no_args_is_help=True)
app.add_typer(env_app, name="env")


@env_app.command("list")
def env_list() -> None:
    """Show every environment, whether it's locked, and whether it's cached."""
    from . import environments as E
    rows = E.status(_cfg)
    if not rows:
        typer.secho(f"no environments in {E.envs_root(_cfg)}", fg="yellow")
        return
    for r in rows:
        state = ("cached" if r["cached"] else "locked" if r["locked"] else "UNLOCKED")
        colour = {"cached": "green", "locked": None, "UNLOCKED": "yellow"}[state]
        typer.secho(f"  {r['name']:<16} {r['kind']:<6} py={r['python'] or '-':<6} "
                    f"{state:<9} {r['digest'] or ''}  {r.get('error', '')}",
                    fg=colour)


@env_app.command("lock")
def env_lock(name: str) -> None:
    """(Re)compile the lockfile from the spec. Review the diff, commit it."""
    from . import environments as E
    try:
        path = E.lock(_cfg, name)
    except E.EnvironmentError_ as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from exc
    typer.secho(f"wrote {path} — commit this file", fg="green")


@env_app.command("resolve")
def env_resolve(name: str) -> None:
    """Build (or verify) the cached prefix for an environment now, instead of
    on a tool's first run."""
    from . import environments as E
    try:
        r = E.resolve(_cfg, name)
    except E.EnvironmentError_ as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from exc
    verb = "cache hit" if r.cached else f"built in {r.build_s:.1f}s"
    typer.secho(f"{name} ({r.digest or 'system'}) — {verb}", fg="green")
    typer.echo(f"  python: {r.python}")


@env_app.command("prune")
def env_prune() -> None:
    """Delete cached prefixes that no current spec+lockfile points at."""
    from . import environments as E
    typer.echo(f"removed {E.prune(_cfg)} stale prefix(es)")


# --------------------------------------------------------------------------
# dispatcher: agent + operator commands
# --------------------------------------------------------------------------

def _api_client():
    import httpx
    if not _cfg.telemetry_enabled:
        typer.secho("api.url and api.key must be configured", fg="red")
        raise typer.Exit(2)
    return httpx.Client(base_url=_cfg.api_url.rstrip("/"), timeout=15,
                        headers={"Authorization": f"Bearer {_cfg.api_key}"})


@app.command()
def agent(
    name: str = typer.Option("agent", help="Slot name; run two agents with "
                                            "different names for two slots."),
    queue: list[str] = typer.Option(["default"], "--queue", help="Repeatable."),
    labels: str = typer.Option("", help="Comma-separated capability labels "
                                        "(e.g. gpu,bigmem,pdal)."),
    poll_s: float = typer.Option(2.0, help="Poll interval when idle."),
    max_jobs: int = typer.Option(0, help="Exit after N jobs (0 = forever)."),
    idle_exit_s: float = typer.Option(0, help="Exit after this long idle (0 = never)."),
) -> None:
    """Run as a worker: claim dispatched jobs and execute them here."""
    from .agent import run_agent
    run_agent(_cfg, name, list(queue),
              [x.strip() for x in labels.split(",") if x.strip()],
              poll_s, max_jobs or None, idle_exit_s or None)


@app.command()
def submit(
    tool: str = typer.Argument(help="Tool name as known to the worker machines."),
    param: list[str] = typer.Option([], "--param", "-P",
                                    help="Repeatable key=value tool parameter."),
    queue: str = typer.Option("default"),
    priority: int = typer.Option(100, help="Lower runs sooner."),
    require: str = typer.Option("", help="Comma-separated labels the machine must have."),
    project: str = typer.Option(None),
    max_attempts: int = typer.Option(2),
    stage_in: list[str] = typer.Option(
        [], "--stage-in",
        help="Repeatable SRC::PARAM — the worker copies SRC (a path IT can "
             "reach, e.g. a NAS UNC) into its local [staging] dir and sets "
             "tool param PARAM to the local copy."),
    stage_out: list[str] = typer.Option(
        [], "--stage-out",
        help="Repeatable REL::DEST — after an ok run the worker copies REL "
             "(relative to the first staged-in folder; '.' = all of it) to "
             "DEST (e.g. a NAS UNC folder)."),
    stage_keep: bool = typer.Option(
        False, "--stage-keep",
        help="Keep the worker-local staged copy (default: deleted after a "
             "successful stage-out; always kept on failure)."),
) -> None:
    """Queue one job for the worker fleet instead of running it here."""
    params: dict = {}
    for kv in param:
        if "=" not in kv:
            raise typer.BadParameter(f"-P expects key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        if k in params:   # repeated -P key=... becomes a list (multiple: true params)
            cur = params[k]
            params[k] = (cur if isinstance(cur, list) else [cur]) + [v]
        else:
            params[k] = v
    if stage_in or stage_out:
        def split2(spec: str, opt: str) -> list[str]:
            parts = spec.split("::")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise typer.BadParameter(f"{opt} expects A::B, got {spec!r}")
            return parts
        if stage_out and not stage_in:
            raise typer.BadParameter("--stage-out needs at least one --stage-in "
                                     "(REL is relative to the first staged folder)")
        params["_staging"] = {
            "in": [dict(zip(("src", "param"), split2(s, "--stage-in")))
                   for s in stage_in],
            "out": [dict(zip(("rel", "dest"), split2(s, "--stage-out")))
                    for s in stage_out],
            "keep": stage_keep,
        }
    with _api_client() as api:
        r = api.post("/v1/jobs", json={
            "tool": tool, "params": params, "queue": queue, "priority": priority,
            "require_labels": [x.strip() for x in require.split(",") if x.strip()],
            "project": project or _cfg.project, "max_attempts": max_attempts})
        r.raise_for_status()
        d = r.json()
    typer.secho(f"queued {d['job_id'][:8]} on '{d['queue']}'", fg="green")


@app.command()
def workflow(
    file: str = typer.Argument(help="Workflow YAML (see docs/workflow.example.yaml)."),
    set_param: list[str] = typer.Option([], "--set", "-s",
                                        help="Override workflow params: key=value."),
    watch: bool = typer.Option(False, help="Poll until the workflow finishes."),
    local: bool = typer.Option(False, "--local",
                               help="Run the whole DAG on THIS machine — no API, "
                                    "no agents. Deterministic topological order."),
) -> None:
    """Run a multi-step workflow (a DAG): --local here, otherwise via the fleet."""
    import yaml as _yaml
    from pathlib import Path
    spec = _yaml.safe_load(Path(file).read_text())
    spec.setdefault("params", {})
    for kv in set_param:
        k, v = kv.split("=", 1)
        spec["params"][k] = v
    spec.setdefault("project", _cfg.project)

    if local:
        from .agent import _coerce
        from .localflow import LocalWorkflow, WorkflowError, run_local
        lwf = LocalWorkflow(name=spec.get("name", Path(file).stem),
                            steps=spec.get("steps") or [],
                            params=spec["params"], project=spec.get("project"))
        try:
            status, steps = run_local(
                lwf, {t.name: t for t in _tools}, _cfg, _coerce,
                echo=lambda line, colour: typer.secho(f"  {line}", fg=colour))
        except WorkflowError as exc:
            typer.secho(f"invalid workflow: {exc}", fg="red")
            raise typer.Exit(2) from exc
        bad = status != "ok"
        typer.secho(f"\n{status.upper()}  {sum(s.duration_ms for s in steps)/1000:.1f}s "
                    f"({sum(1 for s in steps if s.state == 'ok')}/{len(steps)} steps ok)",
                    fg="red" if bad else "green", bold=True)
        raise typer.Exit(1 if bad else 0)

    with _api_client() as api:
        r = api.post("/v1/workflows", json=spec)
        if r.status_code >= 400:
            typer.secho(f"rejected: {r.text}", fg="red")
            raise typer.Exit(1)
        d = r.json()
        wf_id = d["workflow_id"]
        typer.secho(f"workflow {wf_id[:8]} submitted "
                    f"({len(d['jobs'])} jobs)", fg="green", bold=True)
        if not watch:
            return

        last = ""
        while True:
            wf = api.get(f"/v1/workflows/{wf_id}").json()
            line = " ".join(f"{k}={v}" for k, v in sorted(wf["job_states"].items()))
            if line != last:
                typer.echo(f"  {wf['status']:<9} {line}")
                last = line
            if wf["status"] != "running":
                for j in wf["jobs"]:
                    mark = {"ok": "✓", "failed": "✕", "cancelled": "⊘"}.get(j["state"], "…")
                    where = f" on {j['leased_by']}" if j.get("leased_by") else ""
                    err = f"  ({j['error_kind']})" if j.get("error_kind") else ""
                    typer.echo(f"   {mark} {j['step_key'] or j['tool']:<16} "
                               f"{j['state']}{where}{err}")
                raise typer.Exit(0 if wf["status"] == "ok" else 1)
            import time as _t
            _t.sleep(2)


@app.command()
def jobs(state: str = typer.Option(None), queue: str = typer.Option(None),
         limit: int = 25) -> None:
    """List dispatched jobs (server-side)."""
    with _api_client() as api:
        r = api.get("/v1/jobs", params={k: v for k, v in
                                        [("state", state), ("queue", queue),
                                         ("limit", limit)] if v})
        r.raise_for_status()
    for j in r.json()["jobs"]:
        who = j["leased_by"] or "-"
        err = f"  {j['error_kind']}" if j.get("error_kind") else ""
        flag = "  (cancel requested)" if j.get("cancel_requested") \
            and j["state"] in ("leased", "running") else ""
        typer.echo(f"{j['job_id'][:8]}  {j['state']:<9} {j['queue']:<8} "
                   f"{j['tool']:<16} {who:<28}{err}{flag}")


def _resolve_job(api, prefix: str) -> dict:
    r = api.get("/v1/jobs", params={"limit": 500})
    r.raise_for_status()
    hits = [j for j in r.json()["jobs"] if j["job_id"].startswith(prefix)]
    if not hits:
        typer.secho(f"no job matches {prefix!r}", fg="red")
        raise typer.Exit(1)
    if len(hits) > 1:
        typer.secho(f"{prefix!r} is ambiguous ({len(hits)} jobs) — "
                    f"give more characters", fg="red")
        raise typer.Exit(1)
    return hits[0]


@app.command()
def cancel(job_id: str = typer.Argument(help="Job id (prefix ok)")) -> None:
    """Cancel a job: queued jobs settle immediately; running jobs are stopped
    by their agent at its next lease renewal (within ~a minute)."""
    with _api_client() as api:
        job = _resolve_job(api, job_id)
        r = api.post(f"/v1/jobs/{job['job_id']}/cancel")
        if r.status_code >= 400:
            typer.secho(f"rejected: {r.text}", fg="red")
            raise typer.Exit(1)
        d = r.json()
    if d.get("cancel_requested"):
        typer.secho(f"cancel requested — agent {job['leased_by'] or '?'} will stop "
                    f"the tool at its next lease renewal", fg="yellow")
    else:
        typer.secho(f"{job['job_id'][:8]} cancelled", fg="green")


@app.command()
def retry(job_id: str = typer.Argument(help="Job id (prefix ok)")) -> None:
    """Re-queue a failed or cancelled job (same id, fresh attempt count)."""
    with _api_client() as api:
        job = _resolve_job(api, job_id)
        r = api.post(f"/v1/jobs/{job['job_id']}/retry")
        if r.status_code >= 400:
            typer.secho(f"rejected: {r.text}", fg="red")
            raise typer.Exit(1)
    typer.secho(f"{job['job_id'][:8]} re-queued on '{job['queue']}'", fg="green")


@app.command()
def agents() -> None:
    """List worker machines and what they're doing."""
    with _api_client() as api:
        r = api.get("/v1/agents")
        r.raise_for_status()
    for a in r.json()["agents"]:
        cur = a["current_job_id"][:8] if a["current_job_id"] else "-"
        typer.echo(f"{a['agent_id']:<28} {a['state']:<8} {a['hostname']:<16} "
                   f"queues={','.join(a['queues'])} labels={','.join(a['labels']) or '-'} "
                   f"job={cur}")


# --------------------------------------------------------------------------
# generated tool subcommands
# --------------------------------------------------------------------------

def _register(tool: Tool) -> None:
    clash = {p.name for p in tool.params} & RESERVED
    if clash:
        _errors.append(f"{tool.name}: param name(s) {clash} are reserved — rename "
                       f"the param and set `flag: --<original>` to keep the "
                       f"child script's flag unchanged")
        return

    def impl(**kwargs: Any) -> None:
        project = kwargs.pop("project", None)
        tags = list(kwargs.pop("tag", []) or [])
        dry_run = kwargs.pop("dry_run", False)
        quiet = kwargs.pop("quiet", False)
        json_out = kwargs.pop("json_out", False)

        telemetry.flush_spool(_cfg)   # opportunistic backlog drain
        shown = {"bucket": -1}

        def echo(line: str, level: str, kind: str | None) -> None:
            if quiet:
                return
            if kind == "progress":
                _, payload = protocol.parse_line(line)
                pct = (payload or {}).get("value") or 0.0
                bucket = int(pct * 10)
                if bucket > shown["bucket"]:
                    shown["bucket"] = bucket
                    note = (payload or {}).get("note") or ""
                    typer.secho(f"  .. {pct*100:5.1f}%  {note}", dim=True)
                return
            if kind:            # other protocol lines are data, not output
                return
            colour = {"error": "red", "warning": "yellow"}.get(level)
            typer.secho(line, fg=colour, err=(level == "error"))

        result = execute(
            tool, kwargs, _cfg, project=project, tags=tags,
            on_line=None if json_out else echo, dry_run=dry_run,
        )
        if dry_run:
            return

        if json_out:
            typer.echo(json.dumps({
                "run_id": result.run_id, "status": result.status.value,
                "exit_code": result.exit_code, "duration_ms": result.duration_ms,
                "metrics": result.metrics, "counts": result.counts,
                "error_kind": result.error_kind, "error_message": result.error_message,
                "artifacts": result.artifacts,
            }, indent=2))
        elif not quiet:
            bad = result.status is not RunStatus.OK
            typer.secho(
                f"\n{result.status.value.upper()}  {result.duration_ms/1000:.1f}s  "
                f"run {result.run_id[:8]}"
                + (f"  ({result.error_kind}: {result.error_message})" if bad else ""),
                fg="red" if bad else "green", bold=True,
            )
            if result.spooled:
                typer.secho(f"  {result.spooled} telemetry payload(s) spooled "
                            f"for later upload", fg="yellow")
        raise typer.Exit(0 if result.status is RunStatus.OK else 1)

    sig_params = []
    for p in tool.params:
        if p.required:
            default = typer.Option(..., help=p.help)
        else:
            default = typer.Option(p.default, help=p.help)
        sig_params.append(inspect.Parameter(
            p.name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=p.py_type))

    sig_params += [
        inspect.Parameter("project", inspect.Parameter.KEYWORD_ONLY,
                          default=typer.Option(None, help="Group this run (client/site/job)."),
                          annotation=str),
        inspect.Parameter("tag", inspect.Parameter.KEYWORD_ONLY,
                          default=typer.Option(None, help="Repeatable free-form tag."),
                          annotation=list[str]),
        inspect.Parameter("dry_run", inspect.Parameter.KEYWORD_ONLY,
                          default=typer.Option(False, help="Print the argv and exit."),
                          annotation=bool),
        inspect.Parameter("quiet", inspect.Parameter.KEYWORD_ONLY,
                          default=typer.Option(False, help="Suppress tool output."),
                          annotation=bool),
        inspect.Parameter("json_out", inspect.Parameter.KEYWORD_ONLY,
                          default=typer.Option(False, "--json",
                                               help="Machine-readable result on stdout."),
                          annotation=bool),
    ]
    impl.__signature__ = inspect.Signature(sig_params)
    impl.__name__ = tool.name.replace("-", "_")
    impl.__doc__ = f"{tool.summary}  (v{tool.version})"
    app.command(tool.name)(impl)


for _t in _tools:
    _register(_t)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:
        typer.secho("\ninterrupted", fg="yellow")
        sys.exit(130)


if __name__ == "__main__":
    main()
