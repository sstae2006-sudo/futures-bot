# ROADMAP.md

Current priorities and forward-looking plans. Move finished items into
Completed; re-prioritize what's left as it changes. Don't let this
section sit empty and get guessed at — if a priority isn't listed
here, ask before assuming it matters.

## Current Priorities

**Critical**
- Importer reliability (turtle-data corruption in KNOWN_ISSUES.md
  ISSUE-001 resolved 2026-07-26 — see Completed; other import paths
  not separately audited)
- Startup reliability
- Dependency management
- Backend stability

**High**
- Walk-forward testing
- Monte Carlo
- Parameter robustness

**Medium**
- UX polish
- Dark mode
- Exports
- Fix `bars` schema drift (KNOWN_ISSUES.md ISSUE-004, needs explicit
  approval per CLAUDE.md section 8 — it's a schema change)

**Low**
- New strategies
- Fix `US80Z` genuine OHLC violations in raw source data
  (KNOWN_ISSUES.md ISSUE-005)

## Future Roadmap

Not fleshed out beyond the priorities above yet. This is intentional —
inventing multi-session plans nobody's actually committed to would be
worse than leaving this honestly thin. Add real plans here as they're
decided; a future Claude session should never have to guess what's on
this list.

## Completed

Major subsystems already built (see PROJECT_STATE.md "Completed
Features" for detail, CHANGELOG.md for the commits that brought them
into git history):

- Core framework: risk manager, paper broker, session/contract
  handling, decision journal.
- Backtest engine + reports; four reference strategies.
- Market-data sync pipeline (Massive contracts + flat-file APIs).
- Grid-search optimizer with walk-forward validation.
- ML research workstation (dataset/training/prediction).
- FastAPI research server + autonomous paper-trading layer.
- Tradovate live-broker adapter; trade-import/reconciliation pipeline.
- React research dashboard.
- Deploy tooling (Docker, docker-compose, systemd, bare-metal guide).
- Dependency/packaging fix so `pip install -e .` runs the API
  standalone (2026-07-26).
- Git history cleanup: ~121 untracked files organized into 6 real
  commits, `market_data*.db` and other large/local data gitignored
  (2026-07-26).
- Persistent documentation framework (`CLAUDE.md`, `PROJECT_STATE.md`,
  `CHANGELOG.md`, `KNOWN_ISSUES.md`, `ROADMAP.md`, `BOOT_CHECKLIST.md`)
  (2026-07-26).
- Turtle-data corruption in `market_data.db` diagnosed and repaired:
  century-pivot timestamp bug and a hardcoded `contract` placeholder,
  both fixed with regression test coverage (2026-07-26,
  KNOWN_ISSUES.md ISSUE-001).
- Permanent, read-only database validator (`--validate-db`) covering
  16 integrity classes, with 33 tests and `docs/DATABASE_VALIDATION.md`
  (2026-07-27). Surfaced two new findings on first run against the
  live database — see Medium/Low priorities above.
