# Database Validation

A permanent, read-only data-integrity validator for `market_data.db`,
built directly out of diagnosing and repairing the turtle-data
corruption in [DATABASE_CORRUPTION_REPORT.md](DATABASE_CORRUPTION_REPORT.md)
(2026-07-26). Every check either catches the exact bug shape found
there or a related integrity class the schema doesn't enforce on its
own.

## Running it

```bash
python -m futures_bot.cli --validate-db
```

or directly:

```bash
python -m futures_bot.market_data.validation [--db PATH]
```

Both print a detailed report to stdout and exit **1** if any check
`FAIL`s, **0** otherwise. Never writes to the database — every
connection is opened SQLite read-only (`file:...?mode=ro`), which
raises rather than silently allowing a write.

## Severity model

- **FAIL** — a genuine data-integrity violation. Fails the exit code.
- **WARN** — worth attention but not proof of corruption on its own
  (zero-volume bars in a thin historical market, an unresolved gap the
  sync engine already knows about, a heuristic missing-trading-day
  guess with no verified holiday calendar behind it). Does **not** fail
  the exit code by itself.
- **PASS** — the check ran and found nothing.

## What each check covers

| Check | Severity | Scope / known limitations |
|---|---|---|
| `schema_mismatch` | FAIL | Diffs the live database's actual `PRAGMA table_info` per table against `store.py`'s `_SCHEMA`, imported directly (not re-declared) via a throwaway in-memory connection — this check can never drift out of sync with the real schema. Catches missing tables, missing/extra columns, and type/`NOT NULL` differences. |
| `duplicate_rows` | FAIL | Rows sharing `(product_code, resolution, timestamp)` — should be impossible under `idx_bars_identity`; this is defense-in-depth in case the index is ever missing or bypassed. |
| `missing_timestamps` | FAIL | `NULL` or empty `timestamp`. |
| `timestamp_ordering_errors` | FAIL | Two sub-checks: malformed shape (doesn't match `YYYY-MM-DDT...`), and implausible year (before 1900 or after next year) — the second is exactly the shape of the century-pivot bug fixed 2026-07-26 (1964 stored as 2064). |
| `invalid_ohlc_values` | FAIL | NULL/empty OHLC fields, plus non-numeric values per column. SQLite's `CAST(...AS REAL)` is lenient (non-numeric → `0.0`, not an error) so this checks the raw string shape instead. |
| `high_lt_open` / `high_lt_close` / `low_gt_open` / `low_gt_close` | FAIL | Direct OHLC relationship violations. Uses `CAST(...AS REAL)` for comparison — fine for detection, not a claim of exact-decimal precision (the app's actual money math uses `Decimal` elsewhere, per `models.py`). |
| `negative_volume` | FAIL | Always invalid. |
| `zero_volume` | WARN | Legitimate for thin historical markets — the turtle archive has plenty of genuine single-digit or zero volume days in 1960s–80s commodities. Not a defect by itself. |
| `corrupted_contract_symbols` | FAIL | Every distinct `(product_code, contract)` pair must match one of two conventions this database actually uses — see `is_valid_symbol`'s docstring in `market_data/validation.py`. Live products: `product_code` is a generic root (or `f"{root}_CONTINUOUS"`), `contract` is `"CONTINUOUS"` or a specific ticker. Historical (turtle) products: `product_code` **is** the full ticker and `contract` must match it exactly — deliberate, not a bug (see the corruption report's Resolution section for why treating `product_code` as a generic root there destroys ~90% of the data on reimport). |
| `overlapping_contracts` | FAIL | Scoped to `contract_rolls`' from/to chain consistency (each roll's `from_contract` must match the prior roll's `to_contract`), not a general cross-product timestamp-overlap scan — `MES` and `MES_CONTINUOUS` are *expected* to share timestamps by design, so a naive "do any two product_codes share a day" check would false-positive on every live product. |
| `session_gaps` | WARN | Reuses `market_data.sync`'s existing gap bookkeeping (the `gaps` table, populated by `--verify-data`) rather than recomputing session logic independently. Reports what the sync engine has already recorded, not a fresh calendar-aware recomputation. |
| `missing_trading_days` | WARN | Heuristic only: flags calendar gaps of 4+ consecutive *weekdays* with zero bars, for daily-resolution series. **Not a verified holiday calendar** — the turtle archive predates modern CME rules and covers exchanges never modeled in `contracts.py` (NYMEX/COMEX Crude/Gold/Copper, CBOT Treasury Bonds, the old S&P/Dollar Index tickers). A flagged gap may well be a legitimate historical closure; treat these as "worth a look," not proof of a missing import. |
| `orphan_records` | WARN | Cross-checks `gaps`/`active_contracts`/`contract_rolls`/`sync_runs` against `bars` — flags any `product_code` referenced in the metadata tables with zero actual rows in `bars` (a sync that never completed, or a product later removed). |

## Reading a sample report

```
Database Validation Report -- market_data.db
Generated: 2026-07-27T03:45:11+00:00
Total bars: 3,518,488

[FAIL] high_lt_open: High < Open: violates OHLC invariants.
    count: 6
    - US80Z/US80Z@1980-10-27T00:00:00+00:00
    ...
[WARN] zero_volume: 74726 bar(s) with zero volume -- expected for thin
historical markets, not on its own a defect.
    count: 74,726
[PASS] duplicate_rows: No duplicate (product_code, resolution, timestamp)
rows -- idx_bars_identity holds.

Summary: 5 FAIL, 4 WARN, 11 PASS.
VALIDATION FAILED
```

Findings are sorted FAIL → WARN → PASS. Each finding lists a `count`
and up to 10 concrete samples.

## Known findings as of 2026-07-27 (not fixed by this task — see KNOWN_ISSUES.md)

Running this validator against the live, already-repaired
`market_data.db` for the first time surfaced two **new**, previously
unknown issues, unrelated to the turtle-data corruption already fixed:

1. **`bars`' actual schema has drifted from `store.py`'s current
   `_SCHEMA`** — the live table lacks the `NOT NULL` constraints the
   current schema declares, `id`'s type is `INT` not `INTEGER` and it
   never became the `PRIMARY KEY`/`AUTOINCREMENT` the schema now
   specifies, and `created_at` has no default. `CREATE TABLE IF NOT
   EXISTS` never retroactively alters an existing table, so this table
   was created under an older version of `_SCHEMA` and never migrated.
2. **`US80Z` (a 1980 Treasury Bond contract) has genuine OHLC
   invariant violations in the raw historical source data itself** —
   confirmed by checking `turtle_raw/US80Z.txt` directly, not
   introduced by any conversion/import step in this project.

Per this task's constraints, **neither was fixed** — this validator's
job is detection, not repair. Both are logged in `KNOWN_ISSUES.md` for
a future session to address.

## Adding a new check

Each check is a small, independent function taking the read-only
connection and returning `list[Finding]`. Add it to the module-level
`_CHECKS` tuple in `market_data/validation.py`. `validate_database`
runs every check in a try/except per-check, so one check raising
(e.g. against a database whose schema has drifted badly enough that
a column it expects doesn't exist) is reported as its own FAIL finding
rather than aborting the rest of the run.
