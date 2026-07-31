"""Stream-scan `logs/decisions.jsonl` and report on what it actually
contains -- built so you can independently check the decision journal's
data rather than trust it, per this project's skepticism mandate
(CLAUDE.md section 2A).

Never loads the file into memory: iterates it line by line, so this is
safe to run even at multi-GB scale (one real `decisions.jsonl` reached
9.2 GB / 34.2M lines -- see api/services.py's `_read_tail_lines`
docstring for the same constraint hit before).

What it checks:
  - Every line is valid JSON (flags corruption/truncated writes).
  - Every `trade` record's own arithmetic is self-consistent:
    gross_pnl - commission == net_pnl (flags a logging or engine bug,
    not just a formatting issue).
  - Breaks down `decision` actions and block_reasons, and `event` kinds,
    so you can sanity-check the shape of what's being recorded.

Usage:
    python tools/verify_decisions_journal.py [path-to-decisions.jsonl]

Exit code 0 if no parse errors or arithmetic mismatches were found,
1 otherwise (so this can be used as a CI/scheduled check later, not
just interactively).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _to_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def verify(path: Path, max_examples: int = 10) -> int:
    type_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    block_reason_counts: Counter[str] = Counter()
    event_kind_counts: Counter[str] = Counter()

    parse_errors: list[tuple[int, str]] = []
    arithmetic_mismatches: list[tuple[int, dict]] = []

    trade_count = 0
    net_pnl_sum = Decimal("0")
    first_timestamp: str | None = None
    last_timestamp: str | None = None

    line_no = 0
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                if len(parse_errors) < max_examples:
                    parse_errors.append((line_no, str(exc)))
                continue

            record_type = obj.get("type", "<missing>")
            type_counts[record_type] += 1

            ts = obj.get("timestamp")
            if ts:
                if first_timestamp is None:
                    first_timestamp = ts
                last_timestamp = ts

            if record_type == "decision":
                action_counts[obj.get("action", "<missing>")] += 1
                if obj.get("block_reason"):
                    block_reason_counts[obj["block_reason"]] += 1

            elif record_type == "trade":
                trade_count += 1
                gross = _to_decimal(obj.get("gross_pnl"))
                commission = _to_decimal(obj.get("commission"))
                net = _to_decimal(obj.get("net_pnl"))
                if net is not None:
                    net_pnl_sum += net
                if gross is not None and commission is not None and net is not None:
                    expected = gross - commission
                    if (expected - net).copy_abs() > Decimal("0.01"):
                        if len(arithmetic_mismatches) < max_examples:
                            arithmetic_mismatches.append((line_no, obj))

            elif record_type == "event":
                event_kind_counts[obj.get("kind", "<missing>")] += 1

    size_gb = path.stat().st_size / (1024**3)
    print(f"File: {path}  ({size_gb:.2f} GB)")
    print(f"Lines scanned: {line_no:,}")
    print(f"Timestamp range seen: {first_timestamp} .. {last_timestamp}")
    print()

    print("Record types:")
    for key, count in type_counts.most_common():
        print(f"  {key:12} {count:>12,}")
    print()

    if action_counts:
        print("Decision actions:")
        for key, count in action_counts.most_common():
            print(f"  {key:12} {count:>12,}")
        print()

    if block_reason_counts:
        print("Block reasons (why a signal was declined):")
        for key, count in block_reason_counts.most_common(20):
            print(f"  {key:40} {count:>10,}")
        print()

    if event_kind_counts:
        print("Event kinds:")
        for key, count in event_kind_counts.most_common():
            print(f"  {key:20} {count:>10,}")
        print()

    print(f"Trades: {trade_count:,}  |  Sum of net_pnl across all trades: {net_pnl_sum}")
    print()

    ok = True

    if parse_errors:
        ok = False
        print(f"JSON PARSE ERRORS: {len(parse_errors)} shown (there may be more beyond this cap)")
        for ln, err in parse_errors:
            print(f"  line {ln}: {err}")
        print()

    if arithmetic_mismatches:
        ok = False
        print(f"TRADE ARITHMETIC MISMATCHES (gross_pnl - commission != net_pnl): "
              f"{len(arithmetic_mismatches)} shown")
        for ln, obj in arithmetic_mismatches:
            print(
                f"  line {ln}: gross={obj.get('gross_pnl')} "
                f"commission={obj.get('commission')} net={obj.get('net_pnl')}"
            )
        print()

    if ok:
        print("No parse errors or trade-arithmetic mismatches found in this scan.")

    return 0 if ok else 1


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/decisions.jsonl")
    if not target.exists():
        print(f"No such file: {target}", file=sys.stderr)
        sys.exit(2)
    sys.exit(verify(target))
