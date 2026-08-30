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
def doctor(as_json: bool = typer.Option(False, "--json",
                                        help="Machine-readable output.")) -> None:
    """Check config, API reachability, and spool depth."""
    spool = (list(telemetry.SPOOL_DIR.glob("*.json.gz"))
             if telemetry.SPOOL_DIR.exists() else [])
    d: dict = {
        "cli_version": CLI_VERSION,
        "tools_dir": str(_cfg.tools_dir),
        "tool_count": len(_tools),
        "machine_id": machine_id(),
        "user": _cfg.user,
        "api_url": _cfg.api_url,
        "api_key_set": bool(_cfg.api_key),
        "telemetry": _cfg.telemetry_enabled,
        "spool_backlog": len(spool),
        "staging_dir": str(_cfg.staging_dir) if _cfg.staging_dir else None,
        "manifest_errors": list(_errors),
        "api_health": None,
    }
    if _cfg.telemetry_enabled:
        import httpx
        try:
            r = httpx.get(f"{_cfg.api_url.rstrip('/')}/v1/health",
                          headers={"Authorization": f"Bearer {_cfg.api_key}"}, timeout=5)
            d["api_health"] = {"status": r.status_code, "body": r.text[:120]}
        except Exception as exc:  # noqa: BLE001
            d["api_health"] = {"status": None, "body": f"unreachable: {exc}"}
    healthy = ((not _cfg.telemetry_enabled
                or (d["api_health"] or {}).get("status") == 200)
               and not _errors)
    d["ok"] = healthy

    if as_json:
        from .apiclient import emit_json
        emit_json(d)
        raise typer.Exit(0 if healthy else 1)

    typer.echo(f"cli            {CLI_VERSION}")
    typer.echo(f"tools dir      {_cfg.tools_dir} ({len(_tools)} tools)")
    typer.echo(f"machine id     {machine_id()}")
    typer.echo(f"user           {_cfg.user}")
    typer.echo(f"api url        {_cfg.api_url or '(unset)'}")
    typer.echo(f"api key        {'set' if _cfg.api_key else '(unset)'}")
    typer.echo(f"telemetry      {'on' if _cfg.telemetry_enabled else 'OFF (local only)'}")
    typer.echo(f"spool backlog  {len(spool)}")
    if d["api_health"] is not None:
        h = d["api_health"]
        typer.secho(f"api health     {h['status'] or ''} {h['body']}".rstrip(),
                    fg="green" if h["status"] == 200 else "red")
    for err in _errors:
        typer.secho(f"manifest error {err}", fg="red")
    raise typer.Exit(0 if healthy else 1)


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


def _emit_event(e: dict, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(e, default=str))
        return
    ts = (str(e.get("ts") or ""))[11:19]
    colour = {"error": "red", "warning": "yellow"}.get(e.get("level"))
    typer.secho(f"{ts} {e.get('level', ''):<7} {e['message']}", fg=colour)


def _resolve_remote_run(prefix: str) -> str | None:
    """Match a run OR job id prefix to one remote run id (None = no match)."""
    from .apiclient import fetch
    with _api_client() as api:
        ids = {j["run_id"] for j in fetch(api, "/v1/jobs", {"limit": 500})["jobs"]
               if j["job_id"].startswith(prefix) and j.get("run_id")}
        ids |= {r["run_id"] for r in fetch(api, "/v1/runs", {"limit": 500})["runs"]
                if r["run_id"].startswith(prefix)}
    if len(ids) > 1:
        typer.secho(f"{prefix!r} is ambiguous ({len(ids)} runs) — "
                    f"give more characters", fg="red")
        raise typer.Exit(1)
    return ids.pop() if ids else None


@app.command()
def logs(run_id: str = typer.Argument(help="Run OR job id (prefix ok)"),
         follow: bool = typer.Option(False, "--follow",
                                     help="Keep tailing until the run finishes."),
         errors_only: bool = typer.Option(False, "--errors",
                                          help="Only error/warning lines."),
         tail: int = typer.Option(0, help="Start from the last N events "
                                          "(0 = from the beginning)."),
         raw: bool = typer.Option(False, "--raw",
                                  help="Include ::sdtools:: telemetry "
                                       "sentinel lines."),
         as_json: bool = typer.Option(False, "--json",
                                      help="One JSON object per line.")) -> None:
    """Print (or --follow) a run's log by run id or job id. Uses the API when
    configured; falls back to the local NDJSON for offline runs."""
    import time as _t
    from .apiclient import fetch

    remote = _resolve_remote_run(run_id) if _cfg.telemetry_enabled else None

    if remote:
        level = "error,warning" if errors_only else None
        with _api_client() as api:
            after = 0
            if tail:
                after = max(0, fetch(api, f"/v1/runs/{remote}")
                            .get("event_count", 0) - tail)
            status = None
            while True:
                batch = fetch(api, f"/v1/runs/{remote}/events",
                              {"after_seq": after, "limit": 2000,
                               "level": level})["events"]
                for e in batch:
                    after = max(after, e["seq"])
                    if as_json or raw or \
                            not e["message"].startswith("::sdtools::"):
                        _emit_event(e, as_json)
                if len(batch) == 2000:
                    continue                      # keep paging this drain
                if not follow:
                    break
                status = fetch(api, f"/v1/runs/{remote}").get("status")
                if status != "running":
                    break                          # final drain already done
                try:
                    _t.sleep(2)
                except KeyboardInterrupt:
                    raise typer.Exit(130) from None
            if follow and status and not as_json:
                typer.secho(f"-- run {status} --",
                            fg="green" if status == "ok" else "red", bold=True)
        return

    # ---- local NDJSON fallback (offline runs / no API) ----
    matches = list(RUNS_DIR.glob(f"{run_id}*.ndjson"))
    if not matches:
        typer.secho(f"no run or job matches {run_id!r} (API"
                    f"{' and' if _cfg.telemetry_enabled else ' not configured,'}"
                    f" local logs checked)", fg="red")
        raise typer.Exit(1)
    if follow:
        typer.secho("--follow needs the API; printing the local snapshot",
                    fg="yellow")
    recs = [json.loads(line)["data"]
            for line in matches[0].read_text(errors="replace").splitlines()
            if line.strip().startswith("{")
            and json.loads(line)["kind"] == "event"]
    if errors_only:
        recs = [d for d in recs if d.get("level") in ("error", "warning")]
    if not (as_json or raw):
        recs = [d for d in recs
                if not str(d.get("message", "")).startswith("::sdtools::")]
    for d in recs[-tail:] if tail else recs:
        _emit_event(d, as_json)


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
    from .apiclient import client
    return client(_cfg)


def _agent_status(as_json: bool) -> None:
    """Local worker health + this machine's config sanity + fleet view."""
    import shutil as _shutil

    from .apiclient import emit_json, fetch

    mid = machine_id()
    scope = None
    if _cfg.api_key and _cfg.api_key.startswith("sdt_"):
        scope = {"subm": "submit", "inge": "ingest", "read": "read",
                 "admi": "admin"}.get(_cfg.api_key[4:8])
    spool = (len(list(telemetry.SPOOL_DIR.glob("*.json.gz")))
             if telemetry.SPOOL_DIR.exists() else 0)
    staging = _cfg.staging_dir
    staging_free_gb = None
    if staging:
        anchor = staging if staging.exists() else staging.anchor
        try:
            staging_free_gb = round(
                _shutil.disk_usage(anchor).free / 1e9, 1)
        except OSError:
            pass

    fleet: list[dict] | None = None
    fleet_note = ""
    if _cfg.telemetry_enabled:
        try:
            with _api_client() as api:
                fleet = fetch(api, "/v1/agents")["agents"]
            for a in fleet:
                a["seen_s"] = _age_s(a["last_seen_at"])
                a["this_machine"] = a["machine_id"] == mid
        except typer.Exit:
            fleet_note = ("fleet view unavailable (API unreachable, or this "
                          "key's scope cannot read agents)")
    else:
        fleet_note = "fleet view unavailable (API not configured)"

    local = {
        "machine_id": mid,
        "cli_version": CLI_VERSION,
        "api_key_scope": scope,
        "telemetry": _cfg.telemetry_enabled,
        "spool_backlog": spool,
        "staging_dir": str(staging) if staging else None,
        "staging_dir_exists": staging.exists() if staging else None,
        "staging_free_gb": staging_free_gb,
        "tools": len(_tools),
    }
    if as_json:
        emit_json({"local": local, "agents": fleet, "note": fleet_note or None})
        return

    typer.secho("worker config (this machine)", bold=True)
    typer.echo(f"  machine id     {mid}")
    typer.echo(f"  cli            {CLI_VERSION}   tools {len(_tools)}")
    typer.secho(f"  api key scope  {scope or '(unset)'}"
                + ("" if scope in ("ingest", "submit", "admin") else
                   "   <- a worker needs an ingest key"),
                fg=None if scope in ("ingest", "submit", "admin") else "red")
    typer.secho(f"  spool backlog  {spool}", fg="yellow" if spool else None)
    if staging:
        bad = not staging.exists()
        typer.secho(f"  staging dir    {staging}"
                    + ("  MISSING" if bad else
                       f"  ({staging_free_gb} GB free)"),
                    fg="red" if bad else
                    "yellow" if (staging_free_gb or 99) < 50 else None)
    else:
        typer.secho("  staging dir    (unset — staged jobs will fail here)",
                    fg="yellow")

    typer.secho("\nregistered agents", bold=True)
    if fleet is None:
        typer.secho(f"  {fleet_note}", fg="yellow")
        return
    from rich import box
    from rich.console import Console
    from rich.table import Table
    t = Table(box=box.SIMPLE_HEAD, pad_edge=False, header_style="bold dim")
    t.add_column("here", justify="center")
    t.add_column("agent", overflow="fold", min_width=18)
    for col in ("state", "host", "queues", "labels", "job", "seen"):
        t.add_column(col)
    for a in sorted(fleet, key=lambda x: (not x["this_machine"],
                                          x["seen_s"] or 0)):
        style = ({"idle": "green", "busy": "cyan"}.get(a["state"], "dim"))
        t.add_row("●" if a["this_machine"] else "",
                  a["agent_id"], a["state"], a["hostname"],
                  ",".join(a["queues"]) or "-", ",".join(a["labels"]) or "-",
                  a["current_job_id"][:8] if a["current_job_id"] else "-",
                  f"{_fmt_age(a['seen_s'])} ago",
                  style=style)
    Console().print(t)


@app.command()
def agent(
    action: str = typer.Argument(None, help="'status' shows worker health "
                                            "and config; omit to run the worker."),
    name: str = typer.Option("agent", help="Slot name; run two agents with "
                                            "different names for two slots."),
    queue: list[str] = typer.Option(["default"], "--queue", help="Repeatable."),
    labels: str = typer.Option("", help="Comma-separated capability labels "
                                        "(e.g. gpu,bigmem,pdal)."),
    poll_s: float = typer.Option(2.0, help="Poll interval when idle."),
    max_jobs: int = typer.Option(0, help="Exit after N jobs (0 = forever)."),
    idle_exit_s: float = typer.Option(0, help="Exit after this long idle (0 = never)."),
    as_json: bool = typer.Option(False, "--json",
                                 help="Machine-readable output (status only)."),
) -> None:
    """Run as a worker: claim dispatched jobs and execute them here.
    `sdtools agent status` shows worker health instead of running."""
    if action == "status":
        _agent_status(as_json)
        return
    if action is not None:
        raise typer.BadParameter(f"unknown action {action!r} — did you mean "
                                 f"'sdtools agent status'?")
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
    as_json: bool = typer.Option(False, "--json",
                                 help="Machine-readable output."),
    watch: bool = typer.Option(False, "--watch",
                               help="Follow the job live until it finishes."),
) -> None:
    """Queue one job for the worker fleet instead of running it here."""
    if watch and as_json:
        raise typer.BadParameter("--watch and --json are mutually exclusive")
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
    if as_json:
        from .apiclient import emit_json
        emit_json({"job_id": d["job_id"], "queue": d["queue"]})
        return
    typer.secho(f"queued {d['job_id'][:8]} on '{d['queue']}'", fg="green")
    if watch:
        raise typer.Exit(_follow_job(d["job_id"]))


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


_STATE_STYLE = {"queued": "yellow", "blocked": "yellow", "leased": "cyan",
                "running": "cyan", "ok": "green", "failed": "red",
                "cancelled": "dim"}


def _jobs_table(job_rows: list[dict]):
    from rich import box
    from rich.table import Table
    t = Table(box=box.SIMPLE_HEAD, pad_edge=False, header_style="bold dim")
    t.add_column("job")
    t.add_column("tool", overflow="fold", min_width=10)
    t.add_column("state")
    t.add_column("queue")
    t.add_column("att")
    t.add_column("agent", overflow="fold", min_width=14)
    t.add_column("lease")
    t.add_column("age")
    t.add_column("error", overflow="fold")
    for j in job_rows:
        live = j["state"] in ("leased", "running")
        state = j["state"] + (" (cancel requested)"
                              if j.get("cancel_requested") and live else "")
        lease = (_fmt_age(-(_age_s(j["lease_expires_at"]) or 0))
                 if live and j.get("lease_expires_at") else "-")
        age = _fmt_age(_age_s(j.get("finished_at") or j["created_at"]))
        t.add_row(j["job_id"][:8], j["tool"], state, j["queue"],
                  f"{j['attempts']}/{j['max_attempts']}",
                  j["leased_by"] or "-", lease, age,
                  j.get("error_kind") or "",
                  style=("yellow" if j.get("cancel_requested") and live
                         else _STATE_STYLE.get(j["state"])))
    if not job_rows:
        t.add_row("(no jobs match)", *[""] * 8, style="dim")
    return t


@app.command()
def jobs(state: str = typer.Option(None), queue: str = typer.Option(None),
         limit: int = 25,
         watch: bool = typer.Option(False, "--watch",
                                    help="Live-updating view (Ctrl+C exits)."),
         as_json: bool = typer.Option(False, "--json",
                                      help="Machine-readable output.")) -> None:
    """List dispatched jobs (server-side)."""
    from .apiclient import emit_json, fetch
    params = {"state": state, "queue": queue, "limit": limit}

    if watch:
        if as_json:
            raise typer.BadParameter("--watch and --json are mutually exclusive")
        import time as _t
        from datetime import datetime
        from rich.console import Console, Group
        from rich.live import Live
        from rich.text import Text
        con = Console()
        rows: list[dict] = []
        with _api_client() as api, Live(console=con, screen=False,
                                        refresh_per_second=4) as live:
            try:
                while True:
                    try:
                        rows = fetch(api, "/v1/jobs", params)["jobs"]
                        head = Text(f"jobs  {datetime.now():%H:%M:%S}  "
                                    + "  ".join(f"{k}={v}" for k, v in
                                                params.items() if v),
                                    style="bold")
                    except typer.Exit:
                        # transient API blip: keep the last table on screen
                        head = Text(f"jobs  {datetime.now():%H:%M:%S}  "
                                    "api unreachable — retrying", style="bold red")
                    live.update(Group(head, _jobs_table(rows)))
                    _t.sleep(2)
            except KeyboardInterrupt:
                pass
        return

    with _api_client() as api:
        d = fetch(api, "/v1/jobs", params)
    if as_json:
        emit_json(d["jobs"])
        return
    for j in d["jobs"]:
        who = j["leased_by"] or "-"
        err = f"  {j['error_kind']}" if j.get("error_kind") else ""
        flag = "  (cancel requested)" if j.get("cancel_requested") \
            and j["state"] in ("leased", "running") else ""
        typer.echo(f"{j['job_id'][:8]}  {j['state']:<9} {j['queue']:<8} "
                   f"{j['tool']:<16} {who:<28}{err}{flag}")


_TERMINAL_STATES = ("ok", "failed", "cancelled")


def _follow_job(job_id: str, tail: int = 8, interval: float = 2.0) -> int:
    """Live-follow one job to its terminal state. Returns an exit code."""
    import time as _t
    from collections import deque

    from rich.console import Console, Group
    from rich.live import Live
    from rich.progress_bar import ProgressBar
    from rich.text import Text

    from .apiclient import fetch

    con = Console()
    events: deque = deque(maxlen=tail)
    cursor_run: str | None = None
    after_seq = 0
    job: dict = {}

    def frame(job: dict, run: dict | None, note: str = "") -> Group:
        style = _STATE_STYLE.get(job.get("state", ""), "")
        parts = [Text.assemble(
            (f"{job['job_id'][:8]}  {job['tool']}  ", "bold"),
            (job["state"], f"bold {style}" if style else "bold"),
            (" (cancel requested)" if job.get("cancel_requested")
             and job["state"] in ("leased", "running") else "", "yellow"),
            (f"   attempt {job['attempts']}/{job['max_attempts']}"
             f"   queue {job['queue']}"
             f"   on {job.get('leased_by') or '-'}", "dim"))]
        if job["state"] in ("leased", "running"):
            lease = (-(_age_s(job["lease_expires_at"]) or 0)
                     if job.get("lease_expires_at") else None)
            hb = _age_s(run.get("last_heartbeat_at")) if run else None
            line = Text(f"lease {_fmt_age(lease)}   hb "
                        f"{_fmt_age(hb) + ' ago' if hb is not None else '-'}",
                        style="red" if hb is not None and hb > 60 else "dim")
            parts.append(line)
            if run and run.get("progress") is not None:
                bar = ProgressBar(total=100, completed=run["progress"] * 100,
                                  width=40)
                parts.append(Group(bar, Text(
                    f"{run['progress']*100:.0f}%  "
                    f"{run.get('progress_note') or ''}", style="cyan")))
        if events:
            parts.append(Text("─" * 60, style="dim"))
            for e in events:
                ts = (e.get("ts") or "")[11:19]
                sty = {"error": "red", "warning": "yellow"}.get(e["level"], "dim")
                parts.append(Text(f"{ts} {e['message'][:200]}", style=sty))
        if note:
            parts.append(Text(note, style="bold red"))
        return Group(*parts)

    with _api_client() as api, Live(console=con, refresh_per_second=4) as live:
        try:
            while True:
                note = ""
                try:
                    job = fetch(api, f"/v1/jobs/{job_id}")
                    run = None
                    if job.get("run_id"):
                        if job["run_id"] != cursor_run:   # new attempt
                            cursor_run, after_seq = job["run_id"], 0
                            events.clear()
                        try:
                            run = fetch(api, f"/v1/runs/{cursor_run}")
                            for e in fetch(api, f"/v1/runs/{cursor_run}/events",
                                           {"after_seq": after_seq,
                                            "limit": 200})["events"]:
                                after_seq = max(after_seq, e["seq"])
                                # sentinel lines are machine telemetry (the
                                # progress bar already shows them decoded)
                                if not e["message"].startswith("::sdtools::"):
                                    events.append(e)
                        except typer.Exit:
                            pass          # run telemetry not landed yet
                except typer.Exit:
                    note = "api unreachable — retrying"
                    run = None
                live.update(frame(job, run, note) if job else Text("resolving…"))
                if job.get("state") in _TERMINAL_STATES:
                    break
                _t.sleep(interval)
        except KeyboardInterrupt:
            con.print("[dim]detached — the job keeps running "
                      "(sdtools cancel to stop it)[/]")
            return 130
    ok = job.get("state") == "ok"
    err = f"  ({job.get('error_kind')})" if job.get("error_kind") else ""
    con.print(f"\n[bold {'green' if ok else 'red'}]"
              f"{job['job_id'][:8]} -> {job['state']}{err}[/]")
    return 0 if ok else 1


@app.command()
def watch(job_id: str = typer.Argument(help="Job id (prefix ok)"),
          tail: int = typer.Option(8, help="Event lines kept on screen."),
          interval: float = typer.Option(2.0, help="Refresh seconds.")) -> None:
    """Follow one job live to its terminal state: attempt transitions,
    lease/heartbeat, progress, and a rolling event tail."""
    with _api_client() as api:
        job = _resolve_job(api, job_id)
    raise typer.Exit(_follow_job(job["job_id"], tail=tail, interval=interval))


def _resolve_job(api, prefix: str) -> dict:
    from .apiclient import fetch
    hits = [j for j in fetch(api, "/v1/jobs", {"limit": 500})["jobs"]
            if j["job_id"].startswith(prefix)]
    if not hits:
        typer.secho(f"no job matches {prefix!r}", fg="red")
        raise typer.Exit(1)
    if len(hits) > 1:
        typer.secho(f"{prefix!r} is ambiguous ({len(hits)} jobs) — "
                    f"give more characters", fg="red")
        raise typer.Exit(1)
    return hits[0]


@app.command()
def cancel(job_id: str = typer.Argument(help="Job id (prefix ok)"),
           as_json: bool = typer.Option(False, "--json",
                                        help="Machine-readable output.")) -> None:
    """Cancel a job: queued jobs settle immediately; running jobs are stopped
    by their agent at its next lease renewal (within ~a minute)."""
    with _api_client() as api:
        job = _resolve_job(api, job_id)
        r = api.post(f"/v1/jobs/{job['job_id']}/cancel")
        if r.status_code >= 400:
            typer.secho(f"rejected: {r.text}", fg="red")
            raise typer.Exit(1)
        d = r.json()
    if as_json:
        from .apiclient import emit_json
        emit_json({"job_id": job["job_id"], "state": d.get("state"),
                   "cancel_requested": bool(d.get("cancel_requested"))})
        return
    if d.get("cancel_requested"):
        typer.secho(f"cancel requested — agent {job['leased_by'] or '?'} will stop "
                    f"the tool at its next lease renewal", fg="yellow")
    else:
        typer.secho(f"{job['job_id'][:8]} cancelled", fg="green")


@app.command()
def retry(job_id: str = typer.Argument(help="Job id (prefix ok)"),
          as_json: bool = typer.Option(False, "--json",
                                       help="Machine-readable output.")) -> None:
    """Re-queue a failed or cancelled job (same id, fresh attempt count)."""
    with _api_client() as api:
        job = _resolve_job(api, job_id)
        r = api.post(f"/v1/jobs/{job['job_id']}/retry")
        if r.status_code >= 400:
            typer.secho(f"rejected: {r.text}", fg="red")
            raise typer.Exit(1)
    if as_json:
        from .apiclient import emit_json
        emit_json({"job_id": job["job_id"], "state": "queued",
                   "queue": job["queue"]})
        return
    typer.secho(f"{job['job_id'][:8]} re-queued on '{job['queue']}'", fg="green")


@app.command()
def agents(as_json: bool = typer.Option(False, "--json",
                                        help="Machine-readable output.")) -> None:
    """List worker machines and what they're doing."""
    from .apiclient import emit_json, fetch
    with _api_client() as api:
        d = fetch(api, "/v1/agents")
    if as_json:
        emit_json(d["agents"])
        return
    for a in d["agents"]:
        cur = a["current_job_id"][:8] if a["current_job_id"] else "-"
        typer.echo(f"{a['agent_id']:<28} {a['state']:<8} {a['hostname']:<16} "
                   f"queues={','.join(a['queues'])} labels={','.join(a['labels']) or '-'} "
                   f"job={cur}")


def _age_s(ts: str | None) -> float | None:
    """Seconds since an ISO timestamp (API timestamps are tz-aware UTC)."""
    if not ts:
        return None
    from datetime import datetime, timezone
    t = datetime.fromisoformat(ts)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    s = abs(seconds)
    txt = (f"{s:.0f}s" if s < 120 else
           f"{s/60:.0f}m" if s < 7200 else
           f"{s/3600:.1f}h" if s < 172800 else f"{s/86400:.1f}d")
    return f"-{txt}" if seconds < 0 else txt


@app.command()
def status(
    all_agents: bool = typer.Option(False, "--all",
                                    help="Include agents not seen for over a day."),
    as_json: bool = typer.Option(False, "--json",
                                 help="Machine-readable output."),
) -> None:
    """One-screen fleet overview: API, agents, queues, running work, spool."""
    from .apiclient import emit_json, fetch

    with _api_client() as api:
        health = fetch(api, "/v1/health")
        agent_rows = fetch(api, "/v1/agents")["agents"]
        job_rows = fetch(api, "/v1/jobs", {"limit": 500})["jobs"]
        run_rows = fetch(api, "/v1/runs",
                         {"status": "running", "limit": 100})["runs"]

    spool = (len(list(telemetry.SPOOL_DIR.glob("*.json.gz")))
             if telemetry.SPOOL_DIR.exists() else 0)
    runs_by_id = {r["run_id"]: r for r in run_rows}

    queued = [j for j in job_rows if j["state"] == "queued"]
    active = [j for j in job_rows if j["state"] in ("leased", "running")]
    queues: dict[str, dict] = {}
    for j in queued:
        q = queues.setdefault(j["queue"], {"depth": 0, "oldest_s": 0.0})
        q["depth"] += 1
        q["oldest_s"] = max(q["oldest_s"], _age_s(j["created_at"]) or 0.0)

    for a in agent_rows:
        a["seen_s"] = _age_s(a["last_seen_at"])
    shown_agents = [a for a in agent_rows
                    if all_agents or (a["seen_s"] or 0) < 86400]

    for j in active:
        run = runs_by_id.get(j.get("run_id") or "")
        j["heartbeat_s"] = _age_s(run.get("last_heartbeat_at")) if run else None
        j["progress"] = run.get("progress") if run else None
        j["lease_remaining_s"] = (-(_age_s(j["lease_expires_at"]) or 0)
                                  if j.get("lease_expires_at") else None)

    if as_json:
        emit_json({"health": health, "agents": agent_rows, "queues": queues,
                   "active_jobs": active, "spool_backlog": spool})
        return

    from rich import box
    from rich.console import Console
    from rich.table import Table

    con = Console()
    ok = health.get("ok")
    con.print(f"[bold]sdtools fleet[/]  {_cfg.api_url}   "
              + (f"[bold green]● ok[/]" if ok else "[bold red]● NOT OK[/]")
              + f"   running: {health.get('running')}"
              + (f"   [yellow]local spool backlog: {spool}[/]" if spool else ""))

    t = Table(title=None, box=box.SIMPLE_HEAD, pad_edge=False,
              header_style="bold dim", title_justify="left")
    t.add_column("agent", overflow="fold", min_width=18)
    for col in ("state", "host", "queues", "labels", "job", "seen"):
        t.add_column(col)
    for a in sorted(shown_agents, key=lambda x: x["seen_s"] or 0):
        stale = (a["seen_s"] or 0) > 120 and a["state"] != "offline"
        style = ("yellow" if stale else
                 {"idle": "green", "busy": "cyan"}.get(a["state"], "dim"))
        t.add_row(a["agent_id"],
                  a["state"] + (" (stale)" if stale else ""),
                  a["hostname"],
                  ",".join(a["queues"]) or "-",
                  ",".join(a["labels"]) or "-",
                  a["current_job_id"][:8] if a["current_job_id"] else "-",
                  f"{_fmt_age(a['seen_s'])} ago",
                  style=style)
    if not shown_agents:
        t.add_row("(none seen in the last day — use --all)",
                  *[""] * 6, style="dim")
    con.print("\n[bold]agents[/]")
    con.print(t)

    t = Table(box=box.SIMPLE_HEAD, pad_edge=False, header_style="bold dim")
    for col in ("queue", "queued", "oldest wait"):
        t.add_column(col)
    for name, q in sorted(queues.items()):
        t.add_row(name, str(q["depth"]), _fmt_age(q["oldest_s"]),
                  style="yellow" if q["oldest_s"] > 600 else None)
    if not queues:
        t.add_row("(nothing queued)", "", "", style="dim")
    con.print("\n[bold]queues[/]")
    con.print(t)

    t = Table(box=box.SIMPLE_HEAD, pad_edge=False, header_style="bold dim")
    t.add_column("job")
    t.add_column("tool", overflow="fold", min_width=10)
    t.add_column("state")
    t.add_column("att")
    t.add_column("agent", overflow="fold", min_width=14)
    for col in ("lease", "hb", "prog"):
        t.add_column(col)
    for j in active:
        hb = j["heartbeat_s"]
        hb_bad = hb is not None and hb > 60
        state = j["state"] + (" (cancel requested)"
                              if j.get("cancel_requested") else "")
        t.add_row(j["job_id"][:8], j["tool"], state,
                  f"{j['attempts']}/{j['max_attempts']}",
                  j["leased_by"] or "-",
                  _fmt_age(j["lease_remaining_s"]),
                  f"{_fmt_age(hb)} ago" if hb is not None else "-",
                  f"{j['progress']*100:.0f}%" if j.get("progress") else "-",
                  style=("red" if hb_bad else
                         "yellow" if j.get("cancel_requested") else None))
    if not active:
        t.add_row("(nothing running)", *[""] * 7, style="dim")
    con.print("\n[bold]running[/]")
    con.print(t)


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
