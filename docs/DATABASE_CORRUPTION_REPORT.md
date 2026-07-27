# Database Corruption Report — Turtle-Sourced Historical Data

**Status:** RESOLVED 2026-07-26. See "Resolution" section at the end
for what was actually done, including a near-miss during the repair
that was caught and rolled back before any data was permanently lost.

**Date of investigation:** 2026-07-26

**Scope:** Confirmed to be specific to the turtle-data import (all
`bars` rows with `source = 'turtletrader'`). No other source
(`massive`, `massive_flatfiles`, `autonomous_paper`) is affected.

---

## Summary

The originally reported symptom — "the contract/ticker field was
populated with date values" — **does not literally match what's in
the database.** There are no date-shaped strings anywhere in
`bars.product_code` or `bars.contract`; every value in both columns
was checked against a date-pattern regex across all 897 distinct
`product_code` values and all 28 distinct `contract` values, with zero
matches.

What actually exists are **two separate, confirmed, code-level bugs**
in the turtle-data import pipeline, both of which produce the
higher-level symptom the user observed (a database full of confusing,
seemingly-bogus distinct "contracts"):

1. **`product_code`/`contract` semantic swap** — 100% of turtle rows
   (342,494 rows) have the specific per-contract ticker (e.g. `CL00F`)
   stored in `product_code`, where the schema requires a generic root
   (e.g. `CL`), and the literal placeholder string `"CONTINUOUS"`
   hardcoded into `contract` for every row, discarding the real ticker
   entirely.
2. **Century-pivot timestamp corruption** — 17,668 rows (all Copper,
   root `HG`) have their bar `timestamp` shifted **exactly 100 years
   into the future** (e.g. a 1964 bar stored as `2064`), because the
   date parser uses Python's default two-digit-year pivot, which is
   wrong for this dataset.

Both are 100% reproducible and root-caused to exact lines of code (see
below). Confidence level: **Confirmed / Very High** for both.

---

## 1. Database Integrity

```
PRAGMA integrity_check;  -> ok
PRAGMA quick_check;      -> ok
```

Checked on all three on-disk copies:

| File | Integrity check |
|---|---|
| `market_data.db` (live) | ok |
| `market_data_backup.db` | ok |
| `market_data_before_merge.db` | ok |

The corruption here is a **logical/semantic data-quality bug**, not
file/page-level SQLite corruption. The database file itself is
structurally sound.

## 2. Affected Table(s) and Column(s)

Only one table is affected: **`bars`**. No other table
(`sync_runs`, `gaps`, `active_contracts`, `contract_rolls`) contains
any turtle-sourced or otherwise malformed data — all three of
`active_contracts`/`contract_rolls`/`sync_runs`'s `product_code`
values are clean (`MES`, `M2K`, `MNQ`, `CONTINUOUS` only).

Affected columns within `bars`: **`product_code`** and **`contract`**
(the symbol/identity columns) for Bug 1; **`timestamp`** for Bug 2.

Per `src/futures_bot/market_data/store.py`'s own schema comment, the
intended contract is:

```sql
product_code  TEXT NOT NULL,   -- generic product, e.g. 'MES' (contracts.CONTRACTS key)
contract      TEXT NOT NULL,   -- the specific expiry ticker this bar actually came from, e.g. 'MESU6'
```

Turtle-sourced rows violate this on both sides simultaneously.

## 3 & 4. First and Last Corrupted Date

Two different corruptions, two different date ranges:

**Bug 1 (semantic swap)** affects every turtle row uniformly across
the entire imported range:
- First: `1969-01-02`
- Last (of the "normal-looking" dates): `2000-01-27`
- (See Bug 2 below for the remaining rows, which show impossible
  future dates as a direct symptom of the same import.)

**Bug 2 (century-pivot shift)** — the corrupted date range, as
currently stored:
- First corrupted (shifted) date: `2059-07-01`
- Last corrupted (shifted) date: `2068-12-31`
- True (intended) dates, per the shift: `1959-07-01` through
  `1968-12-31`.

## 5. Every Affected Contract

**Bug 1** affects all 893 distinct turtle-derived `product_code`
values (every one of them is actually a specific contract ticker, not
a product root). Grouped by implied root symbol and row count:

| Root | Instrument | Rows |
|---|---|---|
| HG | Copper | 105,642 |
| GC | Gold | 75,884 |
| CL | Crude Oil (WTI) | 74,006 |
| US | 30-Year Treasury Bond | 54,780 |
| SP | S&P 500 (old ticker) | 18,819 |
| DX | US Dollar Index | 13,363 |

**Bug 2** affects only the **HG (Copper)** root — specifically the 24
contract files with year codes `59` through `68`
(`HG59V`, `HG59Z`, `HG60F`...`HG68...`, etc.) — 17,668 rows total.

## 6. Comparison Against Original Source Files

Both `turtle_raw/` (1,023 raw `.txt` files) and `turtle_converted/`
(1,023 converted `.csv` files) are **still present on disk** and were
used directly for this diagnosis.

- Raw files (`turtle_raw/CL00F.txt` etc.) have **no header row and no
  symbol column at all** — just `date(YYMMDD),open,high,low,close,
  volume,open_interest`. The contract identity is carried *only* by
  the filename.
- Converted files (`turtle_converted/CL00F.csv`) preserve that: a
  clean `timestamp,open,high,low,close,volume` header, still with no
  symbol column, still relying on the filename for identity.
- This confirms the import script (not the source data) is entirely
  responsible for deriving `product_code`/`contract` from the
  filename — and it derives them backwards.
- Spot-check: `turtle_raw/CL00F.txt` row 1 is `970821,19.55,...` →
  `turtle_converted/CL00F.csv` row 2 correctly converts this to
  `1997-08-21T00:00:00+00:00,19.55,...` (year 97 → 1997, correct side
  of the pivot). A file with an affected year code, e.g. any `HG6xH`
  file's earliest rows, converts to a `20xx` timestamp instead of
  `19xx` — reproducing Bug 2 exactly.

## 7. Root Cause

**Bug 1 — `tools/import_turtle_data.py`, lines 57 and 64–65:**

```python
product = file.stem.upper()          # e.g. "CL00F" -- a SPECIFIC ticker, not a root
...
inserted = store.upsert_bars(
    product_code=product,            # WRONG: specific ticker written where a generic root belongs
    contract="CONTINUOUS",           # WRONG: hardcoded placeholder, discards the real ticker
    ...
)
```

The script takes the filename stem (which is always a fully-specific
contract ticker like `CL00F`, `GC98Z`, `HG64H`) and writes it directly
into `product_code`, which per the schema must hold a generic root
(`CL`, `GC`, `HG`, ... — the same convention `contracts.CONTRACTS`
uses for `MES`/`MNQ`/`M2K`/`MYM`). Simultaneously it hardcodes
`contract="CONTINUOUS"` for every single row instead of using the
ticker it already parsed out of the filename. The two fields are, in
effect, swapped and one of them is thrown away.

Because the schema's uniqueness/coalescing index is
`idx_bars_identity ON bars(product_code, resolution, timestamp)` (see
`store.py`), this bug also means turtle data never benefited from the
system's contract-rollover coalescing — each of the 893 specific
tickers is treated as its own unrelated "product."

**Bug 2 — `tools/convert_turtle_data.py`, line 16:**

```python
def parse_date(value: str):
    """Converts YYMMDD -> UTC timestamp"""
    dt = datetime.strptime(value, "%y%m%d")
    return dt.replace(tzinfo=timezone.utc)
```

`%y` uses Python's (POSIX-inherited) default century pivot: two-digit
years `00`–`68` are interpreted as `2000`–`2068`, and `69`–`99` as
`1969`–`1999`. That pivot is wrong for this dataset, which
legitimately contains Copper (`HG`) data from as early as 1959 — any
row with a raw year code `00`–`68` from a pre-1969 file gets bumped
exactly 100 years into the future instead of being read as `19xx`.
Contracts trading entirely after 1969 (the `CL`/`GC`/`US`/`SP`/`DX`
roots, none of which existed before the mid-1970s to early-1980s) are
unaffected by this specific bug — it only bites `HG`'s pre-1969
history.

## 8. Estimated Records Affected

- **Bug 1 (semantic swap):** 342,494 rows — 100% of `source =
  'turtletrader'` rows in `bars`. This is 9.7% of the entire `bars`
  table (3,518,488 rows total).
- **Bug 2 (timestamp shift):** 17,668 rows — a subset of the above,
  all root `HG`. This is 5.2% of turtle rows / 0.5% of the whole
  table.
- **Rows NOT affected:** all `MES`/`MNQ`/`M2K`/`MES_CONTINUOUS` rows
  (sources `massive`, `massive_flatfiles`, `autonomous_paper` —
  3,175,994 rows) are clean; no cross-contamination was found in
  either direction.

## 9. Repair Strategy (proposed — not executed)

Both source directories (`turtle_raw/`, `turtle_converted/`, 1,023
files each) are still on disk, so a **full re-import from source** is
possible and is the safest option — no fragile in-place row surgery on
3.5M rows is needed.

1. **Backup first, unconditionally.** Take a fresh timestamped copy of
   `market_data.db`, then open the copy read-only and run `PRAGMA
   integrity_check` on it before touching anything. Do not rely on
   `market_data_backup.db` or `market_data_before_merge.db` — both are
   stale relative to today's live data (`market_data_backup.db` is
   ~800k rows short of the live table). If the fresh backup can't be
   created or verified, stop and report — do not proceed.
2. **Fix `tools/convert_turtle_data.py`'s `parse_date`** to remove the
   century-pivot ambiguity — e.g. parse the two-digit year explicitly
   and pick the century using a fixed, dataset-appropriate rule (this
   corpus never contains a timestamp after roughly 2000, so any
   parsed year greater than, say, the current year should be
   interpreted as 1900s, not 2000s) rather than relying on `%y`'s
   built-in default.
3. **Fix `tools/import_turtle_data.py`** to derive `product_code` and
   `contract` correctly: `contract = file.stem.upper()` (the full
   ticker, e.g. `CL00F`) and `product_code` = the root symbol parsed
   from it (strip the trailing 2-digit-year + 1 month-letter suffix —
   every root in this corpus is exactly 2 characters: `CL`, `GC`,
   `HG`, `US`, `SP`, `DX`).
4. **Re-run `convert_turtle_data.py`** over `turtle_raw/` to regenerate
   `turtle_converted/` with corrected timestamps.
5. **Delete existing corrupted rows** — precisely identifiable via
   `DELETE FROM bars WHERE source = 'turtletrader'` (this predicate
   exactly isolates the 342,494 affected rows with zero risk to any
   other source).
6. **Re-run the fixed `import_turtle_data.py`** to reload all 1,023
   files with correct `product_code`/`contract`/`timestamp`.
7. **Verify:** re-run the distinct-`product_code` query (expect 4
   clean roots for live data + 6 clean historical roots — `CL`, `GC`,
   `HG`, `US`, `SP`, `DX` — instead of 897 mixed values), confirm
   `MIN(timestamp)`/`MAX(timestamp)` for `source='turtletrader'` falls
   in a sane historical window with no post-2026 dates, and spot-check
   a handful of repaired rows against their source `.csv` files.

This whole repair is deferred pending explicit approval, per your
instruction. No code or data has been modified as part of producing
this report.

## Confidence Level

**Confirmed / Very High** for both bugs — root-caused to exact lines
of code, independently reproduced by cross-referencing source files
against database contents, and the exact corrupted-row counts/date
boundaries match what the buggy code would be expected to produce.

## Out of Scope / Noticed But Not Investigated Further

- `bars.id` is `NULL` for every row regardless of source (not just
  turtle data) — the table's real uniqueness constraint is the
  `(product_code, resolution, timestamp)` index, not `id`, so this
  looks like an unused legacy column rather than a symptom of this
  corruption. Not pursued further here.
- The originally reported hypothesis (dates literally appearing in a
  symbol/ticker field) does not match anything found in the database.
  If there's a specific query, dashboard view, or export where this
  was actually observed, it would help to see that — it may point to
  a third issue this investigation didn't cover, e.g. in a downstream
  view/aggregation rather than in `bars` itself.

---

## Resolution (2026-07-26)

**Backup.** A fresh backup was taken via SQLite's online backup API
(safe under WAL mode) before any write:
`market_data.backup_20260726_221257.db` (927,682,560 bytes, matching
the live file exactly). Verified before proceeding: opened `mode=ro`,
`PRAGMA integrity_check` → `ok`, and row counts confirmed identical to
the live database (3,518,488 total bars, 342,494 turtletrader, 17,668
future-shifted). This backup is gitignored (`market_data*.db`) and
was kept on disk after the repair rather than deleted.

**Correction to the original diagnosis — a near-miss, caught before
data was lost for good.** The original Bug 1 write-up above proposed
treating `product_code` as the defect and rewriting it to a generic
root (e.g. `CL` instead of `CL00F`). That was wrong, and running it
proved it immediately: reimporting with `product_code` set to the
generic root reduced the 342,494 turtletrader rows to just 34,331 —
because `bars`' uniqueness index is `(product_code, resolution,
timestamp)`, and this archive has hundreds of individual contract
files with heavily overlapping trading date ranges (e.g. `CL98M` and
`CL98N` both trade on the same calendar days). Collapsing them onto a
shared generic `product_code` collided every overlapping day and
silently kept only the first contract written for each. This was
caught immediately after the reimport (by comparing the new row count
against the pre-repair count), and the live database was restored from
the verified backup before proceeding further — no data was lost.

The corrected understanding: `product_code` was already correct as the
full per-contract ticker (that's what let the original, buggy script
avoid this exact collision for 342,494 rows without ever knowing it).
The one real defect was `contract` being hardcoded to `"CONTINUOUS"`
instead of also holding that same ticker.

**Fixes applied:**

- `tools/convert_turtle_data.py` — `parse_date` now uses a fixed
  50-year pivot (00–49 → 2000–2049, 50–99 → 1950–1999) instead of
  Python's `%y` default, correctly resolving every year actually
  present in this corpus (1959–2000).
- `tools/import_turtle_data.py` — added `parse_ticker`, which validates
  the filename against the contract-symbol pattern (root + 2-digit
  year + month-letter) and rejects/skips anything that doesn't match
  instead of silently importing it. `contract` is now set to the same
  validated ticker as `product_code`, instead of the `"CONTINUOUS"`
  placeholder.
- `tests/test_tools_turtle_import.py` (new, 15 tests) — covers the
  century-pivot fix, the ticker-validation fix, and — directly —
  the collision failure mode itself: one test proves two
  overlapping-day contracts survive independently under the corrected
  (full-ticker) `product_code`, and a second test reproduces the
  generic-root collision on purpose, so this exact mistake can't
  silently recur.
- `tools/repair_turtle_corruption_2026_07_26.py` (new) — the one-time
  delete step (`DELETE FROM bars WHERE source = 'turtletrader'`),
  kept on disk for auditability, with before/after row-count logging
  and an assertion that the arithmetic matches.

**Repair procedure actually run:**

1. Backup + verify (above).
2. `tools/convert_turtle_data.py` re-run over `turtle_raw/` (1,023
   files) to regenerate `turtle_converted/` with corrected timestamps.
3. `tools/repair_turtle_corruption_2026_07_26.py` — deleted 342,494
   `source='turtletrader'` rows (logged: 3,518,488 → 3,175,994).
4. `tools/import_turtle_data.py` (fixed) re-run — reloaded 342,494
   rows from the regenerated source files. One file,
   `turtle_raw/GC001F.txt`, was skipped: it uses a completely
   different schema (a header row, `MM/DD/YYYY` dates) than every
   other file in the corpus, and its filename doesn't match the
   expected ticker pattern either (`GC001F` vs. the legitimate,
   already-present `GC01F`). It contributed zero usable rows either
   way, since its date format wouldn't have parsed under the fixed
   `convert_turtle_data.py` regardless. Flagged, not fixed — guessing
   its intended symbol/date format would be an assumption, not a
   diagnosis.

**Verification:**

- `PRAGMA integrity_check` → `ok`.
- `bars` total: 3,518,488 (unchanged from before the repair).
- `source='turtletrader'` rows: 342,494 (unchanged).
- Rows with `timestamp` year > 2026: **0** (was 17,668).
- Rows with `contract = 'CONTINUOUS'`: **0** (was 342,494).
- `product_code`/`contract` now match for every turtle row (e.g.
  `HG64H`/`HG64H` instead of `HG64H`/`CONTINUOUS`).
- Turtletrader date range: `1959-07-01` through `2000-01-27` — sane,
  no future dates.
- Spot-checked repaired rows against source CSVs for two different
  contracts (`HG64H`, `CL00F`) — exact match on timestamp and OHLCV.
- Full test suite: **896 passed, 0 failed** (881 pre-existing + 15
  new). The previously-known test-order-dependent flake
  (KNOWN_ISSUES.md ISSUE-002) did not reproduce in this run.
