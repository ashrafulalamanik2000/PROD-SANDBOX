# Console MVP plan (2026-08-28)

Derived from the Codex audit (`sdtools-audit-and-remediation-plan.md`, Part
III "Console/TUI"). Scope: operator experience on top of the EXISTING HTTP
API. The P0 backend rebuild (lease fencing, outbox, migrations, auth
scoping, updater) is a separate workstream; nothing here may silently
depend on it. Rendering uses Rich (already a Typer dependency) — no
Textual full-TUI in the MVP.

Rule: every command reads the same HTTP API the dashboard uses
(`/v1/jobs`, `/v1/agents`, `/v1/runs/{id}/events`, `/v1/health`).
No separate state model, no direct DB access from the console.

## Items, in build order

1. **Foundation: shared client + `--json` everywhere** — DONE 2026-08-28
   - `sdtools/apiclient.py`: one client used by all read commands
     (auth header, base URL, timeouts, error rendering).
   - `--json` on `jobs`, `agents`, `doctor`, `cancel`, `retry`, `submit`
     with stable field names (audit: script-friendly output).
2. **`sdtools status`** — DONE 2026-08-28 — one-screen fleet overview: API health + version,
   agents with last-seen freshness + current job, queue depth and
   oldest-queued age per queue (starvation signal, client-side from
   created_at), running jobs with heartbeat age, local spool backlog.
3. **`sdtools jobs --watch`** — DONE 2026-08-28 — Rich Live table, 2 s
   refresh: state (color), queue, tool, attempt n/m, owner, lease-expiry
   countdown, cancel-requested flag, age, error kind; timestamp header
   with active filters; API blips keep the last table with a red
   "retrying" banner; Ctrl+C exits. `--watch` and `--json` are mutually
   exclusive. Table renderer (_jobs_table) verified at full width against
   live data; interactive refresh is standard Rich Live.
4. **`sdtools watch <job>` + `submit --watch`** — DONE 2026-08-28 —
   single-job live follow (_follow_job): styled state header w/ attempt +
   agent + cancel flag, lease countdown + heartbeat age (red >60 s),
   Rich progress bar + note, rolling event tail (::sdtools:: sentinels
   filtered — the bar shows them decoded; error/warning colored), event
   cursor resets on attempt change, API blips tolerated, Ctrl+C detaches
   (exit 130) without killing the job, terminal summary + exit code
   0/1 by state. New additive endpoint: GET /v1/jobs/{job_id}.
   Verified: submit --watch on a live soak -> event tail + `-> ok`,
   exit 0.
5. **`sdtools logs <run|job> --follow`** — DONE 2026-08-28 — rewrote the
   local-only `logs`: prefix resolution across jobs AND runs (ambiguity
   rejected), API event streaming with paging (after_seq/level already
   existed server-side), `--follow` polls to terminal state and prints
   `-- run <status> --` (exit 0/1), `--errors` (error+warning), `--tail N`,
   `--json` (JSON-lines), `--raw` keeps ::sdtools:: sentinels (hidden by
   default in human mode), local NDJSON fallback when API is off/unknown.
   Verified: job-id tail on live pole-vec, --errors on a failed run,
   --json, offline fallback via SDTOOLS_OFFLINE, --follow to `-- run ok --`.
6. **`sdtools agent status`** — DONE 2026-08-28 — optional positional
   action on the existing `agent` command (worker invocation unchanged —
   ML04's scheduled task is safe; unknown actions rejected with a hint).
   Local block: machine id, cli version, tool count, API key SCOPE
   (derived from the sdt_XXXX_ prefix; red warning when it can't work
   jobs), spool backlog, staging dir existence + free GB (yellow <50 GB,
   red missing, yellow unset). Fleet block: registered-agents table with
   a ● "here" marker for this machine, sorted this-machine-first;
   degrades gracefully when the API is unreachable or the key can't read
   agents. --json emits {local, agents, note}.
7. **Console-local audit fixes** — DONE 2026-08-28
   - TEL-05: flush_spool keeps 408/429 (break: throttled, try later) and
     404 (continue: run-start dependency not landed yet, keep file) —
     only other 4xx are dropped as permanent. Live _send already
     exempted 408/429.
   - INST-09 (lite): CLI_VERSION now read from installed package
     metadata (pyproject is the single source; source-tree fallback
     reads pyproject directly). pyproject bumped to 0.5.0 — telemetry
     confirms runs now record cli_version 0.5.0.
     GOTCHA: pyproject.toml must stay BOM-FREE — a UTF-8 BOM breaks
     setuptools' tomllib and the whole build.
   - `--json` on submit/cancel/retry: landed in item 1.

## MVP COMPLETE (7/7) — 2026-08-28, all local, awaiting "ship"

## Explicitly out of MVP

- `update status` / rollback (needs INST-02/06 immutable releases).
- Textual TUI, dashboards, ETA estimation (audit Part III ETA design).
- Any lease/attempt-fencing display guarantees — the console shows what
  the API believes; correctness of that belief is Phase 1 backend work.

## Estimate

~5–6 working days total; each item independently shippable; item 1 is
the only hard prerequisite for the rest. Ships only on explicit "ship"
(batching rule) — local testing via `install.ps1` reinstalls is fine.

## Progress log

- 2026-08-28: plan created; item 1 started.
- 2026-08-28: item 1 done — sdtools/apiclient.py (client/fetch/post/
  emit_json); --json on jobs, agents, doctor, submit, cancel, retry;
  doctor also reports staging_dir and manifest errors in JSON. Verified
  live: doctor/agents/jobs --json parse cleanly; submit->cancel->retry->
  cancel round-trip via --json job ids. Local only (not shipped).
- 2026-08-28: item 2 done — `sdtools status` (+ --json, --all): api health,
  agents w/ freshness + stale highlighting (offline >1d hidden by default),
  queue depth + oldest-queued age, running jobs w/ attempt, lease countdown,
  heartbeat age, progress %, cancel flag; local spool backlog. Additive API
  fields: runs._run_dict gains last_heartbeat_at, jobs._job_dict gains
  lease_expires_at. Verified live with a running soak (lease 104s, hb 1s,
  37%). NOTE for the ship: agent must be STOPPED during console reinstall
  (uv tool dir lock breaks the install — same as ML04).
