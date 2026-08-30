# sdtools dispatcher API runbook

Run commands from `C:\sdtools` in PowerShell. The deployment uses Docker
Compose, publishes the API on port 8080, and persists Postgres in the named
`pgdata` volume.

## First deployment and routine operation

Before the first start, replace `POSTGRES_PASSWORD=CHANGE_ME` and fill the blank
`ANTHROPIC_API_KEY` in `.env`. The API uses deterministic fallback summaries
while the Anthropic key is blank. Keep `.env` local; it is gitignored and must
never be added to an installer payload.

```powershell
Set-Location C:\sdtools
docker compose up -d --build
docker compose ps
docker compose logs api
```

Both services use `restart: unless-stopped`, so Docker restarts them after a
host or Docker Desktop restart. An intentional `docker compose stop` remains
stopped until explicitly started again.

Dispatcher leases default to 120 seconds and the sweeper defaults to 30
seconds. They can be overridden with `LEASE_TTL_S` and `SWEEP_INTERVAL_S` for
controlled recovery testing; leave the production defaults unless operational
requirements justify a change.

```powershell
# Stop without deleting containers or the database volume
docker compose stop

# Start existing containers
docker compose start

# Recreate after an image/configuration change
docker compose up -d --build

# Follow API logs
docker compose logs -f api
```

Do not use `docker compose down -v` unless permanent deletion of the database
volume is intended.

## Health check

```powershell
$health = Invoke-WebRequest http://127.0.0.1:8080/v1/health -UseBasicParsing
$health.StatusCode
$health.Content
```

Expected: HTTP 200 and JSON containing `"ok":true`.

## Mint API keys

On the first boot of an empty database, the API prints a bootstrap admin key
once. Retrieve it from the initial API logs and keep it out of shell history and
tracked files:

```powershell
docker compose logs api
$adminKey = Read-Host 'Bootstrap admin key'
```

Mint with the admin endpoint. The returned `key` value is shown only in that
response; capture it in a password manager or provision it immediately on its
target machine.

```powershell
$headers = @{ Authorization = "Bearer $adminKey" }
$body = @{ name = 'operator-laptop'; scope = 'submit' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/v1/keys -Headers $headers -ContentType 'application/json' -Body $body
```

Supported scopes are `submit` (submit and read queue state), `ingest` (run
telemetry plus agent poll/lease/finish), `read` (dashboard), and `admin`. The
dispatcher contract intentionally uses `ingest` for worker agents; use distinct
ingest keys for a worker and for telemetry-only clients so either credential can
be revoked independently.

Configure machine credentials in `%USERPROFILE%\.sdtools\config.toml` or the
`SDTOOLS_API_KEY` environment variable. The fleet `C:\sdtools\config.toml` may
contain `api.url`, but must never contain a key.

## Nightly Postgres backup

Create a local backup directory once, then schedule the following PowerShell
command with Task Scheduler. It writes a portable plain-SQL dump without
embedding the database password in the command line.

```powershell
New-Item -ItemType Directory -Force C:\sdtools-backups | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
docker compose -f C:\sdtools\docker-compose.yml exec -T db pg_dump -U sdtools -d sdtools --clean --if-exists | Out-File -Encoding utf8 "C:\sdtools-backups\sdtools-$stamp.sql"
```

Periodically test restores into a disposable database; a backup is not proven
until it has been restored successfully.

## Reference dashboard CORS

`.env` allows the dashboard when served on port 3000 from localhost,
127.0.0.1, or this host's MagicDNS hostname. Serve `dashboard\` on one
of those origins and update `CORS_ORIGINS` if the final dashboard host differs.

## Deployment note

The production schema's `key_scope` enum includes `submit`. This is required by
the dispatcher contract and matches the existing API authorization logic. The
Compose API process intentionally uses one Uvicorn worker because the sweeper,
summary worker, and first-boot key creation currently run inside that process;
multiple Uvicorn workers would duplicate those background loops.

The schema also enables PostgreSQL's bundled `pg_trgm` extension before creating
the command-line trigram index. Earlier schema copies omitted that enablement and
failed first-boot initialization at `runs_cmdline_trgm`.

`api/requirements.txt` explicitly installs `httpx`, which is imported by the
optional Plane integration during API startup. Earlier images crash-looped at
import time when that indirect dependency was absent.

The SQLAlchemy `ApiKey.scope` model uses the same native `key_scope` enum as the
production DDL. Earlier code declared it as `VARCHAR`, causing bootstrap-key
inserts to fail against Postgres even though SQLite development tests passed.
The `Run.status` model is likewise aligned with the native `run_status` enum;
without that mapping, even `/v1/health` failed when comparing run state.

The SQLAlchemy run-id columns use the same native `uuid` type as the
production DDL (via `Uuid(as_uuid=False)`, plain strings on SQLite), and
`Run.tags` uses native `text[]`. Earlier code declared both as
VARCHAR/JSON, so every telemetry write 500-failed against Postgres
(`operator does not exist: uuid = character varying`, then
`column "tags" is of type text[]`) while SQLite development tests passed.

Telemetry writes (`POST /v1/runs`, events, artifacts) accept `submit`-scoped
keys as well as `ingest`: operators run tools locally too, and their runs
must upload with the one key their machine holds. `sdtools flush` reports
permanently rejected (4xx) spool items as DROPPED instead of counting them
as uploaded.

## Cancelling and retrying jobs

`sdtools cancel <job-id-prefix>` cancels a job. Queued/blocked jobs settle
`cancelled` immediately. Leased/running jobs are cancelled cooperatively: the
API sets `cancel_requested`, the worker sees it on its next lease renewal
(about LEASE_TTL_S/3, ~40 s at defaults), kills the tool's process tree, and
reports `cancelled` — which never retries, even mid-attempts. The lease
renewal doubles as this signal channel, and an agent whose lease has moved on
now also stops its tool instead of duplicating the rescuer's work.

`sdtools retry <job-id-prefix>` re-queues a `failed` or `cancelled` job under
the same job id with a fresh attempt counter. Jobs belonging to a workflow
cannot be retried individually; resubmit the workflow.

Caveat: cooperative cancel kills the process tree on the worker, but a
`docker run` client's container keeps running server-side — stage1/pole-vec
GPU containers must be stopped with `docker stop` on the worker if the cancel
lands mid-container.

`submit`-scoped keys may drive the worker/agent endpoints as well (local/dev
agents on an operator machine); `ingest` keys remain worker/write-only.

## Staging data to and from workers

Jobs can carry a staging spec so the worker copies inputs from a share to its
local drive, processes locally, delivers results, and cleans up:

```powershell
sdtools submit classification --queue gpu --require gpu `
  --stage-in  '\\SDAI-FS1\Production\...\Cambridge_P3_60::data_root' `
  --stage-out 'Lidar\Classified::\\SDAI-FS1\Production\...\Results\Cambridge_P3_60' `
  -P epsg=26917 -P las_pattern=*_final_classified.las
```

* `--stage-in SRC::PARAM` (repeatable): the worker copies SRC — a path IT can
  reach, typically a NAS UNC — into `<staging dir>\<job id>\` and sets tool
  param PARAM to the local copy. The submitting machine never touches SRC.
* `--stage-out REL::DEST` (repeatable): after an `ok` run the worker copies
  REL (resolved against the first staged-in folder; `.` = all of it) to DEST.
* `--stage-keep`: keep the worker-local copy. Default: deleted after a
  successful stage-out; always kept when the run fails (for debugging).

Each worker sets its scratch location once in `%USERPROFILE%\.sdtools\
config.toml`:

```toml
[staging]
dir = "D:\\sdai_scratch"
```

A staged job on a worker with no `[staging]` dir fails fast with
`StagingFailed`. Copies use robocopy (additive, `/E`, 2 retries); stage-in,
the run, and stage-out all happen inside the lease-renewal window, so long
copies do not lose the lease. Cancel takes effect between stages and during
the run (not mid-robocopy).

## Workers time out after the API host sleeps

Symptom: every worker logs `poll failed (timed out); retrying` while
`/v1/health` works fine ON the API host (both via 127.0.0.1 and via its own
MagicDNS name — the latter loops back internally and proves nothing about
real inbound traffic).

Cause: after the Windows host sleeps/wakes, Docker Desktop's published-port
proxy can keep serving loopback but drop connections from external
interfaces, including the Tailscale interface the fleet uses.

Fix: `docker compose restart api` on the API host. Workers recover on their
next poll retry automatically. This is a standing argument for promoting the
API to an always-on host that never sleeps (handoff step 6); until then,
disable sleep-on-lid-close or restart the api container after each wake.

## Docker Desktop startup failure on Windows build 26200

Docker Desktop 4.81 can crash before its engine starts with an inaccessible
`dockerInference` or `docker-secrets-engine\engine.sock` listener on Windows
build 26200. Do not use **Reset to factory defaults**, because that can remove
Docker state. The recovery sequence used on this host was:

1. Reboot Windows and do not launch Docker Desktop yet.
2. Rename `%LOCALAPPDATA%\Docker\run` and
   `%LOCALAPPDATA%\docker-secrets-engine` to timestamped `.stale-*` names.
3. Create fresh empty directories at the two original paths.
4. Launch Docker Desktop, then confirm `docker info` returns a server version.

The stale directories are retained for recovery evidence; they are not part of
the sdtools database volume. This is a Docker Desktop/Windows AF_UNIX issue, not
an sdtools API or dispatcher failure.
