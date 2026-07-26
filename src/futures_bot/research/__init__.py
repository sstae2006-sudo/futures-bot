"""Phase 3: the research intelligence layer.

Everything here runs *after* a backtest (or a batch of them) and answers
questions about results the core `backtest` package already produced --
which trades happened and why, which parameters hold up out-of-sample, how
strategies compare, and where a result should not be trusted. Nothing in
this package can place an order, touch `engine.py`, or change what any
strategy decides; it only reads what `backtest.runner.run_backtest` already
returns and journals.
"""

from __future__ import annotations
