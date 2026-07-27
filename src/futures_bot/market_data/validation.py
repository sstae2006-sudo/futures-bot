"""Permanent, read-only data-integrity validator for market_data.db.

Grew directly out of diagnosing/repairing the turtle-data corruption in
docs/DATABASE_CORRUPTION_REPORT.md (2026-07-26) -- every check here either
catches the exact bug shape found there (the century-pivot timestamp shift,
the hardcoded "CONTINUOUS" contract placeholder) or a related integrity
class the schema itself doesn't enforce.

Never writes to the database: every connection this module opens is a
SQLite read-only URI (`mode=ro`), which raises rather than silently
allowing a write. The one exception is the in-memory reference connection
used for the schema check, which never touches the real file at all.

Two severities:

* FAIL -- a genuine data-integrity violation. Makes `validate_database`'s
  report "failed" and the CLI/BOOT_CHECKLIST entry exit nonzero.
* WARN -- worth a human's attention but not proof of corruption on its
  own (zero-volume bars in a thin historical market, an unresolved gap
  the sync engine already knows about, a heuristic missing-trading-day
  guess with no verified holiday calendar behind it). Does not fail the
  exit code by itself.

See docs/DATABASE_VALIDATION.md for what each check means and its known
limitations -- several of these are deliberately scoped narrower than
their name might suggest, to avoid false positives on data this project
doesn't have enough information to judge (pre-1980s exchange calendars,
for one).
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..contracts import CONTRACTS
from .store import _SCHEMA, default_db_path

MONTH_LETTERS = "FGHJKMNQUVXZ"

#: Historical (turtle-archive) ticker: root (1-3 letters) + 2-digit year +
#: month-letter, e.g. "CL00F". Shared with tools/import_turtle_data.py's
#: parse_ticker so there is one definition of "valid historical contract
#: symbol," not two -- see docs/DATABASE_CORRUPTION_REPORT.md.
HISTORICAL_TICKER_PATTERN = re.compile(rf"^[A-Z]{{1,3}}\d{{2}}[{MONTH_LETTERS}]$")

#: Live CONTRACTS-registry specific ticker, e.g. "MESH6" (root + month
#: letter + single-digit year).
_LIVE_ROOTS = "|".join(re.escape(root) for root in CONTRACTS)
LIVE_TICKER_PATTERN = re.compile(rf"^(?:{_LIVE_ROOTS})[{MONTH_LETTERS}]\d$")


def is_valid_historical_ticker(value: str) -> bool:
    """True for a turtle-archive style ticker, e.g. 'CL00F'."""
    return bool(HISTORICAL_TICKER_PATTERN.match(value))


def is_valid_symbol(product_code: str, contract: str) -> bool:
    """True when a (product_code, contract) pair matches one of the two
    conventions this database actually uses.

    Live CONTRACTS-registry products: product_code is a generic root (or
    f"{root}_CONTINUOUS" for the synthetic continuous series), contract is
    either the literal "CONTINUOUS" or a specific root+month+year ticker.

    Historical (turtle) products: product_code IS the full ticker, and
    contract must match it exactly -- deliberate, not a bug, because this
    archive holds hundreds of individually-overlapping contract histories
    under one schema whose uniqueness key is (product_code, resolution,
    timestamp). See docs/DATABASE_CORRUPTION_REPORT.md's Resolution
    section for why treating product_code as a generic root here silently
    destroys ~90% of the data.
    """
    if not product_code or not contract:
        return False
    root = product_code[: -len("_CONTINUOUS")] if product_code.endswith("_CONTINUOUS") else product_code
    if root in CONTRACTS:
        return contract == "CONTINUOUS" or bool(LIVE_TICKER_PATTERN.match(contract))
    return is_valid_historical_ticker(product_code) and contract == product_code


@dataclass
class Finding:
    check: str
    severity: str  # "FAIL" | "WARN" | "PASS"
    count: int
    detail: str
    samples: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    db_path: Path
    generated_at: datetime
    total_bars: int
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "FAIL"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "WARN"]

    @property
    def passed(self) -> bool:
        return not self.failures


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _count_weekdays_between(start: date, end: date) -> int:
    """Weekdays strictly between start and end (both exclusive). Bounded
    to at most 6 loop iterations regardless of how wide the gap is."""
    span = (end - start).days - 1
    if span <= 0:
        return 0
    full_weeks, remainder = divmod(span, 7)
    weekdays = full_weeks * 5
    d = start + timedelta(days=1)
    for _ in range(remainder):
        if d.weekday() < 5:
            weekdays += 1
        d += timedelta(days=1)
    return weekdays


def _check_schema(conn: sqlite3.Connection) -> list[Finding]:
    """Diffs the live database's actual schema against store.py's _SCHEMA
    (imported directly, not re-declared) via a throwaway in-memory
    connection -- so this check can never drift from the real schema."""
    ref = sqlite3.connect(":memory:")
    try:
        ref.executescript(_SCHEMA)
        findings: list[Finding] = []
        ref_tables = {r[0] for r in ref.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        live_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

        missing_tables = ref_tables - live_tables
        if missing_tables:
            findings.append(Finding(
                "schema_mismatch:tables", "FAIL", len(missing_tables),
                f"Table(s) expected by store.py's schema are missing from the database: "
                f"{', '.join(sorted(missing_tables))}",
            ))

        for table in sorted(ref_tables & live_tables):
            ref_cols = {(r[1], r[2], r[3]) for r in ref.execute(f"PRAGMA table_info({table})")}
            live_cols = {(r[1], r[2], r[3]) for r in conn.execute(f"PRAGMA table_info({table})")}
            if ref_cols != live_cols:
                missing = ref_cols - live_cols
                extra = live_cols - ref_cols
                parts = []
                if missing:
                    parts.append(f"expected but not found: {sorted(missing)}")
                if extra:
                    parts.append(f"present but unexpected: {sorted(extra)}")
                findings.append(Finding(
                    f"schema_mismatch:{table}", "FAIL", len(missing) + len(extra),
                    "; ".join(parts),
                ))

        if not findings:
            findings.append(Finding(
                "schema_mismatch", "PASS", 0,
                "Live database schema matches store.py's canonical _SCHEMA exactly.",
            ))
        return findings
    finally:
        ref.close()


def _check_duplicate_rows(conn: sqlite3.Connection) -> list[Finding]:
    dupe_groups = conn.execute(
        "SELECT COUNT(*) FROM (SELECT 1 FROM bars "
        "GROUP BY product_code, resolution, timestamp HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    if not dupe_groups:
        return [Finding(
            "duplicate_rows", "PASS", 0,
            "No duplicate (product_code, resolution, timestamp) rows -- idx_bars_identity holds.",
        )]
    samples = [
        f"{p}/{r}/{t} (x{c})" for p, r, t, c in conn.execute(
            "SELECT product_code, resolution, timestamp, COUNT(*) c FROM bars "
            "GROUP BY product_code, resolution, timestamp HAVING c > 1 LIMIT 10"
        )
    ]
    return [Finding(
        "duplicate_rows", "FAIL", dupe_groups,
        "Rows sharing the same (product_code, resolution, timestamp) -- should be "
        "impossible under the schema's own unique index; indicates the index is "
        "missing or was bypassed.",
        samples,
    )]


def _check_missing_timestamps(conn: sqlite3.Connection) -> list[Finding]:
    count = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE timestamp IS NULL OR TRIM(timestamp) = ''"
    ).fetchone()[0]
    if count:
        return [Finding("missing_timestamps", "FAIL", count, "Rows with a NULL or empty timestamp.")]
    return [Finding("missing_timestamps", "PASS", 0, "No NULL/empty timestamps.")]


def _check_timestamp_validity(conn: sqlite3.Connection) -> list[Finding]:
    """Malformed shape, plus the exact class of bug this validator exists
    to catch a recurrence of: an implausible year (docs/DATABASE_CORRUPTION_REPORT.md)."""
    findings: list[Finding] = []

    malformed = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE timestamp IS NOT NULL AND TRIM(timestamp) != '' "
        "AND timestamp NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*'"
    ).fetchone()[0]
    if malformed:
        samples = [r[0] for r in conn.execute(
            "SELECT DISTINCT timestamp FROM bars WHERE timestamp NOT GLOB "
            "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*' LIMIT 10"
        )]
        findings.append(Finding(
            "timestamp_ordering_errors:format", "FAIL", malformed,
            "Timestamps that don't match the expected ISO 8601 shape (YYYY-MM-DDT...).",
            samples,
        ))

    next_year = str(date.today().year + 1)
    implausible = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE substr(timestamp,1,4) < '1900' OR substr(timestamp,1,4) > ?",
        (next_year,),
    ).fetchone()[0]
    if implausible:
        samples = [r[0] for r in conn.execute(
            "SELECT DISTINCT timestamp FROM bars WHERE substr(timestamp,1,4) < '1900' "
            "OR substr(timestamp,1,4) > ? LIMIT 10",
            (next_year,),
        )]
        findings.append(Finding(
            "timestamp_ordering_errors:implausible_year", "FAIL", implausible,
            "Timestamps before 1900 or after next year -- this is exactly the shape of "
            "the century-pivot bug fixed 2026-07-26 (1964 stored as 2064).",
            samples,
        ))

    if not findings:
        findings.append(Finding(
            "timestamp_ordering_errors", "PASS", 0,
            "All timestamps are well-formed and within a plausible date range.",
        ))
    return findings


def _check_ohlc_presence(conn: sqlite3.Connection) -> list[Finding]:
    count = conn.execute(
        "SELECT COUNT(*) FROM bars WHERE open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL "
        "OR TRIM(open) = '' OR TRIM(high) = '' OR TRIM(low) = '' OR TRIM(close) = ''"
    ).fetchone()[0]
    if count:
        return [Finding("invalid_ohlc_values:presence", "FAIL", count,
                         "Rows with a NULL or empty open/high/low/close field.")]
    return [Finding("invalid_ohlc_values:presence", "PASS", 0, "No NULL/empty OHLC fields.")]


def _check_ohlc_numeric(conn: sqlite3.Connection) -> list[Finding]:
    """SQLite's CAST(...AS REAL) is lenient (non-numeric -> 0.0, not an
    error), so this checks the raw string shape instead: anything
    containing a character other than a digit, '.', or '-' is flagged."""
    findings: list[Finding] = []
    for col in ("open", "high", "low", "close"):
        count = conn.execute(
            f"SELECT COUNT(*) FROM bars WHERE {col} IS NOT NULL AND TRIM({col}) != '' "
            f"AND {col} GLOB '*[^0-9.-]*'"
        ).fetchone()[0]
        if count:
            samples = [r[0] for r in conn.execute(
                f"SELECT DISTINCT {col} FROM bars WHERE {col} GLOB '*[^0-9.-]*' LIMIT 10"
            )]
            findings.append(Finding(
                f"invalid_ohlc_values:{col}_non_numeric", "FAIL", count,
                f"Non-numeric value(s) found in {col}.", samples,
            ))
    if not findings:
        findings.append(Finding("invalid_ohlc_values:numeric", "PASS", 0,
                                 "All OHLC values are numeric-looking."))
    return findings


def _check_ohlc_relationships(conn: sqlite3.Connection) -> list[Finding]:
    checks = [
        ("high_lt_open", "CAST(high AS REAL) < CAST(open AS REAL)", "High < Open"),
        ("high_lt_close", "CAST(high AS REAL) < CAST(close AS REAL)", "High < Close"),
        ("low_gt_open", "CAST(low AS REAL) > CAST(open AS REAL)", "Low > Open"),
        ("low_gt_close", "CAST(low AS REAL) > CAST(close AS REAL)", "Low > Close"),
    ]
    findings: list[Finding] = []
    for name, predicate, label in checks:
        count = conn.execute(f"SELECT COUNT(*) FROM bars WHERE {predicate}").fetchone()[0]
        if count:
            samples = [
                f"{p}/{c}@{t}" for p, c, t in conn.execute(
                    f"SELECT product_code, contract, timestamp FROM bars WHERE {predicate} LIMIT 10"
                )
            ]
            findings.append(Finding(name, "FAIL", count, f"{label}: violates OHLC invariants.", samples))
        else:
            findings.append(Finding(name, "PASS", 0, f"No {label} violations."))
    return findings


def _check_volume(conn: sqlite3.Connection) -> list[Finding]:
    findings: list[Finding] = []
    negative = conn.execute("SELECT COUNT(*) FROM bars WHERE volume < 0").fetchone()[0]
    findings.append(Finding(
        "negative_volume", "FAIL" if negative else "PASS", negative,
        f"{negative} bar(s) with negative volume." if negative else "No negative volume.",
    ))
    zero = conn.execute("SELECT COUNT(*) FROM bars WHERE volume = 0").fetchone()[0]
    findings.append(Finding(
        "zero_volume", "WARN" if zero else "PASS", zero,
        (f"{zero} bar(s) with zero volume -- expected for thin historical markets "
         "(single-digit-volume 1960s-80s commodities), not on its own a defect.")
        if zero else "No zero-volume bars.",
    ))
    return findings


def _check_corrupted_symbols(conn: sqlite3.Connection) -> list[Finding]:
    pairs = conn.execute("SELECT DISTINCT product_code, contract FROM bars").fetchall()
    bad = [(p, c) for p, c in pairs if not is_valid_symbol(p, c)]
    if not bad:
        return [Finding(
            "corrupted_contract_symbols", "PASS", 0,
            f"All {len(pairs)} distinct (product_code, contract) pairs match an expected convention.",
        )]
    total_rows = sum(
        conn.execute(
            "SELECT COUNT(*) FROM bars WHERE product_code = ? AND contract = ?", (p, c)
        ).fetchone()[0]
        for p, c in bad
    )
    samples = [f"{p}/{c}" for p, c in bad[:15]]
    return [Finding(
        "corrupted_contract_symbols", "FAIL", total_rows,
        f"{len(bad)} distinct (product_code, contract) pair(s) don't match a known symbol convention.",
        samples,
    )]


def _check_overlapping_contracts(conn: sqlite3.Connection) -> list[Finding]:
    """Scoped to contract_rolls' chronology and chain consistency -- not a
    general cross-product timestamp overlap scan. MES vs MES_CONTINUOUS
    are *expected* to share timestamps by design (see store.py's module
    docstring), so a naive "do any two product_codes share a day" check
    would false-positive on every live product. contract_rolls recording
    exactly one active contract at a time, in order, with each roll's
    from_contract matching the prior roll's to_contract, is the concrete,
    checkable invariant this scopes to instead."""
    products = [r[0] for r in conn.execute("SELECT DISTINCT product_code FROM contract_rolls")]
    problems: list[str] = []
    for product in products:
        rolls = conn.execute(
            "SELECT from_contract, to_contract, rolled_at FROM contract_rolls "
            "WHERE product_code = ? ORDER BY rolled_at ASC",
            (product,),
        ).fetchall()
        prev_to: Optional[str] = None
        for from_contract, to_contract, rolled_at in rolls:
            if prev_to is not None and from_contract is not None and from_contract != prev_to:
                problems.append(
                    f"{product}: roll chain break at {rolled_at} -- expected "
                    f"from_contract={prev_to!r}, got {from_contract!r}"
                )
            prev_to = to_contract
    if problems:
        return [Finding(
            "overlapping_contracts", "FAIL", len(problems),
            "contract_rolls' from/to chain is inconsistent when read in chronological order -- "
            "either two contracts were simultaneously \"active,\" or a roll event is missing/misordered.",
            problems[:10],
        )]
    return [Finding(
        "overlapping_contracts", "PASS", 0,
        "contract_rolls history is chronologically consistent for every product."
        if products else "No contract_rolls history to check yet.",
    )]


def _check_session_gaps(conn: sqlite3.Connection) -> list[Finding]:
    """Reuses market_data.sync's existing gap bookkeeping (the `gaps`
    table, populated by --verify-data) rather than recomputing session
    logic independently -- see sync.py's `verify`/`is_market_open`. This
    reports what the sync engine has already recorded, not a fresh
    calendar-aware recomputation."""
    rows = conn.execute(
        "SELECT product_code, resolution, COUNT(*) FROM gaps WHERE resolved_at IS NULL "
        "GROUP BY product_code, resolution"
    ).fetchall()
    if not rows:
        return [Finding(
            "session_gaps", "PASS", 0,
            "No unresolved session gaps recorded (see market_data.sync.verify/repair_gaps).",
        )]
    total = sum(r[2] for r in rows)
    samples = [f"{p}/{res}: {count} open gap(s)" for p, res, count in rows]
    return [Finding(
        "session_gaps", "WARN", total,
        "Unresolved gaps already recorded by the sync engine (run --repair-gaps to attempt "
        "to fill them) -- this reuses that bookkeeping rather than an independent recomputation.",
        samples,
    )]


def _check_missing_trading_days(conn: sqlite3.Connection, gap_threshold_weekdays: int = 4) -> list[Finding]:
    """Heuristic only. Flags calendar gaps of gap_threshold_weekdays+
    *weekdays* with zero bars, for daily-resolution series. This is NOT a
    verified holiday calendar for every exchange represented here -- some
    of this archive predates modern CME rules and covers exchanges never
    modeled in contracts.py (NYMEX/COMEX Crude/Gold/Copper, CBOT Treasury
    Bonds, the old S&P/Dollar Index tickers). A flagged gap may well be a
    legitimate historical closure -- treat findings here as "worth a
    look," not proof of a missing import.
    """
    series = conn.execute(
        "SELECT DISTINCT product_code FROM bars WHERE resolution = '1day'"
    ).fetchall()
    problems: list[str] = []
    for (product_code,) in series:
        dates = [
            datetime.fromisoformat(r[0]).date()
            for r in conn.execute(
                "SELECT DISTINCT date(timestamp) FROM bars WHERE product_code = ? AND resolution = '1day' "
                "ORDER BY timestamp",
                (product_code,),
            )
        ]
        for prev, curr in zip(dates, dates[1:]):
            gap = _count_weekdays_between(prev, curr)
            if gap >= gap_threshold_weekdays:
                problems.append(f"{product_code}: {gap} weekday(s) missing between {prev} and {curr}")
    if not problems:
        return [Finding(
            "missing_trading_days", "PASS", 0,
            f"No calendar gaps of {gap_threshold_weekdays}+ weekdays found in daily-resolution series "
            "(heuristic -- see docstring).",
        )]
    return [Finding(
        "missing_trading_days", "WARN", len(problems),
        f"Calendar gaps of {gap_threshold_weekdays}+ consecutive weekdays with no bar -- heuristic, "
        "not a verified holiday calendar; many are likely legitimate historical exchange closures.",
        problems[:15],
    )]


def _check_orphan_records(conn: sqlite3.Connection) -> list[Finding]:
    valid_products = {r[0] for r in conn.execute("SELECT DISTINCT product_code FROM bars")}
    findings: list[Finding] = []
    for table in ("gaps", "active_contracts", "contract_rolls", "sync_runs"):
        orphans = sorted(
            r[0] for r in conn.execute(f"SELECT DISTINCT product_code FROM {table}")
            if r[0] not in valid_products
        )
        if orphans:
            findings.append(Finding(
                f"orphan_records:{table}", "WARN", len(orphans),
                f"{table} references product_code(s) with zero rows in bars -- likely stale "
                "metadata from a sync that never completed or a product later removed.",
                orphans[:10],
            ))
        else:
            findings.append(Finding(
                f"orphan_records:{table}", "PASS", 0,
                f"Every product_code referenced in {table} has at least one row in bars.",
            ))
    return findings


_CHECKS = (
    _check_schema,
    _check_duplicate_rows,
    _check_missing_timestamps,
    _check_timestamp_validity,
    _check_ohlc_presence,
    _check_ohlc_numeric,
    _check_ohlc_relationships,
    _check_volume,
    _check_corrupted_symbols,
    _check_overlapping_contracts,
    _check_session_gaps,
    _check_missing_trading_days,
    _check_orphan_records,
)


def validate_database(db_path: Optional[Path] = None) -> ValidationReport:
    """Runs every check read-only against db_path (default: the project's
    configured market_data.db) and returns a ValidationReport. Never
    writes to the database -- the connection is opened SQLite
    read-only (`mode=ro`), which raises on any write attempt rather than
    silently allowing one.

    Each check runs independently: if one raises (e.g. `bars` is missing
    a column the check expects, on a database whose schema has drifted
    badly enough that `_check_schema` alone can't describe it), that's
    reported as its own FAIL finding rather than aborting every other
    check -- a badly damaged database is exactly when you want as much
    of the report as possible, not none of it."""
    path = Path(db_path) if db_path is not None else default_db_path()
    conn = _connect_readonly(path)
    try:
        try:
            total_bars = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        except sqlite3.Error:
            total_bars = 0

        findings: list[Finding] = []
        for check in _CHECKS:
            try:
                findings.extend(check(conn))
            except sqlite3.Error as exc:
                findings.append(Finding(
                    check.__name__.lstrip("_"), "FAIL", 0,
                    f"Check raised {exc.__class__.__name__}: {exc} -- likely a schema problem "
                    "severe enough that this check couldn't run at all. See the schema_mismatch "
                    "finding above for detail.",
                ))
        return ValidationReport(
            db_path=path,
            generated_at=datetime.now(timezone.utc),
            total_bars=total_bars,
            findings=findings,
        )
    finally:
        conn.close()


def render_report(report: ValidationReport) -> str:
    order = {"FAIL": 0, "WARN": 1, "PASS": 2}
    lines = [
        f"Database Validation Report -- {report.db_path}",
        f"Generated: {report.generated_at.isoformat()}",
        f"Total bars: {report.total_bars:,}",
        "",
    ]
    for finding in sorted(report.findings, key=lambda f: order.get(f.severity, 3)):
        lines.append(f"[{finding.severity}] {finding.check}: {finding.detail}")
        if finding.severity != "PASS":
            lines.append(f"    count: {finding.count:,}")
        for sample in finding.samples[:10]:
            lines.append(f"    - {sample}")
    fails, warns = report.failures, report.warnings
    passes = len(report.findings) - len(fails) - len(warns)
    lines += [
        "",
        f"Summary: {len(fails)} FAIL, {len(warns)} WARN, {passes} PASS.",
        "VALIDATION FAILED" if fails else "VALIDATION PASSED",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m futures_bot.market_data.validation",
        description="Read-only data-integrity validator for market_data.db.",
    )
    parser.add_argument("--db", type=Path, default=None, help="Path to the database (default: default_db_path()).")
    args = parser.parse_args(argv)

    report = validate_database(args.db)
    print(render_report(report))
    return 1 if not report.passed else 0


if __name__ == "__main__":
    sys.exit(main())
