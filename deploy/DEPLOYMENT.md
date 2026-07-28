# Deployment

Host-agnostic guide covering **two separate products** that live in this
repo. Deploy either, both, or neither — they don't share a container or a
process, though they can share a host and, if you want, a `MASSIVE_API_KEY`.

| | CLI live-trading bot | Research API / dashboard |
| --- | --- | --- |
| What it is | `futures_bot.cli --live` -- one strategy, one broker connection | FastAPI + React: backtests, optimizer, trade analysis, reports, the dashboard-controlled Live Session (paper only), the opt-in autonomous Research Server (Phases 6-8B) |
| Image / unit | `Dockerfile` / `futures-bot.service` | `Dockerfile.api` / `futures-bot-api.service` |
| Talks to a real broker | Yes, if `broker.name: tradovate` | Never -- `/api/live/*` and the Research Server are structurally paper-only, enforced at runtime, not just by convention |
| Has authentication | N/A (no network listener) | **No.** See that section below before running this anywhere but localhost |

Read `docs/USER_MANUAL.md`'s "Going live: read this first" section in full
before the CLI bot is anything other than `broker.name: paper` against
`TRADOVATE_ENV=demo` — the Tradovate adapter has **not** been exercised
against a real or demo account as of this writing, and IBKR is still not
implemented.

## Part 1 — CLI live-trading bot

### What this workload actually needs

Trading bots don't fit the shape most modern hosting assumes. Four requirements:

**1. A process that stays running.**
Not request-driven. Vercel, Netlify, Cloudflare Workers, and plain Lambda all
spin down between requests — fine for a web app, useless for something that has
to watch the market for six hours straight.

**2. A persistent disk.**

This is the one that bites people. `state/bot_state.json` holds the daily loss
tally and the kill-switch flag. On most container platforms the filesystem is
ephemeral by default: wiped on restart, redeploy, and host migration.

The failure is quiet and specific. The bot hits its daily loss limit and halts.
The container restarts for any ordinary reason. It comes back with no memory of
the halt and a full loss allowance, and loses the limit again the same day. The
file exists precisely to survive that, so it must be on a mounted volume.

Verify it. Write state, restart the host, confirm the file survived. Do this
before the first live session, not after.

**3. Automatic restart, with a ceiling.**
Restart on crash, but stop after repeated failures. A bot crash-looping against
a live broker is worse than one that is cleanly down — it can submit duplicate
orders and it produces no useful logs.

**4. Correct time.**
Session boundaries, the daily reset, and the force-flat deadline are all
time-derived. Run NTP. Set the container timezone explicitly rather than
inheriting whatever the host defaults to. The code resolves `America/Chicago`
itself, but a wrong system clock defeats that.

### Option A — VPS

Most control, real disk, no surprises. What most retail futures traders use.

```bash
sudo useradd --system --create-home --home-dir /opt/futures-bot bot
sudo -u bot git clone <your-repo> /opt/futures-bot
cd /opt/futures-bot
sudo -u bot python3 -m venv .venv
sudo -u bot .venv/bin/pip install -e .

sudo -u bot cp config.example.yaml config.yaml
sudo -u bot nano config.yaml

# Secrets, root-owned, not world-readable. MASSIVE_API_KEY is required
# (the live feed's credential); the TRADOVATE_* block is only needed if
# config.yaml's broker.name is 'tradovate' -- see brokers/tradovate.py's
# module docstring for what each variable is and the safety checklist to
# run through before TRADOVATE_ENV=live.
sudo mkdir -p /etc/futures-bot
sudo tee /etc/futures-bot/env > /dev/null <<'EOF'
MASSIVE_API_KEY=...
TRADOVATE_ENV=demo
TRADOVATE_USERNAME=...
TRADOVATE_PASSWORD=...
TRADOVATE_APP_ID=...
TRADOVATE_CLIENT_ID=...
TRADOVATE_CLIENT_SECRET=...
TRADOVATE_ACCOUNT_ID=...
EOF
sudo chmod 600 /etc/futures-bot/env

sudo cp deploy/futures-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now futures-bot
journalctl -u futures-bot -f
```

Pick a US region — latency to CME's Aurora IL matching engine is what matters,
and a bot running from Singapore will see meaningfully worse fills.

### Option B — Containers

```bash
cp config.example.yaml config.yaml   # edit it
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f
```

On Railway, Fly.io, Render, or similar, the compose file maps across directly:

| Compose | Platform equivalent |
| --- | --- |
| `volumes: bot-state:/data` | Attach a persistent volume mounted at `/data` |
| `restart: unless-stopped` | Restart policy: on-failure |
| `environment:` | The platform's secrets/env settings |
| `TZ=America/Chicago` | Same, as an env var |

The volume is the step that gets skipped. If your platform's dashboard doesn't
show a volume attached to this service, the kill switch will not survive a
restart.

Point `state_file` in `config.yaml` at the volume:

```yaml
state_file: /data/state/bot_state.json
logging:
  directory: /data/logs
```

### Secrets

Broker credentials go in the environment. Never in `config.yaml`, never in the
image, never committed. `.gitignore` already excludes `config.yaml` for this
reason; `.dockerignore` excludes it from the image too.

### Before the first live session

- [ ] `--check` passes with production settings
- [ ] State file confirmed on persistent storage, verified across a restart
- [ ] System clock synced; timezone explicit
- [ ] Restart policy set, with a failure ceiling
- [ ] Log rotation configured — per-decision logging fills a disk over months
- [ ] Alerting on process death, so a bot that stops is noticed the same day
- [ ] Kill switch tested end to end on the deployed host, not just locally
- [ ] Broker credentials in env, not in the repo
- [ ] A documented way to stop it in a hurry, tested at least once

That last item is worth taking literally. Know the command, know where the
broker's flatten-all button is, and have both to hand before there is money on
the line.

### Monitoring

At minimum, alert on the process dying during market hours. A bot that stops at
9:15am and is noticed at 4pm is a bot that missed a session — and if it stopped
while holding a position, only the broker-side stop was protecting it.

`logs/decisions.jsonl` is one JSON object per line, so it feeds into any log
collector without transformation.

## Part 2 — Research API / dashboard

The FastAPI backend and React frontend behind everything built since Phase 6:
backtests, the optimizer, trade analysis, reports, the dashboard-controlled
**Live Session** (paper-trading only, enforced at runtime — see
`api/live_session.py`'s module docstring), market-data sync, and the opt-in
**Research Server** (Phase 8B: autonomous paper trading across several
strategies plus nightly research jobs). Not a trading bot in the sense Part 1
is — it never talks to a real broker — but it is a persistent, stateful
service with two SQLite databases behind it, so it has real deployment
requirements of its own.

### No authentication — read this before anything else

**There is no login, no API key, no user account system in front of this
API.** Anyone who can reach the port can start/stop paper-trading sessions,
submit optimizer/backtest jobs, and read every stored trade and report. CORS
is wide open (`allow_origins=["*"]`) by design, for local development against
the Vite dev server on a different port — it is not a defense once this is
reachable from anywhere beyond localhost.

`python -m futures_bot.api` enforces this structurally: it refuses to bind
any host other than `127.0.0.1`/`::1`/`localhost` unless you pass
`--allow-network-exposure` (see `api/__main__.py`). That flag is a
confirmation, not a fix — it exists so a `0.0.0.0` bind can never happen by
accident, not so you can skip putting something real in front of the port.
Before this is reachable from anywhere but the machine it runs on, put a
reverse proxy (nginx, Caddy) in front that adds authentication, or restrict
access with a VPN/firewall — both options below default to loopback-only for
exactly this reason.

See `TEAM_DEPLOYMENT.md` for the one supported way to reach this API from
more than one machine today: a private Tailscale network as the access-
control boundary (Tailscale itself, not a reverse proxy) plus a shared
TimescaleDB/Postgres instance instead of per-developer SQLite files. It
does not add authentication either — it's an alternative to the VPN/
firewall option above, not a fix for the underlying "no login" fact.

### What this workload needs (different from Part 1)

- **A persistent disk** for `research.db` and `market_data.db` (SQLite,
  WAL mode) plus `logs/` and any autonomous paper-trading state files — same
  "verify it survives a restart" discipline as Part 1's kill-switch state.
- **Correct time**, for the same session-boundary reasons as Part 1.
- **Automatic restart**, though the ceiling matters less here than for a
  live-broker bot — a paper-trading dashboard crash-looping wastes CPU, it
  doesn't submit duplicate real orders.
- It does **not** need sub-second uptime guarantees the way a live session
  watching the market does; a five-minute outage overnight is an
  inconvenience here, not a missed stop-loss.

### Option A — VPS

```bash
sudo useradd --system --create-home --home-dir /opt/futures-bot bot
sudo -u bot git clone <your-repo> /opt/futures-bot
cd /opt/futures-bot
sudo -u bot python3 -m venv .venv
sudo -u bot .venv/bin/pip install -e .

sudo -u bot cp config.example.yaml config.yaml
sudo -u bot nano config.yaml   # set research_server.enabled if you want autonomous mode

# Build the dashboard. VITE_API_BASE_URL= (empty) bakes in relative API
# paths, so the built bundle works regardless of host/port -- see
# api/app.py's `_maybe_mount_frontend`, which serves this directory
# directly from the API process once FUTURES_BOT_FRONTEND_DIST points at it.
sudo -u bot bash -c 'cd frontend && npm ci && VITE_API_BASE_URL= npm run build'

sudo mkdir -p /etc/futures-bot
sudo tee /etc/futures-bot/env > /dev/null <<'EOF'
MASSIVE_API_KEY=...
EOF
sudo chmod 600 /etc/futures-bot/env

sudo mkdir -p /opt/futures-bot/data && sudo chown bot:bot /opt/futures-bot/data

sudo cp deploy/futures-bot-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now futures-bot-api
journalctl -u futures-bot-api -f
```

This binds `127.0.0.1:8000` by default (see the unit file). Reach it from a
browser by either tunneling (`ssh -L 8000:localhost:8000 user@host`) or
putting nginx/Caddy in front with real authentication.

### Option B — Containers

```bash
cp config.example.yaml config.yaml   # edit it; set research_server.enabled if wanted
docker compose -f deploy/docker-compose.yml up -d futures-bot-api
docker compose -f deploy/docker-compose.yml logs -f futures-bot-api
```

`Dockerfile.api` builds the frontend and the API into one image; the compose
service publishes it at `127.0.0.1:8000` on the host, same loopback-only
default as Option A and for the same reason. Change the `ports:` mapping in
`deploy/docker-compose.yml` only once a reverse proxy or VPN is actually in
front of it.

On Railway, Fly.io, Render, or similar, the same mapping from Part 1 applies:
the `bot-api-data` volume must be attached (both SQLite databases, logs, and
paper-trading state live there — nothing else on the container survives a
redeploy), and the platform's own secrets manager holds `MASSIVE_API_KEY`.

### Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `FUTURES_BOT_CONFIG` | Path to `config.yaml` | `config.yaml` |
| `FUTURES_BOT_FRONTEND_DIST` | Where the built dashboard (`npm run build`'s output) lives; omit to run API-only, dashboard-less | `frontend/dist` |
| `FUTURES_BOT_RESEARCH_DB` | Path to the trades/runs/jobs SQLite database | `research.db` in the working directory |
| `FUTURES_BOT_MARKET_DATA_DB` | Path to the synced-bars SQLite database | `market_data.db` in the working directory |
| `MASSIVE_API_KEY` | Market-data vendor credential; required for sync and the Research Server | none |

### Before exposing this beyond localhost

- [ ] A reverse proxy or VPN actually sits in front — `--allow-network-exposure`
      is not that layer, it only lets the process listen
- [ ] The proxy adds real authentication, not just a non-obvious URL
- [ ] `research.db`/`market_data.db` confirmed on persistent storage,
      verified across a restart (WAL mode means two files sit alongside each
      `.db` — `-wal` and `-shm` — make sure the volume covers the directory,
      not just the `.db` file itself)
- [ ] System clock synced; timezone explicit
- [ ] Restart policy set
- [ ] `MASSIVE_API_KEY` in env, not in the repo or the image
