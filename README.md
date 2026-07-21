# Futures Trading Bot — Phase 1 Framework

Python framework for trading a single Micro E-mini contract. This is the
strategy-agnostic half of the Phase 1 spec: risk controls, session handling,
order lifecycle, paper trading, and decision logging.

## Status

**Built and tested**

- Contract specs (MES/MNQ/M2K/MYM) with correct tick and point values
- CME session arithmetic — trade dates, maintenance halt, weekend closure
- Risk manager: daily loss kill switch, trade cap, trading-hours filter, force-flat
- Durable state — the kill switch survives a restart
- Paper broker with adverse slippage and conservative fill resolution
- Structured decision journal (every decision, not just trades)
- Validated settings file with risk warnings in dollars
- Engine wiring strategy → risk → broker
- 16 tests covering the risk logic

**Not built yet**

- The actual strategy — still needs mechanical rules from the client
- Broker adapters (Tradovate / IBKR) — blocked on which broker
- Backtest runner over historical CSV
- Live data feed

## Setup

```bash
pip install -e ".[dev]"
cp config.example.yaml config.yaml
```

Validate settings and see the risk profile in dollars:

```bash
python -m futures_bot.cli --config config.yaml --check
```

Run the engine on synthetic bars to confirm wiring:

```bash
python -m futures_bot.cli --config config.yaml --demo
```

Tests:

```bash
pytest
```

## Plugging in the real strategy

Subclass `Strategy`, register it, point the config at it:

```python
from futures_bot.strategy.base import Strategy, StrategyRegistry

@StrategyRegistry.register("client_strategy")
class ClientStrategy(Strategy):
    warmup_bars = 20

    def on_bar(self, bars, position):
        if position is None and some_condition(bars):
            return self.enter_long("Condition met: ...")
        return self.hold("No setup.")
```

The strategy only decides. It never places orders, sizes positions, or checks
the clock — the engine does that after the risk manager approves. A strategy
that could reach the broker directly could also bypass the kill switch.

Two rules: never index past `bars[-1]` (that's lookahead bias, and it makes a
backtest profitable in ways that don't survive contact with a live market), and
always give a reason, including on holds.

`strategy/ema_crossover.py` is a working reference implementation, not the
client's strategy. It exists so the framework can be exercised end to end.

## Design decisions worth knowing

**Decimal, not float.** Futures P&L is exact tick arithmetic. Float drift is
the difference between "stop hit" and "stop missed".

**Session dates aren't calendar dates.** CME equity index futures run 17:00 CT
to 16:00 CT the next day. A position opened 18:00 Monday belongs to Tuesday's
session. Keying the daily loss limit on the calendar date would reset it at
midnight — mid-session, right after a bad evening.

**The kill switch persists to disk.** A bot that hits its limit, crashes, and
restarts with a clean slate has a speed bump, not a kill switch. There's a test
for exactly this.

**A corrupt state file refuses to start.** Starting fresh would silently
discard an active halt.

**Stops rest at the broker, not in memory.** If this process dies, the
protective order survives it. Any adapter that can't place a broker-side stop
should raise rather than quietly fall back.

**Ambiguous bars resolve against you.** When a bar's range covers both stop and
target, OHLC can't say which came first. This assumes the stop. Assuming the
target is the most common way a backtest reports profits that never appear.

**Slippage is always adverse.** Every market fill is pushed against the
position.

## Open questions for the client

1. **What are the actual rules?** "Strategy based on ES livestream signals"
   isn't implementable. Need entry trigger, stop placement, target, and what
   invalidates a setup — stated as rules a computer follows without judgment.
   If the signals come from a human talking on a stream, that's a different
   (and much harder) project than indicator-derived signals, and it can't be
   backtested, which conflicts with the roadmap.

2. **Which broker?** The architecture supports any of them, but one concrete
   target is needed to build and test against. Also worth checking whether the
   account is with a prop firm — several prohibit full automation outright.

3. **Account size.** At an S&P around 7,500, one MES is roughly $37,500
   notional and $5/point. On $200, a 40-point move is the whole account, and
   40 points is an ordinary session. Run `--check` with the real numbers: at
   $200 with a 10-point stop, the tool reports 25% of the account at risk per
   trade and a kill switch that fires on the first loss. The risk controls in
   the spec stop functioning at that size.
