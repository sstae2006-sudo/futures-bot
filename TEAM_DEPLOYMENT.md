# Team Deployment

Covers the **team mode** deployment path: several developers on different
machines, connected over a private [Tailscale](https://tailscale.com)
network, sharing one backend process and one TimescaleDB/Postgres database
instead of each running their own SQLite files locally. Explicitly **no
authentication and no exposure beyond the Tailscale network** — Tailscale
itself is the access-control boundary, the same reasoning
`deploy/DEPLOYMENT.md`'s "No authentication" section already documents for
every other deployment option.

This is additive, not a replacement: `scripts\start.ps1` (single developer,
local SQLite, separate Vite dev server) keeps working exactly as before,
completely unaffected by anything below. Read that doc, `BOOT_CHECKLIST.md`,
and `docs/ARCHITECTURE.md`'s PERSISTENCE section first if you haven't —
this doc only covers what's different about team mode.

## How this works, in one paragraph

`FUTURES_BOT_DATABASE_URL` (unset by default) is the single switch: unset,
every store class is the SQLite file it's always been, and the whole app
behaves exactly as documented everywhere else. Set it to a Postgres DSN,
and `market_data/store.py::get_market_data_store()` /
`api/store.py::get_store()` transparently swap in `PgMarketDataStore` /
`PgTradeStore` instead — every route, the CLI, the scheduler, every
`research_server` background thread goes through those two factory
functions already, so nothing else in the codebase needs to know or care
which database is actually behind it. `scripts\start-team.ps1` is the one
new entry point: it builds the frontend once and starts a single backend
process bound to this machine's Tailscale address (`--allow-network-exposure`,
serving the built dashboard from the same process/port — no separate Vite
server, no new CORS surface).

## Server setup

One machine (a small VPS, or any always-on box on the tailnet) hosts the
shared database and, typically, the backend too — though they don't have to
be the same machine, since the DSN is just a normal Postgres connection
string.

```bash
# 1. Install Docker (for TimescaleDB) and Tailscale on the server.
#    https://docs.docker.com/engine/install/
#    https://tailscale.com/download

# 2. Join the tailnet.
sudo tailscale up
tailscale ip -4   # note this -- every developer's FUTURES_BOT_DATABASE_URL
                  # and the URL they browse to both point at it

# 3. Clone the repo and bring up TimescaleDB.
git clone <your-repo> /opt/futures-bot
cd /opt/futures-bot
export TIMESCALEDB_PASSWORD='<a real password -- do not use the compose file default>'
docker compose -f deploy/docker-compose.yml up -d timescaledb
docker compose -f deploy/docker-compose.yml ps timescaledb   # wait for "healthy"
```

`deploy/docker-compose.yml`'s `timescaledb` service binds
`127.0.0.1:5432` on the host by default — reachable from this machine only.
Once Tailscale is confirmed working, either leave it loopback-only and run
the backend on this same machine (simplest, and what this doc assumes below),
or change the port mapping to bind the Tailscale interface if the database
and backend need to live on different boxes. Do **not** map it to `0.0.0.0`
— Tailscale is the only intended access path, same posture the API itself
already enforces via `api/__main__.py`'s network-exposure guard.

### Running the schema migration

The Postgres/TimescaleDB schema is managed by Alembic (`alembic/`), not
`CREATE TABLE IF NOT EXISTS` — a bad migration must never silently apply
itself against a shared team database, so this is always a deliberate,
manual step:

```bash
export FUTURES_BOT_DATABASE_URL="postgresql+psycopg://futures_bot:${TIMESCALEDB_PASSWORD}@127.0.0.1:5432/futures_bot"
pip install -e ".[db]"
alembic upgrade head
```

This creates all of `market_data.db`'s and `research.db`'s tables and
converts `bars` into a TimescaleDB hypertable. Safe to re-run — Alembic
tracks the applied revision in its own `alembic_version` table and only
applies what's new.

### Migrating existing SQLite data (optional)

If there's existing `market_data.db`/`research.db` data to bring over
(rather than starting the shared database empty), see
`tools/migrate_to_timescaledb.py`:

```bash
python tools/migrate_to_timescaledb.py --dry-run     # prints what it would do, writes nothing
python tools/migrate_to_timescaledb.py --yes         # actually migrates, verifies row counts match
```

Reads through the existing `MarketDataStore`/`TradeStore` methods and writes
through `PgMarketDataStore`/`PgTradeStore` — batched, safe to re-run
(`ON CONFLICT DO NOTHING` on the Postgres side), and finishes with a
source-vs-destination row-count check. Point `FUTURES_BOT_MARKET_DATA_DB` /
`FUTURES_BOT_RESEARCH_DB` at the SQLite files to migrate from (defaults:
`market_data.db` / `research.db` in the working directory) and
`FUTURES_BOT_DATABASE_URL` at the destination before running it. Back up
both `.db` files first regardless — this only ever reads from them, but
"the migration only reads" is not the same guarantee as "verified by a
backup," and this project's own history
(`docs/DATABASE_CORRUPTION_REPORT.md`) is exactly why that discipline
exists.

### Starting the backend

```bash
export FUTURES_BOT_DATABASE_URL="postgresql+psycopg://futures_bot:${TIMESCALEDB_PASSWORD}@127.0.0.1:5432/futures_bot"
pwsh scripts/start-team.ps1
```

(`start-team.ps1` is PowerShell — on a Linux server, follow the same steps
it automates by hand: `cd frontend && npm ci && VITE_API_BASE_URL= npm run
build`, then `python -m futures_bot.api --host $(tailscale ip -4) --port
8000 --allow-network-exposure` with `FUTURES_BOT_FRONTEND_DIST=frontend/dist`
in the environment. A `start-team.sh` port of the script is a reasonable
future addition if the server runs Linux — not written yet, since this
project's own dev environment is Windows.)

For a server that should keep the backend running across reboots, wrap the
same command in a systemd unit (`deploy/futures-bot-api.service` is the
template — copy it, add the `FUTURES_BOT_DATABASE_URL` line under
`EnvironmentFile=` or `Environment=`, change `ExecStart`'s `--host`/`--port`
to match, same as any other `EnvironmentFile=`-based secret in that unit).

### Backups

```bash
python tools/backup_timescaledb.py
```

Requires the Postgres client tools (`pg_dump`, matching the server's major
version) on whatever machine runs this — install via your OS package
manager (e.g. `apt install postgresql-client`). Writes a timestamped
`pg_dump -Fc` dump to `db_backups/` (gitignored) and a `last_backup.json`
marker `/api/system/health` reads. Run this on a schedule (cron / Windows
Task Scheduler) — it isn't triggered automatically by anything in this
codebase.

## Tailscale setup

Every developer needs the Tailscale client installed and signed into the
same tailnet as the server — see https://tailscale.com/download. Verify
with:

```bash
tailscale status
```

Nothing else is required on the client side; Tailscale handles the
networking (including NAT traversal) transparently once both machines show
up in `tailscale status`.

## Developer onboarding

For each new developer joining the team deployment:

1. Install Tailscale, join the tailnet (an admin invites them via the
   Tailscale admin console).
2. `git clone` the repo, `pip install -e .` (the `db` extra is **not**
   needed on a developer machine that only talks to the already-running
   shared backend over HTTP — only the server needs it).
3. Get the shared backend's URL from whoever set up the server
   (`http://<server-tailscale-ip>:8000`) and browse to it directly — the
   dashboard is served from that one process, there's no separate frontend
   to run.
4. That's it. There's no per-developer database, no local sync step, no
   account to create (see "No authentication," inherited from
   `deploy/DEPLOYMENT.md`) — anyone who can reach the URL over the tailnet
   has full access, same as any other team-deployment client.

### Connecting a new developer's machine to the database directly (research/scripting only)

Only needed for someone running the CLI (`futures_bot.cli`) or a script
directly against the shared database, rather than just using the dashboard
through the running backend:

```bash
export FUTURES_BOT_DATABASE_URL="postgresql+psycopg://futures_bot:<password>@<server-tailscale-ip>:5432/futures_bot"
pip install -e ".[db]"
```

This is the real, final check the approved plan for this feature flagged as
unverifiable from a single-machine development sandbox: an actual second
device joining the tailnet and reaching the shared backend from a different
network. Everything else in this doc has been verified against a local
TimescaleDB instance; confirming a genuinely separate machine works this way
is the one step that needs a second device to actually try.

## Updating the server

```bash
cd /opt/futures-bot
git pull
pip install -e ".[db]"
alembic upgrade head          # run this before restarting the backend, not after --
                               # a schema change must land before the new code expects it
scripts/stop.ps1              # or however the process was started -- stop it
pwsh scripts/start-team.ps1   # rebuilds the frontend and restarts
```

`alembic upgrade head` is always a manual, reviewed step — never automatic
on boot (see "Running the schema migration" above for why). Check
`alembic/versions/` for what a given update actually changes before running
it against a shared database with real team data in it.

## Troubleshooting

**`alembic upgrade head` fails with a connection error.**
Confirm `FUTURES_BOT_DATABASE_URL` is set and correct in the current shell
(it's read by `alembic/env.py`, not `alembic.ini` — the DSN is never
written to a tracked file). Confirm the `timescaledb` service is up and
healthy: `docker compose -f deploy/docker-compose.yml ps timescaledb`.

**`scripts\start-team.ps1` fails at "Tailscale CLI not found."**
Install Tailscale and make sure `tailscale` is on `PATH`, or pass
`-HostAddress <ip>` explicitly to skip auto-detection.

**The dashboard loads but shows no data / errors on every page.**
Check `/api/system/health` or `/api/system/overview` directly in a browser; check the backend
log (`.startup/team-backend.err.log`) for a database connection error.
Confirm `alembic upgrade head` was actually run against this database
before the backend's first boot.

**A developer's machine can reach the dashboard but the CLI/a script
against `FUTURES_BOT_DATABASE_URL` times out.**
The dashboard works over HTTP (port 8000) — a direct database connection
needs port 5432 reachable too. Confirm the `timescaledb` service's port
mapping actually exposes it to the tailnet interface, not just
`127.0.0.1`, on the server (see "Server setup" above).

**Someone needs to roll back a bad migration.**
`alembic downgrade -1` reverts the most recent revision — read what it
actually drops first (`alembic/versions/<revision>.py`'s `downgrade()`
function) before running it against real data; consider a fresh
`tools/backup_timescaledb.py` run beforehand regardless.
