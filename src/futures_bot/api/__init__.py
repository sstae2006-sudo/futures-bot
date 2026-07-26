"""Phase 6A: the research intelligence HTTP API.

A thin, read-mostly REST layer over the existing `backtest`/`research`
packages -- everything here calls into code that already exists and is
already tested (`backtest.runner.run_backtest`, `research.optimizer.
run_optimization`, `research.comparison.compare_strategies`, ...) rather
than reimplementing any of it. Nothing in this package can place a live
order or touch a real broker: `brokers.tradovate` and `feeds.massive` are
not imported anywhere under `api/`, on purpose -- see docs/RESEARCH_INTERFACE.md.

Layout:

* `store.py` -- one shared `TradeStore` (SQLite) instance, the backing
  store for run/report history the dashboard reads.
* `introspection.py` -- reads each registered `Strategy` subclass's
  constructor signature to build a parameter schema for the frontend's
  backtest launcher, so adding a strategy parameter never requires a
  matching edit here.
* `schemas.py` -- Pydantic request/response models. Deliberately separate
  from `futures_bot.models`/`futures_bot.config`: those are the trading
  domain's own types, and coupling the API's wire format to them directly
  would mean an internal refactor could silently break API clients.
* `services.py` -- the actual work (run a backtest, run an optimization,
  generate a report), independently callable and tested without going
  through HTTP.
* `routes/` -- one FastAPI router per resource area; thin, calling
  `services.py`.
* `app.py` -- assembles the FastAPI app.
"""

from __future__ import annotations
