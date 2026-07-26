# Universal Client Trade Importer (Phase 10.1)

Imports a client's trade history from a broker export -- Tradovate, NinjaTrader,
generic CSV, or Excel -- and reconstructs closed round-trip trades into the same
`trades` table every backtest/live/paper session already writes to. Imported
trades show up in Trade Explorer immediately, with no changes to that page:
they're written with `strategy = "import:<client-profile-name>"`, which slots
straight into Trade Explorer's existing strategy filter.

## What it expects

**Raw fill/execution records, not a broker's own pre-closed "Trades" report.**
One row per fill (a buy or a sell), not one row per finished trade. This
importer reconstructs the round trips itself via FIFO position matching --
see "How FIFO matching works" below. If your export is already a closed-trade
report (entry price, exit price, and P&L all on one row), it will still parse,
but each row will look like a same-quantity open immediately followed by a
close; matching it as fills is unnecessary in that case but harmless.

Canonical fields the importer needs, mapped from your file's actual column
names (dropdown wizard, always shown and always editable, regardless of how
confidently the format was recognized):

| Field | Required | Notes |
|---|---|---|
| `timestamp` | yes | Fill/execution time |
| `symbol` | yes | Contract/instrument, e.g. `MESZ5` |
| `side` | yes | Buy/Sell/B/S/Long/Short/SellShort/BuyToCover -- normalized automatically |
| `quantity` | yes | Must be positive |
| `price` | yes | Fill price |
| `commission` | no | Defaults to 0 if not mapped |
| `realized_pnl` | no | Used directly (proportionally split) when present -- see P&L below |
| `account` | no | Only used to warn if a file mixes more than one account |
| `fill_id` | no | Used as the duplicate-detection key when present (else a hash of symbol+time+side+qty+price) |

## Format detection

Tradovate and NinjaTrader are detected by matching a file's header row against
the commonly published column names for each platform's fill/execution
export. **This has not been verified against a live account export from
either platform** (same honesty as `brokers/tradovate.py`'s own docstring) --
detection is a starting point, not a guarantee. The column-mapping wizard is
always shown and always editable, whether or not a format was recognized.
Anything that doesn't match either fingerprint falls back to `"generic"`,
with a fuzzy best-guess mapping (matching header substrings like "qty",
"px", "side", "time") that you confirm or correct before importing.

## The upload -> preview -> confirm flow

1. **Upload**: pick a client profile and a file. The file is parsed
   synchronously (fast enough not to need a background job) and a full
   preview is computed: detected format, suggested column mapping, mapped
   fill rows, resulting trades, row-level validation errors, and duplicate
   counts -- all without writing anything.
2. **Adjust the mapping** if anything looks wrong, then **Confirm**. This
   submits the actual commit through the same background job system every
   backtest/optimizer/training run already uses -- you get live progress and
   a job you can watch to completion.
3. On completion, an **Import History** entry is created (filename, format,
   row/duplicate/error counts, trades created) and the resulting trades are
   immediately visible in Trade Explorer, filtered to `import:<profile>`.

## How FIFO matching works

Fills are processed in chronological order, per symbol, against a **FIFO
queue of currently open lots for that client profile** (persisted between
imports -- see "Cross-import behavior" below):

- A fill in the **same direction** as the open position extends it (a new
  open lot).
- A fill in the **opposite direction** consumes open lots oldest-first. A
  fill that spans multiple lots at different entry prices produces one
  closed trade per lot, each keeping its own real entry price and time --
  never averaged together.
- A closing fill **larger than the whole open position reverses it**: the
  excess quantity becomes a new open lot in the new direction.
- **Commission** is split proportionally across whichever trades a fill's
  quantity was divided into.

### P&L

Three fallback levels, most-honest-first:

1. **Broker-reported**: if the file's mapped `realized_pnl` column has a
   value on the closing fill, it's used directly (split proportionally
   across the lots that fill closed). Tagged `pnl_basis: "broker_reported"`.
2. **Computed from contract**: if not, and the symbol (after stripping a
   trailing month/year code like `Z5`/`H26`) matches one of this project's
   configured contracts (`MES`, `MNQ`, `M2K`, `MYM` -- see `contracts.py`),
   P&L is computed from the price difference times that contract's point
   value. Tagged `pnl_basis: "computed_from_contract"`.
3. **Points only**: if the contract isn't recognized, P&L falls back to a
   raw point difference with **no dollar multiplier applied** -- flagged
   `pnl_basis: "points_only_unknown_contract"` in the trade's metadata so
   this is never mistaken for a real dollar figure.

Every generated trade's `entry_metadata` also includes the **complete
original raw row** for both the opening and closing fill (`raw_entry_fill`,
`raw_exit_fill`) -- Trade Explorer's existing Market Context panel shows
these with no changes to that page.

## Cross-import behavior

A position opened in one uploaded file and closed in a **later, separate**
upload still matches correctly: open lots are persisted (`client_open_lots`)
between imports, not just within one file's processing. Re-uploading a file
(or one with overlapping fills) is safe -- fills are fingerprinted and
checked against every fill previously imported for that client profile
(`client_import_fills`); duplicates are skipped before matching ever runs,
so they can't corrupt the open-lot queue.

## Known limitations

- **One account per client profile.** If a file contains more than one
  distinct `account` value, a non-blocking warning suggests splitting it
  into separate profiles -- matching isn't done per-account within one
  profile.
- **FIFO only** (no LIFO or average-cost accounting option).
- **Four configured contracts** for automatic dollar P&L (`MES`/`MNQ`/`M2K`/
  `MYM`); anything else needs a `realized_pnl` column in the source file or
  falls back to points-only P&L.
- **Format detection is heuristic**, not verified against a live Tradovate
  or NinjaTrader account export -- always review the mapping wizard before
  confirming.
