"""Phase 8B: the autonomous research server.

Ties together everything Phases 1-8A already built into one thing that
runs continuously with no one clicking buttons: `market_data.scheduler`
keeps the local database current, `paper_trader.AutonomousPaperTrader`
paper-trades several strategies at once against it, `nightly_jobs`
submits research through the existing `api.jobs` system on a schedule,
and `insights` surfaces (never applies) findings about drift and better
parameters. `orchestrator.ResearchServer` composes all of it; `api/app.py`
starts it on boot only if `Settings.research_server.enabled` is true.
"""

from __future__ import annotations
